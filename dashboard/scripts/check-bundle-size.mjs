#!/usr/bin/env node
// Bundle budget gate (L8-R9): fail the build when the emitted dist exceeds the raw byte budget.
// The review measured a ~28 MB committed bundle; the budget is the rail's hard ceiling, with
// AR_DASHBOARD_BUNDLE_BUDGET_BYTES available to adjust a specific pipeline run.
import { readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const dashboardRoot = fileURLToPath(new URL("..", import.meta.url));
const distRoot = join(dashboardRoot, "dist");
const budgetBytes = Number(
  process.env.AR_DASHBOARD_BUNDLE_BUDGET_BYTES ?? 32 * 1024 * 1024,
);

function assetSizes(root) {
  const sizes = [];
  for (const name of readdirSync(root)) {
    const path = join(root, name);
    if (statSync(path).isDirectory()) {
      sizes.push(...assetSizes(path));
    } else {
      sizes.push({ path: relative(distRoot, path), size: statSync(path).size });
    }
  }
  return sizes;
}

const assets = assetSizes(distRoot);
const total = assets.reduce((sum, asset) => sum + asset.size, 0);
const largest = [...assets].sort((a, b) => b.size - a.size)[0];

console.log(
  `[bundle-budget] dist total ${total} bytes (budget ${budgetBytes})` +
    (largest ? `; largest ${largest.path} ${largest.size} bytes` : ""),
);
if (total > budgetBytes) {
  console.error(
    `[bundle-budget] FAIL: dist total ${total} exceeds budget ${budgetBytes}`,
  );
  process.exit(1);
}
console.log("[bundle-budget] PASS");
