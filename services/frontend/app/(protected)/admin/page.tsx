'use client';
import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import {
  fetchTenants, createTenant, updateTenant,
  TenantOut, TenantCreatePayload,
  fetchAdminUsers, createAdminUser, updateAdminUser, deleteAdminUser, resetUserPassword,
  AdminUser, AdminUserCreateIn,
  fetchUsageStats, generateInviteLink, UsageStats,
} from '@/lib/api';
import {
  Shield, Key, Trash2, Plus, Power, Building2,
  CheckCircle2, XCircle, Users, ChevronDown, ChevronUp,
  UserPlus, RefreshCw, Lock, BarChart3, Copy,
} from 'lucide-react';

interface ApiKey { id: string; name: string; prefix: string; created_at: string; last_used_at?: string; is_active: boolean; }
interface SystemFlag { key: string; value: unknown; updated_at?: string; }

const fetchApiKeys   = () => api.get<ApiKey[]>('/admin/api-keys').then((r) => r.data);
const fetchFlags     = () => api.get<SystemFlag[]>('/admin/flags').then((r) => r.data).catch(() => [] as SystemFlag[]);
const deleteKey      = (id: string) => api.delete(`/admin/api-keys/${id}`);
const toggleMaint    = (enabled: boolean) => api.post('/admin/maintenance-mode', null, { params: { enabled } });
const updateFlag     = (flagName: string, value: string) => api.put(`/admin/flags/${flagName}`, { value });

type Tab = 'tenants' | 'keys' | 'flags' | 'users' | 'usage';

const ROLES = ['ADMIN', 'AML_ANALYST', 'AUDITOR'] as const;
const ROLE_COLORS: Record<string, string> = {
  ADMIN:        'bg-purple-100 text-purple-700',
  AML_ANALYST:  'bg-blue-100 text-blue-700',
  AUDITOR:      'bg-gray-100 text-gray-600',
  SUPER_ADMIN:  'bg-red-100 text-red-700',
};

