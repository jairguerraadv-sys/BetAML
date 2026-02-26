'use client';

import { useEffect, useState } from 'react';
import { useRules } from '@/hooks/useRules';
import type { Rule, CreateRulePayload, RuleScope, RuleSeverity } from '@/lib/types';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input, Textarea, Select } from '@/components/ui/input';
import { Modal } from '@/components/ui/modal';
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from '@/components/ui/table';
import { formatDate } from '@/lib/utils';
import { Plus, Edit2, Trash2, Play } from 'lucide-react';

const emptyForm: CreateRulePayload = {
  name: '',
  description: '',
  scope: 'PLAYER',
  severity: 'MEDIUM',
  condition_dsl: '',
  params: {},
};

export default function RulesPage() {
  const { rules, loading, error, fetchRules, create, update, remove, simulate } = useRules();

  const [formOpen, setFormOpen] = useState(false);
  const [editingRule, setEditingRule] = useState<Rule | null>(null);
  const [form, setForm] = useState<CreateRulePayload>(emptyForm);
  const [paramsRaw, setParamsRaw] = useState('{}');
  const [paramsError, setParamsError] = useState('');
  const [formLoading, setFormLoading] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const [simOpen, setSimOpen] = useState(false);
  const [simRule, setSimRule] = useState<Rule | null>(null);
  const [simJson, setSimJson] = useState('{}');
  const [simResult, setSimResult] = useState<{ matched: boolean; score?: number; details?: Record<string, unknown> } | null>(null);
  const [simLoading, setSimLoading] = useState(false);
  const [simError, setSimError] = useState<string | null>(null);

  const [deleteLoading, setDeleteLoading] = useState<string | null>(null);

  useEffect(() => { fetchRules(); }, [fetchRules]);

  function openCreate() {
    setEditingRule(null);
    setForm(emptyForm);
    setParamsRaw('{}');
    setParamsError('');
    setFormError(null);
    setFormOpen(true);
  }

  function openEdit(rule: Rule) {
    setEditingRule(rule);
    setForm({
      name: rule.name,
      description: rule.description ?? '',
      scope: rule.scope,
      severity: rule.severity,
      condition_dsl: rule.condition_dsl,
      params: rule.params ?? {},
    });
    setParamsRaw(JSON.stringify(rule.params ?? {}, null, 2));
    setParamsError('');
    setFormError(null);
    setFormOpen(true);
  }

  function handleParamsChange(v: string) {
    setParamsRaw(v);
    try {
      JSON.parse(v);
      setParamsError('');
    } catch {
      setParamsError('Invalid JSON');
    }
  }

  async function handleSubmit() {
    if (paramsError) return;
    setFormLoading(true);
    setFormError(null);
    let params: Record<string, unknown> = {};
    try { params = JSON.parse(paramsRaw) as Record<string, unknown>; } catch { /* empty */ }
    const payload: CreateRulePayload = { ...form, params };
    try {
      if (editingRule) {
        await update(editingRule.id, payload);
      } else {
        await create(payload);
      }
      setFormOpen(false);
    } catch {
      setFormError('Failed to save rule.');
    } finally {
      setFormLoading(false);
    }
  }

  async function handleDelete(id: string) {
    if (!confirm('Delete this rule?')) return;
    setDeleteLoading(id);
    try { await remove(id); } catch { /* ignore */ } finally { setDeleteLoading(null); }
  }

  function openSim(rule: Rule) {
    setSimRule(rule);
    setSimJson('{}');
    setSimResult(null);
    setSimError(null);
    setSimOpen(true);
  }

  async function handleSimulate() {
    if (!simRule) return;
    let testEvent: Record<string, unknown> = {};
    try { testEvent = JSON.parse(simJson) as Record<string, unknown>; } catch {
      setSimError('Invalid JSON for test event.');
      return;
    }
    setSimLoading(true);
    setSimError(null);
    try {
      const result = await simulate(simRule.id, { test_event: testEvent });
      setSimResult(result);
    } catch {
      setSimError('Simulation failed.');
    } finally {
      setSimLoading(false);
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-bold text-gray-900">Rules</h2>
        <Button onClick={openCreate}>
          <Plus className="mr-2 h-4 w-4" />
          New Rule
        </Button>
      </div>

      {error && <p className="text-sm text-red-600">{error}</p>}

      <Card>
        <CardHeader>
          <CardTitle>{rules.length} rules</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {loading ? (
            <div className="flex justify-center py-12">
              <div className="h-6 w-6 animate-spin rounded-full border-4 border-blue-600 border-t-transparent" />
            </div>
          ) : (
            <Table>
              <TableHead>
                <TableRow>
                  <TableHeaderCell>Name</TableHeaderCell>
                  <TableHeaderCell>Scope</TableHeaderCell>
                  <TableHeaderCell>Severity</TableHeaderCell>
                  <TableHeaderCell>Status</TableHeaderCell>
                  <TableHeaderCell>Version</TableHeaderCell>
                  <TableHeaderCell>Created</TableHeaderCell>
                  <TableHeaderCell>Actions</TableHeaderCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {rules.length ? (
                  rules.map((rule) => (
                    <TableRow key={rule.id}>
                      <TableCell className="font-medium">{rule.name}</TableCell>
                      <TableCell>
                        <Badge variant="info">{rule.scope}</Badge>
                      </TableCell>
                      <TableCell>
                        <Badge variant={rule.severity === 'HIGH' || rule.severity === 'CRITICAL' ? 'danger' : rule.severity === 'MEDIUM' ? 'warning' : 'success'}>
                          {rule.severity}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        <Badge variant={rule.is_active ? 'success' : 'default'}>
                          {rule.is_active ? 'Active' : 'Inactive'}
                        </Badge>
                      </TableCell>
                      <TableCell className="font-mono text-xs">v{rule.version}</TableCell>
                      <TableCell className="text-xs text-gray-500">{formatDate(rule.created_at)}</TableCell>
                      <TableCell>
                        <div className="flex gap-2">
                          <button
                            onClick={() => openEdit(rule)}
                            className="rounded p-1 text-gray-400 hover:text-blue-600"
                            title="Edit"
                          >
                            <Edit2 className="h-4 w-4" />
                          </button>
                          <button
                            onClick={() => openSim(rule)}
                            className="rounded p-1 text-gray-400 hover:text-green-600"
                            title="Simulate"
                          >
                            <Play className="h-4 w-4" />
                          </button>
                          <button
                            onClick={() => handleDelete(rule.id)}
                            disabled={deleteLoading === rule.id}
                            className="rounded p-1 text-gray-400 hover:text-red-600 disabled:opacity-50"
                            title="Delete"
                          >
                            <Trash2 className="h-4 w-4" />
                          </button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))
                ) : (
                  <TableRow>
                    <TableCell colSpan={7} className="py-8 text-center text-gray-400">
                      No rules yet
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* Create / Edit Modal */}
      <Modal
        open={formOpen}
        onClose={() => setFormOpen(false)}
        title={editingRule ? 'Edit Rule' : 'Create Rule'}
        className="max-w-2xl"
      >
        <div className="space-y-4">
          {formError && (
            <p className="rounded bg-red-50 px-3 py-2 text-sm text-red-600">{formError}</p>
          )}
          <Input
            label="Name"
            value={form.name}
            onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
            required
          />
          <Input
            label="Description"
            value={form.description ?? ''}
            onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
          />
          <div className="grid grid-cols-2 gap-4">
            <Select
              label="Scope"
              value={form.scope}
              onChange={(e) => setForm((f) => ({ ...f, scope: e.target.value as RuleScope }))}
            >
              <option value="PLAYER">Player</option>
              <option value="TRANSACTION">Transaction</option>
              <option value="SESSION">Session</option>
              <option value="AGGREGATE">Aggregate</option>
            </Select>
            <Select
              label="Severity"
              value={form.severity}
              onChange={(e) => setForm((f) => ({ ...f, severity: e.target.value as RuleSeverity }))}
            >
              <option value="LOW">Low</option>
              <option value="MEDIUM">Medium</option>
              <option value="HIGH">High</option>
              <option value="CRITICAL">Critical</option>
            </Select>
          </div>
          <Textarea
            label="Condition DSL"
            value={form.condition_dsl}
            onChange={(e) => setForm((f) => ({ ...f, condition_dsl: e.target.value }))}
            rows={5}
            placeholder={`e.g.\namount > params.threshold\nAND player.risk_score > 0.7`}
          />
          <p className="text-xs text-gray-400">
            DSL supports: field access, comparisons (&gt;, &lt;, ==, !=), logical operators (AND, OR, NOT), and references to <code>params.*</code>.
          </p>
          <Textarea
            label="Params (JSON)"
            value={paramsRaw}
            onChange={(e) => handleParamsChange(e.target.value)}
            error={paramsError}
            rows={4}
            placeholder='{"threshold": 10000}'
          />
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => setFormOpen(false)}>Cancel</Button>
            <Button loading={formLoading} onClick={handleSubmit}>
              {editingRule ? 'Save Changes' : 'Create Rule'}
            </Button>
          </div>
        </div>
      </Modal>

      {/* Simulate Modal */}
      <Modal open={simOpen} onClose={() => setSimOpen(false)} title={`Simulate: ${simRule?.name ?? ''}`}>
        <div className="space-y-4">
          {simError && <p className="rounded bg-red-50 px-3 py-2 text-sm text-red-600">{simError}</p>}
          <Textarea
            label="Test Event (JSON)"
            value={simJson}
            onChange={(e) => setSimJson(e.target.value)}
            rows={6}
            placeholder='{"amount": 15000, "player_id": "abc"}'
          />
          {simResult && (
            <div className={`rounded-lg px-4 py-3 text-sm font-medium ${simResult.matched ? 'bg-red-50 text-red-700' : 'bg-green-50 text-green-700'}`}>
              {simResult.matched ? '🔴 Rule MATCHED' : '🟢 Rule did NOT match'}
              {simResult.score !== undefined && (
                <span className="ml-2 text-xs opacity-80">Score: {simResult.score.toFixed(4)}</span>
              )}
              {simResult.details && (
                <pre className="mt-2 text-xs overflow-x-auto">{JSON.stringify(simResult.details, null, 2)}</pre>
              )}
            </div>
          )}
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => setSimOpen(false)}>Close</Button>
            <Button variant="secondary" loading={simLoading} onClick={handleSimulate}>
              <Play className="mr-1 h-4 w-4" />
              Run Simulation
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
