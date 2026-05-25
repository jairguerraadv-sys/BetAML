'use client';
import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import Link from 'next/link';
import {
  api,
  fetchMonthlySummary,
  fetchReportFilingHotlist,
  fetchReportFilingOverview,
  fetchReportFilingQueue,
  downloadReportPackage,
  exportReportPackageHtml,
  submitReportPackageFiling,
  type MonthlyReport,
} from '@/lib/api';
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  Download,
  ExternalLink,
  FileBarChart2,
  RefreshCw,
  Send,
  ShieldCheck,
  Users,
} from 'lucide-react';

const SEV_CLS: Record<string, string> = {
  CRITICAL: 'bg-red-100 text-red-700 border border-red-200',
  HIGH:     'bg-orange-100 text-orange-700 border border-orange-200',
  MEDIUM:   'bg-yellow-100 text-yellow-700 border border-yellow-200',
  LOW:      'bg-green-100 text-green-700 border border-green-200',
};

const SEV_PT: Record<string, string> = {
  CRITICAL: 'Crítico', HIGH: 'Alto', MEDIUM: 'Médio', LOW: 'Baixo',
};

const FILING_STATE_CLS: Record<string, string> = {
  BREACH: 'bg-red-100 text-red-700 border-red-200',
  WARNING: 'bg-yellow-100 text-yellow-700 border-yellow-200',
  OK: 'bg-green-50 text-green-700 border-green-200',
  NO_REPORT: 'bg-gray-100 text-gray-600 border-gray-200',
};

const FILING_STATE_PT: Record<string, string> = {
  BREACH: 'Vencido',
  WARNING: 'Próximo',
  OK: 'Em dia',
  NO_REPORT: 'Sem dossiê',
};

const FILING_ACTION_PT: Record<string, string> = {
  SUBMIT_REPORT: 'Submeter COAF',
  REGISTER_PROTOCOL: 'Registrar protocolo',
};

function fmt(d: Date) {
  return d.toISOString().slice(0, 10);
}

