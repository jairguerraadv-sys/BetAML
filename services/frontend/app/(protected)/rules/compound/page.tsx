'use client';
import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api, fetchRules, Rule } from '@/lib/api';
import { GitBranch, Trash2, Plus, CheckSquare, Square } from 'lucide-react';

interface CompoundRule {
  id: string;
  name: string;
  logic?: string;
  min_score_threshold: number | null;
  component_rule_ids: string[];
  is_active: boolean;
  created_at: string;
  tenant_id: string;
}

const fetchCompound = () =>
  api.get<CompoundRule[]>('/rules/compound').then((r) => r.data);

export default function CompoundRulesPage() {
  const qc = useQueryClient();

  const { data: compoundRules = [], isLoading } = useQuery({
    queryKey: ['compound-rules'],
    queryFn: fetchCompound,
  });

  const { data: allRules = [] } = useQuery({
    queryKey: ['rules'],
    queryFn: fetchRules,
  });

  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState('');
  const [operator, setOperator] = useState('AND');
  const [nThreshold, setNThreshold] = useState(2);
  const [severityMode, setSeverityMode] = useState('MAX');
  const [fixedSeverity, setFixedSeverity] = useState('HIGH');
  const [minScore, setMinScore] = useState(1.0);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [err, setErr] = useState('');

  const toggleRule = (id: string) =>
    setSelectedIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    );

  const create = useMutation({
    mutationFn: () =>
      api.post('/rules/compound', {
        name,
        logic: operator,
        operator,
        n_threshold: operator === 'N_OF_M' ? nThreshold : undefined,
        severity_mode: severityMode,
        fixed_severity: severityMode === 'FIXED' ? fixedSeverity : undefined,
        min_score_threshold: minScore,
        component_rule_ids: selectedIds,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['compound-rules'] });
      setShowForm(false);
      setName('');
      setOperator('AND');
      setNThreshold(2);
      setSeverityMode('MAX');
      setFixedSeverity('HIGH');
      setSelectedIds([]);
      setErr('');
    },
    onError: (e: unknown) => setErr(String(e)),
  });

  const remove = useMutation({
    mutationFn: (id: string) => api.delete(`/rules/compound/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['compound-rules'] }),
  });

  const activeRules = allRules.filter((r: Rule) => r.status === 'ACTIVE');

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <GitBranch size={22} className="text-brand" />
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Condições Compostas</h1>
        </div>
        <button
          onClick={() => setShowForm((v) => !v)}
          className="flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand/90"
        >
          <Plus size={16} /> Nova Condição Composta
        </button>
      </div>

      {/* Form */}
      {showForm && (
        <form
          className="space-y-4 rounded-xl border border-brand/20 bg-brand/5 p-5 dark:bg-brand/10"
          onSubmit={(e) => { e.preventDefault(); create.mutate(); }}
        >
          <h2 className="font-semibold text-gray-800 dark:text-white">Nova Condição Composta</h2>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="mb-1 block text-xs font-medium text-gray-600 dark:text-gray-400">Nome</label>
              <input
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand dark:border-gray-700 dark:bg-gray-800 dark:text-white"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-gray-600 dark:text-gray-400">
                Score Mínimo (0–1)
              </label>
              <input
                type="number"
                step="0.05"
                min="0"
                max="1"
                value={minScore}
                onChange={(e) => setMinScore(parseFloat(e.target.value))}
                className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand dark:border-gray-700 dark:bg-gray-800 dark:text-white"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-gray-600 dark:text-gray-400">Operador</label>
              <select
                value={operator}
                onChange={(e) => setOperator(e.target.value)}
                className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-800 dark:text-white"
              >
                <option value="AND">Todas devem disparar (E)</option>
                <option value="OR">Qualquer uma dispara (OU)</option>
                <option value="N_OF_M">Pelo menos N de M</option>
              </select>
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-gray-600 dark:text-gray-400">Gravidade resultante</label>
              <select
                value={severityMode}
                onChange={(e) => setSeverityMode(e.target.value)}
                className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-800 dark:text-white"
              >
                <option value="MAX">A mais grave das condições</option>
                <option value="FIXED">Gravidade fixa definida abaixo</option>
              </select>
            </div>
            {operator === 'N_OF_M' && (
              <div>
                <label className="mb-1 block text-xs font-medium text-gray-600 dark:text-gray-400">Mínimo de condições (N)</label>
                <input
                  type="number"
                  min="1"
                  value={nThreshold}
                  onChange={(e) => setNThreshold(parseInt(e.target.value || '1', 10))}
                  className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-800 dark:text-white"
                />
              </div>
            )}
            {severityMode === 'FIXED' && (
              <div>
                <label className="mb-1 block text-xs font-medium text-gray-600 dark:text-gray-400">Severidade fixa</label>
                <select
                  value={fixedSeverity}
                  onChange={(e) => setFixedSeverity(e.target.value)}
                  className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-800 dark:text-white"
                >
                  <option value="LOW">Baixo</option>
                  <option value="MEDIUM">Médio</option>
                  <option value="HIGH">Alto</option>
                  <option value="CRITICAL">Crítico</option>
                </select>
              </div>
            )}
          </div>

          {/* Rule checkboxes */}
          <div>
            <label className="mb-2 block text-xs font-medium text-gray-600 dark:text-gray-400">
              Condições componentes ({selectedIds.length} selecionada{selectedIds.length !== 1 ? 's' : ''})
            </label>
            {activeRules.length === 0 ? (
              <p className="text-xs text-gray-400">Nenhuma regra ativa disponível.</p>
            ) : (
              <div className="max-h-56 overflow-y-auto rounded-lg border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-800">
                {activeRules.map((r: Rule) => {
                  const isChecked = selectedIds.includes(r.id);
                  return (
                    <button
                      key={r.id}
                      type="button"
                      onClick={() => toggleRule(r.id)}
                      className={`flex w-full items-center gap-3 px-4 py-2.5 text-left text-sm transition-colors hover:bg-gray-50 dark:hover:bg-gray-700 ${
                        isChecked ? 'bg-brand/5 dark:bg-brand/10' : ''
                      }`}
                    >
                      {isChecked
                        ? <CheckSquare size={16} className="flex-shrink-0 text-brand" />
                        : <Square size={16} className="flex-shrink-0 text-gray-300" />}
                      <span className="flex-1 font-medium text-gray-800 dark:text-gray-200">{r.name}</span>
                      <span className="text-xs text-gray-400">{r.scope} · {r.severity}</span>
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          {err && <p className="text-xs text-red-600">{err}</p>}

          <div className="flex gap-2">
            <button
              type="submit"
              disabled={!name || selectedIds.length === 0 || create.isPending}
              className="rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand/90 disabled:opacity-50"
            >
              {create.isPending ? 'Salvando…' : 'Salvar'}
            </button>
            <button
              type="button"
              onClick={() => setShowForm(false)}
              className="rounded-lg border px-4 py-2 text-sm dark:border-gray-700"
            >
              Cancelar
            </button>
          </div>
        </form>
      )}

      {/* Table */}
      <div className="overflow-hidden rounded-xl border border-gray-100 bg-white shadow-sm dark:border-gray-700 dark:bg-gray-900">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-xs font-semibold uppercase text-gray-500 dark:bg-gray-800 dark:text-gray-400">
            <tr>
              <th className="px-4 py-3 text-left">Nome</th>
              <th className="px-4 py-3 text-left">Score Mín.</th>
              <th className="px-4 py-3 text-left">Condições</th>
              <th className="px-4 py-3 text-left">Lógica</th>
              <th className="px-4 py-3 text-left">Status</th>
              <th className="px-4 py-3 text-left">Criado em</th>
              <th className="px-4 py-3" />
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-50 dark:divide-gray-800">
            {isLoading && (
              <tr><td colSpan={7} className="py-8 text-center text-gray-400">Carregando…</td></tr>
            )}
            {!isLoading && compoundRules.length === 0 && (
              <tr><td colSpan={7} className="py-8 text-center text-gray-400">Nenhuma condição composta cadastrada</td></tr>
            )}
            {compoundRules.map((r: CompoundRule) => {
              const componentNames = (r.component_rule_ids ?? [])
                .map((id) => allRules.find((ar: Rule) => ar.id === id)?.name ?? id.slice(0, 8))
                .join(', ');
              return (
                <tr key={r.id} className="hover:bg-gray-50/50 dark:hover:bg-gray-800/50">
                  <td className="px-4 py-3 font-medium text-gray-800 dark:text-gray-200">{r.name}</td>
                  <td className="px-4 py-3 text-gray-600 dark:text-gray-400">{r.min_score_threshold ?? '—'}</td>
                  <td className="max-w-xs px-4 py-3 truncate text-xs text-gray-500 dark:text-gray-400" title={componentNames}>
                    {componentNames || '—'}
                  </td>
                  <td className="px-4 py-3">
                    <span className="rounded bg-gray-100 px-2 py-0.5 text-xs font-semibold dark:bg-gray-700 dark:text-gray-300">
                      {{ AND: 'Todas (E)', OR: 'Qualquer (OU)', N_OF_M: 'Pelo menos N' }[r.logic ?? 'AND'] ?? (r.logic ?? 'AND')}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${
                      r.is_active ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500 dark:bg-gray-700'
                    }`}>
                      {r.is_active ? 'Ativa' : 'Inativa'}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-gray-500">{new Date(r.created_at).toLocaleDateString('pt-BR')}</td>
                  <td className="px-4 py-3 text-right">
                    <button
                      onClick={() => remove.mutate(r.id)}
                      className="rounded p-1 text-gray-400 hover:text-red-500"
                      title="Remover"
                    >
                      <Trash2 size={14} />
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
