#!/usr/bin/env node
// Per-diff coverage floor for dashboard/src (L8-R7, mirroring the Python changed-lines gate):
// every changed line against the resolved base must be covered by the Vitest run. Resolves
// AR_GATE_DIFF_BASE, then origin/main, then git's empty tree. Requires the v8 coverage JSON
// emitted by `npm run test:coverage` (coverage/coverage-final.json).
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const dashboardRoot = fileURLToPath(new URL("..", import.meta.url));
const floor = Number(process.env.AR_DASHBOARD_DIFF_COVERAGE_FLOOR ?? 90);
const emptyTree = "4b825dc642cb6eb9a060e54bf8d69288fbee4904";

const git = (args) =>
  execFileSync("git", args, {
    cwd: dashboardRoot,
    encoding: "utf8",
    // The diff against a series fork point can be large; never let the default 1 MiB pipe
    // buffer turn a legitimate diff into a spawn failure.
    maxBuffer: 256 * 1024 * 1024,
  }).trim();

function resolveBase() {
  if (process.env.AR_GATE_DIFF_BASE) return process.env.AR_GATE_DIFF_BASE;
  try {
    git(["rev-parse", "--verify", "origin/main"]);
    return git(["merge-base", "origin/main", "HEAD"]);
  } catch {
    return emptyTree;
  }
}

const base = resolveBase();
console.log(`[diff-coverage] base=${base}`);

const diff = git([
  "diff",
  "--unified=0",
  base,
  "--",
  "src/**/*.ts",
  "src/**/*.tsx",
]);

const changedLines = new Map();
let currentFile = null;
let hunkStart = 0;
for (const line of diff.split("\n")) {
  const fileMatch = line.match(/^\+\+\+ b\/(.+)$/);
  if (fileMatch) {
    currentFile = fileMatch[1];
    if (!changedLines.has(currentFile)) {
      changedLines.set(currentFile, new Set());
    }
    continue;
  }
  const hunkMatch = line.match(/^@@ -\S+ \+(\d+)(?:,(\d+))? @@/);
  if (hunkMatch) {
    hunkStart = Number(hunkMatch[1]);
    continue;
  }
  if (!currentFile) continue;
  if (line.startsWith("+")) {
    changedLines.get(currentFile)?.add(hunkStart);
    hunkStart += 1;
  } else if (line.startsWith("-")) {
    // Removed lines have no new-file counterpart to cover; skip.
  } else if (line.startsWith(" ")) {
    hunkStart += 1;
  }
}

let coverage;
try {
  coverage = JSON.parse(
    readFileSync(join(dashboardRoot, "coverage", "coverage-final.json"), "utf8"),
  );
} catch {
  console.error(
    "[diff-coverage] FAIL: coverage/coverage-final.json missing; run `npm run test:coverage` first",
  );
  process.exit(1);
}

// v8 emits absolute source paths; normalize to the same relative keys git emits.
const coverageByRelative = new Map();
for (const [absolutePath, entry] of Object.entries(coverage)) {
  const srcIndex = absolutePath.lastIndexOf("/src/");
  coverageByRelative.set(
    srcIndex >= 0 ? absolutePath.slice(srcIndex + 1) : absolutePath,
    entry,
  );
}

function coveredLines(fileKey) {
  const entry = coverageByRelative.get(fileKey);
  if (!entry) return new Set();
  const covered = new Set();
  for (const [id, count] of Object.entries(entry.s)) {
    if (count <= 0) continue;
    const statement = entry.statementMap[id];
    for (let line = statement.start.line; line <= statement.end.line; line += 1) {
      covered.add(line);
    }
  }
  return covered;
}

let changedTotal = 0;
let coveredTotal = 0;
const missing = [];
for (const [file, lines] of changedLines) {
  const key = file.startsWith("dashboard/")
    ? file.slice("dashboard/".length)
    : file;
  if (!key.startsWith("src/")) continue;
  if (
    key.endsWith(".test.ts") ||
    key.endsWith(".test.tsx") ||
    key.startsWith("src/test/") ||
    key.startsWith("src/dev/") ||
    key.startsWith("src/types/")
  ) {
    continue;
  }
  const covered = coveredLines(key);
  for (const line of lines) {
    changedTotal += 1;
    if (covered.has(line)) {
      coveredTotal += 1;
    } else {
      missing.push(`${key}:${line}`);
    }
  }
}

const percent =
  changedTotal === 0 ? 100 : (coveredTotal / changedTotal) * 100;
console.log(
  `[diff-coverage] ${coveredTotal}/${changedTotal} changed lines covered (${percent.toFixed(1)}%, floor ${floor}%)`,
);
if (missing.length > 0) {
  console.log(`[diff-coverage] uncovered changed lines:\n${missing.join("\n")}`);
}
if (percent < floor) {
  console.error("[diff-coverage] FAIL");
  process.exit(1);
}
console.log("[diff-coverage] PASS");
