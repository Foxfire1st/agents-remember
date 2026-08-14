// 260718-CHATS-L5F R7: boot an isolated REAL dashboard daemon from this worktree for the durable
// Chats E2E. Never touches the developer's :8871 — a scratch coordination root, a free port, and the
// worktree's own bundle. The daemon is the same `agents_remember.cli dashboard` process the product
// ships; the E2E drives the real installed harnesses through it.

import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { createServer } from "node:net";
import {
  mkdtempSync,
  writeFileSync,
  mkdirSync,
  existsSync,
  openSync,
  symlinkSync,
} from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const dashboardDir = resolve(fileURLToPath(new URL("../../", import.meta.url)));
const worktreeRoot = resolve(dashboardDir, "..");
const repoScripts = join(worktreeRoot, "scripts", "sync-dashboard.py");

export interface BootedDaemon {
  baseUrl: string;
  port: number;
  coordinationRoot: string;
  logPath: string;
  stop: () => Promise<void>;
}

// 260718-CHATS-L5F R8 collision-safety (the L5P re-verification lesson): a scratch codex chat must
// never land in the developer's live ``~/.codex`` — its session rollouts and any codex-global state
// stay isolated in a per-run CODEX_HOME. The real ``auth.json`` is symlinked in so codex still
// authenticates for ordinary turns; nothing else is copied, so no MCP-server fleet from the
// developer's config is re-launched. The daemon's own runtime is already isolated by the scratch
// coordination root (control_root = coordinationRoot/runtime/harness-control), keeping the live
// :8871 untouched. Respect an explicitly provided CODEX_HOME (a caller that already isolated it).
function isolatedCodexHome(baseDir: string): string {
  if (process.env.CODEX_HOME) return process.env.CODEX_HOME;
  const codexHome = join(baseDir, "codex-home");
  mkdirSync(codexHome, { recursive: true });
  const realAuth = join(homedir(), ".codex", "auth.json");
  if (existsSync(realAuth)) {
    try {
      symlinkSync(realAuth, join(codexHome, "auth.json"));
    } catch {
      /* already linked or unlinkable — codex will report an auth error the specs surface */
    }
  }
  return codexHome;
}

function python(): string {
  return process.env.AR_CHATS_E2E_PYTHON ?? "python3";
}

async function freePort(): Promise<number> {
  return new Promise((res, rej) => {
    const srv = createServer();
    srv.on("error", rej);
    srv.listen(0, "127.0.0.1", () => {
      const address = srv.address();
      const port = typeof address === "object" && address ? address.port : 0;
      srv.close(() => res(port));
    });
  });
}

function writeSettings(baseDir: string, coordinationRoot: string): string {
  mkdirSync(coordinationRoot, { recursive: true });
  mkdirSync(join(coordinationRoot, "logs", "mcp"), { recursive: true });
  // The settings JSON must live OUTSIDE the coordination root (the daemon rejects a settings file
  // inside it), so it goes in the base temp dir alongside — never under — coordinationRoot.
  const settingsPath = join(baseDir, "agents-remember-settings.json");
  writeFileSync(
    settingsPath,
    JSON.stringify(
      {
        version: 1,
        coordinationRoot,
        workspaceRoot: worktreeRoot,
        transcriptRoot: join(coordinationRoot, "logs", "mcp"),
        repositories: { "agents-remember": {} },
        providers: {},
        timeoutCaps: { toolSeconds: 60, providerSetupSeconds: 1800 },
        benchmarksEnabled: false,
        dashboard: { autoStart: false, port: 8765 },
      },
      null,
      2,
    ),
    "utf-8",
  );
  return settingsPath;
}

function buildAndSyncBundle(): void {
  if (process.env.AR_CHATS_E2E_SKIP_BUILD === "1") return;
  const build = spawnSync("npm", ["run", "build"], { cwd: dashboardDir, stdio: "inherit" });
  if (build.status !== 0) throw new Error("dashboard bundle build failed");
  if (existsSync(repoScripts)) {
    const sync = spawnSync(python(), [repoScripts], { cwd: worktreeRoot, stdio: "inherit" });
    if (sync.status !== 0) throw new Error("dashboard bundle sync failed");
  }
}

async function waitHealthy(baseUrl: string, proc: ChildProcess): Promise<void> {
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    if (proc.exitCode !== null) throw new Error(`daemon exited early (code ${proc.exitCode})`);
    try {
      const res = await fetch(`${baseUrl}/api/state`);
      if (res.ok || res.status === 200) return;
    } catch {
      /* not up yet */
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`daemon did not become healthy at ${baseUrl}`);
}

export async function bootDaemon(): Promise<BootedDaemon> {
  buildAndSyncBundle();
  const port = await freePort();
  const baseDir = mkdtempSync(join(tmpdir(), "ar-chats-e2e-"));
  const coordinationRoot = join(baseDir, "coordination");
  const settingsPath = writeSettings(baseDir, coordinationRoot);
  const baseUrl = `http://127.0.0.1:${port}`;
  // Capture the daemon's own stdout/stderr to a file so an E2E failure surfaces the daemon-side
  // error (composition tracebacks, harness runner exits) instead of losing it into Playwright's
  // piped stdout. The path is reported on the boot handle and echoed to the runner's console.
  const logPath = join(baseDir, "daemon.log");
  const logFd = openSync(logPath, "a");
  const codexHome = isolatedCodexHome(baseDir);
  const proc = spawn(
    python(),
    [
      "-m",
      "agents_remember.cli",
      "dashboard",
      "--config",
      settingsPath,
      "--host",
      "127.0.0.1",
      "--port",
      String(port),
      "--no-access-log",
    ],
    { cwd: worktreeRoot, stdio: ["ignore", logFd, logFd], env: { ...process.env, CODEX_HOME: codexHome } },
  );
  console.log(`[chats-e2e] daemon on ${baseUrl} · CODEX_HOME=${codexHome} · log ${logPath}`);
  await waitHealthy(baseUrl, proc);
  return {
    baseUrl,
    port,
    coordinationRoot,
    logPath,
    stop: () =>
      new Promise<void>((res) => {
        if (proc.exitCode !== null) return res();
        proc.once("exit", () => res());
        proc.kill("SIGTERM");
        setTimeout(() => {
          if (proc.exitCode === null) proc.kill("SIGKILL");
        }, 5_000);
      }),
  };
}
