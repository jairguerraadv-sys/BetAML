# BetAML Rules Engine DSL Guide

## Overview

O **Domain Specific Language (DSL)** do BetAML permite definir regras de detecção de anomalias de forma declarativa, sem necessidade de código. As regras são avaliadas em tempo real contra eventos e features.

---

## 1. Sintaxe Básica

### Operadores de Comparação

```
> (maior que)
< (menor que)
>= (maior ou igual)
<= (menor ou igual)
== (igual)
!= (diferente)
in (contém, para listas)
contains (substring)
```

**Exemplos:**
```
features.deposit_sum_24h > 5000
player.pepFlag == true
features.status != "BLOCKED"
method in ["PIX", "TED"]
```

### Operadores Lógicos

```
and (e)
or (ou)
not (negação)
```

**Exemplos:**
```
features.deposit_sum_24h > 5000 and features.zscore_current_deposit_vs_baseline > 3.0
player.pepFlag = true or player.declaredIncomeMonthly > 100000
not (player.is_verified)
```

### Parênteses

```
(condition1 and condition2) or condition3
```

---

## 2. Operandos

### Literais

**Números:**
```
100
5000.50
0.85
```

**Strings:**
```
"PIX"
"DEPOSIT"
```

**Booleanos:**
```
true
false
```

### Identificadores (Variáveis)

Acessam dados do evento ou features:

```
features.deposit_sum_24h        # Feature computada
features.zscore_current_deposit_vs_baseline
player.pepFlag                  # Atributo do jogador
player.declaredIncomeMonthly
transaction.amount              # Atributo da transação
transaction.type
bet.stakeAmount
```

---

## 3. Funções Suportadas

### sum(valores...)
Soma de valores.

```
sum(features.deposit_sum_24h, features.withdrawal_sum_24h)
```

### count(lista)
Contagem de elementos.

```
count(features.deposit_count_24h)
```

### avg(valores...)
Média de valores.

```
avg(features.deposit_sum_7d, features.deposit_sum_30d)
```

### max(valores...)
Máximo.

```
max(features.deposit_sum_24h, features.bet_stake_sum_24h)
```

### min(valores...)
Mínimo.

```
min(features.deposit_count_24h, features.withdrawal_count_7d)
```

### zscore(valor, baseline, stddev)
Desvio padrão da média (Z-score).

**Uso:** Detectar spikes em relação à baseline do jogador.

```
zscore(features.current_deposit, features.baseline_avg_daily_deposit, features.baseline_stddev_deposit) > 3.0
```

**Interpretação:**
- Z-score > 3.0: Spike significativo (anomalia)
- Z-score > 2.0: Acima da normal
- Z-score entre -1 a 1: Comportamento normal

### ratio(numerador, denominador)
Razão entre dois valores.

```
ratio(features.withdrawal_sum_7d, features.deposit_sum_7d) > 0.8
```

---

## 4. Regras Padrão (12 Exemplos)

### 1. Spike vs Baseline
**Detecta:** Depósito anormalmente alto comparado ao histórico.

```dsl
features.zscore_current_deposit_vs_baseline > 3.0
AND features.deposit_sum_24h > 5000
```

**Severidade:** HIGH
**Categoria:** SPIKE

---

### 2. Structuring (Quebra de Valores)
**Detecta:** Múltiplos depósitos pequenos para evitar threshold.

```dsl
features.deposit_count_24h >= 5
AND features.deposit_sum_24h > 3000
```

**Severidade:** MEDIUM
**Categoria:** STRUCTURING

---

### 3. Saque Rápido Pós-Depósito
**Detecta:** Depósito seguido rapidamente por saque (tipicamente < 1h).

```dsl
features.withdrawal_sum_24h > 0
AND features.deposit_sum_24h > 0
AND features.ratio_withdrawal_to_deposit_7d > 0.5
```

**Severidade:** MEDIUM
**Categoria:** RAPID_WITHDRAWAL

---

### 4. Instrumento de Pagamento Novo + Valor Alto
**Detecta:** Novo cartão/conta bancária com transação grande.

