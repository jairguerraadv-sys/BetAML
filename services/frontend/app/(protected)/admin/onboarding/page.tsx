'use client';
import { useState, useRef } from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import Link from 'next/link';
import {
  Building2, User, Plug, Upload, Shield,
  CheckCircle2, ChevronRight, ChevronLeft,
  Eye, EyeOff, Code2, AlertCircle,
  ArrowRight, X, FolderOpen, Bell,
} from 'lucide-react';
import {
  fetchMappingTemplates,
  fetchRules,
  createMapping,
  createTenant,
  TenantCreateResult,
  createRule,
  ingestFile,
} from '@/lib/api';

// ── Step metadata ─────────────────────────────────────────────────────────────

type StepId = 1 | 2 | 3 | 4 | 5;

const STEPS: { id: StepId; label: string; icon: React.ElementType }[] = [
  { id: 1, label: 'Dados do Operador', icon: Building2 },
  { id: 2, label: 'Usuário ADMIN',     icon: User       },
  { id: 3, label: 'Conector',          icon: Plug       },
  { id: 4, label: 'Importar Teste',    icon: Upload     },
  { id: 5, label: 'Primeira Regra',    icon: Shield     },
];

// ── Connector templates (fallback + API merge) ────────────────────────────────

const FALLBACK_CONNECTORS = [
  {
    connector_name: 'ConnectorGamma',
    label:           'ConnectorGamma (XML)',
    source_system:   'connector_gamma',
    payload_format:  'XML',
    template: [
      'source_system: connector_gamma',
      'entity_type: transaction',
      'format: xml',
      'auth_mode: basic',
      'field_mappings:',
      '  player_id:        "//Transaction/PlayerId"',
      '  amount:           "//Transaction/Amount"',
      '  currency:         "//Transaction/Currency"',
      '  occurred_at:      "//Transaction/DateTime"',
      '  transaction_type: "//Transaction/Type"',
    ].join('\n'),
  },
  {
    connector_name: 'ConnectorDelta',
    label:           'ConnectorDelta (NDJSON)',
    source_system:   'connector_delta',
    payload_format:  'NDJSON',
    template: [
      'source_system: connector_delta',
      'entity_type: transaction',
      'format: ndjson',
      'auth_mode: api_key',
      'field_mappings:',
      '  player_id:        "$.player.id"',
      '  amount:           "$.event.amount"',
      '  currency:         "$.event.currency"',
      '  occurred_at:      "$.event.timestamp"',
      '  transaction_type: "$.event.type"',
    ].join('\n'),
  },
  {
    connector_name: 'ConnectorEpsilon',
    label:           'ConnectorEpsilon (Webhook)',
    source_system:   'connector_epsilon',
    payload_format:  'Webhook',
    template: [
      'source_system: connector_epsilon',
      'entity_type: transaction',
      'format: json',
      'auth_mode: hmac_sha256',
      'signature_header: X-Signature-256',
      'field_mappings:',
      '  player_id:        "$.data.userId"',
      '  amount:           "$.data.value"',
      '  currency:         "$.data.currency"',
      '  occurred_at:      "$.data.ts"',
      '  transaction_type: "$.data.eventType"',
    ].join('\n'),
  },
] as const;

type ConnectorEntry = (typeof FALLBACK_CONNECTORS)[number] & { template: string };

// ── Rule templates ────────────────────────────────────────────────────────────

const RULE_TEMPLATES = [
  {
    name:          'Spike de Depósito',
    description:   'Detecta depósito de alto valor combinado com frequência elevada nas últimas 24h.',
    condition_dsl: 'event.type == "DEPOSIT" AND event.amount > 10000 AND player.deposit_count_24h >= 3',
    severity:      'HIGH',
    scope:         'TRANSACTION',
  },
  {
    name:          'Muitos Depósitos Pequenos (Structuring)',
    description:   'Detecta padrão de fracionamento — múltiplos depósitos abaixo do limite de reporte.',
    condition_dsl: 'event.type == "DEPOSIT" AND event.amount BETWEEN 2000 AND 9999 AND player.deposit_count_24h >= 5',
    severity:      'CRITICAL',
    scope:         'TRANSACTION',
  },
] as const;

// ── Helper functions ──────────────────────────────────────────────────────────

function slugify(val: string): string {
  return val
    .toLowerCase()
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/\s+/g, '-')
    .replace(/[^a-z0-9_-]/g, '');
}

