from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import xml.etree.ElementTree as ET

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session
import logging
from pydantic import BaseModel, Field

from app.auth import require_roles
from app.avaya.client import AvayaIPOfficeAPI
from app.avaya.provisioning import AvayaProvisioning
from app.avaya.user_packages import user_package_code
from app.db import get_db
from app.models import AuditLog, AvayaUserCache, ProvisioningOperation

logger = logging.getLogger(__name__)


# ============================================================
# ROUTER
# ============================================================

router = APIRouter(
    prefix="/api/provisioning",
    tags=["Provisioning"],
)


# ============================================================
# SCHEMAS
# ============================================================

class UserPreviewRequest(BaseModel):
    """
    Dados mínimos necessários para validar
    disponibilidade do nome e extensão.
    """

    name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Nome/login do usuário Avaya",
    )

    extension: str = Field(
        ...,
        min_length=1,
        max_length=20,
        description="Extensão do ramal",
    )

    target_ip: str = Field(
        ...,
        min_length=7,
        max_length=15,
        description="IP LAN1 do IPO de destino",
    )


class UserCreateRequest(BaseModel):
    """
    Dados utilizados para criação de usuário.

    Os campos sensíveis são recebidos somente
    no endpoint de criação/dry-run e nunca
    são retornados sem mascaramento.
    """

    name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Nome/login do usuário Avaya",
    )

    password: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Senha do usuário",
    )

    full_name: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Nome completo",
    )

    extension: str = Field(
        ...,
        min_length=1,
        max_length=20,
        description="Extensão do ramal",
    )

    target_ip: str = Field(
        ...,
        min_length=7,
        max_length=15,
        description="IP LAN1 do IPO de destino",
    )

    voicemail_email: str = Field(
        default="",
        max_length=200,
    )

    xdirectory: bool = False

    voicemail_on: bool = True

    voicemail_code: str = Field(
        default="",
        max_length=50,
    )

    receptionist: bool = False

    assigned_package: int = Field(
        default=1,
        ge=1,
        le=8,
        description="Perfil/licença AssignedPackage definido pela API do IP Office 12.x.",
    )

    remote_worker: bool = False

    twinning_type: int = Field(
        default=0,
        ge=0,
    )

    mobility_features: bool = False

    ums_web_services: bool = False

    flare_enabled: bool = False

    login_code: str = Field(
        default="",
        max_length=50,
    )

    sip_name: str = Field(
        default="",
        max_length=200,
    )

    sip_contact: str = Field(
        default="",
        max_length=500,
    )

    web_collaboration: bool = False


# ============================================================
# FUNÇÕES AUXILIARES
# ============================================================

