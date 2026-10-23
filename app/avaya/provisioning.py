from __future__ import annotations

import ipaddress
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Optional

from app.avaya.client import AvayaIPOfficeAPI


logger = logging.getLogger(__name__)


@dataclass
class ProvisioningResult:
    """
    Resultado normalizado de uma operação de provisioning.

    Mantém compatibilidade com app/api/provisioning.py,
    incluindo a propriedade `errors`.
    """

    success: bool
    http_status: Optional[int]
    api_status: Optional[str]
    guid: Optional[str]
    message: str
    error: Optional[str] = None
    raw_xml: Optional[str] = None

    @property
    def errors(self) -> list[str]:
        """
        Compatibilidade com a camada FastAPI.

        O serviço usa `error` internamente, enquanto
        app/api/provisioning.py espera `errors`.
        """
        if self.error:
            return [self.error]

        return []


class AvayaProvisioning:
    """
    Serviço de provisionamento de usuários do Avaya IP Office.

    Operações:

        build_user_xml()
            Monta XML de criação.

        dry_run_create()
            Monta XML sem enviar ao Avaya.

        create_user()
            Executa POST real no Avaya.

        parse_response()
            Interpreta resposta XML/HTTP do Avaya.

    Observações:

        - Não registra senhas.
        - Não registra LoginCode.
        - Não registra VoicemailCode.
        - Não registra tokens.
        - Não registra cookies.
        - Não registra XML completo de produção.
    """

    USERS_PATH = "/WebManagement/ws/sdk/admin/v1/users"

    SENSITIVE_FIELDS = {
        "Password",
        "LoginCode",
        "VoicemailCode",
    }

    def __init__(
        self,
        client: AvayaIPOfficeAPI,
    ):
        self.client = client

    # ==========================================================
    # UTILITÁRIOS
    # ==========================================================

    @staticmethod
    def _bool_text(value: Any) -> str:
        return "true" if bool(value) else "false"

    @staticmethod
    def _safe_str(value: Any) -> str:
        if value is None:
            return ""

        return str(value)

    @staticmethod
    def _clean_text(value: Optional[str]) -> Optional[str]:
        if value is None:
            return None

        value = value.strip()

        return value if value else None

    # ==========================================================
    # BUILD XML
    # ==========================================================

    def build_user_xml(
        self,
        *,
        name: str,
        password: str,
        full_name: str = "",
        extension: str = "",
        voicemail_email: str = "",
        xdirectory: bool = False,
        voicemail_on: bool = True,
        voicemail_code: str = "",
        receptionist: bool = False,
        assigned_package: int = 1,
        remote_worker: bool = False,
        twinning_type: int = 0,
        mobility_features: bool = False,
        ums_web_services: bool = False,
        flare_enabled: bool = False,
        login_code: str = "",
        sip_name: str = "",
        sip_contact: str = "",
        web_collaboration: bool = False,
    ) -> bytes:
        """
        Monta o XML de criação do usuário.

        Estrutura:

        <data>
          <ws_object>
            <User>
              ...
            </User>
          </ws_object>
        </data>
        """

        data = ET.Element("data")

        ws_object = ET.SubElement(
            data,
            "ws_object",
        )

        user = ET.SubElement(
            ws_object,
            "User",
        )

        def add(
            tag: str,
            value: Any,
        ) -> None:

            element = ET.SubElement(
                user,
                tag,
            )

            element.text = self._safe_str(value)

        add("Name", name)
        add("Password", password)
        add("FullName", full_name)
        add("Extension", extension)
        add("VoicemailEmail", voicemail_email)
        add(
            "XDirectory",
            self._bool_text(xdirectory),
        )
        add(
            "VoicemailOn",
            self._bool_text(voicemail_on),
        )
        add(
            "VoicemailCode",
            voicemail_code,
        )
        add(
            "Receptionist",
            self._bool_text(receptionist),
        )
        add(
            "AssignedPackage",
            assigned_package,
        )
        add(
            "RemoteWorker",
            self._bool_text(remote_worker),
        )
        add(
            "TwinningType",
            twinning_type,
        )
        add(
            "MobilityFeatures",
            self._bool_text(mobility_features),
        )
        add(
            "UMSWebServices",
            self._bool_text(ums_web_services),
        )
        add(
            "FlareEnabled",
            self._bool_text(flare_enabled),
        )
        add(
            "LoginCode",
            login_code,
        )
        add(
            "SIPName",
            sip_name,
        )
        add(
            "SIPContact",
            sip_contact,
        )
        add(
            "WebCollaboration",
            self._bool_text(web_collaboration),
        )

        return ET.tostring(
            data,
            encoding="utf-8",
            xml_declaration=True,
        )

    # ==========================================================
    # MASK XML
    # ==========================================================

    def mask_xml(
        self,
        xml_bytes: bytes,
    ) -> str:
        """
        Mascara campos sensíveis.

        Usado somente para Dry-Run.
        """

        try:

            root = ET.fromstring(
                xml_bytes,
            )

        except ET.ParseError:

            return "[XML_INVALIDO]"

        for field in self.SENSITIVE_FIELDS:

            for element in root.iter(field):

                if element.text:
                    element.text = "********"

        return ET.tostring(
            root,
            encoding="unicode",
        )

    # Compatibilidade com versões anteriores.
    def _mask_xml(
        self,
        xml_bytes: bytes,
    ) -> str:

        return self.mask_xml(
            xml_bytes,
        )

    # ==========================================================
    # DRY RUN
    # ==========================================================

    def dry_run_create(
        self,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        Monta o XML sem executar nenhuma operação no Avaya.

        `target_ip` é usado somente para definir o nó de destino
        da Management API e não é incluído no XML do usuário.

        Este é o método utilizado pelo endpoint:

            POST /api/provisioning/users/dry-run
        """

        target_ip = str(
            kwargs.pop("target_ip", "")
        ).strip()

        if not target_ip:
            raise ValueError(
                "target_ip é obrigatório para o dry-run."
            )

        try:
            ipaddress.ip_address(target_ip)
        except ValueError as exc:
            raise ValueError(
                "target_ip deve ser um endereço IP válido."
            ) from exc

        users_endpoint = (
            f"{self.USERS_PATH}"
            f"?ipaddress={target_ip}"
        )

        xml_bytes = self.build_user_xml(
            **kwargs,
        )

        return {
            "ready": True,
            "method": "POST",
            "endpoint": users_endpoint,
            "target_ip": target_ip,
            "content_type": "application/xml",
            "secrets_masked": True,
            "xml": self.mask_xml(
                xml_bytes,
            ),
        }

    # Compatibilidade com código anterior.
    def dry_run_user(
        self,
        **kwargs: Any,
    ) -> dict[str, Any]:

        return self.dry_run_create(
            **kwargs,
        )

    # ==========================================================
    # XML SEARCH
    # ==========================================================

    @staticmethod
    def _find_first(
        root: ET.Element,
        names: tuple[str, ...],
    ) -> Optional[ET.Element]:
        """
        Retorna o primeiro elemento encontrado pelos nomes.
        """

        for name in names:

            element = root.find(
                f".//{name}",
            )

            if element is not None:
                return element

        return None

    @staticmethod
    def _extract_element_text(
        root: ET.Element,
        names: tuple[str, ...],
    ) -> Optional[str]:
        """
        Extrai texto de um elemento XML.
        """

        element = AvayaProvisioning._find_first(
            root,
            names,
        )

        if element is None:
            return None

        return AvayaProvisioning._clean_text(
            element.text,
        )

    @staticmethod
    def _extract_attribute(
        root: ET.Element,
        names: tuple[str, ...],
    ) -> Optional[str]:
        """
        Procura atributos relevantes no XML.
        """

        # Primeiro verifica o root.
        for name in names:

            if name in root.attrib:

                return AvayaProvisioning._clean_text(
                    root.attrib.get(name),
                )

        # Depois procura em todos os elementos.
        for element in root.iter():

            for name in names:

                if name in element.attrib:

                    return AvayaProvisioning._clean_text(
                        element.attrib.get(name),
                    )

        return None

    # ==========================================================
    # BUSINESS ERRORS
    # ==========================================================

    def _extract_business_errors(
        self,
        root: ET.Element,
    ) -> list[str]:
        """
        Extrai erros de negócio mesmo quando o Avaya responde HTTP 200.
        """
        errors: list[str] = []

        for ws_object in root.findall(".//ws_object"):
            if ws_object.attrib.get("error_node") != "true":
                continue

            for error in ws_object.findall(".//error"):
                error_desc = self._clean_text(
                    error.findtext("error_desc")
                )
                error_code = self._clean_text(
                    error.findtext("error_code")
                )
                cause = self._clean_text(
                    error.findtext("cause")
                )

                if not error_desc and not error_code:
                    continue

                if error_code and error_desc:
                    message = f"{error_code}: {error_desc}"
                else:
                    message = error_code or error_desc or "Erro retornado pelo Avaya."

                if cause:
                    message = f"{message} (cause={cause})"

                errors.append(message)

        return errors

    # ==========================================================
    # RESPONSE STATUS
    # ==========================================================

    def _extract_api_status(
        self,
        root: ET.Element,
    ) -> Optional[str]:
        """
        Extrai o Response Status.

        A API Avaya documenta respostas como:

            <response status="1">

        Portanto, atributo é prioritário.

        Também aceita variantes por elemento para
        compatibilidade com respostas diferentes.
        """

        # ------------------------------------------------------
        # 1. atributo status do root
        # ------------------------------------------------------

        if root.tag.lower() == "response":

            status = root.attrib.get(
                "status",
            )

            if status is not None:

                return self._clean_text(
                    status,
                )

        # ------------------------------------------------------
        # 2. atributo status em qualquer elemento
        # ------------------------------------------------------

        status = self._extract_attribute(
            root,
            (
                "status",
                "Status",
                "responseStatus",
                "ResponseStatus",
            ),
        )

        if status is not None:
            return status

        # ------------------------------------------------------
        # 3. elemento <status>
        # ------------------------------------------------------

        status = self._extract_element_text(
            root,
            (
                "status",
                "Status",
                "responseStatus",
                "ResponseStatus",
            ),
        )

        if status is not None:
            return status

        return None

    # ==========================================================
    # GUID
    # ==========================================================

    def _extract_guid(
        self,
        root: ET.Element,
    ) -> Optional[str]:
        """
        Extrai GUID do usuário.

        A resposta documentada pela Avaya utiliza:

            <User GUID="...">

        portanto verificamos atributos antes de procurar
        elementos <GUID>.
        """

        # ------------------------------------------------------
        # Procurar User especificamente
        # ------------------------------------------------------

        for element in root.iter():

            if element.tag.lower() == "user":

                guid = (
                    element.attrib.get("GUID")
                    or element.attrib.get("Guid")
                    or element.attrib.get("guid")
                )

                if guid:

                    return self._clean_text(
                        guid,
                    )

        # ------------------------------------------------------
        # Fallback para atributo GUID
        # ------------------------------------------------------

        guid = self._extract_attribute(
            root,
            (
                "GUID",
                "Guid",
                "guid",
            ),
        )

        if guid is not None:
            return guid

        # ------------------------------------------------------
        # Fallback para elemento GUID
        # ------------------------------------------------------

        guid = self._extract_element_text(
            root,
            (
                "GUID",
                "Guid",
                "guid",
            ),
        )

        return guid

    # ==========================================================
    # MESSAGE
    # ==========================================================

    def _extract_message(
        self,
        root: ET.Element,
    ) -> Optional[str]:
        """
        Extrai mensagem não sensível da resposta.
        """

        # Elementos comuns.
        message = self._extract_element_text(
            root,
            (
                "message",
                "Message",
                "description",
                "Description",
                "error",
                "Error",
                "result",
                "Result",
            ),
        )

        if message:
            return message

        # Atributos comuns.
        return self._extract_attribute(
            root,
            (
                "message",
                "Message",
                "description",
                "Description",
                "error",
                "Error",
                "result",
                "Result",
            ),
        )

    # ==========================================================
    # RESPONSE DIAGNOSTICS
    # ==========================================================

    def _log_safe_xml_structure(
        self,
        root: ET.Element,
    ) -> None:
        """
        Registra somente a estrutura do XML.

        Nunca registra:
          - valores de senha
          - LoginCode
          - VoicemailCode
          - XML completo
        """

        try:

            tags: list[str] = []

            for element in root.iter():

                tags.append(
                    element.tag,
                )

            attributes: list[dict[str, Any]] = []

            for element in root.iter():

                if not element.attrib:
                    continue

                attributes.append(
                    {
                        "tag": element.tag,
                        "attributes": list(
                            element.attrib.keys()
                        ),
                    }
                )

            logger.info(
                "[AVAYA PROVISIONING] "
                "XML response root=%s | tags=%s",
                root.tag,
                tags[:100],
            )

            logger.info(
                "[AVAYA PROVISIONING] "
                "XML response attributes=%s",
                attributes[:100],
            )

        except Exception:

            logger.debug(
                "[AVAYA PROVISIONING] "
                "Não foi possível registrar estrutura XML.",
                exc_info=True,
            )

    # ==========================================================
    # PARSE RESPONSE
    # ==========================================================

    def parse_response(
        self,
        response: Any,
    ) -> ProvisioningResult:
        """
        Interpreta a resposta do Avaya.

        Regras:

            HTTP 201 + vazio
                sucesso.

            HTTP 201 + XML
                sucesso.

            HTTP 200 + XML status=1
                sucesso.

            HTTP 200 + XML status=0
                falha.

            HTTP 200 + XML sem status
                sucesso NÃO confirmado.

            HTTP >=400
                erro HTTP.

            XML inválido
                erro de resposta.
        """

        status_code = getattr(
            response,
            "status_code",
            None,
        )

        content = getattr(
            response,
            "content",
            b"",
        ) or b""

        logger.info(
            "[AVAYA PROVISIONING] RAW RESPONSE | HTTP=%s | CONTENT_TYPE=%s | LENGTH=%s",
            response.status_code,
            response.headers.get("Content-Type"),
            len(content),
        )

        # Não registra o corpo completo da resposta: ele pode conter
        # informações operacionais/sensíveis do Avaya.

        headers = getattr(
            response,
            "headers",
            {},
        )

        content_type = headers.get(
            "Content-Type",
            "",
        )

        content_length = headers.get(
            "Content-Length",
        )

        location = headers.get(
            "Location",
        )

        # ------------------------------------------------------
        # LOG HTTP SEGURO
        # ------------------------------------------------------

        logger.info(
            "[AVAYA PROVISIONING] POST %s -> HTTP %s | "
            "Content-Type=%s | Content-Length=%s | "
            "Location=%s | BodyBytes=%s",
            self.USERS_PATH,
            status_code,
            content_type,
            content_length,
            location,
            len(content),
        )

        # ------------------------------------------------------
        # HTTP INVÁLIDO
        # ------------------------------------------------------

        if status_code is None:

            return ProvisioningResult(
                success=False,
                http_status=None,
                api_status=None,
                guid=None,
                message="Resposta HTTP inválida.",
                error="INVALID_HTTP_RESPONSE",
            )

        # ------------------------------------------------------
        # HTTP ERROR
        # ------------------------------------------------------

        if status_code >= 400:

            return ProvisioningResult(
                success=False,
                http_status=status_code,
                api_status=None,
                guid=None,
                message=(
                    f"Avaya retornou HTTP {status_code}."
                ),
                error="AVAYA_HTTP_ERROR",
            )

        # ------------------------------------------------------
        # BODY EMPTY
        # ------------------------------------------------------

        if not content.strip():

            if status_code == 201:

                logger.info(
                    "[AVAYA PROVISIONING] "
                    "HTTP 201 recebido sem corpo."
                )

                return ProvisioningResult(
                    success=True,
                    http_status=201,
                    api_status="1",
                    guid=None,
                    message=(
                        "Usuário criado no Avaya. "
                        "O servidor retornou HTTP 201 "
                        "sem corpo."
                    ),
                    error=None,
                )

            logger.warning(
                "[AVAYA PROVISIONING] "
                "HTTP %s com corpo vazio. "
                "Sucesso não confirmado.",
                status_code,
            )

            return ProvisioningResult(
                success=False,
                http_status=status_code,
                api_status=None,
                guid=None,
                message=(
                    "Avaya retornou resposta vazia; "
                    "a criação não pôde ser confirmada."
                ),
                error="EMPTY_RESPONSE",
            )

        # ------------------------------------------------------
        # PARSE XML
        # ------------------------------------------------------

        try:

            root = ET.fromstring(
                content,
            )

        except ET.ParseError as exc:

            logger.warning(
                "[AVAYA PROVISIONING] "
                "Resposta não XML | HTTP=%s | "
                "BodyBytes=%s | ParseError=%s",
                status_code,
                len(content),
                str(exc),
            )

            return ProvisioningResult(
                success=False,
                http_status=status_code,
                api_status=None,
                guid=None,
                message=(
                    "Avaya retornou conteúdo que não "
                    "pôde ser interpretado como XML."
                ),
                error="INVALID_XML_RESPONSE",
            )

        # ------------------------------------------------------
        # DIAGNÓSTICO SEGURO
        # ------------------------------------------------------

        self._log_safe_xml_structure(
            root,
        )

        # ------------------------------------------------------
        # BUSINESS ERRORS
        # ------------------------------------------------------
        business_errors = self._extract_business_errors(
            root,
        )

        if business_errors:
            logger.warning(
                "[AVAYA PROVISIONING] Erros de negócio retornados "
                "pelo Avaya: %s",
                business_errors,
            )

            return ProvisioningResult(
                success=False,
                http_status=status_code,
                api_status=self._extract_api_status(root) or "0",
                guid=None,
                message=business_errors[0],
                error="; ".join(business_errors),
            )

        # ------------------------------------------------------
        # STATUS
        # ------------------------------------------------------

        api_status = self._extract_api_status(
            root,
        )

        # ------------------------------------------------------
        # GUID
        # ------------------------------------------------------

        guid = self._extract_guid(
            root,
        )

        # ------------------------------------------------------
        # MESSAGE
        # ------------------------------------------------------

        message = self._extract_message(
            root,
        )

        # ------------------------------------------------------
        # NORMALIZA STATUS
        # ------------------------------------------------------

        normalized_status = (
            api_status.lower()
            if api_status
            else None
        )

        # ------------------------------------------------------
        # SUCESSO
        # ------------------------------------------------------

        if normalized_status in {
            "1",
            "true",
            "success",
            "ok",
        }:

            if not message:

                message = (
                    "Usuário criado com sucesso "
                    "no Avaya."
                )

            logger.info(
                "[AVAYA PROVISIONING] "
                "Criação confirmada | HTTP=%s | "
                "APIStatus=%s | GUID=%s",
                status_code,
                api_status,
                guid or "[não informado]",
            )

            return ProvisioningResult(
                success=True,
                http_status=status_code,
                api_status=api_status,
                guid=guid,
                message=message,
                error=None,
            )

        # ------------------------------------------------------
        # FALHA EXPLÍCITA
        # ------------------------------------------------------

        if normalized_status in {
            "0",
            "false",
            "error",
            "failed",
            "failure",
        }:

            if not message:

                message = (
                    "Avaya informou falha "
                    "na operação."
                )

            logger.warning(
                "[AVAYA PROVISIONING] "
                "Avaya informou falha | HTTP=%s | "
                "APIStatus=%s",
                status_code,
                api_status,
            )

            return ProvisioningResult(
                success=False,
                http_status=status_code,
                api_status=api_status,
                guid=guid,
                message=message,
                error="AVAYA_OPERATION_FAILED",
            )

        # ------------------------------------------------------
        # HTTP 201
        # ------------------------------------------------------

        if status_code == 201:

            if not message:

                message = (
                    "Usuário criado com sucesso "
                    "no Avaya."
                )

            logger.info(
                "[AVAYA PROVISIONING] "
                "HTTP 201 considerado sucesso | "
                "GUID=%s",
                guid or "[não informado]",
            )

            return ProvisioningResult(
                success=True,
                http_status=status_code,
                api_status=api_status,
                guid=guid,
                message=message,
                error=None,
            )

        # ------------------------------------------------------
        # HTTP 200 SEM STATUS
        # ------------------------------------------------------

        logger.warning(
            "[AVAYA PROVISIONING] "
            "HTTP=%s | APIStatus=%s | "
            "GUID=%s | criação não confirmada.",
            status_code,
            api_status or "[não informado]",
            guid or "[não informado]",
        )

        if not message:

            message = (
                "Avaya retornou HTTP 200, "
                "mas não informou um Response Status "
                "reconhecível."
            )

        return ProvisioningResult(
            success=False,
            http_status=status_code,
            api_status=api_status,
            guid=guid,
            message=message,
            error="AVAYA_CREATE_NOT_CONFIRMED",
        )

    # Compatibilidade com versões anteriores.
    def _parse_response(
        self,
        response: Any,
    ) -> ProvisioningResult:

        return self.parse_response(
            response,
        )

    # ==========================================================
    # VALIDATE USER NAME
    # ==========================================================

    def validate_user_name(
        self,
        name: str,
    ) -> list[str]:
        """Valida o campo Name conforme as restrições do Avaya."""

        errors: list[str] = []
        value = str(name or "")

        if not value:
            errors.append("Name é obrigatório.")
            return errors

        if len(value) > 15:
            errors.append("Name deve possuir no máximo 15 caracteres.")

        if value.startswith(" "):
            errors.append("Name não pode iniciar com espaço.")

        if not re.fullmatch(r"[A-Za-z0-9 ]+", value):
            errors.append("Name contém caracteres inválidos. Use somente letras, números e espaços.")

        return errors

    # ==========================================================
    # CREATE USER
    # ==========================================================

    def create_user(
        self,
        **kwargs: Any,
    ) -> ProvisioningResult:
        """
        Executa o POST real de criação no Avaya.

        `target_ip` define explicitamente o nó da Solution
        no qual o usuário deverá ser criado.
        """

        target_ip = str(
            kwargs.pop("target_ip", "")
        ).strip()

        if not target_ip:
            return ProvisioningResult(
                success=False,
                http_status=422,
                api_status="0",
                guid=None,
                message="target_ip não informado.",
                error="target_ip é obrigatório.",
            )

        try:
            ipaddress.ip_address(target_ip)
        except ValueError:
            return ProvisioningResult(
                success=False,
                http_status=422,
                api_status="0",
                guid=None,
                message="target_ip inválido.",
                error=(
                    "target_ip deve ser um endereço IP válido."
                ),
            )

        name = kwargs.get("name", "")

        name_errors = self.validate_user_name(
            str(name),
        )

        if name_errors:
            logger.warning(
                "[AVAYA PROVISIONING] Validação local rejeitou "
                "usuário | Name=%s | errors=%s",
                name,
                name_errors,
            )

            return ProvisioningResult(
                success=False,
                http_status=422,
                api_status="0",
                guid=None,
                message=name_errors[0],
                error="; ".join(name_errors),
            )

        xml_bytes = self.build_user_xml(
            **kwargs,
        )

        logger.info(
            "[AVAYA PROVISIONING] "
            "Iniciando criação | Name=%s | Extension=%s | TargetIP=%s",
            kwargs.get("name"),
            kwargs.get("extension"),
            target_ip,
        )

        try:

            # IMPORTANTE:
            # client.request() espera uma URL absoluta.
            # USERS_PATH é somente o path relativo da API; usar o path
            # diretamente aqui provoca:
            #   Invalid URL ... No scheme supplied
            # O target_ip deve ser enviado como query parameter, exatamente
            # como no GET /admin/v1/users.
            response = self.client.request(
                "POST",
                self.client.users_url,
                params={"ipaddress": target_ip},
                data=xml_bytes,
                headers={
                    "Content-Type": "application/xml",
                    "Accept": "application/xml",
                    "X-User-Client": "Avaya-WebAdmin",
                    "X-User-Agent": "Avaya-SDKUser",
                },
            )
            logger.info(
                "[AVAYA PROVISIONING] HTTP response | status=%s | content_type=%s | length=%s",
                response.status_code,
                response.headers.get("Content-Type"),
                len(response.content or b""),
            )

            # O parse_response() registra somente metadados e estrutura
            # segura do XML; não registrar o corpo completo aqui.

        except Exception as exc:

            logger.exception(
                "[AVAYA PROVISIONING] "
                "Erro durante POST no Avaya."
            )

            return ProvisioningResult(
                success=False,
                http_status=None,
                api_status=None,
                guid=None,
                message=(
                    "Erro durante a comunicação "
                    "com o Avaya."
                ),
                error=str(exc),
            )

        return self.parse_response(
            response,
        )

    # ==========================================================
    # GET USER BY GUID
    # ==========================================================

    def get_user_by_guid(
        self,
        guid: str,
    ) -> Optional[dict[str, Any]]:
        """
        Consulta usuário por GUID, quando suportado
        pelo client.
        """

        if not guid:
            return None

        try:

            return self.client.get_user_by_guid(
                guid,
            )

        except AttributeError:

            logger.warning(
                "[AVAYA PROVISIONING] "
                "client.get_user_by_guid() "
                "não está implementado."
            )

            return None

        except Exception:

            logger.exception(
                "[AVAYA PROVISIONING] "
                "Erro consultando GUID."
            )

            return None
