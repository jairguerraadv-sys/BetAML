'use client';

import { useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, PlayerList, PlayerListEntry } from '@/lib/api';
import { List, Plus, Trash2, Upload } from 'lucide-react';

const fetchLists = () => api.get<PlayerList[]>('/player-lists').then((r) => r.data);
const fetchEntries = (listId: string) => api.get<PlayerListEntry[]>(`/player-lists/${listId}/entries`).then((r) => r.data);

export default function PlayerListsPage() {
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [showForm, setShowForm] = useState(false);
  const [selectedList, setSelectedList] = useState<PlayerList | null>(null);
  const [entryText, setEntryText] = useState('');
  const [valueType, setValueType] = useState('CPF');
  const [uploadMsg, setUploadMsg] = useState('');
  const [form, setForm] = useState({
    name: '',
    description: '',
    list_type: 'BLACKLIST',
    source: 'MANUAL',
  });

  const { data: lists = [], isLoading } = useQuery({
    queryKey: ['player-lists'],
    queryFn: fetchLists,
  });

  const { data: entries = [] } = useQuery({
    queryKey: ['player-list-entries', selectedList?.id],
    queryFn: () => fetchEntries(selectedList!.id),
    enabled: Boolean(selectedList?.id),
  });

  const create = useMutation({
    mutationFn: () => api.post('/player-lists', form),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['player-lists'] });
      setShowForm(false);
      setForm({ name: '', description: '', list_type: 'BLACKLIST', source: 'MANUAL' });
    },
  });

  const remove = useMutation({
    mutationFn: (id: string) => api.delete(`/player-lists/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['player-lists'] });
      if (selectedList?.id) setSelectedList(null);
    },
  });

  const addEntries = useMutation({
    mutationFn: async () => {
      if (!selectedList) return;
      const values = entryText.split('\n').map((v) => v.trim()).filter(Boolean);
      return api.post(`/player-lists/${selectedList.id}/entries`, { values, value_type: valueType });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['player-list-entries', selectedList?.id] });
      qc.invalidateQueries({ queryKey: ['player-lists'] });
      setEntryText('');
    },
  });

  const removeEntry = useMutation({
    mutationFn: ({ listId, entryId }: { listId: string; entryId: string }) =>
      api.delete(`/player-lists/${listId}/entries/${entryId}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['player-list-entries', selectedList?.id] });
      qc.invalidateQueries({ queryKey: ['player-lists'] });
    },
  });

  async function handleUpload(listId: string) {
    const file = fileRef.current?.files?.[0];
    if (!file) return;
    const fd = new FormData();
    fd.append('file', file);
    try {
      await api.post(`/player-lists/${listId}/upload-csv?value_type=${encodeURIComponent(valueType)}`, fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      setUploadMsg('Upload concluído');
      qc.invalidateQueries({ queryKey: ['player-lists'] });
      qc.invalidateQueries({ queryKey: ['player-list-entries', listId] });
    } catch {
      setUploadMsg('Erro no upload');
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <List size={22} className="text-brand" />
          <div>
            <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Listas de Monitoramento</h1>
            <p className="text-sm text-gray-500">Listas de permissão, bloqueio, acompanhamento especial e perfis por operador.</p>
          </div>
        </div>
        <button
          onClick={() => setShowForm((v) => !v)}
          aria-label="Nova lista"
          className="flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white"
        >
          <Plus size={16} /> Nova lista
        </button>
      </div>

      {showForm && (
        <form className="grid gap-4 rounded-xl border border-brand/20 bg-brand/5 p-5 md:grid-cols-4" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
          <input aria-label="Nome da player list" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Nome" className="rounded-lg border px-3 py-2 text-sm dark:bg-gray-800" />
          <input aria-label="Descrição da player list" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} placeholder="Descrição" className="rounded-lg border px-3 py-2 text-sm dark:bg-gray-800" />
          <select aria-label="Tipo da player list" value={form.list_type} onChange={(e) => setForm({ ...form, list_type: e.target.value })} className="rounded-lg border px-3 py-2 text-sm dark:bg-gray-800">
            <option value="WHITELIST">WHITELIST</option>
            <option value="BLACKLIST">BLACKLIST</option>
            <option value="WATCH_LIST">WATCH_LIST</option>
            <option value="CUSTOM">CUSTOM</option>
          </select>
          <select aria-label="Origem da player list" value={form.source} onChange={(e) => setForm({ ...form, source: e.target.value })} className="rounded-lg border px-3 py-2 text-sm dark:bg-gray-800">
            <option value="MANUAL">MANUAL</option>
            <option value="AUTO">AUTO</option>
            <option value="EXTERNAL">EXTERNAL</option>
          </select>
          <div className="md:col-span-4 flex gap-2">
            <button aria-label="Salvar player list" type="submit" className="rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white">Salvar</button>
            <button type="button" onClick={() => setShowForm(false)} className="rounded-lg border px-4 py-2 text-sm">Cancelar</button>
          </div>
        </form>
      )}

      <div className="grid gap-6 lg:grid-cols-[1.1fr_0.9fr]">
        <div className="overflow-hidden rounded-xl border border-gray-100 bg-white shadow-sm dark:border-gray-700 dark:bg-gray-900">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-xs uppercase text-gray-500 dark:bg-gray-800 dark:text-gray-400">
              <tr>
                <th className="px-4 py-3 text-left">Nome</th>
                <th className="px-4 py-3 text-left">Tipo</th>
                <th className="px-4 py-3 text-left">Origem</th>
                <th className="px-4 py-3 text-right">Entradas</th>
                <th className="px-4 py-3 text-left">Status</th>
                <th className="px-4 py-3" />
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50 dark:divide-gray-800">
              {isLoading && <tr><td colSpan={6} className="py-8 text-center text-gray-400">Carregando…</td></tr>}
              {!isLoading && lists.length === 0 && <tr><td colSpan={6} className="py-8 text-center text-gray-400">Nenhuma lista cadastrada</td></tr>}
              {lists.map((list) => (
                <tr key={list.id} className={`cursor-pointer hover:bg-gray-50/50 dark:hover:bg-gray-800/50 ${selectedList?.id === list.id ? 'bg-brand/5' : ''}`} onClick={() => setSelectedList(list)}>
                  <td className="px-4 py-3 font-medium">{list.name}</td>
                  <td className="px-4 py-3">{list.list_type}</td>
                  <td className="px-4 py-3 text-gray-500">{list.source ?? 'MANUAL'}</td>
                  <td className="px-4 py-3 text-right">{list.entry_count}</td>
                  <td className="px-4 py-3">{list.active === false ? 'Inativa' : 'Ativa'}</td>
                  <td className="px-4 py-3 text-right">
                    <button onClick={(e) => { e.stopPropagation(); remove.mutate(list.id); }} className="rounded p-1 text-gray-400 hover:text-red-500">
                      <Trash2 size={14} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="rounded-xl border border-gray-100 bg-white p-5 shadow-sm dark:border-gray-700 dark:bg-gray-900">
          {!selectedList ? (
            <p className="text-sm text-gray-500">Selecione uma lista para ver e editar as entradas.</p>
          ) : (
            <div className="space-y-4">
              <div>
                <h2 className="text-lg font-semibold">{selectedList.name}</h2>
                <p className="text-xs text-gray-400">{selectedList.list_type} · {selectedList.source ?? 'MANUAL'}</p>
              </div>

              <div className="flex items-center gap-2">
                <select aria-label="Tipo do valor da entrada" value={valueType} onChange={(e) => setValueType(e.target.value)} className="rounded-lg border px-3 py-2 text-sm dark:bg-gray-800">
                  <option value="CPF">CPF</option>
                  <option value="PLAYER_ID">PLAYER_ID</option>
                  <option value="CUSTOM">CUSTOM</option>
                </select>
                <button
                  onClick={() => fileRef.current?.click()}
                  aria-label="Importar CSV de player list"
                  className="inline-flex items-center gap-2 rounded-lg border px-3 py-2 text-sm"
                >
                  <Upload size={14} /> Importar CSV
                </button>
                {uploadMsg && <span className="text-xs text-gray-500">{uploadMsg}</span>}
              </div>

              <textarea
                aria-label="Entradas manuais da player list"
                rows={5}
                value={entryText}
                onChange={(e) => setEntryText(e.target.value)}
                placeholder="Uma entrada por linha"
                className="w-full rounded-lg border px-3 py-2 text-sm dark:bg-gray-800"
              />
              <button aria-label="Adicionar entradas à player list" onClick={() => addEntries.mutate()} className="rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white">
                Adicionar entradas
              </button>

              <div className="max-h-80 overflow-y-auto rounded-lg border border-gray-100 dark:border-gray-800">
                {entries.length === 0 ? (
                  <p className="p-4 text-sm text-gray-500">Nenhuma entrada cadastrada.</p>
                ) : (
                  <table className="w-full text-sm">
                    <thead className="bg-gray-50 text-xs uppercase text-gray-500 dark:bg-gray-800 dark:text-gray-400">
                      <tr>
                        <th className="px-3 py-2 text-left">Valor</th>
                        <th className="px-3 py-2 text-left">Tipo</th>
                        <th className="px-3 py-2 text-left">Adicionado em</th>
                        <th className="px-3 py-2" />
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-50 dark:divide-gray-800">
                      {entries.map((entry) => (
                        <tr key={entry.id}>
                          <td className="px-3 py-2 font-mono text-xs">{entry.value ?? entry.external_player_id ?? entry.cpf_hash ?? '—'}</td>
                          <td className="px-3 py-2 text-gray-500">{entry.value_type ?? '—'}</td>
                          <td className="px-3 py-2 text-gray-500">{entry.added_at ? new Date(entry.added_at).toLocaleString('pt-BR') : '—'}</td>
                          <td className="px-3 py-2 text-right">
                            <button onClick={() => removeEntry.mutate({ listId: selectedList.id, entryId: entry.id })} className="rounded p-1 text-gray-400 hover:text-red-500">
                              <Trash2 size={14} />
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      <input aria-label="Arquivo CSV da player list" ref={fileRef} type="file" accept=".csv" className="hidden" onChange={() => selectedList && handleUpload(selectedList.id)} />
    </div>
  );
}
