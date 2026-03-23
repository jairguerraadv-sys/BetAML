'use client';

import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchAuditLogs, type AuditLog, type AuditLogFilters } from '@/lib/api';
import DataTable from '@/components/DataTable';

const PAGE_SIZE = 50;

export default function AuditLogsPage() {
  const [page, setPage] = useState(1);
  const [entityType, setEntityType] = useState('');
  const [entityId, setEntityId] = useState('');
  const [action, setAction] = useState('');
  const [actor, setActor] = useState('');
  const [query, setQuery] = useState('');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [piiOnly, setPiiOnly] = useState(false);

  const params: AuditLogFilters = useMemo(() => {
    const next: AuditLogFilters = { page: String(page), per_page: String(PAGE_SIZE) };
    if (entityType) next.entity_type = entityType;
    if (entityId) next.entity_id = entityId;
    if (action) next.action = action;
    if (actor) next.user_id = actor;
    if (query) next.q = query;
    if (dateFrom) next.date_from = `${dateFrom}T00:00:00Z`;
    if (dateTo) next.date_to = `${dateTo}T23:59:59Z`;
    if (piiOnly) next.pii_only = 'true';
    return next;
  }, [action, actor, dateFrom, dateTo, entityId, entityType, page, piiOnly, query]);

  const { data: logs = [], isLoading } = useQuery({
    queryKey: ['audit-logs', params],
    queryFn: () => fetchAuditLogs(params),
  });

  const normalizedLogs = logs.filter((item) => Boolean(item.id));
  const piiTouches = normalizedLogs.filter((item) => Boolean(item.pii_accessed)).length;
  const distinctActions = new Set(normalizedLogs.map((item) => item.action)).size;
  const distinctActors = new Set(
    normalizedLogs
      .map((item) => item.user_id ?? item.actor_id)
      .filter((value): value is string => Boolean(value)),
  ).size;

  const columns = [
    {
      header: 'Ação',
      accessorKey: 'action' as keyof AuditLog,
      cell: (v: unknown) => (
        <span className="rounded bg-blue-50 px-2 py-0.5 text-xs font-mono text-blue-700">{v as string}</span>
      ),
    },
    {
      header: 'Entidade',
      accessorKey: 'entity_type' as keyof AuditLog,
      cell: (_: unknown, row?: AuditLog) => (
        <div className="space-y-0.5">
          <p className="text-sm font-medium text-gray-800">{row?.entity_type ?? '—'}</p>
          <p className="font-mono text-[11px] text-gray-400">{row?.entity_id ?? 'sem entity_id'}</p>
        </div>
      ),
    },
    {
      header: 'Ator',
      accessorKey: 'user_id' as keyof AuditLog,
      cell: (_: unknown, row?: AuditLog) => {
        const value = row?.user_id ?? row?.actor_id ?? '';
        return <span className="font-mono text-xs">{value || 'sistema'}</span>;
      },
    },
    {
      header: 'PII',
      accessorKey: 'pii_accessed' as keyof AuditLog,
      cell: (v: unknown) => v
        ? <span className="rounded bg-orange-50 px-1.5 py-0.5 text-xs font-mono text-orange-700">{v as string}</span>
        : <span className="text-xs text-gray-300">—</span>,
    },
    {
      header: 'Diff',
      accessorKey: 'after' as keyof AuditLog,
      cell: (_: unknown, row?: AuditLog) => {
        const before = row?.before ? Object.keys(row.before).length : 0;
        const after = row?.after ? Object.keys(row.after).length : 0;
        return <span className="text-xs text-gray-500">{before || after ? `${before}/${after} campos` : '—'}</span>;
      },
    },
    {
      header: 'Data',
      accessorKey: 'created_at' as keyof AuditLog,
      cell: (v: unknown) => new Date(v as string).toLocaleString('pt-BR'),
    },
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Logs de Auditoria</h1>
        <p className="mt-1 text-sm text-gray-500">
          Consulta centralizada de ações críticas, acessos a PII, exports e alterações administrativas.
        </p>
      </div>

      <section className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <SummaryCard label="Registros nesta página" value={String(normalizedLogs.length)} />
        <SummaryCard label="Ações distintas" value={String(distinctActions)} />
        <SummaryCard label="Atores distintos" value={String(distinctActors)} />
        <SummaryCard label="Toques em PII" value={String(piiTouches)} tone={piiTouches > 0 ? 'warning' : 'default'} />
      </section>

      <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <FilterInput label="Busca livre" placeholder="ação, entidade ou entity_id" value={query} onChange={setQuery} />
          <FilterInput label="Ação exata" placeholder="EXPORT_REPORT_JSON" value={action} onChange={setAction} />
          <FilterInput label="Entidade" placeholder="Case, Report, User..." value={entityType} onChange={setEntityType} />
          <FilterInput label="Entity ID" placeholder="UUID / chave" value={entityId} onChange={setEntityId} />
          <FilterInput label="Ator (user_id)" placeholder="UUID do usuário" value={actor} onChange={setActor} />
          <DateInput label="De" value={dateFrom} onChange={setDateFrom} />
          <DateInput label="Até" value={dateTo} onChange={setDateTo} />
          <label className="flex items-end gap-2 rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-700">
            <input
              type="checkbox"
              checked={piiOnly}
              onChange={(e) => { setPiiOnly(e.target.checked); setPage(1); }}
              className="mt-0.5"
            />
            Somente acessos com PII
          </label>
        </div>

        <div className="mt-3 flex flex-wrap gap-2">
          <button
            onClick={() => setPage(1)}
            className="rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand/90"
          >
            Aplicar filtros
          </button>
          <button
            onClick={() => {
              setPage(1);
              setEntityType('');
              setEntityId('');
              setAction('');
              setActor('');
              setQuery('');
              setDateFrom('');
              setDateTo('');
              setPiiOnly(false);
            }}
            className="rounded-lg border border-gray-200 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
          >
            Limpar
          </button>
        </div>
      </section>

      <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
        <DataTable data={normalizedLogs} columns={columns} loading={isLoading} />
      </section>

      <div className="flex items-center gap-4">
        <button
          onClick={() => setPage((p) => Math.max(1, p - 1))}
          disabled={page === 1}
          className="rounded-lg border px-4 py-1.5 text-sm disabled:opacity-40"
        >
          ← Anterior
        </button>
        <span className="text-sm text-gray-500">Página {page}</span>
        <button
          onClick={() => setPage((p) => p + 1)}
          disabled={normalizedLogs.length < PAGE_SIZE}
          className="rounded-lg border px-4 py-1.5 text-sm disabled:opacity-40"
        >
          Próxima →
        </button>
      </div>
    </div>
  );
}

function SummaryCard({ label, value, tone = 'default' }: { label: string; value: string; tone?: 'default' | 'warning' }) {
  return (
    <div className={`rounded-xl border p-4 shadow-sm ${tone === 'warning' ? 'border-orange-200 bg-orange-50' : 'border-gray-200 bg-white'}`}>
      <p className="text-xs text-gray-500">{label}</p>
      <p className="mt-1 text-2xl font-bold text-gray-900">{value}</p>
    </div>
  );
}

function FilterInput({
  label,
  placeholder,
  value,
  onChange,
}: {
  label: string;
  placeholder: string;
  value: string;
  onChange: (next: string) => void;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-gray-600">{label}</span>
      <input
        type="text"
        placeholder={placeholder}
        aria-label={label}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-lg border px-3 py-2 text-sm"
      />
    </label>
  );
}

function DateInput({ label, value, onChange }: { label: string; value: string; onChange: (next: string) => void }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-gray-600">{label}</span>
      <input
        type="date"
        aria-label={label}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-lg border px-3 py-2 text-sm"
      />
    </label>
  );
}
