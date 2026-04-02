'use client';
import { useState } from 'react';
import { useQuery, useQueries, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  addCaseComment,
  assignCase,
  CaseDetail,
  fetchAdminUsers,
  fetchAlertRelatedTransactions,
  fetchCase,
  fetchCaseNarrativeSuggestion,
  fetchCaseReportPackages,
  fetchPlayer,
  fetchPlayerBetsChart,
  fetchPlayerCaseAlertHistory,
  fetchPlayerEconCompat,
  fetchPlayerPaymentInstruments,
  fetchPlayerNetwork,
  fetchPlayerTransactionsChart,
  generateReportPackage,
  linkAlertToCase,
  linkTransactionToCase,
  lookupCaseEntities,
  submitReportPackage,
  updateCaseStatus,
  SISCOAF_OCCURRENCE_CODES,
  SISCOAF_INVOLVEMENT_TYPES,
} from '@/lib/api';
import { useParams, useRouter } from 'next/navigation';
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend,
  LineChart, Line,
} from 'recharts';
import {
  ArrowLeft, AlertTriangle, Clock, User, TrendingDown,
  FileText, CheckCircle2, MessageSquare, Send, ChevronRight,
  Activity, HelpCircle, X, Network, CreditCard, History, ArrowRightLeft,
  Search,
} from 'lucide-react';
import PlayerNetworkGraph from '@/components/PlayerNetworkGraph';

// ── Helpers ───────────────────────────────────────────────────────────────────
const SEV_LABEL: Record<string, string> = {
  CRITICAL: 'Crítico', HIGH: 'Alto', MEDIUM: 'Médio', LOW: 'Baixo',
};
const SEV_CLS: Record<string, string> = {
  CRITICAL: 'bg-red-100 text-red-700 border-red-200',
  HIGH:     'bg-orange-100 text-orange-700 border-orange-200',
  MEDIUM:   'bg-yellow-100 text-yellow-700 border-yellow-200',
  LOW:      'bg-green-100 text-green-700 border-green-200',
};
const STATUS_CLS: Record<string, string> = {
  OPEN:           'bg-blue-100 text-blue-700',
  INVESTIGATING:  'bg-indigo-100 text-indigo-700',
  PENDING_REVIEW: 'bg-purple-100 text-purple-700',
  IN_REVIEW:      'bg-purple-100 text-purple-700',    // legacy
  UNDER_REVIEW:   'bg-purple-100 text-purple-700',    // legacy
  CLOSED:         'bg-gray-100 text-gray-500',
  REPORTED:       'bg-green-100 text-green-700',
};
const RISK_BAND_CLS: Record<string, string> = {
  HIGH:   'bg-red-100 text-red-700',
  MEDIUM: 'bg-yellow-100 text-yellow-700',
  LOW:    'bg-green-100 text-green-700',
};
const STATUS_PT: Record<string, string> = {
  OPEN:           'Aberto',
  INVESTIGATING:  'Investigando',
  PENDING_REVIEW: 'Aguarda Revisão',
  IN_REVIEW:      'Em revisão',
  CLOSED:         'Encerrado',
  REPORTED:       'Reportado ao COAF',
};
const ECON_CLS: Record<string, string> = {
  GREEN:   'bg-green-100 text-green-700',
  YELLOW:  'bg-yellow-100 text-yellow-700',
  RED:     'bg-red-100 text-red-700',
  UNKNOWN: 'bg-gray-100 text-gray-500',
};
const EVT_PT: Record<string, string> = {
  CREATED:            'Caso criado',
  ASSIGNED:           'Atribuído a analista',
  ASSIGNMENT:         'Atribuído a analista',
  COMMENTED:          'Comentário adicionado',
  COMMENT:            'Comentário adicionado',
  STATUS_CHANGED:     'Status atualizado',
  STATUS_CHANGE:      'Status atualizado',
  ALERT_LINKED:       'Alerta vinculado',
  REPORT_GENERATED:   'Relatório gerado',
  REPORT_SUBMITTED:   'Relatório submetido ao COAF',
  SYSTEM_AUTO_CREATED:'Auto-criado pelo sistema',
  EVIDENCE_UPLOAD:    'Evidência enviada',
};

const TRANSITIONS: Record<string, string[]> = {
  OPEN:           ['INVESTIGATING', 'CLOSED'],
  INVESTIGATING:  ['PENDING_REVIEW', 'CLOSED', 'OPEN'],
  PENDING_REVIEW: ['INVESTIGATING', 'CLOSED', 'REPORTED'],
  CLOSED:         ['OPEN'],
  REPORTED:       [],
};

// StatusTransitionSelect — small inline select to advance the case workflow
function StatusTransitionSelect({
  caseId, currentStatus, onSuccess,
}: {
  caseId: string;
  currentStatus: string;
  onSuccess: () => void;
}) {
  const qc = useQueryClient();
  const allowed = TRANSITIONS[currentStatus] ?? [];
  const transition = useMutation({
    mutationFn: (s: string) => updateCaseStatus(caseId, s),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['case', caseId] }); onSuccess(); },
  });
  if (!allowed.length) return null;
  return (
    <select
      aria-label="Mover status do caso"
      defaultValue=""
      onChange={(e) => { if (e.target.value) transition.mutate(e.target.value); }}
      disabled={transition.isPending}
      className="rounded-lg border border-gray-200 bg-white px-2 py-1 text-xs text-gray-700 focus:outline-none focus:ring-1 focus:ring-brand disabled:opacity-50"
    >
      <option value="" disabled>Mover para…</option>
      {allowed.map((s) => (
        <option key={s} value={s}>{STATUS_PT[s] ?? s}</option>
      ))}
    </select>
  );
}

function AssignCaseSelect({
  caseId,
  assignedTo,
}: {
  caseId: string;
  assignedTo?: string;
}) {
  const qc = useQueryClient();
  const { data: users = [] } = useQuery({
    queryKey: ['admin-users'],
    queryFn: fetchAdminUsers,
  });
  const assignMut = useMutation({
    mutationFn: (userId: string) => assignCase(caseId, userId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['case', caseId] }),
  });

  const assignees = users.filter((u) => u.active && ['ADMIN', 'AML_ANALYST'].includes(u.role));

  return (
    <select
      aria-label="Atribuir caso a analista"
      value={assignedTo ?? ''}
      onChange={(e) => { if (e.target.value) assignMut.mutate(e.target.value); }}
      disabled={assignMut.isPending}
      className="rounded-lg border border-gray-200 bg-white px-2 py-1 text-xs text-gray-700 focus:outline-none focus:ring-1 focus:ring-brand disabled:opacity-50"
    >
      <option value="">Atribuir…</option>
      {assignees.map((user) => (
        <option key={user.id} value={user.id}>
          {user.username} ({user.role})
        </option>
      ))}
    </select>
  );
}

function SLABadge({ sla_due_at }: { sla_due_at?: string }) {
  if (!sla_due_at) return null;
  const diff = new Date(sla_due_at).getTime() - Date.now();
  if (diff < 0)
    return <span className="rounded border bg-red-100 px-2 py-0.5 text-xs font-bold text-red-700 border-red-200">SLA VENCIDO</span>;
  const mins = Math.round(diff / 60000);
  const label = mins < 60 ? `SLA: ${mins}min` : `SLA: ${Math.round(mins / 60)}h`;
  const cls   = diff < 7200000 ? 'bg-orange-100 text-orange-700 border-orange-200' : 'bg-green-50 text-green-700 border-green-200';
  return <span className={`rounded border px-2 py-0.5 text-xs font-semibold ${cls}`}>{label}</span>;
}

