'use client';
import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { login } from '@/lib/api';

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError]       = useState('');
  const [loading, setLoading]   = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError('');
    try {
      // login() chama /api/auth/login (Next.js API route) que seta cookie httpOnly.
      // O token NUNCA toca o localStorage — imune a XSS.
      await login(username, password);

      // Carrega perfil autenticado e guarda metadados locais (sem token).
      const meRes = await fetch('/api-proxy/me');
      if (meRes.ok) {
        const me = await meRes.json();
        localStorage.setItem('betaml_user', JSON.stringify(me));
      }

      router.push('/dashboard');
    } catch {
      setError('Credenciais inválidas.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-100">
      <div className="w-full max-w-md rounded-2xl bg-white p-8 shadow-lg">
        <h1 className="mb-2 text-2xl font-bold text-brand">BetAML</h1>
        <p className="mb-6 text-sm text-gray-500">PLD/FT Intelligence — Brazilian Betting Operators</p>

        <form onSubmit={handleSubmit} className="space-y-4">
          <input
            required
            type="text"
            aria-label="Usuário"
            placeholder="Usuário"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            className="w-full rounded-lg border border-gray-300 px-4 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand"
          />
          <input
            required
            type="password"
            aria-label="Senha"
            placeholder="Senha"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded-lg border border-gray-300 px-4 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand"
          />
          {error && <p className="text-sm text-red-600">{error}</p>}
          <button
            type="submit"
            disabled={loading}
            className="w-full rounded-lg bg-brand py-2 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-50"
          >
            {loading ? 'Entrando...' : 'Entrar'}
          </button>
        </form>

        <p className="mt-4 text-xs text-gray-400 text-center">
          Demo: admin_a / admin123
        </p>
      </div>
    </div>
  );
}
