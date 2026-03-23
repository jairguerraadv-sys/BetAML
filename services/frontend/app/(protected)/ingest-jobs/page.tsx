'use client';
import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import DataTable from '@/components/DataTable';
import {
  fetchIngestJobs,
  fetchIngestJob,
  fetchMappings,
  fetchMappingVersions,
  reprocessIngestJob,
  type IngestJob,
  type IngestJobStatus,
  type MappingListItem,
  type MappingVersion,
} from '@/lib/api';
import { useIngestStream } from '@/hooks/useIngestStream';
import { Activity, RefreshCw, X } from 'lucide-react';

const PAGE_SIZE = 50;

const STATUS_BADGE: Record<IngestJobStatus, string> = {
  QUEUED:     'bg-gray-100 text-gray-600',
  PROCESSING: 'bg-blue-100 text-blue-700',
  DONE:       'bg-green-100 text-green-700',
  PARTIAL:    'bg-yellow-100 text-yellow-700',
  FAILED:     'bg-red-100 text-red-700',
};

function StatusBadge({ status }: { status: IngestJobStatus }) {
  return (
    <span className={`rounded px-2 py-0.5 text-xs font-medium ${STATUS_BADGE[status] ?? 'bg-gray-100 text-gray-500'}`}>
      {status}
    </span>
  );
}

