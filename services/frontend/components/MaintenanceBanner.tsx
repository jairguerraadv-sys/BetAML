'use client';
import { useQuery } from '@tanstack/react-query';
import { fetchSystemFlags } from '@/lib/api';

export default function MaintenanceBanner() {
  const { data: flags = [] } = useQuery({
    queryKey: ['system-flags'],
    queryFn: fetchSystemFlags,
    refetchInterval: 30_000,
  });
  const active = flags.some(
    (f) => f.key.endsWith(':maintenance_mode') && f.value?.enabled,
  );
  if (!active) return null;
  return (
    <div className="sticky top-0 z-50 flex items-center justify-center gap-2 bg-amber-500 px-4 py-2 text-sm font-semibold text-white">
      <span>⚠️ Sistema em modo de manutenção — operações podem estar indisponíveis.</span>
    </div>
  );
}
