from app.db import engine
from sqlalchemy import inspect

inspector = inspect(engine)

print("COLUNAS avaya_users_cache:")
for column in inspector.get_columns("avaya_users_cache"):
    print(f"  {column['name']} ({column['type']})")

print()
print("INDICES:")
for index in inspector.get_indexes("avaya_users_cache"):
    print(f"  {index['name']}")

print()
print("VALIDACAO:")
columns = {c["name"] for c in inspector.get_columns("avaya_users_cache")}
indexes = {i["name"] for i in inspector.get_indexes("avaya_users_cache")}

if "target_ip" in columns:
    print("[OK] Coluna target_ip existe.")
else:
    print("[ERRO] Coluna target_ip NAO encontrada.")

if "ix_avaya_users_cache_target_ip" in indexes:
    print("[OK] Indice ix_avaya_users_cache_target_ip existe.")
else:
    print("[ERRO] Indice ix_avaya_users_cache_target_ip NAO encontrado.")