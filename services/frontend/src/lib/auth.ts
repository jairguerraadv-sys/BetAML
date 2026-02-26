import type { User } from './types';

const ACCESS_KEY = 'betaml_access_token';
const REFRESH_KEY = 'betaml_refresh_token';

export function storeTokens(access: string, refresh: string): void {
  if (typeof window === 'undefined') return;
  localStorage.setItem(ACCESS_KEY, access);
  localStorage.setItem(REFRESH_KEY, refresh);
}

export function getAccessToken(): string | null {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem(ACCESS_KEY);
}

export function getRefreshToken(): string | null {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem(REFRESH_KEY);
}

export function clearTokens(): void {
  if (typeof window === 'undefined') return;
  localStorage.removeItem(ACCESS_KEY);
  localStorage.removeItem(REFRESH_KEY);
}

interface JwtPayload {
  sub: string;
  email: string;
  full_name: string;
  role: User['role'];
  exp: number;
  iat: number;
}

export function parseJwt(token: string): JwtPayload | null {
  try {
    const base64 = token.split('.')[1];
    if (!base64) return null;
    const json = atob(base64.replace(/-/g, '+').replace(/_/g, '/'));
    return JSON.parse(json) as JwtPayload;
  } catch {
    return null;
  }
}

export function getCurrentUser(): User | null {
  const token = getAccessToken();
  if (!token) return null;
  const payload = parseJwt(token);
  if (!payload) return null;
  // Client-side expiry check – UX optimisation only.
  // The API's 401 interceptor is the authoritative security boundary.
  if (payload.exp * 1000 < Date.now()) {
    clearTokens();
    return null;
  }
  return {
    id: payload.sub,
    email: payload.email,
    full_name: payload.full_name,
    role: payload.role,
    is_active: true,
    created_at: '',
  };
}

export function isAuthenticated(): boolean {
  return getCurrentUser() !== null;
}
