from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.db import get_db
from app.auth import require_roles
from app.models import OperatorUser, AuditLog
from app.security import hash_password
router=APIRouter(); ROLES={"admin","provisioning","readonly"}
class OperatorCreate(BaseModel):
    username:str=Field(min_length=3,max_length=80); password:str=Field(min_length=8,max_length=128); role:str
@router.post("/operators")
def create_operator(data:OperatorCreate,db:Session=Depends(get_db),operator=Depends(require_roles("admin"))):
    if data.role not in ROLES: raise HTTPException(400,"Perfil inválido")
    if db.query(OperatorUser).filter_by(username=data.username).first(): raise HTTPException(409,"Usuário já existe")
    row=OperatorUser(username=data.username,password_hash=hash_password(data.password),role=data.role,active=True); db.add(row); db.commit()
    db.add(AuditLog(username=operator.username,action="CREATE_OPERATOR",resource=data.username,detail=f"role={data.role}")); db.commit()
    return {"id":row.id,"username":row.username,"role":row.role,"active":row.active}
@router.get("/operators")
def list_operators(db:Session=Depends(get_db),operator=Depends(require_roles("admin"))):
    return [{"id":x.id,"username":x.username,"role":x.role,"active":x.active,"created_at":x.created_at,"last_login_at":x.last_login_at} for x in db.query(OperatorUser).all()]
@router.get("/audit")
def audit_log(limit:int=100,db:Session=Depends(get_db),operator=Depends(require_roles("admin"))):
    limit=min(max(limit,1),500)
    return [{"id":x.id,"username":x.username,"action":x.action,"resource":x.resource,"success":x.success,"detail":x.detail,"created_at":x.created_at} for x in db.query(AuditLog).order_by(AuditLog.id.desc()).limit(limit).all()]
