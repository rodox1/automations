from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from sqlalchemy.orm import Session
from app.config import settings
from app.db import get_db
from app.models import OperatorUser, AuditLog
from app.security import verify_password, create_access_token, ALGORITHM

router = APIRouter()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

def current_operator(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not username: raise ValueError()
    except (JWTError, ValueError):
        raise HTTPException(401, "Credenciais inválidas")
    user = db.query(OperatorUser).filter_by(username=username, active=True).first()
    if not user: raise HTTPException(401, "Credenciais inválidas")
    return user

def require_roles(*roles):
    def dep(user=Depends(current_operator)):
        if user.role not in roles: raise HTTPException(403, "Acesso não permitido para este perfil")
        return user
    return dep

@router.post("/login")
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(OperatorUser).filter_by(username=form_data.username).first()
    if not user or not user.active or not verify_password(form_data.password, user.password_hash):
        db.add(AuditLog(username=form_data.username, action="LOGIN", success=False, detail="Falha de autenticação"))
        db.commit()
        raise HTTPException(401, "Usuário ou senha inválidos")
    user.last_login_at = datetime.now(timezone.utc)
    db.add(AuditLog(username=user.username, action="LOGIN", success=True, detail=f"role={user.role}"))
    db.commit()
    return {"access_token": create_access_token(user.username, user.role), "token_type": "bearer", "role": user.role}
