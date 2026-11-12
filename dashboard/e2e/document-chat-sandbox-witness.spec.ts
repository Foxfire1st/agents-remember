// MIK-R121 rule 1 scripted witness (rulings faa07 + 49138e34 points 2-6): dashboard chat
// end-to-end suite leg.
//
// Drives the REAL sandbox dashboard in Chromium against a REAL sandbox (BASELINE first,
// FINAL only if the baseline fails under the corrected verdict), same browser context for
// a dashboard page and a frame page (shared profile storage). Narrow and wide run as two
// Playwright projects of one spec file.
//
// Recorder (ruling point 2, non-invasive, no product change): ONE init script installed
// BEFORE page scripts on the whole context wraps the "message" listeners on every page
// into window.__arRec from the page's load on. It records every message as RECEIVED with
// its time: on the dashboard page anything with source "ar-plugin" (ready, selection,
// hierarchy, shown, error, pong); on the frame page anything with an "ar." type (ar.open,
// ar.ping) — in practice the frame page never receives any (ar.open goes to the
// dashboard's iframe, not to another top-level page; channelFrame stays empty by
// construction, stated not hidden). The outgoing postMessage call itself cannot be wrapped
// without touching product code (the dashboard calls it on the cross-origin iframe's
// contentWindow, whose properties are unwritable from the page); the received open is
// recorded exactly one other way: the dashboard iframe's src transitions (its query
// carries ?open=agent:<id>&arPage=document), polled with timestamps. During every wait the
// spec samples every 250 ms: the dashboard iframe element's bounds/display, the pane state
// (Chat active, launcher, problem, connecting overlay), and inside the frame each condition
// of the sign (route workspace, store focus per exact key, visible selected strip tabs,
// scroll-node bounds, loading/not-found presence). The trace JSON is flushed after every leg
// and in a finally, so a stopped run still writes its trace; files are tagged per run and
// never overwritten.
//
// Verdict (ruling point 4): what a person sees in the dashboard pane — the Chat tab active,
// the frame element laid out with room, no launcher, no problem text, no connecting overlay —
// read in the dashboard's own page, alongside shown for that agent. Direct frame reads
// (restored title/word/store) are the faa07 point-7 identity control, recorded, never the
// verdict.
//
// Legs: zero-size frame explanation FIRST (iframe room at CHATS / OPERATIONS-idle /
// master+Chat states, no agent start); master (manager); sprint REQUIRED (launcher by the
// binding rule, cause source-read in the delivery); leaf L1 (reviewer); two agents each
// direction twice (frame opens); master again; A-while-B control in faithful form.
// EXPECT tags the arm (fail=baseline sandbox, pass=final sandbox) for the evidence files.
//
// Env (all required): AR_WITNESS_DASH, AR_WITNESS_PASEO, AR_WITNESS_EXPECT=fail|pass,
// AR_WITNESS_EVIDENCE (dir), AR_WITNESS_IDENTITY (JSON with server, masterWs, leafWs,
// manager/worker/reviewer full agent UUIDs, workerWord, reviewerWord, managerWord).
import { expect, test, type BrowserContext, type Page } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const DASH = process.env.AR_WITNESS_DASH ?? "";
const PASEO = process.env.AR_WITNESS_PASEO ?? "";
const EXPECT = process.env.AR_WITNESS_EXPECT ?? "";
const EVIDENCE = process.env.AR_WITNESS_EVIDENCE ?? "";
const IDENTITY = JSON.parse(process.env.AR_WITNESS_IDENTITY ?? "{}") as {
  server: string;
  masterWs: string;
  leafWs: string;
  manager: string;
  worker: string;
  reviewer: string;
  workerWord: string;
  reviewerWord: string;
  managerWord: string;
};

type RecRow = { t: number; side: string; dir: string; type: string; agentId?: string };
type Sample = { t: number; leg: string; dash: string; frame: string };

