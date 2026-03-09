/**
 * middleware.ts — Next.js Edge Middleware
 *
 * Responsabilidades:
 *  1. Proteger rotas autenticadas: redireciona para /login se não há cookie betaml_token
 *  2. Injetar o token como header Authorization nas chamadas /api-proxy/* (rewrite)
 *     — isso elimina a necessidade de ler o token via JS no cliente.
 */
import { NextRequest, NextResponse } from 'next/server';

// Rotas que NÃO requerem autenticação
const PUBLIC_PATHS = ['/login', '/api/auth/login', '/api/auth/logout', '/_next', '/favicon.ico'];

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

  // Para chamadas de proxy ao backend, injetar o token como Authorization header
  // O Next.js rewrite já encaminha para o backend; aqui garantimos o header.
  const requestHeaders = new Headers(req.headers);
  requestHeaders.set('Authorization', `Bearer ${token}`);

  return NextResponse.next({ request: { headers: requestHeaders } });
}

export const config = {
  // Aplica o middleware a todas as rotas exceto _next/static e _next/image
  matcher: ['/((?!_next/static|_next/image|favicon.ico).*)'],
};
