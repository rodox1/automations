from datetime import datetime, timezone
import csv
import io
import ipaddress

from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import Integer, case, cast
from sqlalchemy.orm import Session
from xml.sax.saxutils import escape as xml_escape

from app.db import get_db
from app.auth import current_operator, require_roles
from app.models import AvayaSystem, AvayaUserCache, AuditLog
from app.avaya.client import AvayaIPOfficeAPI
from app.avaya.user_packages import USER_PACKAGE_NAMES, user_package_code


router = APIRouter()


class UserUpdateInput(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=31)
    extension: str | None = Field(default=None, min_length=2, max_length=15, pattern=r"^\d+$")
    sip_name: str | None = Field(default=None, min_length=1, max_length=64)
    assigned_package: int | None = Field(default=None, ge=1, le=8)

    @model_validator(mode="after")
    def require_a_change(self):
        if not self.model_fields_set or all(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError("Informe ao menos um campo para atualizar.")
        return self


def _normalize_target_ip(value: str | None) -> str | None:
    if value is None or not str(value).strip():
        return None

    value = str(value).strip()

    try:
        parsed = ipaddress.ip_address(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "INVALID_TARGET_IP",
                "message": "target_ip deve ser um endereço IPv4/IPv6 válido.",
                "target_ip": value,
            },
        ) from exc

    if parsed.version != 4:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "INVALID_TARGET_IP",
                "message": "target_ip deve ser IPv4.",
                "target_ip": value,
            },
        )

    return str(parsed)


def _user_value(user: dict, *keys: str) -> str:
    for key in keys:
        value = user.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _record_type(name: str) -> str:
    """Classifica entradas técnicas conhecidas sem remover registros do cache."""
    if name.strip().lower() == "nouser":
        return "SYSTEM"
    return "USER"


def _active_filter(value: str) -> bool | None:
    normalized = value.strip().lower()
    if normalized in {"", "all"}:
        return None
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise HTTPException(
        status_code=422,
        detail={"code": "INVALID_ACTIVE_FILTER", "message": "active deve ser true, false ou all."},
    )


def _filtered_users_query(
    db: Session,
    *,
    target_ip: str | None,
    active: bool | None,
    record_type: str | None,
    name: str | None,
    extension: str | None,
    sip: str | None,
    category: str | None,
    assigned_package: int | None,
):
    target_ip = _normalize_target_ip(target_ip)
    if record_type is not None:
        record_type = record_type.strip().upper()
        if record_type not in {"USER", "SYSTEM"}:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "INVALID_RECORD_TYPE",
                    "message": "record_type deve ser USER ou SYSTEM.",
                },
            )

    query = db.query(AvayaUserCache)
    if target_ip:
        query = query.filter(AvayaUserCache.target_ip == target_ip)
    if active is not None:
        query = query.filter(AvayaUserCache.active.is_(active))
    if record_type:
        query = query.filter(AvayaUserCache.record_type == record_type)
    if name:
        query = query.filter(AvayaUserCache.name.ilike(f"%{name}%"))
    if extension:
        query = query.filter(AvayaUserCache.extension.ilike(f"%{extension}%"))
    if sip:
        query = query.filter(AvayaUserCache.sip.ilike(f"%{sip}%"))
    if category:
        query = query.filter(AvayaUserCache.category.ilike(f"%{category}%"))
    if assigned_package is not None:
        query = query.filter(AvayaUserCache.assigned_package == assigned_package)
    return query, target_ip, record_type


