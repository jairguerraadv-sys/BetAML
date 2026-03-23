'use client';

import { useEffect, useMemo, useState } from 'react';

export interface IngestStreamEvent {
  type: string;
  count: number;
  ts: string;
}

export interface IngestStreamState {
  connected: boolean;
  reconnectCount: number;
  lastEvent: IngestStreamEvent | null;
  error: string | null;
}

export function useIngestStream(enabled = true): IngestStreamState {
  const [connected, setConnected] = useState(false);
  const [reconnectCount, setReconnectCount] = useState(0);
  const [lastEvent, setLastEvent] = useState<IngestStreamEvent | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled || typeof window === 'undefined') {
      return;
    }

    const source = new EventSource('/api-proxy/ingest/stream', { withCredentials: true });

    source.onopen = () => {
      setConnected(true);
      setError(null);
    };

    source.onmessage = (event) => {
      try {
        const parsed = JSON.parse(event.data) as IngestStreamEvent;
        setLastEvent(parsed);
        setConnected(true);
        setError(null);
      } catch {
        setError('Falha ao interpretar evento SSE');
      }
    };

    source.onerror = () => {
      setConnected(false);
      setReconnectCount((value) => value + 1);
      setError('Conexao SSE indisponivel, tentando reconectar');
    };

    return () => {
      source.close();
      setConnected(false);
    };
  }, [enabled]);

  return useMemo(
    () => ({
      connected,
      reconnectCount,
      lastEvent,
      error,
    }),
    [connected, reconnectCount, lastEvent, error],
  );
}
