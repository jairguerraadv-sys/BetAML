'use client';
import { useState, useCallback } from 'react';
import { useMutation } from '@tanstack/react-query';
import { api, validateDsl, createRule, simulateRule, previewDsl, SimulateRuleResult } from '@/lib/api';
import {
  Plus, Trash2, Play, Save, Copy, ChevronDown, ChevronRight,
  AlertTriangle, Info, CheckCircle2, X, ToggleLeft, ToggleRight,
  Search, TrendingUp, Users, Target,
} from 'lucide-react';

// ── Tipos ─────────────────────────────────────────────────────────────────────

type FieldType = 'number' | 'string' | 'select';
type Operator = 'gt' | 'gte' | 'lt' | 'lte' | 'eq' | 'neq' | 'in' | 'contains';
type Severity = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
type Scope = 'TRANSACTION' | 'BET' | 'PLAYER';
type JoinOp = 'and' | 'or';

interface FieldDef {
  key: string;      // real DSL field path, e.g. "features.deposit_count_24h"
  label: string;
  type: FieldType;
  unit?: string;
  options?: string[];
  hint?: string;
}

interface Condition {
  id: string;
  field: string;
  operator: Operator;
  value: string;
}

interface RuleTemplate {
  id: string;
  name: string;
  description: string;
  icon: string;
  tag: string;
  conditions: Omit<Condition, 'id'>[];
  severity: Severity;
  scope: Scope;
}

// ── Campos disponíveis (chave = caminho DSL real) ───────────────────────────

const FIELDS: FieldDef[] = [
  { key: 'features.deposit_sum_30d',               label: 'Volume depósitos (30d)',         type: 'number', unit: 'R$',         hint: 'Soma dos depósitos nos últimos 30 dias' },
  { key: 'features.deposit_count_24h',             label: 'Depósitos em 24h',               type: 'number', unit: 'N',          hint: 'Indicador de estruturação/smurfing' },
  { key: 'features.withdrawal_sum_7d',             label: 'Volume saques (7d)',              type: 'number', unit: 'R$' },
  { key: 'features.chargeback_count_30d',          label: 'Estornos/contestações (30d)',    type: 'number', unit: 'N',          hint: 'Contagem de estornos Pix ou reversões administrativas nos últimos 30d' },
  { key: 'features.avg_deposit_to_withdrawal_hours', label: 'Tempo médio depósito→saque',   type: 'number', unit: 'horas',      hint: 'Valores baixos indicam layering' },
  { key: 'features.shared_device_count',           label: 'Dispositivos compartilhados',    type: 'number', unit: 'N' },
  { key: 'features.unique_instruments_7d',         label: 'Instrumentos únicos (7d)',        type: 'number', unit: 'N' },
  { key: 'features.cashout_ratio_7d',              label: '% do saldo sacado (7d)',          type: 'number', unit: '0–1' },
  { key: 'features.cluster_size',                  label: 'Apostadores no mesmo grupo',     type: 'number', unit: 'N' },
  { key: 'player.risk_score',                      label: 'Score de risco',                 type: 'number', unit: '0–100' },
  { key: 'player.pep_flag',                        label: 'É PEP',                          type: 'select', options: ['true', 'false'] },
  { key: 'transaction.amount',                     label: 'Valor da transação',             type: 'number', unit: 'R$' },
  { key: 'features.bonus_to_real_ratio_30d',       label: 'Bônus vs depósitos reais (30d)', type: 'number', unit: '0–1',        hint: 'Proporção de créditos de bônus sobre depósitos reais' },
  { key: 'player.self_exclusion_flag',             label: 'Autoexclusão ativa',             type: 'select', options: ['true', 'false'], hint: 'Apostador registrado no SIGAP com autoexclusão vigente (Portaria 1.231/2024)' },
  { key: 'transaction.type',                       label: 'Tipo de transação',              type: 'select', options: ['DEPOSIT', 'WITHDRAWAL', 'REVERSAL', 'BONUS', 'CASHOUT', 'FREE_BET'] },
];

