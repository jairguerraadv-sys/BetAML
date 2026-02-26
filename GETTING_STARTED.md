# 🎯 BetAML - Instruções Finais de Setup

## 📥 Arquivos Gerados (Recapitulação)

Foram criados **20 arquivos** de documentação, código e configuração:

### 📄 Documentação (5 arquivos)
1. **README.md** - Overview, quick start, credenciais
2. **EXECUTIVE_SUMMARY.md** - Resumo executivo, produto, arquitetura
3. **docs/ARCHITECTURE.md** - Diagramas, fluxos, componentes
4. **docs/DSL_GUIDE.md** - Linguagem de regras, 12 exemplos
5. **docs/DEPLOYMENT.md** - Deploy local e production

### 📦 Código-Fonte (14 arquivos)
6. **libs/schemas.py** - Canonical event envelope
7. **libs/dsl_parser.py** - DSL tokenizer, parser, evaluator
8. **services/api/main.py** - FastAPI with auth, CRUD
9. **services/api/requirements.txt** - Python dependencies
10. **services/api/Dockerfile** - Container image
11. **services/stream_processor/main.py** - Feature computation
12. **services/stream_processor/requirements.txt**
13. **services/rules_engine/main.py** - DSL rules evaluation
14. **services/rules_engine/requirements.txt**
15. **services/ml_service/main.py** - ML scoring
16. **services/ml_service/requirements.txt**
17. **infra/docker-compose.yml** - Orquestração completa
18. **infra/init-db.sql** - PostgreSQL schema
19. **infra/clickhouse-init.sql** - ClickHouse tables

### 📑 Índice e Referência (1 arquivo)
20. **FILE_INDEX.md** - Índice completo de arquivos

---

## 🚀 Como Começar (3 passos)

### Passo 1: Estruturar Repositório Local

```bash
# Criar diretório raiz
mkdir -p ~/projects/betaml
cd ~/projects/betaml

# Criar estrutura de diretórios
mkdir -p {libs,services/{api,stream_processor,rules_engine,ml_service,frontend},infra/{configs},tests/{unit,integration,fixtures},docs}

# Copiar arquivos de código para suas localizações
# (Você receberá os arquivos em seus paths originais)

tree -L 3 .
```

Resultado esperado:
```
betaml/
├── README.md
├── EXECUTIVE_SUMMARY.md
├── FILE_INDEX.md
├── libs/
│   ├── __init__.py
│   ├── schemas.py
│   └── dsl_parser.py
├── services/
│   ├── api/
│   │   ├── main.py
│   │   ├── requirements.txt
│   │   └── Dockerfile
│   ├── stream_processor/
│   │   ├── main.py
│   │   ├── requirements.txt
│   │   └── Dockerfile (criar: FROM python:3.11-slim + pip install)
│   ├── rules_engine/
│   │   ├── main.py
│   │   ├── requirements.txt
│   │   └── Dockerfile (criar similar)
│   └── ml_service/
│       ├── main.py
│       ├── requirements.txt
│       └── Dockerfile (criar similar)
├── infra/
│   ├── docker-compose.yml
│   ├── init-db.sql
│   ├── clickhouse-init.sql
│   └── configs/
├── docs/
│   ├── ARCHITECTURE.md
│   ├── DSL_GUIDE.md
│   └── DEPLOYMENT.md
└── tests/
    ├── conftest.py
    ├── unit/
    └── integration/
```

### Passo 2: Verificar Pré-requisitos

```bash
# Versões necessárias
docker --version          # >= 20.10
docker-compose --version  # >= 1.29
python --version          # >= 3.11
node --version            # >= 18 (para frontend)
git --version             # >= 2.30

# Portas disponíveis (testar uma por uma)
netstat -an | grep -E ":(5432|9092|6379|9000|8000|3000)" && echo "Portas ocupadas!" || echo "✓ Portas livres"
```

### Passo 3: Rodar Locally

```bash
# 1. Entrar no diretório
cd ~/projects/betaml

# 2. Iniciar Docker Compose
docker-compose -f infra/docker-compose.yml up -d

# 3. Verificar status (aguarde ~30s)
docker-compose -f infra/docker-compose.yml ps

# 4. Health checks
curl http://localhost:8000/health
curl http://localhost:9001 (MinIO UI)
curl http://localhost:8080 (Kafka UI)

# 5. Acessar
open http://localhost:3000       # Frontend (em breve)
open http://localhost:8000/docs  # API Docs
```

---

## 📋 Checklist de Validação

Após 30 segundos de containers UP, verifique:

- [ ] **API está responsiva**
  ```bash
  curl http://localhost:8000/health
  # Esperado: {"status":"ok","timestamp":"..."}
  ```

- [ ] **PostgreSQL pronto**
  ```bash
  docker-compose -f infra/docker-compose.yml exec postgres \
    psql -U betaml -d betaml_dev -c "SELECT count(*) FROM tenants;"
  # Esperado: count: 2
  ```

- [ ] **Kafka com tópicos**
  ```bash
  docker-compose -f infra/docker-compose.yml exec kafka \
    kafka-topics --bootstrap-server localhost:29092 --list
  # Esperado: raw.*, canonical.*, features.*, scoring.alerts, etc.
  ```

- [ ] **Redis conectando**
  ```bash
  docker-compose -f infra/docker-compose.yml exec redis \
    redis-cli ping
  # Esperado: PONG
  ```

- [ ] **MinIO bucket criado**
  ```bash
  # Acesse http://localhost:9001
  # Login: admin / minio123
  # Verifique bucket "betaml-lakehouse"
  ```

