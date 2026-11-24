#!/usr/bin/env node
/**
 * Load one prospective revision in Chromium without a model (MIK-R104 rule 14).
 *
 * `--dir <staged assets> --entry <entrypoint>`: this serves ONLY that directory from a random
 * loopback port, opens it in Chromium at 1280x720, and prints one JSON object. It is not a
 * `file://` load: a file URL answers with no headers at all, so the CSP, the sandbox and the
 * CORP/CORS arrangement the page really executes under would be untested. The headers below mirror
 * `mcp/src/agents_remember/canvas/policy.py` (scripts enabled, the shipped v2 arrangement);
 * asset paths live under the same `/canvas/page/` prefix the page is served from, so the
 * allowlist is the same expression it is in production.
 *
 * The response is a report, not a verdict the caller has to trust: `agents_remember.canvas.
 * prospective` re-checks that a "loaded" answer names no fault of its own.
 */
import { createServer } from "node:http";
import { writeSync } from "node:fs";
import { readFile, stat } from "node:fs/promises";
import { extname, join, resolve, sep } from "node:path";
import { pathToFileURL } from "node:url";
import { interactiveReport } from "./canvas-interactive-report.mjs";
import { collectDomInventory } from "./canvas-dom-inventory.mjs";

const PREFIX = "/canvas/page/prospective/";
// When this load began: its caller's time limit counts from here.
const STARTED = Date.now();
const PLAYWRIGHT = process.env.AR_PLAYWRIGHT
  ? pathToFileURL(process.env.AR_PLAYWRIGHT).href
  : "playwright";

const MEDIA = {
  ".html": "text/html",
  ".htm": "text/html",
  ".css": "text/css",
  ".js": "text/javascript",
  ".mjs": "text/javascript",
  ".svg": "image/svg+xml",
  ".json": "application/json",
  ".txt": "text/plain",
  ".md": "text/markdown",
  ".png": "image/png",
  ".webm": "video/webm",
  ".mp4": "video/mp4",
  ".woff2": "font/woff2",
};

const PERMISSIONS = (
  "accelerometer autoplay camera clipboard-read clipboard-write cross-origin-isolated " +
  "display-capture encrypted-media fullscreen geolocation gyroscope hid idle-detection " +
  "magnetometer microphone midi payment picture-in-picture publickey-credentials-get " +
  "screen-wake-lock serial usb xr-spatial-tracking"
)
  .split(" ")
  .map((feature) => `${feature}=()`)
  .join(", ");

const BASE_HEADERS = {
  "Cross-Origin-Opener-Policy": "same-origin",
  "Permissions-Policy": PERMISSIONS,
  "Referrer-Policy": "no-referrer",
  "X-Content-Type-Options": "nosniff",
};

function documentHeaders(origin) {
  const allowed = `${origin}${PREFIX}`;
  const policy = [
    "sandbox allow-scripts",
    "default-src 'none'",
    `script-src ${allowed} 'unsafe-inline'`,
    `style-src ${allowed} 'unsafe-inline'`,
    `img-src ${allowed} data:`,
    `font-src ${allowed} data:`,
    `media-src ${allowed}`,
    `manifest-src ${allowed}`,
    "connect-src 'none'",
    "frame-src 'none'",
    "child-src 'none'",
    "worker-src 'none'",
    "object-src 'none'",
    "frame-ancestors 'self'",
    "base-uri 'none'",
    "form-action 'none'",
  ].join("; ");
  return {
    ...BASE_HEADERS,
    "Cross-Origin-Resource-Policy": "same-origin",
    "Content-Type": "text/html; charset=utf-8",
    "Content-Security-Policy": policy,
    "Cache-Control": "no-store",
  };
}

function assetHeaders(media) {
  const headers = {
    ...BASE_HEADERS,
    "Access-Control-Allow-Origin": "*",
    "Cross-Origin-Resource-Policy": "cross-origin",
    "Content-Type": media,
    "Cache-Control": "private, max-age=31536000, immutable",
  };
  if (media === "text/html" || media === "image/svg+xml") {
    headers["Content-Security-Policy"] = "default-src 'none'; sandbox";
  }
  return headers;
}

