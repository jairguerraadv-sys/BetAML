/**
 * API Route — POST /api/auth/login
 *
 * Proxies credenciais para o backend FastAPI e persiste o JWT em um
 * cookie httpOnly (não acessível via JS, imune a XSS).
 *
 * O cliente Next.js chama este endpoint em vez de chamar o backend diretamente.
 */
import { NextRequest, NextResponse } from 'next/server';

const BACKEND = process.env.NEXT_PUBLIC_API_URL ?? 'http://api:8000';

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
      tenant_id: data.tenant_id,
    });

    // Token armazenado apenas em cookie httpOnly — nunca exposto ao JS do browser
    response.cookies.set('betaml_token', data.access_token, {
      httpOnly: true,
      secure: process.env.NODE_ENV === 'production',
      sameSite: 'strict',
      path: '/',
      maxAge: 60 * 60, // 1 hora (sincronizado com ACCESS_TOKEN_EXPIRE_MIN=60)
    });

    return response;
  } catch {
    return NextResponse.json({ detail: 'Erro interno' }, { status: 500 });
  }
}
