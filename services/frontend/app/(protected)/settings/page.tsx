'use client';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { fetchScoringConfig, updateScoringConfig, ScoringConfig } from '@/lib/api';
import { Settings, Save, Info } from 'lucide-react';
import { useState, useEffect } from 'react';

// ── Grupos de configuração ────────────────────────────────────────────────────

type FieldDef = {
  key: keyof ScoringConfig;
  label: string;
  hint: string;
  step: string;
  min: number;
  max: number;
  unit?: string;
};

type Group = { title: string; description: string; fields: FieldDef[] };

const GROUPS: Group[] = [
  {
    title: 'Importância de cada componente de risco',
    description:
      'Define quanto cada perspectiva de análise influencia a pontuação final de risco. A soma deve ser 100%.',
    fields: [
      {
        key: 'rule_weight',
        label: 'Condições de risco cadastradas',
        hint: 'Peso das regras de PLD definidas pelo time (ex: depósitos fracionados, cashout rápido). Escala de 0 a 1.',
        step: '0.01', min: 0, max: 1,
      },
      {
        key: 'ml_weight',
        label: 'Análise de comportamento (IA)',
        hint: 'Peso dos desvios do padrão histórico do apostador, detectados automaticamente. Escala de 0 a 1.',
        step: '0.01', min: 0, max: 1,
      },
      {
        key: 'network_weight',
        label: 'Rede de vínculos',
        hint: 'Peso das conexões com outros apostadores via dispositivo ou chave Pix compartilhada. Escala de 0 a 1.',
        step: '0.01', min: 0, max: 1,
      },
    ],
  },
  {
    title: 'Faixas de risco (pontuação 0–100)',
    description:
      'Define a pontuação mínima para cada categoria de alerta. Valores menores geram mais alertas; maiores, menos.',
    fields: [
      { key: 'low_threshold',      label: 'Risco baixo — a partir de',    hint: 'Sugerido: 30–40.',  step: '1', min: 1,   max: 100, unit: 'pts' },
      { key: 'medium_threshold',   label: 'Risco médio — a partir de',    hint: 'Sugerido: 55–65.',  step: '1', min: 1,   max: 100, unit: 'pts' },
      { key: 'high_threshold',     label: 'Risco alto — a partir de',     hint: 'Sugerido: 75–85.',  step: '1', min: 1,   max: 100, unit: 'pts' },
      { key: 'critical_threshold', label: 'Risco crítico — a partir de',  hint: 'Sugerido: 90–95.',  step: '1', min: 1,   max: 100, unit: 'pts' },
    ],
  },
  {
    title: 'Prazos de resolução (SLA)',
    description:
      'Tempo máximo para um analista tomar decisão. Alertas vencidos aparecem em vermelho na fila.',
    fields: [
      { key: 'sla_low_hours',      label: 'Prazo — risco baixo',    hint: '', step: '1', min: 1, max: 720, unit: 'h' },
      { key: 'sla_medium_hours',   label: 'Prazo — risco médio',    hint: '', step: '1', min: 1, max: 720, unit: 'h' },
      { key: 'sla_high_hours',     label: 'Prazo — risco alto',     hint: '', step: '1', min: 1, max: 720, unit: 'h' },
      { key: 'sla_critical_hours', label: 'Prazo — risco crítico',  hint: '', step: '1', min: 1, max: 720, unit: 'h' },
    ],
  },
  {
    title: 'Retenção de dados',
    description:
      'Por quanto tempo os dados são mantidos nos diferentes estágios de processamento.',
    fields: [
      { key: 'data_retention_days',         label: 'Retenção geral',                  hint: 'Prazo padrão em dias.',                        step: '1', min: 30,  max: 1825, unit: 'dias' },
      { key: 'data_retention_raw_years',    label: 'Dados brutos recebidos',          hint: 'Dados no formato original enviado pelo sistema.', step: '1', min: 1,   max: 10,   unit: 'anos' },
      { key: 'data_retention_silver_years', label: 'Dados processados (canônico)',    hint: 'Dados após normalização e validação.',            step: '1', min: 1,   max: 10,   unit: 'anos' },
      { key: 'data_retention_gold_years',   label: 'Indicadores calculados',          hint: 'Scores, agregações e features derivadas.',        step: '1', min: 1,   max: 10,   unit: 'anos' },
    ],
  },
  {
    title: 'Parâmetros operacionais',
    description:
      'Controles avançados de comportamento do sistema. Altere com cautela.',
    fields: [
      {
        key: 'auto_case_threshold',
        label: 'Score mínimo para abrir caso automaticamente',
        hint: 'Apostadores com pontuação acima deste valor têm caso aberto sem intervenção manual. Escala de 0 a 1.',
        step: '0.01', min: 0, max: 1,
      },
      {
        key: 'ingest_rate_limit_tpm',
        label: 'Limite de velocidade de ingestão',
        hint: 'Máximo de registros processados por minuto na entrada de dados.',
        step: '1', min: 10, max: 1000000, unit: 'reg/min',
      },
      {
        key: 'ml_challenger_pct',
        label: 'Porcentagem de tráfego no teste A/B do modelo',
        hint: 'Fração do tráfego enviada ao modelo candidato durante testes comparativos. 0 = sem teste ativo.',
        step: '1', min: 0, max: 100, unit: '%',
      },
    ],
  },
];

