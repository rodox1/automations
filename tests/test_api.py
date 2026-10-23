"""
Testes de integração da API Avaya FastAPI (Stage 2).

Usa o FastAPI TestClient + SQLite em memória.
Não requer conexão com o Avaya IP Office.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.db import Base, get_db, engine as app_engine
from app.models import OperatorUser, AvayaUserCache, AvayaSystem, AvayaHuntGroupCache, AvayaExtensionCache
from app.security import hash_password
from app.config import settings
from app.avaya.client import AvayaIPOfficeAPI


# ---------------------------------------------------------------------------
# Fixture: banco SQLite temporário (em memória) para cada teste
# ---------------------------------------------------------------------------

@pytest.fixture
def db_session(tmp_path, monkeypatch):
    """Cria um banco SQLite temporário e faz a app usá-lo."""
    db_file = tmp_path / "test.db"
    test_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=test_engine)

    TestSessionLocal = sessionmaker(bind=test_engine, autocommit=False, autoflush=False)

    def override_get_db():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    # Bootstrap admin direto no banco de teste
    db = TestSessionLocal()
    if not db.query(OperatorUser).filter_by(username="admin").first():
        db.add(OperatorUser(
            username="admin",
            password_hash=hash_password("admin"),
            role="admin",
            active=True,
        ))
        db.commit()
    db.close()

    yield TestSessionLocal

    app.dependency_overrides.clear()


@pytest.fixture
def client(db_session):
    return TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def login(client: TestClient, username: str = "admin", password: str = "admin"):
    r = client.post(
        "/api/auth/login",
        data={"username": username, "password": password},
    )
    assert r.status_code == 200, f"Login falhou: {r.text}"
    return r.json()["access_token"]


# ---------------------------------------------------------------------------
# Testes
# ---------------------------------------------------------------------------

def test_root(client):
    r = client.get("/")
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "avaya-fastapi"
    assert body["version"] == "3.0.0"


def test_login_success(client):
    token = login(client)
    assert token is not None and len(token) > 0


def test_login_wrong_password(client):
    r = client.post(
        "/api/auth/login",
        data={"username": "admin", "password": "senha_errada"},
    )
    assert r.status_code == 401


def test_login_unknown_user(client):
    r = client.post(
        "/api/auth/login",
        data={"username": "inexistente", "password": "qualquer"},
    )
    assert r.status_code == 401


def test_health(client):
    token = login(client)
    r = client.get("/api/health", headers=auth_headers(token))
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"


def test_health_summary(client):
    token = login(client)
    r = client.get("/api/health/summary", headers=auth_headers(token))
    assert r.status_code == 200
    body = r.json()
    assert "status" in body
    assert "systems" in body
    assert "users" in body


def test_dashboard_overview(client):
    token = login(client)
    r = client.get("/api/dashboard/overview", headers=auth_headers(token))
    assert r.status_code == 200
    body = r.json()
    assert "status" in body
    assert "coverage" in body
    assert "systems" in body
    assert "users" in body


def test_dashboard_login_flow_does_not_require_existing_token(client):
    """O login do dashboard deve ser possível sem token prévio em localStorage."""
    r = client.get("/dashboard")
    assert r.status_code == 200
    html = r.text
    # O helper _api não pode bloquear a própria chamada de login.
    assert "skipAuth:true" in html
    assert "Sessão não encontrada" in html
    login_call = html.split("/api/auth/login", 1)[1].split(")\n", 1)[0]
    assert "skipAuth:true" in login_call


def test_list_systems_empty(client):
    token = login(client)
    r = client.get("/api/systems", headers=auth_headers(token))
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 0
    assert body["items"] == []


def test_list_systems_summary_empty(client):
    token = login(client)
    r = client.get("/api/systems/summary", headers=auth_headers(token))
    assert r.status_code == 200
    body = r.json()
    assert body["total_systems"] == 0


def test_register_system(client):
    token = login(client)
    r = client.post(
        "/api/systems/register",
        json={"target_ip": "172.16.36.50", "name": "Primary", "role": "primary"},
        headers=auth_headers(token),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["target_ip"] == "172.16.36.50"
    assert body["name"] == "Primary"
    assert body["role"] == "primary"


def test_list_users_empty(client):
    token = login(client)
    r = client.get("/api/users", headers=auth_headers(token))
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0
    assert body["items"] == []


def test_create_operator_admin(client):
    """Somente admin pode criar operadores."""
    token = login(client, "admin", "admin")
    r = client.post(
        "/api/admin/operators",
        json={
            "username": "operador1",
            "password": "Senha#1234",
            "role": "provisioning",
        },
        headers=auth_headers(token),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["username"] == "operador1"
    assert body["role"] == "provisioning"


def test_create_operator_invalid_role(client):
    token = login(client, "admin", "admin")
    r = client.post(
        "/api/admin/operators",
        json={
            "username": "operador2",
            "password": "Senha#1234",
            "role": "invalido",
        },
        headers=auth_headers(token),
    )
    assert r.status_code == 400


def test_create_operator_duplicate(client):
    token = login(client, "admin", "admin")
    # Criar primeiro
    client.post(
        "/api/admin/operators",
        json={
            "username": "operador3",
            "password": "Senha#1234",
            "role": "readonly",
        },
        headers=auth_headers(token),
    )
    # Tentar duplicado
    r = client.post(
        "/api/admin/operators",
        json={
            "username": "operador3",
            "password": "Senha#1234",
            "role": "readonly",
        },
        headers=auth_headers(token),
    )
    assert r.status_code == 409


def test_list_operators(client):
    token = login(client, "admin", "admin")
    r = client.get("/api/admin/operators", headers=auth_headers(token))
    assert r.status_code == 200
    body = r.json()
    assert any(op["username"] == "admin" for op in body)


def test_audit_log(client):
    token = login(client, "admin", "admin")
    r = client.get("/api/admin/audit", headers=auth_headers(token))
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)


def test_provisioning_preview_requires_auth(client):
    r = client.post(
        "/api/provisioning/users/preview",
        json={"name": "Teste", "extension": "2001", "target_ip": "172.16.36.50"},
    )
    assert r.status_code == 401


def test_provisioning_create_without_confirm(client):
    """create com confirm=false não deve alterar nada."""
    token = login(client, "admin", "admin")
    r = client.post(
        "/api/provisioning/users",
        json={
            "name": "Teste",
            "password": "Senha#1234",
            "full_name": "Teste Usuario",
            "extension": "2001",
            "target_ip": "172.16.36.50",
        },
        headers=auth_headers(token),
    )
    # Sem confirm=true → operação bloqueada
    assert r.status_code == 200
    body = r.json()
    assert body["created"] is False


def test_rbac_readonly_cannot_create_operator(client):
    """readonly não pode criar operadores."""
    # Criar um usuário readonly primeiro
    admin_token = login(client, "admin", "admin")
    client.post(
        "/api/admin/operators",
        json={
            "username": "ro_user",
            "password": "Senha#1234",
            "role": "readonly",
        },
        headers=auth_headers(admin_token),
    )
    ro_token = login(client, "ro_user", "Senha#1234")
    r = client.post(
        "/api/admin/operators",
        json={
            "username": "outro",
            "password": "Senha#1234",
            "role": "readonly",
        },
        headers=auth_headers(ro_token),
    )
    assert r.status_code == 403


def test_rbac_provisioning_can_preview(client):
    """provisioning pode usar preview."""
    admin_token = login(client, "admin", "admin")
    client.post(
        "/api/admin/operators",
        json={
            "username": "prov_user",
            "password": "Senha#1234",
            "role": "provisioning",
        },
        headers=auth_headers(admin_token),
    )
    prov_token = login(client, "prov_user", "Senha#1234")
    # Preview tenta consultar o Avaya (vai falhar sem servidor real),
    # mas a autenticação/RBAC deve passar até chegar lá.
    r = client.post(
        "/api/provisioning/users/preview",
        json={"name": "Teste", "extension": "2001", "target_ip": "172.16.36.50"},
        headers=auth_headers(prov_token),
    )
    # 502 = Avaya indisponível (esperado em teste), não 401/403
    assert r.status_code != 401
    assert r.status_code != 403


def test_user_detail_not_found(client):
    token = login(client)
    r = client.get("/api/users/detail/99999", headers=auth_headers(token))
    assert r.status_code == 404
    body = r.json()
    assert "detail" in body or "code" in body


def test_invalid_target_ip(client):
    token = login(client)
    r = client.get("/api/users", params={"target_ip": "não-é-ip"}, headers=auth_headers(token))
    assert r.status_code == 422


def test_last_login_persisted(client, db_session):
    """Verifica que last_login_at é gravado no banco após login."""
    token = login(client)
    db = db_session()
    user = db.query(OperatorUser).filter_by(username="admin").first()
    assert user.last_login_at is not None
    db.close()


def test_sync_requires_admin_or_provisioning(client):
    """Somente admin e provisioning podem sincronizar."""
    # Criar readonly
    admin_token = login(client, "admin", "admin")
    client.post(
        "/api/admin/operators",
        json={
            "username": "ro_user2",
            "password": "Senha#1234",
            "role": "readonly",
        },
        headers=auth_headers(admin_token),
    )
    ro_token = login(client, "ro_user2", "Senha#1234")
    r = client.post("/api/users/sync", headers=auth_headers(ro_token))
    assert r.status_code == 403


def test_update_user_scopes_ipo_and_sends_guid_in_xml(client, db_session, monkeypatch):
    db = db_session()
    db.add(AvayaUserCache(guid="user-guid", target_ip="10.0.1.213", name="Old", extension="2001", sip="old", record_type="USER", active=True))
    db.commit()
    db.close()
    sent = {}
    def fake_update(self, xml_payload, guid=None, target_ip=None):
        sent.update(xml=xml_payload, guid=guid, target_ip=target_ip)
        return {"status": "1"}
    monkeypatch.setattr(AvayaIPOfficeAPI, "update_user", fake_update)

    token = login(client)
    r = client.put("/api/users/user-guid", params={"target_ip": "10.0.1.213"},
                   json={"name": "New", "extension": "2002"}, headers=auth_headers(token))
    assert r.status_code == 200, r.text
    assert 'User GUID="user-guid"' in sent["xml"]
    assert "<Name>New</Name>" in sent["xml"]
    assert sent["target_ip"] == "10.0.1.213"

    wrong_ipo = client.put("/api/users/user-guid", params={"target_ip": "10.64.0.2"},
                           json={"name": "Wrong scope"}, headers=auth_headers(token))
    assert wrong_ipo.status_code == 404


def test_delete_user_requires_confirmation_and_marks_only_local_cache(client, db_session, monkeypatch):
    db = db_session()
    db.add(AvayaUserCache(guid="delete-guid", target_ip="10.0.1.213", name="Delete me", extension="2010", record_type="USER", active=True))
    db.commit()
    db.close()
    deleted = []
    monkeypatch.setattr(AvayaIPOfficeAPI, "delete_user", lambda self, guid, target_ip=None: deleted.append((guid, target_ip)) or True)
    token = login(client)
    missing_confirmation = client.delete("/api/users/delete-guid", params={"target_ip": "10.0.1.213"}, headers=auth_headers(token))
    assert missing_confirmation.status_code == 400
    assert deleted == []
    r = client.delete("/api/users/delete-guid", params={"target_ip": "10.0.1.213", "confirm": "true"}, headers=auth_headers(token))
    assert r.status_code == 200, r.text
    assert deleted == [("delete-guid", "10.0.1.213")]
    db = db_session()
    assert db.query(AvayaUserCache).filter_by(guid="delete-guid", target_ip="10.0.1.213").one().active is False
    db.close()


def test_users_page_sends_required_delete_confirmation(client):
    r = client.get("/dashboard/users")
    assert r.status_code == 200
    assert "url.searchParams.set('confirm','true')" in r.text


def test_edit_huntgroup_existing_reserved_extension_is_allowed(client, db_session, monkeypatch):
    db = db_session()
    db.add(AvayaHuntGroupCache(guid="group-guid", target_ip="10.0.1.213", name="Old", extension="9001", members_list="2001|", group_ring_mode=1, service_mode=1, voicemail_on=True, is_group_distributed=False, active=True))
    db.commit()
    db.close()
    monkeypatch.setattr(AvayaIPOfficeAPI, "mutate_management_object", lambda self, resource, method, payload, **kwargs: {"status": "1", "guid": "group-guid"})
    monkeypatch.setattr(AvayaIPOfficeAPI, "get_huntgroups", lambda self, target_ip=None: [{"GUID": "group-guid", "Name": "Renamed", "Extension": "9001", "MembersList": "2001|", "GroupRingMode": "1", "ServiceMode": "1", "VoicemailOn": "true", "IsGroupDistributed": "false"}])
    token = login(client)
    r = client.put("/api/huntgroups/group-guid", params={"target_ip": "10.0.1.213"}, json={
        "name": "Renamed", "extension": "9001", "members": ["2001"], "group_ring_mode": 1,
        "service_mode": 1, "voicemail_on": True, "is_group_distributed": False,
    }, headers=auth_headers(token))
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("route,model", [
    ("/api/huntgroups", AvayaHuntGroupCache),
    ("/api/extensions", AvayaExtensionCache),
])
def test_delete_telephony_resource_skips_edit_validation(client, db_session, monkeypatch, route, model):
    db = db_session()
    db.add(model(guid="delete-guid", target_ip="10.0.1.213", active=True))
    db.commit()
    db.close()
    calls = []
    monkeypatch.setattr(AvayaIPOfficeAPI, "mutate_management_object", lambda self, resource, method, payload, **kwargs: calls.append((resource, method, payload, kwargs)) or {"status": "1", "guid": "delete-guid"})

    token = login(client)
    r = client.delete(f"{route}/delete-guid", params={"target_ip": "10.0.1.213", "confirm": "true"}, headers=auth_headers(token))
    assert r.status_code == 200, r.text
    assert calls and calls[0][1:3] == ("DELETE", {})
    db = db_session()
    assert db.query(model).filter_by(guid="delete-guid", target_ip="10.0.1.213").one().active is False
    db.close()
