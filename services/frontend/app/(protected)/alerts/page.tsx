'use client';
import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useRouter } from 'next/navigation';
import { fetchAlerts, triageAlert, Alert } from '@/lib/api';
import DataTable from '@/components/DataTable';

const SEV_BADGE: Record<string, string> = {
  CRITICAL: 'bg-red-100 text-red-700',
  HIGH:     'bg-orange-100 text-orange-700',
  MEDIUM:   'bg-yellow-100 text-yellow-700',
  LOW:      'bg-green-100 text-green-700',
};

export default function AlertsPage() {
  const router = useRouter();
  const qc = useQueryClient();
  const [selected, setSelected] = useState<Alert | null>(null);
  const [filter, setFilter]     = useState<string>('OPEN');
  const [note, setNote]         = useState('');
  const [disposition, setDisp]  = useState('');

  const { data, isLoading } = useQuery({
    queryKey: ['alerts', filter],
    queryFn:  () => fetchAlerts(filter ? { status: filter } : undefined),
  });
  const alerts = data?.items ?? [];

  const triage = useMutation({
    mutationFn: () => triageAlert(selected!.id, disposition, note),
    onSuccess:  () => { qc.invalidateQueries({ queryKey: ['alerts'] }); setSelected(null); },
  });

  const columns = [
    { header: 'Título',     accessorKey: 'title' as keyof Alert },
    {
      header: 'Severidade',
      accessorKey: 'severity' as keyof Alert,
      cell: (v: unknown) => {
        const s = v as string;
        return <span className={`rounded px-2 py-0.5 text-xs font-semibold ${SEV_BADGE[s] ?? 'bg-gray-100'}`}>{s}</span>;
      },
    },
    { header: 'Tipo',    accessorKey: 'alert_type' as keyof Alert },
    { header: 'Status',  accessorKey: 'status' as keyof Alert },
    {
      header: 'Criado em',
      accessorKey: 'created_at' as keyof Alert,
      cell: (v: unknown) => new Date(v as string).toLocaleString('pt-BR'),
    },
    {
      header: 'Ações',
      accessorKey: 'id' as keyof Alert,
      cell: (v: unknown, row: Alert) => (
        <div className="flex gap-2">
          <button
            onClick={(e) => { e.stopPropagation(); router.push(`/alerts/${row.id}`); }}
            className="text-xs text-blue-600 hover:underline"
          >
            Ver detalhes
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); setSelected(row); }}
            className="text-xs text-indigo-600 hover:underline"
          >
            Triagem
          </button>
        </div>
      ),
    },
  ];

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-2xl font-bold">Alertas</h1>
        <div className="flex items-center gap-3">
          <span className="text-sm text-gray-500">{data?.total ?? 0} total</span>
          <select
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="rounded-lg border px-3 py-1.5 text-sm"
          >
            <option value="">Todos</option>
            <option value="OPEN">Abertos</option>
            <option value="IN_REVIEW">Em Revisão</option>
            <option value="CLOSED">Fechados</option>
          </select>
        </div>
      </div>

      <DataTable
        data={alerts}
        columns={columns}
        loading={isLoading}
        onRowClick={(row) => router.push(`/alerts/${row.id}`)}
      />

      {/* Triagem modal */}
      {selected && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-xl">
            <h2 className="mb-1 text-lg font-semibold">Triagem: {selected.title}</h2>
            <p className="mb-4 text-xs text-gray-500">Alerta #{selected.id.slice(0, 8)}</p>

            <label className="mb-1 block text-sm font-medium">Disposição</label>
            <select
              value={disposition}
              onChange={(e) => setDisp(e.target.value)}
              className="mb-3 w-full rounded-lg border px-3 py-2 text-sm"
            >
              <option value="">Selecione...</option>
              <option value="FALSE_POSITIVE">False Positive</option>
              <option value="TRUE_POSITIVE">True Positive</option>
              <option value="UNDER_REVIEW">Em Análise</option>
            </select>

            <label className="mb-1 block text-sm font-medium">Observação</label>
            <textarea
              rows={3}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              className="mb-4 w-full rounded-lg border px-3 py-2 text-sm"
              placeholder="Justificativa da triagem..."
            />

            <div className="flex gap-3">
              <button
                onClick={() => triage.mutate()}
                disabled={!disposition || triage.isPending}
                className="flex-1 rounded-lg bg-brand py-2 text-sm text-white disabled:opacity-50"
              >
                {triage.isPending ? 'Salvando...' : 'Confirmar'}
              </button>
              <button
                onClick={() => setSelected(null)}
                className="flex-1 rounded-lg border py-2 text-sm"
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
