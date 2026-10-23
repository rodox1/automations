"""Cache, query and manage IPO extensions and hunt groups."""
from datetime import datetime, timezone
import csv
import io
import ipaddress

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import Integer, case, cast
from sqlalchemy.orm import Session

from app.auth import current_operator, require_roles
from app.avaya.client import AvayaIPOfficeAPI
from app.db import get_db
from app.models import AuditLog, AvayaExtensionCache, AvayaHuntGroupCache, AvayaSystem
from app.api.users import cache_users

extensions_router = APIRouter()
huntgroups_router = APIRouter()
ipo_router = APIRouter()

EXTENSION_TYPES = {0: "Normal", 1: "H.323", 2: "ADMM", 3: "SIP", 4: "D100"}
RING_MODES = {0: "Collective", 1: "Sequential", 2: "Rotary", 3: "Longest Waiting", 4: "Collective Call Waiting"}
SERVICE_MODES = {0: "Out of Service", 1: "In Service", 2: "Night Service"}


def _ip(value: str) -> str:
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "INVALID_TARGET_IP", "message": "target_ip deve ser um IPv4 válido."}) from exc
    if address.version != 4:
        raise HTTPException(422, detail={"code": "INVALID_TARGET_IP", "message": "target_ip deve ser um IPv4 válido."})
    return str(address)


def _value(row: dict, *names: str, default=""):
    for name in names:
        value = row.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


def _int_or_none(value):
    try:
        return int(value) if value is not None and str(value).strip() else None
    except (ValueError, TypeError):
        return None


def _bool_or_none(value):
    if value is None or str(value).strip() == "":
        return None
    return str(value).strip().lower() in {"1", "true", "yes"}


def _cache_extensions(db: Session, rows: list[dict], target_ip: str) -> dict:
    if not rows:
        raise ValueError("Sincronização cancelada: o IPO retornou zero ramais; o cache foi preservado.")
    now, seen = datetime.now(timezone.utc), set()
    inserted = updated = skipped = 0
    for item in rows:
        guid = _value(item, "GUID", "guid")
        if not guid or guid.lower() in seen:
            skipped += 1
            continue
        seen.add(guid.lower())
        row = db.query(AvayaExtensionCache).filter_by(target_ip=target_ip, guid=guid).one_or_none()
        if row is None:
            row = AvayaExtensionCache(target_ip=target_ip, guid=guid)
            db.add(row)
            inserted += 1
        else:
            updated += 1
        row.extension = _value(item, "Extension", "extension") or None
        row.type_info = _int_or_none(_value(item, "TypeInfo", "type_info"))
        row.physical_id = _int_or_none(_value(item, "Id", "id"))
        row.module = _int_or_none(_value(item, "Module", "module"))
        row.port = _int_or_none(_value(item, "Port", "port"))
        row.location = _int_or_none(_value(item, "Location", "location"))
        row.active, row.last_seen_at = True, now
    deactivated = 0
    for row in db.query(AvayaExtensionCache).filter_by(target_ip=target_ip, active=True).all():
        if row.guid.lower() not in seen:
            row.active = False
            deactivated += 1
    db.commit()
    return {"inserted": inserted, "updated": updated, "deactivated": deactivated, "skipped": skipped}


def _cache_huntgroups(db: Session, rows: list[dict], target_ip: str) -> dict:
    if not rows:
        raise ValueError("Sincronização cancelada: o IPO retornou zero grupos; o cache foi preservado.")
    now, seen = datetime.now(timezone.utc), set()
    inserted = updated = skipped = 0
    for item in rows:
        guid = _value(item, "GUID", "guid")
        if not guid or guid.lower() in seen:
            skipped += 1
            continue
        seen.add(guid.lower())
        row = db.query(AvayaHuntGroupCache).filter_by(target_ip=target_ip, guid=guid).one_or_none()
        if row is None:
            row = AvayaHuntGroupCache(target_ip=target_ip, guid=guid)
            db.add(row)
            inserted += 1
        else:
            updated += 1
        row.name = _value(item, "Name", "name") or None
        row.extension = _value(item, "Extension", "extension") or None
        row.members_list = _value(item, "MembersList", "members_list") or None
        row.group_ring_mode = _int_or_none(_value(item, "GroupRingMode", "group_ring_mode"))
        row.service_mode = _int_or_none(_value(item, "ServiceMode", "service_mode"))
        row.voicemail_on = _bool_or_none(_value(item, "VoicemailOn", "voicemail_on"))
        row.is_group_distributed = _bool_or_none(_value(item, "IsGroupDistributed", "is_group_distributed"))
        row.active, row.last_seen_at = True, now
    deactivated = 0
    for row in db.query(AvayaHuntGroupCache).filter_by(target_ip=target_ip, active=True).all():
        if row.guid.lower() not in seen:
            row.active = False
            deactivated += 1
    db.commit()
    return {"inserted": inserted, "updated": updated, "deactivated": deactivated, "skipped": skipped}


