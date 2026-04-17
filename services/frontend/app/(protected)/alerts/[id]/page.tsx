'use client';
import { useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  AlertDetail,
  AlertExplainability,
  Case,
  RelatedTransactions,
  closeAlert,
  fetchAlert,
  fetchAlertExplainability,
  fetchAlertRelatedTransactions,
  fetchCases,
  labelAlert,
  linkAlertToCase,
  triageAlert,
  type AlertTriageDisposition,
} from '@/lib/api';

const SEV_BADGE: Record<string, string> = {
  CRITICAL: 'bg-red-100 text-red-700 border border-red-200',
  HIGH:     'bg-orange-100 text-orange-700 border border-orange-200',
  MEDIUM:   'bg-yellow-100 text-yellow-700 border border-yellow-200',
  LOW:      'bg-green-100 text-green-700 border border-green-200',
};

const STATUS_BADGE: Record<string, string> = {
  OPEN:      'bg-blue-100 text-blue-700',
  IN_REVIEW: 'bg-purple-100 text-purple-700',
  CLOSED:    'bg-gray-100 text-gray-500',
};

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mb-6 rounded-xl border bg-white p-5 shadow-sm">
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-gray-500">{title}</h2>
      {children}
    </div>
  );
}

function KV({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-xs text-gray-400">{label}</span>
      <span className="text-sm font-medium text-gray-800">{value ?? '—'}</span>
    </div>
  );
}

