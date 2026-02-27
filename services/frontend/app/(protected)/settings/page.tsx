'use client';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { Settings, Save } from 'lucide-react';
import { useState, useEffect } from 'react';

interface ScoringConfig {
  id: string;
  isolation_forest_weight: number;
  structuring_weight: number;
  graph_weight: number;
  recurrence_weight: number;
  alert_threshold: number;
  block_threshold: number;
  review_threshold: number;
  updated_at: string;
}

const fetchConfig = () =>
  api.get<ScoringConfig>('/scoring-config').then((r) => r.data);

const fields: { key: keyof ScoringConfig; label: string; step: string }[] = [
  { key: 'isolation_forest_weight', label: 'Peso — Isolation Forest',    step: '0.01' },
  { key: 'structuring_weight',      label: 'Peso — Structuring GBM',      step: '0.01' },
  { key: 'graph_weight',            label: 'Peso — Graph Clustering',     step: '0.01' },
  { key: 'recurrence_weight',       label: 'Peso — Recurrence kNN',       step: '0.01' },
  { key: 'alert_threshold',         label: 'Limiar — Gerar Alerta',       step: '0.01' },
  { key: 'review_threshold',        label: 'Limiar — Revisão Manual',     step: '0.01' },
  { key: 'block_threshold',         label: 'Limiar — Bloqueio Automático',step: '0.01' },
];

export default function SettingsPage() {
  const qc = useQueryClient();

  const { data: config, isLoading } = useQuery({
    queryKey: ['scoring-config'],
    queryFn: fetchConfig,
  });

  const [form, setForm] = useState<Partial<ScoringConfig>>({});
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (config) setForm(config);
  }, [config]);

  const save = useMutation({
    mutationFn: () => api.put('/scoring-config', form),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['scoring-config'] });
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    },
  });

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2">
        <Settings size={22} className="text-brand" />
        <h1 className="text-2xl font-bold text-gray-900">Configurações</h1>
      </div>

      <section className="rounded-xl border border-gray-100 bg-white p-6 shadow-sm">
        <h2 className="mb-4 text-sm font-semibold text-gray-700">Pontuação & Limiares de Risco</h2>

        {isLoading && <p className="text-sm text-gray-400">Carregando…</p>}

        {!isLoading && (
          <form
            className="space-y-4"
            onSubmit={(e) => { e.preventDefault(); save.mutate(); }}
          >
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
              {fields.map(({ key, label, step }) => (
                <div key={key}>
                  <label className="mb-1 block text-xs font-medium text-gray-600">{label}</label>
                  <input
                    type="number"
                    step={step}
                    min={0}
                    max={10}
                    value={(form[key] as number) ?? 0}
                    onChange={(e) => setForm((f) => ({ ...f, [key]: parseFloat(e.target.value) }))}
                    className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand"
                  />
                </div>
              ))}
            </div>

            <div className="flex items-center gap-3 pt-2">
              <button
                type="submit"
                disabled={save.isPending}
                className="flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand/90 disabled:opacity-50"
              >
                <Save size={15} />
                {save.isPending ? 'Salvando…' : 'Salvar Configuração'}
              </button>
              {saved && <span className="text-sm text-green-600">Salvo com sucesso!</span>}
            </div>

            {config && (
              <p className="text-xs text-gray-400">
                Última atualização: {new Date(config.updated_at).toLocaleString('pt-BR')}
              </p>
            )}
          </form>
        )}
      </section>
    </div>
  );
}
