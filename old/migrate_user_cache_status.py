from pathlib import Path
import sqlite3, shutil
from datetime import datetime
DB_PATH=Path("avaya.db")
def main():
    if not DB_PATH.exists(): raise SystemExit(f"[ERRO] Banco não encontrado: {DB_PATH.resolve()}")
    backup=DB_PATH.with_name(f"avaya_backup_before_user_status_{datetime.now():%Y%m%d_%H%M%S}.db")
    shutil.copy2(DB_PATH,backup); print(f"[OK] Backup: {backup}")
    con=sqlite3.connect(DB_PATH)
    try:
        cols={r[1] for r in con.execute("PRAGMA table_info(avaya_users_cache)").fetchall()}
        if "active" not in cols: con.execute("ALTER TABLE avaya_users_cache ADD COLUMN active BOOLEAN NOT NULL DEFAULT 1"); print("[OK] Coluna criada: active")
        else: print("[OK] Coluna já existe: active")
        if "last_seen_at" not in cols: con.execute("ALTER TABLE avaya_users_cache ADD COLUMN last_seen_at DATETIME NULL"); print("[OK] Coluna criada: last_seen_at")
        else: print("[OK] Coluna já existe: last_seen_at")
        con.execute("CREATE INDEX IF NOT EXISTS ix_avaya_users_cache_active ON avaya_users_cache(active)")
        con.execute("CREATE INDEX IF NOT EXISTS ix_avaya_users_cache_last_seen_at ON avaya_users_cache(last_seen_at)")
        con.execute("UPDATE avaya_users_cache SET active=1 WHERE active IS NULL")
        con.commit(); print("[OK] Migração concluída.")
    finally: con.close()
if __name__=="__main__": main()
