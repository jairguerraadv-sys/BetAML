'use client';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { Bell, CheckCheck } from 'lucide-react';

interface Notification {
  id: string;
  title: string;
  body: string;
  is_read: boolean;
  created_at: string;
}

const fetchNotifications = () =>
  api.get<Notification[]>('/notifications').then((r) => r.data);

const markRead    = (id: string) => api.post(`/notifications/${id}/read`);
const markAllRead = ()           => api.post('/notifications/read-all');

export default function NotificationsPage() {
  const qc = useQueryClient();

  const { data: items = [], isLoading } = useQuery({
    queryKey: ['notifications'],
    queryFn: fetchNotifications,
    refetchInterval: 30_000,
  });

  const readOne  = useMutation({ mutationFn: markRead,    onSuccess: () => qc.invalidateQueries({ queryKey: ['notifications'] }) });
  const readAll  = useMutation({ mutationFn: markAllRead, onSuccess: () => qc.invalidateQueries({ queryKey: ['notifications'] }) });

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
            className="flex items-center gap-1 rounded-lg border border-gray-200 px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50"
          >
            <CheckCheck size={15} /> Marcar todas como lidas
          </button>
        )}
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
                  {new Date(n.created_at).toLocaleString('pt-BR')}
                </p>
              </div>
              {!n.is_read && (
                <button
                  onClick={() => readOne.mutate(n.id)}
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
