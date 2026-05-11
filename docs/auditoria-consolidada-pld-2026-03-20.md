# BetAML - Auditoria Consolidada do Estado Atual

Data-base original: 2026-03-20
Revalidado historicamente em: 2026-04-04
Escopo: consolidacao de auditorias historicas + confronto com o estado real do branch atual
Status executivo atual: parecer historico de NO-GO de marco, superado localmente em 2026-04-07 e remotamente em 2026-05-11 pelo fechamento do workflow `Release Readiness` e pelas evidencias em `artifacts/readiness/`

## 1. Objetivo deste documento

Este arquivo continua sendo a consolidacao historica principal do projeto, mas nao deve mais ser lido como o parecer executivo vigente por si so.

O parecer tecnico mais recente do branch atual deve considerar em conjunto:
- este documento, apenas como diagnostico historico;
- `docs/go-live-checklist.md`, `docs/ops-guide.md` e `docs/runbook-deploy.md`, como procedimento;
- `artifacts/readiness/`, como evidencia objetiva mais recente do estado validado localmente e com fechamento remoto de readiness.

Use este documento para:
- entender quais auditorias e relatórios antigos ainda servem como contexto;
- identificar afirmações históricas que ficaram invalidadas pelo branch atual;
- apontar as fontes canônicas ainda válidas para operação, segurança e deploy.

Não use versões antigas deste arquivo como evidência de readiness atual sem regenerar validações no branch vigente.

## 2. Consolidação documental

### 2.1 Auditorias históricas aproveitáveis

Os documentos históricos abaixo ainda são úteis como contexto arquitetural e backlog de risco, mas não podem mais ser tratados como prova de readiness atual:
- docs/go-live-checklist.md
- docs/ops-guide.md
- docs/runbook-incidentes.md
- docs/security-secrets-management.md
- docs/branch-protection.md
- docs/slo-sli.md
- docs/analyst-guide.md
- docs/aml-scorecard.md
- docs/ml-trainer-implementation.md
- docs/product-readiness-backlog.md

### 2.2 Documentos invalidados ou obsoletos

Os seguintes artefatos foram invalidados como fonte de verdade operacional:
- qualquer referência antiga a READY FOR STAGING ou aprovacao condicional para producao sem reexecucao dos gates atuais;
- historicos de testes manuais anexados em datas anteriores como evidência suficiente para o branch atual;
- relatórios promocionais de “100% enterprise-ready” sem validação runtime reproduzível;
- mapas de dependência que descrevem fluxo raw -> canonical quando o runtime atual publica majoritariamente direto em canonical.

### 2.3 Fonte canônica atual

As fontes canônicas atuais devem ser usadas em conjunto:
- este documento, para contexto e invalidação de histórico desatualizado;
- docs/product-readiness-backlog.md, para priorização residual;
- docs/go-live-checklist.md, para critérios formais de go/no-go;
- docs/ops-guide.md e docs/runbook-deploy.md, para procedimentos operacionais;
- artifacts/readiness/, para o estado objetivo mais recente validado no branch atual.

## 3. Revalidação do estado atual

### 3.1 O que permanece validado e aproveitável

Os seguintes blocos permanecem estruturalmente válidos e aproveitáveis:
- arquitetura multi-serviço com API, frontend, stream processor, rules engine, ml-service e ml-trainer;
- stack operacional com PostgreSQL, Redis, Redpanda, MinIO, ClickHouse e observabilidade provisionada em compose;
- presença de filas operacionais de alertas, casos, relatórios, feature store e administração no frontend;
- documentação de restore drill, readiness, capacity smoke, go/no-go e runbooks;
- infraestrutura de autenticação JWT, blacklist de refresh/logout, audit logs, RLS parcial e masking de PII;
- base funcional de regras DSL, features, model registry, report package e trilha de casos.

### 3.2 O que foi invalidado na revalidação de 2026-04-04

As afirmações abaixo, válidas ou assumidas em março, não representam mais o estado real do branch atual:
- “staging aprovado” e qualquer forma de go-live implícito;
- “stack sobe íntegra com um comando” sem intervenção adicional;
- “pipeline raw -> canonical -> features -> alerts -> cases” como descrição fiel do runtime atual;
- “ferramentas nucleares estão operacionais de ponta a ponta” sem ressalvas;
- “reportes regulatórios prontos para operação real” sem mencionar filing manual/stub;
- “isolamento multi-tenant forte” como conclusão fechada;
- “nenhum gap técnico bloqueador identificado” em gestão de alertas/casos e auditoria/PII.

