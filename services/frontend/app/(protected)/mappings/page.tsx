'use client';

import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import DataTable from '@/components/DataTable';
import {
  createMapping,
  fetchMapping,
  fetchMappings,
  fetchMappingTemplates,
  fetchMappingVersions,
  previewMappingConfig,
  rollbackMappingVersion,
  testMapping,
  updateMappingAsNewVersion,
  validateMappingConfig,
  type MappingListItem,
  type MappingTemplate,
  type MappingVersion,
} from '@/lib/api';

type EditorFormat = 'yaml' | 'json';

const DEFAULT_SAMPLE = JSON.stringify(
  {
    event_id: 'evt-1001',
    external_player_id: 'CPF123',
    transaction_type: 'DEPOSIT',
    amount: '2500.00',
    occurred_at: '2026-03-09T10:00:00Z',
    currency: 'BRL',
    instrument_type: 'PIX',
    instrument_token: 'pix-token-123',
  },
  null,
  2,
);

function prettyJson(v: unknown): string {
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}

function highlightLine(line: string, format: EditorFormat): ReactNode {
  const jsonKeyMatch = line.match(/^(\s*)"([^"]+)"\s*:(.*)$/);
  const yamlKeyMatch = line.match(/^(\s*)([A-Za-z_][\w-]*)\s*:(.*)$/);

  if (format === 'json' && jsonKeyMatch) {
    const [, indent, key, rest] = jsonKeyMatch;
    return (
      <>
        <span className="text-slate-500">{indent}</span>
        <span className="text-cyan-300">{JSON.stringify(key)}</span>
        <span className="text-slate-300">:{rest}</span>
      </>
    );
  }

  if (format === 'yaml' && yamlKeyMatch) {
    const [, indent, key, rest] = yamlKeyMatch;
    return (
      <>
        <span className="text-slate-500">{indent}</span>
        <span className="text-cyan-300">{key}</span>
        <span className="text-slate-300">:{rest}</span>
      </>
    );
  }

  return <span className="text-slate-300">{line}</span>;
}

function HighlightedEditor({
  value,
  onChange,
  format,
  ariaLabel,
}: {
  value: string;
  onChange: (next: string) => void;
  format: EditorFormat;
  ariaLabel?: string;
}) {
  const lines = value.split('\n');
  return (
    <div className="relative h-96 w-full overflow-hidden rounded-xl border border-slate-800 bg-gray-950">
      <pre className="pointer-events-none absolute inset-0 overflow-auto p-3 font-mono text-xs leading-5">
        {lines.map((line, idx) => (
          <div key={idx}>{highlightLine(line, format)}</div>
        ))}
      </pre>
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-label={ariaLabel ?? 'Editor do mapping'}
        className="relative z-10 h-full w-full resize-none bg-transparent p-3 font-mono text-xs leading-5 text-transparent caret-emerald-200 focus:outline-none"
        spellCheck={false}
      />
    </div>
  );
}

