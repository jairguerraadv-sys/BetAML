'use client';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { clsx } from 'clsx';
import {
  LayoutDashboard, AlertTriangle, FolderOpen,
  BookOpen, Users, LogOut, Plug, ScrollText,
} from 'lucide-react';

const NAV = [
  { href: '/dashboard',  label: 'Dashboard',   icon: LayoutDashboard },
  { href: '/alerts',     label: 'Alertas',     icon: AlertTriangle },
  { href: '/cases',      label: 'Casos',       icon: FolderOpen },
  { href: '/rules',      label: 'Regras DSL',  icon: BookOpen },
  { href: '/players',    label: 'Jogadores',   icon: Users },
  { href: '/mappings',   label: 'Conectores',  icon: Plug },
  { href: '/audit-logs', label: 'Auditoria',   icon: ScrollText },
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
