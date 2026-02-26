'use client';

import { Bell, Menu } from 'lucide-react';
import type { User } from '@/lib/types';
import { roleLabel } from '@/lib/utils';

interface HeaderProps {
  user: User | null;
  title?: string;
  onMenuToggle?: () => void;
}

export function Header({ user, title, onMenuToggle }: HeaderProps) {
  return (
    <header className="flex h-16 items-center justify-between border-b border-gray-200 bg-white px-6">
      <div className="flex items-center gap-4">
        {onMenuToggle && (
          <button
            onClick={onMenuToggle}
            className="rounded-md p-1.5 text-gray-500 hover:bg-gray-100 lg:hidden"
            aria-label="Toggle menu"
          >
            <Menu className="h-5 w-5" />
          </button>
        )}
        {title && <h1 className="text-lg font-semibold text-gray-900">{title}</h1>}
      </div>
      <div className="flex items-center gap-3">
        <button className="rounded-md p-1.5 text-gray-500 hover:bg-gray-100" aria-label="Notifications">
          <Bell className="h-5 w-5" />
        </button>
        {user && (
          <div className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-full bg-blue-600 text-sm font-semibold text-white">
              {user.full_name.charAt(0).toUpperCase()}
            </div>
            <div className="hidden sm:block">
              <p className="text-sm font-medium text-gray-900">{user.full_name}</p>
              <p className="text-xs text-gray-500">{roleLabel(user.role)}</p>
            </div>
          </div>
        )}
      </div>
    </header>
  );
}
