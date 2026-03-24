'use client';

import { useEffect, useMemo, useState } from 'react';

export interface IngestStreamEvent {
  type: string;
  count: number;
  ts: string;
  summary?: {
    active_jobs: number;
    failed_jobs_24h: number;
    unresolved_errors: number;
    quarantine_breakdown?: Array<{
      source_system: string;
      entity_type: string | null;
      count: number;
    }>;
    configured_rate_limit_per_min: number;
    ws_active_connections: number;
    ws_queued_messages: number;
    ws_peak_queue_depth: number;
    ws_backpressure_events: number;
    ws_max_queue_size: number;
    ws_last_backpressure_at: string | null;
    latest_job_id: string | null;
    latest_job_status: string | null;
    latest_source_system: string | null;
    latest_job_updated_at: string | null;
    recent_failed_jobs?: Array<{
      id: string;
      source_system: string;
      status: string;
      failed_records: number | null;
      updated_at: string | null;
    }>;
  };
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
