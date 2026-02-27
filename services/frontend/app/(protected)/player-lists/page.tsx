'use client';
import { useState, useRef } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { List, Trash2, Plus, Upload } from 'lucide-react';

interface PlayerList {
  id: string;
  name: string;
  description?: string;
  list_type: string;
  entry_count: number;
  created_at: string;
}

const fetchLists = () =>
  api.get<PlayerList[]>('/player-lists').then((r) => r.data);

export default function PlayerListsPage() {
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);

  const { data: lists = [], isLoading } = useQuery({
    queryKey: ['player-lists'],
    queryFn: fetchLists,
  });

  const [form, setForm] = useState({ name: '', description: '', list_type: 'BLOCKLIST' });
  const [showForm, setShowForm] = useState(false);
  const [selectedList, setSelectedList] = useState<string | null>(null);
  const [uploadMsg, setUploadMsg] = useState('');

  const create = useMutation({
    mutationFn: () => api.post('/player-lists', form),
    onSuccess:  () => { qc.invalidateQueries({ queryKey: ['player-lists'] }); setShowForm(false); },
  });

  const remove = useMutation({
    mutationFn: (id: string) => api.delete(`/player-lists/${id}`),
    onSuccess:  () => qc.invalidateQueries({ queryKey: ['player-lists'] }),
  });

  async function handleUpload(listId: string) {
    const file = fileRef.current?.files?.[0];
    if (!file) return;
    const fd = new FormData();
    fd.append('file', file);
    try {
      await api.post(`/player-lists/${listId}/upload-csv`, fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      setUploadMsg('Upload concluído!');
      qc.invalidateQueries({ queryKey: ['player-lists'] });
    } catch {
      setUploadMsg('Erro no upload.');
    }
  }

  const TYPE_BADGE: Record<string, string> = {
    BLOCKLIST:  'bg-red-100 text-red-700',
    ALLOWLIST:  'bg-green-100 text-green-700',
    WATCHLIST:  'bg-yellow-100 text-yellow-700',
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <List size={22} className="text-brand" />
          <h1 className="text-2xl font-bold text-gray-900">Listas de Jogadores</h1>
        </div>
        <button
          onClick={() => setShowForm((v) => !v)}
          className="flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand/90"
        >
          <Plus size={16} /> Nova Lista
        </button>
      </div>

      {showForm && (
        <form
          className="space-y-4 rounded-xl border border-brand/20 bg-brand/5 p-5"
          onSubmit={(e) => { e.preventDefault(); create.mutate(); }}
        >
          <h2 className="font-semibold text-gray-800">Criar Lista</h2>
          <div className="grid grid-cols-3 gap-4">
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
              <label className="mb-1 block text-xs font-medium text-gray-600">Tipo</label>
              <select
                value={form.list_type}
                onChange={(e) => setForm({ ...form, list_type: e.target.value })}
                className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand"
              >
                <option value="BLOCKLIST">BLOCKLIST</option>
                <option value="ALLOWLIST">ALLOWLIST</option>
                <option value="WATCHLIST">WATCHLIST</option>
              </select>
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-gray-600">Descrição</label>
              <input
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
                className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand"
              />
            </div>
          </div>
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

      <div className="overflow-hidden rounded-xl border border-gray-100 bg-white shadow-sm">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-xs font-semibold uppercase text-gray-500">
            <tr>
              <th className="px-4 py-3 text-left">Nome</th>
              <th className="px-4 py-3 text-left">Tipo</th>
              <th className="px-4 py-3 text-right">Entradas</th>
              <th className="px-4 py-3 text-left">Criado em</th>
              <th className="px-4 py-3 text-center">Upload CSV</th>
              <th className="px-4 py-3" />
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-50">
            {isLoading && (
              <tr><td colSpan={6} className="py-8 text-center text-gray-400">Carregando…</td></tr>
            )}
            {!isLoading && lists.length === 0 && (
              <tr><td colSpan={6} className="py-8 text-center text-gray-400">Nenhuma lista cadastrada</td></tr>
            )}
            {lists.map((l: PlayerList) => (
              <tr key={l.id} className="hover:bg-gray-50/50">
                <td className="px-4 py-3 font-medium">{l.name}</td>
                <td className="px-4 py-3">
                  <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${TYPE_BADGE[l.list_type] ?? 'bg-gray-100'}`}>
                    {l.list_type}
                  </span>
                </td>
                <td className="px-4 py-3 text-right">{l.entry_count.toLocaleString('pt-BR')}</td>
                <td className="px-4 py-3 text-gray-500">{new Date(l.created_at).toLocaleDateString('pt-BR')}</td>
                <td className="px-4 py-3 text-center">
                  <button
                    onClick={() => { setSelectedList(l.id); fileRef.current?.click(); }}
                    className="inline-flex items-center gap-1 rounded px-2 py-1 text-xs text-brand hover:underline"
                  >
                    <Upload size={13} /> Importar
                  </button>
                  {selectedList === l.id && uploadMsg && (
                    <p className="mt-0.5 text-xs text-green-600">{uploadMsg}</p>
                  )}
                </td>
                <td className="px-4 py-3 text-right">
                  <button
                    onClick={() => remove.mutate(l.id)}
                    className="rounded p-1 text-gray-400 hover:text-red-500"
                  >
                    <Trash2 size={14} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Hidden file input for CSV upload */}
      <input
        ref={fileRef}
        type="file"
        accept=".csv"
        className="hidden"
        onChange={() => selectedList && handleUpload(selectedList)}
      />
    </div>
  );
}