- [ ] **ClickHouse tabelas**
  ```bash
  docker-compose -f infra/docker-compose.yml exec clickhouse \
    clickhouse-client -e "SHOW TABLES"
  # Esperado: canonical_events, scoring_alerts, etc.
  ```

---

## 🔐 Login & Teste Rápido

### Credenciais Default (DEV ONLY)
```
Tenant: OperadorA
Username: admin_a
Password: admin123
```

### API Test
```bash
# 1. Login
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin_a","password":"admin123"}' \
  | jq -r .access_token)

# 2. Get current user
curl -s http://localhost:8000/me \
  -H "Authorization: Bearer $TOKEN" | jq .

# 3. List alerts (vazio no início)
curl -s http://localhost:8000/alerts \
  -H "Authorization: Bearer $TOKEN" | jq .

# 4. Create test event
curl -s -X POST http://localhost:8000/ingest/event \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "sourceSystem": "BackofficeAlpha",
    "entityType": "TRANSACTION",
    "payload": {
      "playerId": "test-player-1",
      "amount": 10000,
      "type": "DEPOSIT",
      "method": "PIX"
    }
  }' | jq .
```

---

## 🐛 Troubleshooting Rápido

### Container não inicia
```bash
# Ver logs detalhados
docker-compose -f infra/docker-compose.yml logs -f api

# Reiniciar serviço
docker-compose -f infra/docker-compose.yml restart api

# Resetar tudo
docker-compose -f infra/docker-compose.yml down -v
docker-compose -f infra/docker-compose.yml up -d
```

### Porta já em uso
```bash
# Encontrar o processo
lsof -i :8000

# Matar
kill -9 <PID>

# Ou usar porta diferente em docker-compose.yml:
# ports:
#   - "8001:8000"  (mude 8001 para outra)
```

### Memória insuficiente
```bash
# Aumentar alocação Docker
# macOS: Docker Desktop → Preferences → Resources → Memory
# Linux: docker system prune -a
```

---

## 📚 O Que Ler Primeiro

### Para Product/Business
1. **EXECUTIVE_SUMMARY.md** (10 min)
2. **README.md** - seção "Arquitetura" (15 min)

### Para Arquiteto/Lead Tech
1. **docs/ARCHITECTURE.md** (30 min)
2. **README.md** - completo (20 min)
3. **FILE_INDEX.md** - estrutura (10 min)

### Para Developer Backend
1. **docs/DEPLOYMENT.md** - Quick Start (5 min)
2. **services/api/main.py** - ler código (30 min)
3. **libs/dsl_parser.py** - ler código (20 min)

### Para Data Scientist
1. **docs/DSL_GUIDE.md** (20 min)
2. **services/ml_service/main.py** (30 min)
3. **services/stream_processor/main.py** (30 min)

### Para DevOps
1. **infra/docker-compose.yml** (20 min)
2. **docs/DEPLOYMENT.md** - Production section (30 min)

---

## ✅ Próximas Ações

### Curto Prazo (Esta semana)
- [ ] Clonar/copiar arquivos para repo
- [ ] Rodar `docker-compose up -d` local
- [ ] Validar health checks
- [ ] Testar login + API básica
- [ ] Review ARCHITECTURE.md com time

### Médio Prazo (Próximas 2 semanas)
- [ ] Completar frontend (Next.js pages)
- [ ] Expandir endpoints API (upload arquivo, case management)
- [ ] Implementar testes (unit + integração)
- [ ] Review DSL com product (regras finais)

### Longo Prazo (Mês 2)
- [ ] Deploy staging (AWS/GCP)
- [ ] Integração com sistemas externos (APIs)
- [ ] Monitoramento (Prometheus + Grafana)
- [ ] Testes de carga e performance
- [ ] Security review + penetration testing
- [ ] Go-live production

---

## 🎓 Recursos Adicionais

### Dentro do Repositório
- `docs/ARCHITECTURE.md` - Design completo
- `docs/DSL_GUIDE.md` - 12 regras exemplares
- `docs/DEPLOYMENT.md` - Deploy guide
- `README.md` - Troubleshooting

### Recomendações Externas
- Kafka: https://kafka.apache.org/quickstart
- ClickHouse: https://clickhouse.com/docs
- FastAPI: https://fastapi.tiangolo.com/
- Redis: https://redis.io/docs/
- PostgreSQL: https://www.postgresql.org/docs/

---

## 🆘 Suporte

Se alguma coisa não funcionar:

1. **Verifique logs**: `docker-compose -f infra/docker-compose.yml logs <service>`
2. **Consulte README.md**: seção Troubleshooting
3. **Releia DEPLOYMENT.md**: diagnóstico detalhado
4. **Stack Overflow**: procure erro exato
5. **GitHub Issues**: reporte com:
   - Docker version
   - OS (macOS/Linux/Windows)
   - Erro completo (logs)
   - Passos para reproduzir

---

## 🎉 Sucesso!

Se você chegou aqui e tudo está rodando: **Parabéns!**

Você tem uma plataforma completa de PLD/FT pronta para:
- ✅ Desenvolvimento local
- ✅ Validação de negócio
- ✅ Demo para stakeholders
- ✅ Base sólida para expansão

**Próximo passo**: Abra http://localhost:3000 (quando frontend estiver pronto) e comece a explorar!

---

**BetAML v1.0.0-MVP**  
**Data:** 26/02/2024  
**Status:** ✅ Pronto para começar

---

## 📞 Contato

- **Equipe:** Arquitetura de Software + Full-Stack Engineering
- **Email:** support@betaml.io (placeholder)
- **Docs:** Este repositório `/docs`
- **Issues:** GitHub Issues

**Bom desenvolvimento! 🚀**
