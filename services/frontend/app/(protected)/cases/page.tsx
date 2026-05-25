'use client';
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchCases, Case } from '@/lib/api';
import { useRouter } from 'next/navigation';
import {
  FolderOpen, Clock, AlertTriangle, ChevronRight,
  Filter, Search, Plus,
} from 'lucide-react';

const STATUS_CONFIG: Record<string, { label: string; cls: string }> = {
  OPEN:            { label: 'Aberto',          cls: 'bg-blue-100 text-blue-700' },
  INVESTIGATING:   { label: 'Investigando',    cls: 'bg-indigo-100 text-indigo-700' },
  PENDING_REVIEW:  { label: 'Aguarda revisão', cls: 'bg-purple-100 text-purple-700' },
  IN_REVIEW:       { label: 'Em revisão',      cls: 'bg-purple-100 text-purple-700' },   // legacy
  UNDER_REVIEW:    { label: 'Em revisão',      cls: 'bg-purple-100 text-purple-700' },   // legacy
  CLOSED:          { label: 'Encerrado',       cls: 'bg-gray-100 text-gray-600' },
  REPORTED:        { label: 'Reportado',       cls: 'bg-green-100 text-green-600' },
  ARCHIVED:        { label: 'Arquivado',       cls: 'bg-gray-50 text-gray-400' },
};

const PRIORITY_CONFIG: Record<string, { label: string; cls: string }> = {
  HIGH:   { label: 'Alta',  cls: 'text-red-600' },
  MEDIUM: { label: 'Média', cls: 'text-orange-500' },
  LOW:    { label: 'Baixa', cls: 'text-green-600' },
};

