#!/usr/bin/env node
/**
 * Load one prospective revision in Chromium without a model (MIK-R104 rule 14).
 *
 * `--dir <staged assets> --entry <entrypoint>`: this serves ONLY that directory from a random
 * loopback port, opens it with `prefers-reduced-motion: reduce` in force, and prints one JSON
 * object. It is not a `file://` load: a file URL answers with no headers at all, so the CSP, the
 * sandbox and the CORP/CORS arrangement the page really executes under would be untested. The
 * headers below mirror `mcp/src/agents_remember/canvas/policy.py` (scripts enabled, the shipped v2
 * arrangement under D331); asset paths live under the same `/canvas/page/` prefix the page is
 * served from, so the allowlist is the same expression it is in production.
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
      (element) => element.getAttribute("data-evidence").trim(),
    );
    const claimIds = [
      ...document.querySelectorAll("[data-evidence-claim]"),
    ].map((element) => element.getAttribute("data-evidence-claim").trim());
    const unresolved = [...references, ...claimIds]
      .filter((reference) => !reference)
      .map(() => "(empty)");
    const playing = [...document.querySelectorAll("audio, video")].filter(
      (media) => !media.paused && !media.ended,
    ).length;
    const body = document.body;
    return {
      unresolved,
      // Read the rendered DOM, keeping data-evidence basis references and data-evidence-claim
      // claim IDs in separate inventories for the Python caller to resolve against its packet.
      references: references.filter(Boolean),
      claimIds: claimIds.filter(Boolean),
      playing,
      reduce: matchMedia("(prefers-reduced-motion: reduce)").matches,
      animations: document.getAnimations ? document.getAnimations().length : 0,
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
 * Suppressing their paint must change the actual reduced-motion viewport. Empty boxes and a
 * covering overlay alone do not qualify. This observes pixels, not the explanation's quality.
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

/** Keep both reference attributes in separate inventories through the load window. */
async function watchReferences(page) {
  return page.evaluateHandle(() => {
    const references = new Set();
    const claimIds = new Set();
    const collect = () => {
      for (const element of document.querySelectorAll("[data-evidence]")) {
        const value = element.getAttribute("data-evidence").trim();
        if (value) references.add(value);
      }
      for (const element of document.querySelectorAll(
        "[data-evidence-claim]",
      )) {
        const value = element.getAttribute("data-evidence-claim").trim();
        if (value) claimIds.add(value);
      }
    };
    collect();
    const observer = new MutationObserver(collect);
    observer.observe(document, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ["data-evidence", "data-evidence-claim"],
    });
    return {
      finish() {
        collect();
        observer.disconnect();
        return {
          references: [...references],
          claimIds: [...claimIds],
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
  try {
    const context = await browser.newContext({ reducedMotion: "reduce" });
    const page = await context.newPage();
    page.on("console", (message) => {
      if (message.type() === "error") errors.push(`console: ${message.text()}`);
    });
    page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
    page.on("requestfailed", (request) => {
      failed.push(
        `${request.url()} ${request.failure()?.errorText ?? "failed"}`,
      );
    });
    page.on("request", (request) => {
      const url = request.url();
      if (!url.startsWith(`${origin}${PREFIX}`) && !url.startsWith("data:"))
        outside.add(url);
    });
    const response = await page.goto(`${origin}${PREFIX}`, {
      waitUntil: "load",
      timeout: 20000,
    });
    if (!response || !response.ok()) {
      errors.push(
        `document answered ${response ? response.status() : "no response"}`,
      );
    }
    const references = await watchReferences(page);
    // One bounded load observation, including delayed DOM work; no claim about arbitrary future JS.
    await page.waitForTimeout(2000);
    const seen = await observe(page);
    const watched = await references.evaluate((watch) => watch.finish());
    seen.references = watched.references;
    seen.claimIds = watched.claimIds;
    await references.dispose();
    seen.visible = await visibleContent(page);
    const screenshotIndex = process.argv.indexOf("--screenshot");
    if (screenshotIndex !== -1)
      await page.screenshot({ path: process.argv[screenshotIndex + 1] });
    const foreignRequests = [...new Set([...foreign, ...outside])];
    // Reduced motion asks for a page a person can use: no media playing, and something visible.
    const reducedMotionOk = seen.reduce && seen.playing === 0 && seen.visible;
    const faults = [
      errors.length ? `${errors.length} console or load error(s)` : "",
      foreignRequests.length
        ? `${foreignRequests.length} request(s) outside the revision`
        : "",
      absent.size ? `${absent.size} asset(s) the revision does not carry` : "",
      seen.unresolved.length
        ? `${seen.unresolved.length} unresolved evidence reference(s)`
        : "",
      seen.rendered ? "" : "the document rendered no body",
      reducedMotionOk ? "" : "the page is not usable with reduced motion",
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
      reducedMotionOk,
      unresolvedReferences: seen.unresolved,
      evidenceReferences: seen.references,
      claimIds: seen.claimIds,
      detail: `${faults.length ? `faults: ${faults.join(", ")}` : "no fault observed"}; page title ${JSON.stringify(
        seen.title,
      )}, ${seen.animations} running animation(s) under reduce`,
      note: "served from a loopback static server with the policy headers; reduced motion set on the browser context; references observed through a 2000 ms post-load window; visible text, controls or graphics checked by paint suppression",
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
