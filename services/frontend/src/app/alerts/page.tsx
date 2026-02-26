'use client';

import { useEffect, useState, useCallback } from 'react';
import Link from 'next/link';
import { useAlerts } from '@/hooks/useAlerts';
import type { AlertFilters, AlertSeverity, AlertStatus } from '@/lib/types';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input, Select } from '@/components/ui/input';
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from '@/components/ui/table';
import { Pagination } from '@/components/ui/pagination';
import { formatDate, severityColor, alertStatusLabel, shortId } from '@/lib/utils';

const PAGE_SIZE = 20;

export default function AlertsPage() {
  const { data, loading, error, fetchAlerts } = useAlerts();

  const [severity, setSeverity] = useState<AlertSeverity | ''>('');
  const [status, setStatus] = useState<AlertStatus | ''>('');
  const [playerId, setPlayerId] = useState('');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [page, setPage] = useState(1);

  const buildFilters = useCallback(
    (p = page): AlertFilters => ({
      ...(severity && { severity }),
      ...(status && { status }),
      ...(playerId && { player_id: playerId }),
      ...(dateFrom && { date_from: dateFrom }),
      ...(dateTo && { date_to: dateTo }),
      page: p,
      page_size: PAGE_SIZE,
    }),
    [severity, status, playerId, dateFrom, dateTo, page],
  );

  useEffect(() => {
    fetchAlerts(buildFilters());
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page]);

  function handleSearch() {
    setPage(1);
    fetchAlerts(buildFilters(1));
  }

  function handlePageChange(p: number) {
    setPage(p);
    fetchAlerts(buildFilters(p));
  }

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-bold text-gray-900">Alerts</h2>

      {/* Filters */}
      <Card>
        <CardContent className="py-4">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-5">
            <Select
              label="Severity"
              value={severity}
              onChange={(e) => setSeverity(e.target.value as AlertSeverity | '')}
            >
              <option value="">All severities</option>
              <option value="LOW">Low</option>
              <option value="MEDIUM">Medium</option>
              <option value="HIGH">High</option>
              <option value="CRITICAL">Critical</option>
            </Select>
            <Select
              label="Status"
              value={status}
              onChange={(e) => setStatus(e.target.value as AlertStatus | '')}
            >
              <option value="">All statuses</option>
              <option value="OPEN">Open</option>
              <option value="TRIAGED">Triaged</option>
              <option value="CLOSED_TP">Closed (TP)</option>
              <option value="CLOSED_FP">Closed (FP)</option>
            </Select>
            <Input
              label="Player ID"
              value={playerId}
              onChange={(e) => setPlayerId(e.target.value)}
              placeholder="Player ID..."
            />
            <Input
              label="From"
              type="date"
              value={dateFrom}
              onChange={(e) => setDateFrom(e.target.value)}
            />
            <Input
              label="To"
              type="date"
              value={dateTo}
              onChange={(e) => setDateTo(e.target.value)}
            />
          </div>
          <div className="mt-3 flex justify-end">
            <Button onClick={handleSearch} loading={loading}>
              Search
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Table */}
      <Card>
        <CardHeader>
          <CardTitle>
            {data ? `${data.total.toLocaleString()} alerts` : 'Alerts'}
          </CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {error && (
            <p className="px-4 py-3 text-sm text-red-600">{error}</p>
          )}
          {loading ? (
            <div className="flex justify-center py-12">
              <div className="h-6 w-6 animate-spin rounded-full border-4 border-blue-600 border-t-transparent" />
            </div>
          ) : (
            <>
              <Table>
                <TableHead>
                  <TableRow>
                    <TableHeaderCell>ID</TableHeaderCell>
                    <TableHeaderCell>Player ID</TableHeaderCell>
                    <TableHeaderCell>Rule</TableHeaderCell>
                    <TableHeaderCell>Severity</TableHeaderCell>
                    <TableHeaderCell>Status</TableHeaderCell>
                    <TableHeaderCell>Created</TableHeaderCell>
                    <TableHeaderCell>Actions</TableHeaderCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {data?.items.length ? (
                    data.items.map((alert) => (
                      <TableRow key={alert.id}>
                        <TableCell>
                          <span className="font-mono text-xs text-gray-500">{shortId(alert.id)}</span>
                        </TableCell>
                        <TableCell className="font-mono text-xs">{alert.player_id}</TableCell>
                        <TableCell className="max-w-[160px] truncate">{alert.rule_name}</TableCell>
                        <TableCell>
                          <Badge variant={severityColor(alert.severity) as 'danger' | 'warning' | 'success' | 'default'}>
                            {alert.severity}
                          </Badge>
                        </TableCell>
                        <TableCell>
                          <span className="text-xs text-gray-600">{alertStatusLabel(alert.status)}</span>
                        </TableCell>
                        <TableCell className="text-xs text-gray-500">
                          {formatDate(alert.created_at)}
                        </TableCell>
                        <TableCell>
                          <Link
                            href={`/alerts/${alert.id}`}
                            className="text-sm font-medium text-blue-600 hover:underline"
                          >
                            View
                          </Link>
                        </TableCell>
                      </TableRow>
                    ))
                  ) : (
                    <TableRow>
                      <TableCell colSpan={7} className="py-8 text-center text-gray-400">
                        No alerts found
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
              {data && (
                <div className="px-4">
                  <Pagination
                    page={data.page}
                    totalPages={data.total_pages}
                    onPageChange={handlePageChange}
                  />
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