function applyCnpjMask(raw: string): string {
  const d = raw.replace(/\D/g, '').slice(0, 14);
  if (d.length <= 2)  return d;
  if (d.length <= 5)  return `${d.slice(0, 2)}.${d.slice(2)}`;
  if (d.length <= 8)  return `${d.slice(0, 2)}.${d.slice(2, 5)}.${d.slice(5)}`;
  if (d.length <= 12) return `${d.slice(0, 2)}.${d.slice(2, 5)}.${d.slice(5, 8)}/${d.slice(8)}`;
  return `${d.slice(0, 2)}.${d.slice(2, 5)}.${d.slice(5, 8)}/${d.slice(8, 12)}-${d.slice(12)}`;
}

function passwordStrength(pw: string): { label: string; color: string; bg: string; pct: number } {
  if (!pw) return { label: 'Fraca', color: 'text-gray-400', bg: 'bg-gray-200', pct: 0 };
  let score = 0;
  if (pw.length >= 8)                            score++;
  if (pw.length >= 12)                           score++;
  if (/[A-Z]/.test(pw))                          score++;
  if (/\d/.test(pw))                             score++;
  if (/[!@#$%^&*()\-_=+[\]{};:'",.<>?/\\|`~]/.test(pw)) score++;
  if (score <= 2) return { label: 'Fraca',  color: 'text-red-500',    bg: 'bg-red-400',    pct: 33  };
  if (score <= 4) return { label: 'Média',  color: 'text-yellow-600', bg: 'bg-yellow-400', pct: 66  };
  return            { label: 'Forte',  color: 'text-green-600',  bg: 'bg-green-500',  pct: 100 };
}

function parseCsvPreview(text: string, maxRows = 3): string[][] {
  const lines = text.split('\n').filter((l) => l.trim());
  return lines.slice(0, maxRows + 1).map((line) => {
    const cols: string[] = [];
    let cur = '';
    let inQ = false;
    for (const ch of line) {
      if (ch === '"') { inQ = !inQ; }
      else if (ch === ',' && !inQ) { cols.push(cur.trim()); cur = ''; }
      else { cur += ch; }
    }
    cols.push(cur.trim());
    return cols;
  });
}

function extractError(err: unknown): string {
  return (
    (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
    'Ocorreu um erro inesperado. Tente novamente.'
  );
}

// ── StepIndicator ─────────────────────────────────────────────────────────────

function StepIndicator({ current }: { current: StepId }) {
  return (
    <div className="flex items-start gap-0">
      {STEPS.map((s, i) => {
        const done   = s.id < current;
        const active = s.id === current;
        const Icon   = s.icon;
        return (
          <div key={s.id} className="flex flex-1 items-start">
            <div className="flex flex-col items-center gap-1.5 w-full">
              <div
                className={`flex h-9 w-9 items-center justify-center rounded-full border-2 transition-colors ${
                  done
                    ? 'border-green-500 bg-green-500 text-white'
                    : active
                    ? 'border-brand bg-brand text-white'
                    : 'border-gray-200 bg-white text-gray-400'
                }`}
              >
                {done ? <CheckCircle2 size={16} /> : <Icon size={15} />}
              </div>
              <span
                className={`text-[10px] font-semibold text-center leading-snug px-1 ${
                  active ? 'text-brand' : done ? 'text-green-600' : 'text-gray-400'
                }`}
              >
                {s.label}
              </span>
            </div>
            {i < STEPS.length - 1 && (
              <div
                className={`mx-1 mt-[18px] h-0.5 flex-1 shrink-0 transition-colors ${
                  done ? 'bg-green-400' : 'bg-gray-200'
                }`}
              />
            )}
          </div>
        );
      })}
    </div>
  );
}

// ── Success screen ────────────────────────────────────────────────────────────

function SuccessScreen({ result }: { result: TenantCreateResult }) {
  return (
    <div className="flex flex-col items-center gap-6 py-14 text-center">
      <div className="flex h-20 w-20 items-center justify-center rounded-full bg-green-100">
        <CheckCircle2 size={44} className="text-green-500" />
      </div>
      <div>
        <h2 className="text-2xl font-bold text-gray-900">Operador cadastrado com sucesso!</h2>
        <p className="mt-2 text-sm text-gray-500">
          O tenant{' '}
          <span className="font-mono font-semibold text-gray-800">{result.slug}</span>{' '}
          está pronto para operar. O usuário admin inicial é{' '}
          <strong>{result.admin_username}</strong>.
        </p>
      </div>

      <div className="grid grid-cols-3 gap-4 mt-2 w-full max-w-md">
        <Link
          href="/cases"
          className="flex flex-col items-center gap-2 rounded-xl border border-gray-200 bg-white p-5 text-sm font-medium text-gray-700 shadow-sm transition-all hover:border-brand hover:shadow-md"
        >
          <FolderOpen size={24} className="text-blue-500" />
          Ver Casos
        </Link>
        <Link
          href="/alerts"
          className="flex flex-col items-center gap-2 rounded-xl border border-gray-200 bg-white p-5 text-sm font-medium text-gray-700 shadow-sm transition-all hover:border-brand hover:shadow-md"
        >
          <Bell size={24} className="text-amber-500" />
          Ver Alertas
        </Link>
        <Link
          href="/rules"
          className="flex flex-col items-center gap-2 rounded-xl border border-gray-200 bg-white p-5 text-sm font-medium text-gray-700 shadow-sm transition-all hover:border-brand hover:shadow-md"
        >
          <Shield size={24} className="text-purple-500" />
          Ver Regras
        </Link>
      </div>

      <Link
        href="/admin"
        className="mt-2 flex items-center gap-1.5 text-sm font-medium text-brand hover:underline"
      >
        Ir para Administração <ArrowRight size={14} />
      </Link>
    </div>
  );
}

// ── Field helpers ─────────────────────────────────────────────────────────────

function Label({ text, required }: { text: string; required?: boolean }) {
  return (
    <label className="mb-1 block text-xs font-semibold text-gray-600">
      {text}
      {required && <span className="ml-0.5 text-red-500"> *</span>}
    </label>
  );
}

function Input(props: React.InputHTMLAttributes<HTMLInputElement> & { error?: boolean }) {
  const { error, className, ...rest } = props;
  return (
    <input
      {...rest}
      className={[
        'w-full rounded-lg border px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand',
        error ? 'border-red-300 bg-red-50' : 'border-gray-200',
        className ?? '',
      ].join(' ')}
    />
  );
}

// ── Main wizard component ─────────────────────────────────────────────────────

interface Step1Form { nome: string; slug: string; cnpj: string; email_contato: string; }
interface Step2Form { username: string; email: string; senha: string; confirmar_senha: string; }

export default function OnboardingPage() {
  // ── Wizard navigation ──────────────────────────────────────────────────────
  const [step, setStep] = useState<StepId>(1);

  // ── Step 1 state ───────────────────────────────────────────────────────────
  const [s1, setS1]         = useState<Step1Form>({ nome: '', slug: '', cnpj: '', email_contato: '' });
  const [slugEdited, setSlugEdited] = useState(false);

  // ── Step 2 state ───────────────────────────────────────────────────────────
  const [s2, setS2]         = useState<Step2Form>({ username: '', email: '', senha: '', confirmar_senha: '' });
  const [showPw, setShowPw] = useState(false);
  const [showCf, setShowCf] = useState(false);

  // ── Step 3 state ───────────────────────────────────────────────────────────
  const [selConnector, setSelConnector] = useState<ConnectorEntry | null>(null);
  const [connName, setConnName]         = useState('');

  // ── Step 4 state ───────────────────────────────────────────────────────────
  const fileRef                         = useRef<HTMLInputElement>(null);
  const [csvFile, setCsvFile]           = useState<File | null>(null);
  const [csvPreview, setCsvPreview]     = useState<string[][] | null>(null);
  const [csvError, setCsvError]         = useState('');

  // ── Step 5 state ───────────────────────────────────────────────────────────
  const [selRule, setSelRule] = useState<number | null>(null);

  // ── Results / completion ───────────────────────────────────────────────────
  const [tenantResult, setTenantResult] = useState<TenantCreateResult | null>(null);
  const [done, setDone]                 = useState(false);
  const [mutError, setMutError]         = useState('');

  // ── API: templates ─────────────────────────────────────────────────────────
  const { data: apiTemplates = [] } = useQuery({
    queryKey: ['mapping-templates'],
    queryFn:  fetchMappingTemplates,
  });

  // Merge API data with fallback templates
  const connectorList: ConnectorEntry[] = FALLBACK_CONNECTORS.map((fb) => {
    const match = apiTemplates.find(
      (t) => t.connector_name.toLowerCase() === fb.connector_name.toLowerCase(),
    );
    return {
      ...fb,
      template: (match?.template && match.template.trim()) ? match.template : fb.template,
    } as ConnectorEntry;
  });

  // ── API: existing rules (merged with fallback templates for step 5) ─────────
  const { data: apiRules = [] } = useQuery({
    queryKey: ['rules-onboarding'],
    queryFn:  fetchRules,
  });

  // Present API rules first (so tenants see their own live rules), then append
  // any fallback templates whose name doesn't already exist in the API set.
  const apiRuleNames = new Set(apiRules.map((r) => r.name.toLowerCase()));
  const rulesList = [
    ...apiRules.map((r) => ({
      name:          r.name,
      description:   r.description ?? '',
      condition_dsl: r.condition_dsl,
      severity:      r.severity,
      scope:         r.scope,
    })),
    ...RULE_TEMPLATES.filter((t) => !apiRuleNames.has(t.name.toLowerCase())),
  ];

  // ── Mutations ──────────────────────────────────────────────────────────────
  const tenantMut = useMutation({
    mutationFn: () =>
      createTenant({
        name:           s1.nome,
        slug:           s1.slug,
        admin_username: s2.username,
        admin_email:    s2.email,
        admin_password: s2.senha,
        cnpj:           s1.cnpj.replace(/\D/g, ''),
      }),
    onSuccess: (data) => { setTenantResult(data); setMutError(''); setStep(3); },
    onError:   (err)  => setMutError(extractError(err)),
  });

  const mappingMut = useMutation({
    mutationFn: () =>
      createMapping({
        name:          connName.trim() || selConnector!.connector_name,
        source_system: selConnector!.source_system,
        entity_type:   'transaction',
        config_text:   selConnector!.template,
        format:        'yaml',
        change_notes:  'Criado via wizard de onboarding',
      }),
    onSuccess: () => { setMutError(''); setStep(4); },
    onError:   (err) => setMutError(extractError(err)),
  });

  const ingestFileMut = useMutation({
    mutationFn: () => {
      const fd = new FormData();
      fd.append('file', csvFile!);
      return ingestFile(fd);
    },
    onSuccess: () => { setMutError(''); setStep(5); },
    onError:   (err) => setMutError(extractError(err)),
  });

  const ruleMut = useMutation({
    mutationFn: () => {
      const tpl = rulesList[selRule!];
      return createRule({
        name:          tpl.name,
        condition_dsl: tpl.condition_dsl,
        severity:      tpl.severity,
        description:   tpl.description,
        scope:         tpl.scope,
      });
    },
    onSuccess: () => { setMutError(''); setDone(true); },
    onError:   (err) => setMutError(extractError(err)),
  });

  const anyPending =
    tenantMut.isPending ||
    mappingMut.isPending ||
    ingestFileMut.isPending ||
    ruleMut.isPending;

  // ── Validation ─────────────────────────────────────────────────────────────
  const step1Valid = Boolean(s1.nome && s1.slug && s1.cnpj.replace(/\D/g, '').length === 14);

  const s2EmailValid   = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(s2.email);
  const s2PwMatch      = s2.senha === s2.confirmar_senha;
  const step2Valid     = Boolean(s2.username && s2EmailValid && s2.senha.length >= 8 && s2PwMatch);
  const pwStrength     = passwordStrength(s2.senha);

  // ── CSV handling ───────────────────────────────────────────────────────────
  function handleCsvSelect(file: File) {
    setCsvError('');
    if (!file.name.toLowerCase().endsWith('.csv')) {
      setCsvError('Formato inválido. Somente arquivos .csv são aceitos.');
      return;
    }
    if (file.size > 5 * 1024 * 1024) {
      setCsvError('Arquivo muito grande. O tamanho máximo é 5 MB.');
      return;
    }
    setCsvFile(file);
    const reader = new FileReader();
    reader.onload = (e) => {
      const text = (e.target?.result as string) ?? '';
      setCsvPreview(parseCsvPreview(text, 3));
    };
    reader.readAsText(file);
  }

  function clearCsv() {
    setCsvFile(null);
    setCsvPreview(null);
    setCsvError('');
    if (fileRef.current) fileRef.current.value = '';
  }

  // ── Navigation handlers ────────────────────────────────────────────────────
  function goBack() {
    setMutError('');
    setStep((s) => (s - 1) as StepId);
  }

  function handleStep3Next() {
    if (!selConnector) { setStep(4); return; }
    mappingMut.mutate();
  }

  function handleStep4Next() {
    if (!csvFile) { setStep(5); return; }
    ingestFileMut.mutate();
  }

  function handleStep5Finish(skip: boolean) {
    if (skip || selRule === null) { setDone(true); return; }
    ruleMut.mutate();
  }

  // ── Completion ─────────────────────────────────────────────────────────────
  if (done && tenantResult) {
    return (
      <div className="mx-auto max-w-3xl rounded-xl border border-gray-100 bg-white shadow-sm">
        <SuccessScreen result={tenantResult} />
      </div>
    );
  }

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <div className="mx-auto max-w-3xl space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Cadastrar Novo Operador</h1>
        <p className="mt-1 text-sm text-gray-500">
          Siga os 5 passos abaixo para configurar um novo tenant no BetAML.
        </p>
      </div>

      {/* Progress bar */}
      <div className="rounded-xl border border-gray-100 bg-white p-6 shadow-sm">
        <StepIndicator current={step} />
      </div>

      {/* Step card */}
      <div className="rounded-xl border border-gray-100 bg-white p-6 shadow-sm">
        {/* Error banner */}
        {mutError && (
          <div className="mb-5 flex items-start gap-3 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
            <AlertCircle size={16} className="mt-0.5 shrink-0" />
            <span className="flex-1">{mutError}</span>
            <button
              onClick={() => setMutError('')}
              className="shrink-0 text-red-400 hover:text-red-600"
              aria-label="Fechar"
            >
              <X size={14} />
            </button>
          </div>
        )}

        {/* ── Step 1: Dados do Operador ─────────────────────────────────────── */}
        {step === 1 && (
          <div className="space-y-5">
            <div>
              <h2 className="text-base font-bold text-gray-900">
                Etapa 1 — Dados do Operador
              </h2>
              <p className="mt-0.5 text-xs text-gray-500">
                Identidade do novo tenant no sistema BetAML.
              </p>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <div className="sm:col-span-2">
                <Label text="Nome do Operador" required />
                <Input
                  value={s1.nome}
                  onChange={(e) => {
                    const nome = e.target.value;
                    setS1((prev) => ({
                      ...prev,
                      nome,
                      slug: slugEdited ? prev.slug : slugify(nome),
                    }));
                  }}
                  placeholder="Bet Esportiva Ltda"
                  autoComplete="organization"
                />
              </div>

              <div className="sm:col-span-2">
                <Label text="Slug (identificador único)" required />
                <Input
                  value={s1.slug}
                  onChange={(e) => {
                    setSlugEdited(true);
                    setS1((prev) => ({ ...prev, slug: slugify(e.target.value) }));
                  }}
                  placeholder="bet-esportiva"
                  className="font-mono"
                />
                <p className="mt-1 text-[11px] text-gray-400">
                  Somente letras minúsculas, números e hífens. Gerado automaticamente a partir do nome.
                </p>
              </div>

              <div>
                <Label text="CNPJ" required />
                <Input
                  value={s1.cnpj}
                  onChange={(e) =>
                    setS1((prev) => ({ ...prev, cnpj: applyCnpjMask(e.target.value) }))
                  }
                  placeholder="00.000.000/0000-00"
                  className="font-mono tracking-wide"
                  maxLength={18}
                  inputMode="numeric"
                />
              </div>

              <div>
                <Label text="Email de Contato" />
                <Input
                  type="email"
                  value={s1.email_contato}
                  onChange={(e) =>
                    setS1((prev) => ({ ...prev, email_contato: e.target.value }))
                  }
                  placeholder="compliance@betesportiva.com"
                />
              </div>
            </div>
          </div>
        )}

        {/* ── Step 2: Criar Usuário ADMIN ───────────────────────────────────── */}
        {step === 2 && (
          <div className="space-y-5">
            <div>
              <h2 className="text-base font-bold text-gray-900">
                Etapa 2 — Criar Usuário ADMIN
              </h2>
              <p className="mt-0.5 text-xs text-gray-500">
                Este usuário terá acesso completo ao tenant{' '}
                <span className="font-mono font-semibold text-gray-700">{s1.slug}</span>.
              </p>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <Label text="Username" required />
                <Input
                  value={s2.username}
                  onChange={(e) => setS2((prev) => ({ ...prev, username: e.target.value }))}
                  placeholder="admin_bet"
                  autoComplete="username"
                />
              </div>

              <div>
                <Label text="Email" required />
                <Input
                  type="email"
                  value={s2.email}
                  onChange={(e) => setS2((prev) => ({ ...prev, email: e.target.value }))}
                  placeholder="admin@betesportiva.com"
                  autoComplete="email"
                  error={Boolean(s2.email && !s2EmailValid)}
                />
                {s2.email && !s2EmailValid && (
                  <p className="mt-1 text-[11px] text-red-500">Email inválido.</p>
                )}
              </div>

              <div className="sm:col-span-2">
                <Label text="Senha" required />
                <div className="relative">
                  <Input
                    type={showPw ? 'text' : 'password'}
                    value={s2.senha}
                    onChange={(e) => setS2((prev) => ({ ...prev, senha: e.target.value }))}
                    placeholder="Mínimo 8 caracteres"
                    autoComplete="new-password"
                    className="pr-10"
                  />
                  <button
                    type="button"
                    onClick={() => setShowPw((v) => !v)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
                    tabIndex={-1}
                    aria-label={showPw ? 'Ocultar senha' : 'Exibir senha'}
                  >
                    {showPw ? <EyeOff size={15} /> : <Eye size={15} />}
                  </button>
                </div>

                {/* Password strength indicator */}
                {s2.senha && (
                  <div className="mt-2 space-y-1">
                    <div className="h-1.5 w-full overflow-hidden rounded-full bg-gray-100">
                      <div
                        className={`h-full rounded-full transition-all duration-300 ${pwStrength.bg}`}
                        style={{ width: `${pwStrength.pct}%` }}
                      />
                    </div>
                    <p className={`text-[11px] font-semibold ${pwStrength.color}`}>
                      Força da senha: {pwStrength.label}
                    </p>
                  </div>
                )}
              </div>

              <div className="sm:col-span-2">
                <Label text="Confirmar Senha" required />
                <div className="relative">
                  <Input
                    type={showCf ? 'text' : 'password'}
                    value={s2.confirmar_senha}
                    onChange={(e) =>
                      setS2((prev) => ({ ...prev, confirmar_senha: e.target.value }))
                    }
                    placeholder="Repita a senha"
                    autoComplete="new-password"
                    className="pr-10"
                    error={Boolean(s2.confirmar_senha && !s2PwMatch)}
                  />
                  <button
                    type="button"
                    onClick={() => setShowCf((v) => !v)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
                    tabIndex={-1}
                    aria-label={showCf ? 'Ocultar confirmação' : 'Exibir confirmação'}
                  >
                    {showCf ? <EyeOff size={15} /> : <Eye size={15} />}
                  </button>
                </div>
                {s2.confirmar_senha && !s2PwMatch && (
                  <p className="mt-1 text-[11px] text-red-500">As senhas não coincidem.</p>
                )}
              </div>
            </div>
          </div>
        )}

        {/* ── Step 3: Configurar Primeiro Conector ─────────────────────────── */}
        {step === 3 && (
          <div className="space-y-5">
            <div>
              <h2 className="text-base font-bold text-gray-900">
                Etapa 3 — Configurar Primeiro Conector
              </h2>
              <p className="mt-0.5 text-xs text-gray-500">
                Selecione um template para definir como os dados chegarão ao BetAML. Você pode
                pular e configurar conectores depois em <strong>Conectores</strong>.
              </p>
            </div>

            <div className="grid grid-cols-3 gap-3">
              {connectorList.map((c) => {
                const selected = selConnector?.connector_name === c.connector_name;
                return (
                  <button
                    key={c.connector_name}
                    onClick={() => {
                      setSelConnector(selected ? null : c);
                      setConnName('');
                    }}
                    className={`flex flex-col gap-1.5 rounded-xl border-2 p-4 text-left transition-all ${
                      selected
                        ? 'border-brand bg-brand/5 shadow-sm'
                        : 'border-gray-200 hover:border-gray-300 hover:bg-gray-50/70'
                    }`}
                  >
                    <Plug
                      size={18}
                      className={selected ? 'text-brand' : 'text-gray-400'}
                    />
                    <span className="mt-0.5 text-xs font-bold text-gray-800">
                      {c.connector_name}
                    </span>
                    <span className="text-[10px] text-gray-500">{c.payload_format}</span>
                    {selected && (
                      <span className="mt-0.5 flex items-center gap-1 text-[10px] font-semibold text-brand">
                        <CheckCircle2 size={11} /> Selecionado
                      </span>
                    )}
                  </button>
                );
              })}
            </div>

            {selConnector && (
              <div className="space-y-3">
                <div>
                  <Label text="Nome do Conector" />
                  <Input
                    value={connName}
                    onChange={(e) => setConnName(e.target.value)}
                    placeholder={selConnector.connector_name}
                  />
                  <p className="mt-1 text-[11px] text-gray-400">
                    Deixe em branco para usar o nome padrão do template.
                  </p>
                </div>

                <div>
                  <div className="mb-1.5 flex items-center gap-1.5">
                    <Code2 size={12} className="text-gray-400" />
                    <span className="text-xs font-semibold text-gray-600">
                      Configuração YAML
                    </span>
                    <span className="ml-auto text-[10px] text-gray-400">somente leitura</span>
                  </div>
                  <pre className="max-h-52 overflow-auto rounded-lg border border-gray-200 bg-gray-50 p-3.5 font-mono text-[11px] leading-relaxed text-gray-700">
                    {selConnector.template}
                  </pre>
                </div>
              </div>
            )}
          </div>
        )}

        {/* ── Step 4: Importar Arquivo de Teste ────────────────────────────── */}
        {step === 4 && (
          <div className="space-y-5">
            <div>
              <h2 className="text-base font-bold text-gray-900">
                Etapa 4 — Importar Arquivo de Teste
              </h2>
              <p className="mt-0.5 text-xs text-gray-500">
                Faça upload de um CSV para validar o pipeline de ingestão com dados reais. Máximo
                5 MB. Você pode pular e fazer o upload depois.
              </p>
            </div>

            {/* Drop zone */}
            <div
              onClick={() => fileRef.current?.click()}
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => {
                e.preventDefault();
                const f = e.dataTransfer.files[0];
                if (f) handleCsvSelect(f);
              }}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => e.key === 'Enter' && fileRef.current?.click()}
              className="flex cursor-pointer flex-col items-center gap-3 rounded-xl border-2 border-dashed border-gray-200 bg-gray-50/60 px-6 py-10 transition-colors hover:border-brand hover:bg-brand/5 focus:outline-none focus:ring-2 focus:ring-brand"
            >
              <Upload size={28} className={csvFile ? 'text-brand' : 'text-gray-400'} />
              <div className="text-center">
                <p className="text-sm font-semibold text-gray-700">
                  {csvFile ? csvFile.name : 'Clique ou arraste um arquivo CSV'}
                </p>
                <p className="text-xs text-gray-400">Somente .csv — máx. 5 MB</p>
              </div>
              <input
                ref={fileRef}
                type="file"
                accept=".csv"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) handleCsvSelect(f);
                }}
              />
            </div>

            {csvFile && (
              <div className="flex items-center justify-between rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-xs">
                <span className="font-medium text-gray-700 truncate">{csvFile.name}</span>
                <span className="ml-2 shrink-0 text-gray-400">
                  {(csvFile.size / 1024).toFixed(1)} KB
                </span>
                <button
                  onClick={(e) => { e.stopPropagation(); clearCsv(); }}
                  className="ml-3 shrink-0 rounded p-0.5 text-gray-400 hover:text-red-500"
                  title="Remover arquivo"
                >
                  <X size={14} />
                </button>
              </div>
            )}

            {csvError && (
              <div className="flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-600">
                <AlertCircle size={13} className="shrink-0" />
                {csvError}
              </div>
            )}

            {/* Preview table */}
            {csvPreview && csvPreview.length > 1 && (
              <div>
                <p className="mb-2 text-xs font-semibold text-gray-600">
                  Prévia — {Math.min(csvPreview.length - 1, 3)} linha(s) de dados
                </p>
                <div className="overflow-x-auto rounded-lg border border-gray-200">
                  <table className="w-full text-xs">
                    <thead className="bg-gray-50">
                      <tr>
                        {(csvPreview[0] ?? []).map((h, i) => (
                          <th
                            key={i}
                            className="whitespace-nowrap border-b border-gray-200 px-3 py-2 text-left font-semibold text-gray-600"
                          >
                            {h || `coluna_${i + 1}`}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100">
                      {csvPreview.slice(1, 4).map((row, ri) => (
                        <tr key={ri} className="hover:bg-gray-50/60">
                          {row.map((cell, ci) => (
                            <td
                              key={ci}
                              className="max-w-xs truncate whitespace-nowrap px-3 py-2 text-gray-700"
                              title={cell}
                            >
                              {cell}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        )}

        {/* ── Step 5: Criar Primeira Regra ──────────────────────────────────── */}
        {step === 5 && (
          <div className="space-y-5">
            <div>
              <h2 className="text-base font-bold text-gray-900">
                Etapa 5 — Criar Primeira Regra
              </h2>
              <p className="mt-0.5 text-xs text-gray-500">
                Selecione um template para começar a detectar comportamentos suspeitos
                imediatamente. Você pode criar mais regras depois em{' '}
                <strong>Condições de Risco</strong>.
              </p>
            </div>

            <div className="space-y-3">
              {rulesList.map((tpl, i) => {
                const sel  = selRule === i;
                return (
                  <button
                    key={i}
                    onClick={() => setSelRule(sel ? null : i)}
                    className={`w-full rounded-xl border-2 p-4 text-left transition-all ${
                      sel
                        ? 'border-brand bg-brand/5'
                        : 'border-gray-200 hover:border-gray-300 hover:bg-gray-50/60'
                    }`}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <span className="text-sm font-bold text-gray-800">{tpl.name}</span>
                      <span
                        className={`shrink-0 rounded-full px-2.5 py-0.5 text-[10px] font-bold uppercase ${
                          tpl.severity === 'CRITICAL'
                            ? 'bg-red-100 text-red-700'
                            : 'bg-orange-100 text-orange-700'
                        }`}
                      >
                        {tpl.severity}
                      </span>
                    </div>
                    <p className="mt-1 text-xs text-gray-500">{tpl.description}</p>

                    {sel && (
                      <div className="mt-3">
                        <div className="mb-1.5 flex items-center gap-1.5">
                          <Code2 size={11} className="text-gray-400" />
                          <span className="text-[10px] font-semibold text-gray-500">
                            Condição DSL
                          </span>
                        </div>
                        <pre className="overflow-x-auto rounded-lg bg-gray-900 px-3.5 py-3 font-mono text-[11px] leading-relaxed text-green-300">
                          {tpl.condition_dsl}
                        </pre>
                      </div>
                    )}
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {/* ── Navigation ────────────────────────────────────────────────────── */}
        <div className={`mt-6 flex items-center gap-3 ${step > 1 ? 'justify-between' : 'justify-end'}`}>
          {/* Back */}
          {step > 1 && (
            <button
              onClick={goBack}
              disabled={anyPending}
              className="flex items-center gap-1.5 rounded-lg border border-gray-200 px-4 py-2 text-sm font-medium text-gray-600 transition-colors hover:bg-gray-50 disabled:opacity-50"
            >
              <ChevronLeft size={15} /> Anterior
            </button>
          )}

          {/* Skip + primary CTA */}
          <div className="flex items-center gap-3">
            {step === 3 && (
              <button
                onClick={() => {
                  setSelConnector(null);
                  setConnName('');
                  setMutError('');
                  setStep(4);
                }}
                disabled={anyPending}
                className="text-sm text-gray-400 underline underline-offset-2 transition-colors hover:text-gray-600 disabled:opacity-50"
              >
                Pular esta etapa
              </button>
            )}

            {step === 4 && (
              <button
                onClick={() => {
                  clearCsv();
                  setMutError('');
                  setStep(5);
                }}
                disabled={anyPending}
                className="text-sm text-gray-400 underline underline-offset-2 transition-colors hover:text-gray-600 disabled:opacity-50"
              >
                Pular esta etapa
              </button>
            )}

            {step === 5 && (
              <button
                onClick={() => handleStep5Finish(true)}
                disabled={anyPending}
                className="text-sm text-gray-400 underline underline-offset-2 transition-colors hover:text-gray-600 disabled:opacity-50"
              >
                Pular e Concluir
              </button>
            )}

            {/* Step 1 → Próximo */}
            {step === 1 && (
              <button
                onClick={() => { if (step1Valid) setStep(2); }}
                disabled={!step1Valid}
                className="flex items-center gap-1.5 rounded-lg bg-brand px-5 py-2 text-sm font-semibold text-white transition-colors hover:bg-brand/90 disabled:opacity-50"
              >
                Próximo <ChevronRight size={15} />
              </button>
            )}

            {/* Step 2 → Concluir Cadastro (creates tenant) */}
            {step === 2 && (
              <button
                onClick={() => { if (step2Valid) tenantMut.mutate(); }}
                disabled={!step2Valid || tenantMut.isPending}
                className="flex items-center gap-1.5 rounded-lg bg-brand px-5 py-2 text-sm font-semibold text-white transition-colors hover:bg-brand/90 disabled:opacity-50"
              >
                {tenantMut.isPending ? (
                  'Criando…'
                ) : (
                  <>Concluir Cadastro <ChevronRight size={15} /></>
                )}
              </button>
            )}

            {/* Step 3 → Salvar conector / Próximo */}
            {step === 3 && (
              <button
                onClick={handleStep3Next}
                disabled={mappingMut.isPending}
                className="flex items-center gap-1.5 rounded-lg bg-brand px-5 py-2 text-sm font-semibold text-white transition-colors hover:bg-brand/90 disabled:opacity-50"
              >
                {mappingMut.isPending ? (
                  'Salvando…'
                ) : selConnector ? (
                  <>Salvar e Continuar <ChevronRight size={15} /></>
                ) : (
                  <>Próximo <ChevronRight size={15} /></>
                )}
              </button>
            )}

            {/* Step 4 → Enviar arquivo / Próximo */}
            {step === 4 && (
              <button
                onClick={handleStep4Next}
                disabled={ingestFileMut.isPending || Boolean(csvError)}
                className="flex items-center gap-1.5 rounded-lg bg-brand px-5 py-2 text-sm font-semibold text-white transition-colors hover:bg-brand/90 disabled:opacity-50"
              >
                {ingestFileMut.isPending ? (
                  'Enviando…'
                ) : csvFile ? (
                  <>Enviar e Continuar <ChevronRight size={15} /></>
                ) : (
                  <>Próximo <ChevronRight size={15} /></>
                )}
              </button>
            )}

            {/* Step 5 → Criar Regra e Concluir */}
            {step === 5 && (
              <button
                onClick={() => handleStep5Finish(false)}
                disabled={selRule === null || ruleMut.isPending}
                className="flex items-center gap-1.5 rounded-lg bg-brand px-5 py-2 text-sm font-semibold text-white transition-colors hover:bg-brand/90 disabled:opacity-50"
              >
                {ruleMut.isPending ? (
                  'Criando…'
                ) : (
                  <>Criar Regra e Concluir <CheckCircle2 size={15} /></>
                )}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