const flag = (name, fallback) => {
  const index = process.argv.indexOf(`--${name}`);
  return index > 0 && process.argv[index + 1]
    ? process.argv[index + 1]
    : fallback;
};

const directory = resolve(flag("dir", "."));
const entrypoint = flag("entry", "index.html");
const foreign = new Set();
const absent = new Set();
const loadedAssets = new Set();

function recordLoadedAsset(rawPath) {
  if (typeof rawPath !== "string") return;
  const cleaned = rawPath.split(/[?#]/, 1)[0].replace(/^\/+/, "");
  if (cleaned) loadedAssets.add(cleaned);
}

function answer(request, response) {
  const pathname = decodeURIComponent(
    new URL(request.url, "http://127.0.0.1").pathname,
  );
  const origin = `http://${request.headers.host}`;
  if (!pathname.startsWith(PREFIX)) {
    foreign.add(pathname);
    response.writeHead(404).end();
    return;
  }
  const rest = pathname.slice(PREFIX.length);
  const target = rest === "" || rest.endsWith("/") ? entrypoint : rest;
  const file = resolve(join(directory, target));
  if (!file.startsWith(directory + sep)) {
    foreign.add(pathname);
    response.writeHead(404).end();
    return;
  }
  return stat(file)
    .then((info) => {
      if (!info.isFile()) throw new Error("not a file");
      return readFile(file);
    })
    .then((body) => {
      const media =
        MEDIA[extname(file).toLowerCase()] || "application/octet-stream";
      recordLoadedAsset(target);
      response.writeHead(
        200,
        target === entrypoint ? documentHeaders(origin) : assetHeaders(media),
      );
      response.end(body);
    })
    .catch(() => {
      absent.add(pathname);
      response.writeHead(404).end();
    });
}

async function observe(page) {
  return page.evaluate(() => {
    const references = [...document.querySelectorAll("[data-evidence]")].map(
      (element) => (element.getAttribute("data-evidence") || "").trim(),
    );
    const claimIds = [
      ...document.querySelectorAll("[data-evidence-claim]"),
    ].map((element) => (element.getAttribute("data-evidence-claim") || "").trim());
    const directClaimIds = [
      ...document.querySelectorAll("[data-claim]"),
    ].map((element) => (element.getAttribute("data-claim") || "").trim());
    const allClaimIds = [...claimIds, ...directClaimIds];
    const unresolved = [...references, ...allClaimIds]
      .filter((reference) => !reference)
      .map(() => "(empty)");
    const body = document.body;
    return {
      unresolved,
      // Read the rendered DOM, keeping data-evidence basis references and claim IDs
      // in separate inventories for the Python caller to resolve against its packet.
      references: references.filter(Boolean),
      claimIds: allClaimIds.filter(Boolean),
      title: document.title,
      rendered: Boolean(
        body &&
        (body.children.length > 0 || body.textContent.trim().length > 0),
      ),
    };
  });
}

/** Measure painted information, keeping backgrounds and occluding overlays in place.
 * Presence, layout and computed CSS cannot establish that text or graphics reach the screen.
 * Suppressing their paint must change the rendered viewport. Empty boxes and a covering overlay
 * alone do not qualify. This observes pixels, not the explanation's quality.
 */
async function visibleContent(page) {
  const stable = await page.addStyleTag({
    content:
      "*,*::before,*::after{animation-play-state:paused!important;transition:none!important;caret-color:transparent!important}",
  });
  let erased;
  try {
    const painted = await page.screenshot();
    erased = await page.addStyleTag({
      content:
        "*,*::before,*::after{color:transparent!important;-webkit-text-fill-color:transparent!important;text-shadow:none!important;text-decoration-color:transparent!important}" +
        "img,svg,canvas,video,input,textarea,select{visibility:hidden!important}",
    });
    return !painted.equals(await page.screenshot());
  } finally {
    if (erased) await erased.evaluate((element) => element.remove());
    await stable.evaluate((element) => element.remove());
  }
}

/** Keep evidence and claim attributes in separate inventories through the load window. */
async function watchReferences(page) {
  return page.evaluateHandle(() => {
    const references = new Set();
    const claimIds = new Set();
    let hasEmpty = false;
    const collect = () => {
      for (const element of document.querySelectorAll("[data-evidence]")) {
        const value = (element.getAttribute("data-evidence") || "").trim();
        if (value) references.add(value);
        else hasEmpty = true;
      }
      for (const element of document.querySelectorAll(
        "[data-evidence-claim]",
      )) {
        const value = (element.getAttribute("data-evidence-claim") || "").trim();
        if (value) claimIds.add(value);
        else hasEmpty = true;
      }
      for (const element of document.querySelectorAll("[data-claim]")) {
        const value = (element.getAttribute("data-claim") || "").trim();
        if (value) claimIds.add(value);
        else hasEmpty = true;
      }
    };
    collect();
    const observer = new MutationObserver(collect);
    observer.observe(document, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ["data-evidence", "data-evidence-claim", "data-claim"],
    });
    return {
      finish() {
        collect();
        observer.disconnect();
        return {
          references: [...references],
          claimIds: [...claimIds],
          unresolved: hasEmpty ? ["(empty)"] : [],
        };
      },
    };
  });
}

async function main() {
  const server = createServer(
    (request, response) => void answer(request, response),
  );
  await new Promise((ready) => server.listen(0, "127.0.0.1", ready));
  try {
    return await observeInBrowser(`http://127.0.0.1:${server.address().port}`);
  } finally {
    server.close();
  }
}

async function observeInBrowser(origin) {
  const { chromium } = await import(PLAYWRIGHT).then((module) =>
    module.chromium ? module : module.default,
  );
  const browser = await chromium.launch({ args: ["--no-sandbox"] });
  const interactiveDirectory = flag("interactive-dir", null);
  if (interactiveDirectory) {
    // The earlier run of the same draft, when it left passes or controls unvisited (resumed).
    const resumeFile = flag("interactive-resume", null);
    const resume = resumeFile
      ? JSON.parse(await readFile(resumeFile, "utf8"))
      : null;
    try {
      return await interactiveReport(
        browser,
        `${origin}${PREFIX}`,
        interactiveDirectory,
        Number(flag("comparison", "0")),
        resume,
        { started: STARTED, limitSeconds: Number(flag("time-limit", "0")) },
      );
    } finally {
      await browser.close();
    }
  }
  const errors = [];
  const failed = [];
  const outside = new Set();
  const pushUniqueError = (entry) => {
    if (!errors.includes(entry)) errors.push(entry);
  };
  const attachListeners = (page) => {
    page.on("console", (message) => {
      if (message.type() === "error") pushUniqueError(`console: ${message.text()}`);
    });
    page.on("pageerror", (error) => pushUniqueError(`pageerror: ${error.message}`));
    page.on("requestfailed", (request) => {
      const entry = `${request.url()} ${request.failure()?.errorText ?? "failed"}`;
      if (!failed.includes(entry)) failed.push(entry);
    });
    page.on("request", (request) => {
      const url = request.url();
      if (!url.startsWith(`${origin}${PREFIX}`) && !url.startsWith("data:"))
        outside.add(url);
    });
    page.on("response", (response) => {
      const status = response.status();
      if (!((status >= 200 && status < 300) || status === 304)) return;
      const url = response.url();
      if (!url.startsWith(`${origin}${PREFIX}`)) return;
      try {
        const pathname = decodeURIComponent(new URL(url).pathname);
        if (!pathname.startsWith(PREFIX)) return;
        const rest = pathname.slice(PREFIX.length);
        const target = rest === "" || rest.endsWith("/") ? entrypoint : rest;
        recordLoadedAsset(target);
      } catch {
        // Ignore malformed response URLs.
      }
    });
  };
  try {
    const context = await browser.newContext({
      viewport: { width: 1280, height: 720 },
    });
    let initialSeen;
    let postSeen;
    let initialVisible;
    let watched = { references: [], claimIds: [], unresolved: [] };
    let domInventory;
    try {
      const page = await context.newPage();
      attachListeners(page);
      const response = await page.goto(`${origin}${PREFIX}`, {
        waitUntil: "load",
        timeout: 20000,
      });
      if (!response || !response.ok()) {
        pushUniqueError(
          `document answered ${response ? response.status() : "no response"}`,
        );
      }
      const references = await watchReferences(page);
      const observeStart = Date.now();
      // Measure initial rendered eligibility, DOM inventory, and citations BEFORE any synthetic control clicks.
      await page.waitForTimeout(150);
      initialSeen = await observe(page);
      initialVisible = await visibleContent(page);
      domInventory = await collectDomInventory(page);
      const screenshotIndex = process.argv.indexOf("--screenshot");
      if (screenshotIndex !== -1)
        await page.screenshot({ path: process.argv[screenshotIndex + 1] });
      // Keep the bounded post-load reference observation window at least 2000 ms for delayed JS.
      const remaining = 2000 - (Date.now() - observeStart);
      if (remaining > 0) await page.waitForTimeout(remaining);
      postSeen = await observe(page);
      watched = await references.evaluate((watch) => watch.finish());
      await references.dispose();
    } finally {
      await context.close();
    }

    const evidenceReferences = [
      ...new Set([
        ...initialSeen.references,
        ...postSeen.references,
        ...watched.references,
      ]),
    ];
    const claimIds = [
      ...new Set([
        ...initialSeen.claimIds,
        ...postSeen.claimIds,
        ...watched.claimIds,
      ]),
    ];
    const unresolvedReferences = [
      ...new Set([
        ...initialSeen.unresolved,
        ...postSeen.unresolved,
        ...watched.unresolved,
      ]),
    ];
    const foreignRequests = [...new Set([...foreign, ...outside])];
    const visibleContentOk = Boolean(initialVisible);
    const faults = [
      errors.length ? `${errors.length} console or load error(s)` : "",
      foreignRequests.length
        ? `${foreignRequests.length} request(s) outside the revision`
        : "",
      absent.size ? `${absent.size} asset(s) the revision does not carry` : "",
      unresolvedReferences.length
        ? `${unresolvedReferences.length} unresolved evidence reference(s)`
        : "",
      initialSeen.rendered ? "" : "the document rendered no body",
      visibleContentOk
        ? ""
        : "the document painted no visible text, controls, or graphics",
    ].filter(Boolean);
    return {
      state: faults.length ? "failed" : "loaded",
      kind: "browser",
      errors: [
        ...errors,
        ...failed,
        ...[...absent].map((path) => `${path} was requested but not served`),
      ],
      foreignRequests,
      visibleContentOk,
      unresolvedReferences,
      evidenceReferences,
      claimIds,
      loadedAssets: [...loadedAssets].sort(),
      domInventory,
      detail: `${faults.length ? `faults: ${faults.join(", ")}` : "no fault observed"}; page title ${JSON.stringify(
        initialSeen.title,
      )}`,
      note: "served from a loopback static server with the policy headers; references observed through a bounded post-load window; visible text, controls or graphics checked by paint suppression before synthetic interactions",
    };
  } finally {
    await browser.close();
  }
}

/** Write one answer whole and leave: a stray handle (a driver child, a socket) must never hang the
 * caller. One writeSync to a pipe writes at most what the pipe holds (64 KiB here), so the answer is
 * written until every byte is out; exiting after a partial write cut the report mid-JSON. */
function answerAndExit(payload, code) {
  const bytes = Buffer.from(`${JSON.stringify(payload)}\n`);
  let written = 0;
  while (written < bytes.length) {
    try {
      written += writeSync(1, bytes, written);
    } catch (error) {
      if (error.code !== "EAGAIN") throw error;
    }
  }
  process.exit(code);
}

main()
  .then((report) => answerAndExit(report, 0))
  .catch((error) =>
    answerAndExit(
      {
        state: "unavailable",
        kind: "browser",
        errors: [`the browser load could not run: ${error.message}`],
        detail: "no browser observed this page",
      },
      1,
    ),
  );
