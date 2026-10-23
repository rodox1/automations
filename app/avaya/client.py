from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape as xml_escape

import requests
from requests import Response
from requests.auth import HTTPBasicAuth
from urllib3.exceptions import InsecureRequestWarning


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constantes da API Avaya IP Office WebManagement SDK
# ---------------------------------------------------------------------------

SDK_BASE_PATH = "/WebManagement/ws/sdk"

AUTH_PATH = f"{SDK_BASE_PATH}/security/authenticate"
USERS_PATH = f"{SDK_BASE_PATH}/admin/v1/users"
SYSTEMS_PATH = f"{SDK_BASE_PATH}/admin/v1/systems"
EXTENSIONS_PATH = f"{SDK_BASE_PATH}/admin/v1/extensions"
HUNTGROUPS_PATH = f"{SDK_BASE_PATH}/admin/v1/huntgroups"

DEFAULT_PORT = 7070
DEFAULT_TIMEOUT = 30
DEFAULT_VERIFY_SSL = False


# ---------------------------------------------------------------------------
# Exceção específica
# ---------------------------------------------------------------------------

class AvayaHTTPError(RuntimeError):
    """Erro HTTP/API específico do Avaya IP Office."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        response_text: str | None = None,
        error_code: str | None = None,
    ) -> None:
        super().__init__(message)

        self.status_code = status_code
        self.response_text = response_text
        self.error_code = error_code


# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

@dataclass
class AvayaConfig:
    host: str
    username: str
    password: str
    port: int = DEFAULT_PORT
    verify_ssl: bool = DEFAULT_VERIFY_SSL
    timeout: int = DEFAULT_TIMEOUT


# ---------------------------------------------------------------------------
# Cliente Avaya IP Office
# ---------------------------------------------------------------------------

class AvayaIPOfficeAPI:
    """
    Cliente para o Avaya IP Office WebManagement SDK.

    IMPORTANTE:
    O prefixo /WebManagement/ws/sdk é definido uma única vez em
    SDK_BASE_PATH.

    Endpoints:

        GET  /WebManagement/ws/sdk/security/authenticate
        GET  /WebManagement/ws/sdk/admin/v1/users
        POST /WebManagement/ws/sdk/admin/v1/users
        DELETE /WebManagement/ws/sdk/admin/v1/users
        GET  /WebManagement/ws/sdk/admin/v1/systems
        GET/POST/PUT/DELETE /WebManagement/ws/sdk/admin/v1/extensions
        GET/POST/PUT/DELETE /WebManagement/ws/sdk/admin/v1/huntgroups
    """

    def __init__(
        self,
        host: str | None = None,
        username: str | None = None,
        password: str | None = None,
        port: int | None = None,
        verify_ssl: bool | None = None,
        timeout: int | None = None,
    ) -> None:

        # Each API operation must provide the IPO selected by the caller.
        # Legacy process-wide host settings are intentionally ignored: a
        # fixed host silently routes every operation to the same IPO.
        self.host = str(host).strip() if host is not None else ""

        self.username = (
            username
            or os.getenv("AVAYA_USERNAME")
        )

        self.password = (
            password
            or os.getenv("AVAYA_PASSWORD")
        )

        self.port = int(
            port
            if port is not None
            else os.getenv("AVAYA_PORT", DEFAULT_PORT)
        )

        if verify_ssl is None:
            env_verify = os.getenv("AVAYA_VERIFY_SSL")

            if env_verify is None:
                self.verify_ssl = DEFAULT_VERIFY_SSL
            else:
                self.verify_ssl = env_verify.strip().lower() in {
                    "1",
                    "true",
                    "yes",
                    "on",
                }
        else:
            self.verify_ssl = verify_ssl

        self.timeout = int(
            timeout
            if timeout is not None
            else os.getenv("AVAYA_TIMEOUT", DEFAULT_TIMEOUT)
        )

        if not self.host:
            raise RuntimeError("Informe o IP do IPO de destino (host).")

        if not self.username:
            raise RuntimeError("AVAYA_USERNAME não configurado")

        if not self.password:
            raise RuntimeError("AVAYA_PASSWORD não configurado")

        # ------------------------------------------------------------------
        # IMPORTANTE:
        # base_url contém somente host + porta + SDK_BASE_PATH.
        # Nenhum endpoint deve adicionar novamente WebManagement/ws/sdk.
        # ------------------------------------------------------------------

        self.base_url = (
            f"https://{self.host}:{self.port}{SDK_BASE_PATH}"
        )

        self.auth_url = (
            f"https://{self.host}:{self.port}{AUTH_PATH}"
        )

        self.users_url = (
            f"https://{self.host}:{self.port}{USERS_PATH}"
        )

        self.systems_url = (
            f"https://{self.host}:{self.port}{SYSTEMS_PATH}"
        )
        self.extensions_url = f"https://{self.host}:{self.port}{EXTENSIONS_PATH}"
        self.huntgroups_url = f"https://{self.host}:{self.port}{HUNTGROUPS_PATH}"

        self.session = requests.Session()

        self.session.headers.update(
            {
                "X-User-Client": "Avaya-WebAdmin",
                "X-User-Agent": "Avaya-SDKUser",
                "Accept": "application/xml, application/json, text/xml",
            }
        )

        self._authenticated = False
        self._sdk_token: str | None = None

        if not self.verify_ssl:
            requests.packages.urllib3.disable_warnings(
                category=InsecureRequestWarning
            )

        logger.info(
            "[AVAYA] Cliente inicializado | host=%s | port=%s | verify_ssl=%s",
            self.host,
            self.port,
            self.verify_ssl,
        )

    # -----------------------------------------------------------------------
    # Utilidades
    # -----------------------------------------------------------------------

    @staticmethod
    def _mask_secret(value: str | None) -> str:
        if not value:
            return "***"

        if len(value) <= 4:
            return "***"

        return (
            value[:2]
            + "***"
            + value[-2:]
        )

    @staticmethod
    def _safe_response_text(response: Response) -> str:
        try:
            return response.text or ""
        except Exception:
            return ""

    @staticmethod
    def _xml_status(text: str) -> tuple[str | None, str | None]:
        """
        Retorna:

            (status, error_detail)

        O SDK Avaya pode responder HTTP 200 mesmo quando a operação
        falhou no nível da API. Por isso, o atributo XML ``status`` é
        tratado como parte obrigatória da validação da resposta.
        """

        if not text:
            return None, None

        try:
            root = ET.fromstring(text)
            status = root.attrib.get("status")

            error_detail = (
                root.attrib.get("error")
                or root.attrib.get("message")
                or root.attrib.get("detail")
            )

            if not error_detail:
                error_code = None
                error_desc = None

                for node in root.iter():
                    tag = node.tag
                    if "}" in tag:
                        tag = tag.split("}", 1)[1]

                    tag_lower = tag.lower()

                    if tag_lower == "error_code" and node.text:
                        error_code = node.text.strip()
                    elif tag_lower == "error_desc" and node.text:
                        error_desc = node.text.strip()

                if error_code and error_desc:
                    error_detail = f"{error_code}: {error_desc}"
                else:
                    error_detail = error_code or error_desc

            return status, error_detail

        except ET.ParseError:
            return None, None

    @staticmethod
    def _json_or_text(text: str) -> Any:
        if not text:
            return None

        try:
            return json.loads(text)
        except Exception:
            return text

    # -----------------------------------------------------------------------
    # Autenticação
    # -----------------------------------------------------------------------

    def authenticate(self) -> bool:
        """
        Autentica no Avaya IP Office e armazena:

            JSESSIONID
            X-SDK-Token
        """

        logger.info(
            "[AVAYA AUTH] Iniciando autenticação | url=%s",
            self.auth_url,
        )

        headers = {
            "X-User-Client": "Avaya-WebAdmin",
            "X-User-Agent": "Avaya-SDKUser",
            "Content-Type": "application/xml",
            "Accept": "application/xml, application/json",
        }

        try:
            response = self.session.get(
                self.auth_url,
                headers=headers,
                auth=HTTPBasicAuth(
                    self.username,
                    self.password,
                ),
                verify=self.verify_ssl,
                timeout=self.timeout,
            )

        except requests.RequestException as exc:
            self._authenticated = False

            logger.error(
                "[AVAYA AUTH] Erro de conexão: %s",
                exc,
            )

            raise AvayaHTTPError(
                f"Erro de conexão com Avaya: {exc}"
            ) from exc

        text = self._safe_response_text(response)

        logger.info(
            "[AVAYA AUTH] HTTP=%s | Content-Type=%s | Length=%s",
            response.status_code,
            response.headers.get("Content-Type"),
            len(text),
        )

        status, error_detail = self._xml_status(text)

        if response.status_code != 200:
            self._authenticated = False

            logger.error(
                "[AVAYA AUTH] Falha HTTP=%s | api_status=%s | detail=%s | body=%s",
                response.status_code,
                status,
                error_detail,
                text[:500],
            )

            raise AvayaHTTPError(
                f"Autenticação Avaya falhou: HTTP "
                f"{response.status_code} - {error_detail or ''}",
                status_code=response.status_code,
                response_text=text,
                error_code=status,
            )

        # ---------------------------------------------------------------
        # IMPORTANTE:
        # O Avaya pode retornar HTTP 200 com <response status="0">.
        # Nesse caso a autenticação NÃO foi realizada.
        # ---------------------------------------------------------------

        if status != "1":
            self._authenticated = False
            self._sdk_token = None
            self.session.headers.pop("X-SDK-Token", None)

            logger.error(
                "[AVAYA AUTH] Falha na API | HTTP=200 | api_status=%s | "
                "detail=%s | body=%s",
                status,
                error_detail,
                text[:500],
            )

            raise AvayaHTTPError(
                "Autenticação Avaya falhou no nível da API: "
                f"status={status!r} - {error_detail or 'resposta sem detalhe; verifique credenciais, permissoes e sessoes administrativas simultaneas no IPO'}",
                status_code=response.status_code,
                response_text=text,
                error_code=(
                    error_detail.split(":", 1)[0]
                    if error_detail and ":" in error_detail
                    else status
                ),
            )

        # ---------------------------------------------------------------
        # Cookies retornados pelo Avaya
        # ---------------------------------------------------------------

        jsessionid = self.session.cookies.get("JSESSIONID")

        sdk_token = self.session.cookies.get("X-SDK-Token")

        # Alguns ambientes podem retornar o token no header.
        if not sdk_token:
            sdk_token = response.headers.get("X-SDK-Token")

        self._sdk_token = sdk_token

        if sdk_token:
            self.session.headers.update(
                {
                    "X-SDK-Token": sdk_token,
                }
            )

        self._authenticated = True

        logger.info(
            "[AVAYA AUTH] Autenticação realizada com sucesso | "
            "JSESSIONID=%s | X-SDK-Token=%s",
            self._mask_secret(jsessionid),
            self._mask_secret(sdk_token),
        )

        return True

    # -----------------------------------------------------------------------
    # Garantir autenticação
    # -----------------------------------------------------------------------

    def _ensure_authenticated(self) -> None:
        if not self._authenticated:
            self.authenticate()

    # -----------------------------------------------------------------------
    # Request genérico
    # -----------------------------------------------------------------------

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        data: Any = None,
        json_data: Any = None,
        headers: dict[str, str] | None = None,
        timeout: int | None = None,
        verify_ssl: bool | None = None,
        retry_auth: bool = True,
    ) -> Response:
        """
        Executa uma chamada HTTP autenticada.

        retry_auth=True:
            Em 401/403/461 tenta autenticar novamente uma vez.
        """

        self._ensure_authenticated()

        request_headers = dict(headers or {})

        if "Accept" not in request_headers:
            request_headers["Accept"] = (
                "application/xml, application/json, text/xml"
            )

        request_verify = (
            self.verify_ssl
            if verify_ssl is None
            else verify_ssl
        )

        request_timeout = (
            self.timeout
            if timeout is None
            else timeout
        )

        logger.info(
            "[AVAYA] %s %s | params=%s",
            method.upper(),
            url,
            params,
        )

        try:
            response = self.session.request(
                method=method.upper(),
                url=url,
                params=params,
                data=data,
                json=json_data,
                headers=request_headers,
                verify=request_verify,
                timeout=request_timeout,
            )

        except requests.RequestException as exc:
            logger.error(
                "[AVAYA] Erro de conexão | %s %s | error=%s",
                method.upper(),
                url,
                exc,
            )

            raise AvayaHTTPError(
                f"Erro de conexão com Avaya: {exc}"
            ) from exc

        text = self._safe_response_text(response)

        logger.info(
            "[AVAYA] HTTP=%s | method=%s | url=%s | length=%s",
            response.status_code,
            method.upper(),
            url,
            len(text),
        )

        # ---------------------------------------------------------------
        # Sessão expirada
        #
        # Avaya pode utilizar:
        #   401
        #   403
        #   461
        # ---------------------------------------------------------------

        if (
            response.status_code in {401, 403, 461}
            and retry_auth
        ):
            logger.warning(
                "[AVAYA] Sessão inválida/expirada "
                "(HTTP=%s). Reautenticando...",
                response.status_code,
            )

            self._authenticated = False

            self.authenticate()

            return self.request(
                method,
                url,
                params=params,
                data=data,
                json_data=json_data,
                headers=headers,
                timeout=timeout,
                verify_ssl=verify_ssl,
                retry_auth=False,
            )

        return response

    # -----------------------------------------------------------------------
    # Extensions and hunt groups
    # -----------------------------------------------------------------------

    @staticmethod
    def _parse_management_objects(xml_text: str, object_name: str) -> list[dict[str, Any]]:
        if not xml_text or not xml_text.strip():
            raise AvayaHTTPError(f"Resposta XML de {object_name} vazia.", response_text=xml_text)
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            raise AvayaHTTPError(f"XML de {object_name} inválido: {exc}", response_text=xml_text) from exc
        root_tag = root.tag.rsplit("}", 1)[-1].lower()
        if root_tag != "response":
            raise AvayaHTTPError(f"Raiz XML inesperada em {object_name}: {root_tag}", response_text=xml_text)
        status, detail = AvayaIPOfficeAPI._xml_status(xml_text)
        if status != "1":
            raise AvayaHTTPError(
                f"Consulta Avaya de {object_name} não confirmou sucesso (status XML={status!r})"
                + (f": {detail}" if detail else ""), response_text=xml_text,
            )
        rows: list[dict[str, Any]] = []
        for node in root.iter():
            if node.tag.rsplit("}", 1)[-1].lower() != object_name.lower():
                continue
            row = {str(key).lstrip("@"): value for key, value in node.attrib.items()}
            for child in node:
                key = child.tag.rsplit("}", 1)[-1]
                row[key] = (child.text or "").strip()
            rows.append(row)
        return rows

    def _get_management_objects(self, url: str, object_name: str, target_ip: str | None) -> list[dict[str, Any]]:
        response = self.request("GET", url, params={"ipaddress": target_ip} if target_ip else None)
        text = self._safe_response_text(response)
        if response.status_code != 200:
            raise AvayaHTTPError(f"Consulta Avaya de {object_name} falhou: HTTP {response.status_code}", status_code=response.status_code, response_text=text)
        return self._parse_management_objects(text, object_name)

    def get_extensions(self, target_ip: str | None = None) -> list[dict[str, Any]]:
        return self._get_management_objects(self.extensions_url, "Extension", target_ip)

    def get_huntgroups(self, target_ip: str | None = None) -> list[dict[str, Any]]:
        return self._get_management_objects(self.huntgroups_url, "HuntGroup", target_ip)

    def mutate_management_object(
        self, resource: str, method: str, payload: dict[str, Any] | None = None,
        *, guid: str | None = None, target_ip: str | None = None,
    ) -> dict[str, Any]:
        """Create/update/delete an Extension or HuntGroup using the documented XML API."""
        config = {
            "extension": (self.extensions_url, "Extension", ("Extension", "TypeInfo", "CallerDisplayType", "Module", "Port", "Location")),
            "huntgroup": (self.huntgroups_url, "HuntGroup", ("Name", "Extension", "GroupRingMode", "MembersList", "ServiceMode", "VoicemailOn", "IsGroupDistributed")),
        }
        key = resource.strip().lower()
        if key not in config or method.upper() not in {"POST", "PUT", "DELETE"}:
            raise ValueError("Recurso ou método de gerenciamento Avaya inválido.")
        url, element_name, allowed = config[key]
        params = {"ipaddress": target_ip} if target_ip else {}
        if method.upper() == "DELETE":
            if not guid or not guid.strip():
                raise ValueError("GUID é obrigatório para exclusão.")
            params["guid"] = guid.strip()
            response = self.request("DELETE", url, params=params)
        else:
            fields = payload or {}
            attrs = f' GUID="{guid.strip()}"' if method.upper() == "PUT" and guid else ""
            body_fields = "".join(f"<{field}>{xml_escape(str(fields[field]))}</{field}>" for field in allowed if field in fields and fields[field] is not None)
            if method.upper() == "PUT" and not guid:
                raise ValueError("GUID é obrigatório para atualização.")
            xml = f'<?xml version="1.0" encoding="UTF-8"?><data><ws_object><{element_name}{attrs}>{body_fields}</{element_name}></ws_object></data>'
            response = self.request(method.upper(), url, params=params or None, data=xml, headers={"Content-Type": "application/xml", "Accept": "application/xml, application/json"})
        text = self._safe_response_text(response)
        if response.status_code != 200:
            raise AvayaHTTPError(f"Operação {method.upper()} de {resource} falhou: HTTP {response.status_code}", status_code=response.status_code, response_text=text)
        status, detail = self._xml_status(text)
        if status == "0":
            raise AvayaHTTPError(f"Operação {method.upper()} de {resource} retornou status=0" + (f": {detail}" if detail else ""), status_code=response.status_code, response_text=text)
        return {"status": status, "guid": self._extract_guid(text), "response": text}

    # -----------------------------------------------------------------------
    # Usuários
    # -----------------------------------------------------------------------

    def get_users(
        self,
        target_ip: str | None = None,
        *,
        save_xml: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Obtém usuários do Avaya.

        Quando target_ip é informado:

            GET /admin/v1/users?ipaddress=<target_ip>
        """

        params: dict[str, Any] = {}

        if target_ip:
            params["ipaddress"] = target_ip

        response = self.request(
            "GET",
            self.users_url,
            params=params or None,
        )

        text = self._safe_response_text(response)

        if response.status_code != 200:
            raise AvayaHTTPError(
                f"Consulta de usuários Avaya falhou: "
                f"HTTP {response.status_code}",
                status_code=response.status_code,
                response_text=text,
            )

        if save_xml:
            with open(
                save_xml,
                "w",
                encoding="utf-8",
            ) as file:
                file.write(text)

            logger.info(
                "[AVAYA] XML de usuários salvo em %s",
                save_xml,
            )

        users = self._parse_users(text)

        logger.info(
            "[AVAYA] GET /admin/v1/users -> HTTP 200 | usuários=%s",
            len(users),
        )

        return users

    # -----------------------------------------------------------------------
    # Parser de usuários
    # -----------------------------------------------------------------------

    @staticmethod
    def _parse_users(xml_text: str) -> list[dict[str, Any]]:
        """Converte XML e aceita somente respostas Avaya bem-sucedidas."""
        if not xml_text or not xml_text.strip():
            raise AvayaHTTPError(
                "Resposta XML de usu?rios vazia.",
                response_text=xml_text,
            )

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            raise AvayaHTTPError(
                f"XML de usu?rios inv?lido: {exc}",
                response_text=xml_text,
            ) from exc

        root_tag = root.tag
        if "}" in root_tag:
            root_tag = root_tag.split("}", 1)[1]

        if root_tag.lower() != "response":
            raise AvayaHTTPError(
                f"Raiz XML inesperada na consulta de usu?rios: {root_tag}",
                response_text=xml_text,
            )

        status, error_detail = AvayaIPOfficeAPI._xml_status(xml_text)
        if status != "1":
            detail = f": {error_detail}" if error_detail else ""
            raise AvayaHTTPError(
                "Consulta de usu?rios Avaya n?o confirmou sucesso "
                f"(status XML={status!r}){detail}",
                response_text=xml_text,
            )

        users: list[dict[str, Any]] = []
        user_nodes = root.findall(".//User")

        logger.debug(
            "[AVAYA DEBUG] XML root=%s | User nodes encontrados=%s",
            root.tag,
            len(user_nodes),
        )

        for node in user_nodes:
            item: dict[str, Any] = {}

            guid = node.attrib.get("GUID")
            if guid:
                item["GUID"] = guid

            for key, value in node.attrib.items():
                if key not in item:
                    item[key] = value

            for child in node:
                tag = child.tag
                if "}" in tag:
                    tag = tag.split("}", 1)[1]

                value = child.text
                if value is not None:
                    value = value.strip()

                item[tag] = value

            users.append(item)

        return users

    def get_systems(
        self,
        target_ip: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Consulta informações do sistema Avaya.

        Quando target_ip é informado:

            GET /admin/v1/systems?ipaddress=<target_ip>
        """

        params: dict[str, Any] = {}

        if target_ip:
            params["ipaddress"] = target_ip

        response = self.request(
            "GET",
            self.systems_url,
            params=params or None,
        )

        text = self._safe_response_text(response)

        if response.status_code != 200:
            raise AvayaHTTPError(
                f"Consulta de sistemas Avaya falhou: "
                f"HTTP {response.status_code}",
                status_code=response.status_code,
                response_text=text,
            )

        systems = self._parse_systems(text)

        logger.info(
            "[AVAYA] GET /admin/v1/systems -> HTTP 200 | sistemas=%s",
            len(systems),
        )

        return systems

    # -----------------------------------------------------------------------
    # Parser de sistemas
    # -----------------------------------------------------------------------

    @staticmethod
    def _parse_systems(xml_text: str) -> list[dict[str, Any]]:
        if not xml_text:
            return []

        try:
            root = ET.fromstring(xml_text)

        except ET.ParseError as exc:
            raise AvayaHTTPError(
                f"XML de sistemas inválido: {exc}",
                response_text=xml_text,
            ) from exc

        systems: list[dict[str, Any]] = []

        # A API pode variar a capitalização/estrutura conforme versão.
        # Procuramos System e também nós que contenham os campos esperados.

        system_nodes = root.findall(".//System")

        if not system_nodes:
            system_nodes = root.findall(".//system")

        for node in system_nodes:
            item: dict[str, Any] = {}

            for key, value in node.attrib.items():
                item[key] = value

            for child in node:
                tag = child.tag

                if "}" in tag:
                    tag = tag.split("}", 1)[1]

                value = child.text

                if value is not None:
                    value = value.strip()

                item[tag] = value

            systems.append(item)

        return systems

    # -----------------------------------------------------------------------
    # DELETE usuário
    # -----------------------------------------------------------------------

    def delete_user(
        self,
        guid: str,
        target_ip: str | None = None,
    ) -> bool:
        """
        Remove um usuário pelo GUID.

        Utilizado principalmente como compensação/rollback da provisão.

        Endpoint:

            DELETE /admin/v1/users

        Query:

            guid=<GUID>
            ipaddress=<target_ip>
        """

        if not guid or not guid.strip():
            raise ValueError("GUID do usuário é obrigatório")

        params: dict[str, Any] = {
            "guid": guid.strip(),
        }

        if target_ip:
            params["ipaddress"] = target_ip

        logger.warning(
            "[AVAYA DELETE] Removendo usuário | guid=%s | target_ip=%s",
            guid,
            target_ip,
        )

        response = self.request(
            "DELETE",
            self.users_url,
            params=params,
        )

        text = self._safe_response_text(response)

        if response.status_code != 200:
            raise AvayaHTTPError(
                f"DELETE de usuário Avaya falhou: "
                f"HTTP {response.status_code}",
                status_code=response.status_code,
                response_text=text,
            )

        status, error_detail = self._xml_status(text)

        # A API pode responder HTTP 200 com status XML=0.
        if status == "0":
            raise AvayaHTTPError(
                "DELETE de usuário Avaya retornou status=0"
                + (
                    f": {error_detail}"
                    if error_detail
                    else ""
                ),
                status_code=response.status_code,
                response_text=text,
                error_code=status,
            )

        logger.info(
            "[AVAYA DELETE] Usuário removido com sucesso | guid=%s",
            guid,
        )

        return True

    # -----------------------------------------------------------------------
    # CREATE genérico
    # -----------------------------------------------------------------------

    def create_user(
        self,
        xml_payload: str,
        target_ip: str | None = None,
    ) -> dict[str, Any]:
        """
        Cria usuário usando XML.

        Retorna:

            {
                "guid": "...",
                "status": "1",
                "response": "...",
            }
        """

        params: dict[str, Any] = {}

        if target_ip:
            params["ipaddress"] = target_ip

        response = self.request(
            "POST",
            self.users_url,
            params=params or None,
            data=xml_payload,
            headers={
                "Content-Type": "application/xml",
                "Accept": "application/xml, application/json",
            },
        )

        text = self._safe_response_text(response)

        if response.status_code != 200:
            raise AvayaHTTPError(
                f"Criação de usuário Avaya falhou: "
                f"HTTP {response.status_code}",
                status_code=response.status_code,
                response_text=text,
            )

        status, error_detail = self._xml_status(text)

        if status == "0":
            raise AvayaHTTPError(
                "Criação de usuário Avaya retornou status=0"
                + (
                    f": {error_detail}"
                    if error_detail
                    else ""
                ),
                status_code=response.status_code,
                response_text=text,
                error_code=status,
            )

        guid = self._extract_guid(text)

        logger.info(
            "[AVAYA CREATE] Usuário criado | guid=%s | target_ip=%s",
            guid,
            target_ip,
        )

        return {
            "guid": guid,
            "status": status,
            "response": text,
        }

    # -----------------------------------------------------------------------
    # Extrair GUID de resposta
    # -----------------------------------------------------------------------

    @staticmethod
    def _extract_guid(xml_text: str) -> str | None:
        if not xml_text:
            return None

        try:
            root = ET.fromstring(xml_text)

        except ET.ParseError:
            return None

        # Procura GUID em qualquer nó.
        for node in root.iter():
            guid = (
                node.attrib.get("GUID")
                or node.attrib.get("guid")
            )

            if guid:
                return guid

        # Alguns retornos podem conter GUID como elemento.
        for node in root.iter():
            tag = node.tag

            if "}" in tag:
                tag = tag.split("}", 1)[1]

            if tag.lower() == "guid":
                if node.text:
                    return node.text.strip()

        return None

    # -----------------------------------------------------------------------
    # UPDATE
    # -----------------------------------------------------------------------

    def update_user(
        self,
        xml_payload: str,
        guid: str | None = None,
        target_ip: str | None = None,
    ) -> dict[str, Any]:
        """
        Atualiza usuário via PUT.

        Mantido como helper de compatibilidade para os módulos existentes.
        """

        params: dict[str, Any] = {}

        if target_ip:
            params["ipaddress"] = target_ip

        # The Management API identifies the User in the request XML.
        if not guid or not guid.strip():
            raise ValueError("GUID é obrigatório para atualizar usuário.")
        try:
            root = ET.fromstring(xml_payload)
            user_node = root.find(".//User")
            if user_node is None:
                raise ValueError("XML de atualização deve conter elemento User.")
            user_node.set("GUID", guid.strip())
            xml_payload = ET.tostring(root, encoding="unicode")
        except ET.ParseError as exc:
            raise ValueError("XML de atualização de usuário inválido.") from exc

        response = self.request(
            "PUT",
            self.users_url,
            params=params or None,
            data=xml_payload,
            headers={
                "Content-Type": "application/xml",
                "Accept": "application/xml, application/json",
            },
        )

        text = self._safe_response_text(response)

        if response.status_code != 200:
            raise AvayaHTTPError(
                f"Atualização de usuário Avaya falhou: "
                f"HTTP {response.status_code}",
                status_code=response.status_code,
                response_text=text,
            )

        status, error_detail = self._xml_status(text)

        if status == "0":
            raise AvayaHTTPError(
                "Atualização de usuário Avaya retornou status=0"
                + (
                    f": {error_detail}"
                    if error_detail
                    else ""
                ),
                status_code=response.status_code,
                response_text=text,
                error_code=status,
            )

        return {
            "status": status,
            "response": text,
        }

    # -----------------------------------------------------------------------
    # Pesquisa local no retorno do Avaya
    # -----------------------------------------------------------------------

    def search_users(
        self,
        *,
        name: str | None = None,
        extension: str | None = None,
        guid: str | None = None,
        target_ip: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Busca usuários no Avaya.

        A consulta é feita através do GET e o filtro adicional ocorre
        localmente para manter compatibilidade com diferentes versões do SDK.
        """

        users = self.get_users(
            target_ip=target_ip,
        )

        result: list[dict[str, Any]] = []

        name_lower = name.lower() if name else None

        for user in users:
            if guid:
                user_guid = str(
                    user.get("GUID")
                    or user.get("guid")
                    or ""
                )

                if user_guid.lower() != guid.lower():
                    continue

            if extension:
                user_extension = str(
                    user.get("Extension")
                    or user.get("EXTENSION")
                    or user.get("extension")
                    or ""
                )

                if user_extension != str(extension):
                    continue

            if name_lower:
                user_name = str(
                    user.get("Name")
                    or user.get("NAME")
                    or user.get("name")
                    or ""
                )

                if name_lower not in user_name.lower():
                    continue

            result.append(user)

        return result

    # -----------------------------------------------------------------------
    # Teste de conexão
    # -----------------------------------------------------------------------

    def test_connection(
        self,
        target_ip: str | None = None,
    ) -> dict[str, Any]:
        """
        Testa autenticação + consulta básica de usuários.
        """

        self.authenticate()

        users = self.get_users(
            target_ip=target_ip,
        )

        return {
            "ok": True,
            "host": self.host,
            "target_ip": target_ip,
            "users": len(users),
        }

    # -----------------------------------------------------------------------
    # Informações seguras de diagnóstico
    # -----------------------------------------------------------------------

    def safe_config(self) -> dict[str, Any]:
        """
        Retorna configuração sem expor senha/token.
        """

        return {
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "verify_ssl": self.verify_ssl,
            "timeout": self.timeout,
            "base_url": self.base_url,
            "auth_url": self.auth_url,
            "users_url": self.users_url,
            "systems_url": self.systems_url,
            "extensions_url": self.extensions_url,
            "huntgroups_url": self.huntgroups_url,
        }

    def __repr__(self) -> str:
        return (
            "AvayaIPOfficeAPI("
            f"host={self.host!r}, "
            f"port={self.port!r}, "
            f"username={self.username!r}, "
            f"verify_ssl={self.verify_ssl!r}"
            ")"
        )
