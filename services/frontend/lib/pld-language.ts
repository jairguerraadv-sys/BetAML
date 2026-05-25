type AlertLike = {
  alert_type?: string | null;
  title?: string | null;
  anomaly_score?: number | null;
  composite_score?: number | null;
  score_breakdown?: Record<string, unknown> | null;
  evidence?: Record<string, unknown> | null;
};

export const ALERT_TYPE_LABELS: Record<string, string> = {
  VELOCITY: 'Movimentação em velocidade incompatível com o perfil',
  STRUCTURING: 'Possível fracionamento de valores',
  ML_ANOMALY: 'Comportamento fora do padrão histórico',
  ANOMALY: 'Comportamento fora do padrão histórico',
  PEP_EXPOSURE: 'Exposição a PEP ou jurisdição de risco',
  MULTI_ACCOUNT: 'Uso incomum de contas ou dispositivos',
  HIGH_RISK_CUST: 'Cliente com perfil de risco elevado',
  COMPOSITE: 'Combinação de sinais de risco',
  RULE: 'Condição de risco atendida',
  SLOT_FREQUENCY: 'Frequência atípica em slots',
  CASINO_WASHING: 'Padrão atípico em casino ao vivo',
  PRODUCT_DIVERSITY: 'Uso incomum de várias modalidades',
};

export const FEATURE_LABELS: Record<string, string> = {
  deposit_sum_24h: 'Total depositado nas últimas 24h',
  deposit_sum_7d: 'Total depositado nos últimos 7 dias',
  deposit_sum_30d: 'Total depositado nos últimos 30 dias',
  deposit_count_24h: 'Quantidade de depósitos em 24h',
  zscore_current_deposit_vs_baseline: 'Depósito atual comparado ao padrão do cliente',
  new_payment_instrument_flag: 'Uso de nova conta ou meio de pagamento',
  shared_device_count: 'Quantidade de clientes no mesmo dispositivo',
  shared_device_score: 'Força do vínculo por dispositivo',
  shared_instrument_score: 'Força do vínculo por conta ou chave de pagamento',
  deposit_velocity: 'Velocidade de depósitos',
  night_activity_ratio: 'Atividade em horário noturno',
  weekend_activity_ratio: 'Atividade em fim de semana',
  chargeback_rate_30d: 'Taxa de estornos nos últimos 30 dias',
  cashout_ratio_7d: 'Relação entre saques e depósitos em 7 dias',
  unique_instruments_7d: 'Meios de pagamento usados em 7 dias',
  unique_instruments_used_7d: 'Meios de pagamento usados em 7 dias',
  bonus_to_real_ratio_30d: 'Uso de bônus comparado a dinheiro real',
  bonus_to_real_money_ratio_30d: 'Uso de bônus comparado a dinheiro real',
  declared_income_monthly: 'Renda mensal declarada',
  income_volume: 'Compatibilidade entre renda e volume movimentado',
  rule_score: 'Peso da condição de risco',
  ml_anomaly_score: 'Sinal automático de comportamento atípico',
  network_score: 'Sinal de vínculos com outros clientes',
  risk_score: 'Pontuação final de risco',
  risk_band: 'Faixa de risco do cliente',
  auto_case_threshold: 'Ponto de abertura automática de caso',
};

export const EVIDENCE_LABELS: Record<string, string> = {
  rule_id: 'Condição de risco',
  rule_version: 'Versão da condição',
  triggered_condition: 'Critério que disparou o alerta',
  feature_snapshot: 'Indicadores considerados',
  threshold_values: 'Limites usados na análise',
  scoring_policy: 'Política de sensibilidade aplicada',
  income_volume: 'Compatibilidade renda x volume',
  model_id: 'Analisador automático',
  top_drivers: 'Principais motivos apontados pelo sistema',
};

export function humanAlertType(type?: string | null) {
  if (!type) return 'Sinal de risco';
  return ALERT_TYPE_LABELS[type] ?? type.replaceAll('_', ' ').toLowerCase();
}

export function humanFeatureName(name?: string | null) {
  if (!name) return 'Indicador';
  return FEATURE_LABELS[name] ?? name.replaceAll('_', ' ');
}

export function humanEvidenceName(name?: string | null) {
  if (!name) return 'Evidência';
  return EVIDENCE_LABELS[name] ?? humanFeatureName(name);
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function formatMoney(value: unknown) {
  const n = Number(value);
  if (!Number.isFinite(n)) return null;
  return n.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
}

function formatPercent(value: unknown) {
  const n = Number(value);
  if (!Number.isFinite(n)) return null;
  return `${(n * 100).toFixed(0)}%`;
}

export function formatIndicatorValue(key: string, value: unknown): string {
  if (value === null || value === undefined || value === '') return 'sem dado';
  if (key.includes('sum') || key.includes('income') || key.includes('amount') || key.includes('value')) {
    const money = formatMoney(value);
    if (money) return money;
  }
  if (key.includes('ratio') || key.includes('score') || key.includes('rate')) {
    const percent = formatPercent(value);
    if (percent) return percent;
  }
  if (typeof value === 'boolean') return value ? 'sim' : 'não';
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(2);
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

export function alertReasonBullets(alert: AlertLike): string[] {
  const bullets: string[] = [];
  const evidence = asRecord(alert.evidence);
  const scoreBreakdown = asRecord(alert.score_breakdown);
  const featureSnapshot = asRecord(evidence.feature_snapshot);
  const incomeVolume = asRecord(evidence.income_volume ?? scoreBreakdown.income_volume);

  bullets.push(humanAlertType(alert.alert_type));

  if (incomeVolume.tier === 'RED') {
    const ratio = Number(incomeVolume.ratio);
    bullets.push(
      Number.isFinite(ratio)
        ? `Volume dos últimos 30 dias está ${ratio.toFixed(1)}x acima da renda declarada`
        : 'Volume movimentado parece incompatível com a renda declarada',
    );
  } else if (incomeVolume.tier === 'YELLOW') {
    bullets.push('Volume movimentado merece atenção frente à renda declarada');
  }

  const interestingKeys = [
    'deposit_sum_24h',
    'deposit_sum_30d',
    'new_payment_instrument_flag',
    'shared_device_count',
    'shared_device_score',
    'shared_instrument_score',
    'chargeback_rate_30d',
    'cashout_ratio_7d',
  ];
  for (const key of interestingKeys) {
    const value = featureSnapshot[key];
    if (value === undefined || value === null || value === false || value === 0) continue;
    bullets.push(`${humanFeatureName(key)}: ${formatIndicatorValue(key, value)}`);
    if (bullets.length >= 4) break;
  }

  const score = alert.composite_score ?? alert.anomaly_score;
  if (score != null && bullets.length < 4) {
    bullets.push(`Pontuação consolidada de risco: ${formatPercent(score) ?? String(score)}`);
  }

  return Array.from(new Set(bullets)).slice(0, 4);
}
