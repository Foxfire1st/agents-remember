import react from "@vitejs/plugin-react";
import { createHash } from "node:crypto";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

const DASHBOARD_ROOT = fileURLToPath(new URL(".", import.meta.url));
const BUILD_INPUT_FILES = [
  "index.html",
  "vite.config.ts",
  "tsconfig.json",
  "tsconfig.app.json",
  "tsconfig.node.json",
  "panda.config.ts",
  "postcss.config.cjs",
  "package.json",
  "package-lock.json",
];
const NON_BUNDLED_MARKERS = [".test.", ".spec.", ".stories."];

function bundledSourceFiles(root: string): string[] {
  const found: string[] = [];
  for (const name of readdirSync(root)) {
    if (name === ".DS_Store" || name === "__pycache__") continue;
    const path = join(root, name);
    if (statSync(path).isDirectory()) found.push(...bundledSourceFiles(path));
    else if (!NON_BUNDLED_MARKERS.some((marker) => name.includes(marker))) found.push(path);
  }
  return found;
}

// Mirrors scripts/sync-dashboard.py::source_fingerprint. The build embeds the same input identity
// the sync step records beside the shipped bundle, so a persistent tab can compare its own JS with
// the process now serving snapshots instead of mistaking the server commit stamp for client truth.
function dashboardSourceFingerprint(): string {
  const inputs = new Map<string, string>();
  const srcRoot = join(DASHBOARD_ROOT, "src");
  for (const path of bundledSourceFiles(srcRoot)) {
    const key = `src/${relative(srcRoot, path).replaceAll("\\", "/")}`;
    inputs.set(key, createHash("sha256").update(readFileSync(path)).digest("hex"));
  }
  for (const name of BUILD_INPUT_FILES) {
    const path = join(DASHBOARD_ROOT, name);
    inputs.set(name, createHash("sha256").update(readFileSync(path)).digest("hex"));
  }
  const fingerprint = createHash("sha256");
  for (const key of [...inputs.keys()].sort()) {
    fingerprint.update(key);
    fingerprint.update("\0");
    fingerprint.update(inputs.get(key) ?? "");
    fingerprint.update("\n");
  }
  return fingerprint.digest("hex");
}

// `base: "./"` keeps the built bundle's asset URLs relative so it works under the FastAPI
// "/" static mount (serving/static.py). The dev `/api` proxy points at a locally running
// `agents-remember dashboard` server so `npm run dev` consumes the real (or --sim) stream.
// Test config lives in vitest.config.ts (separate, to keep pure Vite plugin types here).
export default defineConfig({
  base: "./",
  plugins: [react()],
  define: {
    __AR_DASHBOARD_BUILD__: JSON.stringify(dashboardSourceFingerprint()),
  },
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
