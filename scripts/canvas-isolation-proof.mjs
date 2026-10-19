#!/usr/bin/env node
/**
 * Prove the generated page's isolation in the real browser (MIK-R104 rules 4, 8 and 10).
 *
 * This build serves generated scripts **enabled**: MIK-R104 v2 runs them inside the browser's
 * sandboxed frame and claims no stronger container (D331). The proof therefore shows the sandbox
 * *as configured*: scripts run, the document's origin is opaque, and the authority it does not have
 * is measured rather than assumed -- no cookie, storage, IndexedDB, parent DOM, opener, top
 * navigation, popup, form submission, worker or `eval`, no subresource outside the admitted assets,
 * and permissions denied with no parser warning. The frame navigating itself (a link, and the page's
 * own script) is an *accepted* channel: it is observed and named, never counted as a failed check.
 *
 * What it does not claim is named in the record, not engineered around: a live frame's timers have
 * no enforced count, and its heap and CPU/GPU have no browser-enforced ceiling.
 *
 * The check table comes from the proof server (`/proof/policy`), so the harness and the Python gate
 * cannot disagree about what was required; nothing here re-implements the policy or its digest.
 * Request effects are read at two controlled sinks (this host's ledger of method/path/status, and a
 * second origin's own hit log), not counted from the page's side.
 *
 * Usage: node scripts/canvas-isolation-proof.mjs [--check]
 */
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { createServer } from "node:net";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const { build } = await import(
  pathToFileURL(resolve(ROOT, "dashboard/node_modules/esbuild/lib/main.js"))
    .href
);
const panelBuild = await build({
  entryPoints: [resolve(ROOT, "scripts/canvas-panel-probe.tsx")],
  bundle: true,
  format: "esm",
  platform: "browser",
  jsx: "automatic",
  resolveExtensions: [".tsx", ".ts", ".jsx", ".js", ".json", ".mjs"],
  nodePaths: [resolve(ROOT, "dashboard/node_modules")],
  write: false,
});
const panelBytes = panelBuild.outputFiles[0].contents;
const panelFile = resolve(
  mkdtempSync(resolve(tmpdir(), "ar-canvas-panel-")),
  "panel.js",
);
writeFileSync(panelFile, panelBytes);
process.env.AR_CANVAS_PANEL_BUNDLE = panelFile;
const RECORD = resolve(
  ROOT,
  "mcp/src/agents_remember/package_data/canvas/isolation-proof.json",
);
const CHECK_ONLY = process.argv.includes("--check");
const PLAYWRIGHT =
  process.env.AR_PLAYWRIGHT ||
  resolve(ROOT, "dashboard/node_modules/playwright/index.js");
const playwright = await import(pathToFileURL(PLAYWRIGHT).href);
const { chromium } = playwright.chromium ? playwright : playwright.default;

/** The feature surfaces the served Permissions-Policy must deny. */
const PERMISSION_FEATURES = [
  "clipboard-read",
  "clipboard-write",
  "camera",
  "microphone",
  "geolocation",
  "fullscreen",
  "usb",
];

/** A console line that is the browser refusing something the page tried. */
const REFUSAL =
  /Refused to|violates the following Content Security Policy|Blocked form submission/i;

async function freePort() {
  return await new Promise((resolvePort) => {
    const server = createServer();
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close(() => resolvePort(port));
    });
  });
}

async function waitForServer(base, attempts = 150) {
  for (let index = 0; index < attempts; index += 1) {
    try {
      const response = await fetch(`${base}/proof/policy`);
      if (response.ok) return await response.json();
    } catch {
      /* not up yet */
    }
    await new Promise((resolveWait) => setTimeout(resolveWait, 100));
  }
  throw new Error(`the proof server did not answer at ${base}`);
}

function spawnServer(port) {
  const python = process.env.AR_PYTHON || resolve(ROOT, "mcp/.venv/bin/python");
  return spawn(
    python,
    [resolve(ROOT, "scripts/canvas_proof_app.py"), String(port)],
    {
      cwd: ROOT,
      stdio: ["ignore", "pipe", "inherit"],
      env: { ...process.env, PYTHONPATH: resolve(ROOT, "mcp/src") },
    },
  );
}

async function openSession(base, frame = "review-page") {
  const response = await fetch(`${base}/api/canvas/sessions`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      repository: "agents-remember",
      master: "canvas-isolation-proof",
      leaf: "260928-MIK-L104",
      frame,
    }),
  });
  const opened = await response.json();
  if (!opened.session)
    throw new Error(`no session for ${frame}: ${JSON.stringify(opened)}`);
  return opened;
}

