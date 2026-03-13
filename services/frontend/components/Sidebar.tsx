'use client';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { clsx } from 'clsx';
import { useState, useEffect } from 'react';
import {
  LayoutDashboard, AlertTriangle, FolderOpen,
  FileBarChart2, Bell, LogOut, SlidersHorizontal,
  ChevronDown, BookOpen, Users, Plug, ScrollText,
  Shield, List, BrainCircuit, Database, GitBranch, Wand2,
  Settings, HelpCircle, Search, Moon, Sun,
} from 'lucide-react';
import { useTheme } from './ThemeProvider';

// ── Jornadas principais do analista ──────────────────────────────────────────
const MAIN_NAV = [
  { href: '/dashboard',   label: 'Painel Diário',          icon: LayoutDashboard, tooltip: 'Resumo do dia: alertas, casos pendentes e KPIs' },
  { href: '/alerts',      label: 'Monitor de Alertas',     icon: AlertTriangle,   tooltip: 'Fila de alertas por prioridade para triagem' },
  { href: '/cases',       label: 'Casos em Investigação',  icon: FolderOpen,      tooltip: 'Gerencie investigações em andamento' },
  { href: '/sensitivity', label: 'Ajustes de Sensibilidade', icon: SlidersHorizontal, tooltip: 'Calibre o volume e precisão dos alertas' },
  { href: '/reports',     label: 'Relatórios Reguladores', icon: FileBarChart2,   tooltip: 'Gere dossiês e relatórios para COAF/BACEN' },
  { href: '/notifications', label: 'Notificações',         icon: Bell,            tooltip: 'Alertas enviados para você' },
];

// ── Configurações avançadas — visíveis só para senior/admin ──────────────────
const ADV_NAV = [
  { href: '/rules/builder',  label: 'Construtor de Regras',   icon: Wand2 },
  { href: '/rules',          label: 'Condições de Risco',     icon: BookOpen },
  { href: '/rules/compound', label: 'Regras Compostas',       icon: GitBranch },
  { href: '/players',        label: 'Perfis de Clientes',     icon: Users },
  { href: '/player-lists',   label: 'Listas de Monitoramento',icon: List },
  { href: '/model-registry', label: 'Modelos Analíticos',     icon: BrainCircuit },
  { href: '/feature-store',  label: 'Base de Indicadores',    icon: Database },
  { href: '/mappings',       label: 'Conectores',             icon: Plug },
  { href: '/audit-logs',     label: 'Log de Auditoria',       icon: ScrollText },
  { href: '/settings',       label: 'Parâmetros de Sistema',  icon: Settings },
  { href: '/admin',          label: 'Administração',          icon: Shield },
];

// Roles que veem o menu avançado
const ADVANCED_ROLES = ['admin', 'senior_analyst', 'sysadmin'];

function NavItem({ href, label, icon: Icon, tooltip, active }: {
  href: string; label: string; icon: React.ElementType;
  tooltip?: string; active: boolean;
}) {
  return (
    <Link
      href={href}
      title={tooltip}
      className={clsx(
        'group flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium transition-colors',
        active ? 'bg-brand text-white' : 'text-gray-600 hover:bg-gray-100',
      )}
    >
      <Icon size={15} />
      <span className="flex-1 truncate">{label}</span>
      {tooltip && (
        <HelpCircle
          size={12}
          className={clsx('shrink-0 opacity-0 group-hover:opacity-60 transition-opacity', active && 'text-white')}
        />
      )}
    </Link>
  );
}

