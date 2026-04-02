'use client';
import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import DataTable from '@/components/DataTable';
import {
  fetchIngestErrors,
  fetchMappings,
  fetchMappingVersions,
  resolveIngestError,
  replayIngestError,
  type IngestError,
  type MappingListItem,
  type MappingVersion,
} from '@/lib/api';
import { useIngestStream } from '@/hooks/useIngestStream';
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
  const [detailTarget, setDetailTarget] = useState<IngestError | null>(null);
  const [replayEntityType, setReplayEntityType] = useState('TRANSACTION');
  const [selectedMappingId, setSelectedMappingId] = useState('');
  const [selectedMappingVersionId, setSelectedMappingVersionId] = useState('');
  const ingestStream = useIngestStream(true);

  const { data: errors = [], isLoading, refetch } = useQuery({
    queryKey: ['ingest-errors', resolvedFilter, sourceSystem, offset],
    queryFn: () => fetchIngestErrors({
      source_system: sourceSystem || undefined,
      resolved: resolvedFilter === 'all' ? undefined : resolvedFilter === 'resolved',
      limit: PAGE_SIZE,
      offset,
    }),
  });

  const { data: mappings = [] } = useQuery({
    queryKey: ['mappings', 'ingest-errors'],
    queryFn: fetchMappings,
  });

  const availableMappings = useMemo(
    () => mappings.filter((mapping) => mapping.source_system === replayTarget?.source_system),
    [mappings, replayTarget?.source_system],
  );

  const effectiveMappingId = selectedMappingId || availableMappings[0]?.id || '';

  const { data: mappingVersions = [] } = useQuery({
    queryKey: ['mapping-versions', effectiveMappingId, 'ingest-errors'],
    queryFn: () => fetchMappingVersions(effectiveMappingId),
    enabled: !!effectiveMappingId,
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
      replayIngestError(id, {
        corrected_payload: payload,
        note,
        entity_type: replayEntityType,
        mapping_config_id: selectedMappingVersionId || undefined,
        resolve_original: true,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['ingest-errors'] });
      setReplayTarget(null);
      setReplayPayload('');
      setReplayNote('');
      setReplayEntityType('TRANSACTION');
      setSelectedMappingId('');
      setSelectedMappingVersionId('');
    },
  });

  const summary = useMemo(() => {
    const total = errors.length;
    const unresolved = errors.filter((item) => !item.resolved).length;
    const resolved = total - unresolved;
    return { total, unresolved, resolved };
  }, [errors]);

  useEffect(() => {
    if (!ingestStream.lastEvent) {
      return;
    }
    void refetch();
  }, [ingestStream.lastEvent?.count, refetch]);

  const columns = useMemo(() => [
    {
      header: 'Sistema de origem',
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
      header: 'Dados enviados',
      accessorKey: 'raw_payload' as keyof IngestError,
      cell: (v: unknown) => {
        const preview = String(v ?? '').replace(/\s+/g, ' ').trim();
        return (
          <span className="block max-w-[260px] truncate font-mono text-[11px] text-gray-500" title={preview}>
            {preview || '—'}
          </span>
        );
      },
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
              setReplayEntityType(String(row.entity_type || 'TRANSACTION').toUpperCase());
              setSelectedMappingId('');
              setSelectedMappingVersionId('');
              try {
                setReplayPayload(JSON.stringify(JSON.parse(row.raw_payload), null, 2));
              } catch {
                setReplayPayload(
                  JSON.stringify(
                    {
                      event_id: '',
                      external_player_id: '',
                      transaction_type: 'DEPOSIT',
                      amount: 0,
                      occurred_at: '',
                      raw_payload: row.raw_payload,
                    },
                    null,
                    2,
                  ),
                );
              }
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
          aria-label="Atualizar quarentena de ingestão"
          className="flex items-center gap-1.5 rounded-lg border border-gray-200 px-3 py-1.5 text-sm hover:bg-gray-50 dark:border-gray-700"
        >
          <RefreshCw size={14} />
          Atualizar
        </button>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-3 rounded-xl border border-gray-200 bg-white p-4 dark:border-gray-700 dark:bg-gray-900">
        <select
          aria-label="Status do filtro de quarentena"
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
          aria-label="Source system do filtro de quarentena"
          placeholder="Sistema de origem…"
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
        onRowClick={(row) => setDetailTarget(row)}
        caption="Lista de erros de ingestão em quarentena"
      />

      <section className="grid gap-3 md:grid-cols-3">
        <div className="rounded-xl border border-gray-200 bg-white p-4 dark:border-gray-700 dark:bg-gray-900">
          <p className="text-xs uppercase tracking-wide text-gray-400">Erros na página</p>
          <p className="mt-1 text-2xl font-semibold text-gray-900 dark:text-white">{summary.total}</p>
        </div>
        <div className="rounded-xl border border-gray-200 bg-white p-4 dark:border-gray-700 dark:bg-gray-900">
          <p className="text-xs uppercase tracking-wide text-gray-400">Pendentes</p>
          <p className="mt-1 text-2xl font-semibold text-red-600">{summary.unresolved}</p>
        </div>
        <div className="rounded-xl border border-gray-200 bg-white p-4 dark:border-gray-700 dark:bg-gray-900">
          <p className="text-xs uppercase tracking-wide text-gray-400">Resolvidos</p>
          <p className="mt-1 text-2xl font-semibold text-emerald-600">{summary.resolved}</p>
        </div>
      </section>

      <section className="flex flex-wrap items-center gap-3 rounded-xl border border-gray-200 bg-white p-4 text-sm dark:border-gray-700 dark:bg-gray-900">
        <span className={`rounded-full px-2.5 py-1 text-xs font-medium ${ingestStream.connected ? 'bg-emerald-100 text-emerald-700' : 'bg-amber-100 text-amber-700'}`}>
          {ingestStream.connected ? 'Streaming ativo' : 'Reconectando stream'}
        </span>
        <span className="text-gray-500 dark:text-gray-400">
          Heartbeats: {ingestStream.lastEvent?.count ?? 0}
        </span>
        <span className="text-gray-500 dark:text-gray-400">
          Ultimo evento: {ingestStream.lastEvent?.ts ? new Date(ingestStream.lastEvent.ts).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'medium' }) : '—'}
        </span>
        <span className="text-gray-500 dark:text-gray-400">
          Reconexoes: {ingestStream.reconnectCount}
        </span>
        {ingestStream.lastEvent?.summary && (
          <>
            <span className="text-gray-500 dark:text-gray-400">
              Ativos: {ingestStream.lastEvent.summary.active_jobs}
            </span>
            <span className="text-gray-500 dark:text-gray-400">
              Falhas 24h: {ingestStream.lastEvent.summary.failed_jobs_24h}
            </span>
            <span className="text-gray-500 dark:text-gray-400">
              Quarentena aberta: {ingestStream.lastEvent.summary.unresolved_errors}
            </span>
            <span className="text-gray-500 dark:text-gray-400">
              Rate limit: {ingestStream.lastEvent.summary.configured_rate_limit_per_min}/min
            </span>
            <span className="text-gray-500 dark:text-gray-400">
              WS ativos: {ingestStream.lastEvent.summary.ws_active_connections}
            </span>
            <span className="text-gray-500 dark:text-gray-400">
              Fila WS: {ingestStream.lastEvent.summary.ws_queued_messages}/{ingestStream.lastEvent.summary.ws_max_queue_size}
            </span>
            <span className="text-gray-500 dark:text-gray-400">
              Backpressure: {ingestStream.lastEvent.summary.ws_backpressure_events}
            </span>
          </>
        )}
        {ingestStream.error && (
          <span className="text-amber-600">{ingestStream.error}</span>
        )}
      </section>

      {ingestStream.lastEvent?.summary?.quarantine_breakdown?.length ? (
        <section className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm dark:border-red-800 dark:bg-red-950/30">
          <p className="font-semibold text-red-800 dark:text-red-200">Concentração atual da quarentena</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {ingestStream.lastEvent.summary.quarantine_breakdown.map((item) => (
              <span
                key={`${item.source_system}-${item.entity_type ?? 'unknown'}`}
                className="rounded-full border border-red-300 px-2.5 py-1 text-xs text-red-800 dark:border-red-700 dark:text-red-200"
              >
                {item.source_system} · {item.entity_type ?? '—'} · {item.count}
              </span>
            ))}
          </div>
        </section>
      ) : null}

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

      {detailTarget && (
        <>
          <div className="fixed inset-0 z-30 bg-black/20" onClick={() => setDetailTarget(null)} />
          <aside className="fixed inset-y-0 right-0 z-40 flex w-[520px] flex-col bg-white shadow-2xl dark:bg-gray-900">
            <div className="flex items-start justify-between border-b border-gray-100 p-4 dark:border-gray-700">
              <div>
                <h2 className="text-base font-semibold text-gray-900 dark:text-white">Detalhe da quarentena</h2>
                <p className="font-mono text-xs text-gray-400">{detailTarget.id}</p>
              </div>
              <button onClick={() => setDetailTarget(null)}>
                <X size={18} className="text-gray-300 hover:text-gray-500" />
              </button>
            </div>
            <div className="flex-1 space-y-4 overflow-y-auto p-4">
              <dl className="grid grid-cols-2 gap-3 text-sm">
                <div>
                  <dt className="text-xs text-gray-400">Sistema de origem</dt>
                  <dd>{detailTarget.source_system}</dd>
                </div>
                <div>
                  <dt className="text-xs text-gray-400">Entidade</dt>
                  <dd>{detailTarget.entity_type ?? '—'}</dd>
                </div>
                <div>
                  <dt className="text-xs text-gray-400">Linha</dt>
                  <dd>{detailTarget.line_number ?? '—'}</dd>
                </div>
                <div>
                  <dt className="text-xs text-gray-400">Status</dt>
                  <dd>{detailTarget.resolved ? 'Resolvido' : 'Pendente'}</dd>
                </div>
                <div className="col-span-2">
                  <dt className="text-xs text-gray-400">Motivo</dt>
                  <dd className="text-red-600">{detailTarget.error_reason}</dd>
                </div>
              </dl>

              <div>
                <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-400">Detalhe técnico</p>
                <pre className="max-h-32 overflow-auto rounded-lg bg-slate-950 p-3 text-[11px] text-sky-200">
                  {JSON.stringify(detailTarget.error_detail || {}, null, 2)}
                </pre>
              </div>

              <div>
                <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-400">Dados enviados</p>
                <pre className="max-h-64 overflow-auto rounded-lg bg-gray-950 p-3 text-[11px] text-green-200">
                  {detailTarget.raw_payload}
                </pre>
              </div>
            </div>
          </aside>
        </>
      )}

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
                <h2 className="text-lg font-semibold dark:text-white">Reenviar registro corrigido</h2>
                <p className="mt-0.5 font-mono text-xs text-gray-400">{replayTarget.id.slice(0, 8)}…</p>
              </div>
              <button onClick={() => setReplayTarget(null)}>
                <X size={18} className="text-gray-300 hover:text-gray-500" />
              </button>
            </div>

            <p className="mb-2 text-xs text-gray-500">
              Edite os dados abaixo e reenvie para reprocessamento. O registro com erro será arquivado e o novo será processado com a versão de integração selecionada.
            </p>

            <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">
              Registro corrigido (JSON)
            </label>
            <textarea
              aria-label="Payload corrigido do replay de ingestão"
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
              aria-label="Nota do replay de ingestão"
              value={replayNote}
              onChange={(e) => setReplayNote(e.target.value)}
              placeholder="Ex.: CPF corrigido no campo external_player_id"
              className="mb-4 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-800 dark:text-white"
            />

            <div className="mb-4 grid gap-3 md:grid-cols-2">
              <div>
                <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">
                  Entity type
                </label>
                <select
                  aria-label="Entity type do replay de ingestão"
                  value={replayEntityType}
                  onChange={(e) => setReplayEntityType(e.target.value)}
                  className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-800 dark:text-white"
                >
                  {['TRANSACTION', 'BET', 'PLAYER', 'DEVICE_EVENT'].map((option) => (
                    <option key={option} value={option}>{option}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">
                  MappingConfig
                </label>
                <select
                  aria-label="MappingConfig do replay de ingestão"
                  value={effectiveMappingId}
                  onChange={(e) => {
                    setSelectedMappingId(e.target.value);
                    setSelectedMappingVersionId('');
                  }}
                  className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-800 dark:text-white"
                >
                  <option value="">Sem mapping específico</option>
                  {availableMappings.map((mapping: MappingListItem) => (
                    <option key={mapping.id} value={mapping.id}>
                      {mapping.name} · v{mapping.version_number}
                    </option>
                  ))}
                </select>
              </div>
              <div className="md:col-span-2">
                <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">
                  Versão do mapping
                </label>
                <select
                  aria-label="Versão do mapping no replay de ingestão"
                  value={selectedMappingVersionId}
                  onChange={(e) => setSelectedMappingVersionId(e.target.value)}
                  className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-800 dark:text-white"
                >
                  <option value="">Usar versão atual</option>
                  {mappingVersions.map((version: MappingVersion) => (
                    <option key={version.id} value={version.id}>
                      v{version.version_number}{version.is_current ? ' atual' : ''} · {version.change_notes || 'sem notas'}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <div className="flex gap-3">
              <button
                aria-label="Enviar replay de ingestão"
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
