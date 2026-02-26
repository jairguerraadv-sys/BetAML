'use client';

import { useEffect, useState } from 'react';
import {
  getMappingConfigs,
  createMappingConfig,
  updateMappingConfig,
  deleteMappingConfig,
} from '@/lib/api';
import type { MappingConfig, CreateMappingConfigPayload } from '@/lib/types';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input, Textarea } from '@/components/ui/input';
import { Modal } from '@/components/ui/modal';
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from '@/components/ui/table';
import { formatDate } from '@/lib/utils';
import { Plus, Edit2, Trash2 } from 'lucide-react';

const emptyForm: CreateMappingConfigPayload = {
  source_system: '',
  entity_type: '',
  version: '',
  field_mappings: {},
};

export default function MappingConfigsPage() {
  const [configs, setConfigs] = useState<MappingConfig[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<MappingConfig | null>(null);
  const [form, setForm] = useState<CreateMappingConfigPayload>(emptyForm);
  const [mappingsRaw, setMappingsRaw] = useState('{}');
  const [mappingsError, setMappingsError] = useState('');
  const [formLoading, setFormLoading] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const [deleteLoading, setDeleteLoading] = useState<string | null>(null);

  async function loadConfigs() {
    setLoading(true);
    setError(null);
    try {
      const data = await getMappingConfigs();
      setConfigs(data);
    } catch {
      setError('Failed to load mapping configs.');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { loadConfigs(); }, []);

  function openCreate() {
    setEditing(null);
    setForm(emptyForm);
    setMappingsRaw('{}');
    setMappingsError('');
    setFormError(null);
    setFormOpen(true);
  }

  function openEdit(cfg: MappingConfig) {
    setEditing(cfg);
    setForm({
      source_system: cfg.source_system,
      entity_type: cfg.entity_type,
      version: cfg.version,
      field_mappings: cfg.field_mappings,
    });
    setMappingsRaw(JSON.stringify(cfg.field_mappings, null, 2));
    setMappingsError('');
    setFormError(null);
    setFormOpen(true);
  }

  function handleMappingsChange(v: string) {
    setMappingsRaw(v);
    try {
      JSON.parse(v);
      setMappingsError('');
    } catch {
      setMappingsError('Invalid JSON');
    }
  }

  async function handleSubmit() {
    if (mappingsError) return;
    setFormLoading(true);
    setFormError(null);
    let field_mappings: Record<string, string> = {};
    try { field_mappings = JSON.parse(mappingsRaw) as Record<string, string>; } catch { /* empty */ }
    const payload: CreateMappingConfigPayload = { ...form, field_mappings };
    try {
      if (editing) {
        const updated = await updateMappingConfig(editing.id, payload);
        setConfigs((prev) => prev.map((c) => (c.id === editing.id ? updated : c)));
      } else {
        const created = await createMappingConfig(payload);
        setConfigs((prev) => [...prev, created]);
      }
      setFormOpen(false);
    } catch {
      setFormError('Failed to save mapping config.');
    } finally {
      setFormLoading(false);
    }
  }

  async function handleDelete(id: string) {
    if (!confirm('Delete this mapping config?')) return;
    setDeleteLoading(id);
    try {
      await deleteMappingConfig(id);
      setConfigs((prev) => prev.filter((c) => c.id !== id));
    } catch {
      setError('Failed to delete.');
    } finally {
      setDeleteLoading(null);
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-bold text-gray-900">Mapping Configs</h2>
        <Button onClick={openCreate}>
          <Plus className="mr-2 h-4 w-4" />
          New Config
        </Button>
      </div>

      {error && <p className="text-sm text-red-600">{error}</p>}

      <Card>
        <CardHeader>
          <CardTitle>{configs.length} configs</CardTitle>
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
                  <TableHeaderCell>Source System</TableHeaderCell>
                  <TableHeaderCell>Entity Type</TableHeaderCell>
                  <TableHeaderCell>Version</TableHeaderCell>
                  <TableHeaderCell>Created</TableHeaderCell>
                  <TableHeaderCell>Actions</TableHeaderCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {configs.length ? (
                  configs.map((cfg) => (
                    <TableRow key={cfg.id}>
                      <TableCell className="font-medium">{cfg.source_system}</TableCell>
                      <TableCell>{cfg.entity_type}</TableCell>
                      <TableCell className="font-mono text-xs">{cfg.version}</TableCell>
                      <TableCell className="text-xs text-gray-500">{formatDate(cfg.created_at)}</TableCell>
                      <TableCell>
                        <div className="flex gap-2">
                          <button
                            onClick={() => openEdit(cfg)}
                            className="rounded p-1 text-gray-400 hover:text-blue-600"
                            title="Edit"
                          >
                            <Edit2 className="h-4 w-4" />
                          </button>
                          <button
                            onClick={() => handleDelete(cfg.id)}
                            disabled={deleteLoading === cfg.id}
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
                    <TableCell colSpan={5} className="py-8 text-center text-gray-400">
                      No mapping configs yet
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
        title={editing ? 'Edit Mapping Config' : 'Create Mapping Config'}
      >
        <div className="space-y-4">
          {formError && (
            <p className="rounded bg-red-50 px-3 py-2 text-sm text-red-600">{formError}</p>
          )}
          <Input
            label="Source System"
            value={form.source_system}
            onChange={(e) => setForm((f) => ({ ...f, source_system: e.target.value }))}
            placeholder="e.g. betting_platform_v2"
            required
          />
          <Input
            label="Entity Type"
            value={form.entity_type}
            onChange={(e) => setForm((f) => ({ ...f, entity_type: e.target.value }))}
            placeholder="e.g. transaction"
            required
          />
          <Input
            label="Version"
            value={form.version}
            onChange={(e) => setForm((f) => ({ ...f, version: e.target.value }))}
            placeholder="e.g. 1.0.0"
            required
          />
          <Textarea
            label="Field Mappings (JSON)"
            value={mappingsRaw}
            onChange={(e) => handleMappingsChange(e.target.value)}
            error={mappingsError}
            rows={6}
            placeholder='{"source_field": "canonical_field"}'
          />
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => setFormOpen(false)}>Cancel</Button>
            <Button loading={formLoading} onClick={handleSubmit}>
              {editing ? 'Save Changes' : 'Create Config'}
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