```dsl
features.new_payment_instrument_flag == 1
AND features.deposit_sum_24h > 2000
```

**Severidade:** HIGH
**Categoria:** NEW_INSTRUMENT_HIGH_VALUE

---

### 5. PEP com Desvio Alto
**Detecta:** Pessoa Exposta Politicamente com comportamento anômalo.

```dsl
player.pepFlag == true
AND features.zscore_current_deposit_vs_baseline > 2.0
```

**Severidade:** CRITICAL
**Categoria:** PEP_RISK

---

### 6. Conta Bancária Compartilhada
**Detecta:** Múltiplos jogadores usando a mesma conta bancária.

```dsl
features.shared_bank_account_count > 2
AND player.pepFlag == true
```

**Severidade:** HIGH
**Categoria:** SHARED_ACCOUNT

---

### 7. Dispositivo Compartilhado
**Detecta:** Múltiplos CPFs no mesmo device/IP.

```dsl
features.shared_device_count > 3
```

**Severidade:** MEDIUM
**Categoria:** SHARED_DEVICE

---

### 8. Alta Razão Saque/Depósito
**Detecta:** Padrão de "lavar dinheiro": depósito rápido → saque.

```dsl
features.ratio_withdrawal_to_deposit_7d > 0.9
AND features.deposit_sum_7d > 1000
```

**Severidade:** HIGH
**Categoria:** HIGH_WITHDRAWAL_RATIO

---

### 9. Spike de Apostas
**Detecta:** Aumento abrupto em stake (valor de aposta).

```dsl
features.bet_stake_sum_24h > features.bet_stake_sum_7d * 3
```

**Severidade:** MEDIUM
**Categoria:** BET_SPIKE

---

### 10. Múltiplos Chargebacks/Reversões
**Detecta:** Padrão de disputas de transação.

```dsl
features.chargeback_count_30d >= 3
```

**Severidade:** HIGH
**Categoria:** CHARGEBACK_PATTERN

---

### 11. Múltiplas Tentativas Falhas + Sucesso
**Detecta:** Brute-force ou teste de cartões.

```dsl
features.failed_deposit_attempts_24h > 5
AND features.successful_deposit_count_24h > 0
```

**Severidade:** MEDIUM
**Categoria:** FAILED_ATTEMPTS_THEN_SUCCESS

---

### 12. Round-Tripping (Depósito → Aposta Mínima → Saque)
**Detecta:** Dinâmica onde jogador não "joga", apenas move dinheiro.

```dsl
features.deposit_sum_24h > 500
AND features.bet_stake_sum_24h < 100
AND features.withdrawal_sum_24h > features.deposit_sum_24h * 0.9
```

**Severidade:** MEDIUM
**Categoria:** ROUND_TRIPPING

---

## 5. Features Disponíveis para Uso

### Financeiras (24h/7d/30d)
```
features.deposit_sum_24h
features.deposit_sum_7d
features.deposit_sum_30d
features.deposit_count_24h
features.deposit_count_7d

features.withdrawal_sum_24h
features.withdrawal_sum_7d
features.withdrawal_sum_30d
features.withdrawal_count_24h
features.withdrawal_count_7d

features.chargeback_count_30d
```

### Comportamentais
```
features.bet_stake_sum_24h
features.bet_stake_sum_7d
features.bet_count_24h
features.bet_count_7d

features.new_payment_instrument_flag
features.new_device_flag
```

### Baseline & Z-Score
```
features.baseline_avg_daily_deposit
features.baseline_stddev_deposit
features.zscore_current_deposit_vs_baseline

features.ratio_withdrawal_to_deposit_7d
features.ratio_failed_to_total_deposits_7d
```

### Correlações
```
features.shared_device_count
features.shared_bank_account_count
features.shared_ip_count
```

### Atributos do Jogador
```
player.pepFlag
player.declaredIncomeMonthly
player.profession
player.age
player.is_verified
player.kyc_status
```

---

## 6. Exemplos Avançados

