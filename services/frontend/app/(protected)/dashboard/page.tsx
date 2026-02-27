'use client';
import { useQuery } from '@tanstack/react-query';
import { fetchAlerts, fetchCases } from '@/lib/api';
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
} from 'recharts';

const SEV_COLOR: Record<string, string> = {
  CRITICAL: '#ef4444',
  HIGH:     '#f97316',
  MEDIUM:   '#eab308',
  LOW:      '#22c55e',
};

export default function DashboardPage() {
  const { data: alerts = [] } = useQuery({
    queryKey: ['alerts'],
    queryFn:  () => fetchAlerts({ status: 'OPEN', per_page: '100' }),
  });
  const { data: cases = [] } = useQuery({
    queryKey: ['cases'],
    queryFn:  () => fetchCases({ per_page: '100' }),
  });

  const bySev = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'].map((s) => ({
    name:  s,
    total: alerts.filter((a) => a.severity === s).length,
  }));

  const openCases   = cases.filter((c) => c.status === 'OPEN').length;
  const openAlerts  = alerts.filter((a) => a.status === 'OPEN').length;
  const critAlerts  = alerts.filter((a) => a.severity === 'CRITICAL').length;

  const KpiCard = ({ title, value, sub }: { title: string; value: number; sub?: string }) => (
    <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
      <p className="text-xs font-medium uppercase text-gray-500">{title}</p>
      <p className="mt-1 text-4xl font-bold text-gray-900">{value}</p>
      {sub && <p className="mt-1 text-xs text-gray-400">{sub}</p>}
    </div>
  );

  return (
    <div>
      <h1 className="mb-6 text-2xl font-bold">Dashboard</h1>

      <div className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-3">
        <KpiCard title="Alertas Abertos" value={openAlerts} />
        <KpiCard title="Críticos" value={critAlerts} sub="requerem ação imediata" />
        <KpiCard title="Casos Abertos" value={openCases} />
      </div>

      <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
        <h2 className="mb-4 text-sm font-semibold text-gray-700">Alertas por Severidade</h2>
        <ResponsiveContainer width="100%" height={220}>
          <BarChart data={bySev} barCategoryGap="35%">
            <XAxis dataKey="name" tick={{ fontSize: 12 }} />
            <YAxis tick={{ fontSize: 12 }} />
            <Tooltip />
            <Bar
              dataKey="total"
              radius={[4, 4, 0, 0]}
              fill="#2563eb"
              label={{ position: 'top', fontSize: 12 }}
            />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
