# Key Rotation Runbook

## Scope

Operational runbook for rotating BetAML secrets and keys without reducing security controls.

## Covered Secrets

- JWT secret (`JWT_SECRET`)
- PII encryption key (`PII_ENCRYPTION_KEY`)
- Internal webhooks (`INTERNAL_WEBHOOK_SECRET`, `EPSILON_WEBHOOK_SECRET`)
- Infrastructure credentials (`DATABASE_URL`, `REDIS_URL`, `MINIO_SECRET_KEY`)
- ML internal auth (`ML_INTERNAL_API_KEY`)
- Tenant API keys (`btml_<tenant_uuid_hex32>_<secret>`)

## Rotation Cadence (recommended)

- JWT secret: every 90 days or incident-driven
- Webhook secrets: every 90 days or integration change
- API keys/internal service keys: every 90 days
- DB/Redis/MinIO passwords: every 180 days (or provider policy)
- PII encryption key: only with approved re-encryption plan

## Pre-rotation Checklist

1. Confirm latest backup and tested restore path.
2. Confirm no open incident in auth/crypto path.
3. Validate target secret values generated securely.
4. Prepare rollback steps and owners.
5. Announce maintenance window if required.

## Execution Steps

1. Write new value to secret manager (AWS/Azure/K8s external secret).
2. Roll workload pods/services that read that secret.
3. Validate health endpoints and authentication flows.
4. Validate alerting, case creation and webhook signature verification.
5. Invalidate obsolete tokens/keys when required.

## Special Case: JWT_SECRET

Rotating `JWT_SECRET` invalidates existing access and refresh tokens. Plan user relogin, clear `users.refresh_token_jti` for affected users if compromise is suspected, and monitor login failures after rollout.

## Special Case: PII_ENCRYPTION_KEY

1. Backup database.
2. Run staged re-encryption job (old key decrypt -> new key encrypt).
3. Validate decrypted reads for sampled tenants.
4. Only then revoke old key.
5. Keep rollback window active until post-rotation checks pass.

Without the previous key, encrypted PII cannot be recovered. Treat emergency rotation as a data availability incident as well as a security response.

## API Keys

1. Create a new API key v2.
2. Update the connector/operator system.
3. Observe successful use of the new key.
4. Revoke the old key and confirm it returns 401.

Never send raw API keys through plaintext channels.

## Webhook Secrets

When a provider supports overlap, accept old and new signatures for a short window. End the overlap, then audit rejected calls for replay or stale integrations.

## Post-rotation Validation

```bash
python scripts/check_secret_hygiene.py
TEST_STACK_UP=1 pytest tests/security -q
```

## Rollback

- Restore previous secret version in secret manager.
- Restart impacted services.
- If PII rotation failed, restore backup and previous key version.
- Open incident report with impact, timeline and corrective actions.