const ledger = async (base) =>
  await (await fetch(`${base}/proof/ledger`)).json();
const resetLedger = async (base) => {
  await fetch(`${base}/proof/ledger/reset`, { method: "POST" });
};

function hostStatus(lines, path) {
  const hit = lines.find((line) => line.includes(` ${path} -> `));
  return hit ? Number(hit.slice(hit.lastIndexOf("-> ") + 3)) : null;
}

function hostCount(lines, fragment) {
  return lines.filter((line) => line.includes(fragment)).length;
}

const cspDirective = (csp, name) =>
  (
    csp
      .split(";")
      .map((part) => part.trim())
      .find((part) => part.startsWith(name)) || ""
  ).trim();

/** Whether ``script-src`` permits exactly the admitted prefix and inline script. */
function scriptSourceVerdict(csp, origin, prefix) {
  const tokens = cspDirective(csp, "script-src").split(/\s+/).slice(1);
  const admitted = `${origin}${prefix}`;
  const ok =
    tokens.length === 2 &&
    tokens.includes("'unsafe-inline'") &&
    tokens.includes(admitted);
  return ok
    ? "admitted-prefix-and-inline"
    : `other:${cspDirective(csp, "script-src")}`;
}

async function waitForFrame(page, fragment) {
  for (let attempt = 0; attempt < 150; attempt += 1) {
    const frame = page.frames().find((item) => item.url().includes(fragment));
    if (frame) {
      await frame.waitForLoadState("load");
      return frame;
    }
    await page.waitForTimeout(100);
  }
  throw new Error(`no frame loaded ${fragment}`);
}

async function waitForLanding(page, fragment, attempts = 80) {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const frame = page.frames().find((item) => item.url().endsWith(fragment));
    if (frame) return frame;
    await page.waitForTimeout(100);
  }
  return null;
}

/** The document's own state, read in its JS context by the automation protocol. */
async function readDocument(frame) {
  return await frame.evaluate((features) => {
    const report = window.__probe || {};
    const image = document.getElementById("cors-image");
    const allowed = document.featurePolicy
      ? features.filter((name) => document.featurePolicy.allowsFeature(name))
      : ["no-feature-policy"];
    const attempt = (run) => {
      try {
        return run();
      } catch (error) {
        return `blocked:${error.name}`;
      }
    };
    return {
      report,
      cookie: attempt(() => document.cookie),
      origin: String(window.origin),
      opener: attempt(() => String(window.opener)),
      inlineRan: window.__inlineRan === 1,
      assetRan: window.__assetRan === 1,
      externalRan: window.__extRan === 1,
      background: attempt(
        () => getComputedStyle(document.body).backgroundColor,
      ),
      imageWidth: image ? image.naturalWidth : 0,
      allowedFeatures: allowed,
    };
  }, PERMISSION_FEATURES);
}

/** The page's own probe outcomes, mapped onto the record's check vocabulary. */
function documentVerdicts(read) {
  const report = read.report;
  const probe = (name) =>
    read.report[name] === undefined ? "not-probed" : read.report[name];
  return {
    documentOrigin: read.origin,
    inlineScript: read.inlineRan ? "ran" : "blocked",
    ownAssetScript: read.assetRan ? "ran" : "blocked",
    externalScript: read.externalRan ? "ran" : "blocked",
    cookie:
      report.cookie === "ok"
        ? read.cookie === ""
          ? "blocked:empty-after-set"
          : `reachable:${read.cookie}`
        : probe("cookie"),
    localStorage: report.storage === "ok" ? "reachable" : probe("storage"),
    sessionStorage:
      report.sessionStorage === "ok" ? "reachable" : probe("sessionStorage"),
    indexedDb: report.indexedDb === "ok" ? "reachable" : probe("indexedDb"),
    parentDom: report.parentDom === "ok" ? "reached" : probe("parentDom"),
    opener: read.opener,
    popup:
      report.popup === undefined
        ? "not-probed"
        : report.popup === "null"
          ? "blocked:null-window"
          : `opened:${report.popup}`,
    eval: report.eval === "2" ? "allowed" : probe("eval"),
    admittedStyle:
      read.background === "rgb(15, 17, 21)"
        ? "applied"
        : `missing:${read.background}`,
    admittedCorsImage: read.imageWidth > 0 ? "loaded" : "missing",
    permissions:
      read.allowedFeatures.length === 0
        ? "denied"
        : `allowed:${read.allowedFeatures.join(",")}`,
  };
}

