'use client';

import { useState, useCallback } from 'react';
import { getAlerts, getAlert, triageAlert, closeAlert } from '@/lib/api';
import type { Alert, AlertFilters, PaginatedResponse } from '@/lib/types';

export function useAlerts() {
  const [data, setData] = useState<PaginatedResponse<Alert> | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchAlerts = useCallback(async (filters: AlertFilters = {}) => {
    setLoading(true);
    setError(null);
    try {
      const result = await getAlerts(filters);
      setData(result);
    } catch {
      setError('Failed to load alerts.');
    } finally {
      setLoading(false);
    }
  }, []);

  return { data, loading, error, fetchAlerts };
}

export function useAlert(id: string) {
  const [alert, setAlert] = useState<Alert | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchAlert = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await getAlert(id);
      setAlert(result);
    } catch {
      setError('Failed to load alert.');
    } finally {
      setLoading(false);
    }
  }, [id]);

  const triage = useCallback(async (note: string) => {
    const updated = await triageAlert(id, note);
    setAlert(updated);
  }, [id]);

  const close = useCallback(async (verdict: 'TRUE_POSITIVE' | 'FALSE_POSITIVE') => {
    const updated = await closeAlert(id, verdict);
    setAlert(updated);
  }, [id]);

  return { alert, loading, error, fetchAlert, triage, close };
}
