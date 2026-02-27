# BetAML — Guia do Analista de PLD/FT

## 1. Introdução

O BetAML é uma plataforma de detecção de lavagem de dinheiro e financiamento do terrorismo (PLD/FT) para operadores de apostas esportivas e jogos online. Este guia cobre o uso diário da plataforma pelo analista de compliance.

---

## 2. Acesso e Login

1. Acesse `http://localhost:3000` (ou a URL do seu ambiente)
2. Use as credenciais fornecidas pelo administrador
3. Papéis disponíveis: `ANALYST`, `SUPERVISOR`, `AUDITOR`, `ADMIN`

---

## 3. Dashboard

O dashboard exibe:
- **Alertas em aberto**: contagem por severidade (CRITICAL / HIGH / MEDIUM / LOW)
- **Casos ativos**: casos em investigação
- **Volume de ingestão**: eventos processados nas últimas 24h
- **Top jogadores de risco**: maiores scores de risco

---

## 4. Fluxo de Trabalho — Alertas

### 4.1 Listagem e Filtros

Em **Alertas**, use os filtros:
- Status: `OPEN`, `IN_REVIEW`, `CLOSED`, `FALSE_POSITIVE`
- Severidade: `CRITICAL`, `HIGH`, `MEDIUM`, `LOW`
- Tipo: `RULE_BASED`, `ML_ANOMALY`, `COMPOUND`

### 4.2 Triagem

1. Clique em um alerta para abrir o painel de detalhes
2. Escolha **Disposição**: `FALSE_POSITIVE` | `ESCALATE` | `CLOSE`
3. Preencha a justificativa e confirme

### 4.3 Feedback para o Modelo (Labeling)

Use o botão **Etiquetar** (ícone de tag) para classificar:
- **TRUE_POSITIVE** — confirmado como suspeito real
- **FALSE_POSITIVE** — não é suspeito
- **NEED_REVIEW** — requer supervisão

O feedback alimenta o loop de retreinamento automático via tópico `feedback.labels`.

---

## 5. Fluxo de Trabalho — Casos

### 5.1 Criação Manual

1. Acesse **Casos → Novo Caso**
2. Associe um ou mais alertas
3. Atribua um responsável
4. Defina prazo (SLA)

### 5.2 Evidências

Faça upload de documentos em **Casos → [Caso] → Evidências**. Os arquivos são armazenados no MinIO com versionamento.

### 5.3 Relatório de Caso (PDF)

Clique em **Exportar PDF** na página do caso para baixar o pacote completo com:
- Resumo do caso
- Histórico de eventos
- Features do jogador no momento do alerta
- Trilha de auditoria

---

## 6. DSL de Regras

### 6.1 Sintaxe Básica

```
campo operador valor
```

**Operadores**: `>`, `<`, `>=`, `<=`, `==`, `!=`  
**Operadores lógicos**: `AND`, `OR`, `NOT`  
**Parênteses**: `(expr1) AND (expr2)`

### 6.2 Funções Disponíveis

| Função | Descrição | Exemplo |
|--------|-----------|---------|
| `abs(x)` | Valor absoluto | `abs(balance) > 1000` |
| `round(x, n)` | Arredondamento | `round(ratio, 2) > 0.5` |
| `min(a, b)` | Mínimo | `min(score_a, score_b) > 0.7` |
| `max(a, b)` | Máximo | `max(score_a, score_b) > 0.9` |
| `iff(cond, v_true, v_false)` | Condicional | `iff(is_vip, 0.5, 1.0)` |
| `is_in_list(player_id, 'NOME_LISTA')` | Pertence à lista | `is_in_list(player_id, 'BLOCKLIST')` |
| `window_sum(campo, horas)` | Soma janela temporal | `window_sum(amount, 24) > 10000` |
| `window_count(campo, horas)` | Contagem janela | `window_count(txn_id, 1) > 20` |
| `percentile_rank(campo, 'campo')` | Percentil (0-100) | `percentile_rank(amount, 'amount') > 90` |
| `cluster_size(cluster_id)` | Tamanho do cluster | `cluster_size(cluster_id) > 10` |
| `is_in_cluster(player_id)` | Pertence a cluster | `is_in_cluster(player_id) == true` |
| `shared_device_count(device_id)` | Contas no mesmo device | `shared_device_count(device_id) > 3` |

