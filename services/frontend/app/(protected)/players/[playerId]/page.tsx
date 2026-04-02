'use client';
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useParams, useRouter } from 'next/navigation';
import {
  erasePlayerData,
  fetchPlayerDataExport,
  fetchLatestPlayerExternalValidation,
  fetchPlayerExternalValidationHistory,
  fetchPlayer,
  fetchPlayerCaseAlertHistory,
  fetchPlayerEconCompat,
  fetchPlayerNetwork,
  PlayerDetail,
  PlayerDataExport,
  EconCompat,
  requestPlayerRightToErasure,
  retryExternalValidation,
  requestPlayerExternalValidation,
} from '@/lib/api';
import { useCurrentUser } from '@/hooks/useCurrentUser';
import PlayerNetworkGraph from '@/components/PlayerNetworkGraph';

const BAND_COLOR: Record<string, string> = {
  HIGH:   'bg-red-100 text-red-700 border-red-200',
  MEDIUM: 'bg-yellow-100 text-yellow-700 border-yellow-200',
  LOW:    'bg-green-100 text-green-700 border-green-200',
};

const TIER_COLOR: Record<string, string> = {
  RED:     'bg-red-100 text-red-700',
  YELLOW:  'bg-yellow-100 text-yellow-700',
  GREEN:   'bg-green-100 text-green-700',
  UNKNOWN: 'bg-gray-100 text-gray-500',
};

type Tab = 'profile' | 'econ' | 'network';

function EconCompatPanel({ player_id }: { player_id: string }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['econcompat', player_id],
    queryFn:  () => fetchPlayerEconCompat(player_id),
  });

  if (isLoading) return <p className="text-sm text-gray-400">Carregando análise econômica…</p>;
  if (error)     return <p className="text-sm text-red-600">Não foi possível carregar (requer papel AML_ANALYST ou ADMIN).</p>;
  if (!data)     return null;

  const ec = data as EconCompat;

  const ratio = ec.income_ratio_30d;
  const pct   = ratio != null ? (ratio * 100).toFixed(1) : null;

  return (
    <div className="space-y-4">
      {/* Tier badge */}
      <div className="flex items-center gap-3">
        <span className={`rounded-full px-4 py-1.5 text-sm font-bold ${TIER_COLOR[ec.tier] ?? 'bg-gray-100 text-gray-600'}`}>
          {ec.tier}
        </span>
        <span className="text-sm text-gray-600">{ec.interpretation}</span>
      </div>

      {/* Gauge simples */}
      {ratio != null && (
        <div>
          <div className="mb-1 flex justify-between text-xs text-gray-500">
            <span>Razão Depósito/Renda 30d</span>
            <span className="font-semibold">{ratio.toFixed(2)}x (threshold: {ec.ratio_threshold}x)</span>
          </div>
          <div className="h-3 w-full rounded-full bg-gray-100">
            <div
              className={`h-3 rounded-full transition-all ${
                ec.tier === 'RED' ? 'bg-red-500' : ec.tier === 'YELLOW' ? 'bg-yellow-400' : 'bg-green-500'
              }`}
              style={{ width: `${Math.min(100, (ratio / (ec.ratio_threshold * 3)) * 100).toFixed(0)}%` }}
            />
          </div>
        </div>
      )}

      {/* Números */}
      <dl className="grid grid-cols-2 gap-4 text-sm">
        <div>
          <dt className="text-gray-500">Renda declarada/mês</dt>
          <dd className="font-semibold">
            {ec.declared_income_monthly != null
              ? `R$ ${ec.declared_income_monthly.toFixed(2)}`
              : <span className="text-gray-400">Não informada</span>}
          </dd>
        </div>
        <div>
          <dt className="text-gray-500">Depósitos 30d</dt>
          <dd className="font-semibold">R$ {ec.deposit_sum_30d.toFixed(2)}</dd>
        </div>
        <div>
          <dt className="text-gray-500">Razão income 30d</dt>
          <dd className={`font-bold ${ec.tier === 'RED' ? 'text-red-600' : ec.tier === 'YELLOW' ? 'text-yellow-600' : 'text-green-600'}`}>
            {ratio != null ? `${ratio.toFixed(2)}x` : '—'}
          </dd>
        </div>
        <div>
          <dt className="text-gray-500">% vs threshold</dt>
          <dd className="font-semibold">{pct != null ? `${pct}%` : '—'}</dd>
        </div>
      </dl>
    </div>
  );
}

