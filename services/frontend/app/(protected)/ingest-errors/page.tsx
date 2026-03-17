'use client';
import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import DataTable from '@/components/DataTable';
import {
  fetchIngestErrors,
  resolveIngestError,
  replayIngestError,
  type IngestError,
} from '@/lib/api';
import { AlertOctagon, RefreshCw, X } from 'lucide-react';

const PAGE_SIZE = 50;

type ResolvedFilter = 'unresolved' | 'resolved' | 'all';

export default function IngestErrorsPage() {
  const qc = useQueryClient();

  const [resolvedFilter, setResolvedFilter] = useState<ResolvedFilter>('unresolved');
  const [sourceSystem, setSourceSystem] = useState('');
  const [offset, setOffset] = useState(0);

  const [resolveTarget, setResolveTarget] = useState<IngestError | null>(null);
  const [resolveNote, setResolveNote] = useState('');

  const [replayTarget, setReplayTarget] = useState<IngestError | null>(null);
  const [replayPayload, setReplayPayload] = useState('');
  const [replayNote, setReplayNote] = useState('');
  const [replayParseError, setReplayParseError] = useState('');

  const { data: errors = [], isLoading, refetch } = useQuery({
    queryKey: ['ingest-errors', resolvedFilter, sourceSystem, offset],
    queryFn: () => fetchIngestErrors({
      source_system: sourceSystem || undefined,
      resolved: resolvedFilter === 'all' ? undefined : resolvedFilter === 'resolved',
      limit: PAGE_SIZE,
      offset,
    }),
  });

  const resolveMutation = useMutation({
    mutationFn: ({ id, note }: { id: string; note: string }) =>
      resolveIngestError(id, { note }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['ingest-errors'] });
      setResolveTarget(null);
      setResolveNote('');
    },
  });

  const replayMutation = useMutation({
    mutationFn: ({ id, payload, note }: { id: string; payload: Record<string, unknown>; note: string }) =>
      replayIngestError(id, { corrected_payload: payload, note, resolve_original: true }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['ingest-errors'] });
      setReplayTarget(null);
      setReplayPayload('');
      setReplayNote('');
    },
  });

  const columns = useMemo(() => [
    {
      header: 'Source',
      accessorKey: 'source_system' as keyof IngestError,
    },
    {
      header: 'Tipo',
      accessorKey: 'entity_type' as keyof IngestError,
      cell: (v: unknown) => <span className="text-xs">{String(v ?? '—')}</span>,
    },
    {
      header: 'Linha',
      accessorKey: 'line_number' as keyof IngestError,
      cell: (v: unknown) => <span className="text-xs">{v != null ? String(v) : '—'}</span>,
    },
    {
      header: 'Motivo',
      accessorKey: 'error_reason' as keyof IngestError,
      cell: (v: unknown) => (
        <span className="block max-w-[240px] truncate text-xs text-red-600" title={String(v)}>
          {String(v)}
        </span>
      ),
    },
    {
      header: 'Resolvido',
      accessorKey: 'resolved' as keyof IngestError,
      cell: (v: unknown) =>
        v ? (
          <span className="rounded bg-green-100 px-2 py-0.5 text-xs text-green-700">Sim</span>
        ) : (
          <span className="rounded bg-red-100 px-2 py-0.5 text-xs text-red-600">Não</span>
        ),
    },
    {
      header: 'Criado em',
      accessorKey: 'created_at' as keyof IngestError,
      cell: (v: unknown) => (
        <span className="text-xs text-gray-500">
          {new Date(String(v)).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' })}
        </span>
      ),
    },
    {
      header: 'Ações',
      accessorKey: 'id' as keyof IngestError,
      cell: (_: unknown, row: IngestError) => (
        <div className="flex gap-1">
          {!row.resolved && (
            <button
              onClick={(e) => { e.stopPropagation(); setResolveTarget(row); setResolveNote(''); }}
              className="rounded border border-gray-200 px-2 py-0.5 text-xs hover:bg-gray-50"
            >
              Resolver
            </button>
          )}
          <button
            onClick={(e) => {
              e.stopPropagation();
              setReplayTarget(row);
              setReplayPayload(row.raw_payload);
              setReplayParseError('');
              setReplayNote('');
            }}
            className="rounded border border-blue-200 bg-blue-50 px-2 py-0.5 text-xs text-blue-700 hover:bg-blue-100"
          >
            Replay
          </button>
        </div>
      ),
    },
  ], []);

  return (
    <div className="space-y-6 p-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <AlertOctagon className="h-5 w-5 text-red-500" />
          <h1 className="text-xl font-semibold text-gray-900 dark:text-white">
            Quarentena de Erros de Ingestão
          </h1>
        </div>
        <button
          onClick={() => refetch()}
          className="flex items-center gap-1.5 rounded-lg border border-gray-200 px-3 py-1.5 text-sm hover:bg-gray-50 dark:border-gray-700"
        >
          <RefreshCw size={14} />
          Atualizar
        </button>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-3 rounded-xl border border-gray-200 bg-white p-4 dark:border-gray-700 dark:bg-gray-900">
        <select
          value={resolvedFilter}
          onChange={(e) => { setResolvedFilter(e.target.value as ResolvedFilter); setOffset(0); }}
          className="rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-800 dark:text-white"
        >
          <option value="unresolved">Não resolvidos</option>
          <option value="resolved">Resolvidos</option>
          <option value="all">Todos</option>
        </select>

        <input
          type="text"
          placeholder="Source system…"
          value={sourceSystem}
          onChange={(e) => { setSourceSystem(e.target.value); setOffset(0); }}
          className="rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-800 dark:text-white"
        />
      </div>

      {/* Table */}
      <DataTable<IngestError>
        data={errors}
        columns={columns}
        loading={isLoading}
        caption="Lista de erros de ingestão em quarentena"
      />

      {/* Pagination */}
      <div className="flex items-center justify-between pt-1">
        <button
          disabled={offset === 0}
          onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
          className="rounded-lg border border-gray-200 px-3 py-1.5 text-xs disabled:opacity-40 dark:border-gray-700"
        >
          ← Anterior
        </button>
        <span className="text-xs text-gray-400">
          {offset + 1}–{offset + errors.length}
        </span>
        <button
          disabled={errors.length < PAGE_SIZE}
          onClick={() => setOffset((o) => o + PAGE_SIZE)}
          className="rounded-lg border border-gray-200 px-3 py-1.5 text-xs disabled:opacity-40 dark:border-gray-700"
        >
          Próxima →
        </button>
      </div>

      {/* Resolve Modal */}
      {resolveTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-2xl dark:bg-gray-900">
            <div className="mb-4 flex items-start justify-between">
              <div>
                <h2 className="text-lg font-semibold dark:text-white">Resolver erro de ingestão</h2>
                <p className="mt-0.5 font-mono text-xs text-gray-400">{resolveTarget.id.slice(0, 8)}…</p>
              </div>
              <button onClick={() => setResolveTarget(null)}>
                <X size={18} className="text-gray-300 hover:text-gray-500" />
              </button>
            </div>

            <div className="mb-3 rounded-lg border border-red-100 bg-red-50 px-3 py-2.5 text-xs text-red-700">
              {resolveTarget.error_reason}
            </div>

            <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">
              Nota de resolução (opcional)
            </label>
            <textarea
              rows={3}
              value={resolveNote}
              onChange={(e) => setResolveNote(e.target.value)}
              placeholder="Descreva o motivo da resolução…"
              className="mb-4 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none dark:border-gray-700 dark:bg-gray-800 dark:text-white"
            />

            <div className="flex gap-3">
              <button
                onClick={() => resolveMutation.mutate({ id: resolveTarget.id, note: resolveNote })}
                disabled={resolveMutation.isPending}
                className="flex-1 rounded-lg bg-green-600 py-2.5 text-sm font-medium text-white hover:bg-green-700 disabled:opacity-50"
              >
                {resolveMutation.isPending ? 'Salvando…' : 'Confirmar resolução'}
              </button>
              <button
                onClick={() => setResolveTarget(null)}
                className="rounded-lg border border-gray-200 px-4 py-2.5 text-sm text-gray-500 hover:bg-gray-50 dark:border-gray-700"
              >
                Cancelar
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Replay Modal */}
      {replayTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-lg rounded-2xl bg-white p-6 shadow-2xl dark:bg-gray-900">
            <div className="mb-4 flex items-start justify-between">
              <div>
                <h2 className="text-lg font-semibold dark:text-white">Replay de payload corrigido</h2>
                <p className="mt-0.5 font-mono text-xs text-gray-400">{replayTarget.id.slice(0, 8)}…</p>
              </div>
              <button onClick={() => setReplayTarget(null)}>
                <X size={18} className="text-gray-300 hover:text-gray-500" />
              </button>
            </div>

            <p className="mb-2 text-xs text-gray-500">
              Edite o payload abaixo e envie para reprocessamento. O erro original será marcado como resolvido.
            </p>

            <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">
              Payload corrigido (JSON)
            </label>
            <textarea
              rows={10}
              value={replayPayload}
              onChange={(e) => {
                setReplayPayload(e.target.value);
                try {
                  JSON.parse(e.target.value);
                  setReplayParseError('');
                } catch {
                  setReplayParseError('JSON inválido');
                }
              }}
              className="mb-1 w-full rounded-lg border border-gray-200 bg-gray-950 px-3 py-2 font-mono text-xs text-green-200 focus:outline-none"
              spellCheck={false}
            />
            {replayParseError && (
              <p className="mb-2 text-xs text-red-500">{replayParseError}</p>
            )}

            <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">
              Nota (opcional)
            </label>
            <input
              value={replayNote}
              onChange={(e) => setReplayNote(e.target.value)}
              placeholder="Ex.: CPF corrigido no campo external_player_id"
              className="mb-4 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-800 dark:text-white"
            />

            <div className="flex gap-3">
              <button
                onClick={() => {
                  let parsed: Record<string, unknown>;
                  try {
                    parsed = JSON.parse(replayPayload) as Record<string, unknown>;
                  } catch {
                    return;
                  }
                  replayMutation.mutate({ id: replayTarget.id, payload: parsed, note: replayNote });
                }}
                disabled={!!replayParseError || !replayPayload || replayMutation.isPending}
                className="flex-1 rounded-lg bg-blue-600 py-2.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
              >
                {replayMutation.isPending ? 'Enviando…' : 'Enviar para Kafka'}
              </button>
              <button
                onClick={() => setReplayTarget(null)}
                className="rounded-lg border border-gray-200 px-4 py-2.5 text-sm text-gray-500 hover:bg-gray-50 dark:border-gray-700"
              >
                Cancelar
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
