# Security Remediation Plan — BetAML v2.1.0

**Data de Criação:** 2026-03-17
**Baseline:** Auditoria `docs/audit-2026-03-17.md`
**Target Go-Live:** 10 dias úteis (2 devs full-time)
**Responsável:** Security & DevOps Team

---

## RESUMO EXECUTIVO

Esta remediação corrige **5 blockers críticos** e **6 gaps médios** identificados na auditoria de segurança pré-produção.

**Escopo:**
- ✅ Secrets vault integration (AWS Secrets Manager)
- ✅ Refresh token rotation (sliding window 7d)
- ✅ Pre-commit hooks (gitleaks + PII detection)
- ✅ PII logging audit + linter rules
- ✅ Rate limiting por role (ADMIN/ANALYST/AUDITOR)
- ✅ Request-ID Kafka propagation (distributed tracing)
- ✅ Frontend RBAC via Context API (remover localStorage)
- ✅ ClickHouse backfill APScheduler job
- ✅ Data Quality alerting (Great Expectations)
- ✅ A/B testing traffic split (ScoringConfig)
- ✅ Testes E2E Stream Processor + ML Service

**Prazo:** 10 dias úteis | **Effort:** 80 horas-pessoa

---

## FASE 1: BLOCKERS CRÍTICOS (Dias 1-5)

### 🔴 TASK 1: Secrets Vault Integration
**Severidade:** CRÍTICO
**Effort:** 16h (2 dias)
**Owner:** Backend Team + DevOps

#### Objetivos:
1. Migrar todos os secrets de `.env` para AWS Secrets Manager
2. Atualizar `config.py` para fetch dinâmico via boto3
3. CI/CD usar GitHub Secrets (injeção de ARN)
4. Remover `.env` do git history

#### Implementação:

**Step 1.1: Criar secrets no AWS Secrets Manager**
```bash
# Criar secret para cada ambiente (dev/staging/prod)
aws secretsmanager create-secret \
  --name betaml/prod/jwt-secret \
  --secret-string "$(openssl rand -base64 32)"

aws secretsmanager create-secret \
  --name betaml/prod/pii-encryption-key \
  --secret-string "$(openssl rand -base64 32)"

aws secretsmanager create-secret \
  --name betaml/prod/database-credentials \
  --secret-string '{"username":"betaml","password":"<generated>"}'

# Repetir para staging e dev
```

**Step 1.2: Atualizar config.py**
```python
# services/api/config.py
import boto3
from botocore.exceptions import ClientError

def get_secret(secret_name: str, region: str = "us-east-1") -> dict:
    """Fetch secret from AWS Secrets Manager."""
    session = boto3.session.Session()
    client = session.client(service_name='secretsmanager', region_name=region)

    try:
        response = client.get_secret_value(SecretId=secret_name)
        return json.loads(response['SecretString'])
    except ClientError as e:
        if settings.environment == "development":
            logger.warning(f"Secrets Manager unavailable: {e}. Using .env fallback.")
            return {}
        raise RuntimeError(f"CRITICAL: Cannot fetch secret {secret_name}") from e

class Settings(BaseSettings):
    environment: str = Field("development")
    aws_region: str = Field("us-east-1")
    secrets_prefix: str = F

ield("betaml")

    @property
    def jwt_secret(self) -> str:
        if self.environment == "development":
            return os.getenv("JWT_SECRET", "dev-secret-key-min-32-chars")

        secret = get_secret(f"{self.secrets_prefix}/{self.environment}/jwt-secret", self.aws_region)
        return secret.get("value") or self._raise_missing("JWT_SECRET")

    @property
    def pii_encryption_key(self) -> str:
        if self.environment == "development":
            return os.getenv("PII_ENCRYPTION_KEY", "dev-pii-key-32-bytes-base64")

        secret = get_secret(f"{self.secrets_prefix}/{self.environment}/pii-encryption-key", self.aws_region)
        return secret.get("value") or self._raise_missing("PII_ENCRYPTION_KEY")

    def _raise_missing(self, key: str):
        raise RuntimeError(f"CRITICAL: Secret {key} not found in Secrets Manager for env={self.environment}")
```

