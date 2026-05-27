# Matriz de Ambientes

| Ambiente | Objetivo | Secrets default | Provider externo mock | Compose |
|---|---|---:|---:|---|
| development | Desenvolvimento local | Permitido | Permitido | `infra/docker-compose.dev.yml` |
| test | Unit/integration local/CI | Permitido para fixtures | Permitido | sqlite/mocks ou stack CI |
| staging | Homologacao pre-producao | Bloqueado | Bloqueado | Kubernetes/ECS/Terraform recomendado |
| production | Operacao real | Bloqueado | Bloqueado | Kubernetes/ECS/Terraform recomendado |
| SaaS | Multi-tenant BetAML | Bloqueado | Bloqueado | Isolamento por tenant/RLS |
| on-prem | Tenant unico no operador | Bloqueado | Bloqueado | exige `ONPREM_TENANT_ID` |

Staging e production devem usar AWS Secrets Manager, Azure Key Vault ou backend equivalente. `infra/docker-compose.prod.example.yml` e referencia de topologia, nao substitui IaC gerenciado.
