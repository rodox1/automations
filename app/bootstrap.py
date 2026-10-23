from app.config import settings
from app.db import SessionLocal
from app.models import OperatorUser
from app.security import hash_password
def ensure_bootstrap_admin():
    if not settings.bootstrap_admin_user or not settings.bootstrap_admin_password: return
    db = SessionLocal()
    try:
        if not db.query(OperatorUser).filter_by(username=settings.bootstrap_admin_user).first():
            db.add(OperatorUser(username=settings.bootstrap_admin_user,
                password_hash=hash_password(settings.bootstrap_admin_password),
                role="admin", active=True))
            db.commit()
    finally: db.close()