function SLABadge({ sla_due_at }: { sla_due_at?: string }) {
  if (!sla_due_at) return null;
  const diff = new Date(sla_due_at).getTime() - Date.now();
  if (diff < 0) {
    return <span className="rounded bg-red-100 px-1.5 py-0.5 text-[10px] font-bold text-red-700">SLA VENCIDO</span>;
  }
  const mins  = Math.round(diff / 60000);
  const label = mins < 60 ? `${mins}min` : `${Math.round(mins / 60)}h`;
  const cls   = diff < 2 * 3600000 ? 'bg-orange-100 text-orange-700' : 'bg-green-50 text-green-700';
  return <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${cls}`}>{label} p/ vencer</span>;
}

function CaseRow({ c, onClick }: { c: Case; onClick: () => void }) {
  const status   = STATUS_CONFIG[c.status]   ?? { label: c.status,   cls: 'bg-gray-100 text-gray-600' };
  const priority = PRIORITY_CONFIG[c.priority] ?? { label: c.priority, cls: 'text-gray-600' };
  const isUrgent = !!c.sla_due_at && (new Date(c.sla_due_at).getTime() - Date.now()) < 2 * 3600000;
  const caseReference = c.reference_number || `CASE-${c.id.slice(0, 8).toUpperCase()}`;

  return (
    <div
      onClick={onClick}
      data-testid="case-row"
      className={`flex cursor-pointer items-center gap-4 rounded-xl border bg-white px-5 py-3.5 shadow-sm transition-all hover:shadow-md hover:border-brand/30 ${
        isUrgent ? 'border-orange-200' : 'border-gray-200'
      }`}
    >
      <FolderOpen size={16} className={isUrgent ? 'text-orange-400' : 'text-gray-400'} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <p className="text-sm font-semibold text-gray-900 truncate">{c.title}</p>
          {c.auto_created && (
            <span className="rounded bg-indigo-50 px-1.5 py-0.5 text-[10px] font-semibold text-indigo-600">AUTO</span>
          )}
        </div>
        <p className="mt-0.5 text-[10px] font-mono text-gray-400">
          {caseReference} ·{' '}
          {new Date(c.created_at).toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit', year: '2-digit' })}
        </p>
      </div>

      <div className="flex shrink-0 items-center gap-3">
        <SLABadge sla_due_at={c.sla_due_at} />
        <span className={`text-xs font-semibold ${priority.cls}`}>{priority.label}</span>
        <span className={`rounded px-2 py-0.5 text-xs font-medium ${status.cls}`}>{status.label}</span>
        <ChevronRight size={14} className="text-gray-300" />
      </div>
    </div>
  );
}

export default function CasesPage() {
  const router = useRouter();
  const [search, setSearch]       = useState('');
  const [statusFilter, setStatus] = useState('active');

  const { data: cases = [], isLoading } = useQuery({
    queryKey: ['cases', statusFilter],
    queryFn: () => fetchCases(
      statusFilter === 'active'
        ? { limit: 200 }
        : statusFilter === 'closed'
        ? { status_filter: 'CLOSED', limit: 200 }
        : { limit: 500 },
    ),
  });

  const filtered = cases.filter((c) => {
    const matchStatus =
      statusFilter === 'active'
        ? ['OPEN', 'INVESTIGATING', 'PENDING_REVIEW', 'IN_REVIEW', 'UNDER_REVIEW'].includes(c.status)
        : statusFilter === 'closed'
        ? ['CLOSED', 'REPORTED', 'ARCHIVED'].includes(c.status)
        : true;
    const matchSearch =
      !search ||
      c.title.toLowerCase().includes(search.toLowerCase()) ||
      c.reference_number?.includes(search);
    return matchStatus && matchSearch;
  });

  const priorityOrder: Record<string, number> = { HIGH: 0, MEDIUM: 1, LOW: 2 };
  const sorted = [...filtered].sort((a, b) => {
    const aBreached = !!a.sla_due_at && new Date(a.sla_due_at) < new Date();
    const bBreached = !!b.sla_due_at && new Date(b.sla_due_at) < new Date();
    if (aBreached && !bBreached) return -1;
    if (!aBreached && bBreached) return 1;
    return (priorityOrder[a.priority] ?? 3) - (priorityOrder[b.priority] ?? 3);
  });

  const slaBreachCount = filtered.filter(
    (c) => !!c.sla_due_at && new Date(c.sla_due_at) < new Date(),
  ).length;

  return (
    <div className="space-y-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Casos em Investigação</h1>
          <p className="mt-0.5 text-sm text-gray-500">
            Gerencie as investigações em andamento e acompanhe os prazos.
          </p>
        </div>
        <button
          onClick={() => router.push('/cases/new')}
          className="flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90 shadow-sm"
        >
          <Plus size={15} /> Novo caso
        </button>
      </div>

      {slaBreachCount > 0 && (
        <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3">
          <p className="flex items-center gap-2 text-sm font-medium text-red-700">
            <AlertTriangle size={15} />
            {slaBreachCount} caso{slaBreachCount > 1 ? 's' : ''} com prazo regulatório vencido — requer atenção imediata.
          </p>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <div className="relative">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Buscar por título ou referência..."
            className="rounded-lg border border-gray-200 bg-white py-1.5 pl-8 pr-3 text-sm text-gray-700 focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand w-64 shadow-sm"
          />
        </div>
        <div className="flex items-center gap-1.5">
          <Filter size={13} className="text-gray-400" />
          {[
            { val: 'active', label: `Em andamento (${cases.filter((c) => ['OPEN', 'INVESTIGATING', 'PENDING_REVIEW', 'IN_REVIEW', 'UNDER_REVIEW'].includes(c.status)).length})` },
            { val: 'closed', label: 'Encerrados' },
            { val: 'all',    label: 'Todos' },
          ].map(({ val, label }) => (
            <button
              key={val}
              onClick={() => setStatus(val)}
              className={`rounded-lg px-3 py-1.5 text-xs font-semibold transition-colors ${
                statusFilter === val
                  ? 'bg-brand text-white'
                  : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {isLoading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-16 rounded-xl bg-gray-100 animate-pulse" />
          ))}
        </div>
      ) : sorted.length === 0 ? (
        <div className="rounded-xl border border-dashed border-gray-200 bg-gray-50 py-16 text-center">
          <FolderOpen size={32} className="mx-auto mb-3 text-gray-300" />
          <p className="text-sm font-medium text-gray-400">Nenhum caso encontrado</p>
          {search && <p className="mt-1 text-xs text-gray-400">Tente outra busca.</p>}
        </div>
      ) : (
        <div className="space-y-2.5">
          {sorted.map((c) => (
            <CaseRow key={c.id} c={c} onClick={() => router.push(`/cases/${c.id}`)} />
          ))}
        </div>
      )}

      <p className="text-center text-xs text-gray-400">
        {sorted.length} caso{sorted.length !== 1 ? 's' : ''} exibido{sorted.length !== 1 ? 's' : ''}
      </p>
    </div>
  );
}
