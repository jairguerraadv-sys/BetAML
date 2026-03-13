'use client';
import Sidebar from '@/components/Sidebar';
import GlobalSearch from '@/components/GlobalSearch';
import { ThemeProvider } from '@/components/ThemeProvider';

export default function ProtectedLayout({ children }: { children: React.ReactNode }) {
  return (
    <ThemeProvider>
      <div className="flex h-screen overflow-hidden bg-white dark:bg-gray-950">
        <Sidebar />
        <main className="flex-1 overflow-y-auto p-6 text-gray-900 dark:text-gray-100">
          {children}
        </main>
        <GlobalSearch />
      </div>
    </ThemeProvider>
  );
}
