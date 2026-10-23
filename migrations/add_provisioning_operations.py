from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path("avaya.db")

EXPECTED_COLUMNS = {
    "id", "idempotency_key", "target_ip", "name", "extension", "guid",
    "status", "username", "request_hash", "error_code", "error_detail",
    "compensation_attempted", "compensation_success", "created_at", "updated_at",
}

def backup_database() -> Path:
    if not DB_PATH.exists():
        raise SystemExit(f"[ERRO] Banco não encontrado: {DB_PATH.resolve()}")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = DB_PATH.with_name(
        f"avaya_backup_before_provisioning_operations_{timestamp}.db"
    )
    shutil.copy2(DB_PATH, backup)
    print(f"[OK] Backup: {backup.resolve()}")
    return backup

def migrate() -> None:
    backup_database()
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute('''
            CREATE TABLE IF NOT EXISTS provisioning_operations (
                id INTEGER PRIMARY KEY,
                idempotency_key VARCHAR(100) NOT NULL UNIQUE,
                target_ip VARCHAR(45) NOT NULL,
                name VARCHAR(255) NOT NULL,
                extension VARCHAR(80) NOT NULL,
                guid VARCHAR(100),
                status VARCHAR(40) NOT NULL,
                username VARCHAR(80),
                request_hash VARCHAR(64),
                error_code VARCHAR(100),
                error_detail TEXT,
                compensation_attempted BOOLEAN NOT NULL DEFAULT 0,
                compensation_success BOOLEAN,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
        ''')
        conn.execute('''
            CREATE UNIQUE INDEX IF NOT EXISTS
            ix_provisioning_operations_idempotency_key
            ON provisioning_operations(idempotency_key)
        ''')
        conn.execute('''
            CREATE INDEX IF NOT EXISTS ix_provisioning_operations_target_ip
            ON provisioning_operations(target_ip)
        ''')
        conn.execute('''
            CREATE INDEX IF NOT EXISTS ix_provisioning_operations_status
            ON provisioning_operations(status)
        ''')
        conn.execute('''
            CREATE INDEX IF NOT EXISTS ix_provisioning_operations_guid
            ON provisioning_operations(guid)
        ''')
        conn.commit()

        rows = conn.execute("PRAGMA table_info(provisioning_operations)").fetchall()
        columns = {row[1] for row in rows}
        missing = EXPECTED_COLUMNS - columns
        if missing:
            raise RuntimeError(
                "Estrutura incompleta: " + ", ".join(sorted(missing))
            )

        count = conn.execute(
            "SELECT COUNT(*) FROM provisioning_operations"
        ).fetchone()[0]

        print("[OK] Tabela provisioning_operations criada/validada.")
        print(f"[OK] Colunas validadas: {len(columns)}")
        print(f"[OK] Operações existentes preservadas: {count}")
        print("[OK] Índices validados/criados.")
        print("[OK] Migração FASE 1A concluída.")
    finally:
        conn.close()

if __name__ == "__main__":
    migrate()
