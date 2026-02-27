'use client';
import { useState } from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { FileBarChart2, Download, Plus } from 'lucide-react';

interface MonthlyReport {
  id: string;
  month: string;       // "2024-11"
  tenant_id: string;
  created_at: string;
  download_url?: string;
}

const fetchReports = () =>
  api.get<MonthlyReport[]>('/reports/monthly-summary').then((r) => r.data).catch(() => [] as MonthlyReport[]);

export default function ReportsPage() {
  const [month, setMonth] = useState(() => {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
  });
  const [generating, setGenerating] = useState(false);
  const [msg, setMsg]               = useState('');

  const { data: reports = [], isLoading, refetch } = useQuery({
    queryKey: ['reports'],
    queryFn: fetchReports,
  });

  const generate = useMutation({
    mutationFn: () => api.post('/reports/monthly-summary', { month }),
    onMutate:   () => { setGenerating(true); setMsg(''); },
    onSuccess:  () => { setMsg('Relatório gerado com sucesso!'); refetch(); },
    onError:    () => setMsg('Erro ao gerar relatório.'),
    onSettled:  () => setGenerating(false),
  });

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <FileBarChart2 size={22} className="text-brand" />
        <h1 className="text-2xl font-bold text-gray-900">Relatórios Mensais</h1>
      </div>

      {/* Generate */}
      <div className="flex items-end gap-3 rounded-xl border border-gray-100 bg-white p-4 shadow-sm">
        <div>
          <label className="mb-1 block text-xs font-medium text-gray-600">Mês</label>
          <input
            type="month"
            value={month}
            onChange={(e) => setMonth(e.target.value)}
            className="rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand"
          />
        </div>
        <button
          onClick={() => generate.mutate()}
          disabled={generating}
          className="flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand/90 disabled:opacity-50"
        >
          <Plus size={16} />
          {generating ? 'Gerando…' : 'Gerar Relatório'}
        </button>
        {msg && <p className="text-sm text-brand">{msg}</p>}
      </div>

      {/* List */}
      <div className="overflow-hidden rounded-xl border border-gray-100 bg-white shadow-sm">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-xs font-semibold uppercase text-gray-500">
            <tr>
              <th className="px-4 py-3 text-left">Mês</th>
              <th className="px-4 py-3 text-left">Gerado em</th>
              <th className="px-4 py-3 text-center">Download</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-50">
            {isLoading && (
              <tr><td colSpan={3} className="py-8 text-center text-gray-400">Carregando…</td></tr>
            )}
            {!isLoading && reports.length === 0 && (
              <tr><td colSpan={3} className="py-8 text-center text-gray-400">Nenhum relatório gerado</td></tr>
            )}
            {reports.map((r: MonthlyReport) => (
              <tr key={r.id} className="hover:bg-gray-50/50">
                <td className="px-4 py-3 font-medium">{r.month}</td>
                <td className="px-4 py-3 text-gray-500">{new Date(r.created_at).toLocaleString('pt-BR')}</td>
                <td className="px-4 py-3 text-center">
                  {r.download_url ? (
                    <a
                      href={r.download_url}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-center gap-1 rounded px-2 py-1 text-xs text-brand hover:underline"
                    >
                      <Download size={13} /> PDF
                    </a>
                  ) : (
                    <span className="text-gray-400">—</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