**Step 1.3: CI/CD GitHub Actions**
```yaml
# .github/workflows/ci.yml
env:
  AWS_REGION: us-east-1
  AWS_SECRETS_PREFIX: betaml

jobs:
  backend-tests:
    runs-on: ubuntu-latest
    permissions:
      id-token: write  # OIDC
      contents: read

    steps:
      - name: Configure AWS credentials
        uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ secrets.AWS_ROLE_ARN }}
          aws-region: ${{ env.AWS_REGION }}

      - name: Fetch secrets
        run: |
          JWT_SECRET=$(aws secretsmanager get-secret-value \
            --secret-id betaml/test/jwt-secret \
            --query SecretString --output text | jq -r .value)
          echo "::add-mask::$JWT_SECRET"
          echo "JWT_SECRET=$JWT_SECRET" >> $GITHUB_ENV
```

**Step 1.4: Remover .env do git**
```bash
# CUIDADO: Reescreve history do git (coordenar com time)
git filter-repo --path .env --invert-paths

# Adicionar ao .gitignore (já existe)
echo ".env" >> .gitignore

# Criar .env.example atualizado
cp .env.example .env.local
```

**Step 1.5: Pre-commit hook (gitleaks)**
```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/gitleaks/gitleaks
    rev: v8.18.0
    hooks:
      - id: gitleaks
        name: Detect secrets in code
        entry: gitleaks protect --verbose --redact --staged
```

#### Testes:
```bash
# 1. Validar fetch de secrets (staging)
ENVIRONMENT=staging python -c "from services.api.config import settings; print(settings.jwt_secret[:8])"

# 2. CI deve passar com secrets do Secrets Manager
git push origin feature/secrets-vault

# 3. Pre-commit deve bloquear commit com JWT_SECRET="plaintext"
echo 'JWT_SECRET="my-secret"' >> test.py
git add test.py
git commit -m "test"  # Deve falhar
```

#### Documentação:
- Atualizar `docs/ops-guide.md` Section 14 (key rotation)
- Criar `docs/security-secrets-management.md` (já existe, atualizar)

---

### 🔴 TASK 2: Refresh Token Rotation
**Severidade:** CRÍTICO
**Effort:** 8h (1 dia)
**Owner:** Backend Team

#### Objetivos:
1. Implementar refresh token cookie (httpOnly, 7d sliding window)
2. Schema migration: adicionar `users.refresh_token_jti` column
3. POST /auth/refresh endpoint (valida refresh token, invalida anterior, retorna novo access token)
4. Logout revoga ambos tokens (access + refresh)

#### Implementação:

**Step 2.1: Migration SQL**
```sql
-- infra/migration_v14.sql
-- Add refresh_token_jti column to users table

ALTER TABLE users
ADD COLUMN refresh_token_jti TEXT;

CREATE INDEX idx_users_refresh_token_jti ON users(refresh_token_jti);

COMMENT ON COLUMN users.refresh_token_jti IS 'JTI do refresh token ativo (rotacionado a cada refresh)';
```

**Step 2.2: Auth utils refresh token**
```python
# services/api/routers/auth.py

from datetime import timedelta

REFRESH_TOKEN_EXPIRE_DAYS = 7  # Sliding window

def create_refresh_token(user_id: str, tenant_id: str) -> tuple[str, str]:
    """Create refresh token with 7d expiration.

    Returns:
        tuple[str, str]: (token, jti)
    """
    jti = str(uuid.uuid4())
    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "jti": jti,
        "type": "refresh",
        "exp": datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm="HS256")
    return token, jti

async def store_refresh_token_jti(db: AsyncSession, user_id: str, jti: str):
    """Store refresh token JTI in users table (invalidates previous)."""
    await db.execute(
        update(User).where(User.id == user_id).values(refresh_token_jti=jti)
    )
    await db.commit()
```

**Step 2.3: POST /auth/login (atualizar)**
```python
# services/api/routers/auth.py:login

@router.post("/login", response_model=LoginResponse)
@limiter.limit("10/minute")
async def login(
    credentials: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db)
):
    # ... validação existente ...

    # Create access token (15min)
    access_token = create_access_token({"sub": user.id, "tenant_id": user.tenant_id})

    # Create refresh token (7d sliding)
    refresh_token, refresh_jti = create_refresh_token(user.id, user.tenant_id)

    # Store refresh JTI in users table
    await store_refresh_token_jti(db, user.id, refresh_jti)

    # Set httpOnly cookies
    response.set_cookie(
        key="betaml_token",
        value=access_token,
        httponly=True,
        secure=settings.environment != "development",
        samesite="lax",
        max_age=900  # 15min in seconds
    )

    response.set_cookie(
        key="betaml_refresh_token",
        value=refresh_token,
        httponly=True,
        secure=settings.environment != "development",
        samesite="lax",
        max_age=7 * 24 * 3600  # 7 days
    )

    # Audit log
    await write_audit(db, user.id, user.tenant_id, "LOGIN", ...)

    return LoginResponse(
        access_token=access_token,
        refresh_token=refresh_token,  # Também retorna no body para mobile apps
        token_type="bearer",
        user=UserOut.model_validate(user)
    )
```

