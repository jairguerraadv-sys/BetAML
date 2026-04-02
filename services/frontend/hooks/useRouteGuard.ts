/**
 * hooks/useRouteGuard.ts — Proteção de rotas por papel (client-side)
 *
 * Use este hook em layouts ou páginas que requerem papéis específicos.
 * Em caso de acesso negado, redireciona para /forbidden.
 *
 * Nota de segurança: a proteção real é feita pelo backend (JWT + require_role*).
 *  O frontend apenas melhora a UX evitando chamadas que retornariam 403.
 *
 * Uso:
 *   // layout de /rules — apenas Gestor
 *   useRouteGuard(['Operador_Gestor']);
 *
 *   // layout de /platform — apenas SuperAdmin
 *   useRouteGuard(['BetAML_SuperAdmin']);
 */

'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { useCurrentUser } from './useCurrentUser';
import { type AppRole, hasAnyRole } from '../lib/nav-config';

interface RouteGuardOptions {
  /** Redireciona para cá em caso de acesso negado. Padrão: '/forbidden' */
  redirectTo?: string;
  /**
   * Se true, não redireciona enquanto os dados do usuário ainda estão
   * sendo carregados (evita flickering). Padrão: true
   */
  waitForUser?: boolean;
}

/**
 * Hook de guarda de rota por papel.
 *
 * @param requiredRoles   Um ou mais papéis requeridos (basta ter um deles).
 * @param options         Opções de comportamento.
 */
export function useRouteGuard(
  requiredRoles: AppRole[],
  options: RouteGuardOptions = {},
): void {
  const { redirectTo = '/forbidden', waitForUser = true } = options;
  const router = useRouter();
  const { user, loading } = useCurrentUser();

  useEffect(() => {
    if (waitForUser && loading) return;

    if (!user) {
      // Não autenticado → middleware.ts já deveria ter interceptado,
      // mas re-redireciona para login como fallback.
      router.replace('/login');
      return;
    }

    const userRoles = user.roles ?? (user.role ? [user.role] : []);
    if (!hasAnyRole(userRoles, requiredRoles)) {
      router.replace(redirectTo);
    }
  }, [user, loading, requiredRoles, redirectTo, waitForUser, router]);
}

/**
 * Variante booleana: retorna se o usuário atual tem acesso.
 * Útil para esconder condicionalmente seções de UI sem redirecionar.
 *
 * @example
 *   const canSeePlatform = useHasRole('BetAML_SuperAdmin');
 */
export function useHasRole(role: AppRole): boolean {
  const { user } = useCurrentUser();
  if (!user) return false;
  const userRoles = user.roles ?? (user.role ? [user.role] : []);
  return userRoles.includes(role);
}

/**
 * Variante booleana para múltiplos papéis (OR).
 */
export function useHasAnyRole(roles: AppRole[]): boolean {
  const { user } = useCurrentUser();
  if (!user) return false;
  const userRoles = user.roles ?? (user.role ? [user.role] : []);
  return hasAnyRole(userRoles, roles);
}
