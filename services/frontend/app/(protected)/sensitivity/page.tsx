'use client';
import { useState, useEffect, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import {
  SlidersHorizontal, Save, RefreshCw, HelpCircle, ChevronRight,
  Shield, Clock,
} from 'lucide-react';
import ContextualHelp from '@/components/ContextualHelp';

// ── Tipos ─────────────────────────────────────────────────────────────────────

interface ScoringConfig {
  id: number;
  rule_weight: number;
  ml_weight: number;
  network_weight: number;
  low_threshold: number;
  medium_threshold: number;
  high_threshold: number;
  critical_threshold: number;
  auto_case_threshold: number;
  risk_band_low_threshold: number;
  risk_band_high_threshold: number;
  income_volume_ratio_threshold: number;
  sla_low_hours: number;
  sla_medium_hours: number;
  sla_high_hours: number;
  sla_critical_hours: number;
  updated_at: string | null;
}

interface PreviewCount {
  low: number;
  medium: number;
  high: number;
  critical: number;
}

interface SensitivityPreview {
  current: PreviewCount;
  proposed: PreviewCount;
  total_alerts_30d: number;
}

const fetchConfig = () =>
  api.get<ScoringConfig>('/scoring-config').then((r) => r.data);

const previewConfig = (body: Partial<ScoringConfig>) =>
  api.post<SensitivityPreview>('/scoring-config/preview', body).then((r) => r.data);

// ── Helpers ────────────────────────────────────────────────────────────────────

function clamp01(v: number) { return Math.max(0, Math.min(1, v)); }

function WeightSlider({
  label, desc, value, onChange, color = 'bg-brand',
}: {
  label: string; desc: string; value: number;
  onChange: (v: number) => void; color?: string;
}) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <label className="text-sm font-semibold text-gray-700">{label}</label>
        <span className="text-sm font-mono font-bold text-gray-900">
          {(value * 100).toFixed(0)}%
        </span>
      </div>
      <input
        type="range"
        min={0} max={100}
        value={Math.round(value * 100)}
        onChange={(e) => onChange(Number(e.target.value) / 100)}
        className="h-2 w-full cursor-pointer appearance-none rounded-full bg-gray-200 accent-brand"
      />
      <p className="text-xs text-gray-500">{desc}</p>
    </div>
  );
}

function ThresholdSlider({
  label, desc, value, min, max, onChange, colorClass,
}: {
  label: string; desc: string; value: number; min: number; max: number;
  onChange: (v: number) => void; colorClass: string;
}) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <label className="text-sm font-semibold text-gray-700">{label}</label>
        <span className={`rounded px-2 py-0.5 text-xs font-bold ${colorClass}`}>
          {value.toFixed(0)}
        </span>
      </div>
      <input
        type="range"
        min={min} max={max}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="h-2 w-full cursor-pointer appearance-none rounded-full bg-gray-200 accent-brand"
      />
      <p className="text-xs text-gray-500">{desc}</p>
    </div>
  );
}

function PreviewBar({
  label, current, proposed, color,
}: {
  label: string; current: number; proposed: number; color: string;
}) {
  const diff = proposed - current;
  return (
    <div className="flex items-center gap-3 text-sm">
      <span className={`w-20 shrink-0 rounded px-2 py-0.5 text-center text-xs font-bold ${color}`}>
        {label}
      </span>
      <div className="flex-1">
        <span className="font-mono text-gray-600">{current} atual</span>
        <ChevronRight size={12} className="mx-1 inline text-gray-400" />
        <span className="font-mono font-bold text-gray-900">{proposed} estimado</span>
      </div>
      <span className={`text-xs font-semibold ${diff > 0 ? 'text-orange-600' : diff < 0 ? 'text-green-600' : 'text-gray-400'}`}>
        {diff > 0 ? `+${diff}` : diff < 0 ? `${diff}` : '='}
      </span>
    </div>
  );
}

// ── Página ─────────────────────────────────────────────────────────────────────

