'use client';
import { useEffect, useState } from 'react';

export interface CurrentUser {
  id?: string;
  username: string;
  role: string; // 'ADMIN' | 'AML_ANALYST' | 'AUDITOR'
  tenant_id: string;
}

export function useCurrentUser(): CurrentUser | null {
  const [user, setUser] = useState<CurrentUser | null>(null);
  useEffect(() => {
    try {
      const raw = localStorage.getItem('betaml_user');
      if (raw) setUser(JSON.parse(raw));
    } catch {}
  }, []);
  return user;
}
