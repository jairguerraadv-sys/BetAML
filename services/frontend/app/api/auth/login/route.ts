/**
 * API Route — POST /api/auth/login
 *
 * Proxies credenciais para o backend FastAPI e persiste o JWT em um
 * cookie httpOnly (não acessível via JS, imune a XSS).
 *
 * O cliente Next.js chama este endpoint em vez de chamar o backend diretamente.
 */
import { NextRequest, NextResponse } from 'next/server';

const BACKEND =
  process.env.BACKEND_API_URL ??
  process.env.NEXT_PUBLIC_API_URL ??
  'http://localhost:8000';

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();

    const backendRes = await fetch(`${BACKEND}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });

    const data = await backendRes.json();

    if (!backendRes.ok) {
      return NextResponse.json(data, { status: backendRes.status });
    }

    const response = NextResponse.json({
      role: data.role,
      roles: data.roles ?? [],
      tenant_id: data.tenant_id,
    });

    // Token armazenado apenas em cookie httpOnly — nunca exposto ao JS do browser
    response.cookies.set('betaml_token', data.access_token, {
      httpOnly: true,
      secure: process.env.NODE_ENV === 'production',
      sameSite: 'strict',
      path: '/',
      maxAge: 15 * 60,
    });

    if (data.refresh_token) {
      response.cookies.set('betaml_refresh_token', data.refresh_token, {
        httpOnly: true,
        secure: process.env.NODE_ENV === 'production',
        sameSite: 'strict',
        path: '/',
        maxAge: 7 * 24 * 60 * 60,
      });
    }

    // Lista de papéis em cookie separado para uso no Edge Middleware
    // (NÃO é prova de adulteração; o backend valida o JWT em toda requisição)
    response.cookies.set('betaml_roles', encodeURIComponent(JSON.stringify(data.roles ?? [])), {
      httpOnly: true,
      secure: process.env.NODE_ENV === 'production',
      sameSite: 'strict',
      path: '/',
      maxAge: 15 * 60,
    });

    return response;
  } catch {
    return NextResponse.json({ detail: 'Erro interno' }, { status: 500 });
  }
}