### 6.3 Macros

Defina macros em **Regras → Macros** para reutilizar expressões:

```
Nome: HIGH_RISK_SCORE
Expressão: risk_score > 0.8 AND txn_amount_24h > 5000
```

Use na regra com `%HIGH_RISK_SCORE%`:
```
%HIGH_RISK_SCORE% AND is_in_list(player_id, 'WATCHLIST')
```

### 6.4 Exemplos de Regras

```
# Depósitos em alta velocidade
deposit_velocity > 3 AND txn_amount_24h > 10000

# Estruturação (múltiplos depósitos abaixo do limite COAF)
window_count('DEPOSIT', 24) >= 5 AND window_sum('DEPOSIT', 24) > 40000

# Atividade noturna suspeita
night_activity_ratio > 0.8 AND txn_count_30d > 100

# Multi-moeda + alto volume
multi_currency_flag == true AND txn_amount_7d > 50000

# Jogador em lista de restrição
is_in_list(player_id, 'BLOCKLIST') == true

# Score de rede elevado
shared_instrument_score > 0.7 AND cluster_size(cluster_id) > 5
```

### 6.5 Regras Compostas

Regras compostas combinam múltiplas regras simples com pesos:

```
Regra Composta: "Perfil Alto Risco"
Componentes:
  - "Depósito Veloz"     peso: 2.0
  - "Multi-moeda"        peso: 1.5
  - "Atividade Noturna"  peso: 1.0
Score Mínimo: 3.0  →  Ação: BLOCK
```

---

## 7. Feature Store

Em **Feature Store**, consulte o perfil de features de qualquer jogador:

### Grupos de Features

| Grupo | Features |
|-------|----------|
| Volume | txn_count_24h/7d/30d, txn_amount_24h/7d/30d |
| Depósitos/Saques | deposit_count_30d, withdrawal_count_30d, deposit_velocity, cashout_ratio |
| Comportamento | night_activity_ratio, weekend_activity_ratio, avg_odds_bet_7d, win_loss_ratio |
| Rede | shared_instrument_score, multi_currency_flag, unique_instruments_7d |

---

## 8. Relatórios

### 8.1 Relatório Mensal (COAF)

1. Acesse **Relatórios**
2. Selecione o mês
3. Clique **Gerar Relatório**
4. Após gerado, clique **PDF** para download

O relatório inclui:
- Resumo de alertas por severidade e tipo
- Total de casos abertos/fechados
- Jogadores com maior score de risco
- Operações suspeitas acima de R$ 10.000 (conforme IN COAF nº 161/2022)

### 8.2 Relatório de Caso Individual

Em **Casos → [Caso] → Exportar PDF**

---

## 9. LGPD — Direito ao Esquecimento

Para anonimizar dados de um jogador (Art. 18 LGPD):

1. Acesse a API:
```bash
curl -X POST http://localhost:8000/players/{player_id}/right-to-erasure \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"reason": "Solicitação LGPD Art. 18", "requested_by": "analyst@betaml.io"}'
```

2. Ou via página **Admin → Ações Legais → Anonimizar Jogador**

A ação registra trilha de auditoria e substitui PII por hashes SHA-256.

---

## 10. Listas de Jogadores

Em **Listas**, gerencie:
- **BLOCKLIST**: jogadores bloqueados automaticamente
- **ALLOWLIST**: jogadores isentos de certas regras
- **WATCHLIST**: jogadores monitorados ativamente

### Importação CSV

Formato esperado:
```csv
player_id,notes
UUID-1,Suspeita de estruturação
UUID-2,PEP identificado
```

---

## 11. Atalhos de Teclado

| Tecla | Ação |
|-------|------|
| `?` | Mostrar ajuda |
| `G A` | Ir para Alertas |
| `G C` | Ir para Casos |
| `G D` | Ir para Dashboard |
| `Esc` | Fechar painel lateral |

---

## 12. Suporte

- **Documentação técnica**: `/docs` (Swagger UI em desenvolvimento)
- **E-mail**: compliance@betaml.io
- **Canal interno**: #betaml-suporte
