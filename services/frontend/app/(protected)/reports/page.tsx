'use client';
import { useState } from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { FileBarChart2, Download, RefreshCw, Send, AlertTriangle, Users } from 'lucide-react';

interface AlertsBySeverity {
  CRITICAL: number;
  HIGH: number;
  MEDIUM: number;
  LOW: number;
}

interface TopRule {
  rule_id: string;
  rule_name: string;
  fires: number;
}

interface TopPlayer {
  player_id: string;
  external_id: string;
  avg_risk_score: number;
}

interface MonthlyReport {
  period: { from: string; to: string };
  alerts_by_severity: AlertsBySeverity;
  cases_summary: Record<string, number>;
  top_rules_by_fires: TopRule[];
  top_players_by_risk: TopPlayer[];
  total_ingested_events: number;
  false_positive_rate: number | null;
  generated_at: string;
}

const SEV_CLS: Record<string, string> = {
  CRITICAL: 'bg-red-100 text-red-700 border border-red-200',
  HIGH:     'bg-orange-100 text-orange-700 border border-orange-200',
  MEDIUM:   'bg-yellow-100 text-yellow-700 border border-yellow-200',
  LOW:      'bg-green-100 text-green-700 border border-green-200',
};

const SEV_PT: Record<string, string> = {
  CRITICAL: 'Crítico', HIGH: 'Alto', MEDIUM: 'Médio', LOW: 'Baixo',
};

function fmt(d: Date) {
  return d.toISOString().slice(0, 10);
}

