'use client';
import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { Shield, Key, Trash2, Plus, Power } from 'lucide-react';

interface ApiKey { id: string; name: string; prefix: string; created_at: string; last_used_at?: string; is_active: boolean; }
interface SystemFlag { key: string; value: unknown; updated_at?: string; }

const fetchApiKeys   = () => api.get<ApiKey[]>('/admin/api-keys').then((r) => r.data);
const fetchFlags     = () => api.get<SystemFlag[]>('/admin/flags').then((r) => r.data).catch(() => [] as SystemFlag[]);
const deleteKey      = (id: string) => api.delete(`/admin/api-keys/${id}`);
const toggleMaint    = (enabled: boolean) => api.post('/admin/maintenance-mode', null, { params: { enabled } });
// flagName = part after first colon (e.g. "maintenance_mode" from "{tenant_id}:maintenance_mode")
const updateFlag     = (flagName: string, value: string) => api.put(`/admin/flags/${flagName}`, { value });

export default function AdminPage() {
  const qc = useQueryClient();

  const { data: apiKeys = [], isLoading: loadingKeys } = useQuery({ queryKey: ['api-keys'],     queryFn: fetchApiKeys });
  const { data: flags  = [], isLoading: loadingFlags } = useQuery({ queryKey: ['system-flags'], queryFn: fetchFlags });

  const [newKeyName, setNewKeyName] = useState('');
  const [newKeyRaw, setNewKeyRaw]   = useState('');
  const [maintOn, setMaintOn]       = useState(false);

  const createKey = useMutation({
    mutationFn: () => api.post<{ raw_key: string }>('/admin/api-keys', { name: newKeyName }),
    onSuccess: (res) => {
      setNewKeyRaw(res.data.raw_key);
      setNewKeyName('');
      qc.invalidateQueries({ queryKey: ['api-keys'] });
    },
  });

  const removeKey = useMutation({
    mutationFn: deleteKey,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['api-keys'] }),
  });

  const maint = useMutation({
    mutationFn: (v: boolean) => toggleMaint(v),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['system-flags'] }),
  });

  const saveFlag = useMutation({
    mutationFn: ({ name, value }: { name: string; value: string }) => updateFlag(name, value),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['system-flags'] }),
  });

  return (
    <div className="space-y-8">
      <div className="flex items-center gap-2">
        <Shield size={22} className="text-brand" />
        <h1 className="text-2xl font-bold text-gray-900">Administração</h1>
      </div>

      {/* Maintenance mode */}
      <section className="rounded-xl border border-gray-100 bg-white p-5 shadow-sm">
        <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold text-gray-700">
          <Power size={15} /> Modo Manutenção
        </h2>
        <div className="flex items-center gap-4">
          <label className="flex cursor-pointer items-center gap-3">
            <div
              onClick={() => { const next = !maintOn; setMaintOn(next); maint.mutate(next); }}
              className={`relative h-6 w-11 rounded-full transition-colors ${maintOn ? 'bg-red-500' : 'bg-gray-200'}`}
            >
              <div className={`absolute top-1 h-4 w-4 rounded-full bg-white shadow transition-transform ${maintOn ? 'translate-x-5' : 'translate-x-1'}`} />
            </div>
            <span className={`text-sm font-medium ${maintOn ? 'text-red-600' : 'text-gray-600'}`}>
              {maintOn ? 'Manutenção ATIVA — novas ingestões bloqueadas' : 'Sistema Operacional'}
            </span>
          </label>
        </div>
      </section>

      {/* API Keys */}
      <section className="rounded-xl border border-gray-100 bg-white p-5 shadow-sm">
        <h2 className="mb-4 flex items-center gap-2 text-sm font-semibold text-gray-700">
          <Key size={15} /> Chaves de API
        </h2>

        {/* Create */}
        <form
          className="mb-4 flex gap-3"
          onSubmit={(e) => { e.preventDefault(); createKey.mutate(); }}
        >
          <input
            value={newKeyName}
            onChange={(e) => setNewKeyName(e.target.value)}
            placeholder="Nome da chave (ex: integration-sap)"
            className="flex-1 rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand"
          />
          <button
            type="submit"
            disabled={!newKeyName || createKey.isPending}
            className="flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand/90 disabled:opacity-50"
          >
            <Plus size={15} /> Gerar Chave
          </button>
        </form>

        {newKeyRaw && (
          <div className="mb-4 rounded-lg border border-green-200 bg-green-50 p-3">
            <p className="mb-1 text-xs font-semibold text-green-700">Chave gerada — copie agora, não será exibida novamente:</p>
            <code className="text-xs text-green-800">{newKeyRaw}</code>
          </div>
        )}

        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-xs font-semibold uppercase text-gray-500">
            <tr>
              <th className="px-4 py-2.5 text-left">Nome</th>
              <th className="px-4 py-2.5 text-left">Prefixo</th>
              <th className="px-4 py-2.5 text-left">Criado em</th>
              <th className="px-4 py-2.5 text-left">Último uso</th>
              <th className="px-4 py-2.5" />
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-50">
            {loadingKeys && <tr><td colSpan={5} className="py-6 text-center text-gray-400">Carregando…</td></tr>}
            {apiKeys.map((k) => (
              <tr key={k.id} className="hover:bg-gray-50/50">
                <td className="px-4 py-2.5 font-medium">{k.name}</td>
                <td className="px-4 py-2.5 font-mono text-xs">{k.prefix}…</td>
                <td className="px-4 py-2.5 text-gray-500">{new Date(k.created_at).toLocaleDateString('pt-BR')}</td>
                <td className="px-4 py-2.5 text-gray-500">{k.last_used_at ? new Date(k.last_used_at).toLocaleString('pt-BR') : '—'}</td>
                <td className="px-4 py-2.5 text-right">
                  <button onClick={() => removeKey.mutate(k.id)} className="rounded p-1 text-gray-400 hover:text-red-500">
                    <Trash2 size={14} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {/* System Flags */}
      <section className="rounded-xl border border-gray-100 bg-white p-5 shadow-sm">
        <h2 className="mb-4 text-sm font-semibold text-gray-700">Feature Flags do Sistema</h2>
        {loadingFlags && <p className="text-sm text-gray-400">Carregando…</p>}
        <div className="space-y-3">
          {flags.map((f) => {
            const flagName = f.key.split(':').slice(1).join(':');
            const currentValue = String(f.value ?? '');
            return (
            <div key={f.key} className="flex items-center gap-4 rounded-lg border border-gray-100 px-4 py-3">
              <div className="flex-1">
                <p className="text-sm font-medium text-gray-800">{flagName}</p>
              </div>
              <input
                defaultValue={currentValue}
                onBlur={(e) => {
                  if (e.target.value !== currentValue) {
                    saveFlag.mutate({ name: flagName, value: e.target.value });
                  }
                }}
                className="w-32 rounded border border-gray-200 px-2 py-1 text-sm focus:outline-none focus:ring-1 focus:ring-brand"
              />
            </div>
            );
          })}
          {!loadingFlags && flags.length === 0 && (
            <p className="text-sm text-gray-400">Nenhuma flag configurada</p>
          )}
        </div>
      </section>
    </div>
  );
}
