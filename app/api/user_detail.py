from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import current_operator
from app.db import get_db
from app.models import AvayaUserCache, AuditLog
from app.avaya.user_packages import USER_PACKAGE_NAMES

router = APIRouter(prefix="/api/users", tags=["users-detail"])

@router.get("/detail/{user_id}")
def get_user_detail(user_id: int, db: Session = Depends(get_db), operator=Depends(current_operator)):
    row = db.query(AvayaUserCache).filter(AvayaUserCache.id == user_id).first()
    if not row:
        raise HTTPException(status_code=404, detail={
            "code": "USER_NOT_FOUND",
            "message": "Usuário não encontrado no cache.",
            "user_id": user_id,
        })

    db.add(AuditLog(
        username=operator.username,
        action="VIEW_USER",
        resource=f"/api/users/detail/{user_id}",
        success=True,
        detail=f"target_ip={row.target_ip or 'unknown'};guid={row.guid or 'unknown'}",
    ))
    db.commit()

    return {
        "id": row.id,
        "guid": row.guid,
        "target_ip": row.target_ip,
        "name": row.name,
        "extension": row.extension,
        "sip": row.sip,
        "category": row.category,
        "assigned_package": row.assigned_package,
        "assigned_package_name": USER_PACKAGE_NAMES.get(row.assigned_package),
        "record_type": row.record_type,
        "active": row.active,
        "last_seen_at": row.last_seen_at,
        "source_updated_at": row.source_updated_at,
    }
