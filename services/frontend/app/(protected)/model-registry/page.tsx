'use client';

import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  fetchModelABMetrics,
  fetchModelPerformanceSummary,
  fetchModelRegistry,
  ModelABMetrics,
  ModelPerformanceSummary,
  ModelRegistry,
  promoteModel,
  designateChallenger,
} from '@/lib/api';
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import {
  BarChart2, BrainCircuit, Info, ShieldCheck, Star, Target, TrendingUp, Trophy,
} from 'lucide-react';

const STATUS_LABEL: Record<string, { label: string; color: string }> = {
  champion: { label: 'Em produção', color: 'bg-green-100 text-green-700' },
  active: { label: 'Em produção', color: 'bg-green-100 text-green-700' },
  challenger: { label: 'A/B challenger', color: 'bg-blue-100 text-blue-700' },
  STAGING: { label: 'Pronto para teste', color: 'bg-yellow-100 text-yellow-700' },
  archived: { label: 'Arquivado', color: 'bg-gray-100 text-gray-600' },
};

const MODEL_TYPE_LABEL: Record<string, string> = {
  ANOMALY: 'Detecção de Anomalias',
  StructuringDetector: 'Structuring Detector',
  GraphClustering: 'Rede / Clusterização',
  RecurrenceEstimator: 'Reincidência',
};

function pct(value?: number | null) {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  return `${(value * 100).toFixed(1)}%`;
}

function compact(value?: number | null) {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  return new Intl.NumberFormat('pt-BR').format(value);
}

function MetricCard({
  title,
  value,
  hint,
}: {
  title: string;
  value: string;
  hint: string;
}) {
  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
      <div className="flex items-center gap-2">
        <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">{title}</span>
        <span className="group relative ml-auto cursor-help">
          <Info size={12} className="text-gray-400" />
          <span className="invisible absolute right-0 top-5 z-10 w-52 rounded-lg bg-gray-900 p-2 text-xs leading-snug text-white group-hover:visible">
            {hint}
          </span>
        </span>
      </div>
      <div className="mt-2 text-2xl font-bold text-gray-900">{value}</div>
    </div>
  );
}

