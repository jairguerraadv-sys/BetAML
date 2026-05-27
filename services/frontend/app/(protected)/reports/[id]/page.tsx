'use client';
import { useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import Link from 'next/link';
import {
  api,
  downloadReportPackage,
  exportReportPackageHtml,
  submitReportPackageFiling,
} from '@/lib/api';
import {
  ArrowLeft,
  Download,
  FileText,
  Send,
  ShieldCheck,
} from 'lucide-react';
import { useGlossary } from '@/lib/use-glossary';

interface ReportPackageDetail {
  id: string;
  case_id: string;
  player_id: string | null;
  status: string;
  format: string;
  decision: string | null;
  created_at: string;
  generated_by: string | null;
  pdf_available: boolean;
  coaf_protocol_number?: string | null;
  filed_at?: string | null;
}

const STATUS_CLS: Record<string, string> = {
  DRAFT:  'bg-gray-100 text-gray-600',
  FINAL:  'bg-blue-100 text-blue-700',
  FILED:  'bg-green-100 text-green-700',
};

const DECISION_CLS: Record<string, string> = {
  FILE_SAR:   'bg-red-100 text-red-700',
  REPORT:     'bg-orange-100 text-orange-700',
  NO_ACTION:  'bg-gray-100 text-gray-500',
  PENDING:    'bg-yellow-100 text-yellow-700',
};

export default function ReportPackageDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const qc = useQueryClient();
  const { translate } = useGlossary();
  const [submitMsg, setSubmitMsg] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const { data: rp, isLoading, isError } = useQuery<ReportPackageDetail>({
    queryKey: ['report-package-detail', id],
    queryFn: () => api.get(`/report-packages?status=&limit=200`).then((r) => {
      const items: ReportPackageDetail[] = r.data;
      const found = items.find((i) => i.id === id);
      if (!found) throw new Error('not found');
      return found;
    }),
    enabled: !!id,
    retry: false,
  });

  function triggerBlobDownload(blob: Blob, filename: string) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  }

  async function handleDownload() {
    const blob = await downloadReportPackage(id);
    triggerBlobDownload(blob, `report_package_${id}.json`);
  }

  async function handleExport() {
    const blob = await exportReportPackageHtml(id);
    triggerBlobDownload(blob, `report_package_${id}.html`);
  }

  async function handleSubmit() {
    setSubmitting(true);
    setSubmitMsg('');
    try {
      const res = await submitReportPackageFiling(id);
      setSubmitMsg(res.message);
      qc.invalidateQueries({ queryKey: ['report-package-detail', id] });
      qc.invalidateQueries({ queryKey: ['report-filing-queue'] });
      qc.invalidateQueries({ queryKey: ['report-filing-overview'] });
    } catch {
      setSubmitMsg('Erro ao registrar envio. Verifique se a decisão exige comunicação ao Coaf.');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-3">
        <button
          onClick={() => router.back()}
          className="flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-800"
        >
          <ArrowLeft size={16} /> Voltar
        </button>
        <h1 className="text-xl font-bold text-gray-900">Dossiê COS</h1>
      </div>

      {isLoading && (
        <div className="rounded-xl border border-gray-100 bg-white p-8 text-center text-sm text-gray-400">
          Carregando...
        </div>
      )}

      {isError && (
        <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          Dossiê não encontrado ou sem permissão de acesso.
        </div>
      )}

      {rp && (
        <>
          {/* Metadata grid */}
          <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
            <div className="mb-4 flex items-center justify-between gap-3">
              <h2 className="flex items-center gap-2 text-sm font-semibold text-gray-700">
                <ShieldCheck size={15} className="text-gray-400" /> Dados do dossiê
              </h2>
              <div className="flex items-center gap-2">
                <button
                  onClick={handleDownload}
                  className="flex items-center gap-1.5 rounded-lg border border-gray-200 px-3 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-50"
                >
                  <Download size={13} /> Baixar JSON
                </button>
                <button
                  onClick={handleExport}
                  className="flex items-center gap-1.5 rounded-lg border border-gray-200 px-3 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-50"
                >
                  <FileText size={13} /> Exportar HTML
                </button>
                {rp.status !== 'FILED' && (rp.decision === 'FILE_SAR' || rp.decision === 'REPORT') && (
                  <button
                    onClick={handleSubmit}
                    disabled={submitting}
                    className="flex items-center gap-1.5 rounded-lg border border-blue-300 bg-blue-50 px-3 py-1.5 text-xs font-semibold text-blue-700 hover:bg-blue-100 disabled:opacity-50"
                  >
                    <Send size={13} /> {submitting ? 'Registrando...' : 'Registrar envio ao Coaf'}
                  </button>
                )}
              </div>
            </div>

            {submitMsg && (
              <div className="mb-4 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-700">
                {submitMsg}
              </div>
            )}

            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              <MetaField label="ID do dossiê" value={rp.id} mono />
              <MetaField label="Caso" value={
                <Link href={`/cases/${rp.case_id}`} className="font-mono text-brand hover:underline">
                  {rp.case_id.slice(0, 12)}...
                </Link>
              } />
              <MetaField label="Status" value={
                <span className={`rounded px-2 py-0.5 text-xs font-semibold ${STATUS_CLS[rp.status] ?? 'bg-gray-100 text-gray-600'}`}>
                  {translate.cosStatus(rp.status)}
                </span>
              } />
              <MetaField label="Decisão" value={
                rp.decision ? (
                  <span className={`rounded px-2 py-0.5 text-xs font-semibold ${DECISION_CLS[rp.decision] ?? 'bg-gray-100 text-gray-600'}`}>
                    {translate.caseDecision(rp.decision)}
                  </span>
                ) : '—'
              } />
              <MetaField label="Formato" value={rp.format} />
              <MetaField label="PDF" value={rp.pdf_available ? 'Disponível' : 'Não gerado'} />
              <MetaField label="Gerado em" value={rp.created_at ? new Date(rp.created_at).toLocaleString('pt-BR') : '—'} />
              <MetaField label="Submetido em" value={rp.filed_at ? new Date(rp.filed_at).toLocaleString('pt-BR') : '—'} />
              <MetaField label="Protocolo Coaf" value={rp.coaf_protocol_number ?? '—'} mono />
            </div>
          </section>

          {/* Chain of custody link */}
          <section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
            <h2 className="mb-2 text-sm font-semibold text-gray-700">Cadeia de Custódia</h2>
            <p className="mb-3 text-xs text-gray-500">
              Verificação de integridade e histórico de auditoria do dossiê.
            </p>
            <Link
              href={`/cases/${rp.case_id}?tab=decision`}
              className="inline-flex items-center gap-2 rounded-lg border border-brand px-4 py-2 text-sm font-semibold text-brand hover:bg-brand/5"
            >
              Ver caso completo com cadeia de custódia →
            </Link>
          </section>
        </>
      )}
    </div>
  );
}

function MetaField({ label, value, mono }: { label: string; value: React.ReactNode; mono?: boolean }) {
  return (
    <div className="rounded-lg border border-gray-100 bg-gray-50 p-3">
      <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-gray-400">{label}</p>
      <div className={`text-sm text-gray-800 ${mono ? 'font-mono break-all' : ''}`}>{value}</div>
    </div>
  );
}
