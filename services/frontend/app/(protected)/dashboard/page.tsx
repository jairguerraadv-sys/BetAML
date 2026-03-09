'use client';
import { useQuery } from '@tanstack/react-query';
import { fetchAlerts, fetchCases } from '@/lib/api';
import { useEffect, useState } from 'react';
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell,
} from 'recharts';
import Link from 'next/link';
import {
  AlertTriangle, FolderOpen, Clock, TrendingDown,
  Zap, CheckCircle2, ChevronRight,
} from 'lucide-react';

const SEV_COLOR: Record<string, string> = {
  CRITICAL: '#ef4444',
  HIGH:     '#f97316',
  MEDIUM:   '#eab308',
  LOW:      '#22c55e',
};

const SEV_LABEL: Record<string, string> = {
  CRITICAL: 'Crítico',
  HIGH:     'Alto',
  MEDIUM:   'Médio',
  LOW:      'Baixo',
};

function greeting() {
  const h = new Date().getHours();
  if (h < 12) return 'Bom dia';
  if (h < 18) return 'Boa tarde';
  return 'Boa noite';
}

function KpiCard({
  icon: Icon, title, value, sub, href, variant = 'default',
}: {
  icon: React.ElementType;
  title: string; value: number | string; sub?: string; href?: string;
  variant?: 'default' | 'red' | 'orange' | 'green';
}) {
  const colors = {
    default: 'border-gray-200 bg-white',
    red:     'border-red-200 bg-red-50',
    orange:  'border-orange-200 bg-orange-50',
    green:   'border-green-200 bg-green-50',
  };
  const textColors = {
    default: 'text-gray-900', red: 'text-red-700',
    orange:  'text-orange-600', green: 'text-green-700',
  };
  const iconColors = {
    default: 'text-brand', red: 'text-red-500',
    orange:  'text-orange-500', green: 'text-green-600',
  };
  const inner = (
    <div className={`rounded-xl border p-5 shadow-sm transition-shadow hover:shadow-md ${colors[variant]}`}>
      <div className="flex items-start justify-between">
        <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">{title}</p>
        <Icon size={18} className={iconColors[variant]} />
      </div>
      <p className={`mt-2 text-4xl font-bold ${textColors[variant]}`}>{value}</p>
      {sub && <p className="mt-1 text-xs text-gray-400">{sub}</p>}
      {href && (
        <p className="mt-3 flex items-center gap-1 text-xs font-medium text-brand">
          Ver todos <ChevronRight size={12} />
        </p>
      )}
    </div>
  );
  return href ? <Link href={href}>{inner}</Link> : inner;
}