async function installRecorder(context: BrowserContext): Promise<void> {
  await context.addInitScript(() => {
    const w = window as unknown as Record<string, unknown>;
    if (w.__arRecInstalled) return;
    w.__arRecInstalled = true;
    w.__arRec = [] as RecRow[];
    const push = (data: unknown) => {
      const d = data as Record<string, unknown>;
      (w.__arRec as RecRow[]).push({
        t: Date.now(),
        side: window.location.origin,
        dir: "in",
        type: String(d?.type ?? "?"),
        agentId: typeof d?.agentId === "string" ? (d.agentId as string) : undefined,
      });
    };
    const rawAdd = window.addEventListener.bind(window);
    (window as unknown as { addEventListener: unknown }).addEventListener = (
      type: string,
      listener: unknown,
      ...rest: unknown[]
    ) => {
      if (type === "message" && typeof listener === "function") {
        const wrapped = (event: unknown) => {
          const m = (event as { data?: unknown })?.data as Record<string, unknown>;
          if (m && typeof m === "object") {
            if (m.source === "ar-plugin") push(m);
            else if (typeof m.type === "string" && (m.type as string).startsWith("ar.")) push(m);
          }
          return (listener as (ev: unknown) => void)(event);
        };
        return (rawAdd as (...a: unknown[]) => void)(type, wrapped, ...rest);
      }
      return (rawAdd as (...a: unknown[]) => void)(type, listener, ...rest);
    };
  });
}

async function recOf(page: Page): Promise<RecRow[]> {
  return page.evaluate(() => (window as unknown as { __arRec?: RecRow[] }).__arRec ?? []);
}

async function shownSince(page: Page, agentId: string, since: number): Promise<boolean> {
  const rows = await recOf(page);
  return rows.some((r) => r.type === "shown" && r.agentId === agentId && r.t >= since);
}

async function waitVisibleText(page: Page, text: string): Promise<void> {
  await expect
    .poll(
      async () =>
        page.evaluate((wanted: string) => {
          const els = [...document.querySelectorAll("*")].filter(
            (e) => e.children.length === 0 && ((e as HTMLElement).innerText || "").trim() === wanted,
          );
          return els.some((e) => {
            const r = e.getBoundingClientRect();
            return r.width > 0 && r.height > 0;
          });
        }, text),
      { timeout: 60_000 },
    )
    .toBe(true);
}

async function clickVisibleText(page: Page, text: string): Promise<void> {
  await waitVisibleText(page, text);
  await page.evaluate((wanted: string) => {
    const els = [...document.querySelectorAll("*")].filter(
      (e) => e.children.length === 0 && ((e as HTMLElement).innerText || "").trim() === wanted,
    );
    const vis = els.find((e) => {
      const r = e.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    });
    if (!vis) throw new Error(`no visible text: ${wanted}`);
    (vis as HTMLElement).click();
  }, text);
}

async function openOperations(page: Page): Promise<void> {
  const radio = page.getByRole("radio", { name: "Operations" });
  if ((await radio.count()) > 0) {
    await radio.first().click();
    return;
  }
  await clickVisibleText(page, "OPERATIONS");
}

async function selectTask(page: Page, name: string): Promise<void> {
  await clickVisibleText(page, name);
}

async function openChatTab(page: Page): Promise<void> {
  await clickVisibleText(page, "Chat");
}

async function paneVerdict(page: Page): Promise<{
  chatTab: boolean; iframeLaidOut: boolean; launcher: boolean; problem: boolean;
  connecting: boolean; ok: boolean;
}> {
  const v = await page.evaluate(() => {
    const text = document.body.innerText ?? "";
    const chatHits = [...document.querySelectorAll("*")].filter(
      (e) => e.children.length === 0 && ((e as HTMLElement).innerText || "").trim() === "Chat",
    );
    const chatTab = chatHits.some((e) => {
      const r = e.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    });
    const frames = [...document.querySelectorAll("iframe")].map((f) => {
      const r = f.getBoundingClientRect();
      const cs = window.getComputedStyle(f);
      return { w: Math.round(r.width), h: Math.round(r.height), display: cs.display };
    });
    return {
      chatTab,
      frames,
      launcher: text.includes("ROLE") && text.includes("AGENT OVERRIDE"),
      problem: text.includes("did not show"),
      connecting: text.includes("Connecting embedded chat"),
    };
  });
  const iframeLaidOut = v.frames.some((f) => f.w > 0 && f.h > 0);
  const ok = v.chatTab && iframeLaidOut && !v.launcher && !v.problem && !v.connecting;
  return { chatTab: v.chatTab, iframeLaidOut, launcher: v.launcher, problem: v.problem, connecting: v.connecting, ok };
}

