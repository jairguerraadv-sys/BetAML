# BetAML - Backlog de Product Readiness

Estado atual consolidado em 2026-04-07:
- cadeia local de readiness fechada no branch atual com evidencias em `artifacts/readiness/`;
- `readiness_preflight=PASS`, `github_actions_readiness=PASS`, `restore_drill=PASS`, `load_slo=PASS` e `release_go_no_go=GO`;
- smoke, extended e security reexecutados com XMLs JUnit validos.

Este backlog nao descreve mais bloqueios internos da Fase 1/Fase 2 ja fechados localmente. A partir daqui ele registra o delta restante para producao formal e para repeticao da mesma cadeia fora do ambiente local de referencia.

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

## P0 - Obrigatorio antes de producao formal

1. Repetir a mesma cadeia no workflow remoto ou no ambiente alvo de staging.
   - Critério de aceite: artefatos equivalentes aos de `artifacts/readiness/` publicados pelo ambiente oficial.

2. Substituir metadados locais por metadados operacionais reais.
   - Critério de aceite: `rollback_target`, `oncall_owner`, backup de producao e janela de deploy definidos pelo operador responsavel.

3. Validar provedores externos, secrets e credenciais reais fora do modo local.
   - Critério de aceite: nenhum fluxo critico de producao depende de provider mock, segredo default ou identidade local de desenvolvimento.

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