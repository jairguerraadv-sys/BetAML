# Checklist Go/No-Go Consolidado

Data-base: 2026-05-22
Escopo: aprovacao operacional de release BetAML.

## 1. Gates obrigatorios

- [x] Preflight operacional aprovado
- [x] Restore drill aprovado
- [x] Capacity smoke aprovado
- [x] Gate local go/no-go = GO
- [x] Gate remoto Release Readiness = PASS

Fonte:
- artifacts/readiness/release-go-no-go.txt
- artifacts/readiness/release-readiness-remote.txt

## 2. Evidencias de regressao API Fase 1

- [x] Security (tenant isolation, RBAC, authz) validado
- [x] Auth (JWT, refresh, tenant-bound) validado
- [x] Ingest (core, extended, resilience) validado
- [x] Resultado sem falhas: 137 passed, 8 skipped

Fonte:
- artifacts/readiness/fase1-api-regressao-2026-05-22.md
- artifacts/readiness/junit/fase1-api-regressao-2026-05-22.xml

## 3. Operacao e rollback

- [ ] backup_reference confirmado (<24h)
- [x] rollback_target confirmado e testavel
- [x] oncall_owner confirmado para janela de 60 minutos
- [ ] comunicacao de janela de deploy enviada

Fonte de referencia:
- docs/go-live-checklist.md
- docs/release-handoff.md

## 4. Decisao formal

- [ ] GO
- [x] NO-GO

Observacoes da decisao:
- Ultimo backup referenciado no bundle e de 2026-04-07, fora da janela obrigatoria de 24h.
- Comunicacao formal da janela de deploy nao consta como evidencia neste bundle.
- Go-live deve ser reavaliado apos refresh de backup, comunicacao da janela e nova assinatura final.



## 5. Critico para assinatura

Sem preenchimento dos itens da secao 3, a decisao deve permanecer NO-GO.