/** A worker the policy must refuse: the constructor may not throw, so the refusal is also read. */
function workerVerdict(read, refusals) {
  if (read.report.worker !== "ok") return read.report.worker || "not-probed";
  const refused = refusals.some(
    (text) =>
      /worker/i.test(text) &&
      /Refused|violates|Content Security Policy/i.test(text),
  );
  return refused ? "blocked:refused-by-policy" : "started";
}

function consoleVerdicts(messages) {
  return {
    permissionsPolicyWarnings: String(
      messages.filter((text) =>
        /Permissions-Policy|Unrecognized feature/i.test(text),
      ).length,
    ),
    cspViolations: messages.some((text) => REFUSAL.test(text))
      ? "refused-by-policy"
      : "none-observed",
  };
}

/** Everything one page load can show: the document, its assets, a form, and a link navigation. */
async function framedPhase(base, context, session, messages) {
  await resetLedger(base);
  const start = messages.length;
  const page = await context.newPage();
  page.on("console", (message) => messages.push(message.text()));
  await page.goto(`${base}/?session=${session}&mode=observe`, {
    waitUntil: "load",
  });
  const frame = await waitForFrame(page, "/canvas/page/");
  await page.waitForTimeout(700);

  const read = await readDocument(frame);
  const topAfterProbes = page.url();
  const afterLoad = await ledger(base);
  const checks = {
    ...documentVerdicts(read),
    ...consoleVerdicts(messages.slice(start)),
    worker: workerVerdict(read, messages.slice(start)),
    hostApiRequests: String(hostCount(afterLoad.host, "/api/state")),
    externalSinkRequests: String(afterLoad.external.length),
    topNavigation:
      topAfterProbes !== `${base}/?session=${session}&mode=observe`
        ? "navigated-the-top"
        : read.report.topNavigation === "ok"
          ? "blocked:silently-ignored"
          : read.report.topNavigation || "not-probed",
  };

  await frame.click("#host-form button");
  await page.waitForTimeout(700);
  const afterForm = await ledger(base);
  checks.formNavigation =
    hostCount(afterForm.host, "POST /api/state") === 0
      ? "blocked:no-request"
      : "reached";

  await frame.click("#host-link");
  await page.waitForTimeout(900);
  const afterLink = await ledger(base);
  const movedFrame = page
    .frames()
    .some((item) => item.url().endsWith("/api/state"));
  const topStayed = page.url().startsWith(`${base}/?session=`);
  checks.linkNavigation = movedFrame && topStayed ? "self-frame-only" : "other";
  checks._linkStatus = hostStatus(afterLink.host, "/api/state");
  await page.close();
  return checks;
}

/** The page's own script navigates its frame: an accepted channel, observed and named. */
async function selfNavigationPhase(base, context, session, messages) {
  await resetLedger(base);
  const page = await context.newPage();
  page.on("console", (message) => messages.push(message.text()));
  await page.goto(`${base}/?session=${session}&mode=selfnav`, {
    waitUntil: "load",
  });
  await waitForFrame(page, "/canvas/page/");
  const landed = await waitForLanding(page, "/api/state");
  let origin = "unreadable";
  if (landed) {
    try {
      origin = await landed.evaluate(() => String(window.origin));
    } catch (error) {
      origin = `unreadable:${error.name}`;
    }
  }
  const after = await ledger(base);
  const status = hostStatus(after.host, "/api/state");
  const topStayed = page.url().startsWith(`${base}/?session=`);
  const writes = hostCount(after.host, "POST ");
  await page.close();
  return {
    selfNavigation:
      `observed:navigated;${landed ? "/api/state" : "no-landing"};status:${status};` +
      `landing-origin:${origin};top-unchanged:${topStayed};writes:${writes}`,
  };
}

/** A meta refresh the page inserts: another way its frame navigates itself (D331 accepts it). */
async function metaPhase(base, context, session, messages) {
  await resetLedger(base);
  const page = await context.newPage();
  page.on("console", (message) => messages.push(message.text()));
  await page.goto(`${base}/?session=${session}&mode=meta`, {
    waitUntil: "load",
  });
  await waitForFrame(page, "/canvas/page/");
  const landed = await waitForLanding(page, "/api/state", 40);
  const after = await ledger(base);
  const topStayed = page.url().startsWith(`${base}/?session=`);
  const refused = messages.some((text) => /meta http-equiv/i.test(text));
  await page.close();
  if (landed) {
    return {
      metaNavigation:
        `observed:script-inserted-meta-refresh-navigated;/api/state;` +
        `status:${hostStatus(after.host, "/api/state")};` +
        `top-unchanged:${topStayed}`,
    };
  }
  return {
    metaNavigation: refused
      ? "blocked:refused-by-sandbox"
      : "blocked:no-navigation",
  };
}

