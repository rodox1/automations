import sqlite3
from pathlib import Path

DB_PATH = Path("avaya.db")

conn = sqlite3.connect(DB_PATH)
try:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(avaya_users_cache)")}
    if "record_type" not in cols:
        conn.execute("ALTER TABLE avaya_users_cache ADD COLUMN record_type VARCHAR(20) NOT NULL DEFAULT 'USER'")
        print("[OK] Coluna criada: record_type")
    else:
        print("[OK] Coluna já existe: record_type")

    conn.execute("CREATE INDEX IF NOT EXISTS ix_avaya_users_cache_record_type ON avaya_users_cache(record_type)")
    conn.execute("UPDATE avaya_users_cache SET record_type='SYSTEM' WHERE LOWER(TRIM(COALESCE(name,'')))='nouser'")
    conn.execute("UPDATE avaya_users_cache SET record_type='USER' WHERE record_type IS NULL OR TRIM(record_type)=''")
    conn.commit()

    counts = conn.execute("SELECT record_type, COUNT(*) FROM avaya_users_cache GROUP BY record_type ORDER BY record_type").fetchall()
    print("[OK] Classificação atual:")
    for record_type, count in counts:
        print(f"  {record_type}: {count}")
finally:
    conn.close()
