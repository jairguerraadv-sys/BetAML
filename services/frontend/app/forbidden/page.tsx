/**
 * app/forbidden/page.tsx
 *
 * Página exibida quando o usuário tenta acessar uma rota para a qual
 * não tem papel suficiente. Equivalente a um erro HTTP 403.
 */
'use client';

import Link from 'next/link';
import { ShieldOff } from 'lucide-react';

export default function ForbiddenPage() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-gray-50 px-4 text-center dark:bg-gray-900">
      <div className="flex h-16 w-16 items-center justify-center rounded-full bg-red-100 dark:bg-red-900/30">
        <ShieldOff size={32} className="text-red-600 dark:text-red-400" />
      </div>

      <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Acesso não autorizado</h1>
      <p className="max-w-sm text-sm text-gray-500 dark:text-gray-400">
        Você não tem permissão para acessar esta página. Entre em contato com o
        administrador do sistema se acreditar que isso é um erro.
      </p>

      <div className="flex gap-3">
        <Link
          href="/dashboard"
          className="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white transition hover:opacity-90"
        >
          Ir para o painel
        </Link>
        <Link
          href="/login"
          className="rounded-lg border border-gray-200 bg-white px-4 py-2 text-sm font-medium text-gray-700 transition hover:bg-gray-50 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-300 dark:hover:bg-gray-700"
        >
          Trocar de conta
        </Link>
      </div>
    </div>
  );
}
