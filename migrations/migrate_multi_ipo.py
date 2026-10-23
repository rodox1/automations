"""
Migração idempotente para suportar inventário multi-IPO.

Uso, no diretório do projeto:

    python migrations/migrate_multi_ipo.py

A migração:
1. adiciona avaya_users_cache.target_ip se ainda não existir;
2. cria índice para target_ip;
3. opcionalmente associa registros legados ao IPO_LEGACY_IP informado.

Nenhuma linha é apagada.
"""

import os

from sqlalchemy import inspect, text

from pathlib import Path
import sys

# Garante que a raiz do projeto esteja no sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.db import engine


TABLE = "avaya_users_cache"
COLUMN = "target_ip"
INDEX = "ix_avaya_users_cache_target_ip"


def migrate() -> None:
    inspector = inspect(engine)

    if TABLE not in inspector.get_table_names():
        raise RuntimeError(
            f"Tabela {TABLE!r} não existe. "
            "Inicialize o banco da aplicação antes da migração."
        )

    columns = {
        column["name"]
        for column in inspector.get_columns(TABLE)
    }

    with engine.begin() as connection:
        if COLUMN not in columns:
            connection.execute(
                text(
                    f"ALTER TABLE {TABLE} "
                    f"ADD COLUMN {COLUMN} VARCHAR(45)"
                )
            )

        connection.execute(
            text(
                f"CREATE INDEX IF NOT EXISTS {INDEX} "
                f"ON {TABLE} ({COLUMN})"
            )
        )

        # Associar dados legados somente quando o operador fornecer o IPO
        # explicitamente para esta migração. Não assumir um host global.
        primary_ip = os.getenv("IPO_LEGACY_IP", "").strip()

        if primary_ip:
            connection.execute(
                text(
                    f"UPDATE {TABLE} "
                    f"SET {COLUMN} = :primary_ip "
                    f"WHERE {COLUMN} IS NULL"
                ),
                {"primary_ip": primary_ip},
            )

    print("[OK] Migração multi-IPO concluída.")
    print(f"[OK] Tabela: {TABLE}")
    print(f"[OK] Coluna: {COLUMN}")
    print(f"[OK] Índice: {INDEX}")
    if primary_ip:
        print(f"[OK] Registros legados associados a: {primary_ip}")


if __name__ == "__main__":
    migrate()
