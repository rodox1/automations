from datetime import datetime, timezone
import ipaddress

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.avaya.client import AvayaIPOfficeAPI
from app.db import get_db
from app.auth import current_operator
from app.models import AvayaSystem, AvayaUserCache, AuditLog


router = APIRouter(prefix="/api/systems", tags=["Avaya Systems"])


def _ip(value: str) -> str:
    try:
        parsed = ipaddress.ip_address(value.strip())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="target_ip inválido") from exc

    if parsed.version != 4:
        raise HTTPException(status_code=400, detail="target_ip deve ser IPv4")

    return str(parsed)


class SystemRegisterRequest(BaseModel):
    target_ip: str
    name: str | None = None
    role: str = "expansion"
    active: bool = True


@router.get("")
def list_systems(
    target_ip: str | None = Query(default=None),
    active: bool | None = Query(default=None),
    db: Session = Depends(get_db),
    operator=Depends(current_operator),
):
    q = db.query(AvayaSystem)

    if target_ip:
        q = q.filter(AvayaSystem.target_ip == _ip(target_ip))

    if active is not None:
        q = q.filter(AvayaSystem.active.is_(active))

    systems = q.order_by(AvayaSystem.target_ip).all()

    return {
        "count": len(systems),
        "items": [
            {
                "id": system.id,
                "target_ip": system.target_ip,
                "name": system.name,
                "role": system.role,
                "major_version": system.major_version,
                "minor_version": system.minor_version,
                "configuration_version": system.configuration_version,
                "build_version": system.build_version,
                "active": system.active,
                "discovered": system.discovered,
                "last_seen_at": system.last_seen_at,
            }
            for system in systems
        ],
    }


@router.get("/summary")
def systems_summary(
    target_ip: str | None = Query(default=None),
    db: Session = Depends(get_db),
    operator=Depends(current_operator),
):
    ip_filter = _ip(target_ip) if target_ip else None

    q = db.query(AvayaSystem)
    if ip_filter:
        q = q.filter(AvayaSystem.target_ip == ip_filter)

    systems = q.order_by(AvayaSystem.target_ip).all()

    items = []
    total_users = 0
    total_active_users = 0
    total_inactive_users = 0
    total_system_records = 0

    for system in systems:
        base_query = db.query(AvayaUserCache).filter(
            AvayaUserCache.target_ip == system.target_ip
        )

        user_query = base_query.filter(AvayaUserCache.record_type == "USER")
        system_query = base_query.filter(AvayaUserCache.record_type == "SYSTEM")

        user_total = user_query.count()
        active_users = user_query.filter(
            AvayaUserCache.active.is_(True)
        ).count()
        inactive_users = user_query.filter(
            AvayaUserCache.active.is_(False)
        ).count()
        system_records = system_query.count()
        active_system_records = system_query.filter(
            AvayaUserCache.active.is_(True)
        ).count()
        inactive_system_records = system_query.filter(
            AvayaUserCache.active.is_(False)
        ).count()

        last_user_sync = (
            user_query
            .filter(AvayaUserCache.last_seen_at.isnot(None))
            .order_by(AvayaUserCache.last_seen_at.desc())
            .first()
        )
        last_user_sync_at = (
            last_user_sync.last_seen_at
            if last_user_sync
            else None
        )

        total_users += user_total
        total_active_users += active_users
        total_inactive_users += inactive_users
        total_system_records += system_records

        items.append(
            {
                "id": system.id,
                "target_ip": system.target_ip,
                "name": system.name,
                "role": system.role,
                "major_version": system.major_version,
                "minor_version": system.minor_version,
                "configuration_version": system.configuration_version,
                "build_version": system.build_version,
                "active": system.active,
                "discovered": system.discovered,
                "last_system_seen_at": system.last_seen_at,
                "users": {
                    "total": user_total,
                    "active": active_users,
                    "inactive": inactive_users,
                    "last_sync_at": last_user_sync_at,
                },
                "system_records": {
                    "total": system_records,
                    "active": active_system_records,
                    "inactive": inactive_system_records,
                },
            }
        )

    return {
        "total_systems": len(items),
        "total_users": total_users,
        "total_active_users": total_active_users,
        "total_inactive_users": total_inactive_users,
        "total_system_records": total_system_records,
        "target_ip": ip_filter,
        "items": items,
    }


