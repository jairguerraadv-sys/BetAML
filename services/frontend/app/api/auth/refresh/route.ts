import { NextRequest, NextResponse } from 'next/server';

const BACKEND =
  process.env.BACKEND_API_URL ??
  process.env.NEXT_PUBLIC_API_URL ??
  'http://localhost:8000';

export async function POST(req: NextRequest) {
  const refreshToken = req.cookies.get('betaml_refresh_token')?.value;
  if (!refreshToken) {
    return NextResponse.json({ detail: 'Refresh token ausente' }, { status: 401 });
  }

  try {
    const backendRes = await fetch(`${BACKEND}/auth/refresh`, {
      method: 'POST',
      headers: {
        Cookie: `betaml_refresh_token=${refreshToken}`,
      },
    });
    const data = await backendRes.json().catch(() => ({}));

    if (!backendRes.ok) {
      return NextResponse.json(data, { status: backendRes.status });
    }

    const response = NextResponse.json({
      role: data.role,
      roles: data.roles ?? [],
      tenant_id: data.tenant_id,
    });

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