**Step 2.4: POST /auth/refresh (novo endpoint)**
```python
# services/api/routers/auth.py

@router.post("/refresh", response_model=RefreshResponse)
@limiter.limit("20/minute")
async def refresh_token(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db)
):
    """Refresh access token using refresh token.

    - Validates refresh token from cookie
    - Invalidates previous refresh token (rotation)
    - Returns new access token (15min) + new refresh token (7d sliding)
    """
    refresh_token = request.cookies.get("betaml_refresh_token")
    if not refresh_token:
        raise HTTPException(401, detail="Refresh token missing")

    try:
        payload = jwt.decode(refresh_token, settings.jwt_secret, algorithms=["HS256"])

        # Validate token type
        if payload.get("type") != "refresh":
            raise HTTPException(401, detail="Invalid token type")

        user_id = payload.get("sub")
        jti = payload.get("jti")

        # Check if refresh token JTI matches stored in users table
        result = await db.execute(
            select(User).where(User.id == user_id, User.refresh_token_jti == jti)
        )
        user = result.scalar_one_or_none()

        if not user:
            raise HTTPException(401, detail="Refresh token revoked or invalid")

        if not user.active:
            raise HTTPException(401, detail="User inactive")

        # Create new tokens (rotation)
        new_access_token = create_access_token({"sub": user.id, "tenant_id": user.tenant_id})
        new_refresh_token, new_refresh_jti = create_refresh_token(user.id, user.tenant_id)

        # Store new refresh JTI (invalidates old one)
        await store_refresh_token_jti(db, user.id, new_refresh_jti)

        # Set cookies
        response.set_cookie(
            key="betaml_token",
            value=new_access_token,
            httponly=True,
            secure=settings.environment != "development",
            samesite="lax",
            max_age=900
        )

        response.set_cookie(
            key="betaml_refresh_token",
            value=new_refresh_token,
            httponly=True,
            secure=settings.environment != "development",
            samesite="lax",
            max_age=7 * 24 * 3600
        )

        # Audit log
        await write_audit(db, user.id, user.tenant_id, "REFRESH_TOKEN", "auth", user.id, {}, {})

        return RefreshResponse(
            access_token=new_access_token,
            refresh_token=new_refresh_token,
            token_type="bearer"
        )

    except jwt.ExpiredSignatureError:
        raise HTTPException(401, detail="Refresh token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(401, detail="Invalid refresh token")
```

**Step 2.5: POST /auth/logout (atualizar)**
```python
# services/api/routers/auth.py:logout

@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    current_user: dict = Depends(get_current_user),
    redis: Redis = Depends(get_redis),
    db: AsyncSession = Depends(get_db)
):
    """Logout: revoke both access and refresh tokens."""

    # Revoke access token (BlackList Redis)
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    jti = current_user.get("jti")
    exp = current_user.get("exp")
    ttl = max(0, exp - int(datetime.now(timezone.utc).timestamp()))

    await redis.setex(f"blacklist:{jti}", ttl, "1")

    # Revoke refresh token (nullify JTI in users table)
    await db.execute(
        update(User).where(User.id == current_user["sub"]).values(refresh_token_jti=None)
    )
    await db.commit()

    # Clear cookies
    response.delete_cookie(key="betaml_token")
    response.delete_cookie(key="betaml_refresh_token")

    # Audit log
    await write_audit(db, current_user["sub"], current_user["tenant_id"], "LOGOUT", ...)

    return {"message": "Logged out"}
```

