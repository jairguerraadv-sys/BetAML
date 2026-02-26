'use client';

import { useEffect, useState } from 'react';
import { getDashboardStats } from '@/lib/api';
import type { DashboardStats } from '@/lib/types';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from '@/components/ui/table';
import { formatDate, maskCpf, severityColor } from '@/lib/utils';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import { Bell, Briefcase, AlertTriangle, BookOpen } from 'lucide-react';
import Link from 'next/link';

export default function DashboardPage() {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getDashboardStats()
      .then(setStats)
      .catch(() => {/* silently fall back to empty state */})
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-blue-600 border-t-transparent" />
      </div>
    );
  }

  const kpis = [
    {
      label: 'Total Alerts',
      value: stats?.total_alerts ?? 0,
      icon: Bell,
      color: 'text-blue-600',
      bg: 'bg-blue-50',
    },
    {
      label: 'Open Cases',
      value: stats?.open_cases ?? 0,
      icon: Briefcase,
      color: 'text-purple-600',
      bg: 'bg-purple-50',
    },
    {
      label: 'High / Critical Alerts',
      value: stats?.high_critical_alerts ?? 0,
      icon: AlertTriangle,
      color: 'text-red-600',
      bg: 'bg-red-50',
    },
    {
      label: 'Active Rules',
      value: stats?.active_rules ?? 0,
      icon: BookOpen,
      color: 'text-green-600',
      bg: 'bg-green-50',
    },
  ];

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-bold text-gray-900">Dashboard</h2>

      {/* KPI Cards */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {kpis.map((kpi) => (
          <Card key={kpi.label}>
            <CardContent className="flex items-center gap-4 py-5">
              <div className={`flex h-12 w-12 shrink-0 items-center justify-center rounded-xl ${kpi.bg}`}>
                <kpi.icon className={`h-6 w-6 ${kpi.color}`} />
              </div>
              <div>
                <p className="text-sm text-gray-500">{kpi.label}</p>
                <p className="text-3xl font-bold text-gray-900">{kpi.value.toLocaleString()}</p>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
        {/* Alerts over last 7 days */}
        <Card className="xl:col-span-2">
          <CardHeader>
            <CardTitle>Alerts – Last 7 Days</CardTitle>
          </CardHeader>
          <CardContent>
            {stats?.alerts_by_day && stats.alerts_by_day.length > 0 ? (
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={stats.alerts_by_day} margin={{ top: 4, right: 8, bottom: 0, left: -10 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                  <XAxis dataKey="date" tick={{ fontSize: 12 }} tickFormatter={(v: string) => v.slice(5)} />
                  <YAxis tick={{ fontSize: 12 }} allowDecimals={false} />
                  <Tooltip />
                  <Bar dataKey="count" fill="#3b82f6" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <div className="flex h-[220px] items-center justify-center text-sm text-gray-400">
                No data available
              </div>
            )}
          </CardContent>
        </Card>

        {/* Top risk players */}
        <Card>
          <CardHeader>
            <CardTitle>Top 5 Players by Risk</CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            {stats?.top_risk_players && stats.top_risk_players.length > 0 ? (
              <Table>
                <TableHead>
                  <TableRow>
                    <TableHeaderCell>Player</TableHeaderCell>
                    <TableHeaderCell>Score</TableHeaderCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {stats.top_risk_players.slice(0, 5).map((p) => (
                    <TableRow key={p.player_id}>
                      <TableCell className="font-mono text-xs">{maskCpf(p.player_id)}</TableCell>
                      <TableCell>
                        <span className="font-semibold text-red-600">
                          {(p.risk_score * 100).toFixed(1)}%
                        </span>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            ) : (
              <div className="flex h-28 items-center justify-center text-sm text-gray-400">
                No data
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Recent alerts */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle>Recent Alerts</CardTitle>
          <Link href="/alerts" className="text-sm text-blue-600 hover:underline">
            View all
          </Link>
        </CardHeader>
        <CardContent className="p-0">
          {stats?.recent_alerts && stats.recent_alerts.length > 0 ? (
            <Table>
              <TableHead>
                <TableRow>
                  <TableHeaderCell>ID</TableHeaderCell>
                  <TableHeaderCell>Player</TableHeaderCell>
                  <TableHeaderCell>Rule</TableHeaderCell>
                  <TableHeaderCell>Severity</TableHeaderCell>
                  <TableHeaderCell>Status</TableHeaderCell>
                  <TableHeaderCell>Created</TableHeaderCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {stats.recent_alerts.slice(0, 5).map((alert) => (
                  <TableRow key={alert.id}>
                    <TableCell>
                      <Link
                        href={`/alerts/${alert.id}`}
                        className="font-mono text-xs text-blue-600 hover:underline"
                      >
                        {alert.id.slice(0, 8)}…
                      </Link>
                    </TableCell>
                    <TableCell className="font-mono text-xs">{alert.player_id}</TableCell>
                    <TableCell>{alert.rule_name}</TableCell>
                    <TableCell>
                      <Badge variant={severityColor(alert.severity) as 'danger' | 'warning' | 'success' | 'default'}>
                        {alert.severity}
                      </Badge>
                    </TableCell>
                    <TableCell>{alert.status}</TableCell>
                    <TableCell className="text-xs text-gray-500">{formatDate(alert.created_at)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <div className="flex h-24 items-center justify-center text-sm text-gray-400">
              No recent alerts
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
