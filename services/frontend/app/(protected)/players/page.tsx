'use client';
import { useQuery } from '@tanstack/react-query';
import { fetchPlayers, Player } from '@/lib/api';
import DataTable from '@/components/DataTable';
import { useRouter } from 'next/navigation';

export default function PlayersPage() {
  const router = useRouter();
  const { data: players = [], isLoading } = useQuery({
    queryKey: ['players'],
    queryFn:  () => fetchPlayers(),
  });

  const columns = [
    { header: 'ID Externo',  accessorKey: 'external_id' as keyof Player },
    {
      header: 'Nome',
      accessorKey: 'full_name' as keyof Player,
      cell: (v: unknown) => (v as string) ?? '—',
    },
    {
      header: 'PEP',
      accessorKey: 'pep_flag' as keyof Player,
      cell: (v: unknown) => (v as boolean)
        ? <span className="rounded px-2 py-0.5 text-xs font-semibold bg-red-100 text-red-700">SIM</span>
        : <span className="rounded px-2 py-0.5 text-xs font-semibold bg-gray-100 text-gray-600">NÃO</span>,
    },
    { header: 'Status',    accessorKey: 'status' as keyof Player },
    {
      header: 'Score de Risco',
      accessorKey: 'risk_score' as keyof Player,
      cell: (v: unknown) => {
        const n = v as number | undefined;
        if (n == null) return '—';
        const color = n >= 0.7 ? 'text-red-600' : n >= 0.4 ? 'text-yellow-600' : 'text-green-600';
        return <span className={`font-semibold ${color}`}>{(n * 100).toFixed(0)}%</span>;
      },
    },
  ];

  return (
    <div>
      <h1 className="mb-6 text-2xl font-bold">Jogadores</h1>
      <DataTable
        data={players}
        columns={columns}
        loading={isLoading}
        onRowClick={(p) => router.push(`/players/${p.id}`)}
      />
    </div>
  );
}