export default function DashboardPage() {
  const [userName, setUserName] = useState('');

  useEffect(() => {
    try {
      const raw = localStorage.getItem('betaml_user');
      if (raw) setUserName(JSON.parse(raw)?.username ?? '');
    } catch {}
  }, []);

  const { data: alertsData } = useQuery({
    queryKey: ['alerts', 'dashboard'],
    queryFn:  () => fetchAlerts({ per_page: '500' }),
  });
  const alerts = alertsData?.items ?? [];

  const { data: cases = [] } = useQuery({
    queryKey: ['cases', 'dashboard'],
    queryFn:  () => fetchCases({ per_page: '500' }),
  });

  const today       = new Date(); today.setHours(0, 0, 0, 0);
  const alertsHoje  = alerts.filter((a) => new Date(a.created_at) >= today).length;
  const critAbertos = alerts.filter((a) => a.severity === 'CRITICAL' && a.status === 'OPEN').length;
  const now         = new Date();
  const casesUrgentes = cases.filter((c) => {
    const due = (c as Record<string, unknown>).sla_due_at as string | undefined;
    return due && new Date(due) < now && !['CLOSED', 'REPORTED', 'ARCHIVED'].includes(c.status);
  }).length;
  const casesAbertos  = cases.filter((c) => c.status === 'OPEN' || c.status === 'IN_REVIEW').length;
  const autoCriados   = cases.filter((c) => (c as Record<string, unknown>).auto_created === true).length;

  const bySev = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'].map((s) => ({
    name:  SEV_LABEL[s],
    total: alerts.filter((a) => a.status === 'OPEN' && a.severity === s).length,
    key:   s,
  }));

  const critRecentes = alerts
    .filter((a) => a.severity === 'CRITICAL' && a.status === 'OPEN')
    .slice(0, 5);

  const slaProximo = cases
    .filter((c) => {
      const due = (c as Record<string, unknown>).sla_due_at as string | undefined;
      if (!due) return false;
      const diff = new Date(due).getTime() - now.getTime();
      return diff > 0 && diff < 24 * 3600 * 1000 && !['CLOSED', 'REPORTED', 'ARCHIVED'].includes(c.status);
    })
    .slice(0, 4);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">
          {greeting()}{userName ? `, ${userName}` : ''}!
        </h1>
        <p className="mt-1 text-sm text-gray-500">
          Aqui está o que precisa da sua atenção hoje.
        </p>
      </div>

      {/* KPI Cards */}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
        <KpiCard icon={AlertTriangle} title="Alertas hoje"        value={alertsHoje}   sub="novos desde meia-noite" href="/alerts" />
        <KpiCard icon={Zap}          title="Críticos abertos"    value={critAbertos}  sub="ação imediata"           href="/alerts" variant={critAbertos > 0 ? 'red' : 'default'} />
        <KpiCard icon={FolderOpen}   title="Casos em andamento"  value={casesAbertos} sub="abertos ou em revisão"  href="/cases" />
        <KpiCard icon={Clock}        title="SLA vencido"         value={casesUrgentes} sub="casos fora do prazo"   href="/cases" variant={casesUrgentes > 0 ? 'orange' : 'default'} />
        <KpiCard icon={CheckCircle2} title="Auto-detectados"     value={autoCriados}  sub="criados pelo sistema"   href="/cases" variant="green" />
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        {/* Distribuição por prioridade */}
        <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-gray-700">Alertas abertos por prioridade</h2>
            <Link href="/alerts" className="text-xs text-brand hover:underline">Ver todos →</Link>
          </div>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={bySev} barCategoryGap="35%">
              <XAxis dataKey="name" tick={{ fontSize: 12 }} />
              <YAxis tick={{ fontSize: 12 }} allowDecimals={false} />
              <Tooltip formatter={(v: unknown) => [`${v} alertas`, 'Total']} contentStyle={{ fontSize: 12 }} />
              <Bar dataKey="total" radius={[4, 4, 0, 0]} label={{ position: 'top', fontSize: 11 }}>
                {bySev.map((entry) => (
                  <Cell key={entry.key} fill={SEV_COLOR[entry.key] ?? '#6b7280'} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* Casos com prazo próximo */}
        <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-gray-700">Casos com prazo vencendo em 24h</h2>
            <Link href="/cases" className="text-xs text-brand hover:underline">Ver todos →</Link>
          </div>
          {slaProximo.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-40 text-gray-400">
              <CheckCircle2 size={32} className="mb-2 text-green-400" />
              <p className="text-sm">Nenhum caso urgente no momento</p>
            </div>
          ) : (
            <ul className="space-y-2">
              {slaProximo.map((c) => {
                const due  = new Date((c as Record<string, unknown>).sla_due_at as string);
                const mins = Math.round((due.getTime() - now.getTime()) / 60000);
                const label = mins < 60 ? `${mins}min` : `${Math.round(mins / 60)}h`;
                return (
                  <li key={c.id}>
                    <Link
                      href={`/cases/${c.id}`}
                      className="flex items-center justify-between rounded-lg border border-orange-100 bg-orange-50 px-4 py-2.5 hover:border-orange-200 transition-colors"
                    >
                      <div>
                        <p className="text-xs font-semibold text-gray-800">{c.title}</p>
                        <p className="text-[10px] text-gray-500 mt-0.5 font-mono">{(c as Record<string, unknown>).reference_number as string}</p>
                      </div>
                      <span className="rounded bg-orange-100 px-2 py-0.5 text-xs font-bold text-orange-700">
                        {label} restantes
                      </span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </div>

      {/* Alertas críticos recentes */}
      {critRecentes.length > 0 && (
        <div className="rounded-xl border border-red-200 bg-red-50 p-5 shadow-sm">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-red-800">
              ⚠ Alertas Críticos abertos ({critAbertos})
            </h2>
            <Link href="/alerts" className="text-xs text-red-600 hover:underline">Ver todos →</Link>
          </div>
          <ul className="space-y-2">
            {critRecentes.map((a) => (
              <li key={a.id}>
                <Link
                  href={`/alerts/${a.id}`}
                  className="flex items-center gap-3 rounded-lg border border-red-100 bg-white px-4 py-2.5 text-xs hover:bg-red-50 transition-colors"
                >
                  <TrendingDown size={14} className="shrink-0 text-red-500" />
                  <span className="flex-1 font-medium text-red-900 truncate">{a.title}</span>
                  <span className="shrink-0 text-gray-400">
                    {new Date(a.created_at).toLocaleString('pt-BR', { hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit' })}
                  </span>
                  <ChevronRight size={12} className="text-gray-400" />
                </Link>
              </li>
            ))}
          </ul>
        </div>
      )}

      {casesUrgentes > 0 && (
        <div className="rounded-xl border border-orange-200 bg-orange-50 p-4 shadow-sm">
          <p className="text-sm font-semibold text-orange-800">
            ⏰ Você tem {casesUrgentes} caso{casesUrgentes > 1 ? 's' : ''} com SLA vencido.{' '}
            <Link href="/cases" className="underline">Verificar agora →</Link>
          </p>
        </div>
      )}
    </div>
  );
}