def _request_hash(payload: UserCreateRequest) -> str:
    """Gera hash determinístico do payload completo da operação."""
    raw = json.dumps(
        payload.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _operation_result(operation: ProvisioningOperation) -> dict:
    """Representação segura e determinística de uma operação existente."""
    return {
        "operation_id": operation.id,
        "idempotency_key": operation.idempotency_key,
        "status": operation.status,
        "created": operation.status in {"COMPLETED", "CACHED"},
        "provisioned": bool(operation.guid),
        "verified": operation.status in {"VERIFIED", "CACHED", "COMPLETED"},
        "cached": operation.status in {"CACHED", "COMPLETED"},
        "guid": operation.guid,
        "target_ip": operation.target_ip,
        "name": operation.name,
        "extension": operation.extension,
        "error_code": operation.error_code,
        "error_detail": operation.error_detail,
        "compensation_attempted": operation.compensation_attempted,
        "compensation_success": operation.compensation_success,
        "idempotent_replay": True,
    }


def _set_operation(
    db: Session,
    operation: ProvisioningOperation,
    status: str,
    *,
    guid: str | None = None,
    error_code: str | None = None,
    error_detail: str | None = None,
    compensation_attempted: bool | None = None,
    compensation_success: bool | None = None,
) -> None:
    operation.status = status
    if guid is not None:
        operation.guid = guid
    if error_code is not None:
        operation.error_code = error_code
    if error_detail is not None:
        operation.error_detail = error_detail
    if compensation_attempted is not None:
        operation.compensation_attempted = compensation_attempted
    if compensation_success is not None:
        operation.compensation_success = compensation_success
    operation.updated_at = datetime.now(timezone.utc)
    db.add(operation)
    db.commit()
    db.refresh(operation)


def _delete_target_user(
    client: AvayaIPOfficeAPI,
    guid: str,
    target_ip: str,
) -> None:
    """Compensa uma criação no mesmo Solution Node."""
    # client.request() espera uma URL absoluta.
    # A URL base correta já está disponível em client.users_url.
    # guid e target_ip são enviados como query parameters.
    response = client.request(
        "DELETE",
        client.users_url,
        params={
            "guid": guid,
            "ipaddress": target_ip,
        },
        headers={
            "Accept": "application/xml",
            "X-User-Client": "Avaya-WebAdmin",
            "X-User-Agent": "Avaya-SDKUser",
        },
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"DELETE Avaya retornou HTTP {response.status_code}."
        )

    try:
        root = ET.fromstring(response.content or response.text)
    except ET.ParseError as exc:
        raise RuntimeError("Resposta do DELETE Avaya não é XML válido.") from exc

    if root.attrib.get("status") != "1":
        errors = []
        for error in root.findall(".//error"):
            code = (error.findtext("error_code") or "").strip()
            desc = (error.findtext("error_desc") or "").strip()
            value = ":".join(x for x in (code, desc) if x)
            if value:
                errors.append(value)
        detail = "; ".join(errors) or "Avaya rejeitou a compensação."
        raise RuntimeError(detail)


def _validate_target_ip(
    value: str,
) -> str:
    """
    Valida o endereço IP do IPO de destino.

    O valor representa o IP LAN1 do Solution Node
    utilizado pela Management API.
    """

    value = str(value or "").strip()

    try:
        parsed = ipaddress.ip_address(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "INVALID_TARGET_IP",
                "message": (
                    "target_ip deve ser um endereço IPv4 válido."
                ),
            },
        ) from exc

    if parsed.version != 4:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "INVALID_TARGET_IP",
                "message": (
                    "target_ip deve ser um endereço IPv4."
                ),
            },
        )

    return value


def _get_target_users(
    client: AvayaIPOfficeAPI,
    target_ip: str,
) -> list[dict]:
    """
    Consulta os usuários no Solution Node informado.

    Usa explicitamente:
        GET /admin/v1/users?ipaddress=<target_ip>
    """

    # client.request() espera uma URL absoluta.
    # A URL correta já está disponível em client.users_url.
    # target_ip é enviado como query parameter.
    response = client.request(
        "GET",
        client.users_url,
        params={"ipaddress": target_ip},
        headers={
            "Accept": "application/xml",
            "X-User-Client": "Avaya-WebAdmin",
            "X-User-Agent": "Avaya-SDKUser",
        },
    )

    if response.status_code != 200:
        raise RuntimeError(
            "Avaya retornou HTTP "
            f"{response.status_code} ao consultar "
            f"usuários do target_ip={target_ip}."
        )

    try:
        root = ET.fromstring(
            response.content or response.text
        )
    except ET.ParseError as exc:
        raise RuntimeError(
            "Resposta do Avaya não é um XML válido "
            f"para target_ip={target_ip}."
        ) from exc

    api_status = root.attrib.get("status")

    if api_status != "1":
        errors = []

        for error in root.findall(".//error"):
            desc = (
                error.findtext("error_desc")
                or error.findtext("error_code")
                or ""
            ).strip()

            if desc:
                errors.append(desc)

        detail = "; ".join(errors) or (
            "Avaya retornou status diferente de 1."
        )

        raise RuntimeError(
            f"Falha ao consultar usuários do "
            f"target_ip={target_ip}: {detail}"
        )

    users: list[dict] = []

    for user in root.findall(".//User"):
        item = {
            "GUID": user.attrib.get("GUID"),
        }

        for child in user:
            tag = child.tag.rsplit("}", 1)[-1]
            item[tag] = (
                child.text.strip()
                if child.text
                else ""
            )

        users.append(item)

    return users


