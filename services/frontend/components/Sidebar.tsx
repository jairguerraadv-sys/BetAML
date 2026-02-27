'use client';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { clsx } from 'clsx';
import {
  LayoutDashboard, AlertTriangle, FolderOpen,
  BookOpen, Users, LogOut, Plug, ScrollText,
  Shield, List, BrainCircuit, Database,
  FileBarChart2, Bell, Settings, GitBranch,
} from 'lucide-react';

const NAV = [
  // ── Core ──────────────────────────────────
  { href: '/dashboard',      label: 'Dashboard',       icon: LayoutDashboard },
  { href: '/alerts',         label: 'Alertas',         icon: AlertTriangle },
  { href: '/cases',          label: 'Casos',           icon: FolderOpen },
  { href: '/rules',          label: 'Regras DSL',      icon: BookOpen },
  { href: '/rules/compound', label: 'Regras Compostas',icon: GitBranch },
  { href: '/players',        label: 'Jogadores',       icon: Users },
  { href: '/mappings',       label: 'Conectores',      icon: Plug },
  { href: '/audit-logs',     label: 'Auditoria',       icon: ScrollText },
  // ── Enterprise ────────────────────────────
  { href: '/player-lists',   label: 'Listas',          icon: List },
  { href: '/model-registry', label: 'Modelos ML',      icon: BrainCircuit },
  { href: '/feature-store',  label: 'Feature Store',   icon: Database },
  { href: '/reports',        label: 'Relatórios',      icon: FileBarChart2 },
  { href: '/notifications',  label: 'Notificações',    icon: Bell },
  { href: '/settings',       label: 'Configurações',   icon: Settings },
  { href: '/admin',          label: 'Admin',           icon: Shield },
];

export default function Sidebar() {
  const pathname = usePathname();
  const router   = useRouter();

  function logout() {
    localStorage.removeItem('betaml_token');
    localStorage.removeItem('betaml_user');
    router.push('/login');
  }

  return (
    <aside className="flex h-screen w-56 flex-col border-r border-gray-200 bg-white">
      {/* Logo */}
      <div className="px-5 py-5">
        <span className="text-xl font-extrabold text-brand">BetAML</span>
        <p className="text-[10px] font-medium uppercase tracking-widest text-gray-400">PLD/FT Intelligence</p>
      </div>

      {/* Nav */}
      <nav className="flex-1 space-y-0.5 px-3">
        {NAV.map(({ href, label, icon: Icon }) => (
          <Link
            key={href}
            href={href}
            className={clsx(
              'flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors',
              pathname.startsWith(href)
                ? 'bg-brand text-white'
                : 'text-gray-600 hover:bg-gray-100',
            )}
          >
            <Icon size={16} />
            {label}
          </Link>
        ))}
      </nav>

      {/* Sign out */}
      <div className="border-t border-gray-100 px-3 py-3">
        <button
          onClick={logout}
          className="flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm text-gray-600 hover:bg-gray-100"
        >
          <LogOut size={16} />
          Sair
        </button>
      </div>
    </aside>
  );
}
