'use client';
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchAuditLogs, type AuditLog } from '@/lib/api';
import DataTable from '@/components/DataTable';

export default function AuditLogsPage() {
  const [page, setPage]           = useState(1);
  const [entity, setEntity]       = useState('');
  const [actor, setActor]         = useState('');

  const { data: logs = [], isLoading } = useQuery({
    queryKey: ['audit-logs', page, entity, actor],
    queryFn: () => {
      const params: Record<string, string> = { page: String(page), per_page: '50' };
      if (entity) params.entity_type = entity;
      if (actor)  params.user_id = actor;
      return fetchAuditLogs(params);
    },
  });

  const normalizedLogs = logs.filter((item) => Boolean(item.id));

  const columns = [
    {
      header: 'Ação',
      accessorKey: 'action' as keyof AuditLog,
      cell: (v: unknown) => (
        <span className="rounded bg-blue-50 px-2 py-0.5 text-xs font-mono text-blue-700">{v as string}</span>
      ),
    },
    { header: 'Entidade',   accessorKey: 'entity_type' as keyof AuditLog },
    { header: 'Entity ID',  accessorKey: 'entity_id' as keyof AuditLog,
      cell: (v: unknown) => <span className="font-mono text-xs">{((v as string) ?? '').slice(0, 12)}…</span> },
    { header: 'Ator',       accessorKey: 'user_id' as keyof AuditLog,
      cell: (_: unknown, row?: AuditLog) => {
        const value = row?.user_id ?? row?.actor_id ?? '';
        return <span className="font-mono text-xs">{String(value).slice(0, 12)}{value ? '…' : ''}</span>;
      } },
    { header: 'IP',         accessorKey: 'ip_address' as keyof AuditLog },
    { header: 'PII Acessado', accessorKey: 'pii_accessed' as keyof AuditLog,
      cell: (v: unknown) => v
        ? <span className="rounded bg-orange-50 px-1.5 py-0.5 text-xs font-mono text-orange-700">{v as string}</span>
        : null },
    {
      header: 'Data',
      accessorKey: 'created_at' as keyof AuditLog,
      cell: (v: unknown) => new Date(v as string).toLocaleString('pt-BR'),
    },
  ];

  return (
    <div>
      <h1 className="mb-4 text-2xl font-bold">Logs de Auditoria</h1>

      {/* Filtros */}
      <div className="mb-4 flex gap-3">
        <input
          type="text"
          placeholder="Filtrar por entidade (RULE, ALERT, CASE...)"
          value={entity}
          onChange={(e) => { setEntity(e.target.value); setPage(1); }}
          className="rounded-lg border px-3 py-1.5 text-sm w-64"
        />
        <input
          type="text"
          placeholder="Filtrar por ator (user ID)"
          value={actor}
          onChange={(e) => { setActor(e.target.value); setPage(1); }}
          className="rounded-lg border px-3 py-1.5 text-sm w-64"
        />
      </div>

      <DataTable data={normalizedLogs} columns={columns} loading={isLoading} />

      {/* Paginação */}
      <div className="mt-4 flex items-center gap-4">
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
          disabled={normalizedLogs.length < 50}
          className="rounded-lg border px-4 py-1.5 text-sm disabled:opacity-40"
        >
          Próxima →
        </button>
      </div>
    </div>
  );
}
