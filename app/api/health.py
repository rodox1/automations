from fastapi import APIRouter,Depends
from sqlalchemy.orm import Session
from app.db import get_db
from app.auth import current_operator
from app.models import AvayaUserCache
router=APIRouter()
@router.get("")
def health(db:Session=Depends(get_db),operator=Depends(current_operator)):
    return {"status":"ok","database":"ok","cached_users":db.query(AvayaUserCache).count()}
