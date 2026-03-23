'use client';
import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  fetchHealthStatus,
  fetchAmlKpis,
  fetchOpsSummary,
  fetchSystemFlags,
  toggleMaintenanceMode,
  HealthStatus,
} from '@/lib/api';
import { clsx } from 'clsx';

// ── Helpers ───────────────────────────────────────────────────────────────────

function StatusDot({ status }: { status: string }) {
  return (
    <span
      className={clsx(
        'inline-block h-2.5 w-2.5 rounded-full',
        status === 'ok' ? 'bg-green-500' : 'bg-red-500',
      )}
    />
  );
}

function ServiceCard({ name, status }: { name: string; status: string }) {
  return (
    <div className="flex items-center justify-between rounded-lg border border-gray-200 p-3 dark:border-gray-700">
      <span className="text-sm font-medium capitalize text-gray-700 dark:text-gray-300">
        {name.replace('_', ' ')}
      </span>
      <div className="flex items-center gap-1.5">
        <StatusDot status={status} />
        <span
          className={clsx(
            'text-xs font-semibold',
            status === 'ok' ? 'text-green-600' : 'text-red-600',
          )}
        >
          {status.toUpperCase()}
        </span>
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function OpsPage() {
  const qc = useQueryClient();
  const [maintenanceEnabled, setMaintenanceEnabled] = useState<boolean | null>(null);

  const { data: health, isLoading: healthLoading } = useQuery<HealthStatus>({
    queryKey: ['health-ready'],
    queryFn: fetchHealthStatus,
    refetchInterval: 30_000,
    retry: false,
  });

  const { data: kpis } = useQuery({
    queryKey: ['aml-kpis'],
    queryFn: fetchAmlKpis,
    refetchInterval: 30_000,
  });

  const { data: opsSummary } = useQuery({
    queryKey: ['ops-summary'],
    queryFn: fetchOpsSummary,
    refetchInterval: 30_000,
  });

  const { data: flags = [] } = useQuery({
    queryKey: ['system-flags'],
    queryFn: fetchSystemFlags,
    refetchInterval: 30_000,
  });

  useEffect(() => {
    if (maintenanceEnabled === null && flags.length > 0) {
      const flag = flags.find((f: { key: string; value: Record<string, unknown> }) =>
        f.key.endsWith(':maintenance_mode'),
      );
      if (flag) {
        setMaintenanceEnabled(Boolean(flag.value?.enabled));
      }
    }
  }, [flags, maintenanceEnabled]);

  const toggleMutation = useMutation({
    mutationFn: (enabled: boolean) => toggleMaintenanceMode(enabled),
    onSuccess: (data) => {
      setMaintenanceEnabled(data.maintenance_mode);
      qc.invalidateQueries({ queryKey: ['system-flags'] });
    },
  });

  // Derive operational alerts from health checks + KPIs
  const opAlerts: { label: string; severity: 'warn' | 'error' }[] = [];
  if (health?.checks.kafka !== 'ok') opAlerts.push({ label: 'Kafka broker inacessível', severity: 'error' });
  if (health?.checks.ml_service !== 'ok') opAlerts.push({ label: 'Serviço ML degradado', severity: 'warn' });
  if (health?.checks.rules_engine && health.checks.rules_engine !== 'ok') opAlerts.push({ label: 'Rules engine sem métricas/health', severity: 'warn' });
  if (health?.checks.stream_processor && health.checks.stream_processor !== 'ok') opAlerts.push({ label: 'Stream processor sem métricas/health', severity: 'warn' });
  if (health?.checks.redis !== 'ok') opAlerts.push({ label: 'Redis inacessível', severity: 'error' });
  if (health?.checks.minio !== 'ok') opAlerts.push({ label: 'MinIO inacessível', severity: 'warn' });
  const unresolvedErrors = typeof opsSummary?.unresolved_dlq_events === 'number' ? opsSummary.unresolved_dlq_events : 0;
  if (unresolvedErrors > 0) opAlerts.push({ label: `DLQ com ${unresolvedErrors} erros pendentes`, severity: 'warn' });
  const slaBreach = typeof kpis?.sla_breach_rate_open_cases_percent === 'number' ? Number(kpis.sla_breach_rate_open_cases_percent) : 0;
  if (slaBreach > 10) opAlerts.push({ label: `Taxa de violação de SLA: ${slaBreach}%`, severity: 'warn' });
  for (const alert of opsSummary?.alerts ?? []) {
    opAlerts.push({
      label: `${alert.message}${alert.value != null ? ` (${alert.value})` : ''}`,
      severity: alert.severity === 'critical' ? 'error' : 'warn',
    });
  }

  const serviceEntries = health
    ? (Object.entries(health.checks) as [string, string][])
    : [];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Operações</h1>
          <p className="text-sm text-gray-500 dark:text-gray-400">
            Saúde dos serviços, alertas infra e modo de manutenção
          </p>
        </div>
        <button
          onClick={() => qc.invalidateQueries()}
          className="rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-300"
        >
          Atualizar
        </button>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* ── Service Health ──────────────────────────────────────────────── */}
        <div className="rounded-xl border border-gray-200 bg-white p-5 dark:border-gray-700 dark:bg-gray-900">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-base font-semibold text-gray-800 dark:text-gray-200">
              Saúde dos Serviços
            </h2>
            {health && (
              <span
                className={clsx(
                  'rounded-full px-2.5 py-0.5 text-xs font-semibold',
                  health.status === 'ok'
                    ? 'bg-green-100 text-green-700'
                    : 'bg-red-100 text-red-700',
                )}
              >
                {health.status === 'ok' ? 'Operacional' : 'Degradado'}
              </span>
            )}
          </div>
          {healthLoading ? (
            <p className="text-sm text-gray-400">Verificando serviços…</p>
          ) : serviceEntries.length > 0 ? (
            <div className="grid gap-2 sm:grid-cols-2">
              {serviceEntries.map(([name, status]) => (
                <ServiceCard key={name} name={name} status={status} />
              ))}
            </div>
          ) : (
            <p className="text-sm text-gray-400">Serviço de saúde indisponível.</p>
          )}
          {health?.timestamp && (
            <p className="mt-2 text-[11px] text-gray-400">
              Verificado em {new Date(health.timestamp).toLocaleString('pt-BR')}
            </p>
          )}
        </div>

        {/* ── Operational Alerts ──────────────────────────────────────────── */}
        <div className="rounded-xl border border-gray-200 bg-white p-5 dark:border-gray-700 dark:bg-gray-900">
          <h2 className="mb-3 text-base font-semibold text-gray-800 dark:text-gray-200">
            Alertas Operacionais
          </h2>
          {opAlerts.length === 0 ? (
            <div className="flex items-center gap-2 rounded-lg bg-green-50 px-3 py-2 text-sm text-green-700 dark:bg-green-900/20">
              <span>✓</span>
              <span>Nenhum alerta operacional no momento.</span>
            </div>
          ) : (
            <ul className="space-y-2">
              {opAlerts.map((a) => (
                <li
                  key={a.label}
                  className={clsx(
                    'flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium',
                    a.severity === 'error'
                      ? 'bg-red-50 text-red-700 dark:bg-red-900/20'
                      : 'bg-amber-50 text-amber-700 dark:bg-amber-900/20',
                  )}
                >
                  <span>{a.severity === 'error' ? '⛔' : '⚠️'}</span>
                  {a.label}
                </li>
              ))}
            </ul>
          )}

          {/* AML KPIs mini-panel */}
          {kpis && (
            <div className="mt-4 grid grid-cols-2 gap-2 lg:grid-cols-4">
              <div className="rounded-lg bg-gray-50 p-3 dark:bg-gray-800">
                <p className="text-[10px] uppercase tracking-wider text-gray-400">Alertas abertos</p>
                <p className="text-xl font-bold text-gray-800 dark:text-white">
                  {String(kpis.alerts_open ?? '—')}
                </p>
              </div>
              <div className="rounded-lg bg-gray-50 p-3 dark:bg-gray-800">
                <p className="text-[10px] uppercase tracking-wider text-gray-400">Violação SLA</p>
                <p className="text-xl font-bold text-gray-800 dark:text-white">
                  {typeof kpis.sla_breach_rate_open_cases_percent === 'number'
                    ? `${kpis.sla_breach_rate_open_cases_percent}%`
                    : '—'}
                </p>
              </div>
              <div className="rounded-lg bg-gray-50 p-3 dark:bg-gray-800">
                <p className="text-[10px] uppercase tracking-wider text-gray-400">Lag Kafka</p>
                <p className="text-xl font-bold text-gray-800 dark:text-white">
                  {opsSummary?.kafka_consumer_lag ?? '—'}
                </p>
              </div>
              <div className="rounded-lg bg-gray-50 p-3 dark:bg-gray-800">
                <p className="text-[10px] uppercase tracking-wider text-gray-400">Erro ingestão 24h</p>
                <p className="text-xl font-bold text-gray-800 dark:text-white">
                  {typeof opsSummary?.ingest_error_rate_24h_percent === 'number'
                    ? `${opsSummary.ingest_error_rate_24h_percent}%`
                    : '—'}
                </p>
              </div>
            </div>
          )}
        </div>

        {/* ── Maintenance Mode ─────────────────────────────────────────────── */}
        <div className="rounded-xl border border-gray-200 bg-white p-5 dark:border-gray-700 dark:bg-gray-900 lg:col-span-2">
          <h2 className="mb-3 text-base font-semibold text-gray-800 dark:text-gray-200">
            Modo de Manutenção
          </h2>
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm text-gray-600 dark:text-gray-400">
                Quando ativado, a API retorna 503 para todas as rotas, exceto{' '}
                <code className="rounded bg-gray-100 px-1 dark:bg-gray-800">/health</code> e{' '}
                <code className="rounded bg-gray-100 px-1 dark:bg-gray-800">/auth</code>.
              </p>
              {maintenanceEnabled && (
                <p className="mt-1 text-sm font-semibold text-amber-600">
                  ⚠️ Manutenção ativa — usuários estão recebendo erro 503.
                </p>
              )}
              {opsSummary?.oldest_model_age_days != null && (
                <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
                  Modelo mais antigo sem re-treino: {opsSummary.oldest_model_age_days} dias.
                </p>
              )}
            </div>
            <button
              disabled={toggleMutation.isPending}
              onClick={() => toggleMutation.mutate(!maintenanceEnabled)}
              className={clsx(
                'min-w-[120px] rounded-lg px-4 py-2 text-sm font-semibold text-white transition-colors',
                maintenanceEnabled
                  ? 'bg-green-600 hover:bg-green-700'
                  : 'bg-amber-500 hover:bg-amber-600',
                toggleMutation.isPending && 'cursor-not-allowed opacity-50',
              )}
            >
              {toggleMutation.isPending
                ? 'Aguarde…'
                : maintenanceEnabled
                  ? 'Desativar manutenção'
                  : 'Ativar manutenção'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
