'use client';
import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Notification,
  fetchNotifications,
  markNotificationRead,
  markAllNotificationsRead,
} from '@/lib/api';
import { Bell, CheckCheck, Filter } from 'lucide-react';
import { useLocale, fmtDate } from '@/lib/i18n';

export default function NotificationsPage() {
  const qc = useQueryClient();
  const [locale] = useLocale();
  const [mode, setMode] = useState<'all' | 'unread'>('all');

  const { data: items = [], isLoading } = useQuery({
    queryKey: ['notifications', mode],
    queryFn: () => fetchNotifications(mode === 'unread'),
    refetchInterval: 30_000,
  });

  const readOne = useMutation({
    mutationFn: (id: string) => markNotificationRead(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['notifications'] }),
  });
  const readAll = useMutation({
    mutationFn: () => markAllNotificationsRead(),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['notifications'] }),
  });

  const unread = items.filter((n: Notification) => !n.is_read).length;

  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="flex items-center gap-2 text-2xl font-bold text-gray-900">
          <Bell size={22} /> Notificações
          {unread > 0 && (
            <span className="ml-1 rounded-full bg-brand px-2 py-0.5 text-xs font-semibold text-white">
              {unread}
            </span>
          )}
        </h1>
        {unread > 0 && (
          <button
            onClick={() => readAll.mutate()}
            aria-label="Marcar todas as notificações como lidas"
            className="flex items-center gap-1 rounded-lg border border-gray-200 px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50"
          >
            <CheckCheck size={15} /> Marcar todas como lidas
          </button>
        )}
      </div>

      <div className="flex items-center gap-2">
        <span className="flex items-center gap-1 text-xs font-semibold uppercase tracking-wide text-gray-400">
          <Filter size={13} /> Filtro
        </span>
        <button
          onClick={() => setMode('all')}
          aria-pressed={mode === 'all'}
          className={`rounded-full px-3 py-1 text-xs font-semibold ${
            mode === 'all' ? 'bg-brand text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
          }`}
        >
          Todas
        </button>
        <button
          onClick={() => setMode('unread')}
          aria-pressed={mode === 'unread'}
          className={`rounded-full px-3 py-1 text-xs font-semibold ${
            mode === 'unread' ? 'bg-brand text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
          }`}
        >
          Não lidas
        </button>
      </div>

      {isLoading && <p className="text-sm text-gray-500">Carregando…</p>}

      {items.length === 0 && !isLoading && (
        <div className="rounded-xl border border-dashed border-gray-200 py-16 text-center">
          <Bell size={32} className="mx-auto mb-3 text-gray-300" />
          <p className="text-sm text-gray-500">Nenhuma notificação</p>
        </div>
      )}

      <ul className="space-y-2">
        {items.map((n: Notification) => (
          <li
            key={n.id}
            className={`rounded-xl border px-4 py-3 transition-colors ${
              n.is_read ? 'border-gray-100 bg-white' : 'border-brand/20 bg-brand/5'
            }`}
          >
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className={`text-sm font-semibold ${n.is_read ? 'text-gray-700' : 'text-brand'}`}>
                  {n.title}
                </p>
                <p className="mt-0.5 text-sm text-gray-600">{n.body}</p>
                <p className="mt-1 text-xs text-gray-400">
                  {fmtDate(n.created_at, locale)}
                </p>
                <div className="mt-2 flex items-center gap-2">
                  <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-gray-600">
                    {n.type}
                  </span>
                  {n.reference_type && (
                    <span className="rounded-full bg-sky-50 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-sky-700">
                      {n.reference_type}
                    </span>
                  )}
                </div>
                {n.reference_type && n.reference_id && (
                  <a
                    href={
                      n.reference_type === 'Case'
                        ? `/cases/${n.reference_id}`
                        : n.reference_type === 'alert'
                        ? `/alerts/${n.reference_id}`
                        : '#'
                    }
                    className="mt-2 inline-block text-xs font-semibold text-brand hover:underline"
                  >
                    Abrir referência
                  </a>
                )}
              </div>
              {!n.is_read && (
                <button
                  onClick={() => readOne.mutate(n.id)}
                  aria-label={`Marcar notificação ${n.title} como lida`}
                  className="shrink-0 rounded px-2 py-1 text-xs text-brand hover:underline"
                >
                  Marcar lida
                </button>
              )}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