def cache_users(
    db: Session,
    users: list[dict],
    target_ip: str | None = None,
) -> dict[str, int]:
    """Sincroniza um IPO sem apagar histórico; obsoletos ficam active=False."""
    explicit_ip = _normalize_target_ip(target_ip)

    # Prote??o: uma resposta vazia n?o pode desativar o cache do IPO.
    if explicit_ip and not users:
        raise ValueError(
            f"Sincroniza??o cancelada para {explicit_ip}: "
            "a consulta retornou zero usu?rios. "
            "Nenhum registro do cache foi alterado."
        )


    # Uma resposta vazia n?o pode desativar todos os usu?rios do cache.
    if not users:
        raise ValueError(
            "Sincroniza??o cancelada: a lista de usu?rios est? vazia. "
            "O cache existente foi preservado."
        )

    now = datetime.now(timezone.utc)
    seen: set[str] = set()
    inserted = updated = skipped = 0

    for user in users:
        guid = _user_value(user, "guid", "GUID")

        if not guid or guid.lower() in {item.lower() for item in seen}:
            skipped += 1
            continue

        seen.add(guid)

        row = (
            db.query(AvayaUserCache)
            .filter(
                AvayaUserCache.guid == guid,
                AvayaUserCache.target_ip == explicit_ip,
            )
            .first()
        )

        if not row:
            row = AvayaUserCache(guid=guid)
            db.add(row)
            inserted += 1
        else:
            updated += 1

        source_ip = explicit_ip or _user_value(
            user,
            "PBXAddress",
            "pbx_address",
            "IPAddress",
            "ipaddress",
        )

        if source_ip:
            try:
                source_ip = str(ipaddress.ip_address(source_ip))
            except ValueError:
                source_ip = explicit_ip

        name = _user_value(user, "name", "Name")

        row.target_ip = source_ip
        row.name = name
        row.extension = _user_value(user, "extension", "Extension")
        row.sip = _user_value(user, "sip", "SIPName", "SIPContact")
        row.category = _user_value(user, "category", "Category")
        row.assigned_package = user_package_code(
            user.get("AssignedPackage") or user.get("assigned_package")
        )
        row.record_type = _record_type(name)
        row.active = True
        row.last_seen_at = now
        row.source_updated_at = now

    deactivated = 0

    if explicit_ip:
        seen_lower = {guid.lower() for guid in seen}

        existing = (
            db.query(AvayaUserCache)
            .filter(
                AvayaUserCache.target_ip == explicit_ip,
                AvayaUserCache.guid.isnot(None),
                AvayaUserCache.active.is_(True),
            )
            .all()
        )

        for row in existing:
            if str(row.guid).strip().lower() not in seen_lower:
                row.active = False
                deactivated += 1

    db.commit()

    return {
        "inserted": inserted,
        "updated": updated,
        "deactivated": deactivated,
        "skipped": skipped,
    }


@router.post("/sync")
def sync(
    target_ip: str = Query(
        ...,
        description=(
            "IP LAN 1 do IPO. A aplicação conecta diretamente a este endereço."
        ),
    ),
    db: Session = Depends(get_db),
    operator=Depends(require_roles("admin", "provisioning")),
):
    target_ip = _normalize_target_ip(target_ip)

    try:
        client = AvayaIPOfficeAPI(host=target_ip)
        users = client.get_users(target_ip=target_ip)
        result = cache_users(db, users, target_ip=target_ip)

        system = (
            db.query(AvayaSystem)
            .filter(AvayaSystem.target_ip == target_ip)
            .one_or_none()
        )
        now = datetime.now(timezone.utc)
        if system is None:
            system = AvayaSystem(
                target_ip=target_ip,
                name=target_ip,
                role="ipo",
            )
            db.add(system)
        system.active = True
        system.discovered = True
        system.last_seen_at = now
        system.updated_at = now

        db.add(
            AuditLog(
                username=operator.username,
                action="SYNC_USERS",
                resource="/api/users/sync",
                success=True,
                detail=(
                    f"count={len(users)};"
                    f"inserted={result['inserted']};"
                    f"updated={result['updated']};"
                    f"deactivated={result['deactivated']};"
                    f"skipped={result['skipped']};"
                    f"target_ip={target_ip or 'legacy'}"
                ),
            )
        )
        db.commit()

        return {
            "status": "ok",
            "count": len(users),
            **result,
            "target_ip": target_ip,
        }

    except HTTPException:
        raise

    except Exception as exc:
        db.rollback()

        try:
            db.add(
                AuditLog(
                    username=operator.username,
                    action="SYNC_USERS",
                    resource="/api/users/sync",
                    success=False,
                    detail=(
                        f"{type(exc).__name__}: {exc};"
                        f"target_ip={target_ip or 'legacy'}"
                    ),
                )
            )
            db.commit()
        except Exception:
            db.rollback()

        raise HTTPException(
            status_code=502,
            detail={
                "code": "AVAYA_SYNC_ERROR",
                "message": "Não foi possível sincronizar usuários do Avaya.",
                "error": str(exc),
                "target_ip": target_ip,
            },
        ) from exc


