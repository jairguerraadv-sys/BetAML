'use client';
import { useUser } from '@/contexts/UserContext';

export interface CurrentUser {
  id?: string;
  username: string;
  role: string; // 'ADMIN' | 'AML_ANALYST' | 'AUDITOR'
  tenant_id: string;
}

export function useCurrentUser(): CurrentUser | null {
  const { user } = useUser();
  return (user as CurrentUser | null) ?? null;
}