@ipo_router.post("/sync")
def sync_ipo_resources(
    target_ip: str = Query(...),
    db: Session = Depends(get_db),
    operator=Depends(require_roles("admin", "provisioning")),
):
    """Fetch all read-only IPO inventories through one authenticated session."""
    ip = _ip(target_ip)
    try:
        client = AvayaIPOfficeAPI(host=ip)
        systems = client.get_systems(target_ip=ip)
        users = client.get_users(target_ip=ip)
        extensions = client.get_extensions(target_ip=ip)
        huntgroups = client.get_huntgroups(target_ip=ip)
        if not systems:
            raise ValueError("O IPO não retornou informações do sistema.")

        now = datetime.now(timezone.utc)
        for item in systems:
            system_ip = _ip(str(item.get("LAN1IPAddress") or ip))
            system = db.query(AvayaSystem).filter_by(target_ip=system_ip).one_or_none()
            if system is None:
                system = AvayaSystem(target_ip=system_ip)
                db.add(system)
            system.name = item.get("Name")
            system.role = "primary" if system_ip == client.host else "expansion"
            system.major_version = item.get("MajorVersion")
            system.minor_version = item.get("MinorVersion")
            system.configuration_version = item.get("Configuration")
            system.build_version = item.get("BuildVersion")
            system.active = system.discovered = True
            system.last_seen_at = system.updated_at = now

        user_result = cache_users(db, users, target_ip=ip)
        extension_result = _cache_extensions(db, extensions, ip)
        group_result = _cache_huntgroups(db, huntgroups, ip)
        result = {
            "status": "ok",
            "target_ip": ip,
            "systems": {"count": len(systems)},
            "users": {"count": len(users), **user_result},
            "extensions": {"count": len(extensions), **extension_result},
            "huntgroups": {"count": len(huntgroups), **group_result},
        }
        _audit(db, operator, "SYNC_IPO_INVENTORY", "/api/ipo/sync", str(result))
        return result
    except Exception as exc:
        db.rollback()
        _audit(db, operator, "SYNC_IPO_INVENTORY", "/api/ipo/sync", f"target_ip={ip};{type(exc).__name__}: {exc}", False)
        raise HTTPException(502, detail={"code": "IPO_SYNC_ERROR", "message": "Não foi possível sincronizar todo o inventário do IPO.", "error": str(exc), "target_ip": ip}) from exc


class ExtensionInput(BaseModel):
    extension: str = Field(min_length=1, max_length=15, pattern=r"^\d+$")
    type_info: int = Field(default=0, ge=0, le=4)
    caller_display_type: int | None = Field(default=None, ge=0, le=23)
    module: int | None = Field(default=None, ge=0)
    port: int | None = Field(default=None, ge=0)
    location: int | None = Field(default=None, ge=0)


class HuntGroupInput(BaseModel):
    name: str = Field(min_length=1, max_length=15)
    extension: str = Field(min_length=2, max_length=15, pattern=r"^\d+$")
    group_ring_mode: int = Field(default=0, ge=0, le=4)
    members: list[str] = Field(default_factory=list)
    service_mode: int = Field(default=1, ge=0, le=2)
    voicemail_on: bool = False
    is_group_distributed: bool = False


def _audit(db, operator, action, resource, detail, success=True):
    db.add(AuditLog(username=operator.username, action=action, resource=resource, detail=detail, success=success))
    db.commit()


@extensions_router.post("/sync")
def sync_extensions(target_ip: str = Query(...), db: Session = Depends(get_db), operator=Depends(require_roles("admin", "provisioning"))):
    return _do_sync("extension", target_ip, db, operator)


@huntgroups_router.post("/sync")
def sync_huntgroups(target_ip: str = Query(...), db: Session = Depends(get_db), operator=Depends(require_roles("admin", "provisioning"))):
    return _do_sync("huntgroup", target_ip, db, operator)


