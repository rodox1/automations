from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["dashboard-ui"])
TEMPLATES = Path(__file__).resolve().parents[1] / "templates"

def _render(name: str) -> HTMLResponse:
    return HTMLResponse((TEMPLATES / name).read_text(encoding="utf-8"))

@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
def dashboard_page():
    return _render("dashboard.html")

@router.get("/dashboard/users", response_class=HTMLResponse, include_in_schema=False)
def dashboard_users_page():
    return _render("dashboard_users.html")

@router.get("/dashboard/user", response_class=HTMLResponse, include_in_schema=False)
def dashboard_user_detail_page():
    return _render("dashboard_user_detail.html")


@router.get("/dashboard/provisioning", response_class=HTMLResponse, include_in_schema=False)
def dashboard_provisioning_page():
    return _render("dashboard_provisioning.html")


@router.get("/dashboard/telephony", response_class=HTMLResponse, include_in_schema=False)
def dashboard_telephony_page():
    return _render("dashboard_telephony.html")
