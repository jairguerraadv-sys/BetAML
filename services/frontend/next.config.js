/** @type {import('next').NextConfig} */
const API_INTERNAL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

const nextConfig = {
  output: 'standalone',
  // Proxy: o browser chama /api-proxy/* e o servidor Next.js encaminha
  // para a API (acessível dentro do container). Assim localhost:8000
  // nunca aparece no bundle do browser — compatível com Codespaces/devcontainer.
  async rewrites() {
    return [
      {
        source: '/api-proxy/:path*',
        destination: `${API_INTERNAL}/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
