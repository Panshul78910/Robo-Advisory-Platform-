import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// In dev, the browser talks to Vite (same origin) and Vite forwards /api to the
// FastAPI backend. That sidesteps CORS entirely, so it works on any port.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const target = env.VITE_PROXY_TARGET || "http://localhost:8000";
  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        "/api": { target, changeOrigin: true },
        "/healthz": { target, changeOrigin: true },
      },
    },
  };
});