// ── User Create Form ──────────────────────────────────────────────────────────
function UserCreateForm({ onSuccess }: { onSuccess: () => void }) {
  const [form, setForm] = useState<AdminUserCreateIn>({ username: '', email: '', password: '', role: 'AML_ANALYST' });
  const mut = useMutation({
    mutationFn: () => createAdminUser(form),
    onSuccess: () => { setForm({ username: '', email: '', password: '', role: 'AML_ANALYST' }); onSuccess(); },
  });
  return (
    <div className="rounded-xl border border-blue-100 bg-blue-50/40 p-5">
      <h3 className="mb-4 text-sm font-semibold text-blue-800">Novo Usuário</h3>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="mb-1 block text-xs font-medium text-gray-600">Username *</label>
          <input value={form.username} onChange={(e) => setForm(f => ({ ...f, username: e.target.value }))}
            placeholder="analista_01"
            className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400" />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-gray-600">Email *</label>
          <input type="email" value={form.email} onChange={(e) => setForm(f => ({ ...f, email: e.target.value }))}
            placeholder="analista@bet.com"
            className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400" />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-gray-600">Senha *</label>
          <input type="password" value={form.password} onChange={(e) => setForm(f => ({ ...f, password: e.target.value }))}
            placeholder="mín. 8 caracteres"
            className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400" />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-gray-600">Perfil *</label>
          <select value={form.role} onChange={(e) => setForm(f => ({ ...f, role: e.target.value }))}
            className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400">
            {ROLES.map(r => <option key={r} value={r}>{r}</option>)}
          </select>
        </div>
      </div>
      <button onClick={() => mut.mutate()}
        disabled={!form.username || !form.email || !form.password || mut.isPending}
        className="mt-4 flex items-center gap-2 rounded-lg bg-blue-600 px-5 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-50">
        <UserPlus size={15} /> {mut.isPending ? 'Criando…' : 'Criar Usuário'}
      </button>
      {mut.isError && (
        <p className="mt-2 text-xs text-red-600">
          {(mut.error as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Erro ao criar usuário'}
        </p>
      )}
    </div>
  );
}

// ── Reset Password Modal ───────────────────────────────────────────────────────
function ResetPwModal({ user, onClose }: { user: AdminUser; onClose: () => void }) {
  const [pw, setPw] = useState('');
  const mut = useMutation({ mutationFn: () => resetUserPassword(user.id, pw), onSuccess: onClose });
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div className="w-full max-w-sm rounded-2xl bg-white p-6 shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <h3 className="mb-4 text-sm font-semibold text-gray-800">Redefinir senha — {user.username}</h3>
        <input type="password" value={pw} onChange={(e) => setPw(e.target.value)}
          placeholder="Nova senha (mín. 8 chars)"
          className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand" />
        <div className="mt-4 flex gap-3">
          <button onClick={onClose} className="flex-1 rounded-lg border border-gray-200 px-4 py-2 text-sm text-gray-600 hover:bg-gray-50">Cancelar</button>
          <button onClick={() => mut.mutate()} disabled={pw.length < 8 || mut.isPending}
            className="flex-1 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:opacity-90 disabled:opacity-50">
            {mut.isPending ? 'Salvando…' : 'Redefinir'}
          </button>
        </div>
        {mut.isError && <p className="mt-2 text-xs text-red-600">Erro ao redefinir senha.</p>}
      </div>
    </div>
  );
}

// ── Invite Modal ───────────────────────────────────────────────────────────────
function InviteModal({ onClose }: { onClose: () => void }) {
  const [email, setEmail] = useState('');
  const [role, setRole] = useState('AML_ANALYST');
  const [result, setResult] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const mut = useMutation({
    mutationFn: () => generateInviteLink({ email, role }),
    onSuccess: (data) => setResult(data.invite_link),
  });
  const copy = () => {
    if (result) { navigator.clipboard.writeText(result); setCopied(true); setTimeout(() => setCopied(false), 2000); }
  };
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <h3 className="mb-4 text-sm font-semibold text-gray-800">Convidar Usuário</h3>
        {!result ? (
          <>
            <div className="space-y-3">
              <div>
                <label className="mb-1 block text-xs font-medium text-gray-600">Email *</label>
                <input type="email" value={email} onChange={(e) => setEmail(e.target.value)}
                  placeholder="usuario@bet.com"
                  className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand" />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-gray-600">Perfil *</label>
                <select value={role} onChange={(e) => setRole(e.target.value)}
                  className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand">
                  <option value="AML_ANALYST">AML_ANALYST</option>
                  <option value="AUDITOR">AUDITOR</option>
                  <option value="ADMIN">ADMIN</option>
                </select>
              </div>
            </div>
            <div className="mt-4 flex gap-3">
              <button onClick={onClose} className="flex-1 rounded-lg border border-gray-200 px-4 py-2 text-sm text-gray-600 hover:bg-gray-50">Cancelar</button>
              <button onClick={() => mut.mutate()} disabled={!email || mut.isPending}
                className="flex-1 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:opacity-90 disabled:opacity-50">
                {mut.isPending ? 'Gerando…' : 'Gerar Link'}
              </button>
            </div>
            {mut.isError && <p className="mt-2 text-xs text-red-600">Erro ao gerar convite.</p>}
          </>
        ) : (
          <>
            <p className="mb-2 text-xs text-gray-500">Link de convite — válido por 48 horas. Compartilhe manualmente.</p>
            <div className="flex gap-2">
              <input readOnly value={result}
                className="flex-1 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-xs font-mono text-gray-700" />
              <button onClick={copy}
                className="flex items-center gap-1 rounded-lg border border-gray-200 px-3 py-2 text-xs text-gray-600 hover:bg-gray-50">
                <Copy size={13} /> {copied ? 'Copiado!' : 'Copiar'}
              </button>
            </div>
            <button onClick={onClose} className="mt-4 w-full rounded-lg border border-gray-200 px-4 py-2 text-sm text-gray-600 hover:bg-gray-50">Fechar</button>
          </>
        )}
      </div>
    </div>
  );
}

