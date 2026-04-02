'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { useMutation, useQuery } from '@tanstack/react-query';
import { BarChart, Bar, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import {
  fetchRules, simulateRule, SimulateRuleResult, Rule,
  fetchModelPerformanceSummary, RulePerformanceItem,
} from '@/lib/api';
import {
  Activity, AlertTriangle, ChevronDown, ChevronUp, Edit2, FlaskConical,
  Plus, Search, ShieldAlert, ShieldCheck, ShieldOff,
} from 'lucide-react';

type SimMode = 'manual' | 'historical';

const SEVERITY_CONFIG: Record<string, { label: string; cls: string }> = {
  LOW:      { label: 'Baixo',    cls: 'bg-blue-50 text-blue-700 border-blue-200' },
  MEDIUM:   { label: 'Médio',   cls: 'bg-yellow-50 text-yellow-700 border-yellow-200' },
  HIGH:     { label: 'Alto',    cls: 'bg-orange-50 text-orange-700 border-orange-200' },
  CRITICAL: { label: 'Crítico', cls: 'bg-red-50 text-red-700 border-red-200' },
};

const SCOPE_LABEL: Record<string, string> = {
  TRANSACTION: 'Transação',
  BET: 'Aposta',
  PLAYER: 'Jogador',
};

function fpBadge(rate?: number | null) {
  if (rate == null) return null;
  const pct = rate * 100;
  if (pct <= 15) return { cls: 'bg-green-50 text-green-700 border-green-200', icon: <ShieldCheck size={12} />, label: `${pct.toFixed(0)}% falsos alarmes` };
  if (pct <= 35) return { cls: 'bg-yellow-50 text-yellow-700 border-yellow-200', icon: <ShieldAlert size={12} />, label: `${pct.toFixed(0)}% falsos alarmes` };
  return { cls: 'bg-red-50 text-red-700 border-red-200', icon: <ShieldOff size={12} />, label: `${pct.toFixed(0)}% falsos alarmes` };
}

function pct(value?: number | null) {
  if (value == null) return '—';
  return `${(value * 100).toFixed(1)}%`;
}

function RuleCard({
  rule,
  perfMap,
  onSimulate,
}: {
  rule: Rule;
  perfMap: Map<string, RulePerformanceItem>;
  onSimulate: (rule: Rule) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const sev = SEVERITY_CONFIG[rule.severity] ?? SEVERITY_CONFIG.LOW;
  const perf = perfMap.get(rule.id) ?? perfMap.get(rule.name);
  const fp = fpBadge(perf?.false_positive_rate);
  const isActive = rule.status?.toUpperCase() === 'ACTIVE' || rule.status === 'active';

  return (
    <div className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm transition-shadow hover:shadow-md dark:border-gray-700 dark:bg-gray-900">
      <div className="flex items-start gap-4 p-5">
        <div className="flex-1 min-w-0">
          {/* Row 1 — name + badges */}
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-semibold text-gray-900 dark:text-white truncate">{rule.name}</span>
            <span className={`rounded-full border px-2 py-0.5 text-xs font-semibold ${sev.cls}`}>
              {sev.label}
            </span>
            <span className="rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-600 dark:bg-gray-700 dark:text-gray-400">
              {SCOPE_LABEL[rule.scope] ?? rule.scope}
            </span>
            {fp && (
              <span className={`flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-semibold ${fp.cls}`}>
                {fp.icon} {fp.label}
              </span>
            )}
            <span className={`ml-auto flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${isActive ? 'bg-green-50 text-green-700' : 'bg-gray-100 text-gray-500'}`}>
              <Activity size={11} />
              {isActive ? 'Ativa' : 'Inativa'}
            </span>
          </div>

          {/* Row 2 — description */}
          {rule.description && (
            <p className="mt-1.5 text-sm text-gray-500 dark:text-gray-400 line-clamp-2">{rule.description}</p>
          )}

          {/* Row 3 — metadata */}
          <div className="mt-2 flex flex-wrap gap-3 text-xs text-gray-400">
            <span>v{rule.version}</span>
            <span>Influência: {((rule.weight ?? 0.5) * 100).toFixed(0)}%</span>
            {perf && (
              <>
                <span className="text-green-600 font-medium">Precisão {pct(perf.precision_estimated)}</span>
                <span>{perf.total_alerts.toLocaleString('pt-BR')} alertas revisados</span>
              </>
            )}
          </div>
        </div>
      </div>

      {/* Expandable DSL */}
      {expanded && (
        <div className="border-t border-gray-100 bg-gray-50 px-5 py-3 dark:border-gray-700 dark:bg-gray-800">
          <p className="mb-1 text-xs font-medium text-gray-500">Lógica da condição (técnico)</p>
          <pre className="overflow-x-auto font-mono text-xs text-gray-600 dark:text-gray-300 leading-relaxed">
            {rule.condition_dsl}
          </pre>
        </div>
      )}

      {/* Actions */}
      <div className="flex items-center justify-between border-t border-gray-100 bg-gray-50/50 px-5 py-3 dark:border-gray-700 dark:bg-gray-800/50">
        <button
          onClick={() => setExpanded((v) => !v)}
          className="flex items-center gap-1 text-xs text-gray-400 hover:text-gray-600"
        >
          {expanded ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
          {expanded ? 'Ocultar lógica técnica' : 'Ver lógica técnica'}
        </button>
        <div className="flex items-center gap-2">
          <Link
            href={`/rules/builder?ruleId=${rule.id}`}
            className="flex items-center gap-1.5 rounded-lg border border-gray-200 px-3 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-100 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-700"
          >
            <Edit2 size={12} /> Editar
          </Link>
          <button
            onClick={() => onSimulate(rule)}
            className="flex items-center gap-1.5 rounded-lg bg-brand px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand/90"
          >
            <FlaskConical size={12} /> Testar
          </button>
        </div>
      </div>
    </div>
  );
}


export default function RulesPage() {
  const { data: rules = [], isLoading } = useQuery({
    queryKey: ['rules'],
    queryFn: fetchRules,
  });

  const { data: perfSummary } = useQuery({
    queryKey: ['model-registry', 'performance', 30],
    queryFn: () => fetchModelPerformanceSummary(30),
  });

  const perfMap = useMemo(() => {
    const m = new Map<string, RulePerformanceItem>();
    perfSummary?.by_rule.forEach((item) => {
      if (item.rule_id) m.set(item.rule_id, item);
      m.set(item.rule_name, item);
    });
    return m;
  }, [perfSummary]);

  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState<Rule | null>(null);
  const [simMode, setSimMode] = useState<SimMode>('manual');
  const [simPayload, setSimPayload] = useState('{}');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [playerIds, setPlayerIds] = useState('');
  const [simResult, setSimResult] = useState<SimulateRuleResult | null>(null);

  const filteredRules = useMemo(() =>
    search.trim()
      ? rules.filter((r) =>
          r.name.toLowerCase().includes(search.toLowerCase()) ||
          r.description?.toLowerCase().includes(search.toLowerCase()) ||
          r.scope.toLowerCase().includes(search.toLowerCase()),
        )
      : rules,
    [rules, search],
  );

  const simulate = useMutation({
    mutationFn: async () => {
      if (!selected) throw new Error('Nenhuma regra selecionada');
      if (simMode === 'historical') {
        return simulateRule(selected.id, {
          from: dateFrom || undefined,
          to: dateTo || undefined,
          player_ids: playerIds
            .split(',')
            .map((v) => v.trim())
            .filter(Boolean),
        });
      }

      let payload: Record<string, unknown>;
      try {
        payload = JSON.parse(simPayload);
      } catch {
        payload = {};
      }
      const body = Array.isArray(payload.events) ? payload : { events: [payload] };
      return simulateRule(selected.id, body);
    },
    onSuccess: (data) => setSimResult(data),
  });

  const chartData = useMemo(() => simResult?.timeline ?? [], [simResult]);

  const openSimulate = (rule: Rule) => {
    setSelected(rule);
    setSimResult(null);
    setSimPayload('{}');
    setSimMode('historical');
    const today = new Date().toISOString().slice(0, 10);
    const from = new Date(Date.now() - 30 * 86400000).toISOString().slice(0, 10);
    setDateFrom(from);
    setDateTo(today);
  };

  const highFpRules = filteredRules.filter((r) => {
    const p = perfMap.get(r.id) ?? perfMap.get(r.name);
    return p && p.false_positive_rate > 0.35;
  });

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Condições de Risco</h1>
          <p className="mt-1 text-sm text-gray-500">
            {rules.length} condição{rules.length !== 1 ? 'ões' : ''} — clique em <strong>Testar</strong> para simular com histórico real.
          </p>
        </div>
        <Link
          href="/rules/builder"
          className="flex items-center gap-2 rounded-xl bg-brand px-4 py-2.5 text-sm font-semibold text-white hover:bg-brand/90 self-start"
        >
          <Plus size={15} /> Nova condição
        </Link>
      </div>

      {/* High-FP alert banner */}
      {highFpRules.length > 0 && (
        <div className="flex items-start gap-3 rounded-xl border border-orange-200 bg-orange-50 p-4 text-sm text-orange-800">
          <AlertTriangle size={16} className="mt-0.5 flex-shrink-0 text-orange-500" />
          <div>
            <p className="font-semibold">
              {highFpRules.length} condição{highFpRules.length > 1 ? 'ões com' : ' com'} taxa de falso alarme acima de 35%
            </p>
            <p className="mt-0.5 text-orange-700">
              {highFpRules.map((r) => r.name).join(', ')} — sugerimos revisar ou desativar estas condições.
            </p>
          </div>
        </div>
      )}

      {/* Search */}
      <div className="relative">
        <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
        <input
          type="search"
          placeholder="Buscar por nome, descrição ou escopo…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-full rounded-xl border border-gray-200 bg-white py-2.5 pl-9 pr-4 text-sm focus:ring-2 focus:ring-brand dark:border-gray-700 dark:bg-gray-800 dark:text-white"
        />
      </div>

      {/* Rule cards */}
      {isLoading ? (
        <div className="grid gap-4 md:grid-cols-2">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="h-36 animate-pulse rounded-xl bg-gray-100 dark:bg-gray-700" />
          ))}
        </div>
      ) : filteredRules.length === 0 ? (
        <div className="rounded-xl border border-dashed border-gray-200 py-16 text-center dark:border-gray-700">
          <ShieldOff size={32} className="mx-auto mb-3 text-gray-300" />
          <p className="text-sm text-gray-500">
            {search ? 'Nenhuma condição encontrada para esta busca.' : 'Nenhuma condição cadastrada ainda.'}
          </p>
          {!search && (
            <Link href="/rules/builder" className="mt-4 inline-flex items-center gap-1.5 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white">
              <Plus size={14} /> Criar primeira condição
            </Link>
          )}
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {filteredRules.map((rule) => (
            <RuleCard key={rule.id} rule={rule} perfMap={perfMap} onSimulate={openSimulate} />
          ))}
        </div>
      )}

      {selected && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="max-h-[90vh] w-full max-w-5xl overflow-y-auto rounded-2xl bg-white p-6 shadow-xl dark:bg-gray-900">
            <div className="mb-4 flex items-start justify-between gap-4">
              <div>
                <h2 className="text-lg font-semibold">{selected.name}</h2>
                <p className="text-xs text-gray-400">
                  Âmbito: {SCOPE_LABEL[selected.scope] ?? selected.scope} · Versão {selected.version} · Influência: {((selected.weight ?? 0.5) * 100).toFixed(0)}%
                </p>
              </div>
              <button onClick={() => setSelected(null)} className="rounded-lg border px-3 py-1.5 text-sm">
                Fechar
              </button>
            </div>

            <div className="mb-4 grid gap-4 lg:grid-cols-[1.2fr_1fr]">
              <div>
                <label className="mb-1 block text-xs font-medium text-gray-600">Lógica da condição (técnico)</label>
                <pre className="overflow-x-auto rounded-lg bg-gray-50 p-3 text-xs dark:bg-gray-800">
                  {selected.condition_dsl}
                </pre>
              </div>

              <div className="rounded-xl border border-gray-100 p-4 dark:border-gray-800">
                <div className="mb-3 flex gap-2">
                  <button
                    onClick={() => setSimMode('manual')}
                    className={`rounded-lg px-3 py-1.5 text-sm ${simMode === 'manual' ? 'bg-brand text-white' : 'border'}`}
                  >
                    Manual
                  </button>
                  <button
                    onClick={() => setSimMode('historical')}
                    className={`rounded-lg px-3 py-1.5 text-sm ${simMode === 'historical' ? 'bg-brand text-white' : 'border'}`}
                  >
                    Histórico
                  </button>
                </div>

                {simMode === 'manual' ? (
                  <>
                    <label className="mb-1 block text-xs font-medium text-gray-600">
                      Parâmetros de teste (JSON)
                    </label>
                    <textarea
                      rows={7}
                      value={simPayload}
                      onChange={(e) => setSimPayload(e.target.value)}
                      className="w-full rounded-lg border px-3 py-2 font-mono text-xs dark:bg-gray-800 dark:text-gray-200"
                      placeholder='{"transaction":{"amount":5000,"type":"DEPOSIT"},"features":{"deposit_sum_24h":10000},"player":{"pep_flag":true}}'
                    />
                  </>
                ) : (
                  <div className="space-y-3">
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="mb-1 block text-xs font-medium text-gray-600">De</label>
                        <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className="w-full rounded-lg border px-3 py-2 text-sm dark:bg-gray-800" />
                      </div>
                      <div>
                        <label className="mb-1 block text-xs font-medium text-gray-600">Até</label>
                        <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className="w-full rounded-lg border px-3 py-2 text-sm dark:bg-gray-800" />
                      </div>
                    </div>
                    <div>
                      <label className="mb-1 block text-xs font-medium text-gray-600">IDs dos apostadores (opcional)</label>
                      <input
                        value={playerIds}
                        onChange={(e) => setPlayerIds(e.target.value)}
                        className="w-full rounded-lg border px-3 py-2 text-sm dark:bg-gray-800"
                        placeholder="apostador-1, apostador-2"
                      />
                    </div>
                  </div>
                )}

                <button
                  onClick={() => simulate.mutate()}
                  disabled={simulate.isPending}
                  className="mt-4 w-full rounded-lg bg-brand py-2 text-sm text-white disabled:opacity-50"
                >
                  {simulate.isPending ? 'Testando...' : 'Testar com este histórico'}
                </button>
              </div>
            </div>

            {simResult && (
              <div className="space-y-4">
                <div className="grid gap-3 md:grid-cols-5">
                  <div className="rounded-xl border border-gray-100 p-4 dark:border-gray-800">
                    <p className="text-xs text-gray-400">Alertas</p>
                    <p className="mt-1 text-xl font-semibold">{simResult.total_alerts ?? simResult.matches}</p>
                  </div>
                  <div className="rounded-xl border border-gray-100 p-4 dark:border-gray-800">
                    <p className="text-xs text-gray-400">Apostadores</p>
                    <p className="mt-1 text-xl font-semibold">{simResult.players?.length ?? 0}</p>
                  </div>
                  <div className="rounded-xl border border-gray-100 p-4 dark:border-gray-800">
                    <p className="text-xs text-gray-400">Precisão est.</p>
                    <p className="mt-1 text-xl font-semibold">{pct(simResult.precision_estimated)}</p>
                  </div>
                  <div className="rounded-xl border border-gray-100 p-4 dark:border-gray-800">
                    <p className="text-xs text-gray-400">Recall est.</p>
                    <p className="mt-1 text-xl font-semibold">{pct(simResult.recall_estimated)}</p>
                  </div>
                  <div className="rounded-xl border border-gray-100 p-4 dark:border-gray-800">
                    <p className="text-xs text-gray-400">Falso alarme est.</p>
                    <p className="mt-1 text-xl font-semibold">{pct(simResult.false_positive_estimated)}</p>
                  </div>
                </div>

                {chartData.length > 0 && (
                  <div className="rounded-xl border border-gray-100 bg-white p-4 dark:border-gray-800 dark:bg-gray-900">
                    <h3 className="mb-3 text-sm font-semibold">Alertas por dia</h3>
                    <div className="h-64">
                      <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={chartData}>
                          <CartesianGrid strokeDasharray="3 3" />
                          <XAxis dataKey="date" tick={{ fontSize: 12 }} />
                          <YAxis allowDecimals={false} tick={{ fontSize: 12 }} />
                          <Tooltip />
                          <Bar dataKey="alerts" fill="#0f766e" radius={[6, 6, 0, 0]} />
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                  </div>
                )}

                {simMode === 'manual' && simResult.results.length > 0 && (
                  <div className="rounded-xl border border-gray-100 bg-white p-4 dark:border-gray-800 dark:bg-gray-900">
                    <h3 className="mb-3 text-sm font-semibold">Resultados do teste manual</h3>
                    <div className="space-y-2">
                      {simResult.results.map((result, idx) => (
                        <div key={idx} className={`rounded-lg border px-3 py-2 text-sm ${result.matched ? 'border-red-200 bg-red-50' : 'border-green-200 bg-green-50'}`}>
                          <p className="font-medium">{result.matched ? 'Disparou' : 'Não disparou'}</p>
                          {result.error && <p className="mt-1 text-xs text-orange-700">Erro: {result.error}</p>}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