export default function ReportsPage() {
  const today = new Date();
  const firstOfMonth = new Date(today.getFullYear(), today.getMonth(), 1);

  const [dateFrom, setDateFrom] = useState(() => fmt(firstOfMonth));
  const [dateTo,   setDateTo]   = useState(() => fmt(today));
  const [genYear,  setGenYear]  = useState(() => String(today.getFullYear()));
  const [genMonth, setGenMonth] = useState(() => String(today.getMonth() + 1));
  const [genMsg,   setGenMsg]   = useState('');

  const {
    data: report,
    isFetching,
    refetch,
    isError,
    isFetched,
  } = useQuery({
    queryKey: ['report', dateFrom, dateTo],
    queryFn: () =>
      api.get<MonthlyReport>('/reports/monthly-summary', {
        params: { date_from: dateFrom, date_to: dateTo },
      }).then((r) => r.data),
    enabled: false,
    retry: false,
  });

  const generate = useMutation({
    mutationFn: () =>
      api.post('/reports/monthly-summary', {
        year:  parseInt(genYear, 10),
        month: parseInt(genMonth, 10),
      }),
    onSuccess: () => setGenMsg('Relatório enfileirado — processamento em segundo plano.'),
    onError:   () => setGenMsg('Erro ao enfileirar geração.'),
  });

  const csvHref = `/api-proxy/reports/monthly-summary/csv?date_from=${dateFrom}&date_to=${dateTo}`;

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2">
        <FileBarChart2 size={22} className="text-brand" />
        <h1 className="text-2xl font-bold text-gray-900">Relatórios Mensais</h1>
      </div>

      {/* Consulta por período */}
      <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
        <h2 className="mb-3 text-sm font-semibold text-gray-700">Consultar Período</h2>
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-gray-600">De</label>
            <input
              type="date"
              value={dateFrom}
              onChange={(e) => setDateFrom(e.target.value)}
              className="rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-gray-600">Até</label>
            <input
              type="date"
              value={dateTo}
              onChange={(e) => setDateTo(e.target.value)}
              className="rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand"
            />
          </div>
          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className="flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand/90 disabled:opacity-50"
          >
            <RefreshCw size={14} className={isFetching ? 'animate-spin' : ''} />
            {isFetching ? 'Consultando…' : 'Consultar'}
          </button>
          <a
            href={csvHref}
            target="_blank"
            rel="noreferrer"
            className="flex items-center gap-2 rounded-lg border border-gray-200 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
          >
            <Download size={14} /> CSV
          </a>
        </div>
      </section>

      {isError && (
        <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3">
          <p className="flex items-center gap-2 text-sm text-red-700">
            <AlertTriangle size={14} />
            Erro ao consultar relatório. Verifique as datas e tente novamente.
          </p>
        </div>
      )}

      {!report && isFetched && !isError && (
        <div className="rounded-xl border border-dashed border-gray-200 bg-gray-50 py-10 text-center">
          <p className="text-sm text-gray-500">Nenhum dado encontrado para o período selecionado.</p>
        </div>
      )}

      {!report && !isFetched && (
        <div className="rounded-xl border border-dashed border-gray-200 bg-gray-50 py-16 text-center">
          <FileBarChart2 size={32} className="mx-auto mb-3 text-gray-300" />
          <p className="text-sm text-gray-500">Selecione um período e clique em &ldquo;Consultar&rdquo;</p>
        </div>
      )}

      {report && (
        <div className="space-y-5">
          <p className="text-xs text-gray-400">
            Período: <strong>{report.period.from}</strong> a <strong>{report.period.to}</strong>
            {' · '}Gerado em: {new Date(report.generated_at).toLocaleString('pt-BR')}
          </p>

          {/* Alertas por severidade */}
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            {(['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'] as const).map((sev) => (
              <div key={sev} className={`rounded-xl p-4 ${SEV_CLS[sev]}`}>
                <p className="text-xs font-semibold opacity-70">{SEV_PT[sev]}</p>
                <p className="mt-1 text-2xl font-bold">{report.alerts_by_severity[sev] ?? 0}</p>
                <p className="text-xs opacity-60">alertas</p>
              </div>
            ))}
          </div>

          {/* KPIs adicionais */}
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
            <div className="rounded-xl border border-gray-200 bg-white p-4">
              <p className="text-xs text-gray-500">Eventos ingeridos</p>
              <p className="mt-1 text-2xl font-bold text-gray-900">
                {report.total_ingested_events.toLocaleString('pt-BR')}
              </p>
            </div>
            {report.false_positive_rate != null && (
              <div className="rounded-xl border border-gray-200 bg-white p-4">
                <p className="text-xs text-gray-500">Taxa falsos positivos</p>
                <p className="mt-1 text-2xl font-bold text-gray-900">
                  {(report.false_positive_rate * 100).toFixed(1)}%
                </p>
              </div>
            )}
            <div className="rounded-xl border border-gray-200 bg-white p-4">
              <p className="mb-2 text-xs text-gray-500">Casos por status</p>
              <div className="flex flex-wrap gap-1">
                {Object.entries(report.cases_summary).map(([status, count]) => (
                  <span key={status} className="rounded bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-700">
                    {status}: {count}
                  </span>
                ))}
              </div>
            </div>
          </div>

          {/* Top regras */}
          {report.top_rules_by_fires.length > 0 && (
            <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
              <h3 className="mb-3 text-sm font-semibold text-gray-700">Top Regras Disparadas</h3>
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-100 text-xs text-gray-400">
                    <th className="pb-2 text-left">Regra</th>
                    <th className="pb-2 text-right">Disparos</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-50">
                  {report.top_rules_by_fires.map((r) => (
                    <tr key={r.rule_id}>
                      <td className="py-2 text-gray-700">{r.rule_name}</td>
                      <td className="py-2 text-right font-mono font-semibold text-gray-900">{r.fires}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Top clientes */}
          {report.top_players_by_risk.length > 0 && (
            <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
              <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-gray-700">
                <Users size={14} /> Top Clientes por Risco Médio
              </h3>
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-100 text-xs text-gray-400">
                    <th className="pb-2 text-left">Cliente (ID externo)</th>
                    <th className="pb-2 text-right">Score médio</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-50">
                  {report.top_players_by_risk.map((p) => (
                    <tr key={p.player_id}>
                      <td className="py-2 font-mono text-xs text-gray-600">{p.external_id}</td>
                      <td className="py-2 text-right">
                        <span className={`rounded px-2 py-0.5 text-xs font-semibold ${
                          p.avg_risk_score >= 0.7 ? 'bg-red-100 text-red-700' :
                          p.avg_risk_score >= 0.4 ? 'bg-yellow-100 text-yellow-700' :
                          'bg-green-100 text-green-700'
                        }`}>
                          {(p.avg_risk_score * 100).toFixed(0)}%
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Geração de relatório em background */}
      <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
        <h2 className="mb-1 text-sm font-semibold text-gray-700">Gerar Relatório Mensal</h2>
        <p className="mb-3 text-xs text-gray-400">
          Inicia o processamento em background para o mês selecionado. O resultado aparece na consulta acima ao finalizar.
        </p>
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-gray-600">Ano</label>
            <input
              type="number"
              min={2020}
              max={2030}
              value={genYear}
              onChange={(e) => setGenYear(e.target.value)}
              className="w-24 rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-gray-600">Mês</label>
            <select
              value={genMonth}
              onChange={(e) => setGenMonth(e.target.value)}
              className="rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand"
            >
              {Array.from({ length: 12 }, (_, i) => (
                <option key={i + 1} value={String(i + 1)}>
                  {new Date(2000, i).toLocaleString('pt-BR', { month: 'long' })}
                </option>
              ))}
            </select>
          </div>
          <button
            onClick={() => { setGenMsg(''); generate.mutate(); }}
            disabled={generate.isPending}
            className="flex items-center gap-2 rounded-lg border border-brand px-4 py-2 text-sm font-semibold text-brand hover:bg-gray-50 disabled:opacity-50"
          >
            <Send size={14} />
            {generate.isPending ? 'Enviando…' : 'Enfileirar'}
          </button>
          {genMsg && <p className="text-sm text-gray-600">{genMsg}</p>}
        </div>
      </section>
    </div>
  );
}
