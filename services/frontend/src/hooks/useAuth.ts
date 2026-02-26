'use client';

import { useState, useEffect, useCallback } from 'react';
import { getCurrentUser, isAuthenticated, clearTokens } from '@/lib/auth';
import type { User } from '@/lib/types';

export function useAuth() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setUser(getCurrentUser());
    setLoading(false);
  }, []);

  const logout = useCallback(() => {
    clearTokens();
    window.location.href = '/login';
  }, []);

  return { user, loading, isAuthenticated: isAuthenticated(), logout };
}