/** An interval the page starts, and its stop when the document is disposed. */
async function timerPhase(base, context, session, messages) {
  const page = await context.newPage();
  page.on("console", (message) => messages.push(message.text()));
  const start = messages.length;
  await page.goto(`${base}/?session=${session}&mode=timer`, {
    waitUntil: "load",
  });
  await page.waitForTimeout(1200);
  const live = messages
    .slice(start)
    .filter((text) => REFUSAL.test(text)).length;
  await page.close();
  const closed = messages.length;
  await new Promise((resolveWait) => setTimeout(resolveWait, 800));
  const after = messages
    .slice(closed)
    .filter((text) => REFUSAL.test(text)).length;
  return {
    timerRuns: live > 0 ? "active" : "never-ran",
    timerReleasedOnClose: after === 0 ? "stopped" : `still-running:${after}`,
  };
}

/** The frame the shell framed: the one whose parent is the shell page, not a nested attempt. */
async function waitForOwnFrame(page, session) {
  const fragment = `/canvas/page/${session}/`;
  for (let attempt = 0; attempt < 150; attempt += 1) {
    const frame = page
      .frames()
      .find(
        (item) =>
          item.parentFrame() === page.mainFrame() &&
          item.url().includes(fragment),
      );
    if (frame) {
      await frame.waitForLoadState("load");
      return frame;
    }
    await page.waitForTimeout(100);
  }
  throw new Error(`the shell framed no page for ${fragment}`);
}

/** One matrix verdict from the outcomes of every target that class was attempted against. */
function matrixVerdict(matrix, keys) {
  const missing = keys.filter((key) => typeof matrix[key] !== "string");
  if (missing.length) return `not-probed:${missing.join(",")}`;
  const loaded = keys.filter((key) => !matrix[key].startsWith("blocked"));
  if (loaded.length) {
    return `loaded:${loaded.map((key) => `${key}=${matrix[key]}`).join(",")}`;
  }
  const reasons = [
    ...new Set(
      keys.map((key) => matrix[key].replace(/^blocked:?/, "") || "refused"),
    ),
  ];
  return `blocked:${reasons.join("+")}`;
}

/** The load classes the matrix exercises, and the outcomes the record turns into checks. */
function matrixChecks(matrix) {
  const both = (kind) =>
    matrixVerdict(
      matrix,
      ["revision", "frame", "task", "session"].map(
        (label) => `${kind}:${label}`,
      ),
    );
  return {
    ownScript: matrixVerdict(matrix, ["script:own"]),
    ownStyle: matrixVerdict(matrix, ["style:own"]),
    ownImage: matrixVerdict(matrix, ["image:own"]),
    foreignScript: both("script"),
    foreignStyle: both("style"),
    foreignImage: both("image"),
    foreignFont: both("font"),
    foreignMedia: both("media"),
    foreignIframe: both("iframe"),
    foreignWorker: both("worker"),
    foreignFetch: both("fetch"),
    foreignTaskPath: matrixVerdict(
      matrix,
      [
        "script",
        "style",
        "image",
        "font",
        "media",
        "iframe",
        "worker",
        "fetch",
      ].map((kind) => `${kind}:task`),
    ),
    assetMatrix:
      Object.keys(matrix).length === 35
        ? "complete:35"
        : `incomplete:${Object.keys(matrix).length}`,
  };
}

/**
 * The browser's own frame list is the iframe witness: a refused frame appears as the browser's
 * error page, and a frame that loaded appears at its URL. The element's `load` event fires for
 * both, so the element cannot answer the iframe class by itself.
 */
function applyIframeVerdicts(matrix, targets, frames, refusedByPolicy) {
  Object.keys(matrix)
    .filter((key) => key.startsWith("iframe:"))
    .forEach((key) => {
      const target = targets[key];
      if (!target) {
        matrix[key] = "blocked:no-target";
        return;
      }
      const path = new URL(target).pathname;
      if (frames.some((url) => url.includes(path))) {
        matrix[key] = "loaded";
      } else if (frames.some((url) => url.startsWith("chrome-error://"))) {
        matrix[key] = refusedByPolicy
          ? "blocked:SecurityPolicyViolation"
          : "blocked:error-page";
      } else {
        matrix[key] = "blocked:no-frame";
      }
    });
}

