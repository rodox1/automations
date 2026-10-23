from pathlib import Path
import sys
from sqlalchemy import inspect, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import engine

def main():
    if "avaya_systems" not in inspect(engine).get_table_names():
        with engine.begin() as conn:
            conn.execute(text('''
                CREATE TABLE avaya_systems (
                    id INTEGER NOT NULL PRIMARY KEY,
                    target_ip VARCHAR(45) NOT NULL UNIQUE,
                    name VARCHAR(255),
                    role VARCHAR(30) NOT NULL DEFAULT 'expansion',
                    major_version VARCHAR(30),
                    minor_version VARCHAR(30),
                    configuration_version VARCHAR(30),
                    build_version VARCHAR(30),
                    active BOOLEAN NOT NULL DEFAULT 1,
                    discovered BOOLEAN NOT NULL DEFAULT 0,
                    last_seen_at DATETIME,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
            '''))

    with engine.begin() as conn:
        for name, column in [
            ("ix_avaya_systems_target_ip", "target_ip"),
            ("ix_avaya_systems_name", "name"),
            ("ix_avaya_systems_role", "role"),
            ("ix_avaya_systems_active", "active"),
            ("ix_avaya_systems_discovered", "discovered"),
        ]:
            conn.execute(text(
                f"CREATE INDEX IF NOT EXISTS {name} "
                f"ON avaya_systems ({column})"
            ))

    print("[OK] Inventário avaya_systems criado/validado.")

if __name__ == "__main__":
    main()
