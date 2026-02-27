'use client';
import { useQuery } from '@tanstack/react-query';
import { fetchCases, Case } from '@/lib/api';
import DataTable from '@/components/DataTable';
import { useRouter } from 'next/navigation';

const STATUS_BADGE: Record<string, string> = {
  OPEN:       'bg-blue-100 text-blue-700',
  UNDER_REVIEW:'bg-purple-100 text-purple-700',
  CLOSED:     'bg-gray-100 text-gray-600',
  ARCHIVED:   'bg-gray-50 text-gray-400',
};

export default function CasesPage() {
  const router = useRouter();
  const { data: cases = [], isLoading } = useQuery({
    queryKey: ['cases'],
    queryFn:  fetchCases,
  });

  const columns = [
    { header: 'Ref',       accessorKey: 'reference_number' as keyof Case },
    { header: 'Título',    accessorKey: 'title' as keyof Case },
    { header: 'Prioridade', accessorKey: 'priority' as keyof Case },
    {
      header: 'Status',
      accessorKey: 'status' as keyof Case,
      cell: (v: string) => (
        <span className={`rounded px-2 py-0.5 text-xs font-semibold ${STATUS_BADGE[v] ?? 'bg-gray-100'}`}>
          {v}
        </span>
      ),
    },
    {
      header: 'Criado em',
      accessorKey: 'created_at' as keyof Case,
      cell: (v: string) => new Date(v).toLocaleString('pt-BR'),
    },
  ];

  return (
    <div>
      <h1 className="mb-6 text-2xl font-bold">Casos</h1>
      <DataTable
        data={cases}
        columns={columns}
        loading={isLoading}
        onRowClick={(c) => router.push(`/cases/${c.id}`)}
      />
    </div>
  );
}
