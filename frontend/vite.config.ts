import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies /admin to the local proxy backend so the SPA talks
// same-origin in development, mirroring the nginx reverse proxy in production.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/admin": {
        target: process.env.PROXY_TARGET || "http://localhost:8080",
        changeOrigin: true,
      },
    },
  },
});
