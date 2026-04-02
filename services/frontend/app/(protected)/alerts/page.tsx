'use client';
import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useRouter } from 'next/navigation';
import { fetchAlerts, triageAlert, Alert } from '@/lib/api';
import {
  AlertTriangle, Eye, FolderPlus, X, ChevronRight,
  Clock, Filter, RefreshCw, HelpCircle, Glasses, Search,
} from 'lucide-react';

// ── Tradução de termos técnicos ────────────────────────────────────────────────
const SEV_PT: Record<string, string> = {
  CRITICAL: 'Crítico',
  HIGH:     'Alto',
  MEDIUM:   'Médio',
  LOW:      'Baixo',
};

const SEV_STYLES: Record<string, { card: string; badge: string; dot: string }> = {
  CRITICAL: {
    card:  'border-l-4 border-red-500 bg-white hover:bg-red-50',
    badge: 'bg-red-100 text-red-700 font-bold',
    dot:   'bg-red-500',
  },
  HIGH: {
    card:  'border-l-4 border-orange-400 bg-white hover:bg-orange-50',
    badge: 'bg-orange-100 text-orange-700 font-bold',
    dot:   'bg-orange-400',
  },
  MEDIUM: {
    card:  'border-l-4 border-yellow-400 bg-white hover:bg-yellow-50',
    badge: 'bg-yellow-100 text-yellow-700 font-semibold',
    dot:   'bg-yellow-400',
  },
  LOW: {
    card:  'border-l-4 border-green-400 bg-white hover:bg-green-50',
    badge: 'bg-green-100 text-green-700',
    dot:   'bg-green-400',
  },
};

const TYPE_EXPLAIN: Record<string, string> = {
  VELOCITY:       'Movimentação em velocidade incompatível com o perfil do cliente',
  STRUCTURING:    'Padrão de múltiplas operações menores para evitar controles',
  ML_ANOMALY:     'Comportamento divergente do padrão histórico detectado pelo sistema',
  PEP_EXPOSURE:   'Envolvimento com pessoa politicamente exposta ou jurisdição de risco',
  MULTI_ACCOUNT:  'Uso de múltiplas contas ou dispositivos em curto período',
  HIGH_RISK_CUST: 'Cliente classificado como perfil de alto risco',
  COMPOSITE:      'Combinação de múltiplos fatores de risco detectados simultaneamente',
};

const DISP_PT: Record<string, string> = {
  FALSE_POSITIVE: 'Falso positivo (descartar)',
  TRUE_POSITIVE:  'Confirmado como risco real',
  UNDER_REVIEW:   'Em análise (manter em aberto)',
};

function SeverityDot({ sev }: { sev: string }) {
  const style = SEV_STYLES[sev] ?? SEV_STYLES.LOW;
  return <span className={`inline-block h-2.5 w-2.5 rounded-full ${style.dot}`} />;
}

function SevBadge({ sev }: { sev: string }) {
  const style = SEV_STYLES[sev] ?? SEV_STYLES.LOW;
  return (
    <span className={`rounded px-2 py-0.5 text-xs ${style.badge}`}>
      {SEV_PT[sev] ?? sev}
    </span>
  );
}

