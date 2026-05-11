# BetAML - Backlog de Product Readiness

Estado atual consolidado em 2026-05-11:
- cadeia local de readiness fechada no branch atual com evidencias em `artifacts/readiness/`;
- workflow remoto `Release Readiness` fechado com `success` na run `25696032708` para o head `74c9e14`;
- `readiness_preflight=PASS`, `github_actions_readiness=PASS`, `restore_drill=PASS`, `load_slo=PASS`, `release_go_no_go=GO` e `release_readiness_remote=PASS`;
- bloqueios remotos de fechamento eliminados com bootstrap controlado da API e lockfile versionado do E2E;
- smoke, extended e security reexecutados com XMLs JUnit validos.

Este backlog nao descreve mais bloqueios internos da Fase 1/Fase 2 ja fechados localmente. A partir daqui ele registra o delta restante para producao formal, com a cadeia local e a cadeia remota ja encerradas.

## Fechado no branch atual

1. Bootstrap, healthchecks e onboarding de tenant revalidados.
   - API voltou a responder `/health/live` e `/health/ready`.
   - Onboarding e criacao de tenant agora operam com contexto de plataforma e seed deterministica de superadmin.

2. Fluxos criticos de frontend e contratos UI/API estabilizados.
   - Smoke e extended fecharam com navegacao, mappings, ingest operations, onboarding, report exports e maintenance mode verdes.

3. RBAC e PII endurecidos nos pontos validados.
   - Auditor legado ficou explicitamente bloqueado em mutacoes de alertas.
   - A UI do jogador passou a sinalizar mascaramento apenas quando o valor retornado vier de fato mascarado.

4. Prontidao operacional local comprovada.
   - Preflight, restore drill, capacity smoke e gate final de go/no-go foram executados com evidencia arquivada.

5. Cadeia remota de readiness fechada.
   - O workflow `Release Readiness` do GitHub Actions concluiu em `success` para o commit `74c9e14`, incluindo external validation smoke, install de Playwright deps, smoke, extended, security e go/no-go final.

## P0 - Obrigatorio antes de producao formal

1. Substituir metadados locais por metadados operacionais reais.
   - Critério de aceite: `rollback_target`, `oncall_owner`, backup de producao e janela de deploy definidos pelo operador responsavel.

2. Validar provedores externos, secrets e credenciais reais fora do modo local.
   - Critério de aceite: nenhum fluxo critico de producao depende de provider mock, segredo default ou identidade local de desenvolvimento.

3. Executar o deploy formal com smoke pos-release no ambiente alvo.
   - Critério de aceite: deploy concluido com backup < 24h, revisao real de rollback registrada e smoke funcional pos-deploy sem 5xx persistente, backlog anormal ou quebra de tenant isolation.

## P1 - Hardening de producao

4. Formalizar secret manager, TLS/ingress e runbook de rotacao.
   - Critério de aceite: segredos fora do repositório e politicas de rotacao aprovadas por Operacoes e Compliance.

5. Validar observabilidade e alarmistica no ambiente-alvo.
   - Critério de aceite: dashboards, alertas e canais de on-call respondem com dados reais apos o deploy.

6. Revisar dataset e seeds fora do ambiente local.
   - Critério de aceite: staging/producao nao dependem de seeds sinteticos para operar e auditar fluxos criticos.

## P2 - Pos go-live

7. Endurecer o ciclo de vida de modelos para operacao assistida.
   - Critério de aceite: promocao, fallback e explainability seguem governanca revisada no ambiente real.

8. Transformar a cadeia local em gate repetivel de release.
   - Critério de aceite: o status do branch possa ser reprovado/aprovado automaticamente a partir dos mesmos artefatos e thresholds.

## Regra de execucao

Cada agente deve:
- trabalhar com escopo estreito;
- validar com evidencias reais;
- devolver riscos residuais explicitamente;
- evitar expandir o produto alem do necessario para readiness.