/**
 * API Route — POST /api/auth/logout
 *
 * Chama o backend para revogar o JWT via blacklist Redis e expira o cookie httpOnly.
 */
import { NextRequest, NextResponse } from 'next/server';

const BACKEND =
  process.env.BACKEND_API_URL ??
  process.env.NEXT_PUBLIC_API_URL ??
  'http://localhost:8000';

export async function POST(req: NextRequest) {
  const token = req.cookies.get('betaml_token')?.value;

  // Mesmo sem token, limpar o cookie e retornar 200
  if (token) {
    try {
      await fetch(`${BACKEND}/auth/logout`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      });
    } catch {
      // Falha silenciosa — cookie será expirado de qualquer forma
    }
  }

  const response = NextResponse.json({ message: 'Logout realizado' });
  response.cookies.set('betaml_token', '', {
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production',
    sameSite: 'strict',
    path: '/',
    maxAge: 0, // expira imediatamente
  });
  return response;
}
