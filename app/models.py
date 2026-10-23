from datetime import datetime, timezone

from sqlalchemy import String, Integer, DateTime, Text, Boolean, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class OperatorUser(Base):
    __tablename__ = "operator_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(30), index=True, nullable=False, default="readonly")
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AvayaSystem(Base):
    __tablename__ = "avaya_systems"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_ip: Mapped[str] = mapped_column(String(45), unique=True, index=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)
    role: Mapped[str] = mapped_column(String(30), default="expansion", index=True, nullable=False)
    major_version: Mapped[str | None] = mapped_column(String(30), nullable=True)
    minor_version: Mapped[str | None] = mapped_column(String(30), nullable=True)
    configuration_version: Mapped[str | None] = mapped_column(String(30), nullable=True)
    build_version: Mapped[str | None] = mapped_column(String(30), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    discovered: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc
    )


class AvayaUserCache(Base):
    __tablename__ = "avaya_users_cache"
    __table_args__ = (
        UniqueConstraint("target_ip", "guid", name="uq_avaya_users_cache_target_guid"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guid: Mapped[str | None] = mapped_column(String(100), index=True, nullable=True)
    target_ip: Mapped[str | None] = mapped_column(String(45), index=True, nullable=True)

    name: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)
    extension: Mapped[str | None] = mapped_column(String(80), index=True, nullable=True)
    sip: Mapped[str | None] = mapped_column(String(255), nullable=True)
    category: Mapped[str | None] = mapped_column(String(80), nullable=True)
    assigned_package: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)

    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True, nullable=True)

    # USER = ramal/usuário real; SYSTEM = registro NoUser do IPO.
    record_type: Mapped[str] = mapped_column(
        String(20), default="USER", index=True, nullable=False
    )

    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AvayaExtensionCache(Base):
    __tablename__ = "avaya_extensions_cache"
    __table_args__ = (
        UniqueConstraint("target_ip", "guid", name="uq_avaya_extensions_target_guid"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guid: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    target_ip: Mapped[str] = mapped_column(String(45), nullable=False, index=True)
    extension: Mapped[str | None] = mapped_column(String(80), index=True, nullable=True)
    type_info: Mapped[int | None] = mapped_column(Integer, nullable=True)
    physical_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    module: Mapped[int | None] = mapped_column(Integer, nullable=True)
    port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    location: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True, nullable=True)


class AvayaHuntGroupCache(Base):
    __tablename__ = "avaya_huntgroups_cache"
    __table_args__ = (
        UniqueConstraint("target_ip", "guid", name="uq_avaya_huntgroups_target_guid"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guid: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    target_ip: Mapped[str] = mapped_column(String(45), nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(80), index=True, nullable=True)
    extension: Mapped[str | None] = mapped_column(String(80), index=True, nullable=True)
    members_list: Mapped[str | None] = mapped_column(Text, nullable=True)
    group_ring_mode: Mapped[int | None] = mapped_column(Integer, nullable=True)
    service_mode: Mapped[int | None] = mapped_column(Integer, nullable=True)
    voicemail_on: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_group_distributed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True, nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(80), index=True, nullable=True)
    action: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    resource: Mapped[str | None] = mapped_column(String(255), nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ProvisioningOperation(Base):
    __tablename__ = "provisioning_operations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(
        String(100), unique=True, index=True, nullable=False
    )
    target_ip: Mapped[str] = mapped_column(String(45), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    extension: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    guid: Mapped[str | None] = mapped_column(String(100), index=True, nullable=True)
    status: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(80), index=True, nullable=True)
    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    compensation_attempted: Mapped[bool] = mapped_column(Boolean, default=False)
    compensation_success: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc
    )
