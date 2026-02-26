'use client';

import { useState, useCallback } from 'react';
import { getCases, getCase, createCase, addCaseEvent } from '@/lib/api';
import type { Case, CaseFilters, CaseEvent, PaginatedResponse, CreateCasePayload, AddCaseEventPayload } from '@/lib/types';

export function useCases() {
  const [data, setData] = useState<PaginatedResponse<Case> | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchCases = useCallback(async (filters: CaseFilters = {}) => {
    setLoading(true);
    setError(null);
    try {
      const result = await getCases(filters);
      setData(result);
    } catch {
      setError('Failed to load cases.');
    } finally {
      setLoading(false);
    }
  }, []);

  const create = useCallback(async (payload: CreateCasePayload): Promise<Case> => {
    const result = await createCase(payload);
    return result;
  }, []);

  return { data, loading, error, fetchCases, create };
}

export function useCase(id: string) {
  const [caseData, setCaseData] = useState<Case | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchCase = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await getCase(id);
      setCaseData(result);
    } catch {
      setError('Failed to load case.');
    } finally {
      setLoading(false);
    }
  }, [id]);

  const addEvent = useCallback(async (payload: AddCaseEventPayload): Promise<CaseEvent> => {
    const event = await addCaseEvent(id, payload);
    setCaseData((prev) => prev ? { ...prev, events: [...(prev.events ?? []), event] } : prev);
    return event;
  }, [id]);

  return { caseData, loading, error, fetchCase, addEvent };
}