async function frameSrcs(page: Page): Promise<string[]> {
  return page.locator("iframe").evaluateAll((frames) =>
    frames.map((f) => (f as HTMLIFrameElement).src ?? ""),
  );
}

async function frameSign(frame: Page, wsHint: string): Promise<string> {
  return frame.evaluate((ws: string) => {
    try {
      const path = location.pathname.split("/");
      const routeWs = path.length >= 5 && path[1] === "h" && path[3] === "workspace" ? path[4] : "?";
      let focused = "absent";
      let keys = "";
      try {
        const raw = localStorage.getItem("workspace-layout-state");
        if (raw) {
          const d = (JSON.parse(raw).state.layoutByWorkspace ?? {}) as Record<string, {
            root: unknown; focusedPaneId: string;
          }>;
          keys = Object.keys(d).join(",");
          const layout = (d as Record<string, unknown>)[ws] as { root: unknown; focusedPaneId: string } | undefined;
          if (layout) {
            const panes: { id: string; focusedTabId: string }[] = [];
            const walk = (n: unknown): void => {
              const r = n as { kind?: string; pane?: { id: string; focusedTabId: string }; group?: { children: unknown[] } };
              if (!r || typeof r !== "object") return;
              if (r.kind === "pane" && r.pane) panes.push(r.pane);
              if (r.kind === "group") (r.group?.children ?? []).forEach(walk);
            };
            walk(layout.root);
            const pane = panes.find((p) => p.id === layout.focusedPaneId);
            focused = pane?.focusedTabId ?? "no-pane";
          } else focused = "no-key";
        }
      } catch {
        focused = "unparsable";
      }
      const tabs = [...document.querySelectorAll('[data-testid^="workspace-tab-agent_"]')]
        .filter((e) => {
          const r = (e as HTMLElement).getBoundingClientRect();
          return r.width > 0 && r.height > 0;
        })
        .map((e) => `${e.getAttribute("data-testid")}=${e.getAttribute("aria-selected")}`);
      const scrolls = [...document.querySelectorAll('[id],[data-testid]')]
        .filter((e) => {
          const id = e.getAttribute("id") || "";
          const tid = e.getAttribute("data-testid") || "";
          return id.startsWith("agent-chat-scroll-") || tid.startsWith("agent-chat-scroll-");
        })
        .map((e) => {
          const r = (e as HTMLElement).getBoundingClientRect();
          return `${e.getAttribute("id") || e.getAttribute("data-testid")}:${Math.round(r.width)}x${Math.round(r.height)}`;
        });
      const mark = (sel: string) =>
        [...document.querySelectorAll(sel)]
          .map((e) => {
            const r = (e as HTMLElement).getBoundingClientRect();
            return `${Math.round(r.width)}x${Math.round(r.height)}`;
          })
          .join(",") || "absent";
      return JSON.stringify({
        routeWs, focused, keys: keys.slice(-80), tabs, scrolls,
        loading: mark('[id="agent-loading"],[data-testid="agent-loading"]'),
        notfound: mark('[id="agent-not-found"],[data-testid="agent-not-found"]'),
        trigger: mark('[data-testid="workspace-tab-switcher-trigger"]'),
        inner: `${window.innerWidth}x${window.innerHeight}`,
      });
    } catch {
      return JSON.stringify({ frameSignError: true });
    }
  }, `${IDENTITY.server}:${wsHint}`);
}

