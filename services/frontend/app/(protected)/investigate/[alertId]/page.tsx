'use client';

/**
 * Wizard de Investigação Guiada — Onda 2
 *
 * 4 passos para conduzir o analista do alerta à decisão:
 *   1. O que aconteceu?  — resumo do alerta em linguagem simples + evidências
 *   2. Quem é o apostador? — perfil, risco, PEP e compatibilidade econômica
 *   3. Como o dinheiro se moveu? — transações e apostas relacionadas
 *   4. Qual é a sua conclusão? — triagem + opção de abrir caso formal
 */

import { useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { useQuery, useMutation } from '@tanstack/react-query';
import {
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  HelpCircle,
  User,
  ArrowRightLeft,
  Gavel,
  AlertTriangle,
  ShieldAlert,
  Banknote,
  TrendingDown,
  XCircle,
  FolderPlus,
} from 'lucide-react';
import Link from 'next/link';
import {
  AlertDetail,
  AlertExplainability,
  EconCompat,
  PlayerDetail,
  RelatedTransactions,
  createCase,
  fetchAlert,
  fetchAlertExplainability,
  fetchAlertRelatedTransactions,
  fetchPlayer,
  fetchPlayerEconCompat,
  linkAlertToCase,
  triageAlert,
} from '@/lib/api';

// ── Dicionários de tradução ──────────────────────────────────────────────────

const TYPE_EXPLAIN: Record<string, string> = {
  VELOCITY:       'Movimentação em velocidade incompatível com o perfil do apostador',
  STRUCTURING:    'Múltiplas operações menores para evitar controles (fracionamento)',
  ML_ANOMALY:     'Comportamento divergente do histórico detectado pelo sistema de IA',
  PEP_EXPOSURE:   'Envolvimento com pessoa politicamente exposta ou jurisdição de risco',
  MULTI_ACCOUNT:  'Uso de múltiplas contas ou dispositivos em curto intervalo',
  HIGH_RISK_CUST: 'Apostador classificado como perfil de alto risco',
  COMPOSITE:      'Combinação de múltiplos fatores de risco detectados simultaneamente',
};

const SEV_PT: Record<string, string> = {
  CRITICAL: 'Crítico', HIGH: 'Alto', MEDIUM: 'Médio', LOW: 'Baixo',
};

const SEV_CLS: Record<string, string> = {
  CRITICAL: 'bg-red-100 text-red-700 border-red-200',
  HIGH:     'bg-orange-100 text-orange-700 border-orange-200',
  MEDIUM:   'bg-yellow-100 text-yellow-700 border-yellow-200',
  LOW:      'bg-green-100 text-green-700 border-green-200',
};

const RISK_BAND_CLS: Record<string, string> = {
  HIGH:   'bg-red-100 text-red-700',
  MEDIUM: 'bg-yellow-100 text-yellow-700',
  LOW:    'bg-green-100 text-green-700',
};

const ECON_CLS: Record<string, string> = {
  GREEN:   'bg-green-100 text-green-700',
  YELLOW:  'bg-yellow-100 text-yellow-700',
  RED:     'bg-red-100 text-red-700',
  UNKNOWN: 'bg-gray-100 text-gray-500',
};

const EVIDENCE_LABEL: Record<string, string> = {
  transaction_count:  'Nº de transações',
  amount_total:       'Valor total',
  velocity_1h:        'Frequência (última 1h)',
  velocity_24h:       'Frequência (últimas 24h)',
  amount_24h:         'Valor movimentado (24h)',
  bet_count:          'Total de apostas',
  win_rate:           'Taxa de acerto',
  deposit_amount:     'Total depositado',
  withdrawal_amount:  'Total sacado',
  account_age_days:   'Idade da conta (dias)',
  devices_count:      'Dispositivos distintos',
  ip_count:           'IPs distintos',
  pep_flag:           'É PEP',
  risk_score:         'Pontuação de risco',
  structuring_score:  'Pontuação de fracionamento',
  anomaly_score:      'Pontuação de anomalia',
};

function fmtEvidence(k: string, v: unknown): string {
  if (k === 'pep_flag') return v ? 'Sim' : 'Não';
  if (typeof v === 'number') {
    if (k.includes('amount') || k.includes('score') || k.includes('rate')) {
      return v > 1
        ? `R$ ${v.toLocaleString('pt-BR', { minimumFractionDigits: 2 })}`
        : `${(v * 100).toFixed(1)}%`;
    }
    return v.toLocaleString('pt-BR');
  }
  return String(v);
}

function fmtBRL(v: number) {
  return v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
}

// ── Stepper ──────────────────────────────────────────────────────────────────

const STEPS = [
  { id: 1, label: 'O alerta',       icon: AlertTriangle },
  { id: 2, label: 'O apostador',    icon: User },
  { id: 3, label: 'Movimentações',  icon: ArrowRightLeft },
  { id: 4, label: 'Sua decisão',    icon: Gavel },
];

function Stepper({ current }: { current: number }) {
  return (
    <nav aria-label="Progresso da investigação" className="flex items-center gap-0">
      {STEPS.map((step, idx) => {
        const done    = current > step.id;
        const active  = current === step.id;
        const Icon    = step.icon;
        return (
          <div key={step.id} className="flex items-center">
            <div
              className={`flex items-center gap-2 rounded-full px-4 py-2 text-xs font-semibold transition-all ${
                active  ? 'bg-brand text-white shadow-md' :
                done    ? 'bg-brand/20 text-brand'        :
                           'bg-gray-100 text-gray-400'
              }`}
            >
              {done ? (
                <CheckCircle2 size={13} />
              ) : (
                <Icon size={13} />
              )}
              <span className="hidden sm:inline">{step.label}</span>
              <span className="inline sm:hidden">{step.id}</span>
            </div>
            {idx < STEPS.length - 1 && (
              <div className={`h-0.5 w-6 sm:w-10 ${current > step.id ? 'bg-brand/40' : 'bg-gray-200'}`} />
            )}
          </div>
        );
      })}
    </nav>
  );
}

// ── Card de seção ────────────────────────────────────────────────────────────

function Card({ title, icon: Icon, children, tone = 'default' }: {
  title: string;
  icon?: React.ElementType;
  children: React.ReactNode;
  tone?: 'default' | 'info' | 'warning' | 'danger' | 'success';
}) {
  const borderCls = {
    default: 'border-gray-200',
    info:    'border-blue-200 bg-blue-50',
    warning: 'border-amber-200 bg-amber-50',
    danger:  'border-red-200 bg-red-50',
    success: 'border-emerald-200 bg-emerald-50',
  }[tone];
  return (
    <div className={`rounded-xl border p-5 shadow-sm ${borderCls} bg-white`}>
      {title && (
        <h3 className={`mb-3 flex items-center gap-2 text-sm font-semibold ${
          tone === 'default' ? 'text-gray-700' : 'text-gray-800'
        }`}>
          {Icon && <Icon size={15} className="opacity-70" />}
          {title}
        </h3>
      )}
      {children}
    </div>
  );
}

// ── Step 1: O alerta ─────────────────────────────────────────────────────────

function StepAlert({
  alert,
  explainability,
}: {
  alert: AlertDetail;
  explainability?: AlertExplainability;
}) {
  const explain = TYPE_EXPLAIN[alert.alert_type] ?? `Tipo: ${alert.alert_type}`;
  const evidenceEntries = Object.entries(alert.evidence ?? {});

  return (
    <div className="space-y-5">
      {/* Resumo em linguagem simples */}
      <Card title="O que foi detectado?" icon={HelpCircle} tone="info">
        <p className="text-sm text-blue-900 leading-relaxed">{explain}</p>
        {alert.anomaly_score != null && (
          <div className="mt-3 flex items-center gap-3">
            <div className="text-xs text-blue-700">Pontuação de risco:</div>
            <div className="flex-1 h-2.5 rounded-full bg-blue-200">
              <div
                className="h-2.5 rounded-full bg-blue-600"
                style={{ width: `${Math.min(100, alert.anomaly_score * 100).toFixed(0)}%` }}
              />
            </div>
            <div className="text-sm font-bold text-blue-800">
              {(alert.anomaly_score * 100).toFixed(0)}%
            </div>
          </div>
        )}
      </Card>

      {/* Severidade + status */}
      <div className="flex flex-wrap gap-3">
        <div className="rounded-xl border border-gray-200 bg-white p-4 flex-1 min-w-[140px] shadow-sm">
          <p className="text-xs text-gray-400">Prioridade</p>
          <span className={`mt-1 inline-block rounded border px-2.5 py-1 text-sm font-bold ${SEV_CLS[alert.severity] ?? 'bg-gray-100 text-gray-700'}`}>
            {SEV_PT[alert.severity] ?? alert.severity}
          </span>
        </div>
        <div className="rounded-xl border border-gray-200 bg-white p-4 flex-1 min-w-[140px] shadow-sm">
          <p className="text-xs text-gray-400">Criado em</p>
          <p className="mt-1 text-sm font-semibold text-gray-800">
            {new Date(alert.created_at).toLocaleString('pt-BR', {
              day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit',
            })}
          </p>
        </div>
        {alert.case_id && (
          <div className="rounded-xl border border-indigo-200 bg-indigo-50 p-4 flex-1 min-w-[140px] shadow-sm">
            <p className="text-xs text-indigo-500">Caso vinculado</p>
            <Link href={`/cases/${alert.case_id}`} className="mt-1 block text-sm font-semibold text-indigo-700 hover:underline">
              Ver caso →
            </Link>
          </div>
        )}
      </div>

      {/* Evidências*/}
      {evidenceEntries.length > 0 && (
        <Card title="Informações de suporte" icon={ShieldAlert}>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            {evidenceEntries.map(([k, v]) => (
              <div key={k} className="rounded-lg bg-gray-50 p-3">
                <p className="text-[11px] text-gray-400">{EVIDENCE_LABEL[k] ?? k}</p>
                <p className="mt-0.5 text-sm font-semibold text-gray-800 break-all">
                  {fmtEvidence(k, v)}
                </p>
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* ML features — se disponíveis */}
      {explainability && explainability.top_features.length > 0 && (
        <Card title="O que mais contribuiu para a pontuação?" icon={TrendingDown}>
          <div className="space-y-2.5">
            {explainability.top_features.slice(0, 5).map((item) => {
              const pct     = Math.min(100, Math.abs(item.contribution) * 100);
              const isRisk  = item.contribution > 0;
              return (
                <div key={item.feature}>
                  <div className="flex items-center justify-between text-xs mb-1">
                    <span className="text-gray-700 font-medium">
                      {EVIDENCE_LABEL[item.feature] ?? item.feature}
                    </span>
                    <span className={isRisk ? 'text-red-600 font-semibold' : 'text-green-600'}>
                      {isRisk ? '▲ risco' : '▼ reduz risco'}
                    </span>
                  </div>
                  <div className="h-2 rounded-full bg-gray-100">
                    <div
                      className={`h-2 rounded-full ${isRisk ? 'bg-red-500' : 'bg-green-500'}`}
                      style={{ width: `${pct.toFixed(0)}%` }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </Card>
      )}
    </div>
  );
}

// ── Step 2: O apostador ─────────────────────────────────────────────────────

function StepPlayer({
  player,
  econCompat,
}: {
  player?: PlayerDetail;
  econCompat?: EconCompat;
}) {
  if (!player) {
    return (
      <div className="rounded-xl border border-dashed border-gray-200 bg-gray-50 py-16 text-center text-sm text-gray-400">
        Nenhum apostador vinculado a este alerta.
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {/* Identificação */}
      <Card title="Identificação do apostador" icon={User}>
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
          <div>
            <p className="text-xs text-gray-400">ID externo</p>
            <p className="mt-0.5 text-sm font-semibold text-gray-800 font-mono">{player.external_player_id}</p>
          </div>
          <div>
            <p className="text-xs text-gray-400">CPF (mascarado)</p>
            <p className="mt-0.5 text-sm font-medium text-gray-800 font-mono">{player.cpf ?? '—'}</p>
          </div>
          <div>
            <p className="text-xs text-gray-400">É PEP?</p>
            <p className={`mt-0.5 inline-block rounded px-2 py-0.5 text-sm font-semibold ${
              player.pep_flag ? 'bg-red-100 text-red-700' : 'bg-green-100 text-green-700'
            }`}>
              {player.pep_flag ? 'Sim — pessoa politicamente exposta' : 'Não'}
            </p>
          </div>
        </div>
      </Card>

      {/* Risco */}
      <div className="grid gap-4 sm:grid-cols-2">
        <Card title="Classificação de risco" icon={ShieldAlert} tone={player.risk_band === 'HIGH' ? 'danger' : player.risk_band === 'MEDIUM' ? 'warning' : 'success'}>
          <div className="flex items-end gap-3">
            <p className="text-4xl font-bold text-gray-900">
              {(player.risk_score * 100).toFixed(0)}%
            </p>
            <span className={`mb-1 inline-block rounded px-2 py-0.5 text-xs font-bold ${RISK_BAND_CLS[player.risk_band] ?? 'bg-gray-100 text-gray-600'}`}>
              {player.risk_band === 'HIGH' ? 'Alto risco' : player.risk_band === 'MEDIUM' ? 'Médio risco' : 'Baixo risco'}
            </span>
          </div>
          <p className="mt-2 text-xs text-gray-500">
            {player.risk_band === 'HIGH'
              ? 'Atenção máxima: perfil com histórico de comportamento atípico.'
              : player.risk_band === 'MEDIUM'
              ? 'Monitorar: padrão requer acompanhamento continuado.'
              : 'Perfil sem alertas recorrentes de alto risco.'}
          </p>
        </Card>

        {/* Compatibilidade econômica */}
        {econCompat && (
          <Card title="Compatibilidade econômica" icon={Banknote} tone={
            econCompat.tier === 'RED' ? 'danger' : econCompat.tier === 'YELLOW' ? 'warning' : 'success'
          }>
            <div className="flex items-end gap-3">
              <p className="text-4xl font-bold text-gray-900">
                {econCompat.income_ratio_30d != null
                  ? `${(econCompat.income_ratio_30d * 100).toFixed(0)}%`
                  : '—'}
              </p>
              <span className={`mb-1 inline-block rounded px-2 py-0.5 text-xs font-bold ${ECON_CLS[econCompat.tier ?? 'UNKNOWN']}`}>
                {econCompat.tier === 'RED' ? 'Incompatível' : econCompat.tier === 'YELLOW' ? 'Atenção' : 'Compatível'}
              </span>
            </div>
            <p className="mt-1 text-xs text-gray-600">{econCompat.interpretation}</p>
            <div className="mt-2 grid grid-cols-2 gap-2 text-xs text-gray-500">
              <div>Depósitos 30d: <span className="font-semibold text-gray-700">{fmtBRL(econCompat.deposit_sum_30d)}</span></div>
              <div>Renda declarada: <span className="font-semibold text-gray-700">
                {econCompat.declared_income_monthly != null ? fmtBRL(econCompat.declared_income_monthly) : 'Não informada'}
              </span></div>
            </div>
          </Card>
        )}
      </div>

      {player.last_scored_at && (
        <p className="text-xs text-gray-400">
          Pontuação atualizada em {new Date(player.last_scored_at).toLocaleString('pt-BR')}
        </p>
      )}
    </div>
  );
}

// ── Step 3: Movimentações ────────────────────────────────────────────────────

function StepTransactions({ tx }: { tx?: RelatedTransactions }) {
  if (!tx) {
    return (
      <div className="rounded-xl border border-dashed border-gray-200 bg-gray-50 py-16 text-center text-sm text-gray-400">
        Nenhuma transação relacionada encontrada para este alerta.
      </div>
    );
  }

  const { transactions = [], bets = [] } = tx;

  return (
    <div className="space-y-5">
      {/* Transações financeiras */}
      <Card title={`Transações financeiras no período (${transactions.length})`} icon={ArrowRightLeft}>
        {transactions.length === 0 ? (
          <p className="text-sm text-gray-400">Nenhuma transação no período de análise.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-gray-100 text-left text-gray-400">
                  <th className="py-2 pr-3 font-medium">Tipo</th>
                  <th className="py-2 pr-3 font-medium">Valor</th>
                  <th className="py-2 pr-3 font-medium">Status</th>
                  <th className="py-2 pr-3 font-medium">Método</th>
                  <th className="py-2 font-medium">Data</th>
                </tr>
              </thead>
              <tbody>
                {transactions.map((t) => (
                  <tr key={t.id} className="border-b border-gray-50 hover:bg-gray-50">
                    <td className="py-2 pr-3 font-medium text-gray-800">{t.type}</td>
                    <td className={`py-2 pr-3 font-semibold ${t.type === 'WITHDRAWAL' ? 'text-red-600' : 'text-green-700'}`}>
                      {fmtBRL(t.amount)}
                    </td>
                    <td className="py-2 pr-3">
                      <span className={`rounded px-1.5 py-0.5 ${
                        t.status === 'COMPLETED' ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'
                      }`}>
                        {t.status}
                      </span>
                    </td>
                    <td className="py-2 pr-3 text-gray-500">{t.payment_method ?? '—'}</td>
                    <td className="py-2 text-gray-400">
                      {new Date(t.occurred_at).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* Apostas */}
      {bets.length > 0 && (
        <Card title={`Apostas no período (${bets.length})`} icon={TrendingDown}>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-gray-100 text-left text-gray-400">
                  <th className="py-2 pr-3 font-medium">Tipo</th>
                  <th className="py-2 pr-3 font-medium">Apostado</th>
                  <th className="py-2 pr-3 font-medium">Ganho</th>
                  <th className="py-2 pr-3 font-medium">Status</th>
                  <th className="py-2 font-medium">Evento</th>
                </tr>
              </thead>
              <tbody>
                {bets.map((b) => {
                  const ratio = b.actual_payout != null && b.stake_amount > 0
                    ? b.actual_payout / b.stake_amount
                    : null;
                  return (
                    <tr key={b.id} className="border-b border-gray-50 hover:bg-gray-50">
                      <td className="py-2 pr-3 font-medium text-gray-800">{b.bet_type}</td>
                      <td className="py-2 pr-3 text-gray-700">{fmtBRL(b.stake_amount)}</td>
                      <td className={`py-2 pr-3 font-semibold ${
                        ratio != null && ratio > 1 ? 'text-green-700' : 'text-gray-500'
                      }`}>
                        {b.actual_payout != null ? fmtBRL(b.actual_payout) : '—'}
                      </td>
                      <td className="py-2 pr-3">
                        <span className={`rounded px-1.5 py-0.5 ${
                          b.status === 'WON' ? 'bg-green-100 text-green-700' :
                          b.status === 'LOST' ? 'bg-red-100 text-red-600' :
                          'bg-gray-100 text-gray-500'
                        }`}>
                          {b.status}
                        </span>
                      </td>
                      <td className="py-2 text-gray-400 truncate max-w-[120px]" title={b.event_name ?? ''}>
                        {b.event_name ?? '—'}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {(transactions.length > 0 || bets.length > 0) && (
        <div className="rounded-xl border border-blue-100 bg-blue-50 p-4 text-xs text-blue-700">
          <strong>Janela de análise:</strong> {tx.window_hours}h antes do alerta.
          Total de transações: <strong>{transactions.length}</strong> —
          Apostas: <strong>{bets.length}</strong>.
        </div>
      )}
    </div>
  );
}

// ── Step 4: Decisão ──────────────────────────────────────────────────────────

const DISPOSITIONS = [
  {
    value:       'TRUE_POSITIVE',
    label:       'Confirmado: risco real',
    sub:         'A suspeita se justifica — vou abrir ou vincular um caso de investigação.',
    color:       'border-red-300 bg-red-50',
    activeColor: 'border-red-500 bg-red-100 ring-1 ring-red-400',
    icon:        ShieldAlert,
    iconCls:     'text-red-600',
  },
  {
    value:       'UNDER_REVIEW',
    label:       'Manter em análise',
    sub:         'Ainda preciso investigar mais antes de concluir.',
    color:       'border-yellow-300 bg-yellow-50',
    activeColor: 'border-yellow-500 bg-yellow-100 ring-1 ring-yellow-400',
    icon:        HelpCircle,
    iconCls:     'text-yellow-600',
  },
  {
    value:       'FALSE_POSITIVE',
    label:       'Falso positivo — descartar',
    sub:         'Revisão concluída: não há indícios de irregularidade.',
    color:       'border-green-300 bg-green-50',
    activeColor: 'border-green-500 bg-green-100 ring-1 ring-green-400',
    icon:        XCircle,
    iconCls:     'text-green-600',
  },
];

function StepDecision({
  disposition,
  setDisposition,
  note,
  setNote,
  openCase,
  setOpenCase,
}: {
  disposition: string;
  setDisposition: (v: string) => void;
  note: string;
  setNote: (v: string) => void;
  openCase: boolean;
  setOpenCase: (v: boolean) => void;
}) {
  return (
    <div className="space-y-5">
      <p className="text-sm text-gray-600">
        Baseado na revisão dos passos anteriores, registre aqui sua avaliação sobre este alerta.
      </p>

      <div className="space-y-3">
        {DISPOSITIONS.map((d) => {
          const Icon    = d.icon;
          const active  = disposition === d.value;
          return (
            <button
              key={d.value}
              type="button"
              onClick={() => setDisposition(d.value)}
              className={`w-full rounded-xl border p-4 text-left transition-all ${
                active ? d.activeColor : d.color
              }`}
            >
              <div className="flex items-start gap-3">
                <div className={`mt-0.5 shrink-0 ${d.iconCls}`}>
                  <Icon size={18} />
                </div>
                <div>
                  <p className="text-sm font-semibold text-gray-900">{d.label}</p>
                  <p className="mt-0.5 text-xs text-gray-600">{d.sub}</p>
                </div>
                <div className="ml-auto shrink-0">
                  <div className={`h-4 w-4 rounded-full border-2 transition-colors ${
                    active ? 'border-brand bg-brand' : 'border-gray-300'
                  }`} />
                </div>
              </div>
            </button>
          );
        })}
      </div>

      {/* Opção de criar caso */}
      {disposition === 'TRUE_POSITIVE' && (
        <label className="flex cursor-pointer items-center gap-3 rounded-xl border border-indigo-200 bg-indigo-50 px-4 py-3 transition-colors hover:bg-indigo-100">
          <input
            type="checkbox"
            checked={openCase}
            onChange={(e) => setOpenCase(e.target.checked)}
            className="h-4 w-4 accent-brand"
          />
          <div className="flex items-center gap-2 text-sm text-indigo-800">
            <FolderPlus size={15} />
            <span className="font-semibold">Abrir caso de investigação formal</span>
          </div>
        </label>
      )}

      {/* Observação */}
      <div>
        <label className="mb-1.5 block text-sm font-medium text-gray-700">
          Observação <span className="text-gray-400">(opcional)</span>
        </label>
        <textarea
          rows={4}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Descreva o que você encontrou e como chegou a essa conclusão..."
          className="w-full rounded-xl border border-gray-200 px-4 py-3 text-sm text-gray-800 focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand"
        />
        <p className="mt-1 text-xs text-gray-400">
          Este texto será salvo junto ao alerta e pode ser usado na narrativa de relatório regulatório.
        </p>
      </div>
    </div>
  );
}

// ── Tela de sucesso ──────────────────────────────────────────────────────────

function SuccessScreen({ caseId }: { caseId?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-5 py-16 text-center">
      <div className="flex h-16 w-16 items-center justify-center rounded-full bg-green-100">
        <CheckCircle2 size={32} className="text-green-600" />
      </div>
      <div>
        <h2 className="text-xl font-bold text-gray-900">Avaliação registrada!</h2>
        <p className="mt-2 max-w-sm text-sm text-gray-500">
          Sua análise foi salva com sucesso. O alerta foi atualizado conforme sua decisão.
        </p>
      </div>
      <div className="flex flex-wrap justify-center gap-3">
        {caseId && (
          <Link
            href={`/cases/${caseId}`}
            className="flex items-center gap-2 rounded-xl bg-brand px-5 py-2.5 text-sm font-semibold text-white shadow hover:opacity-90"
          >
            <FolderPlus size={15} />
            Abrir o caso criado
          </Link>
        )}
        <Link
          href="/alerts"
          className="flex items-center gap-2 rounded-xl border border-gray-200 bg-white px-5 py-2.5 text-sm font-semibold text-gray-700 hover:bg-gray-50"
        >
          Voltar aos alertas
        </Link>
      </div>
    </div>
  );
}

// ── Main ─────────────────────────────────────────────────────────────────────

export default function InvestigateWizard() {
  const { alertId } = useParams<{ alertId: string }>();
  const router      = useRouter();

  const [step, setStep]           = useState(1);
  const [disposition, setDisp]    = useState('');
  const [note, setNote]           = useState('');
  const [openCase, setOpenCase]   = useState(false);
  const [createdCaseId, setCaseId] = useState<string | undefined>();
  const [done, setDone]           = useState(false);

  // ── Data fetching ──────────────────────────────────────────────────────────
  const { data: alert, isLoading: loadingAlert } = useQuery<AlertDetail>({
    queryKey: ['alert', alertId],
    queryFn: () => fetchAlert(alertId),
    enabled: !!alertId,
  });

  const { data: explainability } = useQuery<AlertExplainability>({
    queryKey: ['alert-explain', alertId],
    queryFn: () => fetchAlertExplainability(alertId),
    enabled: !!alert && (!!alert.anomaly_score || alert.alert_type === 'ML_ANOMALY' || alert.alert_type === 'COMPOSITE'),
    retry: false,
  });

  const { data: player } = useQuery<PlayerDetail>({
    queryKey: ['player', alert?.player_id],
    queryFn: () => fetchPlayer(alert!.player_id!),
    enabled: !!alert?.player_id,
  });

  const { data: econCompat } = useQuery<EconCompat>({
    queryKey: ['econ-compat', alert?.player_id],
    queryFn: () => fetchPlayerEconCompat(alert!.player_id!),
    enabled: !!alert?.player_id,
    retry: false,
  });

  const { data: relatedTx } = useQuery<RelatedTransactions>({
    queryKey: ['alert-tx', alertId],
    queryFn: () => fetchAlertRelatedTransactions(alertId),
    enabled: !!alertId,
    retry: false,
  });

  // ── Mutations ──────────────────────────────────────────────────────────────
  const submit = useMutation({
    mutationFn: async () => {
      await triageAlert(alertId, disposition, note);

      if (openCase && disposition === 'TRUE_POSITIVE') {
        const newCase = await createCase({
          title: alert?.title ?? 'Caso aberto via investigação',
          description: note || undefined,
          player_id: alert?.player_id ?? undefined,
          severity: alert?.severity ?? 'HIGH',
        });
        await linkAlertToCase(newCase.id, alertId);
        setCaseId(newCase.id);
      }
    },
    onSuccess: () => setDone(true),
  });

  // ── Render ─────────────────────────────────────────────────────────────────
  if (loadingAlert) {
    return (
      <div className="flex h-64 items-center justify-center text-sm text-gray-400">
        Carregando alerta…
      </div>
    );
  }

  if (!alert) {
    return (
      <div className="p-8 text-center">
        <p className="text-sm text-red-500">Alerta não encontrado.</p>
        <button onClick={() => router.back()} className="mt-3 text-sm text-brand hover:underline">
          ← Voltar
        </button>
      </div>
    );
  }

  const totalSteps = STEPS.length;
  const canAdvance = step < totalSteps;
  const canBack    = step > 1;
  const isLast     = step === totalSteps;

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      {/* Breadcrumb */}
      <div className="flex items-center gap-2 text-xs text-gray-400">
        <Link href="/alerts" className="hover:text-brand hover:underline">Alertas</Link>
        <span>/</span>
        <Link href={`/alerts/${alertId}`} className="hover:text-brand hover:underline">
          {alert.id.slice(0, 8)}…
        </Link>
        <span>/</span>
        <span className="text-gray-600 font-medium">Investigação guiada</span>
      </div>

      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Investigação guiada</h1>
        <p className="mt-0.5 text-sm text-gray-500 truncate max-w-xl">{alert.title}</p>
      </div>

      {/* Stepper */}
      <Stepper current={step} />

      {/* Conteúdo do passo */}
      <div className="min-h-[360px]">
        {done ? (
          <SuccessScreen caseId={createdCaseId} />
        ) : (
          <>
            {step === 1 && (
              <StepAlert alert={alert} explainability={explainability} />
            )}
            {step === 2 && (
              <StepPlayer player={player} econCompat={econCompat} />
            )}
            {step === 3 && (
              <StepTransactions tx={relatedTx} />
            )}
            {step === 4 && (
              <StepDecision
                disposition={disposition}
                setDisposition={setDisp}
                note={note}
                setNote={setNote}
                openCase={openCase}
                setOpenCase={setOpenCase}
              />
            )}
          </>
        )}
      </div>

      {/* Navegação */}
      {!done && (
        <div className="flex items-center justify-between border-t border-gray-100 pt-5">
          <button
            onClick={canBack ? () => setStep((s) => s - 1) : () => router.back()}
            className="flex items-center gap-2 rounded-xl border border-gray-200 bg-white px-4 py-2.5 text-sm font-medium text-gray-600 hover:bg-gray-50 transition-colors"
          >
            <ArrowLeft size={15} />
            {canBack ? 'Passo anterior' : 'Voltar'}
          </button>

          {isLast ? (
            <button
              onClick={() => submit.mutate()}
              disabled={!disposition || submit.isPending}
              className="flex items-center gap-2 rounded-xl bg-brand px-6 py-2.5 text-sm font-semibold text-white shadow hover:opacity-90 disabled:opacity-40 transition-opacity"
            >
              <CheckCircle2 size={15} />
              {submit.isPending ? 'Salvando...' : 'Confirmar avaliação'}
            </button>
          ) : (
            <button
              onClick={() => setStep((s) => s + 1)}
              className="flex items-center gap-2 rounded-xl bg-brand px-5 py-2.5 text-sm font-semibold text-white shadow hover:opacity-90 transition-opacity"
            >
              Próximo passo
              <ArrowRight size={15} />
            </button>
          )}
        </div>
      )}

      {submit.isError && (
        <p className="rounded-lg bg-red-50 border border-red-200 px-4 py-2 text-sm text-red-600">
          Erro ao salvar avaliação. Verifique sua conexão e tente novamente.
        </p>
      )}
    </div>
  );
}
