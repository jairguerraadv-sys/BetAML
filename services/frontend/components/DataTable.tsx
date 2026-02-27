'use client';

interface Column<T> {
  header: string;
  accessorKey: keyof T;
  cell?: (value: T[keyof T], row: T) => React.ReactNode;
}

interface DataTableProps<T> {
  data: T[];
  columns: Column<T>[];
  loading?: boolean;
  onRowClick?: (row: T) => void;
}

export default function DataTable<T extends Record<string, unknown>>({
  data, columns, loading, onRowClick,
}: DataTableProps<T>) {
  if (loading) {
    return (
      <div className="rounded-xl border border-gray-200 bg-white p-8 text-center text-sm text-gray-400">
        Carregando...
      </div>
    );
  }

  if (data.length === 0) {
    return (
      <div className="rounded-xl border border-gray-200 bg-white p-8 text-center text-sm text-gray-400">
        Nenhum registro encontrado.
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-xl border border-gray-200 bg-white shadow-sm">
      <table className="w-full text-sm">
        <thead className="border-b border-gray-100 bg-gray-50 text-left">
          <tr>
            {columns.map((col) => (
              <th key={String(col.accessorKey)} className="px-4 py-3 text-xs font-semibold uppercase tracking-wide text-gray-500">
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-50">
          {data.map((row, i) => (
            <tr
              key={i}
              onClick={() => onRowClick?.(row)}
              className={onRowClick ? 'cursor-pointer hover:bg-gray-50' : ''}
            >
              {columns.map((col) => {
                const value = row[col.accessorKey];
                return (
                  <td key={String(col.accessorKey)} className="px-4 py-3 text-gray-700">
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