type Tab = 'overview' | 'profile' | 'movements' | 'network' | 'decision';

const TABS: { id: Tab; label: string; icon: React.ElementType }[] = [
  { id: 'overview',   label: 'Visão Geral',       icon: Activity },
  { id: 'profile',    label: 'Perfil do Cliente', icon: User },
  { id: 'movements',  label: 'Movimentações',     icon: TrendingDown },
  { id: 'network',    label: 'Rede e Vínculos',   icon: Network },
  { id: 'decision',   label: 'Decisão e Relatório', icon: FileText },
];

// ── Tab: Visão Geral ──────────────────────────────────────────────────────────
function TabOverview({ c }: { c: CaseDetail }) {
  const [query, setQuery] = useState('');
  const qc = useQueryClient();
  const { data: lookup } = useQuery({
    queryKey: ['case-lookup', c.id, query],
    queryFn: () => lookupCaseEntities(c.id, query, 'all'),
    enabled: query.trim().length >= 2,
  });
  const linkAlertMut = useMutation({
    mutationFn: (alertId: string) => linkAlertToCase(c.id, alertId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['case', c.id] }),
  });
  const linkTxnMut = useMutation({
    mutationFn: (transactionId: string) => linkTransactionToCase(c.id, transactionId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['case', c.id] }),
  });

  return (
    <div className="space-y-5">
      {/* Resumo da suspeita */}
      <div className="rounded-xl border border-blue-100 bg-blue-50 p-5">
        <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold text-blue-800">
          <HelpCircle size={15} /> Por que esse caso existe?
        </h3>
        <ul className="space-y-1.5 text-sm text-blue-900">
          {c.alerts?.length > 0 && (
            <li className="flex items-start gap-2">
              <span className="mt-1 h-1.5 w-1.5 rounded-full bg-blue-500 shrink-0" />
              {c.alerts.length} alerta{c.alerts.length > 1 ? 's' : ''} vinculado{c.alerts.length > 1 ? 's' : ''},{' '}
              com severidade máxima: <strong>{SEV_LABEL[c.severity ?? 'LOW']}</strong>.
            </li>
          )}
          {c.auto_created && (
            <li className="flex items-start gap-2">
              <span className="mt-1 h-1.5 w-1.5 rounded-full bg-blue-500 shrink-0" />
              Criado automaticamente pelo sistema após detecção de risco elevado.
            </li>
          )}
          {c.player_id && (
            <li className="flex items-start gap-2">
              <span className="mt-1 h-1.5 w-1.5 rounded-full bg-blue-500 shrink-0" />
              Cliente vinculado ao caso — veja a aba "Perfil do Cliente".
            </li>
          )}
        </ul>
      </div>

      {/* Alertas vinculados */}
      {c.alerts?.length > 0 && (
        <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
          <h3 className="mb-3 text-sm font-semibold text-gray-700">
            Alertas que motivaram este caso
          </h3>
          <ul className="space-y-2">
            {c.alerts.map((a) => (
              <li key={a.id} className="flex items-center justify-between rounded-lg border border-gray-100 bg-gray-50 px-4 py-2.5 text-xs">
                <span className="font-mono text-gray-400">{a.id.slice(0, 8)}</span>
                <span className="flex-1 px-3 text-gray-700 truncate">{a.title}</span>
                <span className={`rounded border px-2 py-0.5 font-semibold ${SEV_CLS[a.severity] ?? 'bg-gray-100 text-gray-600'}`}>
                  {SEV_LABEL[a.severity] ?? a.severity}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Linha do tempo */}
      {c.timeline?.length > 0 && (
        <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
          <h3 className="mb-4 text-sm font-semibold text-gray-700">Linha do Tempo</h3>
          <ol className="relative border-l border-gray-200 pl-5 space-y-5">
            {c.timeline.map((ev) => (
              <li key={ev.id} className="relative">
                <div className="absolute -left-2.5 mt-1 h-4 w-4 rounded-full bg-brand border-2 border-white flex items-center justify-center">
                  <span className="h-1.5 w-1.5 rounded-full bg-white" />
                </div>
                <div>
                  <p className="text-xs font-semibold text-gray-800">
                    {EVT_PT[ev.event_type] ?? ev.event_type}
                  </p>
                  {!!ev.content?.comment && (
                    <p className="mt-0.5 text-xs text-gray-600 italic">"{String(ev.content.comment)}"</p>
                  )}
                  {ev.content && Object.keys(ev.content).length > 0 && !ev.content.comment && (
                    <p className="mt-0.5 text-[10px] text-gray-400 font-mono">
                      {JSON.stringify(ev.content).slice(0, 60)}
                    </p>
                  )}
                  <p className="mt-1 text-[10px] text-gray-400">
                    {new Date(ev.created_at).toLocaleString('pt-BR')}
                  </p>
                </div>
              </li>
            ))}
          </ol>
        </div>
      )}

      <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
        <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-gray-700">
          <Search size={14} className="text-gray-400" /> Busca rápida para vincular
        </h3>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Busque por título de alerta, id ou dados de transação..."
          className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand"
        />
        {query.trim().length >= 2 && (
          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            <div>
              <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-400">Alertas disponíveis</p>
              <div className="space-y-2">
                {(lookup?.alerts ?? []).map((alert) => (
                  <div key={alert.id} className="flex items-center justify-between rounded-lg border border-gray-100 px-3 py-2 text-xs">
                    <div className="min-w-0">
                      <p className="truncate font-medium text-gray-700">{alert.title}</p>
                      <p className="text-gray-400">{alert.id.slice(0, 8)}…</p>
                    </div>
                    <button
                      onClick={() => linkAlertMut.mutate(alert.id)}
                      className="rounded bg-brand px-2 py-1 font-semibold text-white disabled:opacity-50"
                      disabled={linkAlertMut.isPending}
                    >
                      Vincular
                    </button>
                  </div>
                ))}
                {!lookup?.alerts?.length && <p className="text-xs text-gray-400">Nenhum alerta encontrado.</p>}
              </div>
            </div>
            <div>
              <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-400">Transações do apostador</p>
              <div className="space-y-2">
                {(lookup?.transactions ?? []).map((tx) => (
                  <div key={tx.id} className="flex items-center justify-between rounded-lg border border-gray-100 px-3 py-2 text-xs">
                    <div className="min-w-0">
                      <p className="font-medium text-gray-700">{tx.type} · {tx.amount.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })}</p>
                      <p className="text-gray-400">{tx.id.slice(0, 8)}… · {new Date(tx.occurred_at).toLocaleString('pt-BR')}</p>
                    </div>
                    <button
                      onClick={() => linkTxnMut.mutate(tx.id)}
                      className="rounded border border-gray-300 px-2 py-1 font-semibold text-gray-700 disabled:opacity-50"
                      disabled={linkTxnMut.isPending}
                    >
                      Referenciar
                    </button>
                  </div>
                ))}
                {!lookup?.transactions?.length && <p className="text-xs text-gray-400">Nenhuma transação encontrada.</p>}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Tab: Perfil do Cliente ────────────────────────────────────────────────────
function TabProfile({ playerId }: { playerId: string | undefined }) {
  const { data: player, isLoading: loadingP } = useQuery({
    queryKey: ['player', playerId],
    queryFn:  () => fetchPlayer(playerId!),
    enabled:  !!playerId,
  });
  const { data: econ } = useQuery({
    queryKey: ['econ', playerId],
    queryFn:  () => fetchPlayerEconCompat(playerId!),
    enabled:  !!playerId,
  });
  const { data: txChartRes } = useQuery({
    queryKey: ['player-tx-chart', playerId],
    queryFn:  () => fetchPlayerTransactionsChart(playerId!),
    enabled:  !!playerId,
  });
  const txChart = txChartRes?.data ?? [];

  const { data: betChartRes } = useQuery({
    queryKey: ['player-bet-chart', playerId],
    queryFn:  () => fetchPlayerBetsChart(playerId!),
    enabled:  !!playerId,
  });
  const betChart = betChartRes?.data ?? [];

  const { data: instrumentsRes } = useQuery({
    queryKey: ['player-instruments', playerId],
    queryFn:  () => fetchPlayerPaymentInstruments(playerId!),
    enabled:  !!playerId,
  });
  const instruments = instrumentsRes?.instruments ?? [];

  const { data: networkRes } = useQuery({
    queryKey: ['player-network', playerId],
    queryFn:  () => fetchPlayerNetwork(playerId!),
    enabled:  !!playerId,
  });
  const network = networkRes?.related_players ?? [];
  const { data: caseHistory } = useQuery({
    queryKey: ['player-case-history', playerId],
    queryFn:  () => fetchPlayerCaseAlertHistory(playerId!),
    enabled:  !!playerId,
  });

  if (!playerId) return (
    <div className="rounded-xl border border-dashed border-gray-200 bg-gray-50 py-16 text-center">
      <User size={32} className="mx-auto mb-3 text-gray-300" />
      <p className="text-sm text-gray-400">Nenhum apostador vinculado a este caso.</p>
    </div>
  );

  if (loadingP) return <p className="text-sm text-gray-400 p-5">Carregando perfil...</p>;
  if (!player)  return <p className="text-sm text-red-500 p-5">Perfil não encontrado.</p>;

  return (
    <div className="space-y-5">
      {/* Dados cadastrais */}
      <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
        <h3 className="mb-4 text-sm font-semibold text-gray-700">Dados Cadastrais</h3>
        <dl className="grid grid-cols-2 gap-4 text-sm">
          <div>
            <dt className="text-xs text-gray-400">ID externo</dt>
            <dd className="font-mono font-medium">{player.external_player_id}</dd>
          </div>
          <div>
            <dt className="text-xs text-gray-400">CPF</dt>
            <dd className="font-mono font-medium">{player.cpf ?? '***.***.***-**'}</dd>
          </div>
          <div>
            <dt className="text-xs text-gray-400">Pontuação de risco</dt>
            <dd className="font-semibold">{(player.risk_score * 100).toFixed(0)}%</dd>
          </div>
          <div>
            <dt className="text-xs text-gray-400">Classificação de risco</dt>
            <dd>
              <span className={`rounded px-2 py-0.5 text-xs font-bold ${RISK_BAND_CLS[player.risk_band] ?? 'bg-gray-100 text-gray-600'}`}>
                {player.risk_band === 'HIGH' ? 'Alto risco' : player.risk_band === 'MEDIUM' ? 'Risco moderado' : 'Baixo risco'}
              </span>
            </dd>
          </div>
          <div>
            <dt className="text-xs text-gray-400">Pessoa Exposta Politicamente (PEP)</dt>
            <dd>
              {player.pep_flag
                ? <span className="rounded bg-red-100 px-2 py-0.5 text-xs font-bold text-red-700">Sim — PEP</span>
                : <span className="rounded bg-gray-100 px-2 py-0.5 text-xs text-gray-500">Não</span>
              }
            </dd>
          </div>
          {player.declared_income_monthly != null && (
            <div>
              <dt className="text-xs text-gray-400">Renda declarada (mensal)</dt>
              <dd className="font-medium">
                {player.declared_income_monthly.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })}
              </dd>
            </div>
          )}
        </dl>
      </div>

      {/* Compatibilidade econômica */}
      {econ && (
        <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
          <h3 className="mb-2 text-sm font-semibold text-gray-700">Compatibilidade Econômica</h3>
          <p className="mb-3 text-xs text-gray-500">
            Compara o volume de depósitos dos últimos 30 dias com a renda declarada do apostador.
          </p>
          <div className="mb-3 flex items-center gap-3">
            <span className={`rounded-full px-3 py-1 text-xs font-bold ${ECON_CLS[econ.tier]}`}>
              {econ.tier === 'GREEN' ? 'Compatível' : econ.tier === 'YELLOW' ? 'Atenção' : econ.tier === 'RED' ? 'Incompatível' : 'Sem dados'}
            </span>
            {econ.income_ratio_30d != null && (
              <span className="text-xs text-gray-500">
                Depósitos = <strong>{(econ.income_ratio_30d * 100).toFixed(0)}%</strong> da renda declarada
              </span>
            )}
          </div>
          <p className="text-xs text-gray-600 italic">"{econ.interpretation}"</p>
          <dl className="mt-3 grid grid-cols-2 gap-3 text-xs">
            <div>
              <dt className="text-gray-400">Depósitos 30d</dt>
              <dd className="font-semibold">{econ.deposit_sum_30d.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })}</dd>
            </div>
            <div>
              <dt className="text-gray-400">Limiar de atenção</dt>
              <dd className="font-semibold">{(econ.ratio_threshold * 100).toFixed(0)}% da renda</dd>
            </div>
          </dl>
        </div>
      )}

      {/* Histórico de Movimentações 90d */}
      {txChart.length > 0 && (
        <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
          <h3 className="mb-4 flex items-center gap-2 text-sm font-semibold text-gray-700">
            <ArrowRightLeft size={14} className="text-gray-400" /> Depósitos vs Saques — 90 dias
          </h3>
          <ResponsiveContainer width="100%" height={180}>
            <BarChart data={txChart} margin={{ top: 0, right: 0, left: 0, bottom: 0 }}>
              <XAxis dataKey="day" tick={{ fontSize: 10 }} tickFormatter={(v: string) => v.slice(5)} />
              <YAxis tick={{ fontSize: 10 }} width={56} tickFormatter={(v: number) => `R$${(v / 1000).toFixed(0)}k`} />
              <Tooltip formatter={(v: number) => v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })} />
              <Legend iconSize={10} wrapperStyle={{ fontSize: 11 }} />
              <Bar dataKey="deposit_sum" name="Depósitos" fill="#22c55e" radius={[2, 2, 0, 0]} />
              <Bar dataKey="withdrawal_sum" name="Saques" fill="#ef4444" radius={[2, 2, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Histórico de Apostas 90d */}
      {betChart.length > 0 && (
        <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
          <h3 className="mb-4 flex items-center gap-2 text-sm font-semibold text-gray-700">
            <Activity size={14} className="text-gray-400" /> Volume de Apostas — 90 dias
          </h3>
          <ResponsiveContainer width="100%" height={160}>
            <LineChart data={betChart} margin={{ top: 0, right: 0, left: 0, bottom: 0 }}>
              <XAxis dataKey="day" tick={{ fontSize: 10 }} tickFormatter={(v: string) => v.slice(5)} />
              <YAxis tick={{ fontSize: 10 }} width={56} tickFormatter={(v: number) => `R$${(v / 1000).toFixed(0)}k`} />
              <Tooltip formatter={(v: number) => v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })} />
              <Line type="monotone" dataKey="stake_sum" name="Apostas" stroke="#6366f1" strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Instrumentos de Pagamento */}
      {instruments.length > 0 && (
        <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-gray-700">
            <CreditCard size={14} className="text-gray-400" /> Instrumentos de Pagamento
          </h3>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-gray-100 text-left text-gray-400">
                  <th className="pb-2 pr-4">Instrumento</th>
                  <th className="pb-2 pr-4">Método</th>
                  <th className="pb-2 pr-4">1ª vez</th>
                  <th className="pb-2 pr-4">Última vez</th>
                  <th className="pb-2">Transações</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {instruments.map((inst, i) => (
                  <tr key={i} className="hover:bg-gray-50">
                    <td className="py-2 pr-4 font-mono text-gray-700">{inst.payment_instrument ?? '—'}</td>
                    <td className="py-2 pr-4 text-gray-500">{inst.payment_method ?? '—'}</td>
                    <td className="py-2 pr-4 text-gray-400">
                      {inst.first_seen ? new Date(inst.first_seen).toLocaleDateString('pt-BR') : '—'}
                    </td>
                    <td className="py-2 pr-4 text-gray-400">
                      {inst.last_seen ? new Date(inst.last_seen).toLocaleDateString('pt-BR') : '—'}
                    </td>
                    <td className="py-2 font-semibold text-gray-700">{inst.tx_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Rede de Relacionamentos */}
      {network.length > 0 && (
        <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-gray-700">
            <Network size={14} className="text-gray-400" /> Rede de Relacionamentos
            <span className="ml-auto rounded bg-orange-100 px-2 py-0.5 text-[10px] font-bold text-orange-700">
              {network.length} vínculo{network.length !== 1 ? 's' : ''}
            </span>
          </h3>
          <ul className="space-y-2">
            {network.map((item, i) => (
              <li key={i} className="flex items-center gap-3 rounded-lg border border-gray-100 bg-gray-50 px-3 py-2 text-xs">
                <span className="font-mono text-gray-600">{item.player_id.slice(0, 8)}…</span>
                <span className="flex flex-wrap gap-1">
                  {item.shared_by.map((s, j) => (
                    <span key={j} className="rounded bg-indigo-50 px-1.5 py-0.5 text-[10px] text-indigo-700">
                      {s.type}: {s.value}
                    </span>
                  ))}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Histórico de Casos e Alertas */}
      {caseHistory && (caseHistory.cases.length > 0 || caseHistory.alerts.length > 0) && (
        <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-gray-700">
            <History size={14} className="text-gray-400" /> Histórico do Cliente
          </h3>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <p className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-gray-400">
                Casos anteriores ({caseHistory.cases.length})
              </p>
              <ul className="space-y-1.5">
                {caseHistory.cases.slice(0, 5).map((ch) => (
                  <li key={ch.id} className="flex items-center justify-between rounded border border-gray-100 px-2 py-1.5 text-xs">
                    <span className="truncate text-gray-600">{ch.title}</span>
                    <span className={`ml-2 shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium ${STATUS_CLS[ch.status] ?? 'bg-gray-100 text-gray-500'}`}>
                      {STATUS_PT[ch.status] ?? ch.status}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <p className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-gray-400">
                Alertas recentes ({caseHistory.alerts.length})
              </p>
              <ul className="space-y-1.5">
                {caseHistory.alerts.slice(0, 5).map((a) => (
                  <li key={a.id} className="flex items-center justify-between rounded border border-gray-100 px-2 py-1.5 text-xs">
                    <span className="truncate text-gray-600">{a.title}</span>
                    <span className={`ml-2 shrink-0 rounded border px-1.5 py-0.5 text-[10px] font-semibold ${SEV_CLS[a.severity] ?? 'bg-gray-100 text-gray-500'}`}>
                      {SEV_LABEL[a.severity] ?? a.severity}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Tab: Movimentações ────────────────────────────────────────────────────────
function TabMovements({ alertIds }: { alertIds: string[] }) {
  const results = useQueries({
    queries: alertIds.map((id) => ({
      queryKey: ['related-txns', id],
      queryFn:  () => fetchAlertRelatedTransactions(id),
      enabled:  !!id,
    })),
  });

  const isLoading = results.some((r) => r.isLoading);
  const allLoaded = results.every((r) => !r.isLoading);

  // Merge + deduplicate transactions and bets across all alerts
  const seenTxn = new Set<string>();
  const seenBet = new Set<string>();
  const transactions: NonNullable<typeof results[0]['data']>['transactions'] = [];
  const bets:        NonNullable<typeof results[0]['data']>['bets'] = [];
  let windowHours = 0;

  for (const r of results) {
    if (!r.data) continue;
    windowHours = Math.max(windowHours, r.data.window_hours);
    for (const t of r.data.transactions) {
      if (!seenTxn.has(t.id)) { seenTxn.add(t.id); transactions.push(t); }
    }
    for (const b of r.data.bets) {
      if (!seenBet.has(b.id)) { seenBet.add(b.id); bets.push(b); }
    }
  }

  if (!alertIds.length) return (
    <div className="rounded-xl border border-dashed border-gray-200 bg-gray-50 py-16 text-center">
      <TrendingDown size={32} className="mx-auto mb-3 text-gray-300" />
      <p className="text-sm text-gray-400">Nenhum alerta vinculado para mostrar movimentações.</p>
    </div>
  );

  if (isLoading) return <p className="text-sm text-gray-400 p-5">Carregando movimentações...</p>;
  if (allLoaded && !transactions.length && !bets.length)
    return <p className="text-sm text-gray-400 p-5">Nenhuma movimentação encontrada no período.</p>;

  return (
    <div className="space-y-5">
      {/* Transações */}
      {transactions.length > 0 && (
        <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
          <h3 className="mb-3 text-sm font-semibold text-gray-700">
            Transações ({transactions.length}) — janela de {windowHours}h
          </h3>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-gray-100 text-left text-gray-400">
                  <th className="pb-2 pr-4">Tipo</th>
                  <th className="pb-2 pr-4">Valor</th>
                  <th className="pb-2 pr-4">Método</th>
                  <th className="pb-2 pr-4">Status</th>
                  <th className="pb-2">Data/Hora</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {transactions.map((t) => (
                  <tr key={t.id} className="hover:bg-gray-50">
                    <td className="py-2 pr-4 font-medium text-gray-700">{t.type}</td>
                    <td className="py-2 pr-4 font-mono font-semibold">
                      {t.amount.toLocaleString('pt-BR', { style: 'currency', currency: t.currency })}
                    </td>
                    <td className="py-2 pr-4 text-gray-500">{t.payment_method ?? '—'}</td>
                    <td className="py-2 pr-4">
                      <span className={`rounded px-1.5 py-0.5 font-semibold ${
                        t.status === 'COMPLETED' ? 'bg-green-100 text-green-700' : 'bg-yellow-100 text-yellow-700'
                      }`}>
                        {t.status}
                      </span>
                    </td>
                    <td className="py-2 text-gray-400">
                      {new Date(t.occurred_at).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Apostas */}
      {bets.length > 0 && (
        <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
          <h3 className="mb-3 text-sm font-semibold text-gray-700">
            Apostas ({bets.length}) — mesma janela
          </h3>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-gray-100 text-left text-gray-400">
                  <th className="pb-2 pr-4">Tipo</th>
                  <th className="pb-2 pr-4">Valor apostado</th>
                  <th className="pb-2 pr-4">Retorno</th>
                  <th className="pb-2 pr-4">Status</th>
                  <th className="pb-2">Data</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {bets.map((b) => (
                  <tr key={b.id} className="hover:bg-gray-50">
                    <td className="py-2 pr-4 font-medium text-gray-700">{b.bet_type}</td>
                    <td className="py-2 pr-4 font-mono font-semibold">
                      {b.stake_amount.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })}
                    </td>
                    <td className="py-2 pr-4 font-mono text-gray-500">
                      {b.actual_payout != null
                        ? b.actual_payout.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })
                        : '—'}
                    </td>
                    <td className="py-2 pr-4">
                      <span className={`rounded px-1.5 py-0.5 font-semibold ${
                        b.status === 'WON' ? 'bg-green-100 text-green-700' :
                        b.status === 'LOST' ? 'bg-red-50 text-red-500' :
                        'bg-yellow-50 text-yellow-700'
                      }`}>
                        {b.status}
                      </span>
                    </td>
                    <td className="py-2 text-gray-400">
                      {new Date(b.occurred_at).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

    </div>
  );
}

// ── Tab: Rede e Vínculos ──────────────────────────────────────────────────────
function TabNetwork({ playerId }: { playerId: string | undefined }) {
  const { data: networkRes, isLoading } = useQuery({
    queryKey: ['player-network', playerId],
    queryFn:  () => fetchPlayerNetwork(playerId!),
    enabled:  !!playerId,
  });

  const relatedPlayers = networkRes?.related_players ?? [];

  if (!playerId) return (
    <div className="rounded-xl border border-dashed border-gray-200 bg-gray-50 py-16 text-center">
      <Network size={32} className="mx-auto mb-3 text-gray-300" />
      <p className="text-sm text-gray-400">Nenhum apostador vinculado a este caso.</p>
    </div>
  );

  if (isLoading) return <p className="text-sm text-gray-400 p-5">Carregando vínculos...</p>;

  return (
    <div className="space-y-5">
      {/* Explicação */}
      <div className="rounded-xl border border-blue-100 bg-blue-50 p-4">
        <h3 className="mb-1 flex items-center gap-2 text-sm font-semibold text-blue-800">
          <Network size={14} /> O que é a rede de vínculos?
        </h3>
        <p className="text-xs text-blue-700 leading-relaxed">
          O sistema rastreia apostadores que compartilham o mesmo dispositivo ou chave Pix/conta.
          Vínculos não indicam ilicitude por si só, mas são relevantes para avaliar se há
          coordenação entre contas no esquema investigado.
        </p>
      </div>

      {/* Grafo visual */}
      {relatedPlayers.length > 0 ? (
        <>
          <PlayerNetworkGraph playerId={playerId} relatedPlayers={relatedPlayers} />

          {/* Tabela detalhada */}
          <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
            <h3 className="mb-3 text-sm font-semibold text-gray-700">
              Detalhamento dos vínculos ({relatedPlayers.length})
            </h3>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-gray-100 text-left text-gray-400">
                    <th className="pb-2 pr-4">ID do apostador vinculado</th>
                    <th className="pb-2 pr-4">Tipo de vínculo</th>
                    <th className="pb-2">Identificador compartilhado</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-50">
                  {relatedPlayers.map((item, i) =>
                    item.shared_by.map((link, j) => (
                      <tr key={`${i}-${j}`} className="hover:bg-gray-50">
                        {j === 0 && (
                          <td
                            className="py-2 pr-4 align-top font-mono text-gray-600"
                            rowSpan={item.shared_by.length}
                          >
                            {item.player_id.slice(0, 12)}…
                          </td>
                        )}
                        <td className="py-2 pr-4">
                          <span className={`rounded px-2 py-0.5 text-[10px] font-semibold ${
                            link.type === 'device'
                              ? 'bg-orange-100 text-orange-700'
                              : 'bg-blue-100 text-blue-700'
                          }`}>
                            {link.type === 'device' ? '📱 Dispositivo' : '🏦 Conta/PIX'}
                          </span>
                        </td>
                        <td className="py-2 font-mono text-gray-500">
                          {link.value.length > 20 ? `${link.value.slice(0, 20)}…` : link.value}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </>
      ) : (
        <div className="rounded-xl border border-dashed border-gray-200 bg-gray-50 py-16 text-center">
          <Network size={32} className="mx-auto mb-3 text-gray-300" />
          <p className="text-sm font-medium text-gray-500">Nenhum vínculo encontrado</p>
          <p className="mt-1 text-xs text-gray-400">
            Este apostador não compartilha dispositivo ou chave Pix/conta com outros apostadores no sistema.
          </p>
        </div>
      )}
    </div>
  );
}

// ── Tab: Decisão e Relatório ──────────────────────────────────────────────────
function TabDecision({ caseId, c, qc }: { caseId: string; c: CaseDetail; qc: ReturnType<typeof useQueryClient> }) {
  const [narrative, setNarrative] = useState('');
  const [decision, setDecision]   = useState<'FILE_SAR' | 'NO_ACTION' | 'PENDING'>('PENDING');
  const [rpResult, setRpResult]   = useState<{ report_package_id: string; pdf_path: string | null } | null>(null);
  const [suggestionError, setSuggestionError] = useState<string | null>(null);
  // Siscoaf 97 fields
  const [occurrenceCodes, setOccurrenceCodes]       = useState<number[]>([]);
  const [involvementTypes, setInvolvementTypes]     = useState<number[]>([49]);
  const [valorPremio, setValorPremio]               = useState<string>('0.00');
  const [valorApostas, setValorApostas]             = useState<string>('0.00');
  const [infoAdicionais, setInfoAdicionais]         = useState<string>('');

  const { data: reportPackages = [] } = useQuery({
    queryKey: ['case-report-packages', caseId],
    queryFn: () => fetchCaseReportPackages(caseId),
  });

  const reportMut = useMutation({
    mutationFn: () => generateReportPackage(caseId, {
      analyst_narrative: narrative,
      decision,
      occurrence_codes: occurrenceCodes,
      involvement_types: involvementTypes,
      valor_premio: parseFloat(valorPremio) || 0,
      valor_apostas: parseFloat(valorApostas) || 0,
      informacoes_adicionais: infoAdicionais || undefined,
    }),
    onSuccess: (res) => {
      setRpResult({ report_package_id: res.report_package_id, pdf_path: res.pdf_path });
      qc.invalidateQueries({ queryKey: ['case', caseId] });
      qc.invalidateQueries({ queryKey: ['case-report-packages', caseId] });
    },
  });
  const submitMut = useMutation({
    mutationFn: () => submitReportPackage(caseId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['case', caseId] });
      qc.invalidateQueries({ queryKey: ['case-report-packages', caseId] });
    },
  });

  const suggestNarrativeMut = useMutation({
    mutationFn: () => fetchCaseNarrativeSuggestion(caseId),
    onSuccess: (data) => {
      setSuggestionError(null);
      setNarrative((prev) => (prev.trim() ? `${prev}\n\n${data.suggested_narrative}` : data.suggested_narrative));
    },
    onError: () => {
      setSuggestionError('Não foi possível obter sugestão automática no momento.');
    },
  });

  return (
    <div className="space-y-5">
      {/* Checklist de investigação */}
      <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
        <h3 className="mb-3 text-sm font-semibold text-gray-700">Checklist de Investigação</h3>
        <p className="mb-4 text-xs text-gray-500">
          Certifique-se de ter verificado cada item antes de emitir a decisão final.
        </p>
        <div className="space-y-2">
          {[
            'Verifiquei o perfil cadastral e histórico do apostador',
            'Analisei as movimentações e apostas no período suspeito',
            'Avaliei se o volume é compatível com a renda declarada',
            'Verifiquei possível exposição a PEP ou jurisdição de risco',
            'Consultei listas de monitoramento internas',
            'Documentei as evidências e o raciocínio da análise',
          ].map((item, i) => (
            <ChecklistItem key={i} label={item} />
          ))}
        </div>
      </div>

      {/* Geração de relatório */}
      <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
        <h3 className="mb-3 text-sm font-semibold text-gray-700">Gerar Dossiê para Reporte</h3>
        {rpResult ? (
          <div className="rounded-lg border border-green-200 bg-green-50 p-4">
            <p className="font-semibold text-green-800">Relatório gerado com sucesso</p>
            <p className="mt-0.5 text-xs text-green-600 font-mono">{rpResult.report_package_id}</p>
            {rpResult.pdf_path && (
              <a
                href={`/api-proxy/cases/${caseId}/report-package/pdf?rp_id=${rpResult.report_package_id}`}
                target="_blank"
                rel="noopener noreferrer"
                className="mt-3 inline-flex items-center gap-1 rounded-lg bg-green-700 px-4 py-2 text-xs font-semibold text-white hover:bg-green-800"
              >
                ⬇ Baixar PDF (COAF)
              </a>
            )}
            {(c.status === 'CLOSED' || c.status === 'REPORTED') && (
              <a
                href={`/api-proxy/cases/${caseId}/report-package/coaf-xml?rp_id=${rpResult.report_package_id}`}
                target="_blank"
                rel="noopener noreferrer"
                className="mt-3 inline-flex items-center gap-1 rounded-lg border border-green-600 px-4 py-2 text-xs font-semibold text-green-700 hover:bg-green-50"
              >
                ⬇ Baixar XML (COAF Res. 36)
              </a>
            )}
          </div>
        ) : (
          <div className="space-y-4">
            <div>
              <label className="mb-1 block text-xs font-semibold text-gray-600">Decisão da investigação</label>
              <div className="space-y-2">
                {([
                  ['FILE_SAR',  'Comunicar ao COAF — indícios suficientes de LD/FT',   'border-red-200 bg-red-50 text-red-700'],
                  ['NO_ACTION', 'Arquivar — operação lícita ou sem indícios relevantes', 'border-green-200 bg-green-50 text-green-700'],
                  ['PENDING',   'Manter em análise — aguardar mais informações',         'border-yellow-200 bg-yellow-50 text-yellow-700'],
                ] as [typeof decision, string, string][]).map(([val, label, cls]) => (
                  <label
                    key={val}
                    className={`flex cursor-pointer items-center gap-3 rounded-lg border px-4 py-3 text-sm transition-colors ${
                      decision === val ? cls : 'border-gray-100 hover:bg-gray-50 text-gray-700'
                    }`}
                  >
                    <input
                      type="radio"
                      name="decision"
                      value={val}
                      aria-label={`Decisão no Report Package: ${val}`}
                      checked={decision === val}
                      onChange={() => setDecision(val)}
                      className="accent-brand"
                    />
                    {label}
                  </label>
                ))}
              </div>
            </div>

            <div>
              <label className="mb-1 block text-xs font-semibold text-gray-600">
                Narrativa analítica{' '}
                {decision === 'FILE_SAR' && <span className="text-red-500">— obrigatório para comunicação COAF</span>}
              </label>
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={() => suggestNarrativeMut.mutate()}
                  disabled={suggestNarrativeMut.isPending}
                  className="rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-1.5 text-xs font-semibold text-indigo-700 hover:bg-indigo-100 disabled:opacity-50"
                >
                  {suggestNarrativeMut.isPending ? 'Sugerindo...' : 'Sugerir narrativa inicial'}
                </button>
                {suggestionError && <span className="text-xs text-red-600">{suggestionError}</span>}
              </div>
              <textarea
                aria-label="Narrativa analítica do report package"
                rows={5}
                value={narrative}
                onChange={(e) => setNarrative(e.target.value)}
                placeholder="Descreva de forma objetiva: o que foi detectado, quais padrões são suspeitos e por quê a operação/apostador merece (ou não) ser reportada..."
                className="w-full rounded-lg border border-gray-200 px-3 py-2.5 text-sm focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand"
              />
            </div>

            {/* ── Siscoaf 97 — Campos obrigatórios (Portaria SPA/MF 1.143/2024) ── */}
            <div className={`space-y-4 rounded-xl border p-4 ${decision === 'FILE_SAR' ? 'border-red-200 bg-red-50' : 'border-gray-100 bg-gray-50'}`}>
              <div className="flex items-center gap-2">
                <span className="rounded bg-red-700 px-2 py-0.5 text-xs font-bold text-white">COAF</span>
                <h4 className="text-xs font-semibold text-gray-700">
                  Siscoaf — Portaria SPA/MF 1.143/2024 (Comunicado 97)
                  {decision === 'FILE_SAR' && <span className="ml-1 text-red-600">— campos obrigatórios</span>}
                </h4>
              </div>

              {/* Tabela de Ocorrências */}
              <div>
                <label className="mb-1.5 block text-xs font-semibold text-gray-600">
                  Códigos de Ocorrência Siscoaf
                  {decision === 'FILE_SAR' && <span className="ml-1 text-red-500">*</span>}
                </label>
                <div className="max-h-52 overflow-y-auto rounded-lg border border-gray-200 bg-white">
                  {Object.entries(SISCOAF_OCCURRENCE_CODES).map(([code, desc]) => {
                    const codeNum = Number(code);
                    return (
                      <label key={code} className="flex cursor-pointer items-start gap-2.5 border-b border-gray-50 px-3 py-2 hover:bg-gray-50 last:border-0">
                        <input
                          type="checkbox"
                          aria-label={`Código de ocorrência Siscoaf ${code}`}
                          checked={occurrenceCodes.includes(codeNum)}
                          onChange={(e) =>
                            setOccurrenceCodes(prev =>
                              e.target.checked
                                ? [...prev, codeNum]
                                : prev.filter(c => c !== codeNum)
                            )
                          }
                          className="mt-0.5 accent-red-700 shrink-0"
                        />
                        <span className="text-xs text-gray-700">
                          <span className="font-mono font-semibold text-gray-900">{code}</span>
                          {' — '}
                          {desc}
                        </span>
                      </label>
                    );
                  })}
                </div>
                {occurrenceCodes.length > 0 && (
                  <p className="mt-1 text-xs text-gray-500">{occurrenceCodes.length} código(s) selecionado(s)</p>
                )}
              </div>

              {/* Tipos de Envolvimento */}
              <div>
                <label className="mb-1.5 block text-xs font-semibold text-gray-600">Tipos de Envolvimento</label>
                <div className="flex flex-wrap gap-2">
                  {Object.entries(SISCOAF_INVOLVEMENT_TYPES).map(([code, desc]) => {
                    const codeNum = Number(code);
                    return (
                      <label key={code} className={`flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-1.5 text-xs transition-colors ${
                        involvementTypes.includes(codeNum)
                          ? 'border-red-200 bg-red-50 text-red-800 font-semibold'
                          : 'border-gray-200 hover:bg-gray-50 text-gray-700'
                      }`}>
                        <input
                          type="checkbox"
                          aria-label={`Tipo de envolvimento ${desc}`}
                          checked={involvementTypes.includes(codeNum)}
                          onChange={(e) =>
                            setInvolvementTypes(prev =>
                              e.target.checked
                                ? [...prev, codeNum]
                                : prev.filter(t => t !== codeNum)
                            )
                          }
                          className="accent-red-700"
                        />
                        <span className="font-mono">{code}</span> — {desc}
                      </label>
                    );
                  })}
                </div>
              </div>

              {/* Valores */}
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="mb-1 block text-xs font-semibold text-gray-600">
                    Valor do Prêmio (R$)
                    {decision === 'FILE_SAR' && <span className="ml-1 text-red-500">*</span>}
                  </label>
                  <input
                    type="number"
                    min="0"
                    step="0.01"
                    aria-label="Valor do prêmio"
                    value={valorPremio}
                    onChange={(e) => setValorPremio(e.target.value)}
                    className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:border-red-400 focus:outline-none"
                    placeholder="0.00"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs font-semibold text-gray-600">
                    Valor das Apostas (R$)
                    {decision === 'FILE_SAR' && <span className="ml-1 text-red-500">*</span>}
                  </label>
                  <input
                    type="number"
                    min="0"
                    step="0.01"
                    aria-label="Valor das apostas"
                    value={valorApostas}
                    onChange={(e) => setValorApostas(e.target.value)}
                    className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:border-red-400 focus:outline-none"
                    placeholder="0.00"
                  />
                </div>
              </div>

              {/* Informações Adicionais */}
              <div>
                <label className="mb-1 block text-xs font-semibold text-gray-600">
                  Informações Adicionais
                  {decision === 'FILE_SAR' && <span className="ml-1 text-red-500">* obrigatório (Siscoaf 97 — campo não pode ser nulo)</span>}
                </label>
                <textarea
                  aria-label="Informações adicionais Siscoaf"
                  rows={3}
                  value={infoAdicionais}
                  onChange={(e) => setInfoAdicionais(e.target.value)}
                  placeholder="Descreva detalhes adicionais específicos sobre os códigos de ocorrência selecionados..."
                  className="w-full rounded-lg border border-gray-200 px-3 py-2.5 text-sm focus:border-red-400 focus:outline-none focus:ring-1 focus:ring-red-200"
                />
              </div>
            </div>

            <button
              onClick={() => reportMut.mutate()}
              disabled={
                reportMut.isPending ||
                (decision === 'FILE_SAR' && (!narrative.trim() || occurrenceCodes.length === 0 || !infoAdicionais.trim()))
              }
              aria-label="Gerar dossiê"
              className="rounded-lg bg-brand px-5 py-2.5 text-sm font-semibold text-white hover:opacity-90 disabled:opacity-50 shadow-sm"
            >
              {reportMut.isPending ? 'Gerando dossiê...' : 'Gerar Dossiê'}
            </button>
            {decision === 'FILE_SAR' && occurrenceCodes.length === 0 && (
              <p className="text-xs text-red-600">Selecione ao menos um código de ocorrência Siscoaf para comunicar ao COAF.</p>
            )}
            {decision === 'FILE_SAR' && !infoAdicionais.trim() && (
              <p className="text-xs text-red-600">Informações Adicionais são obrigatórias (Comunicado Siscoaf 97).</p>
            )}
            {reportMut.isError && (
              <p className="text-xs text-red-600">Erro ao gerar relatório. Tente novamente.</p>
            )}
          </div>
        )}
      </div>

      <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
        <div className="mb-3 flex items-center justify-between gap-3">
          <h3 className="text-sm font-semibold text-gray-700">Histórico de ReportPackages</h3>
          {reportPackages.length > 0 && (
            <button
              onClick={() => submitMut.mutate()}
              disabled={submitMut.isPending}
              aria-label="Submeter último reporte"
              className="rounded-lg border border-green-300 px-3 py-1.5 text-xs font-semibold text-green-700 hover:bg-green-50 disabled:opacity-50"
            >
              {submitMut.isPending ? 'Submetendo…' : 'Submeter último reporte'}
            </button>
          )}
        </div>
        <div className="space-y-2">
          {reportPackages.map((rp) => (
            <div key={rp.id} className="flex flex-col gap-2 rounded-lg border border-gray-100 px-3 py-3 text-xs md:flex-row md:items-center md:justify-between">
              <div>
                <p className="font-mono text-gray-700">{rp.id.slice(0, 8)}…</p>
                <p className="mt-0.5 text-gray-400">
                  {new Date(rp.created_at).toLocaleString('pt-BR')} · {rp.status} · decisão {rp.decision ?? '—'}
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <a
                  href={`/api-proxy/cases/${caseId}/report-package/json?rp_id=${rp.id}`}
                  target="_blank"
                  rel="noreferrer"
                  className="rounded border border-gray-300 px-2 py-1 font-semibold text-gray-700 hover:bg-gray-50"
                >
                  JSON
                </a>
                {rp.pdf_available && (
                  <a
                    href={`/api-proxy/cases/${caseId}/report-package/pdf?rp_id=${rp.id}`}
                    target="_blank"
                    rel="noreferrer"
                    className="rounded border border-gray-300 px-2 py-1 font-semibold text-gray-700 hover:bg-gray-50"
                  >
                    PDF
                  </a>
                )}
                <a
                  href={`/api-proxy/cases/${caseId}/report-package/coaf-xml?rp_id=${rp.id}`}
                  target="_blank"
                  rel="noreferrer"
                  className="rounded border border-gray-300 px-2 py-1 font-semibold text-gray-700 hover:bg-gray-50"
                >
                  XML
                </a>
              </div>
            </div>
          ))}
          {!reportPackages.length && (
            <p className="text-xs text-gray-400">Nenhum report package gerado ainda.</p>
          )}
        </div>
      </div>
    </div>
  );
}

function ChecklistItem({ label }: { label: string }) {
  const [checked, setChecked] = useState(false);
  return (
    <label className={`flex cursor-pointer items-center gap-3 rounded-lg border px-3 py-2.5 text-sm transition-colors ${
      checked ? 'border-green-200 bg-green-50 text-green-700' : 'border-gray-100 text-gray-600 hover:bg-gray-50'
    }`}>
      <input
        type="checkbox"
        checked={checked}
        onChange={() => setChecked((v) => !v)}
        className="h-4 w-4 accent-green-600 rounded"
      />
      <span className={checked ? 'line-through opacity-60' : ''}>{label}</span>
      {checked && <CheckCircle2 size={14} className="ml-auto shrink-0 text-green-600" />}
    </label>
  );
}

// ── Sticky Annotation Bar ─────────────────────────────────────────────────────
function StickyAnnotations({ caseId }: { caseId: string }) {
  const qc = useQueryClient();
  const [open, setOpen]     = useState(false);
  const [text, setText]     = useState('');
  const [saving, setSaving] = useState(false);
  const [saved, setSaved]   = useState(false);
  const { data: users = [] } = useQuery({
    queryKey: ['admin-users'],
    queryFn: fetchAdminUsers,
  });

  async function submit() {
    if (!text.trim()) return;
    setSaving(true);
    try {
      const mentions = Array.from(new Set(
        [...text.matchAll(/@([a-zA-Z0-9_.-]+)/g)]
          .map((match) => match[1].toLowerCase())
          .map((username) => users.find((u) => u.username.toLowerCase() === username)?.id)
          .filter(Boolean),
      )) as string[];
      await addCaseComment(caseId, { content: text, mentions });
      setText('');
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
      qc.invalidateQueries({ queryKey: ['case', caseId] });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed bottom-0 left-60 right-0 z-40 border-t border-gray-200 bg-white shadow-lg">
      <div className="mx-auto max-w-5xl">
        {open ? (
          <div className="flex items-end gap-3 px-6 py-3">
            <MessageSquare size={16} className="mb-2.5 shrink-0 text-gray-400" />
            <textarea
              aria-label="Anotação do caso"
              rows={2}
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="Adicione uma anotação ao caso enquanto analisa... (Shift+Enter para nova linha)"
              className="flex-1 resize-none rounded-lg border border-gray-200 px-3 py-2 text-sm focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand"
              onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); } }}
            />
            <div className="flex items-center gap-2">
              {saved && <span className="text-xs text-green-600 font-medium">Salvo ✓</span>}
              <button
                onClick={submit}
                disabled={!text.trim() || saving}
                aria-label="Anotar"
                className="flex items-center gap-1.5 rounded-lg bg-brand px-3 py-2 text-xs font-semibold text-white disabled:opacity-50 hover:opacity-90"
              >
                <Send size={12} /> {saving ? '...' : 'Anotar'}
              </button>
              <button onClick={() => setOpen(false)} className="text-gray-400 hover:text-gray-600 p-1">
                <X size={16} />
              </button>
            </div>
          </div>
        ) : (
          <button
            onClick={() => setOpen(true)}
            aria-label="Clique para adicionar uma anotação ao caso"
            className="flex w-full items-center gap-2 px-6 py-2.5 text-xs text-gray-400 hover:bg-gray-50 transition-colors"
          >
            <MessageSquare size={14} />
            <span>Clique para adicionar uma anotação ao caso...</span>
            <ChevronRight size={12} className="ml-auto" />
          </button>
        )}
      </div>
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function CaseDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const qc     = useQueryClient();
  const [activeTab, setActiveTab] = useState<Tab>('overview');


  const { data, isLoading, error } = useQuery({
    queryKey: ['case', id],
    queryFn:  () => fetchCase(id),
    enabled:  !!id,
  });

  if (isLoading) return (
    <div className="flex items-center justify-center py-24">
      <div className="h-8 w-8 rounded-full border-4 border-brand border-t-transparent animate-spin" />
    </div>
  );
  if (error)  return <p className="text-sm text-red-600 p-5">Erro ao carregar caso.</p>;
  if (!data)  return null;

  const c = data as CaseDetail;

  return (
    <div className="pb-20">
      {/* Botão voltar */}
      <button
        onClick={() => router.back()}
        className="mb-4 flex items-center gap-1.5 text-sm text-brand hover:underline"
      >
        <ArrowLeft size={14} /> Voltar para Casos
      </button>

      {/* Cabeçalho do caso */}
      <div className="mb-5 rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="text-xs font-mono text-gray-400">{(c as unknown as Record<string, string>).reference_number}</p>
            <h1 className="mt-0.5 text-xl font-bold text-gray-900">{c.title}</h1>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {c.auto_created && (
              <span className="rounded border border-indigo-200 bg-indigo-50 px-2 py-0.5 text-xs font-semibold text-indigo-700">
                AUTO-CRIADO
              </span>
            )}
            {c.severity && (
              <span className={`rounded border px-2 py-0.5 text-xs font-semibold ${SEV_CLS[c.severity] ?? 'bg-gray-100'}`}>
                {SEV_LABEL[c.severity] ?? c.severity}
              </span>
            )}
            <span className={`rounded px-2 py-0.5 text-xs font-semibold ${STATUS_CLS[c.status] ?? 'bg-gray-100'}`}>
              {STATUS_PT[c.status] ?? c.status}
            </span>
            <SLABadge sla_due_at={c.sla_due_at} />
            <StatusTransitionSelect
              caseId={id}
              currentStatus={c.status}
              onSuccess={() => {}}
            />
            <AssignCaseSelect
              caseId={id}
              assignedTo={c.assigned_to}
            />
          </div>
        </div>

        <dl className="mt-4 grid grid-cols-2 gap-3 text-xs sm:grid-cols-4">
          <div>
            <dt className="text-gray-400">Atribuído a</dt>
            <dd className="mt-0.5 font-medium text-gray-700">{c.assigned_to ?? '—'}</dd>
          </div>
          <div>
            <dt className="text-gray-400">Criado em</dt>
            <dd className="mt-0.5 font-medium text-gray-700">{new Date(c.created_at).toLocaleString('pt-BR')}</dd>
          </div>
          <div>
            <dt className="text-gray-400">Alertas vinculados</dt>
            <dd className="mt-0.5 font-medium text-gray-700">{c.alerts?.length ?? 0}</dd>
          </div>
          {c.player_id && (
            <div>
              <dt className="text-gray-400">Cliente</dt>
              <dd className="mt-0.5">
                <button
                  onClick={() => router.push(`/players/${c.player_id}`)}
                  className="flex items-center gap-1 font-mono text-brand hover:underline"
                >
                  {c.player_id.slice(0, 8)}… <ChevronRight size={10} />
                </button>
              </dd>
            </div>
          )}
        </dl>
      </div>

      {/* Abas de investigação */}
      <div className="mb-5 flex gap-1 rounded-xl border border-gray-200 bg-gray-50 p-1">
        {TABS.map(({ id: tabId, label, icon: Icon }) => (
          <button
            key={tabId}
            onClick={() => setActiveTab(tabId)}
            className={`flex flex-1 items-center justify-center gap-2 rounded-lg px-3 py-2 text-xs font-semibold transition-all ${
              activeTab === tabId
                ? 'bg-white text-brand shadow-sm'
                : 'text-gray-500 hover:text-gray-700'
            }`}
          >
            <Icon size={13} />
            <span className="hidden sm:inline">{label}</span>
          </button>
        ))}
      </div>

      {/* Conteúdo da aba */}
      {activeTab === 'overview'   && <TabOverview c={c} />}
      {activeTab === 'profile'    && <TabProfile playerId={c.player_id} />}
      {activeTab === 'movements'  && <TabMovements alertIds={c.alerts?.map((a) => a.id) ?? []} />}
      {activeTab === 'network'    && <TabNetwork playerId={c.player_id} />}
      {activeTab === 'decision'   && <TabDecision caseId={id} c={c} qc={qc} />}

      {/* Barra fixa de anotações */}
      <StickyAnnotations caseId={id} />
    </div>
  );
}
