import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// `base: "./"` keeps the built bundle's asset URLs relative so it works under the FastAPI
// "/" static mount (serving/static.py). The dev `/api` proxy points at a locally running
// `agents-remember dashboard` server so `npm run dev` consumes the real (or --sim) stream.
// Test config lives in vitest.config.ts (separate, to keep pure Vite plugin types here).
export default defineConfig({
  base: "./",
  plugins: [react()],
  build: { outDir: "dist", emptyOutDir: true },
  server: {
    // Port + `/api` proxy target are env-overridable so parallel worktree dev loops can each run
    // their own Vite + dashboard backend without colliding (defaults: 5173 -> 127.0.0.1:8765).
    port: Number(process.env.AR_DASHBOARD_DEV_PORT) || 5173,
    // `ws: true` upgrades proxied WebSockets — the Mode B2 terminal bridge (`/api/terminal/{id}`,
    // slice 6e) rides the same `/api` proxy as the SSE/HTTP channels onto the dashboard server.
    proxy: {
      "/api": {
        target: process.env.AR_DASHBOARD_API ?? "http://127.0.0.1:8765",
        changeOrigin: true,
        ws: true,
      },
    },
  },
});