/**
 * The asset-authority matrix (L104-B03): one page, every governed load class, two existing
 * revisions and a sibling frame plus the leaf's task report.
 *
 * The page attempts each class against its own relative asset, then against the previous revision
 * of its own frame and the sibling frame's revision through its own session prefix -- the only
 * asset address a served document holds -- and against the leaf's task report. The sibling frame's
 * live session value is handed to it in `foreign=`, and those attempts are recorded separately:
 * a session value is a host-issued capability, not an address the document derives, so the run
 * measures that case instead of asserting it.
 */
async function matrixPhase(base, context, session, foreignSession, messages) {
  await resetLedger(base);
  const page = await context.newPage();
  page.on("console", (message) => messages.push(message.text()));
  const extra = foreignSession
    ? `&foreign=${encodeURIComponent(foreignSession)}`
    : "";
  await page.goto(`${base}/?session=${session}&mode=matrix${extra}`, {
    waitUntil: "load",
  });
  const frame = await waitForOwnFrame(page, session);
  let state = { matrix: {}, targets: {}, violations: [], done: false };
  for (let attempt = 0; attempt < 120; attempt += 1) {
    state = await frame.evaluate(() => ({
      matrix:
        window.__probe && window.__probe.matrix ? window.__probe.matrix : {},
      targets:
        window.__probe && window.__probe.matrixTargets
          ? window.__probe.matrixTargets
          : {},
      violations:
        window.__probe && window.__probe.violations
          ? window.__probe.violations
          : [],
      done: window.__matrixDone === true,
    }));
    if (state.done) break;
    await page.waitForTimeout(100);
  }
  const frames = page.frames().map((item) => item.url());
  await page.close();
  applyIframeVerdicts(
    state.matrix,
    state.targets,
    frames,
    state.violations.some((entry) => entry.directive === "frame-src"),
  );
  const responses = await ledger(base);
  for (const [key, target] of Object.entries(state.targets)) {
    if (key.endsWith(":own")) continue;
    const pathname = new URL(target).pathname;
    if (responses.host.some((line) => line.includes(` ${pathname} -> 200`)))
      state.matrix[key] = "loaded:http-200";
  }
  const violations = state.violations
    .filter(
      (entry) =>
        String(entry.blocked).includes("/canvas/page/") ||
        entry.directive === "frame-src",
    )
    .slice(0, 24);
  return {
    matrix: state.matrix,
    targets: state.targets,
    requests: responses,
    violations,
    done: state.done,
    checks: matrixChecks(state.matrix),
  };
}

/** A document navigation request reaches no navigation guard: the host has none (D331). */
async function guardObservation(base) {
  const response = await fetch(`${base}/api/state`, {
    headers: {
      "sec-fetch-mode": "navigate",
      "sec-fetch-dest": "document",
      "sec-fetch-site": "same-origin",
    },
  });
  return {
    navigationGuard: response.status === 403 ? "present" : "absent",
    status: response.status,
  };
}

async function directOpenChecks(base, context, pageUrl) {
  await resetLedger(base);
  const sentinel = await context.newPage();
  await sentinel.goto(`${base}/?mode=sentinel&dashboard=1`, {
    waitUntil: "load",
  });
  const [popup] = await Promise.all([
    sentinel.waitForEvent("popup"),
    sentinel.evaluate((url) => {
      window.__opened = window.open(url, "probe-direct");
    }, pageUrl),
  ]);
  await popup.waitForLoadState("load");
  const checks = {
    directOpenOrigin: await popup.evaluate(() => String(window.origin)),
    directOpenScript: await popup.evaluate(() =>
      window.__inlineRan === 1 ? "ran" : "blocked",
    ),
    directOpenCookie: await popup.evaluate(() => {
      try {
        return document.cookie.includes("dashboard=secret")
          ? "reachable"
          : "blocked";
      } catch (error) {
        return `blocked:${error.name}`;
      }
    }),
    directOpenStorage: await popup.evaluate(() => {
      try {
        localStorage.setItem("y", "1");
        return "reachable";
      } catch (error) {
        return `blocked:${error.name}`;
      }
    }),
    directOpenOpener: await popup.evaluate(() => String(window.opener)),
    directOpenDom: await sentinel.evaluate(() => {
      try {
        return String(window.__opened.document.body);
      } catch (error) {
        return `blocked:${error.name}`;
      }
    }),
    directOpenSentinel: await sentinel.evaluate(() => {
      try {
        return String(window.__opened.__dashboardSentinel);
      } catch (error) {
        return `blocked:${error.name}`;
      }
    }),
  };
  await popup.close();
  await sentinel.close();
  return checks;
}

