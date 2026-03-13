'use client';
import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { GitBranch, Trash2, Plus } from 'lucide-react';

interface CompoundRule {
  id: string;
  name: string;
  logic?: string;
  min_score_threshold: number | null;
  is_active: boolean;
  created_at: string;
  tenant_id: string;
}

const fetchCompound = () =>
  api.get<CompoundRule[]>('/rules/compound').then((r) => r.data);

export default function CompoundRulesPage() {
  const qc = useQueryClient();

  const { data: rules = [], isLoading } = useQuery({
    queryKey: ['compound-rules'],
    queryFn: fetchCompound,
  });

  const [form, setForm] = useState({
    name: '',
    min_score_threshold: 1.0,
    component_rule_ids: '[]',
  });
  const [showForm, setShowForm] = useState(false);
  const [err, setErr] = useState('');

  const create = useMutation({
    mutationFn: () => {
      const body = {
        name: form.name,
        min_score_threshold: form.min_score_threshold,
        component_rule_ids: JSON.parse(form.component_rule_ids) as string[],
      };
      return api.post('/rules/compound', body);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['compound-rules'] });
      setShowForm(false);
      setErr('');
    },
    onError: (e: unknown) => setErr(String(e)),
  });

  const remove = useMutation({
    mutationFn: (id: string) => api.delete(`/rules/compound/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['compound-rules'] }),
  });

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <GitBranch size={22} className="text-brand" />
          <h1 className="text-2xl font-bold text-gray-900">Regras Compostas</h1>
        </div>
        <button
          onClick={() => setShowForm((v) => !v)}
          className="flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand/90"
        >
          <Plus size={16} /> Nova Regra Composta
        </button>
      </div>

      {/* Form */}
      {showForm && (
        <form
          className="space-y-4 rounded-xl border border-brand/20 bg-brand/5 p-5"
          onSubmit={(e) => { e.preventDefault(); create.mutate(); }}
        >
          <h2 className="font-semibold text-gray-800">Criar Regra Composta</h2>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="mb-1 block text-xs font-medium text-gray-600">Nome</label>
              <input
                required
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-gray-600">Score Mínimo</label>
              <input
                type="number"
                step="0.1"
                value={form.min_score_threshold}
                onChange={(e) => setForm({ ...form, min_score_threshold: parseFloat(e.target.value) })}
                className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand"
              />
            </div>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-gray-600">
              IDs das Regras Componentes (JSON array de strings: [{`"rule-uuid-1","rule-uuid-2"`}])
            </label>
            <textarea
              rows={3}
              value={form.component_rule_ids}
              onChange={(e) => setForm({ ...form, component_rule_ids: e.target.value })}
              className="w-full rounded-lg border border-gray-200 px-3 py-2 font-mono text-xs focus:outline-none focus:ring-2 focus:ring-brand"
            />
          </div>
          {err && <p className="text-xs text-red-600">{err}</p>}
          <div className="flex gap-2">
            <button type="submit" className="rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand/90">
              Salvar
            </button>
            <button type="button" onClick={() => setShowForm(false)} className="rounded-lg border px-4 py-2 text-sm">
              Cancelar
            </button>
          </div>
        </form>
      )}

      {/* Table */}
      <div className="overflow-hidden rounded-xl border border-gray-100 bg-white shadow-sm">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-xs font-semibold uppercase text-gray-500">
            <tr>
              <th className="px-4 py-3 text-left">Nome</th>
              <th className="px-4 py-3 text-left">Score Mín.</th>
              <th className="px-4 py-3 text-left">Lógica</th>
              <th className="px-4 py-3 text-left">Status</th>
              <th className="px-4 py-3 text-left">Criado em</th>
              <th className="px-4 py-3" />
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-50">
            {isLoading && (
              <tr><td colSpan={6} className="py-8 text-center text-gray-400">Carregando…</td></tr>
            )}
            {!isLoading && rules.length === 0 && (
              <tr><td colSpan={6} className="py-8 text-center text-gray-400">Nenhuma regra composta</td></tr>
            )}
            {rules.map((r: CompoundRule) => (
              <tr key={r.id} className="hover:bg-gray-50/50">
                <td className="px-4 py-3 font-medium">{r.name}</td>
                <td className="px-4 py-3">{r.min_score_threshold ?? '—'}</td>
                <td className="px-4 py-3">
                  <span className="rounded bg-gray-100 px-2 py-0.5 text-xs font-semibold">{r.logic ?? 'AND'}</span>
                </td>
                <td className="px-4 py-3">
                  <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${r.is_active ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'}`}>
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
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