export default function MappingsPage() {
  const qc = useQueryClient();

  const [selected, setSelected] = useState<MappingListItem | null>(null);
  const [editorFormat, setEditorFormat] = useState<EditorFormat>('yaml');
  const [editorText, setEditorText] = useState('');
  const [mappingName, setMappingName] = useState('');
  const [sourceSystem, setSourceSystem] = useState('ConnectorGamma');
  const [entityType, setEntityType] = useState('TRANSACTION');
  const [changeNotes, setChangeNotes] = useState('');
  const [sampleText, setSampleText] = useState(DEFAULT_SAMPLE);
  const [validationMsg, setValidationMsg] = useState('Validação pendente');
  const [validationOk, setValidationOk] = useState<boolean | null>(null);
  const [validationDetail, setValidationDetail] = useState<Record<string, unknown> | null>(null);
  const [previewResult, setPreviewResult] = useState<Record<string, unknown> | null>(null);
  const [previewValidation, setPreviewValidation] = useState<Record<string, unknown> | null>(null);
  const [previewSampleParse, setPreviewSampleParse] = useState<Record<string, unknown> | null>(null);
  const [savedMappingTestResult, setSavedMappingTestResult] = useState<unknown | null>(null);
  const [normalizedConfig, setNormalizedConfig] = useState<Record<string, unknown> | null>(null);
  const [mode, setMode] = useState<'create' | 'new-version'>('create');

  const { data: templates = [] } = useQuery({
    queryKey: ['mapping-templates'],
    queryFn: fetchMappingTemplates,
  });

  const { data: mappings = [], isLoading: mappingsLoading } = useQuery({
    queryKey: ['mappings'],
    queryFn: fetchMappings,
  });

  const { data: versions = [] } = useQuery({
    queryKey: ['mapping-versions', selected?.id],
    queryFn: () => fetchMappingVersions(selected!.id),
    enabled: !!selected,
  });

  const activeTemplate = useMemo(
    () => templates.find((tpl) => tpl.source_system === sourceSystem) || null,
    [templates, sourceSystem],
  );

  const validateMutation = useMutation({
    mutationFn: (payload: { config_text?: string; format: EditorFormat }) => validateMappingConfig(payload),
    onSuccess: (data) => {
      setValidationOk(data.valid);
      setValidationMsg(data.valid ? 'Config válido e compatível com schema canônico' : data.error || 'Config inválido');
      setValidationDetail((data.canonical_validation as Record<string, unknown>) || null);
      setNormalizedConfig((data.normalized_config as Record<string, unknown>) || null);
    },
    onError: (e: unknown) => {
      const err = e as { response?: { data?: { detail?: string } } };
      setValidationOk(false);
      setValidationMsg(err?.response?.data?.detail || 'Erro ao validar');
      setValidationDetail(null);
    },
  });

  const previewMutation = useMutation({
    mutationFn: (payload: {
      config_text?: string;
      format: EditorFormat;
      sample?: Record<string, unknown>;
      sample_text?: string;
    }) => previewMappingConfig(payload),
    onSuccess: (data) => {
      if (!data.valid) {
        setPreviewResult({ error: data.error || 'Falha no preview' });
        setPreviewValidation((data.canonical_validation as Record<string, unknown>) || null);
        setPreviewSampleParse((data.sample_parse as Record<string, unknown>) || null);
        return;
      }
      setPreviewResult(data.preview || {});
      setPreviewValidation((data.canonical_validation as Record<string, unknown>) || null);
      setPreviewSampleParse((data.sample_parse as Record<string, unknown>) || null);
      setNormalizedConfig((data.normalized_config as Record<string, unknown>) || null);
    },
    onError: (e: unknown) => {
      const err = e as { response?: { data?: { detail?: string } } };
      setPreviewResult({ error: err?.response?.data?.detail || 'Erro ao gerar preview' });
      setPreviewValidation(null);
      setPreviewSampleParse(null);
    },
  });

  const createMutation = useMutation({
    mutationFn: createMapping,
    onSuccess: (created, variables) => {
      qc.setQueryData<MappingListItem[]>(['mappings'], (current = []) => {
        const updated = current.map((item) =>
          item.source_system === variables.source_system && item.entity_type === variables.entity_type
            ? { ...item, is_current: false }
            : item,
        );

        return [
          {
            id: created.id,
            name: created.name,
            source_system: variables.source_system,
            entity_type: variables.entity_type,
            version: '1.0',
            version_number: created.version_number,
            is_current: created.is_current,
            active: true,
            change_notes: variables.change_notes ?? null,
            updated_at: new Date().toISOString(),
          },
          ...updated,
        ];
      });
      void qc.invalidateQueries({ queryKey: ['mappings'] });
      setChangeNotes('');
      setMode('create');
    },
  });

  const versionMutation = useMutation({
    mutationFn: ({ id, body }: { id: string; body: { name?: string; config_text?: string; format: EditorFormat; change_notes?: string } }) =>
      updateMappingAsNewVersion(id, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['mappings'] });
      if (selected) {
        qc.invalidateQueries({ queryKey: ['mapping-versions', selected.id] });
      }
      setChangeNotes('');
    },
  });

  const rollbackMutation = useMutation({
    mutationFn: ({ id, version }: { id: string; version: number }) => rollbackMappingVersion(id, version),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['mappings'] });
      if (selected) {
        qc.invalidateQueries({ queryKey: ['mapping-versions', selected.id] });
      }
    },
  });

  const testSavedMappingMutation = useMutation({
    mutationFn: ({ id, sample }: { id: string; sample: Record<string, unknown> }) => testMapping(id, { sample }),
    onSuccess: (data) => {
      setSavedMappingTestResult(data);
    },
    onError: (e: unknown) => {
      const err = e as { response?: { data?: { detail?: string } } };
      setSavedMappingTestResult({
        status: 'error',
        detail: err?.response?.data?.detail || 'Erro ao testar mapping salvo',
      });
    },
  });

  useEffect(() => {
    const t = setTimeout(() => {
      if (!editorText.trim()) {
        setValidationOk(null);
        setValidationMsg('Validação pendente');
        return;
      }
      validateMutation.mutate({
        config_text: editorText,
        format: editorFormat,
      });
    }, 450);

    return () => clearTimeout(t);
  }, [editorText, editorFormat]);

  const columns = useMemo(
    () => [
      { header: 'Nome', accessorKey: 'name' as keyof MappingListItem },
      { header: 'Sistema de origem', accessorKey: 'source_system' as keyof MappingListItem },
      { header: 'Entidade', accessorKey: 'entity_type' as keyof MappingListItem },
      { header: 'Versão', accessorKey: 'version_number' as keyof MappingListItem },
      {
        header: 'Atual',
        accessorKey: 'is_current' as keyof MappingListItem,
        cell: (v: unknown) =>
          (v as boolean) ? (
            <span className="rounded bg-emerald-100 px-2 py-0.5 text-xs text-emerald-700">SIM</span>
          ) : (
            <span className="rounded bg-gray-100 px-2 py-0.5 text-xs text-gray-500">NÃO</span>
          ),
      },
      {
        header: 'Ativo',
        accessorKey: 'active' as keyof MappingListItem,
        cell: (v: unknown) =>
          (v as boolean) ? (
            <span className="rounded bg-sky-100 px-2 py-0.5 text-xs text-sky-700">ATIVO</span>
          ) : (
            <span className="rounded bg-gray-100 px-2 py-0.5 text-xs text-gray-500">INATIVO</span>
          ),
      },
    ],
    [],
  );

  async function loadTemplate(tpl: MappingTemplate) {
    setEditorText(tpl.template);
    setEditorFormat(tpl.format);
    setSourceSystem(tpl.source_system);
    setEntityType('TRANSACTION');
    setSelected(null);
    setMode('create');
    setMappingName(`${tpl.source_system} TRANSACTION`);
    setPreviewResult(null);
    setPreviewSampleParse(null);
    setSavedMappingTestResult(null);
    setSampleText(tpl.sample_payload);
  }

  async function selectMapping(row: MappingListItem) {
    setSelected(row);
    setMode('new-version');
    setMappingName(row.name);
    setSourceSystem(row.source_system);
    setEntityType(row.entity_type);

    const detail = await fetchMapping(row.id);
    setEditorFormat('json');
    setEditorText(prettyJson(detail.config_json));
    setValidationDetail((detail.canonical_validation as Record<string, unknown>) || null);
    setNormalizedConfig(detail.config_json);
    setPreviewResult(null);
    setPreviewSampleParse(null);
    setSavedMappingTestResult(null);
  }

  async function runPreview() {
    const payloadFormat = String(activeTemplate?.payload_format || '').toLowerCase();
    const expectsStructuredSample = !['xml', 'ndjson'].includes(payloadFormat);

    if (expectsStructuredSample) {
      try {
        const sampleObj = JSON.parse(sampleText) as Record<string, unknown>;
        previewMutation.mutate({
          config_text: editorText,
          format: editorFormat,
          sample: sampleObj,
        });
      } catch {
        setPreviewResult({ error: 'JSON de amostra inválido' });
        setPreviewSampleParse(null);
      }
      return;
    }

    previewMutation.mutate({
      config_text: editorText,
      format: editorFormat,
      sample_text: sampleText,
    });
  }

  async function saveMapping() {
    if (mode === 'create') {
      createMutation.mutate({
        name: mappingName,
        source_system: sourceSystem,
        entity_type: entityType,
        config_text: editorText,
        format: editorFormat,
        change_notes: changeNotes,
      });
      return;
    }

    if (!selected) return;
    versionMutation.mutate({
      id: selected.id,
      body: {
        name: mappingName,
        config_text: editorText,
        format: editorFormat,
        change_notes: changeNotes,
      },
    });
  }

  return (
    <div className="space-y-6">
      <header className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
        <h1 className="text-2xl font-semibold text-gray-900">Configuração de Integrações</h1>
        <p className="mt-1 text-sm text-gray-500">
          Configure como os dados de cada sistema de origem são transformados em eventos PLD.
        </p>
      </header>

      <section className="rounded-2xl border border-gray-200 bg-white p-4 shadow-sm">
        <p className="mb-3 text-xs font-semibold uppercase tracking-wide text-gray-500">Integrações disponíveis</p>
        <div className="flex flex-wrap gap-2">
          {templates.map((tpl) => (
            <button
              key={tpl.source_system}
              onClick={() => loadTemplate(tpl)}
              className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm hover:bg-gray-50"
            >
              {tpl.source_system}
            </button>
          ))}
        </div>
      </section>

      <section className="rounded-2xl border border-gray-200 bg-white p-4 shadow-sm">
        <DataTable data={mappings} columns={columns} loading={mappingsLoading} onRowClick={selectMapping} />
      </section>

      <section className="grid gap-4 lg:grid-cols-3">
        <div className="space-y-3 rounded-2xl border border-gray-200 bg-white p-4 shadow-sm lg:col-span-2">
          <div className="grid gap-3 md:grid-cols-3">
            <div>
              <label htmlFor="mapping-name" className="mb-1 block text-xs font-medium text-gray-600">Nome</label>
              <input
                id="mapping-name"
                aria-label="Nome do mapping"
                value={mappingName}
                onChange={(e) => setMappingName(e.target.value)}
                className="w-full rounded-lg border px-3 py-2 text-sm"
              />
            </div>
            <div>
              <label htmlFor="mapping-source-system" className="mb-1 block text-xs font-medium text-gray-600">Sistema de origem</label>
              <input
                id="mapping-source-system"
                aria-label="Sistema de origem do mapping"
                value={sourceSystem}
                onChange={(e) => setSourceSystem(e.target.value)}
                className="w-full rounded-lg border px-3 py-2 text-sm"
              />
            </div>
            <div>
              <label htmlFor="mapping-entity-type" className="mb-1 block text-xs font-medium text-gray-600">Tipo de dados</label>
              <input
                id="mapping-entity-type"
                aria-label="Tipo de dados do mapping"
                value={entityType}
                onChange={(e) => setEntityType(e.target.value.toUpperCase())}
                className="w-full rounded-lg border px-3 py-2 text-sm"
              />
            </div>
          </div>

          <div className="flex items-center justify-between">
            <label className="text-xs font-medium text-gray-600">Editor</label>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setEditorFormat('yaml')}
                className={`rounded px-2 py-1 text-xs ${editorFormat === 'yaml' ? 'bg-black text-white' : 'border'}`}
              >
                YAML
              </button>
              <button
                onClick={() => setEditorFormat('json')}
                className={`rounded px-2 py-1 text-xs ${editorFormat === 'json' ? 'bg-black text-white' : 'border'}`}
              >
                JSON
              </button>
            </div>
          </div>

          <HighlightedEditor value={editorText} onChange={setEditorText} format={editorFormat} ariaLabel="Editor do mapping" />

          <div className="rounded-lg border px-3 py-2 text-sm">
            <span className={`font-semibold ${validationOk === true ? 'text-emerald-600' : validationOk === false ? 'text-red-600' : 'text-gray-500'}`}>
              {validationOk === true ? 'Válido' : validationOk === false ? 'Inválido' : 'Pendente'}:
            </span>{' '}
            {validationMsg}
          </div>

          <pre className="max-h-40 overflow-auto rounded-lg bg-slate-950 p-3 text-xs text-slate-200">
            {prettyJson(validationDetail || { canonical_validation: 'pendente' })}
          </pre>

          <div>
            <label htmlFor="mapping-change-notes" className="mb-1 block text-xs font-medium text-gray-600">Notas da alteração</label>
            <input
              id="mapping-change-notes"
              aria-label="Notas da alteração do mapping"
              value={changeNotes}
              onChange={(e) => setChangeNotes(e.target.value)}
              placeholder="Ex.: ajuste de enum para transaction_type"
              className="w-full rounded-lg border px-3 py-2 text-sm"
            />
          </div>

          <div className="flex gap-2">
            <button
              onClick={saveMapping}
              disabled={createMutation.isPending || versionMutation.isPending || !mappingName || !editorText}
              aria-label={mode === 'create' ? 'Criar integração' : 'Salvar nova versão da integração'}
              className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
            >
              {mode === 'create' ? 'Criar Integração' : 'Salvar Nova Versão'}
            </button>
            <button
              onClick={() => {
                setMode('create');
                setSelected(null);
                setChangeNotes('');
              }}
              aria-label="Novo mapping"
              className="rounded-lg border px-4 py-2 text-sm"
            >
              Novo
            </button>
          </div>
        </div>

        <div className="space-y-3 rounded-2xl border border-gray-200 bg-white p-4 shadow-sm">
          {activeTemplate && (
            <div className="space-y-3 rounded-lg border border-slate-200 bg-slate-50 p-3">
              <div>
                <p className="text-sm font-semibold text-slate-900">Formato esperado pelo conector</p>
                <p className="text-xs text-slate-500">
                  {activeTemplate.payload_format} · {activeTemplate.content_type} · auth {activeTemplate.auth_mode}
                </p>
                {activeTemplate.signature_header && (
                  <p className="mt-1 text-xs text-slate-500">
                    Signature header: {activeTemplate.signature_header}
                  </p>
                )}
              </div>

              <div>
                <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Campos obrigatórios</p>
                <div className="max-h-32 space-y-1 overflow-auto rounded border border-slate-200 bg-white p-2">
                  {activeTemplate.input_schema.map((field) => (
                    <div key={field.name} className="text-xs text-slate-700">
                      <span className="font-mono text-slate-900">{field.name}</span>{' '}
                      <span className="text-slate-500">({field.type})</span>{' '}
                      {field.required ? <span className="text-rose-600">obrigatório</span> : <span className="text-slate-400">opcional</span>}
                    </div>
                  ))}
                </div>
              </div>

              <div>
                <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Exemplo de dado</p>
                <pre className="max-h-36 overflow-auto rounded border border-slate-200 bg-slate-950 p-3 text-[11px] text-slate-200">
                  {activeTemplate.sample_payload}
                </pre>
              </div>
            </div>
          )}

          <p className="text-sm font-semibold text-gray-800">Testar transformação</p>
          <textarea
            aria-label="Payload de exemplo para preview do mapping"
            value={sampleText}
            onChange={(e) => setSampleText(e.target.value)}
            className="h-36 w-full rounded-lg border p-2 font-mono text-xs"
            spellCheck={false}
          />
          <p className="text-[11px] text-gray-500">
            {activeTemplate
              ? `Use a amostra no formato nativo do conector: ${activeTemplate.payload_format}.`
              : 'Use JSON para fontes genéricas ou o payload nativo do conector selecionado.'}
          </p>
          <button
            onClick={runPreview}
            disabled={previewMutation.isPending}
            aria-label="Gerar preview"
            className="w-full rounded-lg bg-emerald-600 px-3 py-2 text-sm text-white disabled:opacity-50"
          >
            {previewMutation.isPending ? 'Testando...' : 'Testar transformação'}
          </button>

          {selected && (
            <button
              onClick={() => {
                try {
                  const sampleObj = JSON.parse(sampleText) as Record<string, unknown>;
                  testSavedMappingMutation.mutate({ id: selected.id, sample: sampleObj });
                } catch {
                  setSavedMappingTestResult({ status: 'error', detail: 'JSON inválido' });
                }
              }}
              disabled={testSavedMappingMutation.isPending}
              aria-label="Testar mapping salvo"
              className="w-full rounded-lg border border-indigo-300 bg-indigo-50 px-3 py-2 text-sm font-semibold text-indigo-700 disabled:opacity-50"
            >
              {testSavedMappingMutation.isPending ? 'Testando...' : 'Testar versão salva'}
            </button>
          )}

          <pre
            aria-label="Resultado do preview do mapping"
            className="max-h-56 overflow-auto rounded-lg bg-gray-900 p-3 text-xs text-emerald-200"
          >
            {prettyJson(previewResult || { message: 'Sem preview' })}
          </pre>

          <pre
            aria-label="Resumo do parse da amostra do preview"
            className="max-h-40 overflow-auto rounded-lg bg-slate-950 p-3 text-xs text-violet-200"
          >
            {prettyJson(previewSampleParse || { sample_parse: 'não aplicável' })}
          </pre>

          <pre
            aria-label="Validação canônica do preview do mapping"
            className="max-h-44 overflow-auto rounded-lg bg-slate-950 p-3 text-xs text-sky-200"
          >
            {prettyJson(previewValidation || { canonical_preview_validation: 'pendente' })}
          </pre>

          {selected && (
            <pre
              aria-label="Resultado do teste do mapping salvo"
              className="max-h-44 overflow-auto rounded-lg bg-slate-950 p-3 text-xs text-indigo-200"
            >
              {prettyJson(savedMappingTestResult || { saved_mapping_test: 'pendente' })}
            </pre>
          )}

          <pre
            aria-label="Config normalizada do mapping"
            className="max-h-44 overflow-auto rounded-lg bg-slate-950 p-3 text-xs text-amber-200"
          >
            {prettyJson(normalizedConfig || { normalized_config: 'pendente' })}
          </pre>

          {selected && (
            <div className="space-y-2 rounded-lg border border-gray-200 p-3">
              <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">Histórico de versões</p>
              <div className="max-h-44 space-y-2 overflow-auto">
                {versions.map((v: MappingVersion) => (
                  <div key={v.id} className="flex items-center justify-between rounded border px-2 py-1.5 text-xs">
                    <div>
                      <p className="font-medium">v{v.version_number} {v.is_current ? '(atual)' : ''}</p>
                      <p className="text-gray-500">{v.change_notes || 'sem notas'}</p>
                    </div>
                    {!v.is_current && (
                      <button
                        onClick={() => {
                          if (window.confirm(`Restaurar versão v${v.version_number} como ativa?`)) {
                            rollbackMutation.mutate({ id: selected.id, version: v.version_number });
                          }
                        }}
                        aria-label={`Restaurar versão ${v.version_number} como ativa`}
                        className="rounded bg-amber-100 px-2 py-1 text-amber-700"
                      >
                        Restaurar
                      </button>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
