import { useMemo } from 'react';

type SharedBy = { type: string; value: string };

export interface PlayerNetworkNode {
  player_id: string;
  shared_by: SharedBy[];
}

interface GraphProps {
  playerId: string;
  relatedPlayers: PlayerNetworkNode[];
}

type PositionedNode = {
  id: string;
  x: number;
  y: number;
  label: string;
  links: SharedBy[];
};

const colorByLinkType: Record<string, string> = {
  device: '#f97316',
  bank_account: '#2563eb',
};

function shortId(value: string): string {
  if (!value) return '—';
  if (value.length <= 10) return value;
  return `${value.slice(0, 6)}…${value.slice(-4)}`;
}

export default function PlayerNetworkGraph({ playerId, relatedPlayers }: GraphProps) {
  const { center, satellites } = useMemo(() => {
    const size = 420;
    const centerX = size / 2;
    const centerY = size / 2;
    const baseRadius = 140;

    const nodes: PositionedNode[] = relatedPlayers.map((item, idx) => {
      const angle = (Math.PI * 2 * idx) / Math.max(relatedPlayers.length, 1);
      const distance = baseRadius + ((idx % 3) * 24);
      return {
        id: item.player_id,
        x: centerX + Math.cos(angle) * distance,
        y: centerY + Math.sin(angle) * distance,
        label: shortId(item.player_id),
        links: item.shared_by,
      };
    });

    return {
      center: {
        id: playerId,
        x: centerX,
        y: centerY,
        label: shortId(playerId),
        links: [],
      } satisfies PositionedNode,
      satellites: nodes,
    };
  }, [playerId, relatedPlayers]);

  const width = 420;
  const height = 420;

  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-900">Grafo de Relacionamentos</h3>
        <span className="text-xs text-gray-500">{relatedPlayers.length} vínculo(s)</span>
      </div>

      {relatedPlayers.length === 0 ? (
        <p className="text-sm text-gray-500">Nenhum relacionamento por device ou conta bancária foi encontrado.</p>
      ) : (
        <>
          <svg viewBox={`0 0 ${width} ${height}`} className="h-[360px] w-full rounded-lg bg-slate-50">
            {satellites.map((node) => {
              const dominantType = node.links[0]?.type ?? 'device';
              const lineColor = colorByLinkType[dominantType] ?? '#64748b';
              return (
                <g key={`edge-${node.id}`}>
                  <line
                    x1={center.x}
                    y1={center.y}
                    x2={node.x}
                    y2={node.y}
                    stroke={lineColor}
                    strokeWidth="1.8"
                    strokeDasharray="4 3"
                    opacity="0.8"
                  />
                </g>
              );
            })}

            <g>
              <circle cx={center.x} cy={center.y} r="34" fill="#111827" />
              <text x={center.x} y={center.y + 4} textAnchor="middle" fontSize="10" fill="#ffffff" fontWeight="700">
                TARGET
              </text>
            </g>

            {satellites.map((node) => {
              const dominantType = node.links[0]?.type ?? 'device';
              const fill = dominantType === 'bank_account' ? '#1d4ed8' : '#ea580c';
              return (
                <g key={node.id}>
                  <circle cx={node.x} cy={node.y} r="22" fill={fill} opacity="0.92" />
                  <text x={node.x} y={node.y + 3} textAnchor="middle" fontSize="8" fill="#ffffff" fontWeight="700">
                    {node.label}
                  </text>
                </g>
              );
            })}
          </svg>

          <div className="mt-3 flex flex-wrap gap-3 text-xs">
            <span className="inline-flex items-center gap-1 text-gray-600">
              <span className="inline-block h-2 w-2 rounded-full bg-orange-500" /> device compartilhado
            </span>
            <span className="inline-flex items-center gap-1 text-gray-600">
              <span className="inline-block h-2 w-2 rounded-full bg-blue-700" /> conta bancária compartilhada
            </span>
          </div>
        </>
      )}
    </div>
  );
}