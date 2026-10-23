# Resumo por IPO — próxima etapa

Arquivo completo atualizado:
- `api_systems.py`

Novo endpoint:
- `GET /api/systems/summary`
- `GET /api/systems/summary?target_ip=172.16.36.50`
- `GET /api/systems/summary?target_ip=172.16.70.10`

O endpoint consulta somente o SQLite e retorna, por sistema:
- identificação do IPO
- papel (primary/expansion)
- versão
- ativo/discovered
- último `last_system_seen_at`
- total de usuários
- usuários ativos
- usuários inativos
- último `last_sync_at`

Também retorna totais consolidados.

Não realiza chamada ao Avaya e não altera dados.

Validação executada:
- `python -m py_compile api_systems.py` → OK
