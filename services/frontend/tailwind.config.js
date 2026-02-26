/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './src/pages/**/*.{js,ts,jsx,tsx,mdx}',
    './src/components/**/*.{js,ts,jsx,tsx,mdx}',
    './src/app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        sidebar: {
          DEFAULT: '#0f172a',
          hover: '#1e293b',
          active: '#1d4ed8',
          border: '#1e293b',
          text: '#94a3b8',
          'text-active': '#f8fafc',
        },
      },
    },
  },
  plugins: [],
};