export default function ReportsPage() {
  const today = new Date();
  const firstOfMonth = new Date(today.getFullYear(), today.getMonth(), 1);
  const qc = useQueryClient();

  const [dateFrom, setDateFrom] = useState(() => fmt(firstOfMonth));
  const [dateTo,   setDateTo]   = useState(() => fmt(today));
  const [genYear,  setGenYear]  = useState(() => String(today.getFullYear()));
  const [genMonth, setGenMonth] = useState(() => String(today.getMonth() + 1));
  const [genMsg,   setGenMsg]   = useState('');
  const [filingLimit, setFilingLimit] = useState(20);
  const [submittingId, setSubmittingId] = useState<string | null>(null);
  const [submitMsg,    setSubmitMsg]    = useState<Record<string, string>>({});

  function triggerBlobDownload(blob: Blob, filename: string) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  }

  async function handleDownload(rpId: string) {
    const blob = await downloadReportPackage(rpId);
    triggerBlobDownload(blob, `report_package_${rpId}.json`);
  }

  async function handleExport(rpId: string) {
    const blob = await exportReportPackageHtml(rpId);
    triggerBlobDownload(blob, `report_package_${rpId}.html`);
  }

  async function handleSubmit(rpId: string) {
    setSubmittingId(rpId);
    try {
      const res = await submitReportPackageFiling(rpId);
      setSubmitMsg((prev) => ({ ...prev, [rpId]: res.message }));
      qc.invalidateQueries({ queryKey: ['report-filing-queue'] });
      qc.invalidateQueries({ queryKey: ['report-filing-overview'] });
    } catch {
      setSubmitMsg((prev) => ({ ...prev, [rpId]: 'Erro ao submeter. Verifique a decisão do pacote.' }));
    } finally {
      setSubmittingId(null);
    }
  }

  const {
    data: report,
    isFetching,
    refetch,
    isError,
    isFetched,
  } = useQuery({
    queryKey: ['report', dateFrom, dateTo],
    queryFn: () => fetchMonthlySummary(dateFrom, dateTo),
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

  const {
    data: filingOverview,
    isLoading: filingOverviewLoading,
    isError: filingOverviewError,
  } = useQuery({
    queryKey: ['report-filing-overview'],
    queryFn: fetchReportFilingOverview,
  });

  const {
    data: filingHotlist,
    isLoading: filingHotlistLoading,
  } = useQuery({
    queryKey: ['report-filing-hotlist', filingLimit],
    queryFn: () => fetchReportFilingHotlist(filingLimit),
  });

  const {
    data: filingQueue,
    isFetching: filingQueueFetching,
    refetch: refetchFilingQueue,
  } = useQuery({
    queryKey: ['report-filing-queue', filingLimit],
    queryFn: () => fetchReportFilingQueue(filingLimit),
  });

  const csvHref = `/api-proxy/reports/monthly-summary/csv?date_from=${dateFrom}&date_to=${dateTo}`;

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2">
        <FileBarChart2 size={22} className="text-brand" />
        <h1 className="text-2xl font-bold text-gray-900">Relatórios Mensais</h1>
      </div>

      <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="flex items-center gap-2 text-sm font-semibold text-gray-700">
              <ShieldCheck size={16} className="text-gray-400" /> Governança de Filing COAF
            </h2>
            <p className="mt-1 text-xs text-gray-500">
              Pacotes FILE_SAR pendentes, prazos regulatórios e protocolos pós-submissão.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <select
              aria-label="Quantidade de itens na fila de filing"
              value={filingLimit}
              onChange={(e) => setFilingLimit(Number(e.target.value))}
              className="rounded-lg border border-gray-200 px-2 py-1.5 text-xs text-gray-700 focus:outline-none focus:ring-1 focus:ring-brand"
            >
              {[10, 20, 50, 100].map((n) => (
                <option key={n} value={n}>{n} itens</option>
              ))}
            </select>
            <button
              onClick={() => refetchFilingQueue()}
              disabled={filingQueueFetching}
              className="flex items-center gap-2 rounded-lg border border-gray-200 px-3 py-1.5 text-xs font-semibold text-gray-700 hover:bg-gray-50 disabled:opacity-50"
            >
              <RefreshCw size={13} className={filingQueueFetching ? 'animate-spin' : ''} />
              Atualizar
            </button>
          </div>
        </div>

        {filingOverviewError && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
            Não foi possível carregar a governança de filing.
          </div>
        )}

        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div className="rounded-lg border border-gray-100 bg-gray-50 p-3">
            <p className="text-xs text-gray-500">Casos com dossiê</p>
            <p className="mt-1 text-2xl font-bold text-gray-900">
              {filingOverviewLoading ? '—' : filingOverview?.total_cases_with_reports ?? 0}
            </p>
          </div>
          <div className="rounded-lg border border-red-100 bg-red-50 p-3">
            <p className="text-xs text-red-600">Submissão pendente</p>
            <p className="mt-1 text-2xl font-bold text-red-700">
              {filingOverviewLoading ? '—' : filingOverview?.requires_submission_count ?? 0}
            </p>
          </div>
          <div className="rounded-lg border border-blue-100 bg-blue-50 p-3">
            <p className="text-xs text-blue-600">Protocolo pendente</p>
            <p className="mt-1 text-2xl font-bold text-blue-700">
              {filingOverviewLoading ? '—' : filingOverview?.missing_protocol_count ?? 0}
            </p>
          </div>
          <div className="rounded-lg border border-yellow-100 bg-yellow-50 p-3">
            <p className="text-xs text-yellow-700">Mais antigo pendente</p>
            <p className="mt-1 text-2xl font-bold text-yellow-800">
              {filingOverview?.oldest_pending_submission_days != null ? `${filingOverview.oldest_pending_submission_days}d` : '—'}
            </p>
          </div>
        </div>

        <div className="mt-5 grid gap-4 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.4fr)]">
          <div className="rounded-lg border border-gray-100 p-3">
            <h3 className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-gray-500">
              <Clock size={13} /> Ações prioritárias
            </h3>
            <div className="space-y-2">
              {filingHotlistLoading && <p className="text-xs text-gray-400">Carregando...</p>}
              {(filingHotlist?.items ?? []).map((item) => (
                <Link
                  key={`${item.report_package_id}-${item.action_required}`}
                  href={`/cases/${item.case_id}?tab=decision`}
                  className="block rounded-lg border border-gray-100 px-3 py-2 text-xs hover:bg-gray-50"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-semibold text-gray-800">
                      {FILING_ACTION_PT[item.action_required] ?? item.action_required}
                    </span>
                    <span className={`rounded border px-2 py-0.5 font-bold ${FILING_STATE_CLS[item.deadline_state] ?? FILING_STATE_CLS.OK}`}>
                      {FILING_STATE_PT[item.deadline_state] ?? item.deadline_state}
                    </span>
                  </div>
                  <p className="mt-1 font-mono text-gray-400">{item.case_id.slice(0, 8)}... · {item.days_since_report_created ?? 0}d</p>
                  {item.warnings[0] && <p className="mt-1 text-red-600">{item.warnings[0]}</p>}
                </Link>
              ))}
              {!filingHotlistLoading && !(filingHotlist?.items.length) && (
                <div className="rounded-lg border border-green-100 bg-green-50 px-3 py-2 text-xs text-green-700">
                  <CheckCircle2 size={13} className="mr-1 inline" />
                  Sem ações pendentes.
                </div>
              )}
            </div>
          </div>

          <div className="overflow-hidden rounded-lg border border-gray-100">
            <table className="w-full text-xs">
              <thead className="bg-gray-50 text-left text-gray-500">
                <tr>
                  <th className="px-3 py-2 font-semibold">Caso</th>
                  <th className="px-3 py-2 font-semibold">Decisão</th>
                  <th className="px-3 py-2 font-semibold">Status</th>
                  <th className="px-3 py-2 font-semibold">Prazo</th>
                  <th className="px-3 py-2 text-right font-semibold">Ações</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {(filingQueue?.items ?? []).map((item) => (
                  <tr key={item.report_package_id} className="hover:bg-gray-50">
                    <td className="px-3 py-2 font-mono text-gray-700">{item.case_id.slice(0, 8)}...</td>
                    <td className="px-3 py-2 text-gray-600">{item.report_decision ?? '—'}</td>
                    <td className="px-3 py-2 text-gray-600">
                      {item.report_status}
                      {item.protocol_registered && <span className="ml-1 text-green-700">· protocolo</span>}
                    </td>
                    <td className="px-3 py-2">
                      <span className={`rounded border px-2 py-0.5 font-semibold ${FILING_STATE_CLS[item.deadline_state] ?? FILING_STATE_CLS.OK}`}>
                        {FILING_STATE_PT[item.deadline_state] ?? item.deadline_state}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-right">
                      <div className="flex items-center justify-end gap-1 flex-wrap">
                        <button
                          onClick={() => handleDownload(item.report_package_id)}
                          className="inline-flex items-center gap-1 rounded border border-gray-200 px-2 py-1 text-gray-600 hover:bg-white"
                          title="Baixar JSON"
                        >
                          <Download size={11} /> JSON
                        </button>
                        <button
                          onClick={() => handleExport(item.report_package_id)}
                          className="inline-flex items-center gap-1 rounded border border-gray-200 px-2 py-1 text-gray-600 hover:bg-white"
                          title="Exportar HTML"
                        >
                          <Download size={11} /> HTML
                        </button>
                        {item.requires_submission && item.report_status !== 'FILED' && (
                          <button
                            onClick={() => handleSubmit(item.report_package_id)}
                            disabled={submittingId === item.report_package_id}
                            className="inline-flex items-center gap-1 rounded border border-blue-200 bg-blue-50 px-2 py-1 font-semibold text-blue-700 hover:bg-blue-100 disabled:opacity-50"
                            title="Registrar submissão COAF"
                          >
                            <Send size={11} /> {submittingId === item.report_package_id ? '...' : 'Submeter'}
                          </button>
                        )}
                        <Link
                          href={`/cases/${item.case_id}?tab=decision`}
                          className="inline-flex items-center gap-1 rounded border border-gray-200 px-2 py-1 font-semibold text-gray-700 hover:bg-white"
                        >
                          Abrir <ExternalLink size={12} />
                        </Link>
                      </div>
                      {submitMsg[item.report_package_id] && (
                        <p className="mt-1 text-right text-[10px] text-blue-600">{submitMsg[item.report_package_id]}</p>
                      )}
                    </td>
                  </tr>
                ))}
                {!(filingQueue?.items.length) && (
                  <tr>
                    <td colSpan={5} className="px-3 py-8 text-center text-gray-400">
                      Nenhum pacote de reporte na fila.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      {/* Consulta por período */}
      <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
        <h2 className="mb-3 text-sm font-semibold text-gray-700">Consultar Período</h2>
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-gray-600">De</label>
            <input
              type="date"
              aria-label="Data inicial do relatório mensal"
              value={dateFrom}
              onChange={(e) => setDateFrom(e.target.value)}
              className="rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-gray-600">Até</label>
            <input
              type="date"
              aria-label="Data final do relatório mensal"
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
            <div className="rounded-xl border border-gray-200 bg-white p-4">
              <p className="text-xs text-gray-500">Comunicações geradas</p>
              <p className="mt-1 text-2xl font-bold text-gray-900">
                {report.total_communications_generated.toLocaleString('pt-BR')}
              </p>
            </div>
            <div className="rounded-xl border border-gray-200 bg-white p-4">
              <p className="text-xs text-gray-500">Comunicações COAF</p>
              <p className="mt-1 text-2xl font-bold text-gray-900">
                {report.total_sar_reports.toLocaleString('pt-BR')}
              </p>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <div className="rounded-xl border border-gray-200 bg-white p-4">
              <p className="text-xs text-gray-500">Casos totais</p>
              <p className="mt-1 text-2xl font-bold text-gray-900">{report.total_cases}</p>
            </div>
            <div className="rounded-xl border border-gray-200 bg-white p-4">
              <p className="text-xs text-gray-500">Casos fechados</p>
              <p className="mt-1 text-2xl font-bold text-gray-900">{report.total_cases_closed}</p>
            </div>
            <div className="rounded-xl border border-gray-200 bg-white p-4">
              <p className="text-xs text-gray-500">Casos reportados</p>
              <p className="mt-1 text-2xl font-bold text-gray-900">{report.total_cases_reported}</p>
            </div>
            <div className="rounded-xl border border-gray-200 bg-white p-4">
              <p className="text-xs text-gray-500">Alertas rotulados</p>
              <p className="mt-1 text-2xl font-bold text-gray-900">{report.quality_metrics.labeled_alerts}</p>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
            <div className="rounded-xl border border-gray-200 bg-white p-4">
              <p className="text-xs text-gray-500">Taxa falsos positivos</p>
              <p className="mt-1 text-2xl font-bold text-gray-900">
                {report.false_positive_rate != null ? `${(report.false_positive_rate * 100).toFixed(1)}%` : 'N/D'}
              </p>
            </div>
            <div className="rounded-xl border border-gray-200 bg-white p-4">
              <p className="text-xs text-gray-500">Taxa verdadeiros positivos</p>
              <p className="mt-1 text-2xl font-bold text-gray-900">
                {report.true_positive_rate != null ? `${report.true_positive_rate.toFixed(1)}%` : 'N/D'}
              </p>
            </div>
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

          {/* Top jogadores */}
          {report.top_players_by_risk.length > 0 && (
            <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
              <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-gray-700">
                <Users size={14} /> Top Jogadores por Risco Médio
              </h3>
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-100 text-xs text-gray-400">
                    <th className="pb-2 text-left">Jogador (ID externo)</th>
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
              aria-label="Ano para geração do relatório mensal"
              value={genYear}
              onChange={(e) => setGenYear(e.target.value)}
              className="w-24 rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-gray-600">Mês</label>
            <select
              aria-label="Mês para geração do relatório mensal"
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