def _cache_verified_user(
    db: Session,
    user: dict,
    target_ip: str,
) -> AvayaUserCache:
    """
    Atualiza/inclui no cache local um usuário que acabou de ser
    confirmado pelo GET do mesmo target_ip.

    A função não consulta outro IPO e não altera o Avaya.
    """

    guid = str(user.get("GUID") or user.get("guid") or "").strip()
    if not guid:
        raise RuntimeError("Usuário pós-validado sem GUID; cache não pode ser atualizado.")

    row = (
        db.query(AvayaUserCache)
        .filter(
            AvayaUserCache.guid == guid,
            AvayaUserCache.target_ip == target_ip,
        )
        .first()
    )

    if row is None:
        row = AvayaUserCache(guid=guid)
        db.add(row)

    now = datetime.now(timezone.utc)
    row.target_ip = target_ip
    row.name = user.get("Name") or user.get("name") or ""
    row.extension = user.get("Extension") or user.get("extension") or ""
    row.sip = (
        user.get("SIPName")
        or user.get("sip")
        or user.get("SIPContact")
        or ""
    )
    row.category = user.get("Category") or user.get("category") or ""
    row.assigned_package = user_package_code(
        user.get("AssignedPackage") or user.get("assigned_package")
    )

    # Campos introduzidos nas etapas de status do cache.
    if hasattr(row, "record_type"):
        row.record_type = "USER"
    if hasattr(row, "active"):
        row.active = True
    if hasattr(row, "last_seen_at"):
        row.last_seen_at = now
    row.source_updated_at = now

    db.commit()
    db.refresh(row)
    return row


def _normalize_name(
    value: str,
) -> str:
    return (
        str(value or "")
        .strip()
        .casefold()
    )


def _normalize_extension(
    value: str,
) -> str:
    return (
        str(value or "")
        .strip()
    )


def _check_user_conflicts(
    users: list[dict],
    *,
    name: str,
    extension: str,
) -> tuple[bool, bool]:
    """
    Retorna:

        (name_conflict, extension_conflict)
    """

    requested_name = _normalize_name(
        name
    )

    requested_extension = (
        _normalize_extension(
            extension
        )
    )

    name_conflict = False
    extension_conflict = False

    for user in users:

        current_name = _normalize_name(
            user.get("Name")
            or user.get("name")
            or ""
        )

        current_extension = (
            _normalize_extension(
                user.get("Extension")
                or user.get("extension")
                or ""
            )
        )

        if (
            current_name
            and current_name
            == requested_name
        ):
            name_conflict = True

        if (
            current_extension
            and current_extension
            == requested_extension
        ):
            extension_conflict = True

        if (
            name_conflict
            and extension_conflict
        ):
            break

    return (
        name_conflict,
        extension_conflict,
    )


def _create_provisioning_service(target_ip: str):
    """
    Cria o serviço de provisionamento
    usando o IP do IPO selecionado na requisição.
    """

    client = AvayaIPOfficeAPI(host=_validate_target_ip(target_ip))

    return AvayaProvisioning(
        client
    )


def _payload_to_kwargs(
    payload: UserCreateRequest,
) -> dict:
    """
    Converte o modelo Pydantic para
    os argumentos esperados por
    AvayaProvisioning.build_create_xml().
    """

    return {
        "name": payload.name.strip(),

        "password": payload.password,

        "full_name": payload.full_name.strip(),

        "extension": payload.extension.strip(),

        "target_ip": _validate_target_ip(
            payload.target_ip
        ),

        "voicemail_email": (
            payload.voicemail_email.strip()
        ),

        "xdirectory": payload.xdirectory,

        "voicemail_on": payload.voicemail_on,

        "voicemail_code": (
            payload.voicemail_code
        ),

        "receptionist": payload.receptionist,

        "assigned_package": (
            payload.assigned_package
        ),

        "remote_worker": (
            payload.remote_worker
        ),

        "twinning_type": (
            payload.twinning_type
        ),

        "mobility_features": (
            payload.mobility_features
        ),

        "ums_web_services": (
            payload.ums_web_services
        ),

        "flare_enabled": (
            payload.flare_enabled
        ),

        "login_code": (
            payload.login_code
        ),

        "sip_name": (
            payload.sip_name.strip()
        ),

        "sip_contact": (
            payload.sip_contact.strip()
        ),

        "web_collaboration": (
            payload.web_collaboration
        ),
    }


