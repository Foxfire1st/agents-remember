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
//
// `/canvas` is the backend's second surface, not the app's: a revision frame's `pageUrl` is
// `/canvas/page/<session>/` (serving/canvas.py CANVAS_FRAME_PREFIX), every asset it loads hangs off
// that prefix, and the backend answers 410 for a session it does not hold. Unproxied, Vite answers
// the page itself with its own index.html and a 200, so the frame renders white and the backend
// never sees a request (F4). Both prefixes come from one target so they cannot drift.
const BACKEND = process.env.AR_DASHBOARD_API ?? "http://127.0.0.1:8765";

// The browser's own origin travels with a `/canvas` request. The backend admits a request only when
// `Host` names its own address and bound port (serving/write_guard.py, `foreign-host`), and it
// builds a frame document's policy from the origin the page is loaded from
// (serving/canvas.py `_origin`). Those two facts conflict behind a proxy: with `changeOrigin` the
// backend sees the proxy target and every `style-src`/`img-src` it writes names the wrong origin, so
// the browser blocks the frame's own `styles.css` and the page renders as raw HTML (F5). The proxy
// therefore keeps `Host` for admission and states the browser's origin separately, which the backend
// reads as `X-Forwarded-Host` (and `X-Forwarded-Proto` when it is a known scheme).
interface ProxyRequestOut {
  setHeader(name: string, value: string): void;
}
interface ProxyRequestLike {
  headers: Record<string, string | string[] | undefined>;
}
interface ProxyLike {
  on(event: "proxyReq", listener: (out: ProxyRequestOut, req: ProxyRequestLike) => void): void;
}
function forwardedOrigin(proxy: ProxyLike): void {
  proxy.on("proxyReq", (out, req) => {
    const host = req.headers.host;
    if (typeof host === "string" && host.length > 0) out.setHeader("x-forwarded-host", host);
    out.setHeader("x-forwarded-proto", "http");
  });
}

export default defineConfig({
  base: "./",
  plugins: [react()],
  define: {
    __AR_DASHBOARD_BUILD__: JSON.stringify(dashboardSourceFingerprint()),
  },
  build: { outDir: "dist", emptyOutDir: true },
  server: {
    // Port + proxy target are env-overridable so parallel worktree dev loops can each run their own
    // Vite + dashboard backend without colliding (defaults: 5173 -> 127.0.0.1:8765).
    port: Number(process.env.AR_DASHBOARD_DEV_PORT) || 5173,
    proxy: {
      "/api": { target: BACKEND, changeOrigin: true },
      "/canvas": { target: BACKEND, changeOrigin: true, configure: forwardedOrigin },
    },
  },
});
