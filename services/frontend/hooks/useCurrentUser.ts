'use client';
import { useUser } from '@/contexts/UserContext';
import type { AppRole } from '@/lib/nav-config';

export interface CurrentUser {
  id?: string;
  username: string;
  email?: string;
  role: string;
  /** Lista de papéis no modelo multi-tenant */
  roles?: string[];
  tenant_id: string;
}

export interface UseCurrentUserReturn {
  user: CurrentUser | null;
  loading: boolean;
  /** Retorna true se o usuário possui o papel especificado. */
  hasRole: (role: AppRole) => boolean;
  /** Retorna true se o usuário possui ao menos um dos papéis. */
  hasAnyRole: (roles: AppRole[]) => boolean;
}

export function useCurrentUser(): UseCurrentUserReturn {
  const { user, loading } = useUser();

  const effectiveRoles: string[] = [
    ...(user?.roles ?? []),
    ...(user?.role ? [user.role] : []),
  ];

  return {
    user: user as CurrentUser | null,
    loading,
    hasRole: (role: AppRole) => effectiveRoles.includes(role),
    hasAnyRole: (roles: AppRole[]) => roles.some((r) => effectiveRoles.includes(r)),
  };
}
