'use client';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { fetchScoringConfig, updateScoringConfig, ScoringConfig } from '@/lib/api';
import { Settings, Save } from 'lucide-react';
import { useState, useEffect } from 'react';

const fields: { key: keyof ScoringConfig; label: string; step: string; min: number; max: number }[] = [
  { key: 'rule_weight',          label: 'Peso — Regras DSL',             step: '0.01', min: 0,   max: 1   },
  { key: 'ml_weight',            label: 'Peso — Modelos ML',             step: '0.01', min: 0,   max: 1   },
  { key: 'network_weight',       label: 'Peso — Análise de Rede',        step: '0.01', min: 0,   max: 1   },
  { key: 'low_threshold',        label: 'Limiar Baixo (score 0-100)',    step: '1',    min: 0,   max: 100 },
  { key: 'medium_threshold',     label: 'Limiar Médio (score 0-100)',    step: '1',    min: 0,   max: 100 },
  { key: 'high_threshold',       label: 'Limiar Alto (score 0-100)',     step: '1',    min: 0,   max: 100 },
  { key: 'critical_threshold',   label: 'Limiar Crítico (score 0-100)', step: '1',    min: 0,   max: 100 },
  { key: 'sla_low_hours',        label: 'SLA Baixo (horas)',             step: '1',    min: 1,   max: 720 },
  { key: 'sla_medium_hours',     label: 'SLA Médio (horas)',             step: '1',    min: 1,   max: 720 },
  { key: 'sla_high_hours',       label: 'SLA Alto (horas)',              step: '1',    min: 1,   max: 720 },
  { key: 'sla_critical_hours',   label: 'SLA Crítico (horas)',           step: '1',    min: 1,   max: 720 },
  { key: 'data_retention_days',          label: 'Retenção de dados (dias)',              step: '1', min: 30,  max: 1825 },
  { key: 'data_retention_raw_years',     label: 'Retenção dados brutos / Silver (anos)', step: '1', min: 1,   max: 10   },
  { key: 'data_retention_silver_years',  label: 'Retenção canônico Silver (anos)',       step: '1', min: 1,   max: 10   },
  { key: 'data_retention_gold_years',    label: 'Retenção features Gold (anos)',         step: '1', min: 1,   max: 10   },
  { key: 'auto_case_threshold',          label: 'Threshold auto-criação de caso (0–1)',  step: '0.01', min: 0, max: 1   },
  { key: 'ingest_rate_limit_tpm',        label: 'Rate limit de ingestão (req/min)',      step: '1',    min: 10, max: 10000 },
  { key: 'ml_challenger_pct',            label: 'Tráfego challenger ML (%)',             step: '1',    min: 0,  max: 100 },
];

export default function SettingsPage() {
  const qc = useQueryClient();

  const { data: config, isLoading } = useQuery({
    queryKey: ['scoring-config'],
    queryFn: fetchScoringConfig,
  });

  const [form, setForm] = useState<Partial<ScoringConfig>>({});
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (config) setForm(config);
  }, [config]);

  const save = useMutation({
    mutationFn: () => updateScoringConfig(form),
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
              {fields.map(({ key, label, step, min, max }) => (
                <div key={key}>
                  <label className="mb-1 block text-xs font-medium text-gray-600">{label}</label>
                  <input
                    type="number"
                    step={step}
                    min={min}
                    max={max}
                    aria-label={label}
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
                Última atualização: {config.updated_at ? new Date(config.updated_at).toLocaleString('pt-BR') : '—'}
              </p>
            )}
          </form>
        )}
      </section>
    </div>
  );
}
