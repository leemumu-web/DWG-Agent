import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8010',
        changeOrigin: true,
      },
      // The database console is served by the production gateway, not Vite.
      // Keeping this same-origin also lets the browser send the short-lived
      // HttpOnly database-console cookie issued by the backend.
      '/dba/mysql': {
        target: 'http://127.0.0.1:80',
        changeOrigin: true,
        ws: true,
      },
    },
  },
});
