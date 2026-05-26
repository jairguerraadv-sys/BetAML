'use client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useState } from 'react';
import { UserProvider } from '@/contexts/UserContext';
import { ToastProvider } from '@/components/Toast';

export default function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(() => new QueryClient());
  return (
    <QueryClientProvider client={client}>
      <UserProvider>
        <ToastProvider>{children}</ToastProvider>
      </UserProvider>
    </QueryClientProvider>
  );
}
