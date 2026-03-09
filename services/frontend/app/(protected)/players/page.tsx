'use client';
import { useQuery } from '@tanstack/react-query';
import { fetchPlayers, Player } from '@/lib/api';
import DataTable from '@/components/DataTable';
import { useRouter } from 'next/navigation';

const BAND_COLOR: Record<string, string> = {
  HIGH:   'bg-red-100 text-red-700',
  MEDIUM: 'bg-yellow-100 text-yellow-700',
  LOW:    'bg-green-100 text-green-700',
};

export default function PlayersPage() {
  const router = useRouter();
  const { data: players = [], isLoading } = useQuery({
    queryKey: ['players'],
    queryFn:  () => fetchPlayers(),
  });

  const columns = [
    { header: 'ID Externo',  accessorKey: 'external_player_id' as keyof Player },
    {
      header: 'PEP',
      accessorKey: 'pep_flag' as keyof Player,
      cell: (v: unknown) => (v as boolean)
        ? <span className="rounded px-2 py-0.5 text-xs font-semibold bg-red-100 text-red-700">SIM</span>
        : <span className="rounded px-2 py-0.5 text-xs font-semibold bg-gray-100 text-gray-600">NÃO</span>,
    },
    {
      header: 'Score de Risco',
      accessorKey: 'risk_score' as keyof Player,
      cell: (v: unknown) => {
        const n = v as number | undefined;
        if (n == null) return '—';
        const color = n >= 0.7 ? 'text-red-600' : n >= 0.35 ? 'text-yellow-600' : 'text-green-600';
        return <span className={`font-semibold ${color}`}>{(n * 100).toFixed(0)}%</span>;
      },
    },
    {
      header: 'Banda',
      accessorKey: 'risk_band' as keyof Player,
      cell: (v: unknown) => {
        const band = (v as string) ?? 'LOW';
        return (
          <span className={`rounded px-2 py-0.5 text-xs font-bold ${BAND_COLOR[band] ?? 'bg-gray-100 text-gray-600'}`}>
            {band}
          </span>
        );
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
