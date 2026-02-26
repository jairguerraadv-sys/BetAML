'use client';

import { useState, type FormEvent } from 'react';
import { useRouter } from 'next/navigation';
import { login } from '@/lib/api';
import { storeTokens } from '@/lib/auth';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const tokens = await login({ email, password });
      storeTokens(tokens.access_token, tokens.refresh_token);
      router.replace('/dashboard');
    } catch {
      setError('Invalid email or password. Please try again.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-slate-900 to-slate-800">
      <div className="w-full max-w-md rounded-2xl bg-white p-8 shadow-2xl">
        {/* Logo */}
        <div className="mb-8 flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-blue-600">
            <span className="text-lg font-bold text-white">B</span>
          </div>
          <div>
            <h1 className="text-xl font-bold text-gray-900">BetAML</h1>
            <p className="text-xs text-gray-500">AML Monitoring Platform</p>
          </div>
        </div>

        <h2 className="mb-6 text-2xl font-semibold text-gray-900">Sign in</h2>

        {error && (
          <div className="mb-4 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700 border border-red-200">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <Input
            label="Email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="analyst@company.com"
            required
            autoComplete="email"
          />
          <Input
            label="Password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••"
            required
            autoComplete="current-password"
          />
          <Button type="submit" loading={loading} className="w-full" size="lg">
            Sign in
          </Button>
        </form>

        <p className="mt-6 text-center text-xs text-gray-400">
          BetAML © {new Date().getFullYear()} – Authorised personnel only
        </p>
      </div>
    </div>
  );
}
