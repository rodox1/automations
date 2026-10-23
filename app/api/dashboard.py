from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.auth import current_operator
from app.db import get_db
from app.models import (
    AvayaExtensionCache,
    AvayaHuntGroupCache,
    AvayaSystem,
    AvayaUserCache,
    AuditLog,
)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def _system_item(db: Session, system: AvayaSystem) -> dict:
    base = db.query(AvayaUserCache).filter(AvayaUserCache.target_ip == system.target_ip)
    users = base.filter(AvayaUserCache.record_type == "USER")
    system_records = base.filter(AvayaUserCache.record_type == "SYSTEM")
    total = users.count()
    active = users.filter(AvayaUserCache.active.is_(True)).count()
    inactive = users.filter(AvayaUserCache.active.is_(False)).count()
    last_user = (users.filter(AvayaUserCache.last_seen_at.isnot(None))
                 .order_by(AvayaUserCache.last_seen_at.desc()).first())
    extensions = db.query(AvayaExtensionCache).filter(
        AvayaExtensionCache.target_ip == system.target_ip
    )
    huntgroups = db.query(AvayaHuntGroupCache).filter(
        AvayaHuntGroupCache.target_ip == system.target_ip
    )
    extension_total = extensions.count()
    extension_active = extensions.filter(AvayaExtensionCache.active.is_(True)).count()
    huntgroup_total = huntgroups.count()
    huntgroup_active = huntgroups.filter(AvayaHuntGroupCache.active.is_(True)).count()
    return {
        "id": system.id, "target_ip": system.target_ip, "name": system.name, "role": system.role,
        "version": {"major": system.major_version, "minor": system.minor_version,
                     "configuration": system.configuration_version, "build": system.build_version},
        "status": {"active": system.active, "discovered": system.discovered},
        "last_system_sync": system.last_seen_at,
        "users": {"total": total, "active": active, "inactive": inactive,
                   "last_sync": last_user.last_seen_at if last_user else None},
        "extensions": {"total": extension_total, "active": extension_active,
                       "inactive": extension_total - extension_active},
        "huntgroups": {"total": huntgroup_total, "active": huntgroup_active,
                       "inactive": huntgroup_total - huntgroup_active},
        "system_records": system_records.count(),
    }


def _status_from_counts(*, total: int, inactive: int, discovered: int | None = None, expected: int | None = None) -> str:
    if total == 0:
        return "unknown"
    if inactive > 0:
        return "warning"
    if expected is not None and discovered is not None and discovered < expected:
        return "partial"
    return "ok"


@router.get("/overview")
def dashboard_overview(
    target_ip: str | None = Query(default=None),
    audit_limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
    operator=Depends(current_operator),
):
    query = db.query(AvayaSystem)
    scoped_ip = target_ip.strip() if target_ip and target_ip.strip() else None
    if scoped_ip:
        query = query.filter(AvayaSystem.target_ip == scoped_ip)

    systems = query.order_by(AvayaSystem.role, AvayaSystem.target_ip).all()
    expected_systems = len(systems)
    items = [_system_item(db, system) for system in systems]
    total_systems = len(systems)
    discovered = sum(1 for item in items if item["status"]["discovered"])
    active_systems = sum(1 for item in items if item["status"]["active"])
    inactive_systems = total_systems - active_systems
    total_users = sum(item["users"]["total"] for item in items)
    active_users = sum(item["users"]["active"] for item in items)
    inactive_users = sum(item["users"]["inactive"] for item in items)
    total_system_records = sum(item["system_records"] for item in items)
    total_extensions = sum(item["extensions"]["total"] for item in items)
    active_extensions = sum(item["extensions"]["active"] for item in items)
    total_huntgroups = sum(item["huntgroups"]["total"] for item in items)
    active_huntgroups = sum(item["huntgroups"]["active"] for item in items)

    sync_failures_query = db.query(AuditLog).filter(AuditLog.action == "system_sync", AuditLog.success.is_(False))
    failed_syncs = sync_failures_query.count()
    recent_failures = sync_failures_query.order_by(AuditLog.created_at.desc()).limit(audit_limit).all()
    recent_audit = db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(audit_limit).all()

    coverage = round((discovered / expected_systems) * 100, 2) if expected_systems else 0.0
    coverage_status = "unknown" if expected_systems == 0 else ("ok" if discovered >= expected_systems else "partial")
    sync_status = "warning" if failed_syncs > 0 else "ok"
    system_status = _status_from_counts(total=total_systems, inactive=inactive_systems)
    user_status = _status_from_counts(total=total_users, inactive=inactive_users)
    overall_status = "warning" if sync_status == "warning" or system_status == "warning" else ("partial" if coverage_status == "partial" else "ok")

    return {
        "status": overall_status,
        "generated_at": datetime.now(timezone.utc),
        "scope": {"expected_systems": expected_systems, "target_ip": scoped_ip},
        "coverage": {"discovered": discovered, "not_discovered": max(expected_systems - discovered, 0),
                     "percentage": coverage, "status": coverage_status},
        "systems": {"total": total_systems, "active": active_systems, "inactive": inactive_systems, "status": system_status},
        "users": {"total": total_users, "active": active_users, "inactive": inactive_users, "status": user_status},
        "extensions": {"total": total_extensions, "active": active_extensions,
                       "inactive": total_extensions - active_extensions},
        "huntgroups": {"total": total_huntgroups, "active": active_huntgroups,
                       "inactive": total_huntgroups - active_huntgroups},
        "system_records": {"total": total_system_records},
        "sync": {"failed_system_syncs": failed_syncs, "status": sync_status,
                 "recent_failures": [{"id": x.id, "resource": x.resource, "detail": x.detail, "created_at": x.created_at} for x in recent_failures]},
        "items": items,
        "recent_audit": [{"id": x.id, "username": x.username, "action": x.action, "resource": x.resource,
                          "success": x.success, "detail": x.detail, "created_at": x.created_at} for x in recent_audit],
    }