# ============================================================
# PREVIEW
# ============================================================

@router.post(
    "/users/preview",
)
def preview_user(
    payload: UserPreviewRequest,
    current_user=Depends(
        require_roles(
            "admin",
            "provisioning",
        )
    ),
):
    """
    Valida nome e extensão.

    NÃO cria usuário.
    NÃO altera o Avaya.
    """

    name = payload.name.strip()

    extension = payload.extension.strip()

    if not name:

        raise HTTPException(
            status_code=400,
            detail="name é obrigatório",
        )

    if not extension:

        raise HTTPException(
            status_code=400,
            detail="extension é obrigatório",
        )

    try:

        target_ip = _validate_target_ip(
            payload.target_ip
        )

        client = AvayaIPOfficeAPI(host=target_ip)

        users = _get_target_users(
            client,
            target_ip,
        )

    except Exception as exc:

        raise HTTPException(
            status_code=502,
            detail={
                "code": "AVAYA_UNAVAILABLE",
                "message": (
                    "Não foi possível consultar "
                    "os usuários do Avaya."
                ),
                "error": str(exc),
            },
        )

    (
        name_conflict,
        extension_conflict,
    ) = _check_user_conflicts(
        users,
        name=name,
        extension=extension,
    )

    allowed = (
        not name_conflict
        and not extension_conflict
    )

    return {
        "allowed": allowed,
        "name_conflict": name_conflict,
        "extension_conflict": extension_conflict,
    }


# ============================================================
# DRY-RUN
# ============================================================

@router.post(
    "/users/dry-run",
)
def dry_run_user(
    payload: UserCreateRequest,
    current_user=Depends(
        require_roles(
            "admin",
            "provisioning",
        )
    ),
):
    """
    Monta o XML de criação sem enviar
    nenhuma alteração ao Avaya.

    Os campos sensíveis são mascarados
    na resposta.
    """

    # --------------------------------------------------------
    # Validação contra estado atual do Avaya
    # --------------------------------------------------------

    try:

        target_ip = _validate_target_ip(
            payload.target_ip
        )

        client = AvayaIPOfficeAPI(host=target_ip)

        users = _get_target_users(
            client,
            target_ip,
        )

    except Exception as exc:

        raise HTTPException(
            status_code=502,
            detail={
                "code": "AVAYA_UNAVAILABLE",
                "message": (
                    "Não foi possível consultar "
                    "os usuários do Avaya."
                ),
                "error": str(exc),
            },
        )

    (
        name_conflict,
        extension_conflict,
    ) = _check_user_conflicts(
        users,
        name=payload.name,
        extension=payload.extension,
    )

    if name_conflict or extension_conflict:

        raise HTTPException(
            status_code=409,
            detail={
                "code": "PROVISIONING_CONFLICT",
                "name_conflict": name_conflict,
                "extension_conflict": (
                    extension_conflict
                ),
                "target_ip": target_ip,
                "message": (
                    "O usuário não pode ser "
                    "provisionado devido a conflito."
                ),
            },
        )

    # --------------------------------------------------------
    # Montagem do XML
    # --------------------------------------------------------

    provisioning = (
        AvayaProvisioning(
            client
        )
    )

    kwargs = _payload_to_kwargs(
        payload
    )

    try:

        result = (
            provisioning.dry_run_create(
                **kwargs
            )
        )

    except Exception as exc:

        raise HTTPException(
            status_code=500,
            detail={
                "code": "XML_BUILD_ERROR",
                "message": (
                    "Erro ao montar XML "
                    "de provisionamento."
                ),
                "error": str(exc),
            },
        )

    # --------------------------------------------------------
    # Retorno
    # --------------------------------------------------------

    if isinstance(result, dict):
        result.setdefault(
            "target_ip",
            kwargs.get("target_ip"),
        )

    return result


# ============================================================
# CREATE
# ============================================================

