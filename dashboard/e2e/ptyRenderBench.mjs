// The OQ-B renderer measurement driver (260715-FEUI-L6 S1). NOT a Playwright spec (plain node
// script, never collected by `npm run e2e`): drives /dev/pty-bench through headless Chromium
// and prints the frame stats per (renderer × panes) cell plus the serialize probe.
//
//   node e2e/ptyRenderBench.mjs [baseUrl]
//
// Caveat recorded with the numbers: headless Chromium on this box renders WebGL through
// SwiftShader (software GL) — real-GPU webgl numbers can only be better, but the DOM-renderer
// numbers are hardware-honest, so the decision logic below leans on DOM adequacy + the per-pane
// WebGL context budget rather than on beating software GL.
import { chromium } from "@playwright/test";

const base = process.argv[2] ?? "http://127.0.0.1:5173";
const CELLS = [
  { renderer: "dom", panes: 1 },
  { renderer: "dom", panes: 6 },
  { renderer: "dom", panes: 12 },
  { renderer: "webgl", panes: 1 },
  { renderer: "webgl", panes: 6 },
  { renderer: "webgl", panes: 12 },
];

const browser = await chromium.launch();
const results = [];
for (const cell of CELLS) {
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  const url = `${base}/dev/pty-bench?renderer=${cell.renderer}&panes=${cell.panes}&rate=20&seconds=10&settle=3`;
  await page.goto(url);
  await page.waitForFunction(() => window.__ptyBench?.done === true, undefined, { timeout: 60000 });
  const bench = await page.evaluate(() => window.__ptyBench);
  results.push(bench.stats ?? { error: bench.error, ...cell });
  console.log(JSON.stringify(bench.stats ?? bench, null, 2));
  await page.close();
}

// Serialize probe (reattach evaluation): 5000-line buffer, one serialize + one restore pass.
const page = await browser.newPage();
await page.goto(`${base}/dev/pty-bench?probe=serialize&lines=5000`);
await page.waitForFunction(() => window.__ptyBench?.done === true, undefined, { timeout: 120000 });
console.log("serialize probe:", JSON.stringify(await page.evaluate(() => window.__ptyBench), null, 2));
await page.close();

await browser.close();