export default function IngestJobsPage() {
  const qc = useQueryClient();

  const [statusFilter, setStatusFilter] = useState('');
  const [sourceSystem, setSourceSystem] = useState('');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [offset, setOffset] = useState(0);
  const [drawerJob, setDrawerJob] = useState<IngestJob | null>(null);
  const [showReprocessForm, setShowReprocessForm] = useState(false);
  const [reprocessReason, setReprocessReason] = useState('');
  const [selectedMappingId, setSelectedMappingId] = useState('');
  const [selectedMappingVersionId, setSelectedMappingVersionId] = useState('');
  const ingestStream = useIngestStream(true);

  const { data: jobs = [], isLoading, refetch } = useQuery({
    queryKey: ['ingest-jobs', statusFilter, sourceSystem, dateFrom, dateTo, offset],
    queryFn: () => fetchIngestJobs({
      status: statusFilter || undefined,
      source_system: sourceSystem || undefined,
      from: dateFrom || undefined,
      to: dateTo || undefined,
      limit: PAGE_SIZE,
      offset,
    }),
  });

  const { data: jobDetail, isLoading: detailLoading } = useQuery({
    queryKey: ['ingest-job', drawerJob?.id],
    queryFn: () => fetchIngestJob(drawerJob!.id),
    enabled: !!drawerJob,
  });

  const { data: mappings = [] } = useQuery({
    queryKey: ['mappings', 'ingest-jobs'],
    queryFn: fetchMappings,
  });

  const availableMappings = useMemo(
    () => mappings.filter((mapping) => mapping.source_system === drawerJob?.source_system),
    [mappings, drawerJob?.source_system],
  );

  const effectiveMappingId =
    selectedMappingId || jobDetail?.mapping_config_id || availableMappings[0]?.id || '';

  const { data: mappingVersions = [] } = useQuery({
    queryKey: ['mapping-versions', effectiveMappingId, 'ingest-jobs'],
    queryFn: () => fetchMappingVersions(effectiveMappingId),
    enabled: !!effectiveMappingId,
  });

  const reprocessMutation = useMutation({
    mutationFn: ({ id, reason, mappingVersionId }: { id: string; reason: string; mappingVersionId?: string }) =>
      reprocessIngestJob(id, { reason, mapping_version_id: mappingVersionId || undefined }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['ingest-jobs'] });
      setDrawerJob(null);
      setShowReprocessForm(false);
      setReprocessReason('');
      setSelectedMappingId('');
      setSelectedMappingVersionId('');
    },
  });

  const summary = useMemo(() => {
    const total = jobs.length;
    const processed = jobs.reduce((acc, job) => acc + (job.processed_records ?? 0), 0);
    const failed = jobs.reduce((acc, job) => acc + (job.failed_records ?? 0), 0);
    const bytes = jobs.reduce((acc, job) => acc + (job.bytes_processed ?? 0), 0);
    return { total, processed, failed, bytes };
  }, [jobs]);

  useEffect(() => {
    if (!ingestStream.lastEvent) {
      return;
    }
    void refetch();
  }, [ingestStream.lastEvent?.count, refetch]);

  const columns = useMemo(() => [
    {
      header: 'Job ID',
      accessorKey: 'id' as keyof IngestJob,
      cell: (v: unknown) => (
        <span className="font-mono text-xs text-gray-500">{String(v).slice(0, 8)}…</span>
      ),
    },
    {
      header: 'Source System',
      accessorKey: 'source_system' as keyof IngestJob,
    },
    {
      header: 'Arquivo',
      accessorKey: 'file_name' as keyof IngestJob,
      cell: (v: unknown) => (
        <span className="block max-w-[160px] truncate text-xs" title={String(v ?? '—')}>
          {String(v ?? '—')}
        </span>
      ),
    },
    {
      header: 'Status',
      accessorKey: 'status' as keyof IngestJob,
      cell: (v: unknown) => <StatusBadge status={v as IngestJobStatus} />,
    },
    {
      header: 'Registros',
      accessorKey: 'total_records' as keyof IngestJob,
      cell: (_: unknown, row: IngestJob) => (
        <span className="text-xs">
          {row.processed_records ?? 0}/{row.total_records ?? '?'}
          {(row.failed_records ?? 0) > 0 && (
            <span className="ml-1 text-red-500">({row.failed_records} falhas)</span>
          )}
        </span>
      ),
    },
    {
      header: 'Criado em',
      accessorKey: 'created_at' as keyof IngestJob,
      cell: (v: unknown) => (
        <span className="text-xs text-gray-500">
          {new Date(String(v)).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' })}
        </span>
      ),
    },
  ], []);

  return (
    <div className="space-y-6 p-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Activity className="h-5 w-5 text-brand" />
          <h1 className="text-xl font-semibold text-gray-900 dark:text-white">Monitor de Ingestão</h1>
        </div>
        <button
          onClick={() => refetch()}
          aria-label="Atualizar jobs de ingestão"
          className="flex items-center gap-1.5 rounded-lg border border-gray-200 px-3 py-1.5 text-sm hover:bg-gray-50 dark:border-gray-700"
        >
          <RefreshCw size={14} />
          Atualizar
        </button>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-3 rounded-xl border border-gray-200 bg-white p-4 dark:border-gray-700 dark:bg-gray-900">
        <select
          aria-label="Status do filtro de ingest jobs"
          value={statusFilter}
          onChange={(e) => { setStatusFilter(e.target.value); setOffset(0); }}
          className="rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-800 dark:text-white"
        >
          <option value="">Todos os status</option>
          {(['QUEUED', 'PROCESSING', 'DONE', 'PARTIAL', 'FAILED'] as IngestJobStatus[]).map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>

        <input
          type="text"
          aria-label="Source system do filtro de ingest jobs"
          placeholder="Source system…"
          value={sourceSystem}
          onChange={(e) => { setSourceSystem(e.target.value); setOffset(0); }}
          className="rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-800 dark:text-white"
        />

        <input
          type="date"
          aria-label="Data inicial do filtro de ingest jobs"
          value={dateFrom}
          onChange={(e) => { setDateFrom(e.target.value); setOffset(0); }}
          className="rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-800 dark:text-white"
        />
        <span className="flex items-center text-sm text-gray-400">até</span>
        <input
          type="date"
          aria-label="Data final do filtro de ingest jobs"
          value={dateTo}
          onChange={(e) => { setDateTo(e.target.value); setOffset(0); }}
          className="rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-800 dark:text-white"
        />
      </div>

      <section className="grid gap-3 md:grid-cols-4">
        <div className="rounded-xl border border-gray-200 bg-white p-4 dark:border-gray-700 dark:bg-gray-900">
          <p className="text-xs uppercase tracking-wide text-gray-400">Jobs na página</p>
          <p className="mt-1 text-2xl font-semibold text-gray-900 dark:text-white">{summary.total}</p>
        </div>
        <div className="rounded-xl border border-gray-200 bg-white p-4 dark:border-gray-700 dark:bg-gray-900">
          <p className="text-xs uppercase tracking-wide text-gray-400">Eventos processados</p>
          <p className="mt-1 text-2xl font-semibold text-emerald-600">{summary.processed}</p>
        </div>
        <div className="rounded-xl border border-gray-200 bg-white p-4 dark:border-gray-700 dark:bg-gray-900">
          <p className="text-xs uppercase tracking-wide text-gray-400">Falhas</p>
          <p className="mt-1 text-2xl font-semibold text-red-600">{summary.failed}</p>
        </div>
        <div className="rounded-xl border border-gray-200 bg-white p-4 dark:border-gray-700 dark:bg-gray-900">
          <p className="text-xs uppercase tracking-wide text-gray-400">Bytes processados</p>
          <p className="mt-1 text-2xl font-semibold text-gray-900 dark:text-white">
            {(summary.bytes / 1024).toFixed(1)} KB
          </p>
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
        {ingestStream.error && (
          <span className="text-amber-600">{ingestStream.error}</span>
        )}
      </section>

      {/* Table */}
      <DataTable<IngestJob>
        data={jobs}
        columns={columns}
        loading={isLoading}
        onRowClick={(row) => { setDrawerJob(row); setShowReprocessForm(false); setReprocessReason(''); }}
        caption="Lista de jobs de ingestão"
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
          {offset + 1}–{offset + jobs.length}
        </span>
        <button
          disabled={jobs.length < PAGE_SIZE}
          onClick={() => setOffset((o) => o + PAGE_SIZE)}
          className="rounded-lg border border-gray-200 px-3 py-1.5 text-xs disabled:opacity-40 dark:border-gray-700"
        >
          Próxima →
        </button>
      </div>

      {/* Detail Drawer */}
      {drawerJob && (
        <>
          <div
            className="fixed inset-0 z-30 bg-black/20"
            onClick={() => { setDrawerJob(null); setShowReprocessForm(false); }}
          />
          <aside className="fixed inset-y-0 right-0 z-40 flex w-[480px] flex-col bg-white shadow-2xl dark:bg-gray-900">
            {/* Drawer Header */}
            <div className="flex items-start justify-between border-b border-gray-100 p-4 dark:border-gray-700">
              <div>
                <h2 className="text-base font-semibold text-gray-900 dark:text-white">Detalhes do Job</h2>
                <p className="font-mono text-xs text-gray-400">{drawerJob.id}</p>
              </div>
              <button onClick={() => setDrawerJob(null)}>
                <X size={18} className="text-gray-300 hover:text-gray-500" />
              </button>
            </div>

            {/* Drawer Body */}
            <div className="flex-1 space-y-4 overflow-y-auto p-4">
              {detailLoading ? (
                <div className="h-48 animate-pulse rounded-xl bg-gray-100 dark:bg-gray-800" />
              ) : jobDetail ? (
                <>
                  <dl className="grid grid-cols-2 gap-3 text-sm">
                    <div>
                      <dt className="text-xs text-gray-400">Source System</dt>
                      <dd className="font-medium">{jobDetail.source_system}</dd>
                    </div>
                    <div>
                      <dt className="text-xs text-gray-400">Status</dt>
                      <dd><StatusBadge status={jobDetail.status} /></dd>
                    </div>
                    <div className="col-span-2">
                      <dt className="text-xs text-gray-400">Arquivo</dt>
                      <dd className="truncate text-xs">{jobDetail.file_name ?? '—'}</dd>
                    </div>
                    <div>
                      <dt className="text-xs text-gray-400">Conector</dt>
                      <dd>{jobDetail.connector_type ?? '—'}</dd>
                    </div>
                    <div>
                      <dt className="text-xs text-gray-400">Tamanho</dt>
                      <dd>{jobDetail.file_size_bytes != null ? `${(jobDetail.file_size_bytes / 1024).toFixed(1)} KB` : '—'}</dd>
                    </div>
                    <div>
                      <dt className="text-xs text-gray-400">Duração</dt>
                      <dd>{jobDetail.duration_ms != null ? `${jobDetail.duration_ms}ms` : '—'}</dd>
                    </div>
                    <div>
                      <dt className="text-xs text-gray-400">Registros</dt>
                      <dd>{jobDetail.processed_records ?? 0}/{jobDetail.total_records ?? '?'}</dd>
                    </div>
                    <div>
                      <dt className="text-xs text-gray-400">Falhas</dt>
                      <dd className={jobDetail.failed_records ? 'text-red-600' : ''}>{jobDetail.failed_records ?? 0}</dd>
                    </div>
                    <div>
                      <dt className="text-xs text-gray-400">Erros na fila</dt>
                      <dd>{jobDetail.error_count}</dd>
                    </div>
                    <div className="col-span-2">
                      <dt className="text-xs text-gray-400">Bronze path</dt>
                      <dd className="truncate font-mono text-[11px] text-gray-500">{jobDetail.file_path ?? '—'}</dd>
                    </div>
                    <div className="col-span-2">
                      <dt className="text-xs text-gray-400">Mensagem operacional</dt>
                      <dd className="text-xs text-gray-600 dark:text-gray-300">{jobDetail.error_message ?? '—'}</dd>
                    </div>
                  </dl>

                  {jobDetail.error_sample_preview && jobDetail.error_sample_preview.length > 0 && (
                    <div>
                      <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-400">
                        Resumo bruto do job
                      </p>
                      <pre className="max-h-32 overflow-auto rounded-lg bg-gray-950 p-3 text-[11px] text-sky-200">
                        {JSON.stringify(jobDetail.error_sample_preview, null, 2)}
                      </pre>
                    </div>
                  )}

                  {/* Error samples */}
                  {jobDetail.error_sample.length > 0 && (
                    <div>
                      <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-400">
                        Amostra de erros ({jobDetail.error_sample.length})
                      </p>
                      <div className="space-y-2">
                        {jobDetail.error_sample.map((e) => (
                          <div key={e.id} className="rounded-lg border border-red-100 bg-red-50 p-2.5">
                            <p className="text-xs font-medium text-red-700">
                              {e.line_number != null ? `Linha ${e.line_number}: ` : ''}{e.error_reason}
                            </p>
                            <pre className="mt-1 max-h-20 overflow-auto rounded bg-gray-950 p-1.5 text-[10px] text-green-200">
                              {e.raw_payload.slice(0, 300)}{e.raw_payload.length > 300 ? '…' : ''}
                            </pre>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {jobDetail.reprocessed_from && (
                    <p className="text-xs text-gray-400">
                      Reprocessado de: <span className="font-mono">{jobDetail.reprocessed_from.slice(0, 8)}…</span>
                    </p>
                  )}
                </>
              ) : null}
            </div>

            {/* Reprocess footer */}
            {jobDetail && ['FAILED', 'PARTIAL', 'DONE'].includes(jobDetail.status) && jobDetail.file_path && (
              <div className="space-y-3 border-t border-gray-100 p-4 dark:border-gray-700">
                {!showReprocessForm ? (
                  <button
                    onClick={() => setShowReprocessForm(true)}
                    aria-label="Reprocessar job de ingestão"
                    className="w-full rounded-lg bg-amber-500 py-2 text-sm font-medium text-white hover:bg-amber-600"
                  >
                    Reprocessar Job
                  </button>
                ) : (
                  <>
                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">
                      Motivo do reprocessamento
                    </label>
                    <input
                      aria-label="Motivo do reprocessamento do job"
                      value={reprocessReason}
                      onChange={(e) => setReprocessReason(e.target.value)}
                      placeholder="Ex.: mapping atualizado, payload corrigido"
                      className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-800 dark:text-white"
                    />
                    <div className="grid gap-2">
                      <div>
                        <label className="mb-1 block text-xs font-medium text-gray-500 dark:text-gray-400">
                          MappingConfig
                        </label>
                        <select
                          aria-label="MappingConfig do reprocessamento do job"
                          value={effectiveMappingId}
                          onChange={(e) => {
                            setSelectedMappingId(e.target.value);
                            setSelectedMappingVersionId('');
                          }}
                          className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-800 dark:text-white"
                        >
                          <option value="">Usar mapping atual do job</option>
                          {availableMappings.map((mapping: MappingListItem) => (
                            <option key={mapping.id} value={mapping.id}>
                              {mapping.name} · v{mapping.version_number}{mapping.is_current ? ' atual' : ''}
                            </option>
                          ))}
                        </select>
                      </div>
                      <div>
                        <label className="mb-1 block text-xs font-medium text-gray-500 dark:text-gray-400">
                          Versão específica
                        </label>
                        <select
                          aria-label="Versão do mapping no reprocessamento do job"
                          value={selectedMappingVersionId || jobDetail.mapping_version_id || ''}
                          onChange={(e) => setSelectedMappingVersionId(e.target.value)}
                          className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-800 dark:text-white"
                        >
                          <option value="">Usar atual / padrão</option>
                          {mappingVersions.map((version: MappingVersion) => (
                            <option key={version.id} value={version.id}>
                              v{version.version_number}{version.is_current ? ' atual' : ''} · {version.change_notes || 'sem notas'}
                            </option>
                          ))}
                        </select>
                      </div>
                    </div>
                    <div className="flex gap-2">
                      <button
                        aria-label="Confirmar reprocessamento do job"
                        onClick={() => reprocessMutation.mutate({
                          id: jobDetail.id,
                          reason: reprocessReason,
                          mappingVersionId: selectedMappingVersionId || undefined,
                        })}
                        disabled={!reprocessReason || reprocessMutation.isPending}
                        className="flex-1 rounded-lg bg-amber-500 py-2 text-sm font-medium text-white hover:bg-amber-600 disabled:opacity-50"
                      >
                        {reprocessMutation.isPending ? 'Enviando…' : 'Confirmar'}
                      </button>
                      <button
                        onClick={() => setShowReprocessForm(false)}
                        className="rounded-lg border border-gray-200 px-4 py-2 text-sm text-gray-500 hover:bg-gray-50 dark:border-gray-700"
                      >
                        Cancelar
                      </button>
                    </div>
                  </>
                )}
              </div>
            )}
          </aside>
        </>
      )}
    </div>
  );
}