@router.get("")
def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    target_ip: str | None = Query(default=None),
    active: str = Query(
        default="true",
        description="true=ativos, false=inativos, all=todos.",
    ),
    record_type: str | None = Query(
        default=None,
        description="USER, SYSTEM ou null para todos.",
    ),
    name: str | None = None,
    extension: str | None = None,
    sip: str | None = None,
    category: str | None = None,
    assigned_package: int | None = Query(default=None, ge=1, le=8),
    db: Session = Depends(get_db),
    operator=Depends(current_operator),
):
    active_value = _active_filter(active)
    q, target_ip, record_type = _filtered_users_query(
        db,
        target_ip=target_ip,
        active=active_value,
        record_type=record_type,
        name=name,
        extension=extension,
        sip=sip,
        category=category,
        assigned_package=assigned_package,
    )

    total = q.count()

    rows = (
        q.order_by(
            case((AvayaUserCache.extension.is_(None), 1), else_=0),
            cast(AvayaUserCache.extension, Integer),
            AvayaUserCache.extension,
            AvayaUserCache.id,
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    db.add(
        AuditLog(
            username=operator.username,
            action="LIST_USERS",
            resource="/api/users",
            success=True,
            detail=(
                f"count={len(rows)};"
                f"target_ip={target_ip or 'all'};"
            f"active={active};"
                f"record_type={record_type or 'all'}"
            ),
        )
    )
    db.commit()

    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "target_ip": target_ip,
        "active": active_value,
        "record_type": record_type,
        "assigned_package": assigned_package,
        "items": [
            {
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
            for row in rows
        ],
    }


@router.get("/export")
def export_users(
    target_ip: str | None = Query(default=None),
    active: str = Query(default="true"),
    record_type: str | None = Query(default="USER"),
    name: str | None = None,
    extension: str | None = None,
    sip: str | None = None,
    category: str | None = None,
    assigned_package: int | None = Query(default=None, ge=1, le=8),
    db: Session = Depends(get_db),
    operator=Depends(current_operator),
):
    q, target_ip, record_type = _filtered_users_query(
        db,
        target_ip=target_ip,
        active=_active_filter(active),
        record_type=record_type,
        name=name,
        extension=extension,
        sip=sip,
        category=category,
        assigned_package=assigned_package,
    )
    rows = q.order_by(
        case((AvayaUserCache.extension.is_(None), 1), else_=0),
        cast(AvayaUserCache.extension, Integer),
        AvayaUserCache.extension,
        AvayaUserCache.id,
    ).all()

    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(
        ["IPO IP", "Nome", "Ramal", "SIP", "Categoria", "Tipo de licença", "Status", "GUID"]
    )
    for row in rows:
        writer.writerow(
            [
                row.target_ip or "",
                row.name or "",
                row.extension or "",
                row.sip or "",
                row.category or "",
                USER_PACKAGE_NAMES.get(row.assigned_package, "Não informado"),
                "Ativo" if row.active else "Inativo",
                row.guid or "",
            ]
        )

    db.add(
        AuditLog(
            username=operator.username,
            action="EXPORT_USERS",
            resource="/api/users/export",
            success=True,
            detail=f"count={len(rows)};target_ip={target_ip or 'all'};assigned_package={assigned_package or 'all'}",
        )
    )
    db.commit()

    filename = f"usuarios_{target_ip or 'todos'}.csv"
    return Response(
        content="\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.put("/{guid}")
def update_user(
    guid: str,
    data: UserUpdateInput,
    target_ip: str = Query(...),
    db: Session = Depends(get_db),
    operator=Depends(require_roles("admin", "provisioning")),
):
    """Update selected, non-secret user fields on one explicitly selected IPO."""
    ip = _normalize_target_ip(target_ip)
    row = db.query(AvayaUserCache).filter_by(
        target_ip=ip, guid=guid, active=True, record_type="USER"
    ).one_or_none()
    if row is None:
        raise HTTPException(404, detail="Usuário ativo não encontrado no cache deste IPO; sincronize antes de alterar.")

    values = data.model_dump(exclude_unset=True)
    if "extension" in values and values["extension"] != row.extension:
        conflict = db.query(AvayaUserCache).filter(
            AvayaUserCache.target_ip == ip,
            AvayaUserCache.active.is_(True),
            AvayaUserCache.record_type == "USER",
            AvayaUserCache.extension == values["extension"],
            AvayaUserCache.guid != guid,
        ).first()
        if conflict:
            raise HTTPException(409, detail="Este ramal já está atribuído a outro usuário no cache do IPO.")

    api_fields = {
        "name": "Name",
        "extension": "Extension",
        "sip_name": "SIPName",
        "assigned_package": "AssignedPackage",
    }
    fields = "".join(
        f"<{api_fields[key]}>{xml_escape(str(value))}</{api_fields[key]}>"
        for key, value in values.items()
    )
    xml_payload = f'<data><ws_object><User GUID="{xml_escape(guid)}">{fields}</User></ws_object></data>'

    try:
        result = AvayaIPOfficeAPI(host=ip).update_user(xml_payload, guid=guid, target_ip=ip)
    except Exception as exc:
        db.rollback()
        db.add(AuditLog(username=operator.username, action="UPDATE_USER", resource="/api/users", success=False,
                        detail=f"target_ip={ip};guid={guid};{type(exc).__name__}: {exc}"))
        db.commit()
        raise HTTPException(502, detail={"code": "AVAYA_USER_UPDATE_ERROR", "message": "Falha ao atualizar usuário no Avaya.", "error": str(exc), "target_ip": ip}) from exc

    for key, value in values.items():
        if key == "name":
            row.name = value.strip()
        elif key == "extension":
            row.extension = value
        elif key == "sip_name":
            row.sip = value.strip()
        elif key == "assigned_package":
            row.assigned_package = value
    row.source_updated_at = datetime.now(timezone.utc)
    db.add(AuditLog(username=operator.username, action="UPDATE_USER", resource="/api/users", success=True,
                    detail=f"target_ip={ip};guid={guid};fields={','.join(values)}"))
    db.commit()
    return {"status": "ok", "target_ip": ip, "guid": guid, "avaya_status": result.get("status"), "user": {
        "name": row.name, "extension": row.extension, "sip": row.sip,
        "assigned_package": row.assigned_package,
        "assigned_package_name": USER_PACKAGE_NAMES.get(row.assigned_package),
    }}


@router.delete("/{guid}")
def delete_user(
    guid: str,
    target_ip: str = Query(...),
    confirm: bool = Query(False),
    db: Session = Depends(get_db),
    operator=Depends(require_roles("admin")),
):
    """Delete an IPO user only after explicit confirmation; admins only."""
    if not confirm:
        raise HTTPException(400, detail="Confirme a exclusão com confirm=true.")
    ip = _normalize_target_ip(target_ip)
    row = db.query(AvayaUserCache).filter_by(
        target_ip=ip, guid=guid, active=True, record_type="USER"
    ).one_or_none()
    if row is None:
        raise HTTPException(404, detail="Usuário ativo não encontrado no cache deste IPO; sincronize antes de excluir.")

    try:
        AvayaIPOfficeAPI(host=ip).delete_user(guid, target_ip=ip)
    except Exception as exc:
        db.rollback()
        db.add(AuditLog(username=operator.username, action="DELETE_USER", resource="/api/users", success=False,
                        detail=f"target_ip={ip};guid={guid};{type(exc).__name__}: {exc}"))
        db.commit()
        raise HTTPException(502, detail={"code": "AVAYA_USER_DELETE_ERROR", "message": "Falha ao excluir usuário no Avaya.", "error": str(exc), "target_ip": ip}) from exc

    row.active = False
    db.add(AuditLog(username=operator.username, action="DELETE_USER", resource="/api/users", success=True,
                    detail=f"target_ip={ip};guid={guid}"))
    db.commit()
    return {"status": "ok", "target_ip": ip, "guid": guid, "active": False}