async function servedHeaders(pageUrl, assetUrl, policy) {
  const document = await fetch(pageUrl);
  const csp = document.headers.get("content-security-policy") || "";
  const asset = await fetch(assetUrl);
  const origin = new URL(pageUrl).origin;
  const checks = {
    servedSandbox: (csp.split(";")[0] || "").trim(),
    servedScriptSrc: scriptSourceVerdict(
      csp,
      origin,
      new URL(pageUrl).pathname,
    ),
    frameCorp: document.headers.get("cross-origin-resource-policy"),
    assetCorp: asset.headers.get("cross-origin-resource-policy"),
    assetCors: asset.headers.get("access-control-allow-origin"),
  };
  const servedPolicy = {
    origin: new URL(pageUrl).origin,
    session: new URL(pageUrl).pathname.split("/")[3],
    csp,
    permissionsPolicy: document.headers.get("permissions-policy"),
    openerPolicy: document.headers.get("cross-origin-opener-policy"),
    frameCorp: checks.frameCorp,
    assetCorp: checks.assetCorp,
    assetCors: checks.assetCors,
  };
  return { checks, servedPolicy };
}

/** Dispose the channel the page used, and observe what a revoked channel serves. */
async function disposalChecks(base, session, pageUrl, policy) {
  await fetch(`${base}/api/canvas/sessions/${session}/revoke`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ reason: "navigated" }),
  });
  const page = await fetch(pageUrl);
  const asset = await fetch(`${pageUrl}${policy.assetPath}`);
  return {
    revokedPage: String(page.status),
    revokedAsset: String(asset.status),
  };
}

/**
 * MIK-R104 rule 6 / L104-B02: the frame navigates itself, and the product's own reaction to that
 * second load -- the panel's `onFrameLoad` -- revokes the channel. The old document and a message on
 * the old channel must then answer 410, and the ready revision must still be servable to a fresh
 * channel. The navigation is the accepted channel of D331; the revocation is what is measured here.
 */
async function navigationRevocationPhase(base, context, messages) {
  const page = await context.newPage();
  const revokes = [];
  const allocated = [];
  page.on("console", (message) => messages.push(message.text()));
  page.on("response", async (response) => {
    if (response.url() === `${base}/api/canvas/sessions`)
      allocated.push(await response.json());
    if (response.url().endsWith("/revoke"))
      revokes.push({ url: response.url(), body: await response.json() });
  });
  await page.goto(`${base}/proof/panel?navigate=1`);
  await page.waitForFunction(() =>
    document.querySelector('[data-testid="canvas-navigated"]'),
  );
  for (let i = 0; i < 100 && (allocated.length < 2 || revokes.length < 1); i++)
    await page.waitForTimeout(50);
  const opened = allocated[0];
  if (!opened) throw new Error("the mounted panel allocated no channel");
  const oldPage = await fetch(`${base}${opened.pageUrl}`);
  const oldMessage = await fetch(
    `${base}/api/canvas/sessions/${opened.session}/messages`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ type: "ready" }),
    },
  );
  const fresh = allocated.at(-1);
  const freshPage = await fetch(`${base}${fresh.pageUrl}`);
  // The generated frame speaks through the real panel and the real protocol endpoint.
  const frame = page
    .frames()
    .find((frame) => frame.url().includes(`/canvas/page/${fresh.session}/`));
  await frame.waitForFunction(() => window.__hostBinding);
  const binding = await frame.evaluate(() => window.__hostBinding);
  if (
    binding.session !== fresh.session ||
    binding.revision !== fresh.revision ||
    binding.comparison !== fresh.comparison
  )
    throw Error("mounted panel sent a foreign binding");
  const exchange = page.waitForResponse((response) =>
    response.url().endsWith(`/sessions/${fresh.session}/messages`),
  );
  await frame.evaluate(() =>
    parent.postMessage(
      {
        ...window.__hostBinding,
        schema: "ar-canvas-message/v1",
        type: "ready",
        requestId: "browser-ready-0001",
        payload: {},
      },
      "*",
    ),
  );
  const answered = await (await exchange).json();
  await page.close();
  return {
    navigatedAway: "observed:navigated",
    navigatedRevoke: String(oldPage.status),
    navigatedMessage: String(oldMessage.status),
    navigatedRevoked: revokes.some(
      (row) => row.url.includes(opened.session) && row.body.revoked === true,
    )
      ? "revoked"
      : "not-revoked",
    revisionRetained:
      fresh.session !== opened.session && freshPage.status === 200
        ? "served"
        : "absent",
    browserProtocol:
      answered.state === "accepted" ? "accepted" : JSON.stringify(answered),
  };
}

