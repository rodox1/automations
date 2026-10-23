from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.auth import current_operator
from app.db import get_db
from app.models import AvayaSystem, AvayaUserCache


router = APIRouter(
    prefix="/api/health",
    tags=["health"],
)


@router.get("/summary")
def health_summary(
    target_ip: str | None = Query(default=None),
    db: Session = Depends(get_db),
    operator=Depends(current_operator),
):
    """
    Resumo operacional consolidado dos IPOs e do cache de usuários.

    Não consulta o Avaya diretamente: utiliza o último estado
    sincronizado no SQLite.
    """

    query = db.query(AvayaSystem)

    if target_ip:
        query = query.filter(AvayaSystem.target_ip == target_ip.strip())

    systems = query.order_by(AvayaSystem.target_ip).all()

    total_systems = len(systems)
    active_systems = sum(1 for item in systems if item.active)
    inactive_systems = total_systems - active_systems
    discovered_systems = sum(1 for item in systems if item.discovered)

    total_users = 0
    active_users = 0
    inactive_users = 0
    total_system_records = 0

    items = []

    for system in systems:
        base_query = db.query(AvayaUserCache).filter(
            AvayaUserCache.target_ip == system.target_ip
        )

        user_query = base_query.filter(
            AvayaUserCache.record_type == "USER"
        )

        system_query = base_query.filter(
            AvayaUserCache.record_type == "SYSTEM"
        )

        user_total = user_query.count()
        user_active = user_query.filter(
            AvayaUserCache.active.is_(True)
        ).count()
        user_inactive = user_query.filter(
            AvayaUserCache.active.is_(False)
        ).count()

        system_records = system_query.count()

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
        active_users += user_active
        inactive_users += user_inactive
        total_system_records += system_records

        items.append(
            {
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
                    "last_sync": last_user_sync_at,
                },
                "system_records": system_records,
            }
        )

    # Status geral baseado exclusivamente no estado persistido.
    if total_systems == 0:
        overall_status = "warning"
    elif active_systems == total_systems and discovered_systems == total_systems:
        overall_status = "ok"
    else:
        overall_status = "warning"

    return {
        "status": overall_status,
        "generated_at": datetime.now(timezone.utc),
        "filter": {
            "target_ip": target_ip.strip() if target_ip else None,
        },
        "systems": {
            "total": total_systems,
            "active": active_systems,
            "inactive": inactive_systems,
            "discovered": discovered_systems,
        },
        "users": {
            "total": total_users,
            "active": active_users,
            "inactive": inactive_users,
        },
        "system_records": {
            "total": total_system_records,
        },
        "items": items,
    }