function NetworkPanel({ player_id }: { player_id: string }) {
  const { data: networkData, isLoading: isNetworkLoading, error: networkError } = useQuery({
    queryKey: ['player-network', player_id],
    queryFn: () => fetchPlayerNetwork(player_id),
  });

  const { data: historyData, isLoading: isHistoryLoading } = useQuery({
    queryKey: ['player-case-alert-history', player_id],
    queryFn: () => fetchPlayerCaseAlertHistory(player_id),
  });

  if (isNetworkLoading || isHistoryLoading) {
    return <p className="text-sm text-gray-400">Carregando vínculos de rede e histórico investigativo…</p>;
  }

  if (networkError || !networkData) {
    return <p className="text-sm text-red-600">Não foi possível carregar a análise de rede deste jogador.</p>;
  }

  const related = networkData.related_players ?? [];
  const cases = historyData?.cases ?? [];
  const alerts = historyData?.alerts ?? [];

  return (
    <div className="space-y-4">
      <PlayerNetworkGraph playerId={player_id} relatedPlayers={related} />

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <div className="rounded-xl border border-gray-200 p-4">
          <h4 className="text-sm font-semibold text-gray-900">Relações Detectadas</h4>
          <div className="mt-3 space-y-2">
            {related.length === 0 ? (
              <p className="text-sm text-gray-500">Sem relações correlatas no momento.</p>
            ) : (
              related.slice(0, 8).map((item) => (
                <div key={item.player_id} className="rounded-lg bg-gray-50 p-2">
                  <p className="text-xs font-semibold text-gray-900">{item.player_id}</p>
                  <p className="mt-1 text-xs text-gray-600">
                    {item.shared_by.map((r) => `${r.type}:${r.value}`).join(' • ')}
                  </p>
                </div>
              ))
            )}
          </div>
        </div>

        <div className="rounded-xl border border-gray-200 p-4">
          <h4 className="text-sm font-semibold text-gray-900">Contexto Investigativo</h4>
          <div className="mt-3 space-y-2 text-xs text-gray-700">
            <p>
              <span className="font-semibold">Casos:</span> {cases.length}
            </p>
            <p>
              <span className="font-semibold">Alertas:</span> {alerts.length}
            </p>
            {alerts.slice(0, 4).map((a) => (
              <div key={a.id} className="rounded-lg bg-gray-50 p-2">
                <p className="font-medium text-gray-900">{a.title}</p>
                <p className="text-gray-600">{a.severity} • {a.status}</p>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

export default function PlayerDetailPage() {
  const { playerId } = useParams<{ playerId: string }>();
  const router        = useRouter();
  const [tab, setTab] = useState<Tab>('profile');
  const [historyStatus, setHistoryStatus] = useState<string>('');
  const [historyProvider, setHistoryProvider] = useState<string>('');
  const [erasureReason, setErasureReason] = useState<string>('Solicitação de titular (LGPD Art. 18)');
  const { user: currentUser, hasAnyRole } = useCurrentUser();
  const queryClient = useQueryClient();

  const { data, isLoading, error } = useQuery({
    queryKey: ['player', playerId],
    queryFn:  () => fetchPlayer(playerId),
    enabled:  !!playerId,
  });
  const p = (data as PlayerDetail | undefined) ?? null;

  const { data: extLatest } = useQuery({
    queryKey: ['player-external-validation-latest', playerId],
    queryFn: () => fetchLatestPlayerExternalValidation(playerId),
    enabled: !!playerId,
    retry: false,
    refetchInterval: (q) => (q.state.data?.status === 'PENDING' ? 2000 : false),
  });

  const { data: extHistory } = useQuery({
    queryKey: ['player-external-validation-history', playerId, historyStatus, historyProvider],
    queryFn: () => fetchPlayerExternalValidationHistory(playerId, 5, 0, {
      status: historyStatus || undefined,
      provider: historyProvider || undefined,
    }),
    enabled: !!playerId,
    retry: false,
  });

  const validationMutation = useMutation({
    mutationFn: () =>
      requestPlayerExternalValidation(playerId, {
        provider: 'mock_identity',
        validation_type: 'CPF_IDENTITY',
        payload: { trigger: 'manual_player_screen' },
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['player-external-validation-latest', playerId] });
      await queryClient.invalidateQueries({ queryKey: ['player-external-validation-history', playerId] });
    },
  });

  const retryMutation = useMutation({
    mutationFn: (requestId: string) => retryExternalValidation(requestId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['player-external-validation-latest', playerId] });
      await queryClient.invalidateQueries({ queryKey: ['player-external-validation-history', playerId] });
    },
  });

  const lgpdExportMutation = useMutation({
    mutationFn: () => fetchPlayerDataExport(playerId),
  });

  const eraseMutation = useMutation({
    mutationFn: () => erasePlayerData(playerId, erasureReason),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['player', playerId] });
      await queryClient.invalidateQueries({ queryKey: ['players'] });
    },
  });

  const rightToErasureMutation = useMutation({
    mutationFn: () => requestPlayerRightToErasure(playerId, erasureReason),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['player', playerId] });
      await queryClient.invalidateQueries({ queryKey: ['players'] });
    },
  });

  const downloadDataExport = (payload: PlayerDataExport) => {
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `player-data-export-${payload.player_id}-${payload.export_id}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const canRequestLgpdExport = hasAnyRole(['Operador_Analista', 'Operador_Gestor', 'BetAML_SuperAdmin']);
  const canErasePlayer = hasAnyRole(['Operador_Gestor', 'BetAML_SuperAdmin']);

  if (isLoading) return <p className="text-sm text-gray-400">Carregando perfil…</p>;
  if (error)     return <p className="text-sm text-red-600">Player não encontrado.</p>;
  if (!p)        return null;

  return (
    <div className="max-w-2xl space-y-6">
      <button onClick={() => router.back()} className="text-sm text-brand hover:underline">
        ← Voltar para Jogadores
      </button>

      {/* Header */}
      <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="text-xs font-mono text-gray-400">{p.id}</p>
            <p className="text-lg font-bold">{p.external_player_id}</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <span className={`rounded border px-3 py-1 text-xs font-bold ${BAND_COLOR[p.risk_band] ?? 'bg-gray-100 text-gray-600'}`}>
              {p.risk_band}
            </span>
            {p.pep_flag && (
              <span className="rounded border border-red-200 bg-red-100 px-3 py-1 text-xs font-bold text-red-700">
                PEP
              </span>
            )}
          </div>
        </div>

        <div className="mt-4 grid grid-cols-2 gap-4 text-sm">
          <div>
            <dt className="text-gray-500">CPF</dt>
            <dd className="font-mono font-medium">
              {p.cpf}
              {/* Analistas só veem o CPF mascarado */}
              {currentUser?.roles?.includes('Operador_Analista') && !currentUser?.roles?.includes('Operador_Gestor') && (
                <span className="ml-2 rounded bg-yellow-100 px-1.5 py-0.5 text-[10px] font-semibold text-yellow-700">
                  MASCARADO
                </span>
              )}
            </dd>
          </div>
          <div>
            <dt className="text-gray-500">Score de Risco</dt>
            <dd>
              <span className={`font-bold text-lg ${
                p.risk_score >= 0.7 ? 'text-red-600' : p.risk_score >= 0.35 ? 'text-yellow-600' : 'text-green-600'
              }`}>
                {(p.risk_score * 100).toFixed(0)}%
              </span>
            </dd>
          </div>
          <div>
            <dt className="text-gray-500">Renda declarada/mês</dt>
            <dd className="font-medium">
              {p.declared_income_monthly != null
                ? `R$ ${p.declared_income_monthly.toFixed(2)}`
                : <span className="text-gray-400">—</span>}
            </dd>
          </div>
          <div>
            <dt className="text-gray-500">Último scoring</dt>
            <dd className="font-medium">
              {p.last_scored_at ? new Date(p.last_scored_at).toLocaleString('pt-BR') : '—'}
            </dd>
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className="rounded-xl border border-gray-200 bg-white shadow-sm">
        <div className="flex border-b border-gray-200">
          {(['profile', 'econ', 'network'] as Tab[]).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-5 py-3 text-sm font-medium transition-colors ${
                tab === t
                  ? 'border-b-2 border-brand text-brand'
                  : 'text-gray-500 hover:text-gray-700'
              }`}
            >
              {t === 'profile'
                ? 'Perfil'
                : t === 'econ'
                  ? 'Compatibilidade Econômica'
                  : 'Rede & Contexto'}
            </button>
          ))}
        </div>

        <div className="p-5">
          {tab === 'profile' && (
            <div className="space-y-2 text-sm text-gray-600">
              <p>Use a aba <strong>Compatibilidade Econômica</strong> para verificar a razão
                depósito/renda dos últimos 30 dias (COAF Res. 40/2021).</p>
              <p>A banda de risco (<strong>{p.risk_band}</strong>) é atualizada automaticamente
                após cada alerta disparado pelo motor de regras.</p>

              <div className="mt-4 rounded-xl border border-gray-200 p-4">
                <div className="flex items-center justify-between gap-3">
                  <h4 className="text-sm font-semibold text-gray-900">Validação Externa de Identidade</h4>
                  <button
                    onClick={() => validationMutation.mutate()}
                    disabled={validationMutation.isPending}
                    className="rounded-lg bg-brand px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-50"
                  >
                    {validationMutation.isPending ? 'Solicitando...' : 'Validar CPF (externo)'}
                  </button>
                </div>

                <div className="mt-3 text-xs text-gray-700">
                  <p>
                    <span className="font-semibold">Último status:</span>{' '}
                    <span className={`inline-flex rounded px-2 py-0.5 font-semibold ${
                      extLatest?.status === 'COMPLETED'
                        ? 'bg-green-100 text-green-700'
                        : extLatest?.status === 'FAILED'
                          ? 'bg-red-100 text-red-700'
                          : extLatest?.status === 'IN_PROGRESS' || extLatest?.status === 'PENDING'
                            ? 'bg-yellow-100 text-yellow-700'
                            : 'bg-gray-100 text-gray-600'
                    }`}>
                      {extLatest?.status ?? 'SEM VALIDAÇÃO'}
                    </span>
                  </p>
                  {extLatest?.completed_at && (
                    <p>
                      <span className="font-semibold">Concluído em:</span>{' '}
                      {new Date(extLatest.completed_at).toLocaleString('pt-BR')}
                    </p>
                  )}
                  {typeof extLatest?.response?.latency_ms === 'number' && (
                    <p>
                      <span className="font-semibold">Latência:</span>{' '}
                      {String(extLatest.response.latency_ms)} ms
                    </p>
                  )}
                  {typeof extLatest?.response?.retries_count === 'number' && (
                    <p>
                      <span className="font-semibold">Retries:</span>{' '}
                      {String(extLatest.response.retries_count)}
                    </p>
                  )}
                  {(extLatest?.status === 'PENDING' || extLatest?.status === 'IN_PROGRESS') && (
                    <p className="mt-1 text-yellow-700">Processamento em andamento; atualização automática ativa.</p>
                  )}
                </div>

                {extLatest?.status === 'FAILED' && extLatest.request_id && (
                  <div className="mt-3">
                    <button
                      onClick={() => retryMutation.mutate(extLatest.request_id)}
                      disabled={retryMutation.isPending}
                      className="rounded-md border border-red-200 bg-red-50 px-3 py-1.5 text-xs font-semibold text-red-700 disabled:opacity-50"
                    >
                      {retryMutation.isPending ? 'Reprocessando...' : 'Reprocessar validação falha'}
                    </button>
                  </div>
                )}

                {(extHistory?.items?.length ?? 0) > 0 && (
                  <div className="mt-3 space-y-1 text-xs">
                    <div className="mb-2 grid grid-cols-2 gap-2">
                      <select
                        value={historyStatus}
                        onChange={(e) => setHistoryStatus(e.target.value)}
                        className="rounded border border-gray-200 bg-white px-2 py-1 text-xs"
                      >
                        <option value="">Status: todos</option>
                        <option value="PENDING">PENDING</option>
                        <option value="IN_PROGRESS">IN_PROGRESS</option>
                        <option value="COMPLETED">COMPLETED</option>
                        <option value="FAILED">FAILED</option>
                      </select>
                      <select
                        value={historyProvider}
                        onChange={(e) => setHistoryProvider(e.target.value)}
                        className="rounded border border-gray-200 bg-white px-2 py-1 text-xs"
                      >
                        <option value="">Provider: todos</option>
                        <option value="mock_identity">mock_identity</option>
                      </select>
                    </div>
                    {extHistory!.items.map((it) => (
                      <div key={it.request_id} className="rounded bg-gray-50 px-2 py-1">
                        {it.provider} • {it.validation_type} • <span className="font-semibold">{it.status}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {(canRequestLgpdExport || canErasePlayer) && (
                <div className="mt-4 rounded-xl border border-gray-200 p-4">
                  <h4 className="text-sm font-semibold text-gray-900">LGPD e Governança de Dados</h4>
                  <p className="mt-1 text-xs text-gray-600">
                    Execute exportação de dados pessoais (Art. 18) e, para perfis autorizados,
                    a anonimização irreversível do titular.
                  </p>

                  <div className="mt-3 grid gap-3 md:grid-cols-2">
                    {canRequestLgpdExport && (
                      <button
                        onClick={async () => {
                          const payload = await lgpdExportMutation.mutateAsync();
                          downloadDataExport(payload);
                        }}
                        disabled={lgpdExportMutation.isPending}
                        className="rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-xs font-semibold text-blue-700 disabled:opacity-50"
                      >
                        {lgpdExportMutation.isPending ? 'Gerando exportação...' : 'Baixar Data Export (JSON)'}
                      </button>
                    )}

                    {canErasePlayer && (
                      <div className="space-y-2">
                        <input
                          value={erasureReason}
                          onChange={(e) => setErasureReason(e.target.value)}
                          placeholder="Motivo da solicitação LGPD"
                          className="w-full rounded-lg border border-gray-200 px-3 py-2 text-xs focus:outline-none focus:ring-2 focus:ring-red-200"
                        />
                        <div className="flex flex-wrap gap-2">
                          <button
                            onClick={() => {
                              if (!window.confirm('Confirma anonimização irreversível do player?')) return;
                              eraseMutation.mutate();
                            }}
                            disabled={eraseMutation.isPending}
                            className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs font-semibold text-red-700 disabled:opacity-50"
                          >
                            {eraseMutation.isPending ? 'Anonimizando...' : 'Anonimizar (erase)'}
                          </button>
                          <button
                            onClick={() => {
                              if (!window.confirm('Confirma right-to-erasure para este player?')) return;
                              rightToErasureMutation.mutate();
                            }}
                            disabled={rightToErasureMutation.isPending}
                            className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-semibold text-amber-700 disabled:opacity-50"
                          >
                            {rightToErasureMutation.isPending ? 'Processando...' : 'Right to Erasure (alias)'}
                          </button>
                        </div>
                      </div>
                    )}
                  </div>

                  {(eraseMutation.data || rightToErasureMutation.data) && (
                    <p className="mt-3 text-xs text-green-700">
                      {(eraseMutation.data ?? rightToErasureMutation.data)?.message}
                    </p>
                  )}

                  {(lgpdExportMutation.isError || eraseMutation.isError || rightToErasureMutation.isError) && (
                    <p className="mt-3 text-xs text-red-700">
                      Falha ao executar ação LGPD. Verifique permissões e tente novamente.
                    </p>
                  )}
                </div>
              )}
            </div>
          )}
          {tab === 'econ' && <EconCompatPanel player_id={playerId} />}
          {tab === 'network' && <NetworkPanel player_id={playerId} />}
        </div>
      </div>
    </div>
  );
}
