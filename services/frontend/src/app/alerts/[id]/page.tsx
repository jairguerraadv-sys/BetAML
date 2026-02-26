'use client';

import { useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { useAlert } from '@/hooks/useAlerts';
import { linkAlertToCase } from '@/lib/api';
import { useAuth } from '@/hooks/useAuth';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Modal } from '@/components/ui/modal';
import { Input } from '@/components/ui/input';
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from '@/components/ui/table';
import { formatDate, maskCpf, formatCpf, severityColor, alertStatusLabel, canSeeFull } from '@/lib/utils';
import { ArrowLeft, AlertTriangle } from 'lucide-react';

export default function AlertDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const { user } = useAuth();
  const { alert, loading, error, fetchAlert, triage, close } = useAlert(params.id);

  const [triageOpen, setTriageOpen] = useState(false);
  const [triageNote, setTriageNote] = useState('');
  const [triageLoading, setTriageLoading] = useState(false);

  const [linkOpen, setLinkOpen] = useState(false);
  const [caseId, setCaseId] = useState('');
  const [linkLoading, setLinkLoading] = useState(false);

  const [actionError, setActionError] = useState<string | null>(null);

  useEffect(() => {
    fetchAlert();
  }, [fetchAlert]);

  async function handleTriage() {
    setTriageLoading(true);
    setActionError(null);
    try {
      await triage(triageNote);
      setTriageOpen(false);
      setTriageNote('');
    } catch {
      setActionError('Failed to triage alert.');
    } finally {
      setTriageLoading(false);
    }
  }

  async function handleClose(verdict: 'TRUE_POSITIVE' | 'FALSE_POSITIVE') {
    setActionError(null);
    try {
      await close(verdict);
    } catch {
      setActionError('Failed to close alert.');
    }
  }

  async function handleLink() {
    setLinkLoading(true);
    setActionError(null);
    try {
      await linkAlertToCase(params.id, caseId);
      setLinkOpen(false);
      setCaseId('');
      await fetchAlert();
    } catch {
      setActionError('Failed to link alert to case.');
    } finally {
      setLinkLoading(false);
    }
  }

  if (loading) {
    return (
      <div className="flex justify-center py-24">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-blue-600 border-t-transparent" />
      </div>
    );
  }

  if (error || !alert) {
    return (
      <div className="py-12 text-center text-gray-500">
        {error ?? 'Alert not found.'}
      </div>
    );
  }

  const showFull = user ? canSeeFull(user.role) : false;
  const cpfDisplay = alert.player_cpf
    ? showFull
      ? formatCpf(alert.player_cpf)
      : maskCpf(alert.player_cpf)
    : '—';

  const isClosed = alert.status === 'CLOSED_TP' || alert.status === 'CLOSED_FP';

  return (
    <div className="space-y-6">
      {/* Back */}
      <button
        onClick={() => router.back()}
        className="flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700"
      >
        <ArrowLeft className="h-4 w-4" />
        Back to Alerts
      </button>

      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold text-gray-900">Alert Detail</h2>
          <p className="mt-1 font-mono text-sm text-gray-400">{alert.id}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge
            variant={severityColor(alert.severity) as 'danger' | 'warning' | 'success' | 'default'}
            className="text-sm px-3 py-1"
          >
            <AlertTriangle className="mr-1 h-3.5 w-3.5" />
            {alert.severity}
          </Badge>
          <Badge variant="default" className="text-sm px-3 py-1">
            {alertStatusLabel(alert.status)}
          </Badge>
        </div>
      </div>

      {actionError && (
        <div className="rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700">
          {actionError}
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        {/* Player info */}
        <Card>
          <CardHeader><CardTitle>Player Information</CardTitle></CardHeader>
          <CardContent>
            <dl className="space-y-3 text-sm">
              <div className="flex justify-between">
                <dt className="text-gray-500">Player ID</dt>
                <dd className="font-mono font-medium">{alert.player_id}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-gray-500">CPF</dt>
                <dd className="font-mono">{cpfDisplay}</dd>
              </div>
              {alert.case_id && (
                <div className="flex justify-between">
                  <dt className="text-gray-500">Linked Case</dt>
                  <dd>
                    <a href={`/cases/${alert.case_id}`} className="text-blue-600 hover:underline font-mono text-xs">
                      {alert.case_id.slice(0, 8)}…
                    </a>
                  </dd>
                </div>
              )}
              <div className="flex justify-between">
                <dt className="text-gray-500">Created</dt>
                <dd>{formatDate(alert.created_at)}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-gray-500">Rule</dt>
                <dd className="font-medium">{alert.rule_name}</dd>
              </div>
              {alert.anomaly_score !== undefined && (
                <div className="flex justify-between">
                  <dt className="text-gray-500">ML Anomaly Score</dt>
                  <dd className="font-bold text-red-600">{(alert.anomaly_score * 100).toFixed(2)}%</dd>
                </div>
              )}
            </dl>
          </CardContent>
        </Card>

        {/* Evidence */}
        <Card>
          <CardHeader><CardTitle>Evidence & Features</CardTitle></CardHeader>
          <CardContent>
            {alert.features && Object.keys(alert.features).length > 0 ? (
              <Table>
                <TableHead>
                  <TableRow>
                    <TableHeaderCell>Feature</TableHeaderCell>
                    <TableHeaderCell>Value</TableHeaderCell>
                    <TableHeaderCell>Threshold</TableHeaderCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {Object.entries(alert.features).map(([key, value]) => (
                    <TableRow key={key}>
                      <TableCell className="font-mono text-xs">{key}</TableCell>
                      <TableCell className="font-semibold">{typeof value === 'number' ? value.toFixed(4) : String(value)}</TableCell>
                      <TableCell className="text-gray-500">
                        {alert.thresholds?.[key] !== undefined
                          ? String(alert.thresholds[key])
                          : '—'}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            ) : (
              <p className="text-sm text-gray-400">No feature data available.</p>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Triage info */}
      {alert.triage_note && (
        <Card>
          <CardHeader><CardTitle>Triage Note</CardTitle></CardHeader>
          <CardContent>
            <p className="text-sm text-gray-700">{alert.triage_note}</p>
            <p className="mt-1 text-xs text-gray-400">
              by {alert.triaged_by} · {alert.triaged_at ? formatDate(alert.triaged_at) : ''}
            </p>
          </CardContent>
        </Card>
      )}

      {/* Actions */}
      {!isClosed && (
        <Card>
          <CardHeader><CardTitle>Actions</CardTitle></CardHeader>
          <CardContent>
            <div className="flex flex-wrap gap-3">
              {alert.status === 'OPEN' && (
                <Button variant="outline" onClick={() => setTriageOpen(true)}>
                  Triage
                </Button>
              )}
              <Button variant="primary" onClick={() => handleClose('TRUE_POSITIVE')}>
                Close as True Positive
              </Button>
              <Button variant="secondary" onClick={() => handleClose('FALSE_POSITIVE')}>
                Close as False Positive
              </Button>
              <Button variant="outline" onClick={() => setLinkOpen(true)}>
                Link to Case
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Triage modal */}
      <Modal open={triageOpen} onClose={() => setTriageOpen(false)} title="Triage Alert">
        <div className="space-y-4">
          <Input
            label="Note"
            value={triageNote}
            onChange={(e) => setTriageNote(e.target.value)}
            placeholder="Describe the reason for triaging..."
          />
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => setTriageOpen(false)}>Cancel</Button>
            <Button loading={triageLoading} onClick={handleTriage}>Confirm Triage</Button>
          </div>
        </div>
      </Modal>

      {/* Link case modal */}
      <Modal open={linkOpen} onClose={() => setLinkOpen(false)} title="Link to Case">
        <div className="space-y-4">
          <Input
            label="Case ID"
            value={caseId}
            onChange={(e) => setCaseId(e.target.value)}
            placeholder="Enter case UUID..."
          />
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => setLinkOpen(false)}>Cancel</Button>
            <Button loading={linkLoading} onClick={handleLink}>Link</Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