function evaluateRecord(
  checks,
  servedPolicy,
  browserVersion,
  policy,
  evidence,
) {
  const failedChecks = [];
  for (const [name, expected] of Object.entries(evidence.required)) {
    const value = checks[name];
    if (typeof value !== "string" || !value.startsWith(expected)) {
      failedChecks.push(`${name} observed ${JSON.stringify(value)}`);
    }
  }
  const proven = failedChecks.length === 0;
  const scriptsEnabled = proven && policy.scriptPolicy === "enabled";
  const revisions = evidence.revisions;
  const crossSession = evidence.crossSession;
  const crossLoaded = Object.entries(crossSession).filter(
    ([, value]) => !String(value).startsWith("blocked"),
  );
  const crossSentence = `${Object.keys(crossSession).length} live sibling-session classes were asserted; ${crossLoaded.length} loaded. `;
  const matrixSentence = `The matrix exercised all eight load classes against four foreign targets (previous revision, sibling frame, task path and known live sibling session), plus three own-asset controls. Fonts used FontFace.load(); media decode errors alone were not proof: any foreign HTTP 200 fails. ${crossSentence}`;
  return {
    schema: "ar-canvas-isolation-proof/v1",
    state: proven ? "proven" : "failed",
    scriptPolicy: policy.scriptPolicy,
    scriptsEnabled,
    provenScope: "generated scripts in the browser's sandboxed frame",
    policyDigest: policy.policyDigest,
    originArrangement: policy.originArrangement,
    browser: "chromium",
    browserVersion,
    checkedAt: new Date().toISOString(),
    sandbox: policy.sandbox,
    origin: {
      arrangement: policy.originArrangement,
      documentOrigin: checks.documentOrigin,
      directOpenOrigin: checks.directOpenOrigin,
      landingOrigin: evidence.landingOrigin,
    },
    servedPolicy,
    permissions: {
      header: policy.permissionsPolicy,
      deniedFeatures: PERMISSION_FEATURES,
      observed: checks.permissions,
      parserWarnings: Number(checks.permissionsPolicyWarnings),
    },
    source: evidence.source,
    artifacts: evidence.artifacts,
    requests: {
      host: evidence.hostRequests,
      externalSink: evidence.externalHits,
      hostApiRequests: Number(checks.hostApiRequests),
      externalSinkRequests: Number(checks.externalSinkRequests),
    },
    navigation: {
      link: checks.linkNavigation,
      selfNavigation: checks.selfNavigation,
      form: checks.formNavigation,
      top: checks.topNavigation,
      meta: checks.metaNavigation,
      guard: checks.navigationGuard,
      revokedAfterNavigation: checks.navigatedRevoke,
      messageAfterNavigation: checks.navigatedMessage,
      channelRevokedOnNavigation: checks.navigatedRevoked,
      revisionRetainedAfterNavigation: checks.revisionRetained,
      detail:
        "MIK-R104 v2 (D331) accepts the frame navigating itself. These navigations are observed " +
        "and named; none is a failed boundary, and none gained a write.",
    },
    disposal: {
      timerWhileLive: checks.timerRuns,
      timerAfterClose: checks.timerReleasedOnClose,
      revokedPage: checks.revokedPage,
      revokedAsset: checks.revokedAsset,
    },
    assets: {
      revisions,
      taskReport: policy.taskReport,
      siblingFrame: policy.foreign.siblingFrame,
      targets: evidence.targets,
      matrix: evidence.matrix,
      matrixComplete: evidence.matrixComplete,
      requests: evidence.matrixRequests,
      violations: evidence.violations,
      crossSession,
      detail: matrixSentence,
    },
    requiredChecks: Object.entries(evidence.required).map(
      ([name, expected]) => ({
        name,
        expected,
      }),
    ),
    observed: Object.entries(evidence.required).map(
      ([name]) => `${name}: ${checks[name]}`,
    ),
    failedChecks,
    unprovenBoundary: policy.containerRequired,
    detail: proven
      ? `the sandbox as configured was observed in Chromium ${browserVersion}. Generated scripts ` +
        "ran inside it (inline and admitted asset); the document is an opaque origin with no " +
        "cookie, storage, IndexedDB, parent DOM or opener; the sandbox refused top navigation, " +
        "popups and form submission; the served policy refused every subresource outside the " +
        "admitted assets (0 host-API and 0 external-sink requests, refusals on the console); " +
        "permissions are denied with 0 parser warnings; admitted assets loaded, including a " +
        "CORS-mode image. The frame navigated itself three ways -- a link, the page's own script, " +
        "and a meta refresh the page inserted (one document is served, so that tag is inserted by " +
        "the page's script) -- which MIK-R104 v2 accepts as a channel (D331): each is observed and " +
        "named here, none is a failed boundary, and none gained a write. " +
        `${matrixSentence} ` +
        "Disposal: the document's interval stopped when the page closed and a revoked channel " +
        "answered 410. Not contained and not claimed: a live frame's timers have no enforced " +
        "count, and its heap and CPU/GPU have no browser-enforced ceiling; no stronger container " +
        "is built (rule 8, D331)."
      : `the run did not hold: ${failedChecks.join("; ")}`,
  };
}

