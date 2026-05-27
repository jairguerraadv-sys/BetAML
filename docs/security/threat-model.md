# Threat Model

## Ativos sensiveis

- PII: CPF, nome, data de nascimento, IP, device fingerprint e eventos KYC.
- Credenciais: JWT, refresh tokens, API keys, secrets de webhook, credenciais de banco/MinIO/Redis.
- Dados regulatorios: alerts, cases, report packages, audit logs e cadeia de custodia.
- Dados multi-tenant: todos os registros com `tenant_id`, mappings, rules, features e model registry.

## Fronteiras de confianca

- Navegador do operador para API.
- Conectores externos para endpoints de ingestao.
- API para Postgres com RLS.
- API/stream/rules/ml para Redpanda, Redis, MinIO e ClickHouse.
- Superadmin BetAML para operacao cross-tenant explicitamente autorizada.

## Atores

- Usuario autenticado de tenant tentando IDOR cross-tenant.
- Conector comprometido com API key roubada.
- Atacante com JWT vazado.
- Insider com privilegio administrativo.
- Provedor externo enviando payload malicioso ou replay.

## STRIDE

| Ameaca | Risco | Controles atuais | Gaps/mitigacao |
|---|---|---|---|
| Spoofing via JWT vazado | Acesso como usuario | `jti`, exp curto, blacklist Redis | MFA/SSO e deteccao de anomalia de login |
| Spoofing via API key | Ingestao fraudulenta | API key v2 com tenant hint, permissao `ingest`, expiracao | Rotacao periodica obrigatoria por tenant |
| Tampering report package | Perda de cadeia de custodia | hash de payload e endpoint de verificacao | Assinatura digital externa/HSM |
| Repudiation | Usuario nega acao | audit logs append-only por API | Trigger DB anti-delete para todo papel |
| Information disclosure cross-tenant | Vazamento regulatorio/PII | RLS, testes de isolamento, RBAC | Testes E2E cross-tenant adicionais |
| Logs com PII | Exposicao indireta | sanitizacao e scanner | DLP em pipeline de logs |
| Replay webhook | Duplicidade/poisoning | HMAC e timestamp `x-epsilon-timestamp` | Nonce persistente por provedor |
| Upload malicioso | Parser abuse | limite de tamanho/tipo, parser seguro | Antimalware para evidencias |
| XML/XXE | Exfiltracao | `defusedxml` nos conectores | Fuzzing de XML |
| CSV injection | Formula injection | documentado/testavel no ingest/export | Sanitizacao padrao para exports CSV |
| Abuso superadmin | Cross-tenant indevido | papel separado e testes | break-glass approval e session recording |
| ML poisoning | Modelo contaminado | flag synthetic e governanca | validacao de label e drift gate formal |

## Plano de mitigacao

1. Manter `tests/security` como gate obrigatorio de PR.
2. Expandir RLS/IDOR para E2E autenticado.
3. Adicionar nonces persistentes para webhooks de alto risco.
4. Implementar trigger de imutabilidade em audit/report package.
5. Formalizar aprovacao humana para promocao de modelo champion.
