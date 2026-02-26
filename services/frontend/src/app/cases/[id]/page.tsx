'use client';

import { useEffect, useRef, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { useCase } from '@/hooks/useCases';
import { assignCase, generateReportPackage, uploadEvidence } from '@/lib/api';
import { useAuth } from '@/hooks/useAuth';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/input';
import { Modal } from '@/components/ui/modal';
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from '@/components/ui/table';
import { formatDate, maskCpf, formatCpf, caseStatusLabel, canSeeFull } from '@/lib/utils';
import { ArrowLeft, User2, Clock, FileText, Paperclip } from 'lucide-react';
import type { CaseStatus, CaseEventType } from '@/lib/types';
import Link from 'next/link';

const statusBadge = (s: CaseStatus): 'default' | 'info' | 'warning' | 'danger' | 'success' => {
  const m: Record<CaseStatus, 'default' | 'info' | 'warning' | 'danger' | 'success'> = {
    OPEN: 'info',
    UNDER_REVIEW: 'warning',
    PENDING_COMPLIANCE: 'warning',
    CLOSED_SUBSTANTIATED: 'danger',
    CLOSED_UNSUBSTANTIATED: 'success',
  };
  return m[s] ?? 'default';
};

const eventIcon: Record<CaseEventType, React.ReactNode> = {
  NOTE: <FileText className="h-4 w-4 text-gray-400" />,
  STATUS_CHANGE: <Clock className="h-4 w-4 text-blue-400" />,
  ALERT_LINKED: <FileText className="h-4 w-4 text-purple-400" />,
  EVIDENCE_UPLOADED: <Paperclip className="h-4 w-4 text-green-400" />,
  REPORT_GENERATED: <FileText className="h-4 w-4 text-orange-400" />,
  ASSIGNMENT_CHANGE: <User2 className="h-4 w-4 text-indigo-400" />,
};

export default function CaseDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const { user } = useAuth();
  const { caseData, loading, error, fetchCase, addEvent } = useCase(params.id);

  // Add note
  const [noteText, setNoteText] = useState('');
  const [noteLoading, setNoteLoading] = useState(false);

  // Report modal
  const [reportOpen, setReportOpen] = useState(false);
  const [justification, setJustification] = useState('');
  const [reportLoading, setReportLoading] = useState(false);

  // Assign modal
  const [assignOpen, setAssignOpen] = useState(false);
  const [assignUserId, setAssignUserId] = useState('');
  const [assignLoading, setAssignLoading] = useState(false);

  // Evidence upload
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploadLoading, setUploadLoading] = useState(false);

  const [actionError, setActionError] = useState<string | null>(null);

  useEffect(() => { fetchCase(); }, [fetchCase]);

  async function handleAddNote() {
    if (!noteText.trim()) return;
    setNoteLoading(true);
    setActionError(null);
    try {
      await addEvent({ event_type: 'NOTE', content: noteText });
      setNoteText('');
    } catch {
      setActionError('Failed to add note.');
    } finally {
      setNoteLoading(false);
    }
  }

  async function handleReport() {
    setReportLoading(true);
    setActionError(null);
    try {
      const blob = await generateReportPackage(params.id, { justification });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `case-${params.id}-report.json`;
      a.click();
      URL.revokeObjectURL(url);
      setReportOpen(false);
      setJustification('');
    } catch {
      setActionError('Failed to generate report.');
    } finally {
      setReportLoading(false);
    }
  }

  async function handleAssign() {
    setAssignLoading(true);
    setActionError(null);
    try {
      await assignCase(params.id, assignUserId);
      await fetchCase();
      setAssignOpen(false);
    } catch {
      setActionError('Failed to assign case.');
    } finally {
      setAssignLoading(false);
    }
  }

  async function handleEvidenceUpload(files: FileList | null) {
    if (!files || files.length === 0) return;
    const file = files[0];
    setUploadLoading(true);
    setActionError(null);
    try {
      await uploadEvidence(params.id, file);
      await fetchCase();
    } catch {
      setActionError('Failed to upload evidence.');
    } finally {
      setUploadLoading(false);
    }
  }

  if (loading) {
    return (
      <div className="flex justify-center py-24">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-blue-600 border-t-transparent" />
      </div>
    );
  }

  if (error || !caseData) {
    return <div className="py-12 text-center text-gray-500">{error ?? 'Case not found.'}</div>;
  }

  const showFull = user ? canSeeFull(user.role) : false;
  const cpfDisplay = caseData.player_cpf
    ? showFull ? formatCpf(caseData.player_cpf) : maskCpf(caseData.player_cpf)
    : '—';

  return (
    <div className="space-y-6">
      <button
        onClick={() => router.back()}
        className="flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700"
      >
        <ArrowLeft className="h-4 w-4" />
        Back to Cases
      </button>

      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold text-gray-900">{caseData.title}</h2>
          <p className="mt-1 font-mono text-sm text-gray-400">{caseData.id}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={statusBadge(caseData.status)} className="text-sm px-3 py-1">
            {caseStatusLabel(caseData.status)}
          </Badge>
          <Button variant="outline" size="sm" onClick={() => setAssignOpen(true)}>
            <User2 className="mr-1 h-4 w-4" />
            Assign
          </Button>
          <Button variant="outline" size="sm" onClick={() => setReportOpen(true)}>
            <FileText className="mr-1 h-4 w-4" />
            Generate Report
          </Button>
          <Button
            variant="outline"
            size="sm"
            loading={uploadLoading}
            onClick={() => fileRef.current?.click()}
          >
            <Paperclip className="mr-1 h-4 w-4" />
            Upload Evidence
          </Button>
          <input
            ref={fileRef}
            type="file"
            className="hidden"
            onChange={(e) => handleEvidenceUpload(e.target.files)}
          />
        </div>
      </div>

      {actionError && (
        <div className="rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700">
          {actionError}
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        {/* Case info */}
        <Card>
          <CardHeader><CardTitle>Details</CardTitle></CardHeader>
          <CardContent>
            <dl className="space-y-3 text-sm">
              <div className="flex justify-between">
                <dt className="text-gray-500">Player ID</dt>
                <dd className="font-mono font-medium">{caseData.player_id}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-gray-500">CPF</dt>
                <dd className="font-mono">{cpfDisplay}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-gray-500">Assigned To</dt>
                <dd>{caseData.assigned_to_name ?? <span className="text-gray-400">Unassigned</span>}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-gray-500">Created By</dt>
                <dd>{caseData.created_by_name ?? caseData.created_by}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-gray-500">Created</dt>
                <dd>{formatDate(caseData.created_at)}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-gray-500">Updated</dt>
                <dd>{formatDate(caseData.updated_at)}</dd>
              </div>
            </dl>
            {caseData.description && (
              <p className="mt-4 text-sm text-gray-600 border-t pt-3">{caseData.description}</p>
            )}
          </CardContent>
        </Card>

        {/* Timeline */}
        <Card className="lg:col-span-2">
          <CardHeader><CardTitle>Timeline</CardTitle></CardHeader>
          <CardContent>
            {caseData.events && caseData.events.length > 0 ? (
              <ol className="relative border-l border-gray-200 space-y-6 pl-6">
                {[...caseData.events].reverse().map((ev) => (
                  <li key={ev.id} className="relative">
                    <span className="absolute -left-[1.625rem] flex h-7 w-7 items-center justify-center rounded-full bg-white ring-2 ring-gray-200">
                      {eventIcon[ev.event_type] ?? <Clock className="h-4 w-4 text-gray-400" />}
                    </span>
                    <div>
                      <p className="text-sm font-medium text-gray-900">{ev.content}</p>
                      <p className="mt-0.5 text-xs text-gray-400">
                        {ev.created_by_name ?? ev.created_by} · {formatDate(ev.created_at)}
                      </p>
                    </div>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="text-sm text-gray-400">No events yet.</p>
            )}

            {/* Add note */}
            <div className="mt-6 border-t pt-4 space-y-2">
              <Textarea
                label="Add Note"
                value={noteText}
                onChange={(e) => setNoteText(e.target.value)}
                placeholder="Enter a note..."
                rows={3}
              />
              <div className="flex justify-end">
                <Button size="sm" loading={noteLoading} onClick={handleAddNote}>
                  Add Note
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Linked alerts */}
      {caseData.alerts && caseData.alerts.length > 0 && (
        <Card>
          <CardHeader><CardTitle>Linked Alerts</CardTitle></CardHeader>
          <CardContent className="p-0">
            <Table>
              <TableHead>
                <TableRow>
                  <TableHeaderCell>ID</TableHeaderCell>
                  <TableHeaderCell>Rule</TableHeaderCell>
                  <TableHeaderCell>Severity</TableHeaderCell>
                  <TableHeaderCell>Status</TableHeaderCell>
                  <TableHeaderCell>Created</TableHeaderCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {caseData.alerts.map((a) => (
                  <TableRow key={a.id}>
                    <TableCell>
                      <Link href={`/alerts/${a.id}`} className="font-mono text-xs text-blue-600 hover:underline">
                        {a.id.slice(0, 8)}…
                      </Link>
                    </TableCell>
                    <TableCell>{a.rule_name}</TableCell>
                    <TableCell>
                      <Badge variant={a.severity === 'HIGH' || a.severity === 'CRITICAL' ? 'danger' : a.severity === 'MEDIUM' ? 'warning' : 'success'}>
                        {a.severity}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-xs">{a.status}</TableCell>
                    <TableCell className="text-xs text-gray-500">{formatDate(a.created_at)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      {/* Report modal */}
      <Modal open={reportOpen} onClose={() => setReportOpen(false)} title="Generate Report Package">
        <div className="space-y-4">
          <Textarea
            label="Justification"
            value={justification}
            onChange={(e) => setJustification(e.target.value)}
            placeholder="Describe the reason for this report..."
            rows={4}
          />
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => setReportOpen(false)}>Cancel</Button>
            <Button loading={reportLoading} onClick={handleReport}>Download Report</Button>
          </div>
        </div>
      </Modal>

      {/* Assign modal */}
      <Modal open={assignOpen} onClose={() => setAssignOpen(false)} title="Assign Case">
        <div className="space-y-4">
          <Input
            label="User ID"
            value={assignUserId}
            onChange={(e) => setAssignUserId(e.target.value)}
            placeholder="Enter user UUID..."
          />
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => setAssignOpen(false)}>Cancel</Button>
            <Button loading={assignLoading} onClick={handleAssign}>Assign</Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
