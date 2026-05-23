/**
 * middleware.ts — Next.js Edge Middleware
 *
 * Responsabilidades:
 *  1. Proteger rotas autenticadas: redireciona para /login se não há cookie betaml_token
 *  2. Proteger rotas de plataforma (/platform/**): exige papel BetAML_SuperAdmin
 *     verificado via cookie betaml_roles (definido na rota de login)
 *  3. Em modo on-prem (NEXT_PUBLIC_DEPLOYMENT_MODE=onprem) redirecionar /platform/**
 *     para /forbidden — o console multi-tenant não existe em single-tenant.
 *  4. Injetar o token como header Authorization nas chamadas /api-proxy/* (rewrite)
 *
 * Nota de segurança: o backend valida o JWT em TODA chamada à API.
 *  O middleware apenas melhora a UX ao redirecionar antes de uma chamada 403.
 */
import { NextRequest, NextResponse } from 'next/server';
import { canAccessRoute } from './lib/nav-config';

// Rotas que NÃO requerem autenticação
const PUBLIC_PATHS = ['/login', '/api/auth/login', '/api/auth/logout', '/_next', '/favicon.ico', '/forbidden'];

// Deployment mode vem de variável de ambiente build-time
const DEPLOYMENT_MODE = process.env.NEXT_PUBLIC_DEPLOYMENT_MODE ?? 'saas';
const IS_ONPREM = DEPLOYMENT_MODE === 'onprem';

export function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;
  const token = req.cookies.get('betaml_token')?.value;

  // Ignorar rotas públicas e assets
  if (PUBLIC_PATHS.some((p) => pathname.startsWith(p))) {
    return NextResponse.next();
  }

  // Redirecionar para login se não autenticado
  if (!token) {
    const url = req.nextUrl.clone();
    url.pathname = '/login';
    return NextResponse.redirect(url);
  }

  // Rotas /platform/** não possuem páginas reais no frontend atual.
  if (pathname.startsWith('/platform')) {
    const url = req.nextUrl.clone();
    url.pathname = '/forbidden';
    return NextResponse.redirect(url);
  }

  const rolesRaw = req.cookies.get('betaml_roles')?.value ?? '';
  let roles: string[] = [];
  try {
    roles = JSON.parse(decodeURIComponent(rolesRaw)) as string[];
  } catch {
    // cookie inválido ou ausente
  }

  if (pathname === '/') {
    const target = roles.includes('Operador_Analista') || roles.includes('Operador_Gestor')
      ? '/dashboard'
      : roles.includes('Operador_AdminTecnico')
        ? '/admin'
        : roles.includes('BetAML_SuperAdmin')
          ? '/admin/onboarding'
          : '/dashboard';
    const url = req.nextUrl.clone();
    url.pathname = target;
    return NextResponse.redirect(url);
  }

  if (!canAccessRoute(pathname, roles)) {
    const url = req.nextUrl.clone();
    url.pathname = '/forbidden';
    return NextResponse.redirect(url);
  }

  // Para chamadas de proxy ao backend, injetar o token como Authorization header
  const requestHeaders = new Headers(req.headers);
  requestHeaders.set('Authorization', `Bearer ${token}`);

  return NextResponse.next({ request: { headers: requestHeaders } });
}

export const config = {
  // Aplica o middleware a todas as rotas exceto _next/static e _next/image
  matcher: ['/((?!_next/static|_next/image|favicon.ico).*)'],
};
