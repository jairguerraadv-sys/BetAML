import type { Metadata } from 'next';
import './globals.css';
import Providers from './providers';

export const metadata: Metadata = {
  title: 'BetAML — PLD/FT Intelligence',
  description: 'Multi-tenant AML platform for Brazilian betting operators',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="pt-BR">
      <body className="min-h-screen bg-gray-50 text-gray-900 font-sans">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
