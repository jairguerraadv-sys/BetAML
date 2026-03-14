'use client';
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useParams, useRouter } from 'next/navigation';
import { fetchPlayer, fetchPlayerEconCompat, PlayerDetail, EconCompat } from '@/lib/api';
import { useCurrentUser } from '@/hooks/useCurrentUser';

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

type Tab = 'profile' | 'econ';

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

export default function PlayerDetailPage() {
  const { playerId } = useParams<{ playerId: string }>();
  const router        = useRouter();
  const [tab, setTab] = useState<Tab>('profile');
  const currentUser   = useCurrentUser();

  const { data, isLoading, error } = useQuery({
    queryKey: ['player', playerId],
    queryFn:  () => fetchPlayer(playerId),
    enabled:  !!playerId,
  });

  if (isLoading) return <p className="text-sm text-gray-400">Carregando perfil…</p>;
  if (error)     return <p className="text-sm text-red-600">Player não encontrado.</p>;
  if (!data)     return null;

  const p = data as PlayerDetail;

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
              {currentUser?.role === 'AUDITOR' && (
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
          {(['profile', 'econ'] as Tab[]).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-5 py-3 text-sm font-medium transition-colors ${
                tab === t
                  ? 'border-b-2 border-brand text-brand'
                  : 'text-gray-500 hover:text-gray-700'
              }`}
            >
              {t === 'profile' ? 'Perfil' : 'Compatibilidade Econômica'}
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
            </div>
          )}
          {tab === 'econ' && <EconCompatPanel player_id={playerId} />}
        </div>
      </div>
    </div>
  );
}