// ── Componente de campo ───────────────────────────────────────────────────────

function Field({
  def, value, onChange,
}: {
  def: FieldDef;
  value: number;
  onChange: (v: number) => void;
}) {
  return (
    <div>
      <label className="mb-0.5 block text-xs font-semibold text-gray-700">
        {def.label}
        {def.unit && (
          <span className="ml-1 font-normal text-gray-400">({def.unit})</span>
        )}
      </label>
      {def.hint && (
        <p className="mb-1 text-[11px] text-gray-400 leading-snug">{def.hint}</p>
      )}
      <input
        type="number"
        step={def.step}
        min={def.min}
        max={def.max}
        aria-label={def.label}
        value={value ?? 0}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand"
      />
    </div>
  );
}

// ── Página ────────────────────────────────────────────────────────────────────

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

  const setField = (key: keyof ScoringConfig) => (v: number) =>
    setForm((f) => ({ ...f, [key]: v }));

  return (
    <div className="max-w-3xl space-y-6">
      {/* Cabeçalho */}
      <div className="flex items-center gap-2">
        <Settings size={22} className="text-brand" />
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Parâmetros do Sistema</h1>
          <p className="text-sm text-gray-500">Configurações avançadas — restritas a Administradores.</p>
        </div>
      </div>

      {/* Aviso */}
      <div className="flex items-start gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
        <Info size={15} className="mt-0.5 shrink-0 text-amber-500" />
        <p>
          Alterações aqui afetam <strong>todos os usuários e alertas em produção</strong>.
          Use a tela <a href="/sensitivity" className="underline font-medium">Calibração de Sensibilidade</a> para
          ajustes de rotina — é mais segura e tem pré-visualização de impacto.
        </p>
      </div>

      {isLoading && <p className="text-sm text-gray-400">Carregando…</p>}

      {!isLoading && (
        <form
          className="space-y-6"
          onSubmit={(e) => { e.preventDefault(); save.mutate(); }}
        >
          {GROUPS.map((group) => (
            <section key={group.title} className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
              <h2 className="mb-0.5 text-sm font-bold text-gray-800">{group.title}</h2>
              <p className="mb-4 text-xs text-gray-500">{group.description}</p>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                {group.fields.map((def) => (
                  <Field
                    key={def.key}
                    def={def}
                    value={(form[def.key] as number) ?? 0}
                    onChange={setField(def.key)}
                  />
                ))}
              </div>
            </section>
          ))}

          <div className="flex items-center gap-3 rounded-xl border border-gray-100 bg-white px-6 py-4 shadow-sm">
            <button
              type="submit"
              disabled={save.isPending}
              className="flex items-center gap-2 rounded-lg bg-brand px-5 py-2 text-sm font-semibold text-white hover:bg-brand/90 disabled:opacity-50"
            >
              <Save size={15} />
              {save.isPending ? 'Salvando…' : 'Salvar alterações'}
            </button>
            {saved && <span className="text-sm font-semibold text-green-600">✓ Salvo com sucesso</span>}
            {config?.updated_at && (
              <span className="ml-auto text-xs text-gray-400">
                Última alteração: {new Date(config.updated_at).toLocaleString('pt-BR')}
              </span>
            )}
          </div>
        </form>
      )}
    </div>
  );
}
