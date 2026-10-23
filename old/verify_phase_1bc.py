# verify_phase_1bc.py
#
# Executar na raiz do projeto depois de copiar os patches.
#
# Este teste NÃO cria usuário no Avaya.
# Ele verifica imports, tabela e modelo SQLAlchemy.

import sqlite3
from pathlib import Path

print("== FASE 1B/1C - validação estrutural ==")

db = sqlite3.connect("avaya.db")
table = db.execute(
    "SELECT name FROM sqlite_master "
    "WHERE type='table' AND name='provisioning_operations'"
).fetchone()
print("Tabela:", table)

columns = [
    row[1]
    for row in db.execute(
        "PRAGMA table_info(provisioning_operations)"
    ).fetchall()
]
print("Colunas:", len(columns))
print("idempotency_key:", "idempotency_key" in columns)
print("request_hash:", "request_hash" in columns)
print("compensation_attempted:", "compensation_attempted" in columns)
print("compensation_success:", "compensation_success" in columns)
print("Operações:", db.execute(
    "SELECT COUNT(*) FROM provisioning_operations"
).fetchone()[0])
db.close()

from app.models import ProvisioningOperation
from app.api import provisioning

print("Modelo:", ProvisioningOperation.__tablename__)
print("Endpoint provisioning importado:", provisioning.router.prefix)
print("OK")
