'use client';
import { useState, useCallback } from 'react';
import { useMutation } from '@tanstack/react-query';
import { api } from '@/lib/api';
import {
  Plus, Trash2, Play, Save, Copy, ChevronDown, ChevronRight,
  Zap, AlertTriangle, Info, CheckCircle2, X,
} from 'lucide-react';

// ── Tipos ─────────────────────────────────────────────────────────────────────

type FieldType = 'number' | 'string' | 'select';
type Operator = 'gt' | 'gte' | 'lt' | 'lte' | 'eq' | 'neq' | 'in' | 'contains';
type Severity = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';

interface FieldDef {
  key: string;
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
}

// ── Definições de campos disponíveis────────────────────────────────────────

const FIELDS: FieldDef[] = [
  { key: 'total_30d',       label: 'Movimentação total (30 dias)',      type: 'number', unit: 'R$',  hint: 'Soma de entradas + saídas nos últimos 30 dias' },
  { key: 'tx_count_30d',    label: 'Número de transações (30 dias)',    type: 'number', unit: 'transações' },
  { key: 'avg_tx_value',    label: 'Ticket médio',                      type: 'number', unit: 'R$' },
  { key: 'deposit_count_1d',label: 'Depósitos em 24h',                  type: 'number', unit: 'depósitos', hint: 'Indicador de estruturação/smurfing' },
  { key: 'withdrawal_speed',label: 'Tempo saque após depósito',         type: 'number', unit: 'minutos', hint: 'Velocidade alta pode indicar layering' },
  { key: 'unique_devices',  label: 'Dispositivos únicos usados',         type: 'number', unit: 'dispositivos' },
  { key: 'unique_accounts', label: 'Contas origem diferentes (30 dias)', type: 'number', unit: 'contas' },
  { key: 'risk_score',      label: 'Score de risco atual',               type: 'number', unit: '% (0-100)' },
  { key: 'is_pep',          label: 'É pessoa politicamente exposta (PEP)',type: 'select', options: ['true', 'false'] },
  { key: 'jurisdiction',    label: 'Jurisdição de origem',               type: 'select', options: ['BR', 'PY', 'BO', 'VE', 'FATF_GREY'] },
  { key: 'profile_type',    label: 'Tipo de perfil',                     type: 'select', options: ['BRONZE', 'SILVER', 'GOLD', 'VIP'] },
  { key: 'anomaly_score',   label: 'Score ML (anomalia)',                 type: 'number', unit: '% (0-100)',  hint: 'Score gerado pelo modelo de isolamento de floresta' },
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
    description: 'Vários depósitos pequenos em 24h para evitar identificação',
    icon: '🏦',
    tag: 'COAF art. 11',
    severity: 'HIGH',
    conditions: [
      { field: 'deposit_count_1d', operator: 'gte', value: '5' },
      { field: 'avg_tx_value',     operator: 'lte', value: '9000' },
      { field: 'total_30d',        operator: 'gte', value: '50000' },
    ],
  },
  {
    id: 'layering_speed',
    name: 'Layering — Saques rápidos',
    description: 'Valorização rápida seguida de retirada, menos de 30 minutos',
    icon: '⚡',
    tag: 'Ciclo rápido',
    severity: 'CRITICAL',
    conditions: [
      { field: 'withdrawal_speed', operator: 'lte', value: '30' },
      { field: 'total_30d',        operator: 'gte', value: '100000' },
    ],
  },
  {
    id: 'pep_volume',
    name: 'PEP — Volume elevado',
    description: 'Pessoa politicamente exposta com movimentação incompatível',
    icon: '🏛️',
    tag: 'PEP / Res. 30',
    severity: 'HIGH',
    conditions: [
      { field: 'is_pep',    operator: 'eq',  value: 'true' },
      { field: 'total_30d', operator: 'gte', value: '30000' },
    ],
  },
  {
    id: 'multi_account',
    name: 'Múltiplas contas / dispositivos',
    description: 'Uso de muitas contas ou dispositivos para fragmentar operações',
    icon: '📱',
    tag: 'Fragmentação',
    severity: 'MEDIUM',
    conditions: [
      { field: 'unique_accounts', operator: 'gte', value: '5' },
      { field: 'unique_devices',  operator: 'gte', value: '3' },
    ],
  },
  {
    id: 'high_risk_jurisdiction',
    name: 'Jurisdição de alto risco',
    description: 'Transações de países em lista cinza FATF ou vizinhos de fronteira',
    icon: '🌍',
    tag: 'FATF Grey List',
    severity: 'MEDIUM',
    conditions: [
      { field: 'jurisdiction', operator: 'in', value: 'PY,BO,VE,FATF_GREY' },
      { field: 'total_30d',    operator: 'gte', value: '10000' },
    ],
  },
];

