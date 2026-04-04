'use client';

import { Suspense, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useMutation } from '@tanstack/react-query';
import { createCase, linkAlertToCase } from '@/lib/api';

function NewCasePageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const linkAlertId = searchParams.get('linkAlert') ?? '';
  const [title, setTitle] = useState(searchParams.get('title') ?? '');
  const [description, setDescription] = useState('');
  const [playerId, setPlayerId] = useState(searchParams.get('playerId') ?? '');
  const [severity, setSeverity] = useState('HIGH');

  const createMut = useMutation({
    mutationFn: async () => {
      const created = await createCase({
        title,
        description: description || undefined,
        player_id: playerId || undefined,
        severity,
      });
      if (linkAlertId) {
        await linkAlertToCase(created.id, linkAlertId);
      }
      return created;
    },
    onSuccess: (data) => {
      router.push(`/cases/${data.id}`);
    },
  });

  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Novo Caso</h1>
        <p className="text-sm text-gray-500">Abra uma investigação manual para um alerta ou apostador.</p>
        {linkAlertId && (
          <p className="mt-1 text-xs text-indigo-600">O alerta será vinculado automaticamente quando o caso for criado.</p>
        )}
      </div>

      <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm space-y-4">
        <div>
          <label htmlFor="case-title" className="mb-1 block text-sm font-medium text-gray-700">Título</label>
          <input
            id="case-title"
            aria-label="Título do caso"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
            placeholder="Ex.: Investigação de movimentação atípica"
          />
        </div>

        <div>
          <label htmlFor="case-description" className="mb-1 block text-sm font-medium text-gray-700">Descrição</label>
          <textarea
            id="case-description"
            aria-label="Descrição do caso"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={4}
            className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
            placeholder="Contexto inicial da investigação"
          />
        </div>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <label htmlFor="case-player-id" className="mb-1 block text-sm font-medium text-gray-700">Player ID (opcional)</label>
            <input
              id="case-player-id"
              aria-label="Player ID do caso"
              value={playerId}
              onChange={(e) => setPlayerId(e.target.value)}
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
              placeholder="UUID do player"
            />
          </div>
          <div>
            <label htmlFor="case-severity" className="mb-1 block text-sm font-medium text-gray-700">Severidade</label>
            <select
              id="case-severity"
              aria-label="Severidade do caso"
              value={severity}
              onChange={(e) => setSeverity(e.target.value)}
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
            >
              <option value="LOW">LOW</option>
              <option value="MEDIUM">MEDIUM</option>
              <option value="HIGH">HIGH</option>
              <option value="CRITICAL">CRITICAL</option>
            </select>
          </div>
        </div>

        {createMut.isError && (
          <p className="text-sm text-red-600">Falha ao criar caso. Verifique os dados e tente novamente.</p>
        )}

        <div className="flex gap-2">
          <button
            onClick={() => router.back()}
            className="rounded-lg border border-gray-300 px-4 py-2 text-sm"
          >
            Cancelar
          </button>
          <button
            onClick={() => createMut.mutate()}
            disabled={!title.trim() || createMut.isPending}
            aria-label="Criar caso"
            className="rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
          >
            {createMut.isPending ? 'Criando...' : 'Criar Caso'}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function NewCasePage() {
  return (
    <Suspense fallback={<div className="mx-auto max-w-2xl p-4 text-sm text-gray-500">Carregando formulario...</div>}>
      <NewCasePageContent />
    </Suspense>
  );
}
