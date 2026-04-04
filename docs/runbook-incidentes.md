# BetAML - Runbook de Incidentes

## Severidade

- SEV1: plataforma indisponivel ou perda de trilha AML.
- SEV2: degradacao alta de API, ingestao ou regras.
- SEV3: falha parcial com workaround disponivel.

## 1. Triagem inicial (0-10 min)

- Confirmar escopo: API, frontend, ingestao, regras, ML.
- Coletar sinais:
  - `docker compose -f infra/docker-compose.yml ps`
  - `docker compose -f infra/docker-compose.yml logs --tail=200 api`
  - `docker compose -f infra/docker-compose.yml logs --tail=200 stream-processor`
- Classificar severidade e acionar on-call.

## 2. Fluxos de diagnostico rapido

### API com 5xx alto

- Verificar `GET /health`.
- Validar conexao PostgreSQL/Redis.
- Revisar ultimas mudancas e migration aplicada.

### Ingestao parada

- Confirmar Redpanda/topic-init.
- Verificar crescimento de `ingest_errors`.
- Validar consumidores em `stream_processor` e `rules_engine`.

### Alertas nao gerados

- Verificar topicos `canonical.*`, `features.*`, `scoring.alerts`.
- Conferir status das regras ativas no PostgreSQL.
- Validar logs de avaliacao DSL no `rules_engine`.

## 3. Mitigacao

- Rollback de deploy para versao anterior estavel.
- Antes do rollback, registrar revisao alvo, ultimo backup valido e decidir se o incidente e de aplicacao ou exige restore.
- Reaplicar migracao idempotente se drift detectado.
- Em falha de schema legado, marcar baseline Alembic com `stamp` e revalidar.
- Isolar tenant impactado se necessario para manter plataforma ativa.

## 4. Recuperacao e validacao

- Rodar smoke funcional:
  - login
  - listagem de alertas
  - criacao de caso
- Rodar `bash scripts/readiness_preflight.sh --evidence-out /tmp/betaml-incident-preflight.txt` antes de encerrar o incidente.
- Validar fila voltando ao normal e queda de erros.
- Monitorar por 30 min apos restauracao.

## 5. Pos-incidente

- Publicar RCA em ate 48h.
- Definir owner e prazo para acao preventiva.
- Atualizar este runbook com novo aprendizado.