#### Testes:
```python
# tests/unit/test_auth_refresh_token.py

@pytest.mark.asyncio
async def test_refresh_token_rotation():
    """Test refresh token rotation (invalidates old token)."""
    # Login
    response = await client.post("/auth/login", json={...})
    refresh_token_1 = response.cookies.get("betaml_refresh_token")

    # Refresh
    response2 = await client.post("/auth/refresh", cookies={"betaml_refresh_token": refresh_token_1})
    assert response2.status_code == 200
    refresh_token_2 = response2.cookies.get("betaml_refresh_token")

    # Old refresh token should be invalid
    response3 = await client.post("/auth/refresh", cookies={"betaml_refresh_token": refresh_token_1})
    assert response3.status_code == 401
    assert "revoked" in response3.json()["detail"]

@pytest.mark.asyncio
async def test_logout_revokes_refresh_token():
    """Test logout revokes refresh token."""
    # Login
    response = await client.post("/auth/login", json={...})
    access_token = response.cookies.get("betaml_token")
    refresh_token = response.cookies.get("betaml_refresh_token")

    # Logout
    await client.post("/auth/logout", headers={"Authorization": f"Bearer {access_token}"})

    # Refresh token invalid
    response2 = await client.post("/auth/refresh", cookies={"betaml_refresh_token": refresh_token})
    assert response2.status_code == 401
```

#### Documentação:
- Atualizar `README.md` Section 4 (authentication flow)
- Documentar em `docs/ops-guide.md` Section 8 (token rotation policy)

---

### 🔴 TASK 3: Pre-Commit Hooks (Gitleaks + PII)
**Severidade:** CRÍTICO
**Effort:** 4h (0.5 dia)
**Owner:** DevOps Team

#### Objetivos:
1. Adicionar pre-commit hook com gitleaks (detect secrets)
2. Adicionar custom hook para detectar PII logging patterns
3. Configurar CI para rodar hooks em PRs

#### Implementação:

**Step 3.1: Install pre-commit**
```bash
pip install pre-commit
```

**Step 3.2: Criar .pre-commit-config.yaml**
```yaml
# .pre-commit-config.yaml

repos:
  # Gitleaks: detect secrets
  - repo: https://github.com/gitleaks/gitleaks
    rev: v8.18.2
    hooks:
      - id: gitleaks
        name: Detect hardcoded secrets
        entry: gitleaks protect --verbose --redact --staged
        language: system
        pass_filenames: false

  # Detect plaintext PII in logs
  - repo: local
    hooks:
      - id: detect-pii-logging
        name: Detect PII in logger calls
        entry: python scripts/detect_pii_logging.py
        language: python
        files: \.py$
        pass_filenames: true

  # Ruff linter
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.3.4
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format

  # Bandit security checks
  - repo: https://github.com/PyCQA/bandit
    rev: 1.7.8
    hooks:
      - id: bandit
        args: [-c, pyproject.toml, -r, services/api/, libs/]
        pass_filenames: false
```

**Step 3.3: Script detect_pii_logging.py**
```python
# scripts/detect_pii_logging.py
#!/usr/bin/env python3
"""Pre-commit hook to detect PII fields in logger calls."""

import re
import sys

PII_FIELDS = [
    r"cpf_encrypted",
    r"cpf",
    r"password",
    r"password_hash",
    r"pii_encryption_key",
    r"jwt_secret",
]

# Patterns to detect: logger.info(f"CPF: {cpf}")
PATTERNS = [
    re.compile(rf"logger\.(info|debug|warning|error).*\b{field}\b", re.IGNORECASE)
    for field in PII_FIELDS
]

def check_file(filepath: str) -> bool:
    """Check if file contains PII logging.

    Returns:
        bool: True if safe, False if violations found
    """
    with open(filepath, "r") as f:
        content = f.read()
        for i, line in enumerate(content.splitlines(), start=1):
            for pattern in PATTERNS:
                if pattern.search(line):
                    print(f"❌ PII LOGGING DETECTED in {filepath}:{i}")
                    print(f"   Line: {line.strip()}")
                    return False
    return True

if __name__ == "__main__":
    files = sys.argv[1:]
    violations = []

    for filepath in files:
        if filepath.endswith(".py"):
            if not check_file(filepath):
                violations.append(filepath)

    if violations:
        print(f"\n🔴 {len(violations)} file(s) with PII logging violations.")
        print("   Use mask_cpf() or exclude PII fields from logger calls.")
        sys.exit(1)

    print("✅ No PII logging violations detected.")
    sys.exit(0)
```

**Step 3.4: Install hooks**
```bash
pre-commit install
pre-commit run --all-files  # Test on existing codebase
```

**Step 3.5: CI integration**
```yaml
# .github/workflows/ci.yml

jobs:
  pre-commit-checks:
    name: Pre-commit Hooks
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install pre-commit
        run: pip install pre-commit

      - name: Run pre-commit hooks
        run: pre-commit run --all-files --show-diff-on-failure
```