// ── Tenant Form ───────────────────────────────────────────────────────────────
function TenantCreateForm({ onSuccess }: { onSuccess: () => void }) {
  const [form, setForm] = useState<TenantCreatePayload>({
    name: '', slug: '', admin_username: '', admin_email: '', admin_password: '',
    risk_score_threshold: 0.75,
  });
  const [result, setResult] = useState<{ admin_username: string; message: string } | null>(null);

  const mut = useMutation({
    mutationFn: () => createTenant(form),
    onSuccess: (data) => {
      setResult(data);
      setForm({ name: '', slug: '', admin_username: '', admin_email: '', admin_password: '', risk_score_threshold: 0.75 });
      onSuccess();
    },
  });

  const slugify = (val: string) => val.toLowerCase().replace(/\s+/g, '-').replace(/[^a-z0-9_-]/g, '');

  return (
    <div className="rounded-xl border border-blue-100 bg-blue-50/40 p-5">
      <h3 className="mb-4 text-sm font-semibold text-blue-800">Novo Operador (Tenant)</h3>

      {result && (
        <div className="mb-4 rounded-lg border border-green-200 bg-green-50 p-3 text-sm text-green-800">
          ✅ {result.message} — Login inicial: <strong>{result.admin_username}</strong>
        </div>
      )}

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="mb-1 block text-xs font-medium text-gray-600">Nome do Operador *</label>
          <input
            value={form.name}
            onChange={(e) => setForm(f => ({ ...f, name: e.target.value, slug: slugify(e.target.value) }))}
            placeholder="Bet Esportiva Ltda"
            className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-gray-600">Slug (identificador) *</label>
          <input
            value={form.slug}
            onChange={(e) => setForm(f => ({ ...f, slug: slugify(e.target.value) }))}
            placeholder="bet-esportiva"
            className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-blue-400"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-gray-600">Usuário Admin *</label>
          <input
            value={form.admin_username}
            onChange={(e) => setForm(f => ({ ...f, admin_username: e.target.value }))}
            placeholder="admin_bet"
            className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-gray-600">Email Admin *</label>
          <input
            type="email"
            value={form.admin_email}
            onChange={(e) => setForm(f => ({ ...f, admin_email: e.target.value }))}
            placeholder="admin@betesportiva.com"
            className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-gray-600">Senha Admin (mín. 8 chars) *</label>
          <input
            type="password"
            value={form.admin_password}
            onChange={(e) => setForm(f => ({ ...f, admin_password: e.target.value }))}
            placeholder="••••••••"
            className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-gray-600">Threshold de Risco (0–1)</label>
          <input
            type="number" min={0} max={1} step={0.05}
            value={form.risk_score_threshold}
            onChange={(e) => setForm(f => ({ ...f, risk_score_threshold: parseFloat(e.target.value) }))}
            className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
          />
        </div>
      </div>

      <button
        onClick={() => mut.mutate()}
        disabled={!form.name || !form.slug || !form.admin_username || !form.admin_email || !form.admin_password || mut.isPending}
        className="mt-4 flex items-center gap-2 rounded-lg bg-blue-600 px-5 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
      >
        <Plus size={15} />
        {mut.isPending ? 'Criando…' : 'Criar Operador'}
      </button>
      {mut.isError && (
        <p className="mt-2 text-xs text-red-600">
          Erro: {(mut.error as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Tente novamente'}
        </p>
      )}
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────────
export default function AdminPage() {
  const qc = useQueryClient();
  const [activeTab, setActiveTab] = useState<Tab>('keys');
  const [showCreateTenant, setShowCreateTenant] = useState(false);
  const [showCreateUser, setShowCreateUser]     = useState(false);
  const [resetTarget, setResetTarget]           = useState<AdminUser | null>(null);
  const [showInvite, setShowInvite]             = useState(false);
  const [newKeyName, setNewKeyName] = useState('');
  const [newKeyRaw, setNewKeyRaw]   = useState('');
  const [maintOn, setMaintOn]       = useState(false);

  // Data
  const { data: apiKeys = [], isLoading: loadingKeys }     = useQuery({ queryKey: ['api-keys'],     queryFn: fetchApiKeys });
  const { data: flags  = [], isLoading: loadingFlags }     = useQuery({ queryKey: ['system-flags'], queryFn: fetchFlags });
  const { data: tenants = [], isLoading: loadingTenants }  = useQuery({ queryKey: ['tenants'],      queryFn: fetchTenants });
  const { data: users  = [], isLoading: loadingUsers }     = useQuery({ queryKey: ['admin-users'],  queryFn: fetchAdminUsers, enabled: activeTab === 'users' });
  const { data: usageStats, isLoading: loadingUsage }      = useQuery<UsageStats>({ queryKey: ['usage-stats'], queryFn: fetchUsageStats, refetchInterval: 60_000, enabled: activeTab === 'usage' });

  // Mutations
  const createKey = useMutation({
    mutationFn: () => api.post<{ raw_key: string }>('/admin/api-keys', { name: newKeyName }),
    onSuccess: (res) => { setNewKeyRaw(res.data.raw_key); setNewKeyName(''); qc.invalidateQueries({ queryKey: ['api-keys'] }); },
  });
  const removeKey    = useMutation({ mutationFn: deleteKey,   onSuccess: () => qc.invalidateQueries({ queryKey: ['api-keys'] }) });
  const maint        = useMutation({ mutationFn: (v: boolean) => toggleMaint(v), onSuccess: () => qc.invalidateQueries({ queryKey: ['system-flags'] }) });
  const saveFlag     = useMutation({ mutationFn: ({ name, value }: { name: string; value: string }) => updateFlag(name, value), onSuccess: () => qc.invalidateQueries({ queryKey: ['system-flags'] }) });
  const toggleTenant = useMutation({
    mutationFn: ({ id, active }: { id: string; active: boolean }) => updateTenant(id, { active }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['tenants'] }),
  });
  const toggleUser = useMutation({
    mutationFn: ({ id, active }: { id: string; active: boolean }) => updateAdminUser(id, { active }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin-users'] }),
  });
  const changeRole = useMutation({
    mutationFn: ({ id, role }: { id: string; role: string }) => updateAdminUser(id, { role }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin-users'] }),
  });
  const removeUser = useMutation({
    mutationFn: (id: string) => deleteAdminUser(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin-users'] }),
  });

  const tabs: { id: Tab; label: string; icon: React.ReactNode }[] = [
    { id: 'tenants', label: 'Operadores',   icon: <Building2 size={14} /> },
    { id: 'users',   label: 'Usuários',     icon: <Users size={14} /> },
    { id: 'keys',    label: 'Chaves de API',icon: <Key size={14} /> },
    { id: 'flags',   label: 'Feature Flags',icon: <Shield size={14} /> },
    { id: 'usage',   label: 'Uso',          icon: <BarChart3 size={14} /> },
  ];

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Shield size={22} className="text-brand" />
          <h1 className="text-2xl font-bold text-gray-900">Administração</h1>
        </div>
      </div>

      {/* Maintenance toggle — always visible */}
      <section className="rounded-xl border border-gray-100 bg-white p-4 shadow-sm">
        <div className="flex items-center gap-4">
          <Power size={15} className="text-gray-500" />
          <span className="text-sm font-semibold text-gray-700">Modo Manutenção</span>
          <label className="flex cursor-pointer items-center gap-3 ml-2">
            <div
              onClick={() => { const next = !maintOn; setMaintOn(next); maint.mutate(next); }}
              className={`relative h-6 w-11 rounded-full transition-colors ${maintOn ? 'bg-red-500' : 'bg-gray-200'}`}
            >
              <div className={`absolute top-1 h-4 w-4 rounded-full bg-white shadow transition-transform ${maintOn ? 'translate-x-5' : 'translate-x-1'}`} />
            </div>
            <span className={`text-sm ${maintOn ? 'font-semibold text-red-600' : 'text-gray-500'}`}>
              {maintOn ? 'ATIVO — ingestão bloqueada' : 'Sistema operacional'}
            </span>
          </label>
        </div>
      </section>

      {/* Tabs */}
      <div className="flex gap-1 rounded-xl border border-gray-100 bg-gray-50 p-1">
        {tabs.map((t) => (
          <button
            key={t.id}
            onClick={() => setActiveTab(t.id)}
            className={`flex flex-1 items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition-colors
              ${activeTab === t.id ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}
          >
            {t.icon} {t.label}
          </button>
        ))}
      </div>

      {/* ── Tab: Operadores ──────────────────────────────────────────────── */}
      {activeTab === 'tenants' && (
        <section className="space-y-4">
          <button
            onClick={() => setShowCreateTenant(v => !v)}
            className="flex items-center gap-2 rounded-lg border border-blue-200 bg-blue-50 px-4 py-2 text-sm font-semibold text-blue-700 hover:bg-blue-100"
          >
            <Plus size={15} /> Novo Operador
            {showCreateTenant ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          </button>

          {showCreateTenant && (
            <TenantCreateForm onSuccess={() => { qc.invalidateQueries({ queryKey: ['tenants'] }); setShowCreateTenant(false); }} />
          )}

          <div className="rounded-xl border border-gray-100 bg-white shadow-sm">
            <div className="border-b border-gray-100 px-5 py-3">
              <h2 className="text-sm font-semibold text-gray-700">Operadores cadastrados ({tenants.length})</h2>
            </div>
            {loadingTenants ? (
              <div className="flex items-center justify-center py-10 text-gray-400 text-sm">Carregando…</div>
            ) : (
              <table className="w-full text-sm">
                <thead className="bg-gray-50 text-xs font-semibold uppercase text-gray-500">
                  <tr>
                    <th className="px-5 py-3 text-left">Operador</th>
                    <th className="px-5 py-3 text-left">Slug</th>
                    <th className="px-5 py-3 text-center">Usuários</th>
                    <th className="px-5 py-3 text-center">Status</th>
                    <th className="px-5 py-3 text-left">Criado em</th>
                    <th className="px-5 py-3" />
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-50">
                  {tenants.length === 0 && (
                    <tr><td colSpan={6} className="py-8 text-center text-gray-400">Nenhum operador encontrado</td></tr>
                  )}
                  {(tenants as TenantOut[]).map((t) => (
                    <tr key={t.id} className={`hover:bg-gray-50/60 ${!t.active ? 'opacity-50' : ''}`}>
                      <td className="px-5 py-3 font-medium text-gray-900">{t.name}</td>
                      <td className="px-5 py-3 font-mono text-xs text-gray-500">{t.slug}</td>
                      <td className="px-5 py-3 text-center">
                        <span className="flex items-center justify-center gap-1 text-gray-500">
                          <Users size={12} /> {t.user_count ?? '—'}
                        </span>
                      </td>
                      <td className="px-5 py-3 text-center">
                        {t.active
                          ? <span className="flex items-center justify-center gap-1 text-green-600"><CheckCircle2 size={14} /> Ativo</span>
                          : <span className="flex items-center justify-center gap-1 text-red-500"><XCircle size={14} /> Suspenso</span>
                        }
                      </td>
                      <td className="px-5 py-3 text-gray-500">{new Date(t.created_at).toLocaleDateString('pt-BR')}</td>
                      <td className="px-5 py-3 text-right">
                        <button
                          onClick={() => toggleTenant.mutate({ id: t.id, active: !t.active })}
                          disabled={toggleTenant.isPending}
                          className={`rounded-lg px-3 py-1 text-xs font-semibold transition-colors
                            ${t.active
                              ? 'border border-red-200 text-red-600 hover:bg-red-50'
                              : 'border border-green-200 text-green-600 hover:bg-green-50'
                            }`}
                        >
                          {t.active ? 'Suspender' : 'Reativar'}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </section>
      )}

      {/* ── Tab: Usuários ─────────────────────────────────────────────────── */}
      {activeTab === 'users' && (
        <section className="space-y-4">
          <div className="flex gap-3">
            <button
              onClick={() => setShowCreateUser(v => !v)}
              className="flex items-center gap-2 rounded-lg border border-blue-200 bg-blue-50 px-4 py-2 text-sm font-semibold text-blue-700 hover:bg-blue-100"
            >
              <UserPlus size={15} /> Novo Usuário
              {showCreateUser ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            </button>
            <button
              onClick={() => setShowInvite(true)}
              className="flex items-center gap-2 rounded-lg border border-purple-200 bg-purple-50 px-4 py-2 text-sm font-semibold text-purple-700 hover:bg-purple-100"
            >
              <UserPlus size={15} /> Convidar
            </button>
          </div>

          {showCreateUser && (
            <UserCreateForm onSuccess={() => { qc.invalidateQueries({ queryKey: ['admin-users'] }); setShowCreateUser(false); }} />
          )}

          <div className="rounded-xl border border-gray-100 bg-white shadow-sm">
            <div className="border-b border-gray-100 px-5 py-3">
              <h2 className="text-sm font-semibold text-gray-700">Usuários do tenant ({users.length})</h2>
            </div>
            {loadingUsers ? (
              <div className="flex items-center justify-center py-10 text-gray-400 text-sm">Carregando…</div>
            ) : (
              <table className="w-full text-sm">
                <thead className="bg-gray-50 text-xs font-semibold uppercase text-gray-500">
                  <tr>
                    <th className="px-5 py-3 text-left">Usuário</th>
                    <th className="px-5 py-3 text-left">Email</th>
                    <th className="px-5 py-3 text-left">Perfil</th>
                    <th className="px-5 py-3 text-center">Status</th>
                    <th className="px-5 py-3 text-left">Criado em</th>
                    <th className="px-5 py-3" />
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-50">
                  {users.length === 0 && (
                    <tr><td colSpan={6} className="py-8 text-center text-gray-400">Nenhum usuário encontrado</td></tr>
                  )}
                  {(users as AdminUser[]).map((u) => (
                    <tr key={u.id} className={`hover:bg-gray-50/60 ${!u.active ? 'opacity-50' : ''}`}>
                      <td className="px-5 py-3 font-medium text-gray-900">{u.username}</td>
                      <td className="px-5 py-3 text-gray-500">{u.email}</td>
                      <td className="px-5 py-3">
                        <select
                          value={u.role}
                          onChange={(e) => changeRole.mutate({ id: u.id, role: e.target.value })}
                          disabled={u.role === 'SUPER_ADMIN'}
                          className={`rounded-full px-2 py-0.5 text-xs font-semibold border-0 cursor-pointer focus:outline-none focus:ring-1 focus:ring-brand
                            ${ROLE_COLORS[u.role] ?? 'bg-gray-100 text-gray-600'}`}
                        >
                          {ROLES.map(r => <option key={r} value={r}>{r}</option>)}
                        </select>
                      </td>
                      <td className="px-5 py-3 text-center">
                        {u.active
                          ? <span className="flex items-center justify-center gap-1 text-green-600"><CheckCircle2 size={14} /> Ativo</span>
                          : <span className="flex items-center justify-center gap-1 text-red-500"><XCircle size={14} /> Inativo</span>
                        }
                      </td>
                      <td className="px-5 py-3 text-gray-500">{new Date(u.created_at).toLocaleDateString('pt-BR')}</td>
                      <td className="px-5 py-3">
                        <div className="flex items-center justify-end gap-1">
                          <button
                            onClick={() => setResetTarget(u)}
                            title="Redefinir senha"
                            className="rounded p-1.5 text-gray-400 hover:bg-gray-100 hover:text-brand"
                          >
                            <Lock size={13} />
                          </button>
                          <button
                            onClick={() => toggleUser.mutate({ id: u.id, active: !u.active })}
                            disabled={toggleUser.isPending || u.role === 'SUPER_ADMIN'}
                            title={u.active ? 'Desativar' : 'Reativar'}
                            className={`rounded p-1.5 hover:bg-gray-100 ${u.active ? 'text-gray-400 hover:text-orange-500' : 'text-gray-400 hover:text-green-600'}`}
                          >
                            <RefreshCw size={13} />
                          </button>
                          <button
                            onClick={() => { if (confirm(`Excluir usuário ${u.username}?`)) removeUser.mutate(u.id); }}
                            disabled={removeUser.isPending || u.role === 'SUPER_ADMIN'}
                            title="Excluir"
                            className="rounded p-1.5 text-gray-400 hover:bg-gray-100 hover:text-red-500 disabled:opacity-30"
                          >
                            <Trash2 size={13} />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </section>
      )}

      {/* ── Tab: Chaves de API ─────────────────────────────────────────────── */}
      {activeTab === 'keys' && (
        <section className="rounded-xl border border-gray-100 bg-white p-5 shadow-sm">
          <h2 className="mb-4 flex items-center gap-2 text-sm font-semibold text-gray-700">
            <Key size={15} /> Chaves de API
          </h2>

          <form className="mb-4 flex gap-3" onSubmit={(e) => { e.preventDefault(); createKey.mutate(); }}>
            <input
              value={newKeyName}
              onChange={(e) => setNewKeyName(e.target.value)}
              placeholder="Nome da chave (ex: integration-sap)"
              className="flex-1 rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand"
            />
            <button
              type="submit"
              disabled={!newKeyName || createKey.isPending}
              className="flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:bg-brand/90 disabled:opacity-50"
            >
              <Plus size={15} /> Gerar Chave
            </button>
          </form>

          {newKeyRaw && (
            <div className="mb-4 rounded-lg border border-green-200 bg-green-50 p-3">
              <p className="mb-1 text-xs font-semibold text-green-700">⚠️ Copie agora — não será exibida novamente:</p>
              <code className="break-all text-xs text-green-800 select-all">{newKeyRaw}</code>
            </div>
          )}

          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-xs font-semibold uppercase text-gray-500">
              <tr>
                <th className="px-4 py-2.5 text-left">Nome</th>
                <th className="px-4 py-2.5 text-left">Prefixo</th>
                <th className="px-4 py-2.5 text-left">Criado em</th>
                <th className="px-4 py-2.5 text-left">Último uso</th>
                <th className="px-4 py-2.5" />
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {loadingKeys && <tr><td colSpan={5} className="py-6 text-center text-gray-400">Carregando…</td></tr>}
              {apiKeys.map((k) => (
                <tr key={k.id} className={`hover:bg-gray-50/50 ${!k.is_active ? 'opacity-40 line-through' : ''}`}>
                  <td className="px-4 py-2.5 font-medium">{k.name}</td>
                  <td className="px-4 py-2.5 font-mono text-xs">{k.prefix}…</td>
                  <td className="px-4 py-2.5 text-gray-500">{new Date(k.created_at).toLocaleDateString('pt-BR')}</td>
                  <td className="px-4 py-2.5 text-gray-500">{k.last_used_at ? new Date(k.last_used_at).toLocaleString('pt-BR') : '—'}</td>
                  <td className="px-4 py-2.5 text-right">
                    <button onClick={() => removeKey.mutate(k.id)} className="rounded p-1 text-gray-400 hover:text-red-500" title="Revogar chave">
                      <Trash2 size={14} />
                    </button>
                  </td>
                </tr>
              ))}
              {!loadingKeys && apiKeys.length === 0 && (
                <tr><td colSpan={5} className="py-6 text-center text-gray-400">Nenhuma chave criada</td></tr>
              )}
            </tbody>
          </table>
        </section>
      )}

      {/* ── Tab: Feature Flags ─────────────────────────────────────────────── */}
      {activeTab === 'flags' && (
        <section className="rounded-xl border border-gray-100 bg-white p-5 shadow-sm">
          <h2 className="mb-4 text-sm font-semibold text-gray-700">Feature Flags do Sistema</h2>
          {loadingFlags && <p className="text-sm text-gray-400">Carregando…</p>}
          <div className="space-y-3">
            {flags.map((f) => {
              const flagName = f.key.split(':').slice(1).join(':');
              const currentValue = String(f.value ?? '');
              return (
                <div key={f.key} className="flex items-center gap-4 rounded-lg border border-gray-100 px-4 py-3">
                  <div className="flex-1">
                    <p className="text-sm font-medium text-gray-800">{flagName}</p>
                    {f.updated_at && (
                      <p className="text-xs text-gray-400">Atualizado: {new Date(f.updated_at).toLocaleString('pt-BR')}</p>
                    )}
                  </div>
                  <input
                    defaultValue={currentValue}
                    onBlur={(e) => { if (e.target.value !== currentValue) saveFlag.mutate({ name: flagName, value: e.target.value }); }}
                    className="w-40 rounded border border-gray-200 px-2 py-1 text-sm focus:outline-none focus:ring-1 focus:ring-brand"
                  />
                </div>
              );
            })}
            {!loadingFlags && flags.length === 0 && (
              <p className="rounded-lg border border-dashed border-gray-200 py-8 text-center text-sm text-gray-400">
                Nenhuma flag configurada para este tenant
              </p>
            )}
          </div>
        </section>
      )}

      {/* ── Tab: Uso ───────────────────────────────────────────────────── */}
      {activeTab === 'usage' && (
        <section className="space-y-4">
          <div className="rounded-xl border border-gray-100 bg-white p-5 shadow-sm">
            <h2 className="mb-4 flex items-center gap-2 text-sm font-semibold text-gray-700">
              <BarChart3 size={15} /> Uso do Mês Corrente
              {usageStats && <span className="ml-1 text-xs font-normal text-gray-400">— desde {usageStats.period}</span>}
            </h2>
            {loadingUsage ? (
              <p className="text-sm text-gray-400">Carregando…</p>
            ) : usageStats ? (
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                {[
                  { label: 'Eventos este mês', value: usageStats.events_this_month.toLocaleString('pt-BR') },
                  { label: 'Alertas este mês',  value: usageStats.alerts_this_month.toLocaleString('pt-BR') },
                  { label: 'Casos abertos',     value: usageStats.open_cases.toLocaleString('pt-BR') },
                  { label: 'Banco de dados',    value: `${usageStats.db_size_mb} MB` },
                  { label: 'Armazenamento MinIO', value: `${usageStats.minio_mb} MB` },
                ].map((s) => (
                  <div key={s.label} className="rounded-lg bg-gray-50 p-4 dark:bg-gray-800">
                    <p className="text-xs text-gray-500">{s.label}</p>
                    <p className="mt-1 text-xl font-bold text-gray-900 dark:text-gray-100">{s.value}</p>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-gray-400">Sem dados disponíveis.</p>
            )}
          </div>
        </section>
      )}

      {/* Reset password modal */}
      {resetTarget && <ResetPwModal user={resetTarget} onClose={() => setResetTarget(null)} />}

      {/* Invite modal */}
      {showInvite && <InviteModal onClose={() => setShowInvite(false)} />}
    </div>
  );
}

