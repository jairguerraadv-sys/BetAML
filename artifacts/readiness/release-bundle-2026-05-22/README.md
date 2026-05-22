# Release Bundle Final - BetAML

Data de geracao: 2026-05-22
Objetivo: consolidar evidencias tecnicas e checklist de go/no-go para assinatura operacional.

## Conteudo

- artifacts/readiness/release-go-no-go.txt
- artifacts/readiness/release-readiness-remote.txt
- artifacts/readiness/fase1-api-regressao-2026-05-22.md
- artifacts/readiness/junit/fase1-api-regressao-2026-05-22.xml
- docs/go-live-checklist.md
- docs/release-handoff.md
- CHECKLIST-GO-NO-GO-CONSOLIDADO.md
- ASSINATURA-OPERACIONAL.md
- MANIFEST.sha256

## Evidencia consolidada (resumo)

- Gate local go/no-go: GO (release_go_no_go=GO)
- Gate remoto GitHub Actions: PASS (run 25696032708, conclusion=success)
- Regressao API Fase 1: 145 coletados, 137 passed, 8 skipped, 0 failed

## Uso operacional

1. Revisar CHECKLIST-GO-NO-GO-CONSOLIDADO.md
2. Validar integridade dos arquivos via MANIFEST.sha256
3. Coletar assinaturas em ASSINATURA-OPERACIONAL.md
4. Arquivar o bundle com o ticket de release
