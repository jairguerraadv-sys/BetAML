# Matriz RBAC

Esta matriz e coberta por `tests/security/test_rbac_matrix.py`.

| Area/acao | Operador_Analista | Operador_Gestor | Operador_AdminTecnico | BetAML_SuperAdmin | ADMIN | AML_ANALYST | AUDITOR | SUPER_ADMIN |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Ingestao escrever | Nao | Nao | Sim | Sim | Sim | Nao | Nao | Sim |
| Alertas escrever | Sim | Sim | Nao | Sim | Sim | Sim | Nao | Sim |
| Casos escrever | Sim | Sim | Nao | Sim | Sim | Sim | Nao | Sim |
| Report package escrever | Sim | Sim | Nao | Sim | Sim | Sim | Nao | Sim |
| Rules escrever | Nao | Sim | Nao | Sim | Sim | Nao | Nao | Sim |
| Mappings escrever | Nao | Nao | Sim | Sim | Sim | Nao | Nao | Sim |
| Admin/users escrever | Nao | Nao | Sim | Sim | Sim | Nao | Nao | Sim |
| Admin/tenants administrar | Nao | Nao | Nao | Sim | Nao | Nao | Nao | Sim |
| Audit logs ler | Sim | Sim | Sim | Sim | Sim | Sim | Sim | Sim |
| LGPD erase | Nao | Sim | Nao | Sim | Sim | Nao | Nao | Sim |
| Model registry promover | Nao | Nao | Nao | Sim | Nao | Nao | Nao | Sim |

Regras de seguranca:

- `AUDITOR` e somente leitura.
- `Operador_Analista` nao executa administracao de usuarios, tenants, mappings ou rules.
- `Operador_AdminTecnico` opera ingestao, mappings, usuarios e settings, mas nao recebe decisoes investigativas por padrao.
- `BetAML_SuperAdmin` mantem superpermissao `*`; rotas cross-tenant devem deixar essa autorizacao explicita.
- Papeis legados continuam aceitos para compatibilidade, mas novos fluxos devem preferir `Operador_*` e `BetAML_SuperAdmin`.
