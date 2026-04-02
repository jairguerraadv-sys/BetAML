/**
 * middleware.ts — Next.js Edge Middleware
 *
 * Responsabilidades:
 *  1. Proteger rotas autenticadas: redireciona para /login se não há cookie betaml_token
 *  2. Proteger rotas de plataforma (/platform/**): exige papel BetAML_SuperAdmin
 *     verificado via cookie betaml_roles (definido na rota de login)
 *  3. Injetar o token como header Authorization nas chamadas /api-proxy/* (rewrite)
 *
 * Nota de segurança: o backend valida o JWT em TODA chamada à API.
 *  O middleware apenas melhora a UX ao redirecionar antes de uma chamada 403.
 */
import { NextRequest, NextResponse } from 'next/server';

// Rotas que NÃO requerem autenticação
const PUBLIC_PATHS = ['/login', '/api/auth/login', '/api/auth/logout', '/_next', '/favicon.ico', '/forbidden'];

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

  // Proteger rotas de plataforma — apenas BetAML_SuperAdmin
  if (pathname.startsWith('/platform')) {
    const rolesRaw = req.cookies.get('betaml_roles')?.value ?? '';
    let roles: string[] = [];
    try {
      roles = JSON.parse(decodeURIComponent(rolesRaw)) as string[];
    } catch {
      // cookie inválido ou ausente
    }
    if (!roles.includes('BetAML_SuperAdmin')) {
      const url = req.nextUrl.clone();
      url.pathname = '/forbidden';
      return NextResponse.redirect(url);
    }
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
