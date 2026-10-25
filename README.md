# Avaya FastAPI — Stage 2

SQLite, autenticação Bearer/JWT, RBAC (`admin`, `provisioning`, `readonly`),
cache de usuários Avaya, sincronização, provisionamento com idempotência e
compensação, reconciliação operacional e auditoria.

## Pré-requisitos

- Python 3.10+ (recomendado 3.13 — o `pwdlib[argon2)` tem problemas com Python 3.15)
- Acesso a um Avaya IP Office com WebManagement SDK habilitado

## Instalação

1. `python -m venv .venv`
2. `.\.venv\Scripts\Activate.ps1`
3. `pip install -r requirements.txt`
4. copie `.env.example` para `.env` com `Copy-Item .env.example .env`
5. configure credenciais Avaya, `SECRET_KEY` e `BOOTSTRAP_ADMIN_*` no `.env`
6. `uvicorn app.main:app --reload`
7. abra `/docs`

## Perfis de acesso (RBAC)

| Perfil | Consultar | Sincronizar | Provisionar | Administrar |
|--------|-----------|-------------|-------------|-------------|
| `admin` | ✅ | ✅ | ✅ | ✅ |
| `provisioning` | ✅ | ✅ | ✅ | ❌ |
| `readonly` | ✅ | ❌ | ❌ | ❌ |

## Endpoints principais

### Autenticação
- `POST /api/auth/login` — obtenha o token Bearer

### Usuários (cache local)
- `POST /api/users/sync?target_ip=<ip>` — sincroniza usuários de um IPO (admin/provisioning)
- `GET /api/users` — lista usuários do cache com paginação e filtros
- `GET /api/users/export` - exporta os usuarios filtrados em CSV
- `GET /api/users/detail/{user_id}` — detalhe de um usuário

### Sistemas (inventário multi-IPO)
- `GET /api/systems` — lista sistemas registrados
- `GET /api/systems/summary` — resumo consolidado por sistema
- `POST /api/systems/register` — registra um IPO manualmente
- `POST /api/systems/sync?target_ip=<ip>` — sincroniza dados do sistema via Avaya

### Dashboard
- `GET /api/dashboard/overview` — visão operacional consolidada
- `GET /health` - sonda de disponibilidade simples, sem autenticação
- `GET /api/health` — status rápido do banco e cache
- `GET /api/health/summary` — resumo operacional por IPO

### Provisionamento (admin/provisioning)
- `POST /api/provisioning/users/preview` — valida nome/extensão sem criar
- `POST /api/provisioning/users/dry-run` — monta XML sem enviar ao Avaya
- `POST /api/provisioning/users?confirm=true` — cria usuário (requer `Idempotency-Key`)
- `GET /api/provisioning/operations` — lista operações de provisioning
- `POST /api/provisioning/operations/{id}/reconcile` — reconciliação operacional

### Administração (admin)
- `POST /api/admin/operators` — cria operador
- `GET /api/admin/operators` — lista operadores
- `GET /api/admin/audit` — log de auditoria

## Segurança

- O cache **não** armazena `LoginCode`, `VoicemailCode`, senhas nem XML bruto.
- Campos sensíveis são mascarados em dry-run e nunca retornados em claro.
- Operações de provisioning usam idempotência (`Idempotency-Key`) e compensação automática (rollback) em caso de falha de pós-validação.

## Testes

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -v
```

## Estrutura do projeto

```
app/
  main.py              # FastAPI app + lifespan + rotas
  config.py            # Configuração via .env (python-dotenv)
  db.py                # SQLAlchemy engine, sessão, init_db
  models.py            # Modelos ORM (usuários, cache, sistemas, operações, auditoria)
  security.py          # Hash de senha (argon2), JWT HS256
  auth.py              # Login, current_operator, require_roles
  bootstrap.py         # Criação do admin bootstrap
  avaya/
    client.py          # Cliente Avaya IP Office WebManagement SDK
    provisioning.py    # Serviço de provisionamento (XML, create, parse, compensação)
  api/
    users.py           # Sync e listagem de usuários
    admin.py           # Operadores e auditoria
    health.py          # Health check
    health_summary.py  # Resumo por IPO
    dashboard.py       # Dashboard operacional
    dashboard_page.py  # Páginas HTML do dashboard
    user_detail.py     # Detalhe de usuário
    provisioning.py    # Endpoints de provisionamento
    systems.py         # Inventário de sistemas
    reconciliation.py  # Reconciliação operacional
  templates/           # Templates HTML do dashboard
