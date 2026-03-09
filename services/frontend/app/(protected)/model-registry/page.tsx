'use client';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import {
  BrainCircuit, Trophy, BarChart2, Info,
  TrendingUp, Target, Eye, Star,
} from 'lucide-react';

interface ModelEntry {
  id: string;
  model_type: string;
  version: string;
  status: 'champion' | 'challenger' | 'archived' | 'active';
  metrics: Record<string, number>;
  trained_at: string;
  promoted_at?: string;
}

const fetchModels = () =>
  api.get<ModelEntry[]>('/model-registry').then((r) => r.data);
const promoteModel = (id: string) =>
  api.post(`/model-registry/${id}/promote`);

const STATUS_LABEL: Record<string, { label: string; color: string; icon: React.ReactNode }> = {
  champion:   { label: 'Em produção',  color: 'bg-green-100 text-green-700',  icon: <Star size={10} /> },
  challenger: { label: 'Em teste A/B', color: 'bg-blue-100 text-blue-700',    icon: <TrendingUp size={10} /> },
  active:     { label: 'Ativo',        color: 'bg-emerald-100 text-emerald-700', icon: null },
  archived:   { label: 'Arquivado',    color: 'bg-gray-100 text-gray-500',    icon: null },
};

const MODEL_TYPE_LABEL: Record<string, string> = {
  ISOLATION_FOREST: 'Detecção de Anomalias',
  XGB_CLASSIFIER:   'Classificação de Risco',
  GNN_NETWORK:      'Análise de Redes',
  LSTM_SEQUENCE:    'Padrões Temporais',
};

function MetricBar({ value, color }: { value: number; color: string }) {
  return (
    <div className="w-full bg-gray-100 rounded-full h-1.5 mt-1">
      <div className={`h-1.5 rounded-full ${color}`} style={{ width: `${(value * 100).toFixed(0)}%` }} />
    </div>
  );
}

function MetricCard({
  icon, label, hint, value, color,
}: { icon: React.ReactNode; label: string; hint: string; value?: number; color: string }) {
  if (value === undefined || value === null) return null;
  const pct = (value * 100).toFixed(1);
  const quality = value >= 0.9 ? 'Excelente' : value >= 0.75 ? 'Bom' : value >= 0.6 ? 'Regular' : 'Fraco';
  const qualityColor = value >= 0.9 ? 'text-green-600' : value >= 0.75 ? 'text-blue-600' : value >= 0.6 ? 'text-yellow-600' : 'text-red-600';
  return (
    <div className="bg-gray-50 rounded-lg p-3 space-y-1">
      <div className="flex items-center gap-1.5">
        <span className="text-gray-500">{icon}</span>
        <span className="text-xs font-semibold text-gray-700">{label}</span>
        <span className="group relative cursor-help ml-auto">
          <Info size={12} className="text-gray-400" />
          <span className="absolute right-0 bottom-5 z-10 invisible group-hover:visible w-48 bg-gray-800 text-white text-xs rounded-lg p-2 leading-snug">
            {hint}
          </span>
        </span>
      </div>
      <div className="flex items-end justify-between">
        <span className="text-lg font-bold text-gray-900">{pct}%</span>
        <span className={`text-xs font-semibold ${qualityColor}`}>{quality}</span>
      </div>
      <MetricBar value={value} color={color} />
    </div>
  );
}

