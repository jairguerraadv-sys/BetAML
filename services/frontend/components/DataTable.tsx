'use client';

interface Column<T> {
  header: string;
  accessorKey: keyof T;
  // value typed as unknown so pages with concrete types (string, boolean, number) don't fail
  cell?: (value: unknown, row: T) => React.ReactNode;
}

interface DataTableProps<T> {
  data: T[];
  columns: Column<T>[];
  loading?: boolean;
  onRowClick?: (row: T) => void;
  caption?: string;
}

export default function DataTable<T extends object>({
  data, columns, loading, onRowClick, caption,
}: DataTableProps<T>) {
  if (loading) {
    return (
      <div
        role="status"
        aria-live="polite"
        aria-label="Carregando dados"
        className="rounded-xl border border-gray-200 bg-white p-8 text-center text-sm text-gray-400 dark:border-gray-700 dark:bg-gray-900"
      >
        <span className="sr-only">Carregando...</span>
        <span aria-hidden>Carregando...</span>
      </div>
    );
  }

  if (data.length === 0) {
    return (
      <div
        role="status"
        aria-live="polite"
        className="rounded-xl border border-gray-200 bg-white p-8 text-center text-sm text-gray-400 dark:border-gray-700 dark:bg-gray-900"
      >
        Nenhum registro encontrado.
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-xl border border-gray-200 bg-white shadow-sm dark:border-gray-700 dark:bg-gray-900">
      <table className="w-full text-sm" role="grid" aria-rowcount={data.length}>
        {caption && <caption className="sr-only">{caption}</caption>}
        <thead className="border-b border-gray-100 bg-gray-50 text-left dark:border-gray-700 dark:bg-gray-800">
          <tr role="row">
            {columns.map((col) => (
              <th
                key={String(col.accessorKey)}
                scope="col"
                className="px-4 py-3 text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400"
              >
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-50 dark:divide-gray-800">
          {data.map((row, i) => (
            <tr
              key={i}
              role="row"
              aria-rowindex={i + 1}
              onClick={() => onRowClick?.(row)}
              onKeyDown={onRowClick ? (e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  onRowClick(row);
                }
              } : undefined}
              tabIndex={onRowClick ? 0 : undefined}
              className={
                onRowClick
                  ? 'cursor-pointer hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-inset focus:ring-brand dark:hover:bg-gray-800'
                  : ''
              }
            >
              {columns.map((col) => {
                const value = row[col.accessorKey];
                return (
                  <td
                    key={String(col.accessorKey)}
                    role="gridcell"
                    className="px-4 py-3 text-gray-700 dark:text-gray-300"
                  >
                    {col.cell ? col.cell(value, row) : String(value ?? '—')}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
