# Gestao de Segredos

Segredos reais nunca devem ser commitados. Em staging e production, a API recusa inicializar se os segredos obrigatorios estiverem nos defaults de desenvolvimento.

## Segredos obrigatorios

- `JWT_SECRET`
- `PII_ENCRYPTION_KEY`
- `EPSILON_WEBHOOK_SECRET`
- `INTERNAL_WEBHOOK_SECRET`
- `DATABASE_URL`
- `REDIS_URL`
- `MINIO_SECRET_KEY`

`EXTERNAL_VALIDATION_PROVIDER=mock_identity` so e permitido em development/test. `DEPLOYMENT_MODE=onprem` exige `ONPREM_TENANT_ID` fora de development/test.

## Backends recomendados

AWS Secrets Manager:

- um secret JSON por ambiente ou nomes individuais por prefixo `betaml/<env>/...`;
- IAM com least privilege para a task/pod da API;
- rotacao com janela de dupla validacao para conectores externos.

Azure Key Vault:

- identidade gerenciada para a API;
- secrets nomeados como `betaml-jwt-secret`, `betaml-pii-encryption-key` e equivalentes;
- logs de acesso enviados para Log Analytics/SIEM.

## Rotacao

- `JWT_SECRET`: invalida todos os JWTs existentes. Planejar janela curta e comunicar relogin.
- `PII_ENCRYPTION_KEY`: impacto alto. CPFs e nomes cifrados deixam de decifrar se a chave anterior nao estiver disponivel para recriptografia controlada. Exige plano de re-encrypt, backup e teste de restore.
- Webhooks: usar janela de overlap quando o provedor permitir duas assinaturas.
- MinIO/S3: criar credencial nova, atualizar workloads, validar acesso e revogar antiga.

## Controles

- CI roda scanner de segredos e Bandit.
- `scripts/check_secret_hygiene.py` bloqueia defaults perigosos fora de exemplos permitidos.
- `tests/security/test_config_secrets.py` cobre rejeicao dos defaults em staging/production.
