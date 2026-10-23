# Multi-IPO — próxima etapa

Arquivos:
- `models.py`: modelo com `target_ip`.
- `users.py`: sync direcionado e filtro por IPO.
- `migrate_multi_ipo.py`: migração SQLite idempotente.
- `client_get_users_patch.txt`: alteração mínima no cliente Avaya.

Ordem de aplicação:

1. Faça backup do SQLite.
2. Substitua `app/models.py` por `models.py`.
3. Aplique `client_get_users_patch.txt` em `app/avaya/client.py`.
4. Crie `migrations/migrate_multi_ipo.py`.
5. Execute:
   `python migrations/migrate_multi_ipo.py`
6. Reinicie o Uvicorn.
7. Sincronize Primary:
   `POST /api/users/sync?target_ip=172.16.36.50`
8. Sincronize Expansion:
   `POST /api/users/sync?target_ip=172.16.70.10`
9. Consulte:
   `GET /api/users?target_ip=172.16.70.10`
10. Valide `TesteExp02` e seu GUID.

Importante:
- O sync não apaga usuários existentes.
- `guid` continua único.
- `target_ip` identifica o IPO/Solution Node.
- O sync sem `target_ip` continua disponível para compatibilidade.