function AlertCard({
  alert,
  onTriage,
  onOpen,
  onNewCase,
  onObserve,
  isObserving,
  onInvestigate,
}: {
  alert: Alert;
  onTriage: (a: Alert) => void;
  onOpen: (id: string) => void;
  onNewCase: (a: Alert) => void;
  onObserve: (id: string) => void;
  isObserving: boolean;
  onInvestigate: (id: string) => void;
}) {
  const style   = SEV_STYLES[alert.severity] ?? SEV_STYLES.LOW;
  const explain = TYPE_EXPLAIN[alert.alert_type] ?? `Tipo: ${alert.alert_type}`;
  const hasCase = !!alert.case_id;

  return (
    <div className={`rounded-xl border shadow-sm transition-all ${style.card}`}>
      <div className="p-4">
        {/* Cabeçalho */}
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-start gap-2.5 min-w-0">
            <SeverityDot sev={alert.severity} />
            <div className="min-w-0">
              <h3 className="text-sm font-semibold text-gray-900 truncate">{alert.title}</h3>
              <p className="mt-0.5 text-xs text-gray-400 font-mono">{alert.id.slice(0, 8)}</p>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <SevBadge sev={alert.severity} />
            {hasCase && (
              <span className="rounded bg-indigo-100 px-1.5 py-0.5 text-[10px] font-semibold text-indigo-700">
                Em caso
              </span>
            )}
          </div>
        </div>

        {/* Explicação em linguagem simples */}
        <div className="mt-3 rounded-lg bg-gray-50 px-3 py-2.5">
          <p className="flex items-start gap-1.5 text-xs text-gray-600">
            <HelpCircle size={12} className="mt-0.5 shrink-0 text-gray-400" />
            <span>{explain}</span>
          </p>
          {alert.anomaly_score != null && (
            <p className="mt-1 text-[10px] text-gray-400">
              Pontuação de risco: <span className="font-semibold text-gray-600">{(alert.anomaly_score * 100).toFixed(0)}%</span>
            </p>
          )}
        </div>

        {/* Rodapé: timestamp + ações */}
        <div className="mt-3 flex items-center justify-between">
          <p className="flex items-center gap-1 text-[10px] text-gray-400">
            <Clock size={10} />
            {new Date(alert.created_at).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })}
          </p>

          <div className="flex items-center gap-1.5">
            <button
              onClick={() => onOpen(alert.id)}
              className="flex items-center gap-1 rounded-lg border border-gray-200 bg-white px-2.5 py-1 text-xs text-gray-600 hover:bg-gray-50 transition-colors"
            >
              <Eye size={11} /> Ver detalhes
            </button>
            <button
              onClick={() => onInvestigate(alert.id)}
              className="flex items-center gap-1 rounded-lg border border-brand bg-brand/5 px-2.5 py-1 text-xs text-brand hover:bg-brand/10 transition-colors font-medium"
            >
              <Search size={11} /> Investigar
            </button>
            {!hasCase && (
              <button
                onClick={() => onNewCase(alert)}
                className="flex items-center gap-1 rounded-lg border border-indigo-200 bg-indigo-50 px-2.5 py-1 text-xs text-indigo-700 hover:bg-indigo-100 transition-colors"
              >
                <FolderPlus size={11} /> Abrir caso
              </button>
            )}
            {alert.status === 'OPEN' && (
              <button
                onClick={() => onObserve(alert.id)}
                disabled={isObserving}
                className="flex items-center gap-1 rounded-lg border border-teal-200 bg-teal-50 px-2.5 py-1 text-xs text-teal-700 hover:bg-teal-100 transition-colors disabled:opacity-50"
                title="Marcar como em observação (IN_REVIEW)"
              >
                <Glasses size={11} /> {isObserving ? '...' : 'Observar'}
              </button>
            )}
            <button
              onClick={() => onTriage(alert)}
              className="flex items-center gap-1 rounded-lg border border-gray-200 bg-white px-2.5 py-1 text-xs text-gray-600 hover:bg-gray-50 transition-colors"
            >
              <X size={11} /> Triagem
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Contador por prioridade ────────────────────────────────────────────────────
function PriorityTab({
  sev, count, active, onClick,
}: { sev: string; count: number; active: boolean; onClick: () => void }) {
  const color = {
    ALL:      active ? 'bg-brand text-white' : 'bg-gray-100 text-gray-600',
    CRITICAL: active ? 'bg-red-600 text-white' : 'bg-red-50 text-red-700',
    HIGH:     active ? 'bg-orange-500 text-white' : 'bg-orange-50 text-orange-700',
    MEDIUM:   active ? 'bg-yellow-500 text-white' : 'bg-yellow-50 text-yellow-700',
    LOW:      active ? 'bg-green-600 text-white' : 'bg-green-50 text-green-700',
  }[sev] ?? 'bg-gray-100 text-gray-600';

  return (
    <button
      onClick={onClick}
      className={`rounded-lg px-3 py-1.5 text-xs font-semibold transition-colors ${color}`}
    >
      {sev === 'ALL' ? 'Todos' : SEV_PT[sev]} {count > 0 && <span className="ml-1">({count})</span>}
    </button>
  );
}

