'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import {
  LayoutDashboard,
  Bell,
  Briefcase,
  BookOpen,
  Settings,
  ClipboardList,
  LogOut,
} from 'lucide-react';
import { cn, roleLabel } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import type { User } from '@/lib/types';

interface NavItem {
  label: string;
  href: string;
  icon: React.ComponentType<{ className?: string }>;
  roles?: User['role'][];
}

const navItems: NavItem[] = [
  { label: 'Dashboard', href: '/dashboard', icon: LayoutDashboard },
  { label: 'Alerts', href: '/alerts', icon: Bell },
  { label: 'Cases', href: '/cases', icon: Briefcase },
  { label: 'Rules', href: '/rules', icon: BookOpen },
  { label: 'Mapping Configs', href: '/mapping-configs', icon: Settings },
  {
    label: 'Audit Log',
    href: '/audit-log',
    icon: ClipboardList,
    roles: ['AUDITOR', 'ADMIN'],
  },
];

interface SidebarProps {
  user: User | null;
  onLogout: () => void;
}

export function Sidebar({ user, onLogout }: SidebarProps) {
  const pathname = usePathname();

  const visibleItems = navItems.filter(
    (item) => !item.roles || (user && item.roles.includes(user.role)),
  );

  return (
    <aside className="flex h-screen w-64 flex-col bg-[#0f172a] text-slate-300">
      {/* Logo */}
      <div className="flex h-16 items-center gap-2 border-b border-slate-800 px-6">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-blue-600">
          <span className="text-sm font-bold text-white">B</span>
        </div>
        <span className="text-lg font-bold text-white">BetAML</span>
      </div>

      {/* Nav */}
      <nav className="flex-1 overflow-y-auto px-3 py-4">
        <ul className="space-y-1">
          {visibleItems.map((item) => {
            const isActive = pathname === item.href || pathname.startsWith(item.href + '/');
            return (
              <li key={item.href}>
                <Link
                  href={item.href}
                  className={cn(
                    'flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors',
                    isActive
                      ? 'bg-blue-700 text-white'
                      : 'text-slate-400 hover:bg-slate-800 hover:text-white',
                  )}
                >
                  <item.icon className="h-5 w-5 shrink-0" />
                  {item.label}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>

      {/* User info + logout */}
      <div className="border-t border-slate-800 p-4">
        {user && (
          <div className="mb-3">
            <p className="truncate text-sm font-medium text-white">{user.full_name}</p>
            <p className="truncate text-xs text-slate-400">{user.email}</p>
            <Badge variant="info" className="mt-1 bg-blue-900/60 text-blue-300">
              {roleLabel(user.role)}
            </Badge>
          </div>
        )}
        <button
          onClick={onLogout}
          className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm text-slate-400 hover:bg-slate-800 hover:text-white transition-colors"
        >
          <LogOut className="h-4 w-4" />
          Sign out
        </button>
      </div>
    </aside>
  );
}