@router.get("/inventory")
def systems_inventory(
    target_ip: str | None = Query(default=None),
    db: Session = Depends(get_db),
    operator=Depends(current_operator),
):
    """Visão consolidada do inventário conhecido, sem consultar o Avaya."""
    ip_filter = _ip(target_ip) if target_ip else None

    query = db.query(AvayaSystem)
    if ip_filter:
        query = query.filter(AvayaSystem.target_ip == ip_filter)

    systems = query.order_by(AvayaSystem.target_ip).all()

    items = []
    total_users = 0
    total_active_users = 0
    total_inactive_users = 0
    total_system_records = 0

    for system in systems:
        base = db.query(AvayaUserCache).filter(
            AvayaUserCache.target_ip == system.target_ip
        )
        users = base.filter(AvayaUserCache.record_type == "USER")
        records = base.filter(AvayaUserCache.record_type == "SYSTEM")

        user_total = users.count()
        user_active = users.filter(AvayaUserCache.active.is_(True)).count()
        user_inactive = users.filter(AvayaUserCache.active.is_(False)).count()
        system_total = records.count()

        total_users += user_total
        total_active_users += user_active
        total_inactive_users += user_inactive
        total_system_records += system_total

        last_user_sync = (
            users.filter(AvayaUserCache.last_seen_at.isnot(None))
            .order_by(AvayaUserCache.last_seen_at.desc())
            .first()
        )

        items.append({
            "id": system.id,
            "target_ip": system.target_ip,
            "name": system.name,
            "role": system.role,
            "version": {
                "major": system.major_version,
                "minor": system.minor_version,
                "configuration": system.configuration_version,
                "build": system.build_version,
            },
            "status": {
                "active": system.active,
                "discovered": system.discovered,
            },
            "last_system_sync": system.last_seen_at,
            "users": {
                "total": user_total,
                "active": user_active,
                "inactive": user_inactive,
                "last_sync": last_user_sync.last_seen_at if last_user_sync else None,
            },
            "system_records": system_total,
        })

    discovered = len(systems)
    # Inventory is user-managed and can contain any number of IPOs.
    expected = len(systems)
    not_discovered = max(expected - discovered, 0)
    active_systems = sum(1 for item in systems if item.active)
    inactive_systems = discovered - active_systems

    return {
        "status": "ok",
        "scope": {
            "expected_systems": expected,
            "target_ip": ip_filter,
        },
        "systems": {
            "discovered": discovered,
            "not_discovered": not_discovered,
            "active": active_systems,
            "inactive": inactive_systems,
        },
        "users": {
            "total": total_users,
            "active": total_active_users,
            "inactive": total_inactive_users,
        },
        "system_records": {
            "total": total_system_records,
        },
        "items": items,
    }


@router.post("/register")
def register_system(
    payload: SystemRegisterRequest,
    db: Session = Depends(get_db),
    operator=Depends(current_operator),
):
    target_ip = _ip(payload.target_ip)

    system = (
        db.query(AvayaSystem)
        .filter(AvayaSystem.target_ip == target_ip)
        .one_or_none()
    )

    if system is None:
        system = AvayaSystem(target_ip=target_ip)
        db.add(system)

    system.name = payload.name
    system.role = payload.role
    system.active = payload.active
    system.updated_at = datetime.now(timezone.utc)

    db.add(
        AuditLog(
            username=operator.username,
            action="system_register",
            resource=target_ip,
            success=True,
            detail=f"Sistema registrado: {payload.name or target_ip}",
        )
    )
    db.commit()
    db.refresh(system)

    return {
        "status": "ok",
        "id": system.id,
        "target_ip": system.target_ip,
        "name": system.name,
        "role": system.role,
        "active": system.active,
    }


@router.post("/sync")
def sync_system(
    target_ip: str,
    db: Session = Depends(get_db),
    operator=Depends(current_operator),
):
    target_ip = _ip(target_ip)

    try:
        client = AvayaIPOfficeAPI(host=target_ip)
        systems = client.get_systems(target_ip=target_ip)

        if not systems:
            raise RuntimeError("A API Avaya não retornou nenhum sistema para o target_ip informado.")

        now = datetime.now(timezone.utc)
        inserted = 0
        updated = 0

        for item in systems:
            item_ip = _ip(str(item.get("LAN1IPAddress") or target_ip))
            system = (
                db.query(AvayaSystem)
                .filter(AvayaSystem.target_ip == item_ip)
                .one_or_none()
            )

            if system is None:
                system = AvayaSystem(target_ip=item_ip)
                db.add(system)
                inserted += 1
            else:
                updated += 1

            system.name = item.get("Name")
            system.role = "primary" if item_ip == client.host else "expansion"
            system.major_version = item.get("MajorVersion")
            system.minor_version = item.get("MinorVersion")
            system.configuration_version = item.get("Configuration")
            system.build_version = item.get("BuildVersion")
            system.active = True
            system.discovered = True
            system.last_seen_at = now
            system.updated_at = now

        db.add(
            AuditLog(
                username=operator.username,
                action="system_sync",
                resource=target_ip,
                success=True,
                detail=f"Sistemas sincronizados: {len(systems)}",
            )
        )
        db.commit()

        return {
            "status": "ok",
            "count": len(systems),
            "inserted": inserted,
            "updated": updated,
            "target_ip": target_ip,
        }

    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()

        detail = f"Falha ao sincronizar sistema {target_ip}: {type(exc).__name__}: {exc}"
        try:
            db.add(
                AuditLog(
                    username=operator.username,
                    action="system_sync",
                    resource=target_ip,
                    success=False,
                    detail=detail[:2000],
                )
            )
            db.commit()
        except Exception:
            db.rollback()

        raise HTTPException(
            status_code=502,
            detail={
                "code": "SYSTEM_SYNC_FAILED",
                "message": "Não foi possível sincronizar o sistema Avaya.",
                "target_ip": target_ip,
                "error": str(exc),
            },
        ) from exc
