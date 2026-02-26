'use client';

import { useEffect, useState, useCallback } from 'react';
import Link from 'next/link';
import { useCases } from '@/hooks/useCases';
import type { CaseFilters, CaseStatus } from '@/lib/types';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Select, Input } from '@/components/ui/input';
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from '@/components/ui/table';
import { Pagination } from '@/components/ui/pagination';
import { formatDate, caseStatusLabel } from '@/lib/utils';

const PAGE_SIZE = 20;

const statusBadgeVariant = (s: CaseStatus): 'default' | 'info' | 'warning' | 'danger' | 'success' => {
  const map: Record<CaseStatus, 'default' | 'info' | 'warning' | 'danger' | 'success'> = {
    OPEN: 'info',
    UNDER_REVIEW: 'warning',
    PENDING_COMPLIANCE: 'warning',
    CLOSED_SUBSTANTIATED: 'danger',
    CLOSED_UNSUBSTANTIATED: 'success',
  };
  return map[s] ?? 'default';
};

export default function CasesPage() {
  const { data, loading, error, fetchCases } = useCases();

  const [status, setStatus] = useState<CaseStatus | ''>('');
  const [assignedTo, setAssignedTo] = useState('');
  const [page, setPage] = useState(1);

  const buildFilters = useCallback(
    (p = page): CaseFilters => ({
      ...(status && { status }),
      ...(assignedTo && { assigned_to: assignedTo }),
      page: p,
      page_size: PAGE_SIZE,
    }),
    [status, assignedTo, page],
  );

  useEffect(() => {
    fetchCases(buildFilters());
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page]);

  function handleSearch() {
    setPage(1);
    fetchCases(buildFilters(1));
  }

  function handlePageChange(p: number) {
    setPage(p);
    fetchCases(buildFilters(p));
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-bold text-gray-900">Cases</h2>
      </div>

      {/* Filters */}
      <Card>
        <CardContent className="py-4">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <Select
              label="Status"
              value={status}
              onChange={(e) => setStatus(e.target.value as CaseStatus | '')}
            >
              <option value="">All statuses</option>
              <option value="OPEN">Open</option>
              <option value="UNDER_REVIEW">Under Review</option>
              <option value="PENDING_COMPLIANCE">Pending Compliance</option>
              <option value="CLOSED_SUBSTANTIATED">Closed – Substantiated</option>
              <option value="CLOSED_UNSUBSTANTIATED">Closed – Unsubstantiated</option>
            </Select>
            <Input
              label="Assigned To (User ID)"
              value={assignedTo}
              onChange={(e) => setAssignedTo(e.target.value)}
              placeholder="User ID..."
            />
          </div>
          <div className="mt-3 flex justify-end">
            <Button onClick={handleSearch} loading={loading}>Search</Button>
          </div>
        </CardContent>
      </Card>

      {/* Table */}
      <Card>
        <CardHeader>
          <CardTitle>{data ? `${data.total.toLocaleString()} cases` : 'Cases'}</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {error && <p className="px-4 py-3 text-sm text-red-600">{error}</p>}
          {loading ? (
            <div className="flex justify-center py-12">
              <div className="h-6 w-6 animate-spin rounded-full border-4 border-blue-600 border-t-transparent" />
            </div>
          ) : (
            <>
              <Table>
                <TableHead>
                  <TableRow>
                    <TableHeaderCell>Title</TableHeaderCell>
                    <TableHeaderCell>Player</TableHeaderCell>
                    <TableHeaderCell>Status</TableHeaderCell>
                    <TableHeaderCell>Assigned To</TableHeaderCell>
                    <TableHeaderCell>Created</TableHeaderCell>
                    <TableHeaderCell>Actions</TableHeaderCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {data?.items.length ? (
                    data.items.map((c) => (
                      <TableRow key={c.id}>
                        <TableCell className="font-medium max-w-[200px] truncate">{c.title}</TableCell>
                        <TableCell className="font-mono text-xs">{c.player_id}</TableCell>
                        <TableCell>
                          <Badge variant={statusBadgeVariant(c.status)}>
                            {caseStatusLabel(c.status)}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-sm text-gray-500">
                          {c.assigned_to_name ?? '—'}
                        </TableCell>
                        <TableCell className="text-xs text-gray-500">
                          {formatDate(c.created_at)}
                        </TableCell>
                        <TableCell>
                          <Link
                            href={`/cases/${c.id}`}
                            className="text-sm font-medium text-blue-600 hover:underline"
                          >
                            View
                          </Link>
                        </TableCell>
                      </TableRow>
                    ))
                  ) : (
                    <TableRow>
                      <TableCell colSpan={6} className="py-8 text-center text-gray-400">
                        No cases found
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
