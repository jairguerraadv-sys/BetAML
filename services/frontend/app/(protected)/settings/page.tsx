'use client';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { Settings, Save } from 'lucide-react';
import { useState, useEffect } from 'react';

interface ScoringConfig {
  id: number;
  rule_weight: number;
  ml_weight: number;
  network_weight: number;
  low_threshold: number;
  medium_threshold: number;
  high_threshold: number;
  critical_threshold: number;
  sla_low_hours: number;
  sla_medium_hours: number;
  sla_high_hours: number;
  sla_critical_hours: number;
  updated_at: string | null;
}

const fetchConfig = () =>
  api.get<ScoringConfig>('/scoring-config').then((r) => r.data);

const fields: { key: keyof ScoringConfig; label: string; step: string }[] = [
  { key: 'rule_weight',       label: 'Peso — Regras DSL',             step: '0.01' },
  { key: 'ml_weight',         label: 'Peso — Modelos ML',             step: '0.01' },
  { key: 'network_weight',    label: 'Peso — Análise de Rede',        step: '0.01' },
  { key: 'low_threshold',     label: 'Limiar Baixo (score 0-100)',    step: '1' },
  { key: 'medium_threshold',  label: 'Limiar Médio (score 0-100)',    step: '1' },
  { key: 'high_threshold',    label: 'Limiar Alto (score 0-100)',     step: '1' },
  { key: 'critical_threshold',label: 'Limiar Crítico (score 0-100)', step: '1' },
  { key: 'sla_low_hours',     label: 'SLA Baixo (horas)',             step: '1' },
  { key: 'sla_medium_hours',  label: 'SLA Médio (horas)',             step: '1' },
  { key: 'sla_high_hours',    label: 'SLA Alto (horas)',              step: '1' },
  { key: 'sla_critical_hours',label: 'SLA Crítico (horas)',          step: '1' },
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
