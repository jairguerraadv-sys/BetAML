'use client';
import { useQuery } from '@tanstack/react-query';
import { fetchCase } from '@/lib/api';
import { useParams, useRouter } from 'next/navigation';

export default function CaseDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router  = useRouter();
  const { data, isLoading, error } = useQuery({
    queryKey: ['case', id],
    queryFn:  () => fetchCase(id),
    enabled:  !!id,
  });

  if (isLoading) return <p className="text-sm text-gray-400">Carregando...</p>;
  if (error)     return <p className="text-sm text-red-600">Erro ao carregar caso.</p>;
  if (!data)     return null;

  return (
    <div className="max-w-3xl">
      <button onClick={() => router.back()} className="mb-4 text-sm text-brand hover:underline">
        ← Voltar
      </button>

      <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
        <div className="mb-4 flex items-start justify-between">
          <div>
            <p className="text-xs font-mono text-gray-400">{data.reference_number}</p>
            <h1 className="text-xl font-bold">{data.title}</h1>
          </div>
          <span className="rounded-full border px-3 py-1 text-xs font-medium">{data.status}</span>
        </div>

        <dl className="grid grid-cols-2 gap-4 text-sm">
          <div>
            <dt className="text-gray-500">Prioridade</dt>
            <dd className="font-medium">{data.priority}</dd>
          </div>
          <div>
            <dt className="text-gray-500">Atribuído a</dt>
            <dd className="font-medium">{data.assigned_to ?? '—'}</dd>
          </div>
          <div>
            <dt className="text-gray-500">Criado em</dt>
            <dd className="font-medium">{new Date(data.created_at).toLocaleString('pt-BR')}</dd>
          </div>
        </dl>
      </div>

      {/* Timeline de eventos */}
      {Array.isArray(data.events) && data.events.length > 0 && (
        <div className="mt-6">
          <h2 className="mb-3 text-sm font-semibold text-gray-700">Histórico de Eventos</h2>
          <ul className="space-y-3">
            {(data.events as Array<Record<string, string>>).map((ev, i) => (
              <li key={i} className="rounded-lg border border-gray-100 bg-white px-4 py-3 text-xs">
                <span className="font-medium text-gray-800">{ev.event_type}</span>
                {ev.note && <span className="ml-2 text-gray-500">{ev.note}</span>}
                <span className="ml-auto float-right text-gray-400">
                  {ev.created_at ? new Date(ev.created_at).toLocaleString('pt-BR') : ''}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
