'use client';
import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { Database, Search } from 'lucide-react';

export default function FeatureStorePage() {
  const router = useRouter();
  const [playerId, setPlayerId] = useState('');

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2">
        <Database size={22} className="text-brand" />
        <h1 className="text-2xl font-bold text-gray-900">Feature Store</h1>
      </div>
      <p className="text-sm text-gray-500">
        Consulte o perfil de features (históricas e em tempo real) de um jogador.
      </p>
      <form
        className="flex gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (playerId.trim()) router.push(`/feature-store/${playerId.trim()}`);
        }}
      >
        <input
          value={playerId}
          onChange={(e) => setPlayerId(e.target.value)}
          placeholder="Player ID (UUID)"
          className="flex-1 rounded-lg border border-gray-200 px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand"
        />
        <button
          type="submit"
          className="flex items-center gap-2 rounded-lg bg-brand px-4 py-2.5 text-sm font-semibold text-white hover:bg-brand/90"
        >
          <Search size={16} /> Consultar
        </button>
      </form>
    </div>
  );
}