export default function ModelRegistryPage() {
  const qc = useQueryClient();

  const { data: models = [], isLoading } = useQuery({
    queryKey: ['model-registry'],
    queryFn: fetchModels,
  });

  const promote = useMutation({
    mutationFn: promoteModel,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['model-registry'] }),
  });

  const byType = models.reduce<Record<string, ModelEntry[]>>((acc, m) => {
    (acc[m.model_type] ??= []).push(m);
    return acc;
  }, {});

  // Sort within each type: champion first, then challengers, then archived
  const ORDER = { champion: 0, active: 0, challenger: 1, archived: 2 };

  return (
    <div className="space-y-6">
      <div>
        <div className="flex items-center gap-2">
          <BrainCircuit size={22} className="text-brand-600" />
          <h1 className="text-2xl font-bold text-gray-900">Modelos Analíticos</h1>
        </div>
        <p className="text-gray-500 mt-1 text-sm">
          Desempenho dos modelos de inteligência artificial ativos na detecção de PLD.
        </p>
      </div>

      {isLoading && <p className="text-sm text-gray-500">Carregando…</p>}

      {Object.entries(byType).map(([type, entries]) => {
        const sorted = [...entries].sort((a, b) => (ORDER[a.status] ?? 2) - (ORDER[b.status] ?? 2));
        const champion = sorted.find((m) => m.status === 'champion' || m.status === 'active');
        const challengers = sorted.filter((m) => m.status === 'challenger');

        return (
          <section key={type} className="bg-white rounded-xl border border-gray-200 overflow-hidden">
            {/* Header */}
            <div className="flex items-center justify-between gap-2 border-b border-gray-100 bg-gray-50 px-5 py-3">
              <div className="flex items-center gap-2">
                <BarChart2 size={15} className="text-gray-500" />
                <span className="font-semibold text-gray-800">
                  {MODEL_TYPE_LABEL[type] ?? type}
                </span>
                <span className="text-xs text-gray-400 ml-1">{entries.length} versões</span>
              </div>
              {champion && (
                <span className="text-xs text-gray-500">
                  Versão em produção: <strong className="text-gray-800">v{champion.version}</strong>
                </span>
              )}
            </div>

            <div className="divide-y divide-gray-50">
              {sorted.map((m) => {
                const meta = STATUS_LABEL[m.status] ?? STATUS_LABEL.archived;
                const auc = m.metrics?.auc_roc;
                const prec = m.metrics?.precision;
                const recall = m.metrics?.recall;
                const f1 = m.metrics?.f1;
                const isArchived = m.status === 'archived';

                return (
                  <div key={m.id} className={`px-5 py-4 ${isArchived ? 'opacity-60' : ''}`}>
                    {/* Top row */}
                    <div className="flex items-start justify-between mb-4">
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="font-semibold text-gray-900">v{m.version}</span>
                          <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-semibold ${meta.color}`}>
                            {meta.icon}
                            {meta.label}
                          </span>
                          {m.status === 'champion' && <Trophy size={14} className="text-yellow-500" />}
                        </div>
                        <p className="text-xs text-gray-400 mt-0.5">
                          Treinado em {new Date(m.trained_at).toLocaleDateString('pt-BR', { day: '2-digit', month: 'short', year: 'numeric' })}
                          {m.promoted_at && ` · Ativado em ${new Date(m.promoted_at).toLocaleDateString('pt-BR', { day: '2-digit', month: 'short', year: 'numeric' })}`}
                        </p>
                      </div>

                      {m.status === 'challenger' && (
                        <button
                          onClick={() => promote.mutate(m.id)}
                          disabled={promote.isPending}
                          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-brand-600 hover:bg-brand-700 text-white text-xs font-semibold transition-colors disabled:opacity-50"
                        >
                          <Star size={12} />
                          Promover para produção
                        </button>
                      )}
                    </div>

                    {/* Metric cards */}
                    {(auc !== undefined || prec !== undefined || recall !== undefined) && (
                      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                        <MetricCard
                          icon={<TrendingUp size={13} />}
                          label="Capacidade de detecção"
                          hint="AUC-ROC: quanto mais perto de 100%, melhor o modelo separa clientes suspeitos de normais."
                          value={auc}
                          color="bg-brand-500"
                        />
                        <MetricCard
                          icon={<Target size={13} />}
                          label="Taxa de acerto dos alertas"
                          hint="Precisão: de cada 10 alertas gerados, quantos realmente indicam risco? Alta precisão = menos trabalho desnecessário para o analista."
                          value={prec}
                          color="bg-blue-500"
                        />
                        <MetricCard
                          icon={<Eye size={13} />}
                          label="Casos reais detectados"
                          hint="Recall: dos clientes que realmente apresentavam risco, qual % o modelo identificou? Alto recall = nenhum caso suspeito passa despercebido."
                          value={recall}
                          color="bg-green-500"
                        />
                        <MetricCard
                          icon={<BarChart2 size={13} />}
                          label="Equilíbrio geral (F1)"
                          hint="F1-Score: combina acerto e cobertura num único indicador. Acima de 80% é considerado muito bom para detecção de fraude."
                          value={f1}
                          color="bg-purple-500"
                        />
                      </div>
                    )}

                    {/* No metrics */}
                    {auc === undefined && prec === undefined && recall === undefined && (
                      <p className="text-xs text-gray-400 italic">Métricas não disponíveis para esta versão.</p>
                    )}
                  </div>
                );
              })}
            </div>

            {/* A/B distribution hint */}
            {challengers.length > 0 && champion && (
              <div className="mx-5 mb-4 mt-1 flex items-start gap-2 bg-blue-50 border border-blue-100 rounded-lg p-3">
                <Info size={14} className="text-blue-500 flex-shrink-0 mt-0.5" />
                <p className="text-xs text-blue-800">
                  <strong>Teste A/B ativo:</strong> o modelo challenger está recebendo{' '}
                  <strong>10% do tráfego</strong> de eventos para validação em produção.
                  Se as métricas continuarem superiores, use o botão &quot;Promover&quot; para ativá-lo completamente.
                </p>
              </div>
            )}
          </section>
        );
      })}

      {!isLoading && models.length === 0 && (
        <div className="rounded-xl border border-dashed border-gray-200 py-16 text-center">
          <BrainCircuit size={32} className="mx-auto mb-3 text-gray-300" />
          <p className="text-sm text-gray-500">Nenhum modelo registrado ainda</p>
          <p className="text-xs text-gray-400 mt-1">Os modelos são registrados automaticamente pelo serviço ml_service após treinamento</p>
        </div>
      )}
    </div>
  );
}


interface ModelEntry {
  id: string;
  model_type: string;
  version: string;
  status: 'champion' | 'challenger' | 'archived' | 'active';
  metrics: Record<string, number>;
  trained_at: string;
  promoted_at?: string;
}

const fetchModels = () =>
  api.get<ModelEntry[]>('/model-registry').then((r) => r.data);

const promoteModel = (id: string) =>
  api.post(`/model-registry/${id}/promote`);

const STATUS_BADGE: Record<string, string> = {
  champion:   'bg-yellow-100 text-yellow-700',
  challenger: 'bg-blue-100 text-blue-700',
  active:     'bg-green-100 text-green-700',
  archived:   'bg-gray-100 text-gray-500',
};

export default function ModelRegistryPage() {
  const qc = useQueryClient();

  const { data: models = [], isLoading } = useQuery({
    queryKey: ['model-registry'],
    queryFn: fetchModels,
  });

  const promote = useMutation({
    mutationFn: promoteModel,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['model-registry'] }),
  });

  // Group by model_type
  const byType = models.reduce<Record<string, ModelEntry[]>>((acc: Record<string, ModelEntry[]>, m: ModelEntry) => {
    (acc[m.model_type] ??= []).push(m);
    return acc;
  }, {});

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2">
        <BrainCircuit size={22} className="text-brand" />
        <h1 className="text-2xl font-bold text-gray-900">Registro de Modelos ML</h1>
      </div>

      {isLoading && <p className="text-sm text-gray-500">Carregando…</p>}

      {(Object.entries(byType) as [string, ModelEntry[]][]).map(([type, entries]) => (
        <section key={type} className="overflow-hidden rounded-xl border border-gray-100 bg-white shadow-sm">
          <div className="flex items-center gap-2 border-b border-gray-100 bg-gray-50 px-4 py-2.5">
            <BarChart2 size={15} className="text-gray-500" />
            <span className="text-xs font-semibold uppercase tracking-wide text-gray-600">{type}</span>
          </div>
          <table className="w-full text-sm">
            <thead className="bg-gray-50/50 text-xs font-semibold uppercase text-gray-500">
              <tr>
                <th className="px-4 py-2.5 text-left">Versão</th>
                <th className="px-4 py-2.5 text-left">Status</th>
                <th className="px-4 py-2.5 text-left">AUC-ROC</th>
                <th className="px-4 py-2.5 text-left">Precision</th>
                <th className="px-4 py-2.5 text-left">Recall</th>
                <th className="px-4 py-2.5 text-left">Treinado em</th>
                <th className="px-4 py-2.5" />
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {(entries as ModelEntry[]).map((m: ModelEntry) => (
                <tr key={m.id} className={`hover:bg-gray-50/50 ${m.status === 'champion' ? 'bg-yellow-50/30' : ''}`}>
                  <td className="px-4 py-2.5 font-medium">{m.version}</td>
                  <td className="px-4 py-2.5">
                    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold ${STATUS_BADGE[m.status] ?? 'bg-gray-100'}`}>
                      {m.status === 'champion' && <Trophy size={10} />}
                      {m.status}
                    </span>
                  </td>
                  <td className="px-4 py-2.5">{m.metrics?.auc_roc?.toFixed(4) ?? '—'}</td>
                  <td className="px-4 py-2.5">{m.metrics?.precision?.toFixed(4) ?? '—'}</td>
                  <td className="px-4 py-2.5">{m.metrics?.recall?.toFixed(4) ?? '—'}</td>
                  <td className="px-4 py-2.5 text-gray-500">{new Date(m.trained_at).toLocaleDateString('pt-BR')}</td>
                  <td className="px-4 py-2.5 text-right">
                    {m.status !== 'champion' && (
                      <button
                        onClick={() => promote.mutate(m.id)}
                        disabled={promote.isPending}
                        className="rounded px-2 py-1 text-xs font-medium text-brand hover:underline disabled:opacity-50"
                      >
                        Promover
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ))}

      {!isLoading && models.length === 0 && (
        <div className="rounded-xl border border-dashed border-gray-200 py-16 text-center">
          <BrainCircuit size={32} className="mx-auto mb-3 text-gray-300" />
          <p className="text-sm text-gray-500">Nenhum modelo registrado</p>
        </div>
      )}
    </div>
  );
}
