'use client';

import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import Link from 'next/link';
import {
  FileBarChart2,
  AlertTriangle,
  FolderOpen,
  Clock,
  ShieldAlert,
  Activity,
  ChevronRight,
  Trash2,
  UserCheck,
  TrendingDown,
} from 'lucide-react';
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  PieChart,
  Pie,
  Cell,
  BarChart,
  Bar,
  Legend,
} from 'recharts';

import { fetchDashboardStats, fetchReportFilingOverview } from '@/lib/api';
import { useUser } from '@/contexts/UserContext';
import { fmtDate, useLocale } from '@/lib/i18n';

const SEV_COLOR: Record<string, string> = {
  CRITICAL: '#dc2626',
  HIGH: '#f97316',
  MEDIUM: '#eab308',
  LOW: '#22c55e',
};

const WEEKDAY_LABELS = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sab', 'Dom'];

function greeting() {
  const h = new Date().getHours();
  if (h < 12) return 'Bom dia';
  if (h < 18) return 'Boa tarde';
  return 'Boa noite';
}

function formatCompactDate(iso: string, locale: string) {
  return new Intl.DateTimeFormat(locale, { day: '2-digit', month: '2-digit' }).format(new Date(iso));
}

function KpiCard({
  icon: Icon,
  title,
  value,
  sub,
  href,
  tone = 'default',
}: {
  icon: React.ElementType;
  title: string;
  value: number | string;
  sub?: string;
  href?: string;
  tone?: 'default' | 'warning' | 'danger' | 'success';
}) {
  const cls = {
    default: 'border-slate-200 bg-white',
    warning: 'border-amber-200 bg-amber-50',
    danger: 'border-red-200 bg-red-50',
    success: 'border-emerald-200 bg-emerald-50',
  };
  const iconCls = {
    default: 'text-sky-600',
    warning: 'text-amber-600',
    danger: 'text-red-600',
    success: 'text-emerald-600',
  };

  const content = (
    <div className={`rounded-2xl border p-5 shadow-sm transition-shadow hover:shadow-md ${cls[tone]}`}>
      <div className="flex items-start justify-between gap-3">
        <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-gray-500">{title}</p>
        <Icon size={18} className={iconCls[tone]} aria-hidden="true" />
      </div>
      <p className="mt-3 text-4xl font-bold tracking-tight text-gray-950">{value}</p>
      {sub && <p className="mt-1 text-xs text-gray-500">{sub}</p>}
      {href && (
        <p className="mt-4 flex items-center gap-1 text-xs font-semibold text-brand">
          Abrir painel <ChevronRight size={12} aria-hidden="true" />
        </p>
      )}
    </div>
  );

  return href ? <Link href={href}>{content}</Link> : content;
}