#### Testes:
```bash
# Test 1: Gitleaks deve detectar JWT_SECRET
echo 'JWT_SECRET="my-secret-123"' >> test_secret.py
git add test_secret.py
git commit -m "test"  # Deve falhar

# Test 2: PII logging hook deve detectar cpf
echo 'logger.info(f"CPF: {player.cpf}")' >> test_pii.py
git add test_pii.py
git commit -m "test"  # Deve falhar

# Test 3: Hook passa com mask_cpf
echo 'logger.info(f"CPF: {mask_cpf(player.cpf)}")' >> test_ok.py
git add test_ok.py
git commit -m "test"  # Deve passar
```

---

### 🔴 TASK 4: PII Logging Audit
**Severidade:** CRÍTICO
**Effort:** 4h (0.5 dia)
**Owner:** Backend Team

#### Objetivos:
1. Grep codebase para detectar logging de CPF/PII
2. Corrigir violações (usar mask_cpf())
3. Adicionar ruff custom rule para bloquear futuros

#### Implementação:

**Step 4.1: Audit script**
```bash
# Buscar logging suspeito de CPF
grep -rn "logger\." services/api/ | grep -iE "(cpf|password|secret)" > pii_audit.txt

# Analisar manualmente cada linha
# Validar se está usando mask_cpf() ou se é plaintext
```

**Step 4.2: Fix violations**
```python
# ANTES (VIOLAÇÃO):
logger.info(f"Player {player.id} has CPF {player.cpf_encrypted}")

# DEPOIS (CORRETO):
logger.info(f"Player {player.id} registered", extra={
    "player_id": player.id,
    "cpf_masked": mask_cpf(decrypt_cpf(player.cpf_encrypted))
})
```

**Step 4.3: Ruff custom rule (pyproject.toml)**
```toml
# pyproject.toml

[tool.ruff.lint]
select = ["E", "F", "I", "N", "W", "PL"]
ignore = []

[tool.ruff.lint.flake8-logging-format]
# Custom: warn on logging PII fields
kwargs = ["cpf", "password", "secret", "token"]
```

#### Testes:
```bash
# Rodar ruff em codebase
ruff check services/api/ libs/

# Validar 0 violations
```

---

### 🔴 TASK 5: Rate Limiting por Role
**Severidade:** CRÍTICO
**Effort:** 4h (0.5 dia)
**Owner:** Backend Team

#### Objetivos:
1. Configurar key_func por role + IP
2. Limites: ADMIN 100/min, ANALYST 50/min, AUDITOR 20/min
3. Documentar em ops-guide.md

#### Implementação:

**Step 5.1: Key function**
```python
# services/api/routers/auth.py

def rate_limit_key_by_role(request: Request) -> str:
    """Rate limit key based on role + IP.

    - ADMIN/SUPER_ADMIN: 100 req/min
    - AML_ANALYST: 50 req/min
    - AUDITOR: 20 req/min
    - Anonymous: 10 req/min
    """
    # Try to get user role from request state (set by get_current_user)
    role = getattr(request.state, "user_role", None)
    client_ip = request.client.host if request.client else "unknown"

    return f"{role or 'anonymous'}:{client_ip}"

def get_rate_limit_by_role(request: Request) -> str:
    """Dynamic rate limit based on role."""
    role = getattr(request.state, "user_role", None)

    limits = {
        "ADMIN": "100/minute",
        "SUPER_ADMIN": "100/minute",
        "AML_ANALYST": "50/minute",
        "AUDITOR": "20/minute",
    }

    return limits.get(role, "10/minute")  # Default anonymous

# Atualizar decorator
@router.post("/login", response_model=LoginResponse)
@limiter.limit(get_rate_limit_by_role, key_func=rate_limit_key_by_role)
async def login(...):
    ...
```

**Step 5.2: Middleware para setar role**
```python
# services/api/main.py

@app.middleware("http")
async def set_user_role_middleware(request: Request, call_next):
    """Extract user role from JWT and set in request.state."""

    token = request.headers.get("Authorization", "").replace("Bearer ", "")

    if token:
        try:
            payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
            # Fetch user role from DB (cache 5min)
            user_id = payload.get("sub")

            # TODO: Add cache layer (Redis) for role lookup
            # For now, decode from JWT custom claim
            request.state.user_role = payload.get("role")  # Assumindo role no JWT
        except Exception:
            request.state.user_role = None
    else:
        request.state.user_role = None

    return await call_next(request)
```