async function frameControl(frame: Page, wsKey: string, wsHint: string): Promise<{ focused: string; title: string }> {
  await frame.goto(`${PASEO}/h/${IDENTITY.server}/workspace/${wsHint}`);
  try {
    await expect.poll(async () => frame.title(), { timeout: 30_000 }).not.toContain("Paseo");
  } catch {
    // Recorded as-is below; the title names the outcome.
  }
  const focused = await frame.evaluate((ws: string) => {
    try {
      const raw = localStorage.getItem("workspace-layout-state");
      if (!raw) return "absent";
      const d = JSON.parse(raw).state.layoutByWorkspace as Record<string, { root: unknown; focusedPaneId: string }>;
      const layout = d[ws];
      if (!layout) return "no-key";
      const panes: { id: string; focusedTabId: string }[] = [];
      const walk = (n: unknown): void => {
        const r = n as { kind?: string; pane?: { id: string; focusedTabId: string }; group?: { children: unknown[] } };
        if (!r || typeof r !== "object") return;
        if (r.kind === "pane" && r.pane) panes.push(r.pane);
        if (r.kind === "group") (r.group?.children ?? []).forEach(walk);
      };
      walk(layout.root);
      return panes.find((p) => p.id === layout.focusedPaneId)?.focusedTabId ?? "no-pane";
    } catch {
      return "unparsable";
    }
  }, wsKey);
  return { focused, title: await frame.title().catch(() => "untitled") };
}

