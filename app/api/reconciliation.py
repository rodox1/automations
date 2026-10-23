from __future__ import annotations

from datetime import datetime, timezone
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import require_roles
from app.avaya.client import AvayaIPOfficeAPI
from app.avaya.user_packages import user_package_code
from app.db import get_db
from app.models import AuditLog, AvayaUserCache, ProvisioningOperation

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Provisioning Reconciliation"])

RECONCILIABLE_STATUSES = {
    "FAILED",
    "COMPENSATED",
    "RECONCILIATION_REQUIRED",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _serialize(operation: ProvisioningOperation) -> dict:
    return {
        "operation_id": operation.id,
        "idempotency_key": operation.idempotency_key,
        "status": operation.status,
        "target_ip": operation.target_ip,
        "name": operation.name,
        "extension": operation.extension,
        "guid": operation.guid,
        "username": operation.username,
        "request_hash": operation.request_hash,
        "error_code": operation.error_code,
        "error_detail": operation.error_detail,
        "compensation_attempted": operation.compensation_attempted,
        "compensation_success": operation.compensation_success,
        "created_at": operation.created_at,
        "updated_at": operation.updated_at,
    }


def _find_user(users: list[dict], operation: ProvisioningOperation) -> dict | None:
    if operation.guid:
        wanted = operation.guid.casefold()
        for user in users:
            guid = str(user.get("GUID") or user.get("guid") or "").strip()
            if guid and guid.casefold() == wanted:
                return user
        return None

    wanted_name = str(operation.name or "").strip().casefold()
    wanted_extension = str(operation.extension or "").strip()

    for user in users:
        name = str(user.get("Name") or user.get("name") or "").strip().casefold()
        extension = str(user.get("Extension") or user.get("extension") or "").strip()
        if name == wanted_name and extension == wanted_extension:
            return user

    return None


def _cache_user(db: Session, user: dict, target_ip: str) -> None:
    guid = str(user.get("GUID") or user.get("guid") or "").strip()
    if not guid:
        raise RuntimeError("Usuário encontrado no Avaya sem GUID.")

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

    now = _now()
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
    if hasattr(row, "record_type"):
        row.record_type = "USER"
    if hasattr(row, "active"):
        row.active = True
    if hasattr(row, "last_seen_at"):
        row.last_seen_at = now
    row.source_updated_at = now


def _audit(db: Session, operation: ProvisioningOperation, username: str, success: bool, detail: str) -> None:
    db.add(
        AuditLog(
            username=username,
            action="RECONCILE_AVAYA_PROVISIONING",
            resource=f"/api/provisioning/operations/{operation.id}/reconcile",
            success=success,
            detail=detail,
        )
    )
    db.commit()


@router.get("/operations")
def list_operations(
    status: str | None = Query(default=None),
    target_ip: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user=Depends(require_roles("admin", "provisioning")),
):
    query = db.query(ProvisioningOperation)

    if status:
        if status not in RECONCILIABLE_STATUSES and status not in {
            "PENDING", "CREATED", "VERIFIED", "CACHED", "COMPLETED", "COMPENSATING"
        }:
            raise HTTPException(
                status_code=422,
                detail={"code": "INVALID_OPERATION_STATUS", "status": status},
            )
        query = query.filter(ProvisioningOperation.status == status)

    if target_ip:
        query = query.filter(ProvisioningOperation.target_ip == target_ip.strip())

    rows = (
        query.order_by(ProvisioningOperation.updated_at.desc())
        .limit(limit)
        .all()
    )

    return {
        "count": len(rows),
        "reconcilable_statuses": sorted(RECONCILIABLE_STATUSES),
        "operations": [_serialize(row) for row in rows],
    }


@router.get("/operations/{operation_id}")
def get_operation(
    operation_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles("admin", "provisioning")),
):
    operation = db.get(ProvisioningOperation, operation_id)
    if operation is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "OPERATION_NOT_FOUND", "operation_id": operation_id},
        )
    return _serialize(operation)