function Heatmap({
  points,
}: {
  points: Array<{ weekday: number; hour: number; count: number }>;
}) {
  const max = points.reduce((acc, point) => Math.max(acc, point.count), 1);
  const map = new Map(points.map((point) => [`${point.weekday}-${point.hour}`, point.count]));

  return (
    <div className="overflow-x-auto" aria-label="Mapa de calor de alertas por hora e dia da semana">
      <div className="grid min-w-[760px] grid-cols-[80px_repeat(24,minmax(20px,1fr))] gap-1 text-[10px]">
        <div />
        {Array.from({ length: 24 }).map((_, hour) => (
          <div key={hour} className="text-center text-gray-400">
            {hour.toString().padStart(2, '0')}
          </div>
        ))}
        {WEEKDAY_LABELS.map((day, weekday) => (
          <div key={day} className="contents">
            <div className="flex items-center pr-2 text-xs font-medium text-gray-600">{day}</div>
            {Array.from({ length: 24 }).map((_, hour) => {
              const count = map.get(`${weekday}-${hour}`) ?? 0;
              const opacity = count === 0 ? 0.08 : Math.max(count / max, 0.18);
              return (
                <div
                  key={`${weekday}-${hour}`}
                  className="h-6 rounded-md border border-white/40"
                  style={{ backgroundColor: `rgba(14, 165, 233, ${opacity})` }}
                  aria-label={`${day} às ${hour}h: ${count} alertas`}
                  title={`${day} às ${hour}h: ${count} alertas`}
                />
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}

export default function DashboardPage() {
  const { user } = useUser();
  const [locale] = useLocale();
  const userName = user?.username ?? '';

  const { data: stats, isLoading } = useQuery({
    queryKey: ['dashboard-stats'],
    queryFn: fetchDashboardStats,
    refetchInterval: 30_000,
  });

  const { data: filingOverview } = useQuery({
    queryKey: ['report-filing-overview'],
    queryFn: fetchReportFilingOverview,
    refetchInterval: 60_000,
  });

  const severityTimeline = useMemo(
    () =>
      (stats?.alerts_by_severity_30d ?? []).map((row) => ({
        ...row,
        label: formatCompactDate(row.date, locale),
      })),
    [stats?.alerts_by_severity_30d, locale],
  );

  const topPlayers = stats?.top_players_by_risk ?? [];
  const ruleTypeData = stats?.alerts_by_rule_type ?? [];
  const heatmapPoints = stats?.alert_heatmap ?? [];
  const highFpRules = stats?.high_fp_rules ?? [];
  const canOpenIngestJobs = Boolean(user?.roles?.includes('Operador_AdminTecnico'));

  return (
    <div className="space-y-6">
      <div className="rounded-[28px] border border-slate-200 bg-gradient-to-br from-slate-950 via-sky-950 to-cyan-900 p-6 text-white shadow-sm">
        <p className="text-[11px] font-semibold uppercase tracking-[0.22em] text-cyan-200/80">Painel do Analista</p>
        <h1 className="mt-2 text-3xl font-bold tracking-tight">
          {greeting()}{userName ? `, ${userName}` : ''}! Veja o que precisa da sua atenção hoje.
        </h1>
        <p className="mt-2 max-w-3xl text-sm text-cyan-50/80">
          Resumo da sua fila de trabalho — alertas abertos, SLA, risco e eventos do dia.
        </p>
        {stats?.tenant_name && (
          <span className="mt-3 inline-flex items-center rounded-full border border-cyan-400/30 bg-cyan-400/10 px-3 py-1 text-xs font-semibold text-cyan-200">
            Operador: {stats.tenant_name}
            {stats.tenant_slug && <span className="ml-1.5 font-normal opacity-70">({stats.tenant_slug})</span>}
          </span>
        )}
        {stats?.generated_at && (
          <p className="mt-4 text-xs text-cyan-100/70">
            Atualizado em {fmtDate(stats.generated_at, locale)}
          </p>
        )}
      </div>

      <div className="grid grid-cols-2 gap-4 xl:grid-cols-5">
        <KpiCard
          icon={AlertTriangle}
          title="Alertas abertos"
          value={stats?.alerts_open ?? 0}
          sub="fila ativa de triagem"
          href="/alerts"
          tone={(stats?.alerts_open ?? 0) > 0 ? 'warning' : 'default'}
        />
        <KpiCard
          icon={FolderOpen}
          title="Em investigação"
          value={stats?.cases_investigating ?? 0}
          sub="casos aguardando ação"
          href="/cases"
        />
        <KpiCard
          icon={Clock}
          title="Próximos do SLA"
          value={stats?.cases_near_sla ?? 0}
          sub="vencem nas próximas 24h"
          href="/cases"
          tone={(stats?.cases_near_sla ?? 0) > 0 ? 'danger' : 'success'}
        />
        <KpiCard
          icon={ShieldAlert}
          title="Jogadores alto risco"
          value={stats?.high_risk_players ?? 0}
          sub="classificação de risco alto"
          href="/players"
          tone={(stats?.high_risk_players ?? 0) > 0 ? 'warning' : 'default'}
        />
        <KpiCard
          icon={Activity}
          title="Eventos ingeridos hoje"
          value={(stats?.events_ingested_today ?? 0).toLocaleString(locale)}
          sub="processados desde 00:00"
          href={canOpenIngestJobs ? '/ingest-jobs' : undefined}
          tone="success"
        />
      </div>

      {/* Analyst daily-work row */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <KpiCard
          icon={Trash2}
          title="Descartados (últimos 7 dias)"
          value={stats?.dismissed_7d ?? 0}
          sub="alertas descartados recentemente — vale revisar"
          tone={(stats?.dismissed_7d ?? 0) > 20 ? 'warning' : 'default'}
        />
        <KpiCard
          icon={UserCheck}
          title="Casos na minha fila / SLA"
          value={stats?.my_cases_near_sla ?? 0}
          sub="casos atribuídos a mim que vencem em 24h"
          href="/cases"
          tone={(stats?.my_cases_near_sla ?? 0) > 0 ? 'danger' : 'success'}
        />
        <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="flex items-start justify-between gap-3">
            <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-gray-500">Regras com muitos falsos positivos</p>
            <TrendingDown size={18} className="text-orange-500" aria-hidden="true" />
          </div>
          {highFpRules.length === 0 ? (
            <p className="mt-3 text-sm text-gray-400">Nenhuma regra com FP elevado no período.</p>
          ) : (
            <ul className="mt-3 space-y-1">
              {highFpRules.map((r) => (
                <li key={r.rule_id} className="flex items-center justify-between text-xs">
                  <span className="text-gray-700 truncate max-w-[160px]" title={r.rule_name}>{r.rule_name}</span>
                  <span className="ml-2 shrink-0 rounded-full bg-orange-100 px-2 py-0.5 text-[11px] font-semibold text-orange-700">
                    {r.fp_count} FP
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <KpiCard
          icon={FileBarChart2}
          title="Comunicações pendentes"
          value={filingOverview?.requires_submission_count ?? 0}
          sub="COS aguardando submissão ao Coaf"
          href="/reports"
          tone={(filingOverview?.requires_submission_count ?? 0) > 0 ? 'danger' : 'success'}
        />
        <KpiCard
          icon={ShieldAlert}
          title="Protocolos pendentes"
          value={filingOverview?.missing_protocol_count ?? 0}
          sub="comunicações sem protocolo Coaf"
          href="/reports"
          tone={(filingOverview?.missing_protocol_count ?? 0) > 0 ? 'warning' : 'success'}
        />
        <KpiCard
          icon={Clock}
          title="Pior pendência COAF"
          value={filingOverview?.oldest_pending_submission_days != null ? `${filingOverview.oldest_pending_submission_days}d` : '—'}
          sub="idade do dossiê mais antigo"
          href="/reports"
          tone={(filingOverview?.deadline_state_counts?.BREACH ?? 0) > 0 ? 'danger' : 'default'}
        />
      </div>

      {isLoading && (
        <div className="rounded-2xl border border-dashed border-slate-200 bg-white px-5 py-10 text-center text-sm text-gray-400">
          Carregando dashboard…
        </div>
      )}

      {!isLoading && (
        <>
          <div className="grid gap-6 xl:grid-cols-[1.45fr_0.95fr]">
            <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
              <div className="mb-4 flex items-center justify-between">
                <div>
                  <h2 className="text-sm font-semibold text-gray-800">Alertas por severidade, últimos 30 dias</h2>
                  <p className="mt-1 text-xs text-gray-500">Como os alertas evoluíram nos últimos 30 dias.</p>
                </div>
                <Link href="/alerts" className="text-xs font-semibold text-brand hover:underline">
                  Abrir alertas
                </Link>
              </div>
              <ResponsiveContainer width="100%" height={280}>
                <LineChart data={severityTimeline}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                  <XAxis dataKey="label" tick={{ fontSize: 12 }} minTickGap={18} />
                  <YAxis allowDecimals={false} tick={{ fontSize: 12 }} />
                  <Tooltip />
                  <Legend />
                  {(['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'] as const).map((sev) => (
                    <Line
                      key={sev}
                      type="monotone"
                      dataKey={sev}
                      stroke={SEV_COLOR[sev]}
                      strokeWidth={2}
                      dot={false}
                      activeDot={{ r: 4 }}
                    />
                  ))}
                </LineChart>
              </ResponsiveContainer>
            </section>

            <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
              <div className="mb-4 flex items-center justify-between">
                <div>
                  <h2 className="text-sm font-semibold text-gray-800">Distribuição por tipo de alerta</h2>
                  <p className="mt-1 text-xs text-gray-500">Alertas por origem — regras, anomalias ou composições.</p>
                </div>
              </div>
              <ResponsiveContainer width="100%" height={280}>
                <PieChart>
                  <Pie
                    data={ruleTypeData}
                    dataKey="value"
                    nameKey="label"
                    innerRadius={58}
                    outerRadius={92}
                    paddingAngle={3}
                  >
                    {ruleTypeData.map((entry, idx) => (
                      <Cell
                        key={`${entry.label}-${idx}`}
                        fill={['#0f766e', '#0284c7', '#f97316', '#dc2626', '#7c3aed', '#16a34a'][idx % 6]}
                      />
                    ))}
                  </Pie>
                  <Tooltip formatter={(value: number) => value.toLocaleString(locale)} />
                  <Legend />
                </PieChart>
              </ResponsiveContainer>
            </section>
          </div>

          <div className="grid gap-6 xl:grid-cols-[1.1fr_1.3fr]">
            <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
              <div className="mb-4 flex items-center justify-between">
                <div>
                  <h2 className="text-sm font-semibold text-gray-800">Jogadores em alta prioridade</h2>
                  <p className="mt-1 text-xs text-gray-500">Priorize a revisão dos perfis com maior risco.</p>
                </div>
                <Link href="/players" className="text-xs font-semibold text-brand hover:underline">
                  Ver jogadores
                </Link>
              </div>
              <ResponsiveContainer width="100%" height={300}>
                <BarChart data={topPlayers}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                  <XAxis
                    dataKey="external_player_id"
                    tick={{ fontSize: 11 }}
                    interval={0}
                    angle={-25}
                    textAnchor="end"
                    height={72}
                  />
                  <YAxis tick={{ fontSize: 12 }} domain={[0, 1]} />
                  <Tooltip formatter={(value: number) => `${(value * 100).toFixed(1)}%`} />
                  <Bar dataKey="risk_score" radius={[6, 6, 0, 0]}>
                    {topPlayers.map((row) => (
                      <Cell
                        key={row.player_id}
                        fill={row.risk_band === 'HIGH' ? '#dc2626' : row.risk_band === 'MEDIUM' ? '#f59e0b' : '#16a34a'}
                      />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </section>

            <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
              <div className="mb-4 flex items-center justify-between">
                <div>
                  <h2 className="text-sm font-semibold text-gray-800">Quando os alertas costumam surgir</h2>
                  <p className="mt-1 text-xs text-gray-500">Concentração por hora e dia da semana (últimos 30 dias).</p>
                </div>
                <span className="rounded-full bg-sky-50 px-2.5 py-1 text-[11px] font-semibold text-sky-700">
                  janela 30d
                </span>
              </div>
              <Heatmap points={heatmapPoints} />
            </section>
          </div>

          {(stats?.cases_near_sla ?? 0) > 0 && (
            <div className="rounded-2xl border border-red-200 bg-red-50 px-5 py-4 shadow-sm">
              <p className="text-sm font-semibold text-red-800">
                Há {stats?.cases_near_sla} caso(s) prestes a vencer SLA nas próximas 24 horas.
                <Link href="/cases" className="ml-1 underline">Abrir fila de casos</Link>
              </p>
            </div>
          )}

          <div className="grid gap-4 xl:grid-cols-3">
            <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
              <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-400">Alertas hoje</p>
              <p className="mt-2 text-3xl font-bold text-gray-950">{stats?.alerts_today ?? 0}</p>
              <p className="mt-1 text-xs text-gray-500">novos desde meia-noite</p>
            </div>
            <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
              <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-400">Críticos abertos</p>
              <p className="mt-2 text-3xl font-bold text-gray-950">{stats?.critical_open ?? 0}</p>
              <p className="mt-1 text-xs text-gray-500">ação imediata</p>
            </div>
            <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
              <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-400">Auto-detectados</p>
              <p className="mt-2 text-3xl font-bold text-gray-950">{stats?.auto_detected ?? 0}</p>
              <p className="mt-1 text-xs text-gray-500">casos criados automaticamente</p>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
