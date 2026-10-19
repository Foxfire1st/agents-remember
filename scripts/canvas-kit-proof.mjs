#!/usr/bin/env node
/* Real selected-session CSP execution of all four entries, including carried licence bytes. */
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { writeFileSync } from "node:fs";
import { createServer } from "node:net";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const { chromium } = await import(
  pathToFileURL(resolve(root, "dashboard/node_modules/playwright/index.mjs"))
    .href
);
const port = await new Promise((done) => {
  const s = createServer();
  s.listen(0, "127.0.0.1", () => {
    const p = s.address().port;
    s.close(() => done(p));
  });
});
const base = `http://127.0.0.1:${port}`;
const server = spawn(
  resolve(root, "mcp/.venv/bin/python"),
  [resolve(root, "scripts/canvas_kit_proof_app.py"), String(port)],
  { cwd: root, stdio: ["ignore", "pipe", "inherit"] },
);
let browser;
try {
  let fixture;
  for (let n = 0; n < 300; n++) {
    try {
      const response = await fetch(`${base}/proof/kit`);
      if (response.ok) {
        fixture = await response.json();
        break;
      }
    } catch {}
    await new Promise((done) => setTimeout(done, 100));
  }
  if (!fixture) throw Error("kit fixture did not start");
  const opened = await (
    await fetch(`${base}/api/canvas/sessions`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        repository: "agents-remember",
        master: "canvas-isolation-proof",
        leaf: "260928-MIK-L104",
        frame: "review-page",
      }),
    })
  ).json();
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    reducedMotion: "reduce",
    viewport: { width: 1280, height: 900 },
  });
  const page = await context.newPage();
  const errors = [],
    foreign = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (request) => {
    if (new URL(request.url()).origin !== base) foreign.push(request.url());
  });
  await page.goto(`${base}/?session=${opened.session}`);
  let frame;
  for (let n = 0; n < 100; n++) {
    frame = page
      .frames()
      .find((frame) => frame.url().includes(`/canvas/page/${opened.session}/`));
    if (frame) break;
    await page.waitForTimeout(50);
  }
  await frame.waitForFunction(() => window.__kitProof);
  const observations = await frame.evaluate(() => ({
    origin: window.origin,
    ...window.__kitProof,
  }));
  if (
    observations.origin !== "null" ||
    !observations.shiki.highlighted ||
    !observations.shiki.morphDefined ||
    observations.shiki.code !== "const answer = 42" ||
    !observations.openGenerativeUi.token ||
    !observations.arVideo.pixel[3] ||
    !observations.motionCanvas.pixel[3] ||
    errors.length ||
    foreign.length
  )
    throw Error(JSON.stringify({ observations, errors, foreign }));
  const notices = [];
  for (const asset of fixture.publication.assets.filter((asset) =>
    /LICENSE|PROVENANCE|VOCABULARY/.test(asset.path),
  )) {
    const response = await fetch(`${base}${opened.pageUrl}${asset.path}`);
    const bytes = Buffer.from(await response.arrayBuffer());
    const digest = createHash("sha256").update(bytes).digest("hex");
    if (response.status !== 200 || digest !== asset.digest)
      throw Error(`carried notice differs: ${asset.path}`);
    notices.push({
      path: asset.path,
      status: response.status,
      bytes: bytes.length,
      sha256: digest,
    });
  }
  const output = process.argv[2];
  await page.screenshot({
    path: output.replace(/\.json$/, ".png"),
    fullPage: true,
  });
  const result = {
    state: "passed",
    browser: browser.version(),
    modelStarts: 0,
    fixture:
      "model-free authored page; all product host and publication boundaries real",
    session: opened,
    publication: fixture.publication,
    prospective: fixture.prospective,
    observations,
    notices,
    errors,
    foreign,
  };
  writeFileSync(output, JSON.stringify(result, null, 2) + "\n");
  console.log(
    JSON.stringify(
      {
        state: result.state,
        entries: fixture.entries,
        notices: notices.length,
        observations,
        output,
      },
      null,
      2,
    ),
  );
} finally {
  if (browser) await browser.close();
  server.kill("SIGTERM");
}