def _do_sync(resource, target_ip, db, operator):
    ip = _ip(target_ip)
    try:
        client = AvayaIPOfficeAPI(host=ip)
        rows = client.get_extensions(ip) if resource == "extension" else client.get_huntgroups(ip)
        result = _cache_extensions(db, rows, ip) if resource == "extension" else _cache_huntgroups(db, rows, ip)
        _audit(db, operator, f"SYNC_{resource.upper()}S", f"/api/{resource}s/sync", f"count={len(rows)};target_ip={ip}")
        return {"status": "ok", "target_ip": ip, "count": len(rows), **result}
    except Exception as exc:
        db.rollback()
        _audit(db, operator, f"SYNC_{resource.upper()}S", f"/api/{resource}s/sync", f"{type(exc).__name__}: {exc};target_ip={ip}", False)
        raise HTTPException(502, detail={"code": "AVAYA_SYNC_ERROR", "message": f"Falha ao sincronizar {resource}.", "error": str(exc), "target_ip": ip}) from exc


def _list_resource(resource, target_ip, active, search, page, page_size, db, operator):
    model = AvayaExtensionCache if resource == "extension" else AvayaHuntGroupCache
    query = db.query(model)
    ip = _ip(target_ip) if target_ip else None
    if ip: query = query.filter(model.target_ip == ip)
    if active != "all": query = query.filter(model.active.is_(active == "true"))
    if search:
        fields = [model.extension] if resource == "extension" else [model.name, model.extension, model.members_list]
        from sqlalchemy import or_
        query = query.filter(or_(*(field.ilike(f"%{search}%") for field in fields)))
    total = query.count()
    rows = query.order_by(
        case((model.extension.is_(None), 1), else_=0),
        cast(model.extension, Integer),
        model.extension,
        model.id,
    ).offset((page-1)*page_size).limit(page_size).all()
    _audit(db, operator, f"LIST_{resource.upper()}S", f"/api/{resource}s", f"count={len(rows)};target_ip={ip or 'all'}")
    items = []
    for r in rows:
        item = {"id": r.id, "guid": r.guid, "target_ip": r.target_ip, "extension": r.extension, "active": r.active, "last_seen_at": r.last_seen_at}
        if resource == "extension":
            item.update(type_info=r.type_info, type_name=EXTENSION_TYPES.get(r.type_info, "Unknown"), physical_id=r.physical_id, module=r.module, port=r.port, location=r.location)
        else:
            item.update(name=r.name, members=[m for m in r.members_list.split("|") if m] if r.members_list else [], members_list=r.members_list, group_ring_mode=r.group_ring_mode, group_ring_mode_name=RING_MODES.get(r.group_ring_mode), service_mode=r.service_mode, service_mode_name=SERVICE_MODES.get(r.service_mode), voicemail_on=r.voicemail_on, is_group_distributed=r.is_group_distributed)
        items.append(item)
    return {"page": page, "page_size": page_size, "total": total, "target_ip": ip, "active": active, "items": items}


@extensions_router.get("")
def list_extensions(target_ip: str | None = None, active: str = Query("true", pattern="^(true|false|all)$"), search: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200), db: Session = Depends(get_db), operator=Depends(current_operator)):
    return _list_resource("extension", target_ip, active, search, page, page_size, db, operator)


@huntgroups_router.get("")
def list_huntgroups(target_ip: str | None = None, active: str = Query("true", pattern="^(true|false|all)$"), search: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200), db: Session = Depends(get_db), operator=Depends(current_operator)):
    return _list_resource("huntgroup", target_ip, active, search, page, page_size, db, operator)


@extensions_router.get("/detail/{extension_id}")
def extension_detail(extension_id: int, db: Session = Depends(get_db), operator=Depends(current_operator)):
    row = db.query(AvayaExtensionCache).filter_by(id=extension_id).one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "EXTENSION_NOT_FOUND", "message": "Ramal não encontrado no cache."})
    _audit(db, operator, "VIEW_EXTENSION", f"/api/extensions/detail/{extension_id}", f"target_ip={row.target_ip};guid={row.guid}")
    return {"id": row.id, "guid": row.guid, "target_ip": row.target_ip, "extension": row.extension, "type_info": row.type_info, "type_name": EXTENSION_TYPES.get(row.type_info, "Unknown"), "physical_id": row.physical_id, "module": row.module, "port": row.port, "location": row.location, "active": row.active, "last_seen_at": row.last_seen_at}