test.describe("MIK-R121 sandbox witness", () => {
  test("selection matrix with message trace", async ({ page, context }) => {
    test.setTimeout(900_000);
    if (!DASH || !PASEO || !EVIDENCE || !IDENTITY.server) throw new Error("witness env missing");
    mkdirSync(EVIDENCE, { recursive: true });
    const stamp = new Date().toISOString().slice(11, 19).replace(/:/g, "");
    const tag = `${EXPECT === "pass" ? "final" : "base"}-${test.info().project.name}-${stamp}`;
    const shot = (name: string) => page.screenshot({ path: join(EVIDENCE, `${tag}-${name}.png`) });
    const legs: { leg: string; at: number; note: string }[] = [];
    const samples: Sample[] = [];
    const srcSeen = new Set<string>();
    const note = (leg: string, noteText: string) => trace.push2(leg, noteText);
    const trace = {
      rows: legs as { leg: string; at: number; note: string }[],
      push2(leg: string, noteText: string) {
        this.rows.push({ leg, at: Date.now(), note: noteText });
      },
    };
    const frame = await context.newPage();
    const flushTrace = async () => {
      writeFileSync(
        join(EVIDENCE, `${tag}-trace.json`),
        JSON.stringify({ tag, expect: EXPECT, at: new Date().toISOString(), legs: trace.rows,
          samples: samples.slice(-400),
          channelDashboard: await recOf(page).catch(() => []),
          channelFrame: await recOf(frame).catch(() => []) }, null, 1),
      );
    };
    const sample = async (leg: string, wsHint: string): Promise<void> => {
      const dash = await page.evaluate(() => {
        const text = document.body.innerText ?? "";
        const frames = [...document.querySelectorAll("iframe")].map((f) => {
          const r = f.getBoundingClientRect();
          return `${Math.round(r.width)}x${Math.round(r.height)}/${window.getComputedStyle(f).display}`;
        });
        return JSON.stringify({ frames, chat: text.includes("Chat"), launcher: text.includes("ROLE"), problem: text.includes("did not show"), connecting: text.includes("Connecting embedded chat") });
      }).catch(() => "dash-unreadable");
      // The sign inputs as the bridge itself sees them: evaluated inside the dashboard's
      // app iframe (CDP reaches cross-origin frames; plain DOM access could not).
      const frDirect = await (async () => {
        try {
          const host = new URL(PASEO).hostname;
          const port = new URL(PASEO).port;
          const app = page.frame({ url: new RegExp(`${host}:${port}`) });
          if (!app) return "no-app-frame";
          return await app.evaluate((ws: string) => {
            try {
              const segs = location.pathname.split("/");
              const routeWs = segs.length >= 5 && segs[1] === "h" && segs[3] === "workspace" ? segs[4] : "?";
              let focused = "absent";
              try {
                const raw = localStorage.getItem("workspace-layout-state");
                if (raw) {
                  const d = (JSON.parse(raw).state.layoutByWorkspace ?? {}) as Record<string, { root: unknown; focusedPaneId: string }>;
                  const layout = d[ws] as { root: unknown; focusedPaneId: string } | undefined;
                  if (layout) {
                    const panes: { id: string; focusedTabId: string }[] = [];
                    const walk = (n: unknown): void => {
                      const r = n as { kind?: string; pane?: { id: string; focusedTabId: string }; group?: { children: unknown[] } };
                      if (!r || typeof r !== "object") return;
                      if (r.kind === "pane" && r.pane) panes.push(r.pane);
                      if (r.kind === "group") (r.group?.children ?? []).forEach(walk);
                    };
                    walk(layout.root);
                    focused = panes.find((p) => p.id === layout.focusedPaneId)?.focusedTabId ?? "no-pane";
                  } else focused = "no-key";
                }
              } catch { focused = "unparsable"; }
              const tabs = [...document.querySelectorAll('[data-testid^="workspace-tab-agent_"]')]
                .filter((e) => { const r = (e as HTMLElement).getBoundingClientRect(); return r.width > 0 && r.height > 0; })
                .map((e) => `${e.getAttribute("data-testid")}=${e.getAttribute("aria-selected")}`);
              const scrolls = [...document.querySelectorAll("[id],[data-testid]")]
                .filter((e) => {
                  const id = e.getAttribute("id") || "";
                  const tid = e.getAttribute("data-testid") || "";
                  return id.startsWith("agent-chat-scroll-") || tid.startsWith("agent-chat-scroll-");
                })
                .map((e) => {
                  const r = (e as HTMLElement).getBoundingClientRect();
                  return `${e.getAttribute("id") || e.getAttribute("data-testid")}:${Math.round(r.width)}x${Math.round(r.height)}`;
                });
              const vis = (sel: string) =>
                [...document.querySelectorAll(sel)].map((e) => {
                  const r = (e as HTMLElement).getBoundingClientRect();
                  return `${Math.round(r.width)}x${Math.round(r.height)}`;
                }).join(",") || "absent";
              return JSON.stringify({ routeWs, focused, tabs,
                scrolls, loading: vis('[id="agent-loading"],[data-testid="agent-loading"]'),
                inner: `${window.innerWidth}x${window.innerHeight}` });
            } catch {
              return JSON.stringify({ iframeEvaluateError: true });
            }
          }, `${IDENTITY.server}:${wsHint}`);
        } catch { return "iframe-unreadable"; }
      })();
      const fr = await frameSign(frame, wsHint).catch(() => "frame-unreadable");
      for (const src of await frameSrcs(page).catch((): string[] => [])) {
        if (src && !srcSeen.has(src)) {
          srcSeen.add(src);
          samples.push({ t: Date.now(), leg, dash: `iframe-src-new:${src.slice(-100)}`, frame: "" });
        }
      }
      samples.push({ t: Date.now(), leg, dash, frame: `${fr} || direct:${frDirect}` });
      if (samples.length % 40 === 0) await flushTrace().catch(() => {});
    };
    const waitShown = async (leg: string, wsHint: string, agentId: string, timeoutMs: number, since: number): Promise<boolean> => {
      // Never throws: a destroyed context mid-wait (dashboard navigating under us) reads
      // as not-yet-shown and the loop keeps polling until the timeout.
      for (;;) {
        try {
          await sample(leg, wsHint);
          if (await shownSince(page, agentId, since)) return true;
        } catch { /* keep polling */ }
        if (Date.now() - since > timeoutMs) return false;
        await page.waitForTimeout(250).catch(() => {});
      }
    };
    const deadVerdict = { chatTab: false, iframeLaidOut: false, launcher: false, problem: false, connecting: false, ok: false };
    const safeVerdict = (p: Promise<{ chatTab: boolean; iframeLaidOut: boolean; launcher: boolean; problem: boolean; connecting: boolean; ok: boolean }>) =>
      p.catch(() => deadVerdict);
    const safeControl = (p: Promise<{ focused: string; title: string }>) =>
      p.catch(() => ({ focused: "unreadable", title: "unreadable" }));
    const mark = (leg: string, action: string) => {
      samples.push({ t: Date.now(), leg, dash: `mark:${action}`, frame: "" });
    };
    const withRetry = async (action: string, fn: () => Promise<void>): Promise<void> => {
      // The dashboard page can navigate under a click (boot settling); retry the whole
      // action instead of failing on a destroyed context.
      for (let attempt = 1; ; attempt += 1) {
        try {
          await fn();
          return;
        } catch (e) {
          const msg = String(e);
          if (attempt >= 3 || !/destroyed|navigation|closed/i.test(msg)) throw e;
          mark("retry", `${action}#${attempt}`);
          await page.waitForTimeout(2_000);
        }
      }
    };
    const doSelect = async (leg: string, name: string) => {
      mark(leg, `select:${name.slice(0, 24)}`);
      await withRetry(`select:${name.slice(0, 24)}`, async () => selectTask(page, name));
    };
    const doChat = async (leg: string) => {
      mark(leg, "chat-tab");
      await withRetry("chat-tab", async () => openChatTab(page));
    };
    const failures: string[] = [];
    const check = async (leg: string, fn: () => Promise<void>): Promise<void> => {
      try {
        await fn();
      } catch (e) {
        failures.push(leg + ": " + String(e).split(String.fromCharCode(10))[0].slice(0, 200));
      }
    };
    try {
      await installRecorder(context);
      // Frame pre-entry: a cold frame page shares the profile but has no app state; the
      // plain-route load lets sample-time frame reads observe the live shared store.
      await frame.goto(`${PASEO}/h/${IDENTITY.server}/workspace/${IDENTITY.masterWs}`);
      await frame.waitForTimeout(5_000);
      await page.goto(DASH);
      // Boot settle: the dashboard may still be navigating when goto resolves; wait for a
      // boot marker before any bare evaluate (room reads have no poll of their own).
      await page.waitForLoadState("networkidle").catch(() => {});
      await expect.poll(async () => page.evaluate(() => {
        const text = document.body.innerText ?? "";
        return text.includes("OPERATIONS") || text.includes("CHATS");
      }).catch(() => false), { timeout: 60_000 }).toBe(true);

      // Leg 0: zero-size frame explanation FIRST (no agent start). Lists EVERY iframe
      // with its src tail and rect per state, so a 0x0 first frame cannot mislead.
      const allFrames = () => page.evaluate(() =>
        [...document.querySelectorAll("iframe")].map((f) => {
          const r = f.getBoundingClientRect();
          const src = (f as HTMLIFrameElement).src ?? "";
          return `${Math.round(r.width)}x${Math.round(r.height)}/${window.getComputedStyle(f).display}/${src.slice(-70)}`;
        }).join(" | ") || "no-iframe"
      ).catch(() => "unreadable");
      await openOperations(page).catch(() => {});
      const room0: Record<string, string> = {};
      room0.chats = await allFrames();
      await openOperations(page);
      await page.waitForTimeout(3_000);
      room0.opsIdle = await allFrames();
      // No selection here: the first selection of each leg below must post a fresh open.
      // Selected-state room is sampled during the legs themselves.
      room0.selected = "deferred-to-leg-samples";
      room0.frameInner = await frame.evaluate(() => `${window.innerWidth}x${window.innerHeight}`).catch(() => "unreadable");
      room0.frameSign = await frameSign(frame, IDENTITY.masterWs).catch(() => "unreadable");
      room0.pane = (await page.evaluate(() => document.body.innerText ?? "").catch(() => "")).slice(-220);
      note("room", `chats=${room0.chats} opsIdle=${room0.opsIdle} selected=${room0.selected} frameInner=${room0.frameInner} sign=${(room0.frameSign ?? "").slice(0, 300)} paneTail=${room0.pane}`);
      await shot("room");
      await flushTrace();

      // Leg 1: master -> manager chat. Verdict: pane + shown.
      // Decoy first: a repeat identical selection posts no fresh open, so force one.
      await doSelect("master-decoy", "SBX-M1-L1. Add slugify() and its test");
      const t1 = Date.now();
      await doSelect("master", "Sandbox master: text helpers for the sandbox app");
      await doChat("master");
      const got1 = await waitShown("master", IDENTITY.masterWs, IDENTITY.manager, 90_000, t1);
      const v1 = await safeVerdict(paneVerdict(page));
      const c1 = await safeControl(frameControl(frame, `${IDENTITY.server}:${IDENTITY.masterWs}`, IDENTITY.masterWs));
      note("master", `shown=${got1} pane=${JSON.stringify(v1)} controlTitle=${c1.title} controlFocus=${c1.focused}`);
      await shot("master");
      await flushTrace();
      await check("master", async () => {
        expect(got1 && v1.ok, `master leg verdict (shown=${got1} pane=${JSON.stringify(v1)})`).toBe(true);
      });

      // Leg 2: sprint REQUIRED (launcher by the binding rule). Polls for the launcher
      // with 250ms samples so selection-to-launcher-visible time is measured, not slept.
      const tSprint = Date.now();
      await doSelect("sprint", "Sandbox sprint: text helpers");
      await doChat("sprint");
      let launcherAt = 0;
      for (;;) {
        await sample("sprint", IDENTITY.masterWs);
        const txt = await page.evaluate(() => document.body.innerText ?? "").catch(() => "");
        if (txt.includes("ROLE") && txt.includes("AGENT OVERRIDE")) { launcherAt = Date.now(); break; }
        if (Date.now() - tSprint > 15_000) break;
        await page.waitForTimeout(250);
      }
      const v2 = await safeVerdict(paneVerdict(page));
      const sprintPane = await page.evaluate(() => document.body.innerText ?? "");
      await check("sprint", async () => {
        expect(v2.launcher, "sprint pane shows the launcher").toBe(true);
        expect(sprintPane, "sprint pane without problem text").not.toContain("did not show");
      });
      note("sprint", `launcher=${v2.launcher} sel2launcher=${launcherAt ? ((launcherAt - tSprint) / 1000).toFixed(1) + "s" : "not-observed"} pane=${JSON.stringify(v2)}`);
      await shot("sprint");
      await flushTrace();

      // Leg 3: leaf -> reviewer chat.
      await doSelect("leaf-decoy", "Sandbox master: text helpers for the sandbox app");
      const t3 = Date.now();
      await doSelect("leaf", "SBX-M1-L1. Add slugify() and its test");
      await doChat("leaf");
      const got3 = await waitShown("leaf", IDENTITY.leafWs, IDENTITY.reviewer, 90_000, t3);
      const v3 = await safeVerdict(paneVerdict(page));
      const c3 = await safeControl(frameControl(frame, `${IDENTITY.server}:${IDENTITY.leafWs}`, IDENTITY.leafWs));
      note("leaf", `shown=${got3} pane=${JSON.stringify(v3)} controlTitle=${c3.title} controlFocus=${c3.focused}`);
      await shot("leaf");
      await flushTrace();
      await check("leaf", async () => {
        expect(got3 && v3.ok, `leaf leg verdict (shown=${got3} pane=${JSON.stringify(v3)})`).toBe(true);
      });

      // Legs 4-7: two agents of one workspace, each direction twice (frame opens).
      await frame.goto(`${PASEO}/h/${IDENTITY.server}/workspace/${IDENTITY.leafWs}`);
      await frame.waitForTimeout(8_000);
      const wsKey = `${IDENTITY.server}:${IDENTITY.leafWs}`;
      const opens: Array<{ to: "worker" | "reviewer"; id: string; word: string }> = [
        { to: "worker", id: IDENTITY.worker, word: IDENTITY.workerWord },
        { to: "reviewer", id: IDENTITY.reviewer, word: IDENTITY.reviewerWord },
        { to: "worker", id: IDENTITY.worker, word: IDENTITY.workerWord },
        { to: "reviewer", id: IDENTITY.reviewer, word: IDENTITY.reviewerWord },
      ];
      let n = 0;
      for (const open of opens) {
        n += 1;
        const at = Date.now();
        await frame.goto(
          `${PASEO}/h/${IDENTITY.server}/workspace/${IDENTITY.leafWs}?open=agent:${open.id}`,
        );
        await expect
          .poll(
            async () =>
              (await frame.evaluate((ws: string) => {
                try {
                  const raw = localStorage.getItem("workspace-layout-state");
                  if (!raw) return "";
                  const d = JSON.parse(raw).state.layoutByWorkspace[ws];
                  const panes: { id: string; focusedTabId: string }[] = [];
                  const walk = (x: unknown): void => {
                    const r = x as { kind?: string; pane?: { id: string; focusedTabId: string }; group?: { children: unknown[] } };
                    if (!r || typeof r !== "object") return;
                    if (r.kind === "pane" && r.pane) panes.push(r.pane);
                    if (r.kind === "group") (r.group?.children ?? []).forEach(walk);
                  };
                  walk(d.root);
                  return panes.find((p) => p.id === d.focusedPaneId)?.focusedTabId ?? "";
                } catch {
                  return "";
                }
              }, wsKey)) === `agent_${open.id}`,
            { timeout: 60_000 },
          )
          .toBe(true);
        const title = await frame.title().catch(() => "untitled");
        await check(`two-agent-${n}`, async () => {
          expect(title, `leg two-agent-${n} title`).toContain(open.to === "worker" ? "Worker" : "Reviewer");
          await expect
            .poll(async () => (await frame.locator("body").innerText().catch(() => "")).includes(open.word), { timeout: 60_000 })
            .toBe(true);
        });
        note(`two-agent-${n}`, `to=${open.to} title=${title} ms=${Date.now() - at}`);
        await frame.screenshot({ path: join(EVIDENCE, `${tag}-two-agent-${n}.png`) });
        await flushTrace();
      }

      // Leg 8: master again after the task.
      await page.bringToFront();
      await doSelect("master-again-decoy", "SBX-M1-L1. Add slugify() and its test");
      const t8 = Date.now();
      await doSelect("master-again", "Sandbox master: text helpers for the sandbox app");
      await doChat("master-again");
      const got8 = await waitShown("master-again", IDENTITY.masterWs, IDENTITY.manager, 90_000, t8);
      const v8 = await safeVerdict(paneVerdict(page));
      const c8 = await safeControl(frameControl(frame, `${IDENTITY.server}:${IDENTITY.masterWs}`, IDENTITY.masterWs));
      note("master-again", `shown=${got8} pane=${JSON.stringify(v8)} controlTitle=${c8.title} controlFocus=${c8.focused}`);
      await shot("master-again");
      await flushTrace();
      await check("master-again", async () => {
        expect(got8 && v8.ok, `master-again leg verdict (shown=${got8} pane=${JSON.stringify(v8)})`).toBe(true);
      });

      // Leg 9: A-while-B control (faithful form).
      await frame.bringToFront();
      await frame.goto(`${PASEO}/h/${IDENTITY.server}/workspace/${IDENTITY.leafWs}?open=agent:${IDENTITY.worker}`);
      await expect
        .poll(async () => (await frame.title()).includes("Worker"), { timeout: 60_000 })
        .toBe(true);
      await page.bringToFront();
      await doSelect("control", "SBX-M1-L1. Add slugify() and its test");
      await doChat("control");
      const t9 = Date.now();
      await page.waitForTimeout(20_000);
      const recNow = await recOf(page);
      const workerShown = recNow.some(
        (r) => r.type === "shown" && r.agentId === IDENTITY.worker && r.t >= t9,
      );
      await check("control", async () => {
        expect(workerShown, "shown(worker) while reviewer requested").toBe(false);
      });
      const pane9 = await page.evaluate(() => document.body.innerText ?? "");
      await check("control-problem", async () => {
        if (pane9.includes("did not show")) {
          expect(pane9, "problem names the requested agent").toContain(IDENTITY.reviewer.slice(0, 8));
        }
      });
      note("control", `no shown(worker) after reviewer request; pane problem=${pane9.includes("did not show")}`);
      await shot("control");
      if (failures.length > 0) throw new Error("legs failed: " + failures.join(" | "));
    } finally {
      await flushTrace().catch(() => {});
    }
  });
});
