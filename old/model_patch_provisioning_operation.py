# Adicionar ao final de app/models.py

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
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
