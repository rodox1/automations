from sqlalchemy import Column, Index, MetaData, Table, UniqueConstraint, create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from app.config import settings
connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
class Base(DeclarativeBase): pass
def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()
def init_db():
    from app import models
    Base.metadata.create_all(bind=engine)
    _ensure_assigned_package_column()
    _ensure_multi_ipo_cache_key()
    from app.bootstrap import ensure_bootstrap_admin
    ensure_bootstrap_admin()


def _ensure_assigned_package_column():
    """Add license profile storage to existing user-cache tables."""
    columns = {column["name"] for column in inspect(engine).get_columns("avaya_users_cache")}
    if "assigned_package" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE avaya_users_cache ADD COLUMN assigned_package INTEGER")
            )
        table = Table("avaya_users_cache", MetaData(), autoload_with=engine)
        Index("ix_avaya_users_cache_assigned_package", table.c.assigned_package).create(
            bind=engine, checkfirst=True
        )


def _ensure_multi_ipo_cache_key():
    """Allow the same Avaya GUID to exist on different IPOs.

    Older databases have a globally unique index on ``guid``. SQLite cannot
    drop that auto-index, so rebuild the cache table while preserving rows.
    """
    from app.models import AvayaUserCache

    inspector = inspect(engine)
    indexes = inspector.get_indexes("avaya_users_cache")
    unique_constraints = inspector.get_unique_constraints("avaya_users_cache")
    guid_is_global_unique = any(
        item.get("unique") and item.get("column_names") == ["guid"]
        for item in indexes
    ) or any(
        item.get("column_names") == ["guid"]
        for item in unique_constraints
    )
    composite_exists = any(
        item.get("unique")
        and item.get("column_names") == ["target_ip", "guid"]
        for item in indexes
    ) or any(
        item.get("column_names") == ["target_ip", "guid"]
        for item in unique_constraints
    )

    if not guid_is_global_unique and composite_exists:
        return

    if guid_is_global_unique:
        if engine.dialect.name != "sqlite":
            raise RuntimeError(
                "A migração da chave multi-IPO da tabela avaya_users_cache "
                "precisa ser aplicada para este banco de dados."
            )
        with engine.begin() as connection:
            source = Table("avaya_users_cache", MetaData(), autoload_with=connection)
            source_columns = {column.name for column in source.columns}
            temp_name = "avaya_users_cache_multiipo_new"
            temp_metadata = MetaData()
            destination = Table(
                temp_name,
                temp_metadata,
                *[
                    Column(
                        column.name,
                        column.type,
                        primary_key=column.primary_key,
                        nullable=column.nullable,
                        autoincrement=column.autoincrement,
                    )
                    for column in AvayaUserCache.__table__.columns
                ],
                UniqueConstraint(
                    "target_ip", "guid", name="uq_avaya_users_cache_target_guid"
                ),
            )
            destination.create(connection)

            defaults = {
                "target_ip": "NULL",
                "record_type": "'USER'",
                "active": "1",
            }
            copy_columns = [
                column.name
                for column in destination.columns
                if column.name in source_columns or column.name in defaults
            ]
            quote = connection.dialect.identifier_preparer.quote
            target_list = ", ".join(quote(name) for name in copy_columns)
            source_list = ", ".join(
                quote(name) if name in source_columns else defaults[name]
                for name in copy_columns
            )
            connection.execute(
                text(
                    f"INSERT INTO {quote(temp_name)} ({target_list}) "
                    f"SELECT {source_list} FROM {quote('avaya_users_cache')}"
                )
            )
            connection.execute(text("DROP TABLE avaya_users_cache"))
            connection.execute(
                text(
                    f"ALTER TABLE {quote(temp_name)} "
                    "RENAME TO avaya_users_cache"
                )
            )

    # create_all does not add indexes or constraints to an existing table.
    inspector = inspect(engine)
    composite_exists = any(
        item.get("unique")
        and item.get("column_names") == ["target_ip", "guid"]
        for item in inspector.get_indexes("avaya_users_cache")
    ) or any(
        item.get("column_names") == ["target_ip", "guid"]
        for item in inspector.get_unique_constraints("avaya_users_cache")
    )
    if not composite_exists:
        Index("uq_avaya_users_cache_target_guid", AvayaUserCache.target_ip, AvayaUserCache.guid, unique=True).create(bind=engine, checkfirst=True)

    for index in AvayaUserCache.__table__.indexes:
        index.create(bind=engine, checkfirst=True)