### Exemplo 1: Detecção Multi-Dimensão
```dsl
(
  features.zscore_current_deposit_vs_baseline > 2.5
  OR features.deposit_sum_24h > 10000
)
AND (
  features.shared_device_count > 2
  OR features.shared_bank_account_count > 1
)
AND player.pepFlag == true
```

**Lógica:** Spike OU alta gasto E (compartilhado) E PEP

---

### Exemplo 2: Comportamento Específico por Renda
```dsl
player.declaredIncomeMonthly < 3000
AND features.deposit_sum_24h > 5000
```

**Lógica:** Renda declarada baixa, mas depósito alto (inconsistência)

---

### Exemplo 3: Janelas Temporais
```dsl
features.deposit_count_24h >= 10
AND features.deposit_sum_24h > 3000
AND features.ratio_withdrawal_to_deposit_7d < 0.1
```

**Lógica:** Depósitos frequentes, mas pouco saque (acúmulo)

---

## 7. Testes de Regra (DSL Simulation)

Via API:

```bash
POST /rules/{id}/simulate

{
  "test_events": [
    {
      "player_id": "test-player-1",
      "features": {
        "deposit_sum_24h": 6000,
        "zscore_current_deposit_vs_baseline": 3.5,
        "baseline_avg_daily_deposit": 500,
        "baseline_stddev_deposit": 200
      },
      "player": {
        "pepFlag": false
      }
    }
  ]
}
```

**Resposta:**
```json
{
  "rule_id": "rule-spike-001",
  "test_results": [
    {
      "event_id": "test-player-1",
      "matched": true,
      "evaluation_log": "features.zscore_current_deposit_vs_baseline (3.5) > 3.0: TRUE; features.deposit_sum_24h (6000) > 5000: TRUE; Result: TRUE"
    }
  ]
}
```

---

## 8. Performance e Otimizações

### Cache de Features
- Features são cachetadas em Redis por 1 hora
- Evita recálculos em regras múltiplas
- Baseline atualizada diariamente (batch)

### Avaliação Lazy
- Operadores AND param se esquerda for false
- Economiza CPU em regras complexas

### Índices (ClickHouse)
```sql
ALTER TABLE alerts ADD INDEX rule_id_idx (rule_id) TYPE minmax GRANULARITY 1;
ALTER TABLE alerts ADD INDEX created_at_idx (created_at) TYPE minmax GRANULARITY 1;
```

---

## 9. Erros Comuns

### ❌ Sem espaçamento entre operadores
```
features.deposit_sum_24h>5000  # ERRADO
```

✅ Com espaçamento:
```
features.deposit_sum_24h > 5000  # CORRETO
```

### ❌ Tipo incorreto
```
features.deposit_sum_24h == "5000"  # ERRADO (número vs string)
```

✅ Tipo correto:
```
features.deposit_sum_24h == 5000  # CORRETO
```

### ❌ Feature inexistente
```
features.foobar > 100  # ERRO: foobar não existe
```

✅ Feature válida:
```
features.deposit_sum_24h > 100  # CORRETO
```

---

## 10. Boas Práticas

1. **Sempre especifique tanto magnitude quanto frequência:**
   ```
   ❌ features.deposit_count_24h >= 5
   ✅ features.deposit_count_24h >= 5 AND features.deposit_sum_24h > 3000
   ```

2. **Use Z-scores para desvios em vez de valores absolutos:**
   ```
   ❌ features.deposit_sum_24h > 10000  # Depende da renda
   ✅ features.zscore_current_deposit_vs_baseline > 3.0
   ```

3. **Combine múltiplas sinais para reduzir false positives:**
   ```
   ❌ player.pepFlag == true
   ✅ player.pepFlag == true AND features.zscore_current_deposit_vs_baseline > 2.0
   ```

4. **Revise e valide regras regularmente:**
   - Revise true positive rate
   - Ajuste thresholds baseado em feedback
   - Retire regras com baixa atividade

5. **Documente intenção:**
   ```
   name: "Spike vs Baseline"
   description: "Detect 3+ sigma deposit spike above player baseline"
   ```

---

**Última atualização:** 26/02/2024
