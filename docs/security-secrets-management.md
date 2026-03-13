# Secrets Management — Guia de Migração para Produção

## 🔴 RISCO CRÍTICO

**Em produção, NUNCA use secrets em plaintext** no `.env` ou no código. Este guia mostra como migrar para um secrets manager adequado.

---

## Por Que É Crítico?

1. `.env` commitado acidentalmente → CPFs descriptografados
2. Logs vazados → JWT_SECRET exposto → session hijacking
3. Acesso não auditado → conformidade LGPD/PCI-DSS

---

## Opções de Secrets Manager

### 1. AWS Secrets Manager (Recomendado para AWS)

**Custo**: ~$0.40/secret/mês + $0.05/10k API calls

**Setup**:
```bash
# 1. Criar secrets na AWS
aws secretsmanager create-secret \
  --name betaml/prod/jwt-secret \
  --secret-string '{"jwt_secret":"<gerar_com_secrets.token_hex32>"}'

aws secretsmanager create-secret \
  --name betaml/prod/pii-encryption-key \
  --secret-string '{"pii_encryption_key":"<gerar_com_secrets.token_urlsafe32>"}'

# 2. Rotação automática (90 dias)
aws secretsmanager rotate-secret \
  --secret-id betaml/prod/jwt-secret \
  --rotation-lambda-arn arn:aws:lambda:us-east-1:123456789012:function:BetAMLJwtRotation \
  --rotation-rules AutomaticallyAfterDays=90
```

**Integração no código** (`config.py`):
```python
import boto3
import json
from functools import lru_cache

_secrets_client = None

@lru_cache
def get_secret(secret_name: str) -> dict:
    global _secrets_client
    if not _secrets_client:
        _secrets_client = boto3.client('secretsmanager', region_name='us-east-1')

    response = _secrets_client.get_secret_value(SecretId=secret_name)
    return json.loads(response['SecretString'])


class Settings(BaseSettings):
    environment: str = "development"

    @property
    def jwt_secret(self) -> str:
        if self.environment == "development":
            return "dev-secret-change-me"
        return get_secret("betaml/prod/jwt-secret")["jwt_secret"]

    @property
    def pii_encryption_key(self) -> str:
        if self.environment == "development":
            return "ZGV2LXNlY3JldC1lbmNyeXB0aW9uLWtleS0zMmJ5"
        return get_secret("betaml/prod/pii-encryption-key")["pii_encryption_key"]
```

### 2. Azure Key Vault

**Integração**:
```python
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

credential = DefaultAzureCredential()
client = SecretClient(vault_url="https://betaml-prod.vault.azure.net/", credential=credential)

jwt_secret = client.get_secret("jwt-secret").value
pii_encryption_key = client.get_secret("pii-encryption-key").value
```

### 3. HashiCorp Vault (Self-Hosted ou HCP)

**Integração**:
```python
import hvac

client = hvac.Client(url='https://vault.betaml.io:8200')
client.auth.approle.login(role_id='<role>', secret_id='<secret>')

jwt_secret = client.secrets.kv.v2.read_secret_version(path='betaml/jwt-secret')['data']['data']['value']
pii_encryption_key = client.secrets.kv.v2.read_secret_version(path='betaml/pii-encryption-key')['data']['data']['value']
```

### 4. Kubernetes Secrets (para K8s)

**Não recomendado sozinho** (secrets são base64, não criptografados). Use com **External Secrets Operator**:

```yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: betaml-secrets
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: aws-secretsmanager
    kind: SecretStore
  target:
    name: betaml-api-secrets
  data:
  - secretKey: JWT_SECRET
    remoteRef:
      key: betaml/prod/jwt-secret
      property: jwt_secret
  - secretKey: PII_ENCRYPTION_KEY
    remoteRef:
      key: betaml/prod/pii-encryption-key
      property: pii_encryption_key
```

---

## Checklist de Migração

- [ ] **Gerar secrets novos**:
  ```bash
  python -c "import secrets; print('JWT_SECRET:', secrets.token_hex(32))"
  python -c "import secrets; print('PII_ENCRYPTION_KEY:', secrets.token_urlsafe(32))"
  ```