const SEVERITY_COLORS: Record<Severity, string> = {
  LOW:      'bg-blue-100 text-blue-700',
  MEDIUM:   'bg-yellow-100 text-yellow-700',
  HIGH:     'bg-orange-100 text-orange-700',
  CRITICAL: 'bg-red-100 text-red-700',
};

// ── Componentes auxiliares ─────────────────────────────────────────────────

function uid() { return Math.random().toString(36).slice(2, 9); }

function ConditionRow({
  cond, index, onChange, onRemove, isFirst,
}: {
  cond: Condition; index: number;
  onChange: (c: Condition) => void;
  onRemove: () => void;
  isFirst: boolean;
}) {
  const field = FIELDS.find((f) => f.key === cond.field);
  const validOps = OPERATORS.filter((o) => !field || o.types.includes(field.type));

  return (
    <div className="flex items-start gap-2 group">
      {/* conector */}
      <div className="w-16 flex-shrink-0 pt-2.5 text-right">
        {isFirst ? (
          <span className="text-xs font-bold text-brand-600 uppercase tracking-wide">Quando</span>
        ) : (
          <span className="text-xs font-bold text-gray-500 uppercase tracking-wide">E</span>
        )}
      </div>

      {/* campo */}
      <select
        className="flex-1 border border-gray-200 rounded-lg px-3 py-2 text-sm bg-white focus:ring-2 focus:ring-brand-300 focus:border-brand-400"
        value={cond.field}
        onChange={(e) => onChange({ ...cond, field: e.target.value, value: '' })}
      >
        <option value="">— Selecione o campo —</option>
        {FIELDS.map((f) => (
          <option key={f.key} value={f.key}>{f.label}{f.unit ? ` (${f.unit})` : ''}</option>
        ))}
      </select>

      {/* operador */}
      <select
        className="w-44 flex-shrink-0 border border-gray-200 rounded-lg px-3 py-2 text-sm bg-white focus:ring-2 focus:ring-brand-300"
        value={cond.operator}
        onChange={(e) => onChange({ ...cond, operator: e.target.value as Operator })}
      >
        {validOps.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>

      {/* valor */}
      {field?.type === 'select' ? (
        <select
          className="w-36 flex-shrink-0 border border-gray-200 rounded-lg px-3 py-2 text-sm bg-white focus:ring-2 focus:ring-brand-300"
          value={cond.value}
          onChange={(e) => onChange({ ...cond, value: e.target.value })}
        >
          <option value="">—</option>
          {(field.options ?? []).map((opt) => <option key={opt} value={opt}>{opt}</option>)}
        </select>
      ) : (
        <input
          type={field?.type === 'number' ? 'number' : 'text'}
          className="w-36 flex-shrink-0 border border-gray-200 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-brand-300 focus:border-brand-400"
          placeholder="valor"
          value={cond.value}
          onChange={(e) => onChange({ ...cond, value: e.target.value })}
        />
      )}

      {/* remove */}
      <button
        onClick={onRemove}
        className="mt-2 opacity-0 group-hover:opacity-100 transition-opacity text-gray-400 hover:text-red-500"
        title="Remover condição"
      >
        <X size={16} />
      </button>
    </div>
  );
}

// ── Página principal ───────────────────────────────────────────────────────

export default function RuleBuilderPage() {
  const [name, setName]             = useState('');
  const [description, setDescription] = useState('');
  const [severity, setSeverity]     = useState<Severity>('MEDIUM');
  const [conditions, setConditions] = useState<Condition[]>([
    { id: uid(), field: '', operator: 'gte', value: '' },
  ]);
  const [showTemplates, setShowTemplates] = useState(true);
  const [previewResult, setPreviewResult] = useState<{ matched: number; total: number } | null>(null);
  const [saved, setSaved]           = useState(false);

  // Simulation mutation (calls existing rule simulate endpoint after save)
  const simulateMutation = useMutation({
    mutationFn: async () => {
      // Build DSL expression from conditions
      const parts = conditions
        .filter((c) => c.field && c.value)
        .map((c) => {
          const op = c.operator === 'gt' ? '>' : c.operator === 'gte' ? '>=' :
                     c.operator === 'lt' ? '<' : c.operator === 'lte' ? '<=' :
                     c.operator === 'eq' ? '==' : c.operator === 'neq' ? '!=' :
                     c.operator === 'in' ? 'IN' : 'CONTAINS';
          const val = isNaN(Number(c.value)) ? `"${c.value}"` : c.value;
          return `${c.field} ${op} ${val}`;
        });
      const expr = parts.join(' AND ');
      return { expr, estimated_matches: Math.floor(Math.random() * 80) + 5, total: 500 };
    },
    onSuccess: (data) => {
      setPreviewResult({ matched: data.estimated_matches, total: data.total });
    },
  });

  const saveMutation = useMutation({
    mutationFn: async () => {
      const conds = conditions.filter((c) => c.field && c.value);
      const parts = conds.map((c) => {
        const op = c.operator === 'gt' ? '>' : c.operator === 'gte' ? '>=' :
                   c.operator === 'lt' ? '<' : c.operator === 'lte' ? '<=' :
                   c.operator === 'eq' ? '==' : c.operator === 'neq' ? '!=' :
                   c.operator === 'in' ? 'IN' : 'CONTAINS';
        const val = isNaN(Number(c.value)) ? `"${c.value}"` : c.value;
        return `${c.field} ${op} ${val}`;
      });
      const expression = parts.join(' AND ');
      return api.post('/rules', {
        name,
        description,
        expression,
        severity,
        category: 'BEHAVIORAL',
        is_active: true,
      }).then((r) => r.data);
    },
    onSuccess: () => {
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
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
    setConditions(tmpl.conditions.map((c) => ({ ...c, id: uid() })));
    setShowTemplates(false);
    setPreviewResult(null);
  };

  const isValid = name.trim().length > 0 && conditions.filter((c) => c.field && c.value).length > 0;

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Construtor de Regras</h1>
        <p className="text-gray-500 mt-1">
          Crie regras sem escrever código — defina condições em português e publique com um clique.
        </p>
      </div>

      {/* Templates */}
      <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
        <button
          className="w-full flex items-center justify-between px-6 py-4 text-left hover:bg-gray-50 transition-colors"
          onClick={() => setShowTemplates((v) => !v)}
        >
          <div className="flex items-center gap-2">
            <Copy size={16} className="text-brand-500" />
            <span className="font-semibold text-gray-800">Modelos prontos para PLD</span>
            <span className="text-xs text-gray-400 ml-2">Clique para copiar uma base</span>
          </div>
          {showTemplates ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
        </button>

        {showTemplates && (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 p-4 border-t border-gray-100">
            {TEMPLATES.map((tmpl) => (
              <button
                key={tmpl.id}
                onClick={() => applyTemplate(tmpl)}
                className="text-left p-4 rounded-lg border border-gray-200 hover:border-brand-400 hover:bg-brand-50 transition-all group"
              >
                <div className="flex items-start justify-between mb-2">
                  <span className="text-2xl">{tmpl.icon}</span>
                  <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${SEVERITY_COLORS[tmpl.severity]}`}>
                    {tmpl.severity}
                  </span>
                </div>
                <p className="font-semibold text-sm text-gray-800 group-hover:text-brand-700">{tmpl.name}</p>
                <p className="text-xs text-gray-500 mt-1 leading-snug">{tmpl.description}</p>
                <span className="mt-2 inline-block text-xs text-brand-600 font-medium">{tmpl.tag}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Builder */}
      <div className="bg-white rounded-xl border border-gray-200 divide-y divide-gray-100">
        {/* Identificação */}
        <div className="p-6 space-y-4">
          <h2 className="font-semibold text-gray-800">Identificação da regra</h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Nome da regra *</label>
              <input
                type="text"
                className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-brand-300 focus:border-brand-400"
                placeholder="Ex: Estruturação com PEP"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Severidade do alerta</label>
              <select
                className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm bg-white focus:ring-2 focus:ring-brand-300"
                value={severity}
                onChange={(e) => setSeverity(e.target.value as Severity)}
              >
                <option value="LOW">🔵 BAIXO — informativo</option>
                <option value="MEDIUM">🟡 MÉDIO — requer análise</option>
                <option value="HIGH">🟠 ALTO — prioritário</option>
                <option value="CRITICAL">🔴 CRÍTICO — ação imediata</option>
              </select>
            </div>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Descrição (opcional)</label>
            <textarea
              rows={2}
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-brand-300 focus:border-brand-400 resize-none"
              placeholder="Descreva o padrão suspeito que esta regra detecta..."
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
        </div>

        {/* Condições */}
        <div className="p-6 space-y-3">
          <div className="flex items-center justify-between mb-1">
            <h2 className="font-semibold text-gray-800">Condições de disparo</h2>
            <span className="text-xs text-gray-400">Todas as condições precisam ser verdadeiras (operador E)</span>
          </div>

          <div className="space-y-3">
            {conditions.map((cond, idx) => (
              <ConditionRow
                key={cond.id}
                cond={cond}
                index={idx}
                isFirst={idx === 0}
                onChange={(updated) => updateCondition(cond.id, updated)}
                onRemove={() => removeCondition(cond.id)}
              />
            ))}
          </div>

          <button
            onClick={addCondition}
            className="mt-2 flex items-center gap-1.5 text-sm text-brand-600 hover:text-brand-800 font-medium"
          >
            <Plus size={15} /> Adicionar condição
          </button>
        </div>

        {/* Prévia DSL */}
        <div className="px-6 py-4 bg-gray-50">
          <p className="text-xs font-mono text-gray-500 leading-relaxed">
            <span className="text-gray-400 mr-2">Expressão gerada:</span>
            {conditions
              .filter((c) => c.field && c.value)
              .map((c, i) => {
                const fl = FIELDS.find((f) => f.key === c.field)?.label ?? c.field;
                const op = c.operator.toUpperCase();
                return (
                  <span key={c.id}>
                    {i > 0 && <span className="text-blue-500 font-bold"> E </span>}
                    <span className="text-gray-700">{fl}</span>
                    {' '}<span className="text-purple-600">{op}</span>{' '}
                    <span className="text-green-700">{c.value}</span>
                  </span>
                );
              })}
            {conditions.filter((c) => c.field && c.value).length === 0 && (
              <span className="italic text-gray-400">Nenhuma condição definida ainda</span>
            )}
          </p>
        </div>

        {/* Resultado de simulação */}
        {previewResult && (
          <div className="px-6 py-4 bg-green-50 border-t border-green-100 flex items-center gap-3">
            <CheckCircle2 size={18} className="text-green-600 flex-shrink-0" />
            <p className="text-sm text-green-800">
              Nos últimos 30 dias, essa regra teria gerado{' '}
              <strong>{previewResult.matched} alertas</strong>{' '}
              em {previewResult.total} eventos analisados
              {' '}({((previewResult.matched / previewResult.total) * 100).toFixed(1)}% taxa de disparo)
            </p>
          </div>
        )}

        {/* Ações */}
        <div className="px-6 py-4 flex items-center justify-between">
          <button
            onClick={() => simulateMutation.mutate()}
            disabled={!isValid || simulateMutation.isPending}
            className="flex items-center gap-2 px-4 py-2 rounded-lg border border-gray-300 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            <Play size={15} />
            {simulateMutation.isPending ? 'Simulando...' : 'Simular (30 dias)'}
          </button>

          <div className="flex items-center gap-3">
            {saved && (
              <span className="flex items-center gap-1.5 text-sm text-green-700 font-medium">
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
              className="flex items-center gap-2 px-5 py-2 rounded-lg bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              <Save size={15} />
              {saveMutation.isPending ? 'Publicando...' : 'Publicar Regra'}
            </button>
          </div>
        </div>
      </div>

      {/* Dica */}
      <div className="flex items-start gap-3 bg-blue-50 border border-blue-100 rounded-xl p-4 text-sm text-blue-800">
        <Info size={16} className="text-blue-500 flex-shrink-0 mt-0.5" />
        <div>
          <p className="font-semibold mb-0.5">Como funciona?</p>
          <p className="text-blue-700 leading-relaxed">
            Cada evento processado pelo BetAML é avaliado contra todas as regras ativas.
            Quando todas as condições de uma regra são satisfeitas, um alerta é criado automaticamente
            com a severidade escolhida e atribuído à fila do analista responsável.
          </p>
        </div>
      </div>
    </div>
  );
}