@router.post("/operations/{operation_id}/reconcile")
def reconcile_operation(
    operation_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles("admin", "provisioning")),
):
    operation = db.get(ProvisioningOperation, operation_id)
    if operation is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "OPERATION_NOT_FOUND", "operation_id": operation_id},
        )

    if operation.status not in RECONCILIABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "OPERATION_NOT_RECONCILIABLE",
                "operation_id": operation.id,
                "status": operation.status,
                "allowed_statuses": sorted(RECONCILIABLE_STATUSES),
            },
        )

    try:
        client = AvayaIPOfficeAPI(host=operation.target_ip)
        users = client.get_users(operation.target_ip)
    except Exception as exc:
        logger.exception(
            "[RECONCILIATION] Falha ao consultar Avaya | operation_id=%s | target_ip=%s",
            operation.id,
            operation.target_ip,
        )
        raise HTTPException(
            status_code=502,
            detail={
                "code": "AVAYA_UNAVAILABLE",
                "message": "Não foi possível consultar o estado real no Avaya.",
                "operation_id": operation.id,
                "error": str(exc),
            },
        )

    user = _find_user(users, operation)
    username = getattr(current_user, "username", "unknown")

    # Caso de compensação: o estado correto é ausência do GUID.
    if operation.status == "COMPENSATED":
        if user is None:
            _audit(
                db,
                operation,
                username,
                True,
                f"COMPENSATED confirmado; GUID ausente no Avaya; operation_id={operation.id}; target_ip={operation.target_ip}",
            )
            return {
                "operation_id": operation.id,
                "status": operation.status,
                "reconciled": True,
                "avaya_state": "ABSENT",
                "action": "NONE",
                "message": "Compensação confirmada no Avaya; usuário não está presente.",
            }

        operation.status = "RECONCILIATION_REQUIRED"
        operation.error_code = "COMPENSATION_INCOMPLETE"
        operation.error_detail = (
            "O GUID da operação continua presente no Avaya após a compensação. "
            "Nenhuma exclusão automática foi executada pela reconciliação."
        )
        operation.updated_at = _now()
        db.add(operation)
        _audit(
            db,
            operation,
            username,
            False,
            f"Compensação incompleta; GUID ainda presente; operation_id={operation.id}; target_ip={operation.target_ip}",
        )
        return {
            "operation_id": operation.id,
            "status": operation.status,
            "reconciled": False,
            "avaya_state": "PRESENT",
            "action": "MANUAL_REVIEW_REQUIRED",
            "guid": operation.guid,
            "message": "O usuário continua no Avaya; nenhuma exclusão automática foi executada.",
        }

    # FAILED sem GUID: nunca transforma uma falha genérica em sucesso.
    if operation.status == "FAILED" and not operation.guid:
        if user is None:
            _audit(
                db,
                operation,
                username,
                True,
                f"FAILED confirmado sem usuário correspondente no Avaya; operation_id={operation.id}; target_ip={operation.target_ip}",
            )
            return {
                "operation_id": operation.id,
                "status": operation.status,
                "reconciled": True,
                "avaya_state": "ABSENT",
                "action": "NONE",
                "message": "Falha confirmada; nenhum usuário correspondente foi encontrado.",
            }

        operation.status = "RECONCILIATION_REQUIRED"
        operation.error_code = "UNTRACKED_AVAYA_USER_DETECTED"
        operation.error_detail = (
            "Foi encontrado no Avaya um usuário com o mesmo nome e extensão, "
            "mas a operação não possui GUID registrado. Revisão manual necessária."
        )
        operation.guid = str(user.get("GUID") or user.get("guid") or "").strip() or None
        operation.updated_at = _now()
        db.add(operation)
        _audit(
            db,
            operation,
            username,
            False,
            f"Usuário não rastreado encontrado no Avaya; operation_id={operation.id}; target_ip={operation.target_ip}",
        )
        return {
            "operation_id": operation.id,
            "status": operation.status,
            "reconciled": False,
            "avaya_state": "PRESENT",
            "action": "MANUAL_REVIEW_REQUIRED",
            "guid": operation.guid,
            "message": "Foi encontrado um usuário correspondente, mas a operação não possuía GUID rastreável.",
        }

    # RECONCILIATION_REQUIRED: só fecha automaticamente o caso conhecido
    # de falha de persistência do cache. Não apaga usuários automaticamente.
    if user is not None:
        guid = str(user.get("GUID") or user.get("guid") or "").strip()
        if guid and not operation.guid:
            operation.guid = guid

        if operation.error_code == "CACHE_PERSISTENCE_FAILED":
            try:
                _cache_user(db, user, operation.target_ip)
                operation.status = "COMPLETED"
                operation.error_code = None
                operation.error_detail = None
                operation.updated_at = _now()
                db.add(operation)
                _audit(
                    db,
                    operation,
                    username,
                    True,
                    f"Cache recuperado e operação concluída; operation_id={operation.id}; guid={operation.guid}",
                )
                return {
                    "operation_id": operation.id,
                    "status": operation.status,
                    "reconciled": True,
                    "avaya_state": "PRESENT",
                    "cache_repaired": True,
                    "action": "CACHE_REPAIRED",
                    "guid": operation.guid,
                }
            except Exception as exc:
                db.rollback()
                raise HTTPException(
                    status_code=500,
                    detail={
                        "code": "CACHE_REPAIR_FAILED",
                        "operation_id": operation.id,
                        "error": str(exc),
                    },
                )

        operation.status = "RECONCILIATION_REQUIRED"
        operation.error_code = operation.error_code or "AVAYA_USER_PRESENT"
        operation.error_detail = (
            operation.error_detail
            or "Usuário presente no Avaya; ação automática não executada."
        )
        operation.updated_at = _now()
        db.add(operation)
        _audit(
            db,
            operation,
            username,
            False,
            f"Usuário presente; reconciliação exige revisão manual; operation_id={operation.id}; guid={operation.guid}",
        )
        return {
            "operation_id": operation.id,
            "status": operation.status,
            "reconciled": False,
            "avaya_state": "PRESENT",
            "action": "MANUAL_REVIEW_REQUIRED",
            "guid": operation.guid,
            "message": "O usuário existe no Avaya; nenhuma alteração destrutiva foi executada.",
        }

    # Usuário ausente em uma RECONCILIATION_REQUIRED originada por
    # compensação: pode ser fechado como COMPENSATED.
    if operation.error_code == "COMPENSATION_FAILED":
        operation.status = "COMPENSATED"
        operation.compensation_attempted = True
        operation.compensation_success = True
        operation.error_code = None
        operation.error_detail = None
        operation.updated_at = _now()
        db.add(operation)
        _audit(
            db,
            operation,
            username,
            True,
            f"Compensação confirmada posteriormente; GUID ausente; operation_id={operation.id}; target_ip={operation.target_ip}",
        )
        return {
            "operation_id": operation.id,
            "status": operation.status,
            "reconciled": True,
            "avaya_state": "ABSENT",
            "action": "COMPENSATION_CONFIRMED",
            "guid": operation.guid,
        }

    operation.status = "RECONCILIATION_REQUIRED"
    operation.updated_at = _now()
    db.add(operation)
    _audit(
        db,
        operation,
        username,
        True,
        f"Operação continua sem confirmação no Avaya; operation_id={operation.id}; target_ip={operation.target_ip}",
    )
    return {
        "operation_id": operation.id,
        "status": operation.status,
        "reconciled": False,
        "avaya_state": "ABSENT",
        "action": "MANUAL_REVIEW_REQUIRED",
        "message": "O usuário não foi encontrado no Avaya; a operação permanece em reconciliação para revisão da causa original.",
    }