@huntgroups_router.get("/detail/{huntgroup_id}")
def huntgroup_detail(huntgroup_id: int, db: Session = Depends(get_db), operator=Depends(current_operator)):
    row = db.query(AvayaHuntGroupCache).filter_by(id=huntgroup_id).one_or_none()
    if row is None:
        raise HTTPException(404, detail={"code": "HUNTGROUP_NOT_FOUND", "message": "Grupo não encontrado no cache."})
    _audit(db, operator, "VIEW_HUNTGROUP", f"/api/huntgroups/detail/{huntgroup_id}", f"target_ip={row.target_ip};guid={row.guid}")
    return {"id": row.id, "guid": row.guid, "target_ip": row.target_ip, "name": row.name, "extension": row.extension, "members": [m for m in row.members_list.split("|") if m] if row.members_list else [], "members_list": row.members_list, "group_ring_mode": row.group_ring_mode, "group_ring_mode_name": RING_MODES.get(row.group_ring_mode), "service_mode": row.service_mode, "service_mode_name": SERVICE_MODES.get(row.service_mode), "voicemail_on": row.voicemail_on, "is_group_distributed": row.is_group_distributed, "active": row.active, "last_seen_at": row.last_seen_at}


def _export(resource, target_ip, active, search, db, operator):
    payload = _list_resource(resource, target_ip, active, search, 1, 200, db, operator)
    if payload["total"] > 200:
        # Export all rows directly rather than silently truncating at page size.
        payload = _list_resource(resource, target_ip, active, search, 1, payload["total"], db, operator)
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    columns = list(payload["items"][0].keys()) if payload["items"] else (["target_ip", "extension", "type_name", "guid"] if resource == "extension" else ["target_ip", "name", "extension", "members_list", "group_ring_mode_name", "service_mode_name", "guid"])
    writer.writerow(columns)
    for item in payload["items"]: writer.writerow(["|".join(item[k]) if isinstance(item[k], list) else item[k] for k in columns])
    _audit(db, operator, f"EXPORT_{resource.upper()}S", f"/api/{resource}s/export", f"count={payload['total']}")
    return Response("\ufeff" + out.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{resource}s_{payload["target_ip"] or "todos"}.csv"'})


@extensions_router.get("/export")
def export_extensions(target_ip: str | None = None, active: str = Query("true", pattern="^(true|false|all)$"), search: str | None = None, db: Session = Depends(get_db), operator=Depends(current_operator)):
    return _export("extension", target_ip, active, search, db, operator)


@huntgroups_router.get("/export")
def export_huntgroups(target_ip: str | None = None, active: str = Query("true", pattern="^(true|false|all)$"), search: str | None = None, db: Session = Depends(get_db), operator=Depends(current_operator)):
    return _export("huntgroup", target_ip, active, search, db, operator)


def _manage(resource, method, data, guid, target_ip, db, operator):
    ip = _ip(target_ip)
    model = AvayaExtensionCache if resource == "extension" else AvayaHuntGroupCache
    cached_row = None
    if method in {"PUT", "DELETE"}:
        cached_row = db.query(model).filter_by(target_ip=ip, guid=guid, active=True).first()
    if method in {"PUT", "DELETE"} and cached_row is None:
        raise HTTPException(404, detail="Registro ativo não encontrado no cache deste IPO; sincronize antes da alteração.")
    values = data.model_dump(exclude_none=True) if data else {}
    if method == "DELETE":
        # DELETE is identified by the scoped GUID; it has no editable payload.
        # Skip creation/update field validation for both resource types.
        avaya = {}
        identity = None
    elif resource == "extension":
        avaya = {"Extension": values.get("extension"), "TypeInfo": values.get("type_info", 0), "CallerDisplayType": values.get("caller_display_type"), "Module": values.get("module"), "Port": values.get("port"), "Location": values.get("location")}
        identity = values.get("extension")
        conflict = db.query(AvayaExtensionCache).filter_by(target_ip=ip, extension=identity, active=True).filter(AvayaExtensionCache.guid != (guid or "")).first()
        if method == "POST" and conflict:
            raise HTTPException(409, detail="Este número de ramal já existe no cache do IPO. Sincronize para conferir o estado atual.")
    else:
        if len(values.get("members", [])) != len(set(values.get("members", []))):
            raise HTTPException(422, detail="A lista de membros contém ramais repetidos.")
        if any(not member.isdigit() or not 2 <= len(member) <= 15 for member in values.get("members", [])):
            raise HTTPException(422, detail="Cada membro deve ser um ramal numérico com 2 a 15 dígitos.")
        if any(char in values["name"] for char in "#?/^><,.") or values["name"].startswith(" "):
            raise HTTPException(422, detail="O nome do grupo não pode começar com espaço nem conter # ? / ^ > < , .")
        # Existing IPOs may already contain hunt group numbers in this range.
        # Permit an unchanged number on PUT while retaining the creation rule.
        if (method == "POST" or (method == "PUT" and values["extension"] != cached_row.extension)) and 8897 <= int(values["extension"]) <= 9999:
            raise HTTPException(422, detail="A faixa de ramais 8897 a 9999 é reservada pelo IP Office.")
        conflict = db.query(AvayaHuntGroupCache).filter(AvayaHuntGroupCache.target_ip == ip, AvayaHuntGroupCache.active.is_(True), (AvayaHuntGroupCache.extension == values["extension"]) | (AvayaHuntGroupCache.name == values["name"])).filter(AvayaHuntGroupCache.guid != (guid or "")).first()
        if method == "POST" and conflict:
            raise HTTPException(409, detail="Já existe um grupo com esse nome ou ramal no cache do IPO.")
        avaya = {"Name": values["name"], "Extension": values["extension"], "GroupRingMode": values["group_ring_mode"], "MembersList": "|".join(values.get("members", [])), "ServiceMode": values["service_mode"], "VoicemailOn": str(values["voicemail_on"]).lower(), "IsGroupDistributed": str(values["is_group_distributed"]).lower()}
        identity = values["extension"]
    client = AvayaIPOfficeAPI(host=ip)
    try:
        result = client.mutate_management_object(resource, method, avaya, guid=guid, target_ip=ip)
    except Exception as exc:
        db.rollback()
        _audit(db, operator, f"{method}_{resource.upper()}", f"/api/{resource}s", f"failed;target_ip={ip};guid={guid};{type(exc).__name__}: {exc}", False)
        raise HTTPException(502, detail={"code": "AVAYA_MANAGEMENT_ERROR", "message": f"Falha na operação Avaya de {resource}.", "error": str(exc), "target_ip": ip}) from exc
    cache_refreshed = True
    if method == "DELETE":
        row = db.query(model).filter_by(target_ip=ip, guid=guid).one()
        row.active = False
        db.commit()
    else:
        try:
            rows = client.get_extensions(ip) if resource == "extension" else client.get_huntgroups(ip)
            (_cache_extensions if resource == "extension" else _cache_huntgroups)(db, rows, ip)
        except Exception:
            # The write already succeeded on the IPO. Report that explicitly and
            # let the operator retry only the cache sync instead of repeating a write.
            db.rollback()
            cache_refreshed = False
    _audit(db, operator, f"{method}_{resource.upper()}", f"/api/{resource}s", f"target_ip={ip};guid={guid or result.get('guid')};extension={identity};cache_refreshed={cache_refreshed}")
    return {"status": "ok", "operation": method.lower(), "target_ip": ip, "guid": guid or result.get("guid"), "extension": identity, "avaya_status": result["status"], "cache_refreshed": cache_refreshed}


@extensions_router.post("")
def create_extension(data: ExtensionInput, target_ip: str = Query(...), db: Session = Depends(get_db), operator=Depends(require_roles("admin", "provisioning"))):
    return _manage("extension", "POST", data, None, target_ip, db, operator)


@extensions_router.put("/{guid}")
def update_extension(guid: str, data: ExtensionInput, target_ip: str = Query(...), db: Session = Depends(get_db), operator=Depends(require_roles("admin", "provisioning"))):
    return _manage("extension", "PUT", data, guid, target_ip, db, operator)


@extensions_router.delete("/{guid}")
def delete_extension(guid: str, target_ip: str = Query(...), confirm: bool = Query(False), db: Session = Depends(get_db), operator=Depends(require_roles("admin"))):
    if not confirm: raise HTTPException(400, detail="Confirme a exclusão com confirm=true.")
    return _manage("extension", "DELETE", None, guid, target_ip, db, operator)


@huntgroups_router.post("")
def create_huntgroup(data: HuntGroupInput, target_ip: str = Query(...), db: Session = Depends(get_db), operator=Depends(require_roles("admin", "provisioning"))):
    return _manage("huntgroup", "POST", data, None, target_ip, db, operator)


@huntgroups_router.put("/{guid}")
def update_huntgroup(guid: str, data: HuntGroupInput, target_ip: str = Query(...), db: Session = Depends(get_db), operator=Depends(require_roles("admin", "provisioning"))):
    return _manage("huntgroup", "PUT", data, guid, target_ip, db, operator)


@huntgroups_router.delete("/{guid}")
def delete_huntgroup(guid: str, target_ip: str = Query(...), confirm: bool = Query(False), db: Session = Depends(get_db), operator=Depends(require_roles("admin"))):
    if not confirm: raise HTTPException(400, detail="Confirme a exclusão com confirm=true.")
    return _manage("huntgroup", "DELETE", None, guid, target_ip, db, operator)