function ABComparison({ metrics }: { metrics: ModelABMetrics }) {
  return (
    <div className="rounded-xl border border-blue-100 bg-blue-50 p-4">
      <div className="mb-3 flex items-center gap-2">
        <TrendingUp size={16} className="text-blue-600" />
        <h2 className="text-sm font-semibold text-blue-900">Comparativo Champion vs Challenger</h2>
        <span className="rounded-full bg-white px-2 py-0.5 text-xs font-semibold text-blue-700">
          janela {metrics.days_window}d
        </span>
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <div className="rounded-lg bg-white p-3">
          <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">Champion</div>
          <div className="grid grid-cols-3 gap-3 text-sm">
            <div>
              <div className="text-gray-500">Inferências</div>
              <div className="font-semibold text-gray-900">{compact(metrics.champion_inferences)}</div>
            </div>
            <div>
              <div className="text-gray-500">Precisão</div>
              <div className="font-semibold text-gray-900">{pct(metrics.champion_precision_estimated)}</div>
            </div>
            <div>
              <div className="text-gray-500">Recall</div>
              <div className="font-semibold text-gray-900">{pct(metrics.champion_recall_estimated)}</div>
            </div>
          </div>
        </div>

        <div className="rounded-lg bg-white p-3">
          <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">Challenger</div>
          <div className="grid grid-cols-3 gap-3 text-sm">
            <div>
              <div className="text-gray-500">Inferências</div>
              <div className="font-semibold text-gray-900">{compact(metrics.challenger_inferences)}</div>
            </div>
            <div>
              <div className="text-gray-500">Precisão</div>
              <div className="font-semibold text-gray-900">{pct(metrics.challenger_precision_estimated)}</div>
            </div>
            <div>
              <div className="text-gray-500">Recall</div>
              <div className="font-semibold text-gray-900">{pct(metrics.challenger_recall_estimated)}</div>
            </div>
          </div>
        </div>
      </div>

      {metrics.timeline.length > 0 && (
        <div className="mt-4 h-72 rounded-lg bg-white p-3">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={metrics.timeline}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
              <XAxis dataKey="date" tick={{ fontSize: 11 }} />
              <YAxis tick={{ fontSize: 11 }} />
              <Tooltip />
              <Bar dataKey="champion_inferences" fill="#2563eb" radius={[4, 4, 0, 0]} name="Champion" />
              <Bar dataKey="challenger_inferences" fill="#16a34a" radius={[4, 4, 0, 0]} name="Challenger" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}

export default function ModelRegistryPage() {
  const qc = useQueryClient();
  const [days, setDays] = useState(30);

  const { data: models = [], isLoading } = useQuery({
    queryKey: ['model-registry'],
    queryFn: () => fetchModelRegistry(),
  });

  const { data: summary } = useQuery<ModelPerformanceSummary>({
    queryKey: ['model-registry', 'performance', days],
    queryFn: () => fetchModelPerformanceSummary(days),
  });

  const challenger = useMemo(
    () => models.find((model) => model.is_challenger || model.status === 'challenger') ?? null,
    [models],
  );

  const { data: abMetrics } = useQuery<ModelABMetrics | null>({
    queryKey: ['model-registry', 'ab-metrics', challenger?.id, days],
    queryFn: () => (challenger ? fetchModelABMetrics(challenger.id, days) : Promise.resolve(null)),
    enabled: !!challenger,
  });

  const promote = useMutation({
    mutationFn: promoteModel,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['model-registry'] });
      qc.invalidateQueries({ queryKey: ['model-registry', 'performance'] });
    },
  });

  const designate = useMutation({
    mutationFn: designateChallenger,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['model-registry'] });
      qc.invalidateQueries({ queryKey: ['model-registry', 'performance'] });
    },
  });

  const modelPerformanceById = useMemo(() => {
    const map = new Map<string, ModelPerformanceSummary['by_model'][number]>();
    summary?.by_model.forEach((item) => map.set(item.model_id, item));
    return map;
  }, [summary]);

  const grouped = useMemo(() => {
    return models.reduce<Record<string, ModelRegistry[]>>((acc, model) => {
      (acc[model.model_type] ??= []).push(model);
      return acc;
    }, {});
  }, [models]);

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <BrainCircuit size={22} className="text-brand-600" />
            <h1 className="text-2xl font-bold text-gray-900">Modelos Analíticos</h1>
          </div>
          <p className="mt-1 text-sm text-gray-500">
            Registry, A/B testing e performance do feedback loop supervisionado.
          </p>
        </div>

        <div className="flex items-center gap-2">
          {[7, 30, 90].map((option) => (
            <button
              key={option}
              onClick={() => setDays(option)}
              aria-label={`Selecionar janela de ${option} dias do model registry`}
              className={`rounded-lg px-3 py-1.5 text-sm font-semibold ${
                days === option ? 'bg-brand-600 text-white' : 'border border-gray-200 bg-white text-gray-700'
              }`}
            >
              {option}d
            </button>
          ))}
        </div>
      </div>

      {summary && (
        <div className="grid gap-4 md:grid-cols-4">
          <MetricCard
            title="Precisão estimada"
            value={pct(summary.totals.precision_estimated)}
            hint="TP / (TP + FP) considerando alertas rotulados no período."
          />
          <MetricCard
            title="Falso positivo"
            value={pct(summary.totals.false_positive_rate)}
            hint="FP / (TP + FP) dos alertas já revisados por analistas."
          />
          <MetricCard
            title="Alertas rotulados"
            value={compact(summary.totals.labeled_alerts)}
            hint="Quantidade de alertas com feedback manual disponível."
          />
          <MetricCard
            title="Tráfego challenger"
            value={`${summary.challenger_split_pct}%`}
            hint="Percentual configurado do tráfego enviado ao challenger via scoring por tenant."
          />
        </div>
      )}

      {summary && summary.by_day.length > 0 && (
        <div className="grid gap-4 lg:grid-cols-[2fr,1fr]">
          <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
            <div className="mb-3 flex items-center gap-2">
              <ShieldCheck size={16} className="text-green-600" />
              <h2 className="text-sm font-semibold text-gray-900">Qualidade dos alertas ao longo do tempo</h2>
            </div>
            <div className="h-72">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={summary.by_day}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                  <XAxis dataKey="date" tick={{ fontSize: 11 }} />
                  <YAxis tick={{ fontSize: 11 }} />
                  <Tooltip />
                  <Area type="monotone" dataKey="true_positive_count" stroke="#16a34a" fill="#bbf7d0" name="TP" />
                  <Area type="monotone" dataKey="false_positive_count" stroke="#ef4444" fill="#fecaca" name="FP" />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>

          <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
            <div className="mb-3 flex items-center gap-2">
              <BarChart2 size={16} className="text-brand-600" />
              <h2 className="text-sm font-semibold text-gray-900">Top regras por revisão</h2>
            </div>
            <div className="space-y-3">
              {summary.by_rule.slice(0, 5).map((item) => (
                <div key={item.rule_id ?? item.rule_name}>
                  <div className="flex items-center justify-between gap-3 text-sm">
                    <span className="truncate font-medium text-gray-800">{item.rule_name}</span>
                    <span className="text-gray-500">{compact(item.total_alerts)}</span>
                  </div>
                  <div className="mt-1 h-2 rounded-full bg-gray-100">
                    <div
                      className="h-2 rounded-full bg-brand-500"
                      style={{ width: `${Math.min(100, item.precision_estimated * 100)}%` }}
                    />
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {abMetrics && <ABComparison metrics={abMetrics} />}

      {isLoading && <p className="text-sm text-gray-500">Carregando modelos…</p>}

      {Object.entries(grouped).map(([modelType, entries]) => {
        const ordered = [...entries].sort((a, b) => {
          const rankA = a.status === 'champion' || a.status === 'active' ? 0 : a.is_challenger ? 1 : a.status === 'STAGING' ? 2 : 3;
          const rankB = b.status === 'champion' || b.status === 'active' ? 0 : b.is_challenger ? 1 : b.status === 'STAGING' ? 2 : 3;
          return rankA - rankB;
        });

        return (
          <section key={modelType} className="rounded-xl border border-gray-200 bg-white shadow-sm">
            <div className="border-b border-gray-100 bg-gray-50 px-5 py-3">
              <div className="flex items-center gap-2">
                <Trophy size={15} className="text-gray-500" />
                <span className="font-semibold text-gray-800">{MODEL_TYPE_LABEL[modelType] ?? modelType}</span>
                <span className="text-xs text-gray-400">{entries.length} versões</span>
              </div>
            </div>

            <div className="divide-y divide-gray-100">
              {ordered.map((model) => {
                const status = STATUS_LABEL[model.status] ?? STATUS_LABEL.archived;
                const perf = modelPerformanceById.get(model.id);
                const metrics = model.metrics ?? {};
                const auc = Number(metrics.auc_roc ?? metrics.auc ?? 0);
                const precision = Number(metrics.precision ?? 0);
                const recall = Number(metrics.recall ?? 0);
                const f1 = Number(metrics.f1_score ?? metrics.f1 ?? 0);

                return (
                  <div key={model.id} className="px-5 py-4">
                    <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                      <div>
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-semibold text-gray-900">v{model.version}</span>
                          <span className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${status.color}`}>
                            {status.label}
                          </span>
                          {(model.status === 'champion' || model.status === 'active') && (
                            <Star size={14} className="text-yellow-500" />
                          )}
                        </div>
                        <p className="mt-1 text-xs text-gray-500">
                          {model.algorithm ?? model.model_name ?? 'Modelo sem nome'}
                          {model.trained_at && ` · treinado em ${new Date(model.trained_at).toLocaleDateString('pt-BR')}`}
                          {model.promoted_at && ` · promovido em ${new Date(model.promoted_at).toLocaleDateString('pt-BR')}`}
                        </p>
                        <p className="mt-1 text-xs text-gray-400">
                          {compact(model.training_rows ?? model.sample_count ?? 0)} amostras
                          {model.artifact_path && ` · ${model.artifact_path}`}
                        </p>
                      </div>

                      <div className="flex items-center gap-2">
                        {model.status === 'STAGING' && (
                          <button
                            onClick={() => designate.mutate(model.id)}
                            disabled={designate.isPending}
                            aria-label={`Designar challenger para modelo versão ${model.version}`}
                            className="rounded-lg border border-blue-300 px-3 py-1.5 text-xs font-semibold text-blue-700 hover:bg-blue-50 disabled:opacity-50"
                          >
                            Designar challenger
                          </button>
                        )}
                        {(model.is_challenger || model.status === 'challenger') && (
                          <button
                            onClick={() => promote.mutate(model.id)}
                            disabled={promote.isPending}
                            aria-label={`Promover modelo versão ${model.version} para produção`}
                            className="rounded-lg bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand-700 disabled:opacity-50"
                          >
                            Promover para produção
                          </button>
                        )}
                      </div>
                    </div>

                    <div className="mt-4 grid gap-3 md:grid-cols-4">
                      <MetricCard
                        title="AUC"
                        value={pct(auc || 0)}
                        hint="Capacidade de separação entre padrões normais e suspeitos."
                      />
                      <MetricCard
                        title="Precisão treino"
                        value={pct(precision || 0)}
                        hint="Precisão registrada no treino do modelo."
                      />
                      <MetricCard
                        title="Recall treino"
                        value={pct(recall || 0)}
                        hint="Recall registrado no treino do modelo."
                      />
                      <MetricCard
                        title="F1 treino"
                        value={pct(f1 || 0)}
                        hint="Equilíbrio entre precisão e recall do treino."
                      />
                    </div>

                    {perf && (
                      <div className="mt-4 grid gap-3 md:grid-cols-3">
                        <div className="rounded-lg bg-gray-50 p-3">
                          <div className="text-xs font-semibold uppercase tracking-wide text-gray-500">Feedback do período</div>
                          <div className="mt-1 text-lg font-bold text-gray-900">{compact(perf.total_alerts)}</div>
                        </div>
                        <div className="rounded-lg bg-gray-50 p-3">
                          <div className="text-xs font-semibold uppercase tracking-wide text-gray-500">Precisão estimada</div>
                          <div className="mt-1 text-lg font-bold text-gray-900">{pct(perf.precision_estimated)}</div>
                        </div>
                        <div className="rounded-lg bg-gray-50 p-3">
                          <div className="text-xs font-semibold uppercase tracking-wide text-gray-500">Recall share</div>
                          <div className="mt-1 text-lg font-bold text-gray-900">{pct(perf.recall_estimated)}</div>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </section>
        );
      })}

      {!isLoading && models.length === 0 && (
        <div className="rounded-xl border border-dashed border-gray-200 py-16 text-center">
          <BrainCircuit size={32} className="mx-auto mb-3 text-gray-300" />
          <p className="text-sm text-gray-500">Nenhum modelo registrado ainda.</p>
        </div>
      )}
    </div>
  );
}