const OPERATORS: { value: Operator; label: string; types: FieldType[] }[] = [
  { value: 'gt',       label: '>  maior que',         types: ['number'] },
  { value: 'gte',      label: '>= maior ou igual a',  types: ['number'] },
  { value: 'lt',       label: '<  menor que',         types: ['number'] },
  { value: 'lte',      label: '<= menor ou igual a',  types: ['number'] },
  { value: 'eq',       label: '=  igual a',           types: ['number', 'string', 'select'] },
  { value: 'neq',      label: '≠  diferente de',      types: ['number', 'string', 'select'] },
  { value: 'in',       label: 'está em',              types: ['select', 'string'] },
  { value: 'contains', label: 'contém',               types: ['string'] },
];

const TEMPLATES: RuleTemplate[] = [
  {
    id: 'structuring',
    name: 'Estruturação (Smurfing)',
    description: 'Vários depósitos em 24h combinados com volume elevado em 30 dias',
    icon: '🏦', tag: 'COAF art. 11', severity: 'HIGH', scope: 'TRANSACTION',
    conditions: [
      { field: 'features.deposit_count_24h', operator: 'gte', value: '5' },
      { field: 'features.deposit_sum_30d',   operator: 'gte', value: '50000' },
    ],
  },
  {
    id: 'layering_speed',
    name: 'Layering — Saques rápidos',
    description: 'Retirada acelerada após depósito: menos de 2 horas',
    icon: '⚡', tag: 'Ciclo rápido', severity: 'CRITICAL', scope: 'TRANSACTION',
    conditions: [
      { field: 'features.avg_deposit_to_withdrawal_hours', operator: 'lte', value: '2' },
      { field: 'features.deposit_sum_30d',                 operator: 'gte', value: '100000' },
    ],
  },
  {
    id: 'pep_volume',
    name: 'PEP — Volume elevado',
    description: 'Pessoa politicamente exposta com movimentação incompatível',
    icon: '🏛️', tag: 'PEP / Res. 30', severity: 'HIGH', scope: 'PLAYER',
    conditions: [
      { field: 'player.pep_flag',          operator: 'eq',  value: 'true' },
      { field: 'features.deposit_sum_30d', operator: 'gte', value: '30000' },
    ],
  },
  {
    id: 'multi_device',
    name: 'Múltiplos dispositivos',
    description: 'Uso de muitos dispositivos compartilhados para fragmentar operações',
    icon: '📱', tag: 'Fragmentação', severity: 'MEDIUM', scope: 'TRANSACTION',
    conditions: [
      { field: 'features.shared_device_count',   operator: 'gte', value: '5' },
      { field: 'features.unique_instruments_7d', operator: 'gte', value: '3' },
    ],
  },
  {
    id: 'fast_cashout',
    name: 'Saque após depósito (ratio)',
    description: 'Saque quase total do valor depositado — ratio alto em 7 dias',
    icon: '💸', tag: 'Round-trip', severity: 'HIGH', scope: 'TRANSACTION',
    conditions: [
      { field: 'features.cashout_ratio_7d',  operator: 'gte', value: '0.9' },
      { field: 'features.deposit_sum_30d',   operator: 'gte', value: '10000' },
    ],
  },
  {
    id: 'bonus_abuse',
    name: 'Abuso de Bônus / Free Bets',
    description: 'Uso intensivo de bônus e free bets para movimentar saldo sem risco real — possível evasão via apostas',
    icon: '🎁', tag: 'Bônus Abuse', severity: 'MEDIUM', scope: 'PLAYER',
    conditions: [
      { field: 'features.bonus_to_real_ratio_30d', operator: 'gte', value: '0.7' },
      { field: 'features.cashout_ratio_7d',        operator: 'gte', value: '0.8' },
    ],
  },
  {
    id: 'self_exclusion_active',
    name: 'Aposta com Autoexclusão Ativa',
    description: 'Apostador com autoexclusão registrada realizando apostas — violação Portaria 1.231/2024 (jogo responsável)',
    icon: '🚫', tag: 'Jogo Responsável', severity: 'CRITICAL', scope: 'PLAYER',
    conditions: [
      { field: 'player.self_exclusion_flag', operator: 'eq',  value: 'true' },
      { field: 'player.risk_score',          operator: 'gte', value: '0' },
    ],
  },
];