tests/
  test_api.py          # Testes de integração da API (31 testes)
  test_avaya_users_xml_validation.py  # Testes do parser XML Avaya
migrations/            # Scripts de migração SQLite
```

## Variáveis de ambiente (`.env`)

| Variável | Exemplo | Obrigatório |
| ---------- | --------- | ------------- |
| `AVAYA_PORT` | `7070` | Não |
| `AVAYA_USERNAME` | `xxx` | Sim |
| `AVAYA_PASSWORD` | `...` | Sim |
| `AVAYA_VERIFY_SSL` | `false` | Não |
| `AVAYA_TIMEOUT` | `30` | Não |
| `DATABASE_URL` | `sqlite:///./avaya.db` | Não |
| `SECRET_KEY` | `...` | Sim |
| `ACCESS_TOKEN_MINUTES` | `60` | Não |
| `BOOTSTRAP_ADMIN_USER` | `xxx` | Não |
| `BOOTSTRAP_ADMIN_PASSWORD` | `xxx` | Não |

O IP do IPO não é fixado na configuração da aplicação. Informe o IP de destino em cada sincronização ou operação de provisionamento; a API conecta diretamente a esse IPO. `AVAYA_USERNAME` e `AVAYA_PASSWORD` são as credenciais compartilhadas para os IPOs, e `AVAYA_PORT`, TLS e timeout continuam configuráveis. Na inicialização, o SQLite atualiza automaticamente a chave do cache para `(target_ip, guid)`, preservando os registros existentes.


## Notas de correção (esta sessão)

- Adicionada coluna `last_login_at` ao modelo `OperatorUser` (estava sendo gravada mas não existia no modelo).
- Corrigido `app/api/systems.py`: `last_user_sync[0]` → `last_user_sync` (`.first()` retorna objeto, não lista).
- Removido prefix duplicado `/api/systems` em `main.py` (o router já define seu próprio prefix).
- Removidos blocos de teste forçado (`AVAYA_PROVISIONING_TEST_FORCE_*`) do código de produção em `app/api/provisioning.py`.
- Substituído `@app.on_event("startup")` por `lifespan` (moderno, sem deprecation warning).
- Removidos ~30 arquivos de backup (`.bak`) e duplicados que poluíam o projeto.
- Banco recriado com todas as 5 tabelas: `audit_log`, `avaya_systems`, `avaya_users_cache`, `operator_users`, `provisioning_operations`.

O cache não armazena LoginCode/VoicemailCode nem XML bruto.

## Ramais e grupos de ramais

A tela de gestão fica em `/dashboard/telephony`. Ela permite sincronizar cada recurso por IP do IPO, consultar o cache por IPO, filtrar registros, exportar CSV e criar ramais/grupos. As exclusões exigem `confirm=true` e permissão de administrador; criações e alterações exigem perfil `admin` ou `provisioning`.

- `GET/POST /api/extensions` — lista cache e cria ramal.
- `GET /api/extensions/detail/{extension_id}` — consulta um ramal do cache.
- `POST /api/extensions/sync?target_ip=...` — sincroniza ramais do IPO.
- `GET /api/extensions/export` — exporta CSV.
- `PUT/DELETE /api/extensions/{guid}` — atualiza/exclui ramal; mutações exigem `target_ip` e exclusão exige `confirm=true`.
- `GET/POST /api/huntgroups` — lista cache e cria grupo.
- `GET /api/huntgroups/detail/{huntgroup_id}` — consulta um grupo do cache.
- `POST /api/huntgroups/sync?target_ip=...` — sincroniza grupos do IPO.
- `GET /api/huntgroups/export` — exporta CSV.
- `PUT/DELETE /api/huntgroups/{guid}` — atualiza/exclui grupo; mutações exigem `target_ip` e exclusão exige `confirm=true`.

Os tipos de ramal seguem `TypeInfo` (Normal 0, H.323 1, ADMM 2, SIP 3, D100 4). O modo de toque de grupo segue os valores documentados 0–4; o cache separa registros pelo par `(target_ip, GUID)` e uma sincronização vazia é rejeitada para preservar o cache existente.