export default function AlertDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router  = useRouter();
  const qc      = useQueryClient();

  const [showTriage, setShowTriage]    = useState(false);
  const [showLink, setShowLink]        = useState(false);
  const [disposition, setDisp]         = useState<AlertTriageDisposition | ''>('');
  const [note, setNote]                = useState('');
  const [selectedCase, setSelectedCase] = useState('');
  const [labelValue, setLabelValue] = useState<'TRUE_POSITIVE' | 'FALSE_POSITIVE' | 'NEED_REVIEW'>('NEED_REVIEW');
  const [labelNote, setLabelNote] = useState('');

  const { data: alert, isLoading, error } = useQuery<AlertDetail>({
    queryKey: ['alert', id],
    queryFn:  () => fetchAlert(id),
    enabled:  !!id,
  });

  const { data: casesData } = useQuery({
    queryKey: ['cases'],
    queryFn:  () => fetchCases(),
    enabled:  showLink,
  });
  const { data: explainability } = useQuery<AlertExplainability>({
    queryKey: ['alert-explainability', id],
    queryFn: () => fetchAlertExplainability(id),
    enabled: !!id && !!alert && (alert.alert_type === 'COMPOSITE' || alert.alert_type === 'ANOMALY' || !!alert.anomaly_score),
    retry: false,
  });
  const cases: Case[] = (casesData as Case[] | undefined) ?? [];

  const triage = useMutation({
    mutationFn: () => {
      if (!disposition) {
        throw new Error('Selecione uma disposição antes de concluir a triagem.');
      }
      return triageAlert(id, disposition, note);
    },
    onSuccess:  () => {
      qc.invalidateQueries({ queryKey: ['alert', id] });
      qc.invalidateQueries({ queryKey: ['alerts'] });
      setShowTriage(false);
    },
  });

  const link = useMutation({
    mutationFn: () => linkAlertToCase(selectedCase, id),
    onSuccess:  () => {
      qc.invalidateQueries({ queryKey: ['alert', id] });
      setShowLink(false);
    },
  });

  const close = useMutation({
    mutationFn: () => closeAlert(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['alert', id] });
      qc.invalidateQueries({ queryKey: ['alerts'] });
    },
  });

  const label = useMutation({
    mutationFn: () => labelAlert(id, labelValue, labelNote || undefined),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['alert', id] });
      qc.invalidateQueries({ queryKey: ['alerts'] });
    },
  });

  const { data: relatedTx } = useQuery<RelatedTransactions>({
    queryKey: ['alert-tx', id],
    queryFn: () => fetchAlertRelatedTransactions(id),
    enabled: !!id,
    retry: false,
  });

  if (isLoading) {
    return <div className="p-8 text-center text-gray-400">Carregando alerta...</div>;
  }
  if (error || !alert) {
    return (
      <div className="p-8 text-center">
        <p className="text-red-600">Alerta não encontrado.</p>
        <button onClick={() => router.back()} className="mt-3 text-sm text-brand hover:underline">
          ← Voltar
        </button>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-4xl">
      {/* Header */}
      <div className="mb-6 flex items-start justify-between">
        <div>
          <button onClick={() => router.back()} className="mb-2 text-xs text-gray-400 hover:underline">
            ← Alertas
          </button>
          <h1 className="text-2xl font-bold text-gray-900">{alert.title}</h1>
          <p className="mt-1 text-sm text-gray-500">#{alert.id.slice(0, 8)}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <span className={`rounded-full px-3 py-1 text-xs font-semibold ${SEV_BADGE[alert.severity] ?? 'bg-gray-100'}`}>
            {alert.severity}
          </span>
          <span className={`rounded-full px-3 py-1 text-xs font-semibold ${STATUS_BADGE[alert.status] ?? 'bg-gray-100'}`}>
            {alert.status}
          </span>
          <a
            href={`/investigate/${alert.id}`}
            className="flex items-center gap-1.5 rounded-full bg-brand px-3 py-1 text-xs font-semibold text-white hover:opacity-90"
          >
            🔍 Investigar passo a passo
          </a>
        </div>
      </div>

      {/* Metadados */}
      <Section title="Informações do Alerta">
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
          <KV label="Tipo de alerta" value={alert.alert_type} />
          <KV label="Criado em" value={new Date(alert.created_at).toLocaleString('pt-BR')} />
          <KV label="Cliente" value={
            alert.player_id
              ? <a href={`/players/${alert.player_id}`} className="text-brand hover:underline font-mono text-xs">{alert.player_id.slice(0, 8)}…</a>
              : null
          } />
          <KV label="Regra disparada" value={alert.rule_id ? <span className="font-mono text-xs">{alert.rule_id.slice(0, 8)}…</span> : null} />
          <KV label="Pontuação de risco" value={alert.anomaly_score != null ? `${(alert.anomaly_score * 100).toFixed(1)}%` : null} />
          <KV label="Pontuação composta" value={alert.composite_score != null ? `${(alert.composite_score * 100).toFixed(1)}%` : null} />
          {alert.case_id && (
            <KV label="Caso vinculado" value={
              <a href={`/cases/${alert.case_id}`} className="text-brand hover:underline font-mono text-xs">
                {alert.case_reference_number ?? `${alert.case_id.slice(0, 8)}…`}
              </a>
            } />
          )}
          {alert.triaged_at && (
            <KV label="Triado em" value={new Date(alert.triaged_at).toLocaleString('pt-BR')} />
          )}
          {alert.label && <KV label="Label" value={alert.label} />}
        </div>
        {alert.description && (
          <p className="mt-4 rounded-lg bg-gray-50 p-3 text-sm text-gray-600">{alert.description}</p>
        )}
      </Section>

      {/* Evidências */}
      <Section title="Evidências">
        {alert.evidence && Object.keys(alert.evidence).length > 0 ? (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            {Object.entries(alert.evidence).map(([k, v]) => (
              <div key={k} className="rounded-lg bg-gray-50 p-3">
                <div className="mb-0.5 text-xs font-medium text-gray-400">{k}</div>
                <div className="text-sm font-semibold text-gray-800 break-all">
                  {typeof v === 'object' ? JSON.stringify(v) : String(v)}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-gray-400">Sem evidências registradas.</p>
        )}
      </Section>

      {/* Transações relacionadas */}
      {relatedTx && (relatedTx.transactions.length > 0 || relatedTx.bets.length > 0) && (
        <Section title={`Movimentações no período (janela: ${relatedTx.window_hours}h)`}>
          {relatedTx.transactions.length > 0 && (
            <div className="mb-4">
              <p className="mb-2 text-xs font-semibold text-gray-500">Transações ({relatedTx.transactions.length})</p>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b text-left text-gray-400">
                      <th className="py-1.5 pr-3">Tipo</th><th className="py-1.5 pr-3">Valor</th>
                      <th className="py-1.5 pr-3">Status</th><th className="py-1.5">Data</th>
                    </tr>
                  </thead>
                  <tbody>
                    {relatedTx.transactions.map((t) => (
                      <tr key={t.id} className="border-b border-gray-50">
                        <td className="py-1.5 pr-3 font-medium text-gray-700">{t.type}</td>
                        <td className={`py-1.5 pr-3 font-semibold ${
                          t.type === 'WITHDRAWAL' ? 'text-red-600' : 'text-green-700'
                        }`}>
                          {t.amount.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })}
                        </td>
                        <td className="py-1.5 pr-3 text-gray-500">{t.status}</td>
                        <td className="py-1.5 text-gray-400">
                          {new Date(t.occurred_at).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
          {relatedTx.bets.length > 0 && (
            <div>
              <p className="mb-2 text-xs font-semibold text-gray-500">Apostas ({relatedTx.bets.length})</p>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b text-left text-gray-400">
                      <th className="py-1.5 pr-3">Tipo</th><th className="py-1.5 pr-3">Apostado</th>
                      <th className="py-1.5 pr-3">Ganho</th><th className="py-1.5">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {relatedTx.bets.map((b) => (
                      <tr key={b.id} className="border-b border-gray-50">
                        <td className="py-1.5 pr-3 font-medium text-gray-700">{b.bet_type}</td>
                        <td className="py-1.5 pr-3 text-gray-700">
                          {b.stake_amount.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })}
                        </td>
                        <td className={`py-1.5 pr-3 font-semibold ${
                          b.actual_payout != null && b.actual_payout > b.stake_amount ? 'text-green-700' : 'text-gray-500'
                        }`}>
                          {b.actual_payout != null
                            ? b.actual_payout.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })
                            : '—'}
                        </td>
                        <td className="py-1.5 text-gray-500">{b.status}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </Section>
      )}

      {/* Score Breakdown — oculto por padrão */}
      {alert.score_breakdown && Object.keys(alert.score_breakdown).length > 0 && (
        <details className="rounded-xl border border-gray-200">
          <summary className="cursor-pointer px-5 py-3 text-sm font-medium text-gray-500 hover:bg-gray-50">
            Detalhes técnicos de pontuação
          </summary>
          <div className="px-5 pb-4">
            <pre className="overflow-x-auto rounded-lg bg-gray-50 p-3 text-xs text-gray-700">
              {JSON.stringify(alert.score_breakdown, null, 2)}
            </pre>
          </div>
        </details>
      )}

      {explainability && explainability.top_features.length > 0 && (
        <Section title="Explicabilidade ML">
          <div className="mb-4 grid gap-3 sm:grid-cols-3">
            <div className="rounded-lg bg-gray-50 p-3">
              <div className="text-xs text-gray-400">Modelo</div>
              <div className="text-sm font-semibold text-gray-800">{explainability.model_id?.slice(0, 8) ?? '—'}</div>
            </div>
            <div className="rounded-lg bg-gray-50 p-3">
              <div className="text-xs text-gray-400">Método</div>
              <div className="text-sm font-semibold text-gray-800">{explainability.explanation_method}</div>
            </div>
            <div className="rounded-lg bg-gray-50 p-3">
              <div className="text-xs text-gray-400">Anomaly score</div>
              <div className="text-sm font-semibold text-gray-800">{explainability.anomaly_score.toFixed(4)}</div>
            </div>
          </div>

          <div className="space-y-3">
            {explainability.top_features.map((item) => {
              const magnitude = Math.min(100, Math.abs(item.contribution) * 100);
              const positive = item.contribution >= 0;
              return (
                <div key={item.feature} className="rounded-lg border border-gray-100 p-3">
                  <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                    <div>
                      <div className="text-sm font-semibold text-gray-900">{item.feature}</div>
                      <div className="text-xs text-gray-500">
                        valor atual: {String(item.current_value ?? '—')}
                        {item.baseline_value !== null && item.baseline_value !== undefined && ` · baseline: ${item.baseline_value}`}
                        {item.delta !== null && item.delta !== undefined && ` · delta: ${item.delta}`}
                      </div>
                    </div>
                    <div className={`text-sm font-semibold ${positive ? 'text-red-600' : 'text-green-600'}`}>
                      contribuição {item.contribution.toFixed(4)}
                    </div>
                  </div>
                  <div className="mt-2 h-2 rounded-full bg-gray-100">
                    <div
                      className={`h-2 rounded-full ${positive ? 'bg-red-500' : 'bg-green-500'}`}
                      style={{ width: `${magnitude}%` }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </Section>
      )}

      {/* Ações */}
      <div className="flex flex-wrap gap-3">
        {alert.status !== 'CLOSED' && (
          <button
            onClick={() => setShowTriage(true)}
            className="rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand/90"
          >
            Triagem
          </button>
        )}
        {alert.status !== 'CLOSED' && (
          <button
            onClick={() => {
              if (!window.confirm('Confirma fechamento do alerta?')) return;
              close.mutate();
            }}
            disabled={close.isPending}
            className="rounded-lg border border-gray-300 px-4 py-2 text-sm font-semibold text-gray-700 hover:bg-gray-50 disabled:opacity-50"
          >
            {close.isPending ? 'Fechando...' : 'Fechar Alerta'}
          </button>
        )}
        {!alert.case_id && (
          <button
            onClick={() => setShowLink(true)}
            className="rounded-lg border border-brand px-4 py-2 text-sm font-semibold text-brand hover:bg-brand/5"
          >
            Vincular a Caso
          </button>
        )}
        {alert.case_id && (
          <a
            href={`/cases/${alert.case_id}`}
            aria-label={alert.case_reference_number ? `Ver Caso ${alert.case_reference_number}` : 'Ver Caso'}
            className="rounded-lg border px-4 py-2 text-sm font-semibold text-gray-700 hover:bg-gray-50"
          >
            {alert.case_reference_number ? `Ver Caso ${alert.case_reference_number}` : 'Ver Caso'}
          </a>
        )}
      </div>

      <Section title="Marcar qualidade para treinamento do modelo">
        <div className="grid gap-3 md:grid-cols-[220px_1fr_auto]">
          <select
            value={labelValue}
            onChange={(e) => setLabelValue(e.target.value as 'TRUE_POSITIVE' | 'FALSE_POSITIVE' | 'NEED_REVIEW')}
            className="rounded-lg border border-gray-200 px-3 py-2 text-sm"
          >
            <option value="TRUE_POSITIVE">Risco real (verdadeiro positivo)</option>
            <option value="FALSE_POSITIVE">Falso positivo</option>
            <option value="NEED_REVIEW">Precisa revisar</option>
          </select>
          <input
            value={labelNote}
            onChange={(e) => setLabelNote(e.target.value)}
            placeholder="Nota opcional para feedback do modelo"
            className="rounded-lg border border-gray-200 px-3 py-2 text-sm"
          />
          <button
            onClick={() => label.mutate()}
            disabled={label.isPending}
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
          >
            {label.isPending ? 'Aplicando...' : 'Aplicar Label'}
          </button>
        </div>
        {label.isSuccess && (
          <p className="mt-2 text-xs text-green-700">Label atualizado com sucesso.</p>
        )}
        {label.isError && (
          <p className="mt-2 text-xs text-red-700">Falha ao aplicar label.</p>
        )}
      </Section>

      {/* Modal: Triagem */}
      {showTriage && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-xl">
            <h2 className="mb-4 text-lg font-semibold">Triagem do Alerta</h2>

            <label className="mb-1 block text-sm font-medium">Disposição</label>
            <select
              aria-label="Disposição da triagem"
              value={disposition}
              onChange={(e) => setDisp(e.target.value as AlertTriageDisposition | '')}
              className="mb-3 w-full rounded-lg border px-3 py-2 text-sm"
            >
              <option value="">Selecione...</option>
              <option value="FALSE_POSITIVE">False Positive</option>
              <option value="CONFIRMED">Confirmado</option>
              <option value="IN_REVIEW">Em Análise</option>
            </select>

            <label className="mb-1 block text-sm font-medium">Observação</label>
            <textarea
              aria-label="Observação da triagem"
              rows={3}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              className="mb-4 w-full rounded-lg border px-3 py-2 text-sm"
              placeholder="Justificativa..."
            />
            <div className="flex gap-3">
              <button
                onClick={() => triage.mutate()}
                disabled={!disposition || triage.isPending}
                aria-label="Confirmar triagem"
                className="flex-1 rounded-lg bg-brand py-2 text-sm font-semibold text-white disabled:opacity-50"
              >
                {triage.isPending ? 'Salvando...' : 'Confirmar Triagem'}
              </button>
              <button onClick={() => setShowTriage(false)} className="flex-1 rounded-lg border py-2 text-sm">
                Cancelar
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Modal: Vincular a Caso */}
      {showLink && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-xl">
            <h2 className="mb-4 text-lg font-semibold">Vincular a Caso</h2>

            <label className="mb-1 block text-sm font-medium">Caso</label>
            <select
              aria-label="Selecionar caso para vincular"
              value={selectedCase}
              onChange={(e) => setSelectedCase(e.target.value)}
              className="mb-4 w-full rounded-lg border px-3 py-2 text-sm"
            >
              <option value="">Selecione um caso...</option>
              {cases.filter((c: Case) => c.status !== 'CLOSED').map((c: Case) => (
                <option key={c.id} value={c.id}>
                  {c.reference_number ? `[${c.reference_number}] ` : ''}{c.title}
                </option>
              ))}
            </select>

            <div className="flex gap-3">
              <button
                onClick={() => link.mutate()}
                disabled={!selectedCase || link.isPending}
                aria-label="Vincular alerta a caso"
                className="flex-1 rounded-lg bg-brand py-2 text-sm font-semibold text-white disabled:opacity-50"
              >
                {link.isPending ? 'Vinculando...' : 'Vincular'}
              </button>
              <button onClick={() => setShowLink(false)} className="flex-1 rounded-lg border py-2 text-sm">
                Cancelar
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