// ── Main ─────────────────────────────────────────────────────────────────────
export default function AlertsPage() {
  const router = useRouter();
  const qc = useQueryClient();
  const [selected, setSelected] = useState<Alert | null>(null);
  const [sevFilter, setSevFilter] = useState<string>('ALL');
  const [statusFilter, setStatusFilter] = useState<string>('OPEN');
  const [note, setNote]         = useState('');
  const [disposition, setDisp]  = useState('');
  const [observingId, setObservingId] = useState<string | null>(null);

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['alerts', statusFilter],
    queryFn:  () => fetchAlerts(statusFilter ? { status: statusFilter, per_page: '200' } : { per_page: '200' }),
  });
  const allAlerts = data?.items ?? [];

  // Contadores por prioridade
  const counts: Record<string, number> = {
    ALL:      allAlerts.length,
    CRITICAL: allAlerts.filter((a) => a.severity === 'CRITICAL').length,
    HIGH:     allAlerts.filter((a) => a.severity === 'HIGH').length,
    MEDIUM:   allAlerts.filter((a) => a.severity === 'MEDIUM').length,
    LOW:      allAlerts.filter((a) => a.severity === 'LOW').length,
  };

  const filtered = sevFilter === 'ALL'
    ? allAlerts
    : allAlerts.filter((a) => a.severity === sevFilter);

  // Ordenar: CRITICAL primeiro, depois por data desc
  const sevOrder: Record<string, number> = { CRITICAL: 0, HIGH: 1, MEDIUM: 2, LOW: 3 };
  const sorted = [...filtered].sort((a, b) => {
    const sd = (sevOrder[a.severity] ?? 4) - (sevOrder[b.severity] ?? 4);
    if (sd !== 0) return sd;
    return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
  });

  const triage = useMutation({
    mutationFn: () => triageAlert(selected!.id, disposition, note),
    onSuccess:  () => {
      qc.invalidateQueries({ queryKey: ['alerts'] });
      setSelected(null);
      setNote('');
      setDisp('');
    },
  });

  const observe = useMutation({
    mutationFn: (id: string) => triageAlert(id, 'IN_REVIEW', ''),
    onMutate:   (id) => setObservingId(id),
    onSettled:  () => setObservingId(null),
    onSuccess:  () => qc.invalidateQueries({ queryKey: ['alerts'] }),
  });

  return (
    <div className="space-y-5">
      {/* Cabeçalho */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Monitor de Alertas</h1>
          <p className="mt-0.5 text-sm text-gray-500">
            Fila de situações que precisam da sua avaliação, ordenadas por prioridade.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select
            aria-label="Filtrar status dos alertas"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-600 shadow-sm"
          >
            <option value="OPEN">Aguardando análise</option>
            <option value="IN_REVIEW">Em revisão</option>
            <option value="">Todos os status</option>
            <option value="CLOSED">Fechados</option>
          </select>
          <button
            onClick={() => refetch()}
            className="flex items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs text-gray-500 hover:bg-gray-50 shadow-sm"
          >
            <RefreshCw size={12} /> Atualizar
          </button>
        </div>
      </div>

      {/* Tabs de prioridade */}
      <div className="flex items-center gap-2 flex-wrap">
        <Filter size={14} className="text-gray-400" />
        {['ALL', 'CRITICAL', 'HIGH', 'MEDIUM', 'LOW'].map((s) => (
          <PriorityTab
            key={s}
            sev={s}
            count={counts[s] ?? 0}
            active={sevFilter === s}
            onClick={() => setSevFilter(s)}
          />
        ))}
        <span className="ml-auto text-xs text-gray-400">
          {sorted.length} alerta{sorted.length !== 1 ? 's' : ''} na fila
        </span>
      </div>

      {/* Lista de alertas */}
      {isLoading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-24 rounded-xl bg-gray-100 animate-pulse" />
          ))}
        </div>
      ) : sorted.length === 0 ? (
        <div className="rounded-xl border border-dashed border-gray-200 bg-gray-50 py-16 text-center">
          <AlertTriangle size={32} className="mx-auto mb-3 text-gray-300" />
          <p className="text-sm font-medium text-gray-400">Nenhum alerta nessa fila</p>
          <p className="mt-1 text-xs text-gray-400">
            {sevFilter !== 'ALL' ? `Sem alertas ${SEV_PT[sevFilter]?.toLowerCase()} no momento.` : 'Tudo em dia!'}
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {sorted.map((a) => (
            <AlertCard
              key={a.id}
              alert={a}
              onOpen={(id) => router.push(`/alerts/${id}`)}
              onTriage={(alert) => { setSelected(alert); setDisp(''); setNote(''); }}
              onNewCase={(alert) => router.push(`/cases?linkAlert=${alert.id}`)}
              onObserve={(id) => observe.mutate(id)}
              isObserving={observingId === a.id}
              onInvestigate={(id) => router.push(`/investigate/${id}`)}
            />
          ))}
        </div>
      )}

      {/* Modal de triagem */}
      {selected && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-2xl">
            <div className="mb-4 flex items-start justify-between gap-2">
              <div>
                <h2 className="text-lg font-semibold">Registrar avaliação</h2>
                <p className="mt-0.5 text-xs text-gray-400 truncate max-w-xs">{selected.title}</p>
              </div>
              <button onClick={() => setSelected(null)} className="text-gray-300 hover:text-gray-500">
                <X size={18} />
              </button>
            </div>

            <div className="mb-2 rounded-lg bg-blue-50 border border-blue-100 px-3 py-2.5 text-xs text-blue-700">
              {TYPE_EXPLAIN[selected.alert_type] ?? `Tipo: ${selected.alert_type}`}
            </div>

            <label className="mt-3 mb-1 block text-sm font-medium text-gray-700">O que você concluiu?</label>
            <div className="space-y-2 mb-3">
              {Object.entries(DISP_PT).map(([val, label]) => (
                <label
                  key={val}
                  className={`flex cursor-pointer items-center gap-3 rounded-lg border px-3 py-2.5 transition-colors text-sm ${
                    disposition === val
                      ? 'border-brand bg-blue-50 text-brand'
                      : 'border-gray-100 hover:bg-gray-50 text-gray-700'
                  }`}
                >
                  <input
                    type="radio"
                    name="disposition"
                    value={val}
                    checked={disposition === val}
                    onChange={() => setDisp(val)}
                    className="accent-brand"
                  />
                  {label}
                </label>
              ))}
            </div>

            <label className="mb-1 block text-sm font-medium text-gray-700">
              Observação (opcional)
            </label>
            <textarea
              rows={3}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              className="mb-4 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand"
              placeholder="Descreva sua análise de forma breve..."
            />

            <div className="flex gap-3">
              <button
                onClick={() => triage.mutate()}
                disabled={!disposition || triage.isPending}
                className="flex-1 rounded-lg bg-brand py-2.5 text-sm font-medium text-white disabled:opacity-50 hover:opacity-90"
              >
                {triage.isPending ? 'Salvando...' : 'Confirmar avaliação'}
              </button>
              <button
                onClick={() => setSelected(null)}
                className="rounded-lg border border-gray-200 px-4 py-2.5 text-sm text-gray-500 hover:bg-gray-50"
              >
                Cancelar
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