export default function Sidebar() {
  const pathname = usePathname();
  const router   = useRouter();
  const { theme, toggle: toggleTheme } = useTheme();
  const [role, setRole]         = useState<string>('analyst');
  const [userName, setUserName] = useState<string>('');
  const [advOpen, setAdvOpen]   = useState(false);

  useEffect(() => {
    try {
      const raw = localStorage.getItem('betaml_user');
      if (raw) {
        const u = JSON.parse(raw);
        setRole(u.role ?? 'analyst');
        setUserName(u.username ?? u.email ?? '');
      }
    } catch {}
    // Auto-open advanced if on an advanced route
    if (ADV_NAV.some((n) => pathname.startsWith(n.href))) setAdvOpen(true);
  }, [pathname]);

  const canSeeAdvanced = ADVANCED_ROLES.includes(role);

  function logout() {
    localStorage.removeItem('betaml_token');
    localStorage.removeItem('betaml_user');
    router.push('/login');
  }

  return (
    <aside className="flex h-screen w-60 flex-col border-r border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-900">
      {/* Logo + usuário */}
      <div className="px-4 py-4 border-b border-gray-100 dark:border-gray-700">
        <span className="text-lg font-extrabold text-brand">BetAML</span>
        <p className="text-[10px] font-medium uppercase tracking-widest text-gray-400">PLD/FT Intelligence</p>
        {userName && (
          <p className="mt-1.5 text-[11px] text-gray-500 truncate dark:text-gray-400">
            👤 {userName} · <span className="capitalize">{role.replace('_', ' ')}</span>
          </p>
        )}
      </div>

      {/* Busca rápida */}
      <div className="px-2 pt-3">
        <button
          onClick={() => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', metaKey: true, bubbles: true }))}
          className="flex w-full items-center gap-2 rounded-lg border border-gray-200 bg-gray-50 px-3 py-1.5 text-xs text-gray-400 transition hover:border-gray-300 hover:bg-gray-100 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-500 dark:hover:bg-gray-700"
        >
          <Search size={12} />
          <span className="flex-1 text-left">Buscar…</span>
          <kbd className="rounded bg-gray-200 px-1 text-[10px] dark:bg-gray-600">⌘K</kbd>
        </button>
      </div>

      {/* Jornadas principais */}
      <nav className="flex-1 overflow-y-auto px-2 py-3 space-y-0.5">
        <p className="px-3 mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-gray-400">
          Meu Trabalho
        </p>
        {MAIN_NAV.map((item) => (
          <NavItem
            key={item.href}
            {...item}
            active={pathname === item.href || (item.href !== '/dashboard' && pathname.startsWith(item.href))}
          />
        ))}

        {/* Configurações avançadas — colapsável */}
        {canSeeAdvanced && (
          <div className="mt-4">
            <button
              onClick={() => setAdvOpen((v) => !v)}
              className="flex w-full items-center gap-2 px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wider text-gray-400 hover:text-gray-600 transition-colors"
            >
              <span className="flex-1 text-left">Configurações Avançadas</span>
              <ChevronDown
                size={13}
                className={clsx('transition-transform', advOpen && 'rotate-180')}
              />
            </button>

            {advOpen && (
              <div className="mt-0.5 space-y-0.5">
                {ADV_NAV.map((item) => (
                  <NavItem
                    key={item.href}
                    {...item}
                    active={pathname === item.href || (item.href !== '/settings' && pathname.startsWith(item.href))}
                  />
                ))}
              </div>
            )}
          </div>
        )}
      </nav>

      {/* Footer: dark mode + sign out */}
      <div className="border-t border-gray-100 px-2 py-3 space-y-1 dark:border-gray-700">
        <button
          onClick={toggleTheme}
          className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-sm text-gray-500 hover:bg-gray-100 transition-colors dark:text-gray-400 dark:hover:bg-gray-800"
        >
          {theme === 'dark' ? <Sun size={15} /> : <Moon size={15} />}
          {theme === 'dark' ? 'Modo claro' : 'Modo escuro'}
        </button>
        <button
          onClick={logout}
          className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-sm text-gray-500 hover:bg-gray-100 transition-colors dark:text-gray-400 dark:hover:bg-gray-800"
        >
          <LogOut size={15} />
          Sair da conta
        </button>
      </div>
    </aside>
  );
}