export default function SensitivityPage() {
  const qc = useQueryClient();

  const { data: config, isLoading, error } = useQuery({
    queryKey: ['scoring-config'],
    queryFn: fetchConfig,
  });

  const [form, setForm] = useState<Partial<ScoringConfig>>({});
  const [preview, setPreview] = useState<SensitivityPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [saved, setSaved] = useState(false);
  const [weightsError, setWeightsError] = useState('');

  useEffect(() => {
    if (config) setForm(config);
  }, [config]);

  const totalWeight = (form.rule_weight ?? 0) + (form.ml_weight ?? 0) + (form.network_weight ?? 0);
  const weightsOk   = Math.abs(totalWeight - 1) < 0.02;

  const runPreview = useCallback(async () => {
    if (!form.low_threshold) return;
    setPreviewLoading(true);
    try {
      const res = await previewConfig(form);
      setPreview(res);
    } catch {}
    setPreviewLoading(false);
  }, [form]);

  const save = useMutation({
    mutationFn: () => api.put('/scoring-config', form),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['scoring-config'] });
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    },
  });

  const update = (key: keyof ScoringConfig) => (val: number) => {
    setForm((prev) => ({ ...prev, [key]: val }));
    setWeightsError('');
  };

  if (isLoading) return <div className="p-8 text-center text-gray-400">Carregando configurações…</div>;
  if (error)     return <div className="p-8 text-center text-red-500">Erro ao carregar. Verifique se seu perfil tem permissão de Administrador.</div>;
  if (!form.low_threshold) return null;

  return (
    <div className="max-w-3xl space-y-6">
      {/* Cabeçalho */}
      <div>
        <div className="flex items-center gap-2">
          <SlidersHorizontal size={22} className="text-brand" />
          <h1 className="text-2xl font-bold text-gray-900">Calibração de Sensibilidade</h1>
        </div>
        <p className="mt-1 text-sm text-gray-500">
          Controle quantos alertas o sistema gera por dia e com que urgência.
          Use “Simular impacto” para ver o efeito de qualquer ajuste antes de salvar.
        </p>
      </div>

      {/* Contextual help */}
      <div className="rounded-xl border border-blue-100 bg-blue-50 p-4 text-sm text-blue-900">
        <div className="flex items-start gap-2">
          <HelpCircle size={16} className="mt-0.5 shrink-0 text-blue-500" />
          <div>
            <p className="font-semibold">Como interpretar os limiares</p>
            <p className="mt-1 text-blue-800">
              O sistema calcula um <strong>score de 0 a 100</strong> para cada apostador combinando regras,
              ML e análise de rede. Alertas são gerados quando o score supera o limiar da faixa correspondente.
              Limiares menores → mais alertas (maior recall). Limiares maiores → menos alertas (maior precisão).
            </p>
          </div>
        </div>
      </div>

      {/* Pesos dos componentes */}
      <section className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
        <h2 className="mb-1 text-base font-bold text-gray-800">Como o sistema avalia o risco
          <ContextualHelp title="O que cada componente significa?" side="right">
            <p className="mb-1">A pontuação de risco combina três perspectivas:</p>
            <ul className="space-y-1 pl-2">
              <li>• <strong>Condições de risco:</strong> regras definidas pelo time de PLD (ex: “depósitos acima de R$ 50k em 24h”)</li>
              <li>• <strong>Análise de comportamento:</strong> desvios do padrão histórico do próprio apostador</li>
              <li>• <strong>Rede de vínculos:</strong> conexões com outros apostadores via dispositivo ou chave Pix</li>
            </ul>
            <p className="mt-2 text-gray-500">A soma deve ser sempre 100%. Se você aumenta um componente, precisa reduzir outro.</p>
          </ContextualHelp>
        </h2>
        <p className="mb-4 text-xs text-gray-500">
          A soma dos três pesos deve ser igual a 100%.{' '}
          <span className={`font-bold ${weightsOk ? 'text-green-600' : 'text-red-600'}`}>
            Atual: {(totalWeight * 100).toFixed(0)}%
          </span>
        </p>
        <div className="space-y-5">
          <WeightSlider
            label="Condições de risco cadastradas"
            desc="Quanto as regras de PLD definidas pelo seu time pesam. Mais alto = condições cadastradas têm mais influência no score."
            value={form.rule_weight ?? 0.4}
            onChange={update('rule_weight')}
          />
          <WeightSlider
            label="Análise de comportamento (IA)"
            desc="Quanto os desvios do padrão histórico do apostador pesam. Mais alto = comportamento incomum tem mais influência."
            value={form.ml_weight ?? 0.4}
            onChange={update('ml_weight')}
          />
          <WeightSlider
            label="Rede de vínculos"
            desc="Quanto as conexões com outros apostadores (dispositivos, chaves Pix compartilhadas) pesam. Mais alto = rede suspeita tem mais influência."
            value={form.network_weight ?? 0.2}
            onChange={update('network_weight')}
          />
        </div>
        {!weightsOk && (
          <p className="mt-3 text-xs font-semibold text-red-600">
            ⚠ Soma dos pesos deve ser 100% para salvar. Ajuste os valores.
          </p>
        )}
      </section>

      {/* Limiares de severidade */}
      <section className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
        <h2 className="mb-1 text-base font-bold text-gray-800">Faixas de risco
          <ContextualHelp title="O que são as faixas de risco?" side="right">
            <p className="mb-1">Definem a partir de qual pontuação (0–100) um alerta é classificado em cada categoria:</p>
            <ul className="space-y-1 pl-2">
              <li>• <strong>Abaixar</strong> o valor → mais alertas gerados (sistema mais rigoroso)</li>
              <li>• <strong>Elevar</strong> o valor → menos alertas (sistema mais seletivo)</li>
            </ul>
            <p className="mt-2 text-gray-500">Use “Simular impacto” para ver quantos alertas cada ajuste geraria antes de salvar.</p>
          </ContextualHelp>
        </h2>
        <p className="mb-4 text-xs text-gray-500">
          Quanto menor o valor, mais alertas são gerados para aquela categoria. Clique em “Simular impacto” após ajustar.
        </p>
        <div className="space-y-5">
          <ThresholdSlider
            label="Risco Baixo — a partir de"
            desc="Pontuação mínima para gerar um alerta de baixa prioridade. Sugerido: 30–40."
            value={form.low_threshold ?? 30}
            min={10} max={(form.medium_threshold ?? 60) - 1}
            onChange={update('low_threshold')}
            colorClass="bg-green-100 text-green-700"
          />
          <ThresholdSlider
            label="Risco Médio — a partir de"
            desc="Pontuação mínima para classificar o alerta como prioridade média (amarelo). Sugerido: 55–65."
            value={form.medium_threshold ?? 60}
            min={(form.low_threshold ?? 30) + 1}
            max={(form.high_threshold ?? 80) - 1}
            onChange={update('medium_threshold')}
            colorClass="bg-yellow-100 text-yellow-700"
          />
          <ThresholdSlider
            label="Risco Alto — a partir de"
            desc="Pontuação mínima para alto risco. Casos podem ser abertos automaticamente acima deste valor. Sugerido: 75–85."
            value={form.high_threshold ?? 80}
            min={(form.medium_threshold ?? 60) + 1}
            max={(form.critical_threshold ?? 95) - 1}
            onChange={update('high_threshold')}
            colorClass="bg-orange-100 text-orange-700"
          />
          <ThresholdSlider
            label="Risco Crítico — a partir de"
            desc="Pontuação mínima para alertas críticos. SLA reduzido e escalonamento obrigatório. Sugerido: 90–95."
            value={form.critical_threshold ?? 95}
            min={(form.high_threshold ?? 80) + 1}
            max={99}
            onChange={update('critical_threshold')}
            colorClass="bg-red-100 text-red-700"
          />
        </div>
      </section>

      {/* Política operacional */}
      <section className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
        <h2 className="mb-1 flex items-center gap-2 text-base font-bold text-gray-800">
          <Shield size={16} /> Política de PLD
        </h2>
        <p className="mb-4 text-xs text-gray-500">
          Defina quando o sistema abre casos automaticamente e como consolida a banda de risco do apostador.
        </p>
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="rounded-lg border border-gray-200 p-3">
            <label className="block text-[11px] font-semibold uppercase tracking-wide text-gray-500">Auto-case acima de</label>
            <input
              type="number"
              min={0}
              max={1}
              step={0.01}
              value={form.auto_case_threshold ?? 0.75}
              onChange={(e) => update('auto_case_threshold')(Number(e.target.value))}
              className="mt-1 w-full rounded border border-gray-200 px-2 py-1 text-sm font-mono focus:outline-none focus:ring-1 focus:ring-brand"
            />
            <span className="text-[10px] text-gray-400">score composto de 0 a 1</span>
          </div>
          <div className="rounded-lg border border-gray-200 p-3">
            <label className="block text-[11px] font-semibold uppercase tracking-wide text-gray-500">Renda x volume</label>
            <input
              type="number"
              min={0.1}
              max={20}
              step={0.1}
              value={form.income_volume_ratio_threshold ?? 1.5}
              onChange={(e) => update('income_volume_ratio_threshold')(Number(e.target.value))}
              className="mt-1 w-full rounded border border-gray-200 px-2 py-1 text-sm font-mono focus:outline-none focus:ring-1 focus:ring-brand"
            />
            <span className="text-[10px] text-gray-400">múltiplo da renda mensal declarada</span>
          </div>
          <div className="rounded-lg border border-gray-200 p-3">
            <label className="block text-[11px] font-semibold uppercase tracking-wide text-gray-500">Banda média a partir de</label>
            <input
              type="number"
              min={0}
              max={1}
              step={0.01}
              value={form.risk_band_low_threshold ?? 0.35}
              onChange={(e) => update('risk_band_low_threshold')(Number(e.target.value))}
              className="mt-1 w-full rounded border border-gray-200 px-2 py-1 text-sm font-mono focus:outline-none focus:ring-1 focus:ring-brand"
            />
            <span className="text-[10px] text-gray-400">score persistido no cadastro</span>
          </div>
          <div className="rounded-lg border border-gray-200 p-3">
            <label className="block text-[11px] font-semibold uppercase tracking-wide text-gray-500">Banda alta a partir de</label>
            <input
              type="number"
              min={0}
              max={1}
              step={0.01}
              value={form.risk_band_high_threshold ?? 0.7}
              onChange={(e) => update('risk_band_high_threshold')(Number(e.target.value))}
              className="mt-1 w-full rounded border border-gray-200 px-2 py-1 text-sm font-mono focus:outline-none focus:ring-1 focus:ring-brand"
            />
            <span className="text-[10px] text-gray-400">score persistido no cadastro</span>
          </div>
        </div>
      </section>

      {/* SLA */}
      <section className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
        <h2 className="mb-1 flex items-center gap-2 text-base font-bold text-gray-800">
          <Clock size={16} /> SLA por severidade
        </h2>
        <p className="mb-4 text-xs text-gray-500">
          Prazo máximo (em horas) para um analista tomar decisão sobre um alerta.
          Alertas vencidos aparecem em vermelho na fila.
        </p>
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          {(
            [
              { key: 'sla_low_hours',      label: 'Baixo',    cls: 'border-green-200' },
              { key: 'sla_medium_hours',   label: 'Médio',    cls: 'border-yellow-200' },
              { key: 'sla_high_hours',     label: 'Alto',     cls: 'border-orange-200' },
              { key: 'sla_critical_hours', label: 'Crítico',  cls: 'border-red-200' },
            ] as { key: keyof ScoringConfig; label: string; cls: string }[]
          ).map(({ key, label, cls }) => (
            <div key={key} className={`rounded-lg border-2 p-3 ${cls}`}>
              <label className="block text-[11px] font-semibold uppercase tracking-wide text-gray-500">{label}</label>
              <input
                type="number"
                min={1} max={720}
                value={(form[key] as number) ?? 24}
                onChange={(e) => update(key)(Number(e.target.value))}
                className="mt-1 w-full rounded border border-gray-200 px-2 py-1 text-sm font-mono focus:outline-none focus:ring-1 focus:ring-brand"
              />
              <span className="text-[10px] text-gray-400">horas</span>
            </div>
          ))}
        </div>
      </section>

      {/* Preview impacto */}
      <section className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-base font-bold text-gray-800">Simular impacto (últimos 30 dias)</h2>
          <button
            onClick={runPreview}
            disabled={previewLoading}
            className="flex items-center gap-2 rounded-lg border border-brand px-4 py-2 text-sm font-semibold text-brand hover:bg-brand/5 disabled:opacity-50"
          >
            <RefreshCw size={14} className={previewLoading ? 'animate-spin' : ''} />
            {previewLoading ? 'Calculando…' : 'Simular impacto'}
          </button>
        </div>

        {!preview && (
          <p className="text-sm text-gray-400">
            Clique em "Simular impacto" para ver quantos alertas cada categoria teria com a nova configuração.
          </p>
        )}

        {preview && (
          <div className="space-y-3">
            <p className="text-xs text-gray-500">
              Base: <strong>{preview.total_alerts_30d}</strong> alertas nos últimos 30 dias.
              Diferenças positivas (+) indicam mais alertas; negativas (−) indicam redução.
            </p>
            <PreviewBar label="Baixo"   color="bg-green-100 text-green-700"    current={preview.current.low}      proposed={preview.proposed.low} />
            <PreviewBar label="Médio"   color="bg-yellow-100 text-yellow-700"  current={preview.current.medium}   proposed={preview.proposed.medium} />
            <PreviewBar label="Alto"    color="bg-orange-100 text-orange-700"  current={preview.current.high}     proposed={preview.proposed.high} />
            <PreviewBar label="Crítico" color="bg-red-100 text-red-700"        current={preview.current.critical} proposed={preview.proposed.critical} />
          </div>
        )}
      </section>

      {/* Ações */}
      <div className="flex items-center justify-between rounded-xl border border-gray-100 bg-white px-6 py-4 shadow-sm">
        <p className="text-xs text-gray-500">
          {config?.updated_at
            ? `Última alteração: ${new Date(config.updated_at).toLocaleString('pt-BR')}`
            : 'Configuração ainda não foi alterada.'}
        </p>
        <div className="flex items-center gap-3">
          {saved && (
            <span className="text-sm font-semibold text-green-600">✓ Salvo com sucesso</span>
          )}
          <button
            onClick={() => { if (!weightsOk) return; save.mutate(); }}
            disabled={!weightsOk || save.isPending}
            className="flex items-center gap-2 rounded-lg bg-brand px-5 py-2 text-sm font-semibold text-white hover:bg-brand/90 disabled:cursor-not-allowed disabled:opacity-40"
          >
            <Save size={15} />
            {save.isPending ? 'Salvando…' : 'Aplicar configurações'}
          </button>
        </div>
      </div>
    </div>
  );
}
