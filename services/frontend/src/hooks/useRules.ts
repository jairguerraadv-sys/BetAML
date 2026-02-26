'use client';

import { useState, useCallback } from 'react';
import { getRules, createRule, updateRule, deleteRule, simulateRule } from '@/lib/api';
import type { Rule, CreateRulePayload, SimulateRulePayload, SimulateRuleResult } from '@/lib/types';

export function useRules() {
  const [rules, setRules] = useState<Rule[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchRules = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await getRules();
      setRules(result);
    } catch {
      setError('Failed to load rules.');
    } finally {
      setLoading(false);
    }
  }, []);

  const create = useCallback(async (payload: CreateRulePayload) => {
    const rule = await createRule(payload);
    setRules((prev) => [...prev, rule]);
    return rule;
  }, []);

  const update = useCallback(async (id: string, payload: Partial<CreateRulePayload>) => {
    const rule = await updateRule(id, payload);
    setRules((prev) => prev.map((r) => (r.id === id ? rule : r)));
    return rule;
  }, []);

  const remove = useCallback(async (id: string) => {
    await deleteRule(id);
    setRules((prev) => prev.filter((r) => r.id !== id));
  }, []);

  const simulate = useCallback(async (id: string, payload: SimulateRulePayload): Promise<SimulateRuleResult> => {
    return simulateRule(id, payload);
  }, []);

  return { rules, loading, error, fetchRules, create, update, remove, simulate };
}
