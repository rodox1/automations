# Etapa 3 — Provisionamento

Implementa `POST /api/provisioning/users/preview`, `POST /api/provisioning/users` e `PUT /api/provisioning/users/{guid}`.

A documentação oficial da Avaya descreve POST no `/WebManagement/ws/sdk/admin/v1/users` para criar usuários e PUT no mesmo URI para atualizar; no PUT o GUID deve estar no atributo `User`. A API pode responder HTTP 200 mesmo quando há erro de aplicação, portanto o código também valida `response status` e elementos `error`. citeturn3view0turn4view0

**Fluxo seguro:** primeiro `preview`; depois escrita somente com `confirm=true`. Perfis permitidos: `admin` e `provisioning`.

Teste inicialmente com uma extensão/nome de laboratório livres. Não execute criação/alteração em produção sem autorização operacional.
