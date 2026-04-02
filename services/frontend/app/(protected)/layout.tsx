'use client';
import Sidebar from '@/components/Sidebar';
import GlobalSearch from '@/components/GlobalSearch';
import { ThemeProvider } from '@/components/ThemeProvider';
import MaintenanceBanner from '@/components/MaintenanceBanner';
import OnboardingTour from '@/components/OnboardingTour';

export default function ProtectedLayout({ children }: { children: React.ReactNode }) {
  return (
    <ThemeProvider>
      <div className="flex h-screen overflow-hidden bg-white dark:bg-gray-950">
        <Sidebar />
        <div className="flex flex-1 flex-col overflow-hidden">
          <MaintenanceBanner />
          <main className="flex-1 overflow-y-auto p-6 text-gray-900 dark:text-gray-100">
            {children}
          </main>
        </div>
        <GlobalSearch />
        <OnboardingTour />
      </div>
    </ThemeProvider>
  );
}