async function main() {
  const port = await freePort();
  const base = `http://127.0.0.1:${port}`;
  const server = spawnServer(port);
  const policy = await waitForServer(base);
  const required = Object.fromEntries(
    policy.requiredChecks.map((check) => [check.name, check.expected]),
  );
  const browser = await chromium.launch({
    args: ["--no-sandbox"],
    timeout: 60000,
  });
  const context = await browser.newContext();
  const messages = [];
  // B02 runs first and hands both of its channels back: it must not displace the page under test
  // from the four-session registry (MAX_ACTIVE_FRAMES).
  const navigationRevocation = await navigationRevocationPhase(
    base,
    context,
    messages,
  );
  const opened = await openSession(base);
  const pageUrl = `${base}${policy.assetPrefix}${opened.session}/`;

  // The sibling frame's live session: a foreign frame the matrix is handed the value of (L104-B03).
  // A session for the *previous revision* of the page's own frame cannot be opened: the route names
  // no revision, and opening one for the same frame would revoke the page under test.
  const sibling = await openSession(base, policy.foreign.siblingFrame);

  const framed = await framedPhase(base, context, opened.session, messages);
  const selfNavigation = await selfNavigationPhase(
    base,
    context,
    opened.session,
    messages,
  );
  const meta = await metaPhase(base, context, opened.session, messages);
  const timer = await timerPhase(base, context, opened.session, messages);
  const matrix = await matrixPhase(
    base,
    context,
    opened.session,
    sibling.session,
    messages,
  );
  const guard = await guardObservation(base);
  const direct = await directOpenChecks(base, context, pageUrl);
  const headers = await servedHeaders(
    pageUrl,
    `${pageUrl}${policy.assetPath}`,
    policy,
  );
  const disposal = await disposalChecks(base, opened.session, pageUrl, policy);

  const source = await (await fetch(`${base}/proof/source`)).json();
  source.harness = createHash("sha256")
    .update(readFileSync(fileURLToPath(import.meta.url)))
    .digest("hex");
  source.panelBundle = createHash("sha256").update(panelBytes).digest("hex");
  source.harnessFile = "scripts/canvas-isolation-proof.mjs";
  source.proofServerFile = "scripts/canvas_proof_app.py";
  const artifacts = await (await fetch(`${base}/proof/artifacts`)).json();
  const finalLedger = await ledger(base);

  const checks = {
    ...framed,
    ...selfNavigation,
    ...navigationRevocation,
    ...meta,
    ...timer,
    ...matrix.checks,
    ...guard,
    ...direct,
    ...headers.checks,
    ...disposal,
  };
  const crossSession = Object.fromEntries(
    Object.entries(matrix.matrix).filter(([key]) => key.endsWith(":session")),
  );
  const browserVersion = browser.version();
  await browser.close();
  server.kill("SIGTERM");

  const record = evaluateRecord(
    checks,
    headers.servedPolicy,
    browserVersion,
    policy,
    {
      required,
      source,
      artifacts,
      landingOrigin: selfNavigation.selfNavigation
        .split("landing-origin:")[1]
        ?.split(";")[0],
      hostRequests: finalLedger.host,
      externalHits: finalLedger.external,
      revisions: {
        page: opened.revision,
        revision: policy.foreign.revision,
        sibling: policy.foreign.sibling,
      },
      taskReport: policy.taskReport,
      matrix: matrix.matrix,
      matrixComplete: matrix.done,
      matrixRequests: matrix.requests,
      targets: matrix.targets,
      violations: matrix.violations,
      crossSession,
    },
  );
  if (!CHECK_ONLY)
    writeFileSync(RECORD, `${JSON.stringify(record, null, 2)}\n`);
  process.stdout.write(`${JSON.stringify(record, null, 2)}\n`);
  process.exit(record.state === "proven" ? 0 : 1);
}

main().catch((error) => {
  process.stderr.write(
    `canvas isolation proof failed to run: ${error.stack}\n`,
  );
  process.exit(2);
});