- [ ] **Criar secrets no manager**:
  - AWS Secrets Manager / Azure Key Vault / Vault

- [ ] **Configurar IAM/RBAC**:
  - Apenas role/service account da API pode ler secrets
  - Enable audit logging de acessos

- [ ] **Atualizar config.py**:
  - Adicionar fetching de secrets via SDK
  - Cachear secrets com TTL (ex: 5 min) para reduzir API calls

- [ ] **Testar em staging**:
  - Subir API com ENVIRONMENT=staging
  - Verificar que startup passa guards
  - Testar login + descriptografia de PII

- [ ] **Deploy em produção**:
  - Atualizar secrets no manager
  - Restart da API carrega novos secrets

- [ ] **Configurar rotação**:
  - JWT_SECRET: rotação 90 dias (requer re-login de todos os users)
  - PII_ENCRYPTION_KEY: rotação complexa (requer re-encryption de todos os CPFs → veja seção abaixo)

---

## ⚠️ Rotação de PII_ENCRYPTION_KEY (Avançado)

**ATENÇÃO**: Mudar a chave PII invalida TODOS os CPFs criptografados no banco.

**Estratégia de Rotação Sem Downtime**:
1. Adicionar coluna `pii_encryption_key_version INT DEFAULT 1` nas tabelas com PII
2. Criar nova chave v2 no secrets manager
3. Script de migração:
   ```python
   # Para cada player:
   cpf_v1 = decrypt(player.cpf_encrypted, key_v1)
   cpf_v2_encrypted = encrypt(cpf_v1, key_v2)
   db.execute("UPDATE players SET cpf_encrypted = %s, pii_encryption_key_version = 2", cpf_v2_encrypted)
   ```
4. Após 100% migrados, remover key_v1 do secrets manager

---

## Audit Trail

Todos os secrets managers têm audit logging:

**AWS CloudTrail**: logs de `GetSecretValue` com:
- Timestamp
- IAM role/user
- Source IP
- Success/failure

**Consulta exemplo**:
```bash
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=ResourceName,AttributeValue=betaml/prod/jwt-secret \
  --start-time 2026-01-01 \
  --end-time 2026-03-31
```

---

## Custo Estimado (100k req/dia)

| Manager | Custo/Mês |
|---------|-----------|
| AWS Secrets Manager | $1.60 (2 secrets × $0.40 + 100k calls × $0.05/10k) |
| Azure Key Vault | $0.03 (Standard tier, 100k ops) |
| HashiCorp Vault (HCP) | $50 (Starter tier) |
| Self-hosted Vault | $0 (compute ~ $20/mês t3.small) |

---

## FAQ

**P: Posso usar variáveis de ambiente no ECS/K8s em vez de secrets manager?**
R: Sim, mas:
- Variáveis de ambiente são visíveis via `docker inspect` e logs
- Não há audit trail de quem acessou
- Rotação requer redeploy manual
- **Aceitável para staging, NÃO para produção com dados reais**

**P: E se o secrets manager cair?**
R: Implementar cache local + fallback:
```python
try:
    jwt_secret = get_secret("jwt-secret")
except Exception:
    logger.warning("secrets_manager_unavailable, usando cache local")
    jwt_secret = _cached_jwt_secret  # TTL 1h
```

**P: Como testar localmente sem secrets manager?**
R: Use `ENVIRONMENT=development` no `.env` (valores padrão são aceitos).

---

## Conformidade

**LGPD Art. 46 § 1º**:
> "A adoção de medidas criptográficas, gerenciamento de chaves e mecanismos tecnológicos de segurança"

✅ **Atendido** com secrets manager + rotação + audit trail.

**PCI-DSS Requisito 3.6**:
> "Fully document and implement all key-management processes"

✅ **Atendido** com rotação 90 dias + logs CloudTrail.

---

## Referências

- AWS Secrets Manager: https://docs.aws.amazon.com/secretsmanager/
- Azure Key Vault: https://learn.microsoft.com/en-us/azure/key-vault/
- HashiCorp Vault: https://www.vaultproject.io/docs
- External Secrets Operator: https://external-secrets.io/