### 3.3 Evidências objetivas do estado atual

Evidências levantadas na revalidação de 2026-04-04:
- a API falha no bootstrap do router de ingestão por erro de anotação Pydantic no branch atual;
- o compose não estava íntegro em runtime local: API unhealthy, frontend ausente na malha ativa observada;
- o pipeline operacional publica majoritariamente em canonical, e não em raw, contradizendo parte da documentação histórica;
- rules_engine e alert_processor seguem como autoridades concorrentes para alertas, casos e bandas de risco;
- o frontend possui incompatibilidades reais de contrato com o backend em triagem de alertas, replay de ingest error e notificações;
- o report package continua com exportações úteis, porém filing regulatório ainda é STUB_MANUAL.

## 4. Maturidade por domínio

Escala usada nesta revalidação:
- A: bom desenho e integração consistente;
- B: funcional, mas com hardening pendente e drift relevante;
- C: parcial, com bloqueio operacional real;
- D: inválido como superfície confiável no branch atual.

### 4.1 Ingestão e pipeline de dados
- Estado atual: C
- Aproveitável:
  - ingestão por evento, batch, arquivo, replay e backfill;
  - ingest jobs, quarantine e mapeamento versionado.
- Bloqueios atuais:
  - bootstrap quebrado da API;
  - topologia documental divergente do runtime;
  - inconsistência entre raw e canonical.

### 4.2 Regras, scoring e feature store
- Estado atual: B
- Aproveitável:
  - DSL, macros, regras compostas, cálculo de features e snapshots.
- Bloqueios atuais:
  - score final e risk band com mais de uma autoridade;
  - loop de feedback e registry de modelos inconsistentes.

### 4.3 Alertas e casos
- Estado atual: C
- Aproveitável:
  - filas, detalhe, comentários, lookup, linkagem e exportações básicas.
- Bloqueios atuais:
  - contratos quebrados de triagem;
  - evidência ainda em stub;
  - auto-case duplicado entre serviços.

### 4.4 Reporting regulatório
- Estado atual: C
- Aproveitável:
  - geração de JSON, PDF e XML sob demanda;
  - monthly summary e histórico por caso.
- Bloqueios atuais:
  - filing manual/stub;
  - ausência de cadeia de custódia e versionamento forte do pacote regulatório.

### 4.5 Segurança e isolamento
- Estado atual: C
- Aproveitável:
  - JWT, blacklist, parte do RLS, masking e guards para defaults críticos.
- Bloqueios atuais:
  - rotas sensíveis ainda dependem só de autenticação simples;
  - PII persiste em payloads e auditoria de forma excessiva;
  - serviço de ML sem fronteira de autenticação robusta.

## 5. Parecer executivo atual

### 5.1 Conclusão técnica

O parecer de 2026-03-20 continua valido como fotografia historica daquele momento. No estado atual do branch, os bloqueios de bootstrap, onboarding, RBAC/PII e evidencias operacionais que sustentavam o NO-GO original foram fechados localmente e revalidados com artefatos. O risco residual agora esta concentrado em formalizacao operacional fora do ambiente local, nao mais em falhas estruturais impeditivas do branch.

### 5.2 Conclusão de go-live

- Desenvolvimento local: aprovado com gate local fechado.
- Piloto controlado: tecnicamente viavel, desde que a mesma cadeia seja repetida no ambiente alvo.
- Staging: aprovado condicionalmente a reexecucao completa dos gates no ambiente oficial.
- Producao: depende de evidencias oficiais, segredos reais e metadados operacionais reais; este documento sozinho nao deve mais ser usado como no-go automatico.

## 6. Ações mandatórias antes de novo parecer de readiness

1. Reexecutar o workflow de readiness no ambiente oficial e anexar os artefatos resultantes.
2. Confirmar backup, restore drill e rollback target com metadados operacionais reais.
3. Validar providers externos e segredos de producao fora do modo local.
4. Atualizar este arquivo apenas quando uma nova auditoria executiva voltar a invalidar o estado vigente.

## 7. Uso correto deste documento daqui em diante

- Atualize este arquivo sempre que uma nova auditoria executiva invalidar conclusões anteriores.
- Não reaproveite o status histórico de março como evidência de branch atual.
- Sempre anexe artefatos gerados novamente pelo workflow de readiness ou por execução manual equivalente.