const SEVERITY_COLORS: Record<Severity, string> = {
  LOW:      'bg-blue-100 text-blue-700',
  MEDIUM:   'bg-yellow-100 text-yellow-700',
  HIGH:     'bg-orange-100 text-orange-700',
  CRITICAL: 'bg-red-100 text-red-700',
};

function uid() { return Math.random().toString(36).slice(2, 9); }

function buildDsl(conditions: Condition[], joinOp: JoinOp): string {
  const parts = conditions
    .filter((c) => c.field && c.value)
    .map((c) => {
      const opMap: Record<Operator, string> = {
        gt: '>', gte: '>=', lt: '<', lte: '<=',
        eq: '==', neq: '!=', in: 'in', contains: 'contains',
      };
      const op = opMap[c.operator];
      const val = c.operator === 'in'
        ? `["${c.value.split(',').map((v) => v.trim()).join('","')}"]`
        : isNaN(Number(c.value)) && c.value !== 'true' && c.value !== 'false'
          ? `"${c.value}"`
          : c.value;
      return `${c.field} ${op} ${val}`;
    });
  return parts.join(` ${joinOp} `);
}

// ── ConditionRow ───────────────────────────────────────────────────────────

function ConditionRow({
  cond, index, joinOp, onChange, onRemove,
}: {
  cond: Condition; index: number; joinOp: JoinOp;
  onChange: (c: Condition) => void;
  onRemove: () => void;
}) {
  const field = FIELDS.find((f) => f.key === cond.field);
  const validOps = OPERATORS.filter((o) => !field || o.types.includes(field.type));

  return (
    <div className="flex items-start gap-2 group">
      <div className="w-16 flex-shrink-0 pt-2.5 text-right">
        {index === 0 ? (
          <span className="text-xs font-bold text-brand uppercase tracking-wide">Quando</span>
        ) : (
          <span className={`text-xs font-bold uppercase tracking-wide ${joinOp === 'and' ? 'text-blue-600' : 'text-purple-600'}`}>
            {joinOp === 'and' ? 'E' : 'OU'}
          </span>
        )}
      </div>

      <select
        className="flex-1 rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm focus:ring-2 focus:ring-brand dark:border-gray-700 dark:bg-gray-800 dark:text-white"
        value={cond.field}
        onChange={(e) => onChange({ ...cond, field: e.target.value, value: '' })}
      >
        <option value="">— Selecione o campo —</option>
        {FIELDS.map((f) => (
          <option key={f.key} value={f.key}>
            {f.label}{f.unit ? ` (${f.unit})` : ''}
          </option>
        ))}
      </select>

      <select
        className="w-44 flex-shrink-0 rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm focus:ring-2 focus:ring-brand dark:border-gray-700 dark:bg-gray-800 dark:text-white"
        value={cond.operator}
        onChange={(e) => onChange({ ...cond, operator: e.target.value as Operator })}
      >
        {validOps.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>

      {field?.type === 'select' ? (
        <select
          className="w-36 flex-shrink-0 rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm focus:ring-2 focus:ring-brand dark:border-gray-700 dark:bg-gray-800 dark:text-white"
          value={cond.value}
          onChange={(e) => onChange({ ...cond, value: e.target.value })}
        >
          <option value="">—</option>
          {(field.options ?? []).map((opt) => <option key={opt} value={opt}>{opt}</option>)}
        </select>
      ) : (
        <input
          type={field?.type === 'number' ? 'number' : 'text'}
          className="w-36 flex-shrink-0 rounded-lg border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-brand dark:border-gray-700 dark:bg-gray-800 dark:text-white"
          placeholder="valor"
          value={cond.value}
          onChange={(e) => onChange({ ...cond, value: e.target.value })}
        />
      )}

      <button
        onClick={onRemove}
        className="mt-2 opacity-0 transition-opacity group-hover:opacity-100 text-gray-400 hover:text-red-500"
        title="Remover condição"
      >
        <X size={16} />
      </button>
    </div>
  );
}

// ── Página principal ───────────────────────────────────────────────────────

export default function RuleBuilderPage() {
  const [name, setName]               = useState('');
  const [description, setDescription] = useState('');
  const [severity, setSeverity]       = useState<Severity>('MEDIUM');
  const [scope, setScope]             = useState<Scope>('TRANSACTION');
  const [joinOp, setJoinOp]           = useState<JoinOp>('and');
  const [conditions, setConditions]   = useState<Condition[]>([
    { id: uid(), field: '', operator: 'gte', value: '' },
  ]);
  const [showTemplates, setShowTemplates] = useState(true);
  const [templateSearch, setTemplateSearch] = useState('');
  const [validateResult, setValidateResult] = useState<{ valid: boolean; error?: string } | null>(null);
  const [saved, setSaved]             = useState(false);
  const [impactResult, setImpactResult] = useState<SimulateRuleResult | null>(null);
  const [impactLoading, setImpactLoading] = useState(false);
  const [previewResult, setPreviewResult] = useState<(SimulateRuleResult & { evaluated?: number; days?: number }) | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);

  const dsl = buildDsl(conditions, joinOp);

  const validateMutation = useMutation({
    mutationFn: () => validateDsl(dsl),
    onSuccess: (data) => setValidateResult(data),
  });

  const saveMutation = useMutation({
    mutationFn: async () => {
      const rule = await createRule({ name, description, condition_dsl: dsl, severity, scope });
      const today = new Date().toISOString().slice(0, 10);
      const from = new Date(Date.now() - 30 * 86400000).toISOString().slice(0, 10);
      setImpactLoading(true);
      const impact = await simulateRule(rule.id, { from, to: today }).catch(() => null);
      setImpactLoading(false);
      return { rule, impact };
    },
    onSuccess: ({ impact }) => {
      setSaved(true);
      setImpactResult(impact);
      setTimeout(() => setSaved(false), 4000);
    },
  });

  const addCondition = () =>
    setConditions((prev) => [...prev, { id: uid(), field: '', operator: 'gte', value: '' }]);

  const updateCondition = useCallback((id: string, updated: Condition) =>
    setConditions((prev) => prev.map((c) => (c.id === id ? updated : c))), []);

  const removeCondition = (id: string) =>
    setConditions((prev) => prev.filter((c) => c.id !== id));

  const applyTemplate = (tmpl: RuleTemplate) => {
    setName(tmpl.name);
    setDescription(tmpl.description);
    setSeverity(tmpl.severity);
    setScope(tmpl.scope);
    setConditions(tmpl.conditions.map((c) => ({ ...c, id: uid() })));
    setShowTemplates(false);
    setValidateResult(null);
    setImpactResult(null);
  };

  const filteredTemplates = templateSearch.trim()
    ? TEMPLATES.filter((t) =>
        t.name.toLowerCase().includes(templateSearch.toLowerCase()) ||
        t.tag.toLowerCase().includes(templateSearch.toLowerCase()) ||
        t.description.toLowerCase().includes(templateSearch.toLowerCase()),
      )
    : TEMPLATES;

  const isValid = name.trim().length > 0 && dsl.length > 0;

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Construtor de Regras</h1>
        <p className="mt-1 text-gray-500">
          Defina condições em português e publique com um clique. A DSL gerada é avaliada em tempo real pelo Rules Engine.
        </p>
      </div>

      {/* Templates */}
      <div className="overflow-hidden rounded-xl border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-900">
        <button
          className="flex w-full items-center justify-between px-6 py-4 text-left transition-colors hover:bg-gray-50 dark:hover:bg-gray-800"
          onClick={() => setShowTemplates((v) => !v)}
        >
          <div className="flex items-center gap-2">
            <Copy size={16} className="text-brand" />
            <span className="font-semibold text-gray-800 dark:text-white">Modelos prontos para PLD</span>
            <span className="ml-2 text-xs text-gray-400">clique para expandir</span>
          </div>
          {showTemplates ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
        </button>

        {showTemplates && (
          <div className="border-t border-gray-100 p-4 dark:border-gray-700">
            {/* Template search */}
            <div className="relative mb-3">
              <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
              <input
                type="search"
                placeholder="Filtrar modelos por nome ou tag…"
                value={templateSearch}
                onChange={(e) => setTemplateSearch(e.target.value)}
                className="w-full rounded-lg border border-gray-200 bg-gray-50 py-2 pl-8 pr-3 text-xs focus:ring-2 focus:ring-brand dark:border-gray-700 dark:bg-gray-800 dark:text-white"
              />
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {filteredTemplates.length === 0 ? (
                <p className="col-span-3 py-4 text-center text-xs text-gray-400">Nenhum modelo encontrado.</p>
              ) : filteredTemplates.map((tmpl) => (
              <button
                key={tmpl.id}
                onClick={() => applyTemplate(tmpl)}
                className="group rounded-lg border border-gray-200 p-4 text-left transition-all hover:border-brand hover:bg-brand/5 dark:border-gray-700"
              >
                <div className="mb-2 flex items-start justify-between">
                  <span className="text-2xl">{tmpl.icon}</span>
                  <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${SEVERITY_COLORS[tmpl.severity]}`}>
                    {tmpl.severity}
                  </span>
                </div>
                <p className="text-sm font-semibold text-gray-800 group-hover:text-brand dark:text-white">{tmpl.name}</p>
                <p className="mt-1 text-xs leading-snug text-gray-500">{tmpl.description}</p>
                <span className="mt-2 inline-block text-xs font-medium text-brand">{tmpl.tag}</span>
              </button>
            ))}
            </div>
          </div>
        )}
      </div>

      {/* Builder */}
      <div className="divide-y divide-gray-100 overflow-hidden rounded-xl border border-gray-200 bg-white dark:divide-gray-700 dark:border-gray-700 dark:bg-gray-900">

        {/* Identificação */}
        <div className="space-y-4 p-6">
          <h2 className="font-semibold text-gray-800 dark:text-white">Identificação da regra</h2>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">Nome da regra *</label>
              <input
                type="text"
                className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-brand dark:border-gray-700 dark:bg-gray-800 dark:text-white"
                placeholder="Ex: Estruturação com PEP"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>
            <div>
              <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">Severidade</label>
              <select
                className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm focus:ring-2 focus:ring-brand dark:border-gray-700 dark:bg-gray-800 dark:text-white"
                value={severity}
                onChange={(e) => setSeverity(e.target.value as Severity)}
              >
                <option value="LOW">🔵 BAIXO — informativo</option>
                <option value="MEDIUM">🟡 MÉDIO — requer análise</option>
                <option value="HIGH">🟠 ALTO — prioritário</option>
                <option value="CRITICAL">🔴 CRÍTICO — ação imediata</option>
              </select>
            </div>
            <div>
              <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">Escopo</label>
              <select
                className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm focus:ring-2 focus:ring-brand dark:border-gray-700 dark:bg-gray-800 dark:text-white"
                value={scope}
                onChange={(e) => setScope(e.target.value as Scope)}
              >
                <option value="TRANSACTION">TRANSACTION — avaliada em cada transação</option>
                <option value="BET">BET — avaliada em cada aposta</option>
                <option value="PLAYER">PLAYER — avaliada no perfil do jogador</option>
              </select>
            </div>
            <div>
              <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">Descrição (opcional)</label>
              <textarea
                rows={2}
                className="w-full resize-none rounded-lg border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-brand dark:border-gray-700 dark:bg-gray-800 dark:text-white"
                placeholder="Padrão suspeito que esta regra detecta..."
                value={description}
                onChange={(e) => setDescription(e.target.value)}
              />
            </div>
          </div>
        </div>

        {/* Condições */}
        <div className="space-y-3 p-6">
          <div className="flex items-center justify-between">
            <h2 className="font-semibold text-gray-800 dark:text-white">Condições de disparo</h2>
            <button
              type="button"
              onClick={() => setJoinOp((v) => (v === 'and' ? 'or' : 'and'))}
              className={`flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold transition-colors ${
                joinOp === 'and'
                  ? 'bg-blue-100 text-blue-700 hover:bg-blue-200'
                  : 'bg-purple-100 text-purple-700 hover:bg-purple-200'
              }`}
              title="Alternar entre E (todas) / OU (qualquer)"
            >
              {joinOp === 'and' ? <ToggleLeft size={14} /> : <ToggleRight size={14} />}
              {joinOp === 'and' ? 'Todas (E)' : 'Qualquer (OU)'}
            </button>
          </div>

          <div className="space-y-3">
            {conditions.map((cond, idx) => (
              <ConditionRow
                key={cond.id}
                cond={cond}
                index={idx}
                joinOp={joinOp}
                onChange={(updated) => updateCondition(cond.id, updated)}
                onRemove={() => removeCondition(cond.id)}
              />
            ))}
          </div>

          <button
            onClick={addCondition}
            className="mt-2 flex items-center gap-1.5 text-sm font-medium text-brand hover:opacity-80"
          >
            <Plus size={15} /> Adicionar condição
          </button>
        </div>

        {/* Prévia DSL */}
        <div className="bg-gray-50 px-6 py-4 dark:bg-gray-800">
          <p className="break-all font-mono text-xs leading-relaxed text-gray-500">
            <span className="mr-2 text-gray-400">DSL gerada:</span>
            {dsl || <span className="italic text-gray-400">Nenhuma condição definida</span>}
          </p>
        </div>

        {/* Resultado de validação */}
        {validateResult && (
          <div className={`flex items-start gap-3 px-6 py-4 ${
            validateResult.valid ? 'bg-green-50 dark:bg-green-900/20' : 'bg-red-50 dark:bg-red-900/20'
          }`}>
            {validateResult.valid
              ? <CheckCircle2 size={16} className="mt-0.5 flex-shrink-0 text-green-600" />
              : <AlertTriangle size={16} className="mt-0.5 flex-shrink-0 text-red-600" />}
            <p className={`text-sm ${validateResult.valid ? 'text-green-800' : 'text-red-800'}`}>
              {validateResult.valid
                ? 'DSL válida — a regra pode ser publicada.'
                : `Erro de sintaxe: ${validateResult.error}`}
            </p>
          </div>
        )}

        {/* Ações */}
        <div className="flex items-center justify-between px-6 py-4">
          <div className="flex items-center gap-2">
            <button
              onClick={() => validateMutation.mutate()}
              disabled={!isValid || validateMutation.isPending}
              className="flex items-center gap-2 rounded-lg border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40 dark:border-gray-600 dark:text-gray-300"
            >
              <Play size={15} />
              {validateMutation.isPending ? 'Validando...' : 'Validar DSL'}
            </button>
            <button
              onClick={async () => {
                if (!isValid) return;
                setPreviewLoading(true);
                setPreviewError(null);
                setPreviewResult(null);
                try {
                  const result = await previewDsl(dsl, severity, scope, 30);
                  setPreviewResult(result);
                } catch (err: unknown) {
                  const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Erro ao simular.';
                  setPreviewError(msg);
                } finally {
                  setPreviewLoading(false);
                }
              }}
              disabled={!isValid || previewLoading}
              className="flex items-center gap-2 rounded-lg border border-indigo-300 bg-indigo-50 px-4 py-2 text-sm font-medium text-indigo-700 transition-colors hover:bg-indigo-100 disabled:cursor-not-allowed disabled:opacity-40"
              title="Simula quantos alertas esta regra teria gerado nos últimos 30 dias, sem publicar"
            >
              <Target size={15} />
              {previewLoading ? 'Simulando…' : 'Simular antes de publicar'}
            </button>
          </div>

          <div className="flex items-center gap-3">
            {saved && (
              <span className="flex items-center gap-1.5 text-sm font-medium text-green-700">
                <CheckCircle2 size={15} /> Regra publicada!
              </span>
            )}
            {saveMutation.isError && (
              <span className="flex items-center gap-1.5 text-sm text-red-600">
                <AlertTriangle size={15} /> Erro ao salvar
              </span>
            )}
            <button
              onClick={() => saveMutation.mutate()}
              disabled={!isValid || saveMutation.isPending}
              className="flex items-center gap-2 rounded-lg bg-brand px-5 py-2 text-sm font-semibold text-white transition-colors hover:bg-brand/90 disabled:cursor-not-allowed disabled:opacity-40"
            >
              <Save size={15} />
              {saveMutation.isPending ? 'Publicando...' : 'Publicar Regra'}
            </button>
          </div>
        </div>
      </div>

      {/* Preview antes de publicar */}
      {(previewLoading || previewResult || previewError) && (
        <div className="overflow-hidden rounded-xl border border-indigo-200 bg-indigo-50 dark:border-indigo-900 dark:bg-indigo-950">
          <div className="flex items-center gap-2 border-b border-indigo-100 bg-white px-5 py-3 dark:border-indigo-900 dark:bg-indigo-900/40">
            <Target size={15} className="text-indigo-600" />
            <h2 className="text-sm font-semibold text-indigo-800 dark:text-indigo-200">
              Simulação prévia — últimos {previewResult?.days ?? 30} dias
            </h2>
            {previewLoading && (
              <span className="ml-auto text-xs text-indigo-600 animate-pulse">Calculando…</span>
            )}
          </div>
          {previewError && (
            <div className="flex items-start gap-2 p-4 text-sm text-red-700">
              <AlertTriangle size={15} className="mt-0.5 shrink-0" />
              <span>{previewError}</span>
            </div>
          )}
          {previewResult && (
            <div className="grid grid-cols-2 gap-4 p-5 md:grid-cols-4">
              <div className="rounded-lg bg-white p-3 shadow-sm dark:bg-indigo-900/30">
                <div className="flex items-center gap-1.5 text-xs text-gray-500">
                  <Target size={12} /> Matches estimados
                </div>
                <div className="mt-1 text-2xl font-bold text-gray-900 dark:text-white">
                  {previewResult.total_alerts ?? previewResult.matches}
                </div>
              </div>
              <div className="rounded-lg bg-white p-3 shadow-sm dark:bg-indigo-900/30">
                <div className="flex items-center gap-1.5 text-xs text-gray-500">
                  <Users size={12} /> Clientes afetados
                </div>
                <div className="mt-1 text-2xl font-bold text-gray-900 dark:text-white">
                  {previewResult.players?.length ?? '—'}
                </div>
              </div>
              <div className="rounded-lg bg-white p-3 shadow-sm dark:bg-indigo-900/30">
                <div className="text-xs text-gray-500">Eventos avaliados</div>
                <div className="mt-1 text-2xl font-bold text-gray-900 dark:text-white">
                  {(previewResult as SimulateRuleResult & { evaluated?: number }).evaluated ?? '—'}
                </div>
              </div>
              <div className="rounded-lg bg-white p-3 shadow-sm dark:bg-indigo-900/30">
                <div className="text-xs text-gray-500">Taxa de match</div>
                <div className={`mt-1 text-2xl font-bold ${
                  previewResult.matches > 500 ? 'text-orange-600' : 'text-gray-900 dark:text-white'
                }`}>
                  {(previewResult as SimulateRuleResult & { evaluated?: number }).evaluated
                    ? `${((previewResult.matches / ((previewResult as SimulateRuleResult & { evaluated?: number }).evaluated ?? 1)) * 100).toFixed(1)}%`
                    : '—'}
                </div>
              </div>
            </div>
          )}
          {previewResult && previewResult.matches > 500 && (
            <div className="flex items-start gap-2 border-t border-indigo-100 bg-amber-50 px-5 py-3 text-xs text-amber-800">
              <AlertTriangle size={14} className="mt-0.5 shrink-0 text-amber-600" />
              <span>
                Esta regra geraria muitos alertas. Considere adicionar condições para aumentar a precisão antes de publicar.
              </span>
            </div>
          )}
        </div>
      )}

      {/* Impacto estimado — aparece após publicar */}
      {(impactLoading || impactResult) && (
        <div className="overflow-hidden rounded-xl border border-green-200 bg-green-50 dark:border-green-900 dark:bg-green-950">
          <div className="flex items-center gap-2 border-b border-green-100 bg-white px-5 py-3 dark:border-green-900 dark:bg-green-900/40">
            <TrendingUp size={15} className="text-green-600" />
            <h2 className="text-sm font-semibold text-green-800 dark:text-green-200">
              Impacto estimado — últimos 30 dias
            </h2>
            {impactLoading && (
              <span className="ml-auto text-xs text-green-600 animate-pulse">Calculando…</span>
            )}
          </div>
          {impactResult && (
            <div className="grid grid-cols-2 gap-4 p-5 md:grid-cols-4">
              <div className="rounded-lg bg-white p-3 shadow-sm dark:bg-green-900/30">
                <div className="flex items-center gap-1.5 text-xs text-gray-500">
                  <Target size={12} /> Alertas gerados
                </div>
                <div className="mt-1 text-2xl font-bold text-gray-900 dark:text-white">
                  {impactResult.total_alerts ?? impactResult.matches}
                </div>
              </div>
              <div className="rounded-lg bg-white p-3 shadow-sm dark:bg-green-900/30">
                <div className="flex items-center gap-1.5 text-xs text-gray-500">
                  <Users size={12} /> Clientes envolvidos
                </div>
                <div className="mt-1 text-2xl font-bold text-gray-900 dark:text-white">
                  {impactResult.players?.length ?? '—'}
                </div>
              </div>
              <div className="rounded-lg bg-white p-3 shadow-sm dark:bg-green-900/30">
                <div className="text-xs text-gray-500">Precisão estimada</div>
                <div className="mt-1 text-2xl font-bold text-gray-900 dark:text-white">
                  {impactResult.precision_estimated != null
                    ? `${(impactResult.precision_estimated * 100).toFixed(0)}%`
                    : '—'}
                </div>
              </div>
              <div className="rounded-lg bg-white p-3 shadow-sm dark:bg-green-900/30">
                <div className="text-xs text-gray-500">Falso positivo est.</div>
                <div className={`mt-1 text-2xl font-bold ${
                  impactResult.false_positive_estimated != null && impactResult.false_positive_estimated > 0.35
                    ? 'text-red-600'
                    : 'text-gray-900 dark:text-white'
                }`}>
                  {impactResult.false_positive_estimated != null
                    ? `${(impactResult.false_positive_estimated * 100).toFixed(0)}%`
                    : '—'}
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Dica */}
      <div className="flex items-start gap-3 rounded-xl border border-blue-100 bg-blue-50 p-4 text-sm text-blue-800 dark:border-blue-900 dark:bg-blue-950">
        <Info size={16} className="mt-0.5 flex-shrink-0 text-blue-500" />
        <div>
          <p className="mb-0.5 font-semibold">Como funciona?</p>
          <p className="leading-relaxed text-blue-700">
            Cada evento processado pelo BetAML é avaliado contra todas as regras ativas.
            A DSL gerada usa caminhos reais do contexto de avaliação — clique em{' '}
            <strong>Validar DSL</strong> antes de publicar para confirmar que a sintaxe está correta.
            O motor suporta operadores aritméticos: <code className="font-mono">+</code>,{' '}
            <code className="font-mono">-</code>, <code className="font-mono">*</code>,{' '}
            <code className="font-mono">/</code>.
          </p>
        </div>
      </div>
    </div>
  );
}