@router.post(
    "/users",
)
def create_user(
    payload: UserCreateRequest,
    confirm: bool = False,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    current_user=Depends(
        require_roles(
            "admin",
            "provisioning",
        )
    ),
):
    """
    Cria um usuário no Avaya com idempotência e compensação.

    confirm=false:
        nenhuma alteração é feita.

    confirm=true:
        executa a operação e registra seu estado no SQLite.
    """

    if not confirm:
        return {
            "allowed": False,
            "created": False,
            "message": (
                "Operação bloqueada. "
                "Execute novamente com confirm=true."
            ),
        }

    if not idempotency_key or not idempotency_key.strip():
        raise HTTPException(
            status_code=400,
            detail={
                "code": "IDEMPOTENCY_KEY_REQUIRED",
                "message": "O header Idempotency-Key é obrigatório.",
            },
        )

    idempotency_key = idempotency_key.strip()
    if len(idempotency_key) > 100:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "IDEMPOTENCY_KEY_TOO_LONG",
                "message": "Idempotency-Key deve possuir no máximo 100 caracteres.",
            },
        )

    request_hash = _request_hash(payload)
    target_ip = _validate_target_ip(payload.target_ip)

    # --------------------------------------------------------
    # Idempotência: primeiro consulta a operação persistida.
    # Um replay deve poder retornar o resultado mesmo se o Avaya
    # estiver temporariamente indisponível.
    # --------------------------------------------------------
    operation = (
        db.query(ProvisioningOperation)
        .filter(ProvisioningOperation.idempotency_key == idempotency_key)
        .one_or_none()
    )

    if operation is not None:
        if operation.request_hash != request_hash:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "IDEMPOTENCY_KEY_REUSE",
                    "message": "Idempotency-Key já foi utilizada com outro payload.",
                    "operation_id": operation.id,
                    "status": operation.status,
                },
            )

        if operation.status == "COMPLETED":
            return _operation_result(operation)

        raise HTTPException(
            status_code=409,
            detail={
                "code": "IDEMPOTENCY_OPERATION_EXISTS",
                "message": (
                    "A operação já existe e não será executada novamente "
                    "com a mesma Idempotency-Key."
                ),
                **_operation_result(operation),
            },
        )

    # Registra a intenção antes de qualquer chamada externa.
    operation = ProvisioningOperation(
        idempotency_key=idempotency_key,
        target_ip=target_ip,
        name=payload.name,
        extension=payload.extension,
        status="PENDING",
        username=getattr(current_user, "username", "unknown"),
        request_hash=request_hash,
        compensation_attempted=False,
    )

    try:
        db.add(operation)
        db.commit()
        db.refresh(operation)
    except Exception as exc:
        db.rollback()
        existing = (
            db.query(ProvisioningOperation)
            .filter(ProvisioningOperation.idempotency_key == idempotency_key)
            .one_or_none()
        )

        if existing is None:
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "IDEMPOTENCY_PERSISTENCE_ERROR",
                    "message": "Não foi possível registrar a operação.",
                    "error": str(exc),
                },
            )

        if existing.request_hash != request_hash:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "IDEMPOTENCY_KEY_REUSE",
                    "message": "Idempotency-Key já foi utilizada com outro payload.",
                    "operation_id": existing.id,
                    "status": existing.status,
                },
            )

        if existing.status == "COMPLETED":
            return _operation_result(existing)

        raise HTTPException(
            status_code=409,
            detail={
                "code": "IDEMPOTENCY_OPERATION_EXISTS",
                "message": "A operação já foi iniciada por outra requisição.",
                **_operation_result(existing),
            },
        )

    # --------------------------------------------------------
    # Consulta estado atual do Avaya
    # --------------------------------------------------------
    try:
        client = AvayaIPOfficeAPI(host=target_ip)
        users = _get_target_users(client, target_ip)
    except Exception as exc:
        _set_operation(
            db,
            operation,
            "FAILED",
            error_code="AVAYA_UNAVAILABLE",
            error_detail=str(exc),
        )
        raise HTTPException(
            status_code=502,
            detail={
                "code": "AVAYA_UNAVAILABLE",
                "message": "Não foi possível consultar os usuários do Avaya.",
                "error": str(exc),
                "operation_id": operation.id,
                "idempotency_key": idempotency_key,
            },
        )

    # --------------------------------------------------------
    # Revalidação antes do POST
    # --------------------------------------------------------
    name_conflict, extension_conflict = _check_user_conflicts(
        users,
        name=payload.name,
        extension=payload.extension,
    )

    if name_conflict or extension_conflict:
        _set_operation(
            db,
            operation,
            "FAILED",
            error_code="PROVISIONING_CONFLICT",
            error_detail=(
                f"name_conflict={name_conflict}; "
                f"extension_conflict={extension_conflict}"
            ),
        )
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PROVISIONING_CONFLICT",
                "name_conflict": name_conflict,
                "extension_conflict": extension_conflict,
                "target_ip": target_ip,
                "operation_id": operation.id,
                "idempotency_key": idempotency_key,
                "message": "O usuário não pode ser criado devido a conflito.",
            },
        )

    # --------------------------------------------------------
    # Provisionamento
    # --------------------------------------------------------
    provisioning = AvayaProvisioning(client)
    kwargs = _payload_to_kwargs(payload)

    try:
        result = provisioning.create_user(**kwargs)
    except Exception as exc:
        _set_operation(
            db,
            operation,
            "FAILED",
            error_code="AVAYA_REQUEST_ERROR",
            error_detail=str(exc),
        )
        raise HTTPException(
            status_code=502,
            detail={
                "code": "AVAYA_REQUEST_ERROR",
                "message": "Erro durante a criação do usuário no Avaya.",
                "error": str(exc),
                "operation_id": operation.id,
                "idempotency_key": idempotency_key,
            },
        )

    if not result.success:
        _set_operation(
            db,
            operation,
            "FAILED",
            guid=result.guid,
            error_code="AVAYA_CREATE_REJECTED",
            error_detail="; ".join(result.errors or []) or "Avaya rejeitou a criação.",
        )
        return {
            "created": False,
            "operation_id": operation.id,
            "idempotency_key": idempotency_key,
            "status": operation.status,
            "http_status": result.http_status,
            "api_status": result.api_status,
            "guid": result.guid,
            "errors": result.errors,
        }

    _set_operation(
        db,
        operation,
        "CREATED",
        guid=result.guid,
    )

    # --------------------------------------------------------
    # VALIDAÇÃO PÓS-CRIAÇÃO
    # --------------------------------------------------------
    try:
        verified_users = _get_target_users(client, target_ip)
        verified_user = next(
            (
                user
                for user in verified_users
                if str(user.get("GUID") or "").lower()
                == str(result.guid or "").lower()
            ),
            None,
        )

        if verified_user is None:
            raise RuntimeError(
                "O GUID retornado pelo POST não foi localizado "
                f"no GET de usuários do target_ip={target_ip}."
            )

        verified_name = verified_user.get("Name") or verified_user.get("name") or ""
        verified_extension = (
            verified_user.get("Extension")
            or verified_user.get("extension")
            or ""
        )

        if _normalize_name(verified_name) != _normalize_name(payload.name):
            raise RuntimeError(
                f"Name retornado pelo GET é diferente: {verified_name!r}."
            )

        if _normalize_extension(verified_extension) != _normalize_extension(
            payload.extension
        ):
            raise RuntimeError(
                f"Extension retornada pelo GET é diferente: {verified_extension!r}."
            )

        _set_operation(
            db,
            operation,
            "VERIFIED",
            guid=result.guid,
        )

        logger.info(
            "[PROVISIONING] Pós-validação OK | target_ip=%s | name=%s | extension=%s | guid=%s",
            target_ip,
            payload.name,
            payload.extension,
            result.guid,
        )

    except Exception as exc:
        verification_error = str(exc)

        logger.exception(
            "[PROVISIONING] Falha na pós-validação | target_ip=%s | guid=%s",
            target_ip,
            result.guid,
        )

        # A criação foi aceita pelo Avaya, portanto a compensação é
        # deliberada e limitada ao GUID recém-criado.
        _set_operation(
            db,
            operation,
            "COMPENSATING",
            guid=result.guid,
            error_code="POST_CREATE_VERIFICATION_FAILED",
            error_detail=verification_error,
            compensation_attempted=True,
        )

        try:
            _delete_target_user(
                client,
                str(result.guid),
                target_ip,
            )

            _set_operation(
                db,
                operation,
                "COMPENSATED",
                guid=result.guid,
                compensation_attempted=True,
                compensation_success=True,
            )

            return {
                "created": False,
                "provisioned": True,
                "verified": False,
                "status": operation.status,
                "compensated": True,
                "operation_id": operation.id,
                "idempotency_key": idempotency_key,
                "http_status": result.http_status,
                "api_status": result.api_status,
                "guid": result.guid,
                "target_ip": target_ip,
                "name": payload.name,
                "extension": payload.extension,
                "errors": [
                    "Validação pós-criação falhou; usuário removido por compensação.",
                    verification_error,
                ],
            }

        except Exception as compensation_exc:
            _set_operation(
                db,
                operation,
                "RECONCILIATION_REQUIRED",
                guid=result.guid,
                error_code="COMPENSATION_FAILED",
                error_detail=(
                    f"Validação: {verification_error}; "
                    f"Compensação: {compensation_exc}"
                ),
                compensation_attempted=True,
                compensation_success=False,
            )

            return {
                "created": True,
                "provisioned": True,
                "verified": False,
                "status": operation.status,
                "compensated": False,
                "reconciliation_required": True,
                "operation_id": operation.id,
                "idempotency_key": idempotency_key,
                "http_status": result.http_status,
                "api_status": result.api_status,
                "guid": result.guid,
                "target_ip": target_ip,
                "name": payload.name,
                "extension": payload.extension,
                "errors": [
                    "Usuário aceito pelo Avaya, mas a validação pós-criação falhou.",
                    f"Compensação também falhou: {compensation_exc}",
                ],
            }

    # --------------------------------------------------------
    # CACHE LOCAL
    # --------------------------------------------------------
    cached = False
    cache_error = None

    try:
        _cache_verified_user(
            db,
            verified_user,
            target_ip,
        )
        cached = True

        _set_operation(
            db,
            operation,
            "CACHED",
            guid=result.guid,
        )

        logger.info(
            "[PROVISIONING] Cache atualizado | target_ip=%s | name=%s | extension=%s | guid=%s",
            target_ip,
            payload.name,
            payload.extension,
            result.guid,
        )

    except Exception as exc:
        db.rollback()
        cache_error = str(exc)

        _set_operation(
            db,
            operation,
            "RECONCILIATION_REQUIRED",
            guid=result.guid,
            error_code="CACHE_PERSISTENCE_FAILED",
            error_detail=cache_error,
        )

        logger.exception(
            "[PROVISIONING] Falha ao atualizar cache | target_ip=%s | guid=%s",
            target_ip,
            result.guid,
        )

        return {
            "created": True,
            "provisioned": True,
            "verified": True,
            "cached": False,
            "reconciliation_required": True,
            "operation_id": operation.id,
            "idempotency_key": idempotency_key,
            "http_status": result.http_status,
            "api_status": result.api_status,
            "guid": result.guid,
            "target_ip": target_ip,
            "name": payload.name,
            "extension": payload.extension,
            "cache_error": cache_error,
            "errors": result.errors,
        }

    # --------------------------------------------------------
    # Auditoria
    # --------------------------------------------------------
    try:
        username = getattr(current_user, "username", "unknown")

        db.add(
            AuditLog(
                username=username,
                action="CREATE_AVAYA_USER",
                resource="/api/provisioning/users",
                success=True,
                detail=(
                    f"name={payload.name};"
                    f"extension={payload.extension};"
                    f"target_ip={target_ip};"
                    f"guid={result.guid};"
                    f"operation_id={operation.id};"
                    f"idempotency_key={idempotency_key}"
                ),
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception(
            "[PROVISIONING] Falha ao gravar auditoria | guid=%s | target_ip=%s",
            result.guid,
            target_ip,
        )

    _set_operation(
        db,
        operation,
        "COMPLETED",
        guid=result.guid,
    )

    return {
        "created": True,
        "provisioned": True,
        "verified": True,
        "status": operation.status,
        "http_status": result.http_status,
        "api_status": result.api_status,
        "guid": result.guid,
        "target_ip": target_ip,
        "name": payload.name,
        "extension": payload.extension,
        "cached": True,
        "cache_error": None,
        "operation_id": operation.id,
        "idempotency_key": idempotency_key,
        "errors": result.errors,
    }