**Step 5.3: Adicionar role ao JWT payload**
```python
# services/api/routers/auth.py:create_access_token

def create_access_token(payload: dict) -> str:
    """Create JWT access token with role."""
    jti = str(uuid.uuid4())

    token_payload = {
        **payload,
        "jti": jti,
        "role": payload.get("role"),  # Adicionar role
        "exp": datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_min)
    }

    return jwt.encode(token_payload, settings.jwt_secret, algorithm="HS256")
```

#### Testes:
```python
# tests/unit/test_rate_limiting.py

@pytest.mark.asyncio
async def test_rate_limit_admin_100_per_minute():
    """ADMIN should have 1

00 req/min limit."""
    # Login as ADMIN
    token = await get_admin_token()

    # Make 100 requests (should pass)
    for i in range(100):
        response = await client.get("/alerts", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200

    # 101st request should fail
    response = await client.get("/alerts", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 429

@pytest.mark.asyncio
async def test_rate_limit_auditor_20_per_minute():
    """AUDITOR should have 20 req/min limit."""
    token = await get_auditor_token()

    # Make 20 requests
    for i in range(20):
        response = await client.get("/audit-logs", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200

    # 21st request should fail
    response = await client.get("/audit-logs", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 429
```

---

## FASE 2: GAPS MÉDIOS (Dias 6-10)

*(Documentação completa das tasks 6-14 omitida por brevidade, seguindo mesmo padrão)*

### Task 6: Request-ID Kafka Propagation
- Inject X-Request-ID em headers de todas as mensagens Kafka
- Stream Processor/Rules Engine extraem e logam request_id
- Grafana Loki queries por trace_id

### Task 7: Frontend RBAC via Context API
- Remover localStorage.getItem("betaml_user")
- Criar UserContext com fetch de /me
- Sidebar/components consomem useUser() hook

### Task 8: ClickHouse Backfill Job
- APScheduler job 03:30 UTC daily
- Query Postgres FeatureSnapshot últimas 24h
- Bulk insert ClickHouse (idempotente por snapshot_date)

### Task 9: Data Quality Alerting
- Great Expectations suite (null CPF, duplicate external_id, etc.)
- DQ runner cria Notification se failure
- Auto-pause tenant ingest se critical

### Task 10: A/B Testing Traffic Split
- ScoringConfig.ml_challenger_pct (0-100)
- ML Service /score lê config e faz split determinístico por player (bucket stable) para evitar flapping
- ModelInference log table para analytics

### Task 11-14: Testes E2E
- Stream Processor: pytest-docker + Redpanda container
- ML Service: test /score, /score/shap, /train endpoints
- Frontend: Jest + React Testing Library (opcional)

---

## VALIDAÇÃO & SIGN-OFF

### Checklist Final (Todas as tasks completas):

- [ ] Task 1: Secrets vault integration (AWS Secrets Manager)
- [ ] Task 2: Refresh token rotation (7d sliding)
- [ ] Task 3: Pre-commit hooks (gitleaks + PII)
- [ ] Task 4: PII logging audit (0 violations)
- [ ] Task 5: Rate limiting por role
- [ ] Task 6: Request-ID Kafka propagation
- [ ] Task 7: Frontend RBAC via Context API
- [ ] Task 8: ClickHouse backfill job
- [ ] Task 9: Data Quality alerting
- [ ] Task 10: A/B testing traffic split
- [ ] Task 11: Stream Processor E2E tests
- [ ] Task 12: ML Service inference tests
- [ ] Task 13: Load testing 10k TPS
- [ ] Task 14: Backup automatizado

### Critérios de Aceitação:

1. ✅ Todos os 511+ testes passando
2. ✅ CI/CD pipeline GREEN (backend-tests, lint, typecheck, security, pre-commit)
3. ✅ Secrets vault funcionando em staging
4. ✅ Refresh token rotation validado (tokens antigos invalidados)
5. ✅ Pre-commit hook bloqueia secrets + PII logging
6. ✅ Rate limiting por role aplicado (ADMIN 100/min, ANALYST 50/min, AUDITOR 20/min)
7. ✅ Load testing sustenta 10k TPS por 5min (Locust)
8. ✅ Backup automatizado PostgreSQL + MinIO (diário)

### Sign-Off:

```
[ ] Security Team Lead: _____________________  Data: __________
[ ] Backend Team Lead:  _____________________  Data: __________
[ ] DevOps Team Lead:   _____________________  Data: __________
[ ] CTO:                _____________________  Data: __________
```

---

**Target Go-Live:** 10 dias úteis após sign-off desta remediação.

**Contato:** security@betaml.com.br | DevOps: devops@betaml.com.br
