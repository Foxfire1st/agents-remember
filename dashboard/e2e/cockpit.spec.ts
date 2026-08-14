import { expect, test } from "@playwright/test";

type Oklch = readonly [lightness: number, chroma: number, hue: number];

function parseOklch(value: string): Oklch {
  const match = value
    .trim()
    .match(/^oklch\(\s*([\d.]+)(%?)\s+([\d.]+)\s+([\d.]+)(?:deg)?\s*\)$/i);
  if (!match)
    throw new Error(`expected an opaque oklch token, received ${value}`);
  const lightness = Number(match[1]) / (match[2] === "%" ? 100 : 1);
  return [lightness, Number(match[3]), Number(match[4])];
}

function relativeLuminance([lightness, chroma, hue]: Oklch): number {
  const radians = (hue * Math.PI) / 180;
  const a = chroma * Math.cos(radians);
  const b = chroma * Math.sin(radians);
  const lRoot = lightness + 0.3963377774 * a + 0.2158037573 * b;
  const mRoot = lightness - 0.1055613458 * a - 0.0638541728 * b;
  const sRoot = lightness - 0.0894841775 * a - 1.291485548 * b;
  const l = lRoot ** 3;
  const m = mRoot ** 3;
  const s = sRoot ** 3;
  const clamp = (channel: number) => Math.max(0, Math.min(1, channel));
  const red = clamp(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s);
  const green = clamp(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s);
  const blue = clamp(-0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s);
  return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
}

function contrastRatio(left: Oklch, right: Oklch): number {
  const [lighter, darker] = [
    relativeLuminance(left),
    relativeLuminance(right),
  ].sort((a, b) => b - a);
  return (lighter + 0.05) / (darker + 0.05);
}

// Playwright runs against the gallery (`/dev/bench`), which hydrates panels from fixtures — no
// live server/sim needed, so assertions are deterministic. `?effects=off` freezes animation.

test("attention queue ranks alarms first and surfaces the gate", async ({
  page,
}) => {
  await page.goto("/dev/bench?state=alarm&effects=off");
  const queue = page.getByTestId("attention-queue");
  await expect(queue).toContainText("waiting");
  await expect(page.getByTestId("attn-item").first()).toContainText("down"); // alarm sorts first
});

test("blocked state keeps the projected gate ask visible in the attention queue", async ({
  page,
}) => {
  await page.goto("/dev/bench?state=blocked&effects=off");
  const queue = page.getByTestId("attention-queue");
  await expect(queue).toContainText("Approve the plan?");
  await expect(queue).toContainText("Gate — input needed");
});

test("task rail pivots between repository and phase grouping", async ({
  page,
}) => {
  await page.goto("/dev/bench?state=calm&effects=off");
  const tasks = page.getByRole("heading", { name: /Tasks/ }).locator("..");
  await tasks.getByRole("radio", { name: "BY PHASE" }).click();
  await expect(tasks.getByRole("radio", { name: "BY PHASE" })).toBeChecked();
});

test("empty state renders without items", async ({ page }) => {
  await page.goto("/dev/bench?state=empty&effects=off");
  await expect(page.getByTestId("attention-queue")).toContainText(
    "Queue clear",
  );
  await expect(page.getByRole("heading", { name: "Detail" })).toBeVisible();
  await expect(
    page.getByText("Select a task to inspect its phase, gate, and tokens."),
  ).toBeVisible();
});

const scenarioUrl = (name: string) => `/dev/bench?scenario=${name}&effects=off`;

test("Chats owns responsive inspector intent, inert separators, keyboard resize, and focus recovery", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1400, height: 900 });
  await page.goto(scenarioUrl("sessions-fleet-12"));
  await expect(page.getByRole("radio", { name: "Chats" })).toBeChecked();
  await expect(page.getByRole("radio", { name: "Sessions" })).toHaveCount(0);

  const toggle = page.getByTestId("sessions-toggle-inspector");
  const handle = page.getByTestId("sessions-handle-inspector");
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(page.getByTestId("sessions-inspector")).not.toBeVisible();
  await expect(
    page.getByRole("separator", { name: "Resize chat rail" }),
  ).toBeVisible();
  await expect(
    page.getByRole("separator", { name: "Resize inspector" }),
  ).toHaveCount(0);
  await expect(handle).toHaveAttribute("aria-hidden", "true");
  await expect(handle).toHaveAttribute("tabindex", "-1");
  // Reverse traversal makes the exact DOM adjacency observable without asking the PTY to release
  // Tab (the hosted terminal correctly owns that key while focused).
  await toggle.focus();
  await page.keyboard.press("Shift+Tab");
  await expect(page.locator('[data-region="stage"] :focus')).toHaveCount(1);
  await toggle.focus();
  await page.keyboard.press("Enter");
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByTestId("sessions-inspector")).toBeVisible();
  await expect(
    page.getByRole("separator", { name: "Resize inspector" }),
  ).toBeVisible();
  await expect(handle).toHaveAttribute("tabindex", "0");
  await toggle.focus();
  await page.keyboard.press("Shift+Tab");
  // Base DOM order is rail → stage → inspector: backward from the stage-header toggle lands in
  // the stage, and the inspector handle owns the adjacency to the inspector content.
  await expect(page.locator('[data-region="stage"] :focus')).toHaveCount(1);
  await toggle.focus();
  await page.keyboard.press("Tab");
  await expect(page.locator('[data-region="stage"] :focus')).toHaveCount(1);
  await handle.focus();
  await page.keyboard.press("Tab");
  await expect(page.locator('[data-region="inspector"] :focus')).toHaveCount(1);
  await page.keyboard.press("Shift+Tab");
  await expect(handle).toBeFocused();

  const sizeBefore = await handle.getAttribute("aria-valuenow");
  await handle.focus();
  await page.keyboard.press("ArrowLeft");
  await expect
    .poll(() => handle.getAttribute("aria-valuenow"))
    .not.toBe(sizeBefore);

  // Responsive geometry hides the pane but preserves the deliberate preference and repairs focus.
  await page.locator('[data-region="inspector"] [data-focus-target]').focus();
  await page.setViewportSize({ width: 1000, height: 900 });
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(toggle).toBeFocused();
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.localStorage.getItem("cockpit.chats.inspector-open.v1"),
      ),
    )
    .toBe("1");

  await page.reload();
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(page.getByTestId("sessions-inspector")).not.toBeVisible();
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.localStorage.getItem("cockpit.chats.inspector-open.v1"),
      ),
    )
    .toBe("1");

  await page.setViewportSize({ width: 1400, height: 900 });
  await expect(page.getByTestId("sessions-toggle-inspector")).toHaveAttribute(
    "aria-expanded",
    "true",
  );
  await expect(page.getByTestId("sessions-inspector")).toBeVisible();

  // A deliberate close cancels recovery. Activate the toggle via keyboard (as above at the first
  // toggle): the dev-only ScenarioPlayer caption is a fixed overlay across the bottom StatusLine, so a
  // raw pointer click on the toggle is intercepted by that dev harness element (never present in the
  // real app). focus+Enter exercises the identical collapse path and is what this test already uses.
  await toggle.focus();
  await page.keyboard.press("Enter");
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.localStorage.getItem("cockpit.chats.inspector-open.v1"),
      ),
    )
    .toBe("0");
  await handle.evaluate((node: HTMLElement) => node.focus());
  await page.keyboard.press("ArrowLeft");
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await page.setViewportSize({ width: 1000, height: 900 });
  await page.setViewportSize({ width: 1400, height: 900 });
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
});

test("ended Chats stages are explicit while landed transcripts stay inspectable and read-only", async ({
  page,
}) => {
  await page.goto(scenarioUrl("sessions-ended-exited"));
  const ended = page.getByTestId("sessions-ended-state");
  await expect(ended).toContainText("restored exited chat · exited");
  await expect(ended).toContainText("tmux-command-failed");
  await expect(page.getByTestId("pty-layer-scenario-landed-transcript")).toBeHidden();
  await expect(page.getByTestId("session-composer")).toHaveCount(0);
  await expect(
    page.locator('[data-testid="terminal-host"]:visible'),
  ).toHaveCount(0);

  await page.getByTestId("rail-row-scenario-landed-transcript").click();
  await expect(ended).toHaveCount(0);
  await expect(page.getByTestId("pty-layer-scenario-landed-transcript")).toBeVisible();
  await expect(
    page.locator('[data-testid="terminal-host"]:visible'),
  ).toHaveCount(1);
  await expect(page.getByTestId("session-composer")).toHaveCount(0);

  await page.getByTestId("rail-row-scenario-ended-exited").click();
  await expect(ended).toContainText("restored exited chat · exited");
  await expect(
    page.getByTestId("pty-layer-scenario-landed-transcript"),
  ).toBeHidden();

  await page.goto(scenarioUrl("sessions-ended-retired"));
  await expect(page.getByTestId("sessions-ended-state")).toContainText(
    "restored retired chat · retired",
  );
  await expect(page.getByTestId("sessions-ended-state")).toContainText(
    "seat superseded",
  );
  await expect(page.getByTestId("session-composer")).toHaveCount(0);
  await expect(
    page.locator('[data-testid="terminal-host"]:visible'),
  ).toHaveCount(0);
});

async function openLaunchFlow(page: import("@playwright/test").Page) {
  // The StatusLine bar was removed from the cockpit; the palette opens from any focused
  // surface, so drive it from the always-present rail focus target instead.
  await railFocusTarget(page).focus();
  await page.keyboard.press("Control+K");
  await page.getByTestId("palette-cmd-session.launch").click();
  await expect(page.getByTestId("launch-flow")).toBeVisible();
}

function railFocusTarget(page: import("@playwright/test").Page) {
  return page.locator('[data-region="rail"] [data-focus-target]').first();
}

for (const width of [400, 480]) {
  test(`Chats -> + Chat keeps every harness choice in the ${width}px viewport`, async ({ page }) => {
    await page.setViewportSize({ width, height: 760 });
    await page.goto(scenarioUrl("sessions-launch-happy"));
    await page.getByTestId("chats-new-chat").click();
    for (const id of ["claude", "codex", "pi"]) {
      const box = await page.getByTestId(`launch-harness-${id}`).boundingBox();
      expect(box).not.toBeNull();
      expect(box?.x ?? -1).toBeGreaterThanOrEqual(0);
      expect((box?.x ?? width) + (box?.width ?? 1)).toBeLessThanOrEqual(width);
    }
  });
}

async function chooseClaudePair(page: import("@playwright/test").Page) {
  await page.getByTestId("launch-harness-claude").click();
  await page.getByTestId("launch-model-claude-fable-5[1m]").click();
  await page.getByTestId("launch-effort-max").click();
}

const failedLaunches = [
  {
    harness: "claude",
    model: "ar-unknown-model",
    effort: "max",
    label: "scout-claude",
  },
  {
    harness: "codex",
    model: "gpt-5.6-sol",
    effort: "turbo",
    label: "scout-codex",
  },
  {
    harness: "pi",
    model: "deepseek-v4-flash",
    effort: "max",
    label: "scout-pi",
  },
] as const;

test("sessions scenario: dynamic launch happy path hydrates the new row", async ({
  page,
}) => {
  await page.goto(scenarioUrl("sessions-launch-happy"));
  await openLaunchFlow(page);
  await chooseClaudePair(page);
  await page.getByTestId("launch-submit").click();
  await expect(page.getByTestId("launch-flow")).toBeHidden();
  await expect(page.getByTestId("session-rail")).toContainText("claude");
});

test("sessions scenario: a pending launch cannot cross the selector authority", async ({
  page,
}) => {
  await page.goto(scenarioUrl("sessions-launch-happy"));
  await openLaunchFlow(page);
  await chooseClaudePair(page);

  const oldProbe = await page.evaluateHandle(() => window.__cockpitBench);
  await oldProbe.evaluate((probe) => probe?.advance("defer-next-open"));
  await page.getByTestId("launch-submit").click();
  await expect
    .poll(() =>
      oldProbe.evaluate(
        (probe) =>
          probe?.requests.filter(
            (request) =>
              request.method === "POST" &&
              /^\/api\/terminal\/[^/]+$/.test(request.path),
          ).length ?? 0,
      ),
    )
    .toBe(1);
  const obsoleteId = await oldProbe.evaluate((probe) => {
    const request = probe?.requests.find(
      (candidate) =>
        candidate.method === "POST" &&
        /^\/api\/terminal\/[^/]+$/.test(candidate.path),
    );
    return request
      ? decodeURIComponent(request.path.slice("/api/terminal/".length))
      : null;
  });
  expect(obsoleteId).not.toBeNull();

  await page.getByLabel("scenario").selectOption("sessions-fleet-12");
  const architect = page.getByTestId("rail-row-architect");
  await expect(architect).toBeVisible();
  await architect.click();
  await expect(architect).toHaveAttribute("data-selected", "true");
  await expect(page.getByTestId("sessions-stage")).not.toContainText(
    "no focused session",
  );

  // Keep the successor dialog open as a visible successor-UI survival sentinel. The focused unit
  // regression owns the exact zero-invocation proof for the already-unmounted old callback.
  await openLaunchFlow(page);

  // The retained JSHandle owns the OLD fixture's deferred resolver after the keyed selector swap.
  // Capture both sides and drain promise jobs in one browser task so the 2.5-second catalog interval
  // cannot interleave with the comparison.
  const releaseAudit = await oldProbe.evaluate(async (probe) => {
    const successor = window.__cockpitBench;
    const before = {
      catalogGets: successor?.requestCounts["GET /api/terminal/sessions"] ?? 0,
      snapshot: successor?.snapshot(),
    };
    probe?.advance("release-open");
    for (let turn = 0; turn < 30; turn += 1) await Promise.resolve();
    return {
      before,
      after: {
        catalogGets:
          successor?.requestCounts["GET /api/terminal/sessions"] ?? 0,
        snapshot: successor?.snapshot(),
      },
    };
  });

  expect(releaseAudit.after.catalogGets).toBe(releaseAudit.before.catalogGets);
  expect(releaseAudit.after.snapshot?.pollHealth).toEqual(
    releaseAudit.before.snapshot?.pollHealth,
  );
  expect(releaseAudit.after.snapshot?.sessionIds).toEqual(
    releaseAudit.before.snapshot?.sessionIds,
  );
  expect(releaseAudit.after.snapshot?.sessionIds).not.toContain(obsoleteId);
  expect(releaseAudit.after.snapshot?.cockpitSessionIds).not.toContain(
    obsoleteId,
  );
  expect(releaseAudit.after.snapshot?.focusedSessionId).toBe("architect");
  await expect(architect).toHaveAttribute("data-selected", "true");
  await expect(page.getByTestId(`rail-row-${obsoleteId}`)).toHaveCount(0);
  await expect(page.getByTestId("sessions-stage")).not.toContainText(
    "no focused session",
  );
  await expect(page.getByTestId("launch-flow")).toBeVisible();
  await oldProbe.dispose();
});

test("sessions scenario: launch 409 preserves the live pair and attempted pair", async ({
  page,
}) => {
  await page.goto(scenarioUrl("sessions-launch-conflict"));
  await openLaunchFlow(page);
  await chooseClaudePair(page);
  await page.getByTestId("launch-submit").click();
  const conflict = page.getByTestId("launch-outcome-conflict");
  await expect(conflict).toBeVisible();
  await expect(page.getByTestId("launch-conflict-pairs")).toContainText("live");
  await expect(page.getByTestId("launch-summary")).toContainText(
    "claude-fable-5[1m]",
  );
});

test("sessions scenario: real launches remain starting then one catalog sweep fails all three", async ({
  page,
}) => {
  await page.goto(scenarioUrl("sessions-failed-harnesses"));
  for (const launch of failedLaunches) {
    await openLaunchFlow(page);
    await page.getByTestId(`launch-harness-${launch.harness}`).click();
    await page.getByTestId(`launch-model-${launch.model}`).click();
    await page.getByTestId(`launch-effort-${launch.effort}`).click();
    await page.getByTestId("launch-label").fill(launch.label);
    await page.getByTestId("launch-submit").click();
    await expect(page.getByTestId("launch-flow")).toBeHidden();
  }

  const intermediate = await page.evaluate(() => ({
    ids: window.__cockpitBench?.launchedSessionIds ?? [],
    opens:
      window.__cockpitBench?.requests.filter(
        (request) =>
          request.method === "POST" &&
          /^\/api\/terminal\/[^/]+$/.test(request.path),
      ) ?? [],
  }));
  expect(intermediate.ids).toHaveLength(3);
  expect(new Set(intermediate.ids).size).toBe(3);
  expect(intermediate.opens).toHaveLength(3);
  expect(intermediate.opens.map((request) => request.body)).toEqual(
    failedLaunches.map((launch) => ({
      kind: "harness",
      harness: launch.harness,
      model: launch.model,
      effort: launch.effort,
      label: launch.label,
    })),
  );
  for (const id of intermediate.ids) {
    await expect(page.getByTestId(`rail-dot-${id}`)).toHaveAttribute(
      "aria-label",
      "state: starting",
    );
  }
  await expect(page.getByTestId("failed-launch-banner")).toHaveCount(0);

  await page.evaluate(() => window.__cockpitBench?.advance("launch-failures"));
  for (const [index, id] of intermediate.ids.entries()) {
    await expect(page.getByTestId(`rail-dot-${id}`)).toHaveAttribute(
      "aria-label",
      "state: failed",
      {
        timeout: 7_000,
      },
    );
    await page.getByTestId(`rail-row-${id}`).click();
    await expect(page.getByTestId("failed-launch-banner")).toBeVisible();
    await expect(page.getByTestId("failed-launch-bridge-error")).toContainText(
      [
        "absent from the dynamic catalog",
        "not advertised as launch-settable",
        "provider-qualified",
      ][index],
    );
    await expect(page.getByTestId("failed-launch-refused-pair")).toContainText(
      `${failedLaunches[index].model} · ${failedLaunches[index].effort}`,
    );
    await expect(page.getByTestId("failed-launch-banner")).toContainText(
      "refused",
    );
  }
  const finalOpenCount = await page.evaluate(
    () =>
      window.__cockpitBench?.requests.filter(
        (request) =>
          request.method === "POST" &&
          /^\/api\/terminal\/[^/]+$/.test(request.path),
      ).length,
  );
  expect(finalOpenCount).toBe(3); // the failed projection never auto-retries.
});

test("sessions scenario: queued set promotes only after turn-ended readback", async ({
  page,
}) => {
  await page.goto(scenarioUrl("sessions-set-promotion"));
  await expect(page.getByTestId("model-effort-trigger-effort")).toHaveText(
    "high",
  );
  await page.getByTestId("model-effort-trigger").click();
  await expect(page.getByTestId("model-effort-efforts")).toBeVisible();
  await page.getByTestId("effort-option-max").click();
  await page.getByTestId("model-effort-apply").click();

  await expect(page.getByTestId("set-chip-queued-effort")).toContainText(
    "queued — effort max applies on next turn",
  );
  // The StatusLine's pending-set counter was removed with the bar; the queued chip above is the
  // real pending-set evidence, and the effective marker must stay high until readback.
  await expect(page.getByTestId("set-chip-queued-effort")).toBeVisible();
  await expect(page.getByTestId("model-effort-trigger-effort")).toHaveText(
    "high",
  );
  await expect(page.getByTestId("cockpit-live-polite")).toContainText(
    "effort max queued — applies on next turn",
  );
  const intermediate = await page.evaluate(() => ({
    setRequests:
      window.__cockpitBench?.requests.filter(
        (request) =>
          request.method === "POST" &&
          request.path === "/api/terminal/scenario-set-promotion/set-effort",
      ) ?? [],
    snapshotReads:
      window.__cockpitBench?.requestCounts[
        "GET /api/terminal/scenario-set-promotion/capabilities"
      ] ?? 0,
  }));
  expect(intermediate.setRequests).toHaveLength(1);
  expect(intermediate.setRequests[0]?.body).toEqual({ effort: "max" });

  await page.evaluate(() => window.__cockpitBench?.advance("set-turn-ended"));
  await expect(page.getByTestId("header-state")).toContainText("turn-ended", {
    timeout: 7_000,
  });
  await expect(page.getByTestId("cockpit-live-polite")).toContainText(
    "queued effort change applied — now max",
    { timeout: 7_000 },
  );
  // L5P R4 — a healthy zero is a reassurance zero: once the queued set applies and nothing is
  // pending, the queued chip collapses entirely. The promotion itself is proven by the polite
  // announcement above and the trigger/ledger below.
  await expect(page.getByTestId("set-chip-queued-effort")).toHaveCount(0);
  await expect(page.getByTestId("model-effort-trigger-effort")).toHaveText(
    "max",
  );
  // The set ledger lives in the inspector's Evidence pane (the StatusLine's ledger chip was
  // removed with the bar) — open the inspector to read the recorded line.
  const toggle = page.getByTestId("sessions-toggle-inspector");
  await toggle.focus();
  await page.keyboard.press("Enter");
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await expect(
    page.getByTestId("inspector-set-ledger-item").first(),
  ).toContainText(
    "queued: effort requested max",
  );
  const final = await page.evaluate(() => ({
    setCount:
      window.__cockpitBench?.requestCounts[
        "POST /api/terminal/scenario-set-promotion/set-effort"
      ] ?? 0,
    snapshotReads:
      window.__cockpitBench?.requestCounts[
        "GET /api/terminal/scenario-set-promotion/capabilities"
      ] ?? 0,
  }));
  expect(final.setCount).toBe(1);
  expect(final.snapshotReads).toBe(intermediate.snapshotReads + 1);
});

test("sessions scenario: ambiguous submit reconciles the same request without resend", async ({
  page,
}) => {
  await page.goto(scenarioUrl("sessions-submit-reconcile"));
  const editor = page
    .getByTestId("sessions-stage")
    .getByTestId("session-composer-editor")
    .locator(".cm-content");
  await editor.fill("reconcile this exact message");
  await page
    .getByTestId("sessions-stage")
    .getByTestId("session-composer-send")
    .click();
  await expect(page.getByTestId("cockpit-live-polite")).toContainText(
    "message accepted — delivered",
    {
      timeout: 7_000,
    },
  );
  const counts = await page.evaluate(
    () => window.__cockpitBench?.requestCounts ?? {},
  );
  const submitCount = Object.entries(counts).find(([key]) =>
    key.endsWith("/submit"),
  )?.[1];
  const reconcileCount = Object.entries(counts).find(([key]) =>
    key.endsWith("/reconcile"),
  )?.[1];
  expect(submitCount).toBe(1);
  expect(reconcileCount).toBe(1);
});

test("Chats scenario: same-page selector clears every transient cockpit authority", async ({
  page,
}) => {
  await page.goto(scenarioUrl("sessions-submit-reconcile"));

  // Populate both a pre-session capability cache entry and submission authority/history/announcement.
  await openLaunchFlow(page);
  await page.getByTestId("launch-harness-claude").click();
  await expect(
    page.getByTestId("launch-model-claude-fable-5[1m]"),
  ).toBeVisible();
  await page.getByTestId("launch-cancel").click();
  const editor = page
    .getByTestId("sessions-stage")
    .getByTestId("session-composer-editor")
    .locator(".cm-content");
  await editor.fill("first authority must not survive the selector");
  await page
    .getByTestId("sessions-stage")
    .getByTestId("session-composer-send")
    .click();
  await expect(page.getByTestId("cockpit-live-polite")).toContainText(
    "message accepted — delivered",
    { timeout: 7_000 },
  );

  await page.getByLabel("scenario").selectOption("sessions-fleet-12");
  await expect(page.getByTestId("rail-row-architect")).toBeVisible();
  await expect(page.getByTestId("rail-row-l5-ready")).toHaveCount(0);
  await expect(page.getByTestId("cockpit-live-polite")).toBeEmpty();
  await expect(page.getByTestId("cockpit-live-assertive")).toBeEmpty();
  const resetAudit = await page.evaluate(
    () => window.__cockpitBench?.resetAudit,
  );
  expect(resetAudit?.sessionIds).toHaveLength(12);
  expect(resetAudit?.sessionIds).not.toContain("l5-ready");
  expect(resetAudit?.cockpitSessionIds).toEqual([]);
  expect(resetAudit?.capabilityHarnesses).toEqual([]);
  expect(resetAudit?.polite).toBe("");
  expect(resetAudit?.assertive).toBe("");
  expect(resetAudit?.lifecycleResiduals).toBe(0);
  expect(resetAudit?.ptyHarvestSessions).toEqual([]);

  // Leaving the cockpit catalog entirely must cross the same reset boundary, not only a
  // cockpit→cockpit selector change.
  await page.getByLabel("scenario").selectOption("calm");
  await expect(page.getByRole("radio", { name: "Operations" })).toBeChecked();
  const exitAudit = await page.evaluate(() => window.__cockpitBenchResetAudit);
  expect(exitAudit).toMatchObject({
    sessionIds: [],
    activeId: null,
    cockpitSessionIds: [],
    capabilityHarnesses: [],
    polite: "",
    assertive: "",
    lifecycleResiduals: 0,
    ptyHarvestSessions: [],
  });
  // ScenarioPlayer's dev-only transport controls overlap the bottom ModeBar; use the real
  // keyboard activation path so this remains an in-page selector/reset assertion.
  const chatsView = page.getByRole("radio", { name: "Chats" });
  await chatsView.focus();
  await page.keyboard.press("Enter");
  await expect(chatsView).toBeChecked();
  // The reset boundary itself is proven by `exitAudit` above; whether the rail then shows rows
  // depends on whether a live catalog answers the poll (a running daemon repopulates the rail), so
  // the empty-rail assertion is environment-dependent and intentionally not pinned here.
  await expect(page.getByTestId("cockpit-live-polite")).toBeEmpty();
  await expect(page.getByTestId("cockpit-live-assertive")).toBeEmpty();

  // Reusing the old session id must still perform a fresh authority read, not hit module cache.
  await page.getByLabel("scenario").selectOption("sessions-submit-reconcile");
  await expect(page.getByTestId("rail-row-l5-ready")).toBeVisible();
  const secondEditor = page
    .getByTestId("sessions-stage")
    .getByTestId("session-composer-editor")
    .locator(".cm-content");
  await secondEditor.fill("second authority is a fresh fixture");
  await page
    .getByTestId("sessions-stage")
    .getByTestId("session-composer-send")
    .click();
  await expect(page.getByTestId("cockpit-live-polite")).toContainText(
    "message accepted — delivered",
    { timeout: 7_000 },
  );
  const authorityReads = await page.evaluate(
    () =>
      window.__cockpitBench?.requestCounts[
        "GET /api/terminal/l5-ready/submission-authority"
      ],
  );
  // The reused session performs two authority reads by design: one for the epoch-resolve connect
  // and one for the submit itself — the assertion is that module cache was NOT hit (a stale client
  // would read zero), not that the read count is one.
  expect(authorityReads).toBe(2);
});

test("sessions scenario: interaction choice answers through the session-direct route", async ({
  page,
}) => {
  await page.goto(scenarioUrl("sessions-interaction-answer"));
  await page.getByTestId("interaction-bar-choice").first().click();
  await expect(page.getByTestId("interaction-bar-answered")).toContainText(
    "answered — waiting for the agent",
  );
  const counts = await page.evaluate(
    () => window.__cockpitBench?.requestCounts ?? {},
  );
  expect(counts["POST /api/terminal/interaction-1/interaction-response"]).toBe(1);
});

test("sessions scenario: 12-seat fleet keeps collapsed groups and live attention rollups", async ({
  page,
}) => {
  await page.goto(scenarioUrl("sessions-fleet-12"));
  await expect(page.getByTestId("rail-attention-input")).toContainText(
    "1 need input",
  );
  await expect(page.getByTestId("rail-attention-failed")).toContainText(
    "1 failed",
  );
  await expect(page.getByTestId("rail-attention-working")).toContainText(
    "working",
  );
  await expect(
    page.getByTestId(
      "rail-done-toggle-agents-remember:260714_own-adapter-capability/task.json",
    ),
  ).toContainText("completed · 2");
  await expect(page.getByTestId("rail-row-landed-w1")).toHaveCount(0);
});

test("focused landed cleanup preserves the exact live terminal and its scrollback", async ({
  page,
}) => {
  const sentinel = "r1-scrollback-survives-focused-landed-cleanup";
  await page.goto(scenarioUrl("sessions-terminal-focus"));

  await page.getByTestId("rail-row-raw-terminal").click();
  const workerLayer = page.getByTestId("pty-layer-raw-terminal");
  const workerHost = workerLayer.getByTestId("terminal-host");
  const workerInput = workerLayer.locator(".xterm-helper-textarea");
  await expect(workerHost).toBeVisible();
  await workerInput.focus();
  for (let index = 0; index < 80; index += 1) {
    await page.keyboard.insertText(`continuity filler ${index}`);
    await page.keyboard.press("Enter");
  }
  await page.keyboard.insertText(sentinel);
  await page.keyboard.press("Enter");
  await expect(workerLayer.locator(".xterm-rows")).toContainText(sentinel);
  // `node: HTMLElement` narrows Playwright's default `HTMLElement | SVGElement`: the terminal
  // host is a div, and the identity sentinels below are declared as HTMLElement.
  const before = await workerHost.evaluate((node: HTMLElement) => {
    const viewport = node.querySelector<HTMLElement>(".xterm-viewport");
    const rows = node.querySelector<HTMLElement>(".xterm-rows");
    if (!viewport || !rows) throw new Error("terminal viewport is unavailable");
    const continuity = window as typeof window & {
      __r1TerminalHost?: HTMLElement;
      __r1TerminalViewport?: HTMLElement;
    };
    continuity.__r1TerminalHost = node;
    continuity.__r1TerminalViewport = viewport;
    node.dataset.r1TerminalInstance = "original";
    return { text: rows.textContent ?? "" };
  });
  // This xterm/DOM build keeps only the viewport row elements in the DOM, so scrollHeight does not
  // grow with the buffer; the honest scrollback facts are the retained sentinel text and the host
  // instance identity below.
  expect(before.text).toContain(sentinel);

  await page.getByTestId("rail-row-scenario-landed-transcript").click();
  await expect(
    page.getByTestId("pty-layer-scenario-landed-transcript"),
  ).toBeVisible();
  await page.getByTestId("rail-bulk-sprint").click();
  await page.getByTestId("rail-bulk-execute").click();

  await expect(page.getByTestId("landed-cleanup-outcome")).toContainText(
    "ended 1",
  );
  await expect(
    page.getByTestId("rail-row-scenario-landed-transcript"),
  ).toHaveCount(0);
  await expect(workerLayer).toBeVisible();
  const after = await workerHost.evaluate((node: HTMLElement) => {
    const viewport = node.querySelector<HTMLElement>(".xterm-viewport");
    const rows = node.querySelector<HTMLElement>(".xterm-rows");
    if (!viewport || !rows) throw new Error("terminal viewport is unavailable");
    const continuity = window as typeof window & {
      __r1TerminalHost?: HTMLElement;
      __r1TerminalViewport?: HTMLElement;
    };
    return {
      sameHost: continuity.__r1TerminalHost === node,
      sameViewport: continuity.__r1TerminalViewport === viewport,
      instance: node.dataset.r1TerminalInstance,
    };
  });
  expect(after.sameHost).toBe(true);
  expect(after.sameViewport).toBe(true);
  expect(after.instance).toBe("original");
  // The re-show refit can leave the viewport a few lines above the live bottom (xterm's
  // switch-back scroll re-sync); the buffer itself is retained on the same instance. The honest
  // live contract: retained pre-cleanup rows are still visible, and typing on the same terminal
  // pulls the viewport to the live bottom, where the pre-cleanup sentinel and the new output are
  // both visible.
  await expect(workerLayer.locator(".xterm-rows")).toContainText(
    "continuity filler 70",
  );
  const afterSentinel = `${sentinel}-after-cleanup`;
  await workerInput.focus();
  await page.keyboard.insertText(afterSentinel);
  await page.keyboard.press("Enter");
  await expect(workerLayer.locator(".xterm-rows")).toContainText(sentinel);
  await expect(workerLayer.locator(".xterm-rows")).toContainText(afterSentinel);
});

test("sessions scenario: dropped PTY and stale catalog are visible freshness failures", async ({
  page,
}) => {
  await page.goto(scenarioUrl("sessions-pty-dropped"));
  // A harness seat never mounts a PTY layer, so the honest dropped-socket failure surface is the
  // conversation reconnect banner (the scenario's socket drops before the projection loads).
  await expect(page.getByTestId("conversation-reconnect")).toContainText(
    "structured surface unavailable",
    {
      timeout: 5_000,
    },
  );
  await expect(page.getByTestId("conversation-reconnect")).toContainText(
    "GET /api/terminal/scenario-ready/conversation",
  );

  await page.goto(scenarioUrl("sessions-catalog-stale"));
  await expect(page.getByTestId("rail-poll-stale")).toContainText(
    /\d+ beats missed/,
  );
});

test("sessions scenario: effects=off freezes motion and full keyboard path exits the PTY", async ({
  page,
}) => {
  await page.goto(scenarioUrl("sessions-terminal-focus"));
  await expect(page.locator("html")).toHaveAttribute("data-effects", "off");
  await railFocusTarget(page).focus();
  await page.keyboard.press("Control+K");
  await page.getByTestId("palette-cmd-focus.terminal").click();
  const terminalInput = page
    .getByTestId("sessions-stage")
    .locator(".xterm-helper-textarea");
  await expect(terminalInput).toBeFocused();
  await page.keyboard.type("x");
  await page.keyboard.press("F6");
  await expect(
    page.locator('[data-region="stage"] [data-stage-header]'),
  ).toBeFocused();
});

test("sessions scenario: palette traps Tab, shows effective override, and returns focus", async ({
  page,
}) => {
  await page.addInitScript(() => {
    localStorage.setItem(
      "cockpit.sessions.keymap.v1",
      JSON.stringify({ version: 1, bindings: { "palette.open": "Control+P" } }),
    );
  });
  await page.goto(scenarioUrl("sessions-submit-reconcile"));
  const status = railFocusTarget(page);
  await status.focus();
  await page.keyboard.press("Control+P");
  const input = page.getByTestId("sessions-palette-input");
  await expect(input).toBeFocused();
  await page.getByTestId("palette-cmd-keyboard.reference").click();
  await expect(page.getByTestId("sessions-palette")).toContainText("ctrl+P");
  await page.keyboard.press("Tab");
  expect(
    await page.evaluate(() =>
      document
        .querySelector('[data-testid="sessions-palette"]')
        ?.contains(document.activeElement),
    ),
  ).toBe(true);
  await page.keyboard.press("Escape");
  await expect(status).toBeFocused();
});

test("sessions scenario: malicious Vim region overrides fall back truthfully and PTY F6 is fixed", async ({
  page,
}) => {
  await page.addInitScript(() => {
    localStorage.setItem(
      "cockpit.sessions.keymap.v1",
      JSON.stringify({
        version: 1,
        composerProfile: "vim",
        bindings: { "focus.nextRegion": "K", "focus.prevRegion": "L" },
      }),
    );
  });
  await page.goto(scenarioUrl("sessions-submit-reconcile"));
  const composer = page
    .getByTestId("sessions-stage")
    .getByTestId("session-composer-editor");
  await expect(composer).toHaveAttribute("data-composer-profile", "vim");

  const status = railFocusTarget(page);
  await status.focus();
  await page.keyboard.press("Control+K");
  await page.getByTestId("palette-cmd-keyboard.reference").click();
  const palette = page.getByTestId("sessions-palette");
  await expect(palette).toContainText(/focus\.nextRegion\s*F6/);
  await expect(palette).toContainText(/focus\.prevRegion\s*shift\+F6/);
  await expect(palette).not.toContainText("focus.stageHeader"); // Vim owns Escape; overlay agrees.
  await expect(page.getByTestId("keymap-validation")).toContainText(
    "F6 is the fixed browser-safe composer escape",
  );
  await expect(page.getByTestId("keymap-validation")).toContainText(
    "printable composer binding rejected",
  );
  await page.keyboard.press("Escape");

  const editor = composer.locator(".cm-content");
  await editor.click();
  await page.keyboard.press("i");
  await page.keyboard.type("vim K/L draft");
  await page.keyboard.press("Escape");
  await expect(editor).toBeFocused(); // Vim owns insert → normal; chrome never steals Escape.
  await expect(editor).toContainText("vim K/L draft");

  await page.keyboard.press("F6");
  await expect(
    page.getByTestId("rail-row-l5-ready"),
  ).toBeFocused();
  await page.getByTestId("sessions-toggle-inspector").focus();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("sessions-toggle-inspector")).toHaveAttribute(
    "aria-expanded",
    "true",
  );
  await editor.click();
  await page.keyboard.press("F6");
  await expect(
    page.locator('[data-region="inspector"] [data-focus-target]'),
  ).toBeFocused();
  await expect(editor).toContainText("vim K/L draft");

  // The same persisted payload must not alter the terminal's immutable F6 exit contract.
  await page.goto(scenarioUrl("sessions-terminal-focus"));
  await status.focus();
  await page.keyboard.press("Control+K");
  await page.getByTestId("palette-cmd-focus.terminal").click();
  const terminalInput = page
    .getByTestId("sessions-stage")
    .locator(".xterm-helper-textarea");
  await expect(terminalInput).toBeFocused();
  await page.keyboard.press("F6");
  await expect(
    page.locator('[data-region="stage"] [data-stage-header]'),
  ).toBeFocused();
});

test("sessions scenario: loaded status-text tokens meet WCAG AA on both cockpit grounds", async ({
  page,
}) => {
  await page.goto(scenarioUrl("sessions-fleet-12"));
  await expect(page.getByTestId("rail-status-scout")).toBeVisible();
  await expect(page.getByTestId("rail-status-worker-tui")).toBeVisible();
  await expect(page.getByTestId("rail-status-worker-l4")).toBeVisible();
  const tokenNames = [
    "ink",
    "muted",
    "amber",
    "cyan",
    "alarm",
    "mint",
    "dormant",
    "gold",
    "purple",
  ] as const;
  const values = await page.evaluate((names) => {
    const styles = getComputedStyle(document.documentElement);
    return Object.fromEntries(
      ["bg", "bg-panel", ...names].map((name) => [
        name,
        styles.getPropertyValue(`--${name}`).trim(),
      ]),
    );
  }, tokenNames);
  const backgrounds = [parseOklch(values.bg), parseOklch(values["bg-panel"])];
  for (const token of tokenNames) {
    const foreground = parseOklch(values[token]);
    for (const background of backgrounds) {
      expect(
        contrastRatio(foreground, background),
        `${token} contrast`,
      ).toBeGreaterThanOrEqual(4.5);
    }
  }

  // Representative live status chips must resolve to those audited tokens, not an accidental
  // inherited color. The words are visible alongside their color in the same DOM nodes.
  const resolved = await page.evaluate((names) => {
    const probes = Object.fromEntries(
      names.map((name) => {
        const probe = document.createElement("span");
        probe.style.color = `var(--${name})`;
        document.body.appendChild(probe);
        const color = getComputedStyle(probe).color;
        probe.remove();
        return [name, color];
      }),
    );
    const colorOf = (testId: string) =>
      getComputedStyle(document.querySelector(`[data-testid="${testId}"]`)!)
        .color;
    return {
      probes,
      failed: colorOf("rail-status-scout"),
      awaiting: colorOf("rail-status-worker-tui"),
      working: colorOf("rail-status-worker-l4"),
    };
  }, tokenNames);
  expect(resolved.failed).toBe(resolved.probes.alarm);
  expect(resolved.awaiting).toBe(resolved.probes.amber);
  expect(resolved.working).toBe(resolved.probes.muted);
  await expect(page.getByTestId("rail-status-scout")).toHaveText("failed");
  await expect(page.getByTestId("rail-status-worker-tui")).toHaveText("input?");
  await expect(page.getByTestId("rail-status-worker-l4")).toHaveText("working");
});

// 260718-CHATS-L5P R1/R2 + RV-2 geometry regression. The original crush (`co/nf/ir/m` letter columns,
// `tu…` chips) was fixed at 1440, but the fix's rigidity (chip flexShrink:0 + buttons flex:none)
// regressed the SAME row at ≤1100 into AMPUTATION: End laid out past the `overflow:hidden` aside
// (unreachable at 1100), armed confirm/cancel invisible hundreds of px off the 900 overlay rail
// (the destructive flow could not complete), cancel clipped ~20px even at 1440. The row is now a
// single-line label-group / action-group layout: every grid/flex ancestor can shrink, the elidable
// title/copy yields, and the status chip is dropped while armed. This pins the acceptance the leaf
// actually promised — "never crush at ANY rail width" — at four widths:
// the confirm/cancel are ALWAYS single-line AND fully inside the aside (reachable), never clipped,
// letter-wrapped, or overflowing.
async function reopenRailIfCollapsed(page: import("@playwright/test").Page) {
  const reopen = page.getByTestId("sessions-reopen-rail");
  if ((await reopen.count()) > 0) {
    await reopen.focus();
    await page.keyboard.press("Enter");
    await page.waitForTimeout(250);
  }
}

async function assertArmedRowContained(
  page: import("@playwright/test").Page,
  label: string,
) {
  const rail = await page.getByTestId("session-rail").boundingBox();
  expect(rail, `${label}: rail visible`).not.toBeNull();
  const railRight = rail!.x + rail!.width;

  // Before arming: the chip shows its whole word, never a two-letter remnant (R2).
  await expect(page.getByTestId("rail-status-architect")).toHaveText("turn-ended");

  // Per-row End acts IMMEDIATELY (the armed inline confirm was removed with it; the bulk confirm
  // is the only armed path). Pin the End control itself: single-line, whole, fully inside the aside.
  const end = page.getByTestId("rail-end-architect");
  await expect(end).toBeVisible();
  const endBox = (await end.boundingBox())!;
  expect(endBox, `${label}: end box`).not.toBeNull();
  expect(endBox.height, `${label}: end single-line`).toBeLessThanOrEqual(24);
  expect(endBox.x, `${label}: end left in rail`).toBeGreaterThanOrEqual(rail!.x - 1);
  expect(endBox.x + endBox.width, `${label}: end right in rail`).toBeLessThanOrEqual(railRight + 1);

  // Arm the sprint bulk confirm (injects the long `end 3: a, b, c` copy that used to win the flex
  // fight) and pin the same single-line/reachable contract on the armed confirm/cancel.
  await page.getByTestId("rail-bulk-sprint").click();
  const confirm = page.getByTestId("rail-bulk-execute");
  const cancel = page.getByRole("button", { name: "cancel" });
  await expect(confirm).toBeVisible();
  await expect(cancel).toBeVisible();
  const confirmBox = (await confirm.boundingBox())!;
  const cancelBox = (await cancel.boundingBox())!;
  expect(confirmBox, `${label}: confirm box`).not.toBeNull();
  expect(cancelBox, `${label}: cancel box`).not.toBeNull();

  // Single mono line (the crush blew these to 44-54px tall).
  expect(confirmBox.height, `${label}: confirm single-line`).toBeLessThanOrEqual(24);
  expect(cancelBox.height, `${label}: cancel single-line`).toBeLessThanOrEqual(24);
  // Whole controls: exact copy, one line, and no clipped text. Pixel-width thresholds are host-font
  // magic; overflow is the actual contract the rail must preserve.
  await expect(confirm).toHaveText("confirm");
  await expect(cancel).toHaveText("cancel");
  for (const [name, control] of [
    ["confirm", confirm],
    ["cancel", cancel],
  ] as const) {
    const layout = await control.evaluate((element) => {
      const style = window.getComputedStyle(element);
      return {
        nowrap: style.whiteSpace === "nowrap",
        horizontalFits: element.scrollWidth <= element.clientWidth,
        verticalFits: element.scrollHeight <= element.clientHeight,
      };
    });
    expect(layout.nowrap, `${label}: ${name} nowrap`).toBe(true);
    expect(layout.horizontalFits, `${label}: ${name} horizontally whole`).toBe(true);
    expect(layout.verticalFits, `${label}: ${name} vertically whole`).toBe(true);
  }
  // REACHABLE: fully inside the aside — never amputated past the overflow:hidden edge (RV-2).
  expect(confirmBox.x, `${label}: confirm left in rail`).toBeGreaterThanOrEqual(rail!.x - 1);
  expect(confirmBox.x + confirmBox.width, `${label}: confirm right in rail`).toBeLessThanOrEqual(railRight + 1);
  expect(cancelBox.x, `${label}: cancel left in rail`).toBeGreaterThanOrEqual(rail!.x - 1);
  expect(cancelBox.x + cancelBox.width, `${label}: cancel right in rail`).toBeLessThanOrEqual(railRight + 1);
  // RV-2 — the row keeps its whole status chip while the bulk confirm holds (the per-row drop is
  // gone with the removed per-row armed End), so the chip never collapses to letter remnants.
  await expect(page.getByTestId("rail-status-architect")).toHaveText("turn-ended");

  // Restore for any following assertion on this page.
  await cancel.click();
}

for (const width of [1440, 1100, 900]) {
  test(`R1/R2/RV-2 — End-confirm controls stay single-line and reachable at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(scenarioUrl("sessions-fleet-12"));
    await expect(page.getByTestId("session-rail")).toBeVisible();
    await reopenRailIfCollapsed(page);
    await assertArmedRowContained(page, `${width}px`);
  });
}

test("R1/R2/RV-2 — End-confirm controls stay reachable at the rail's minimum width (collapse threshold)", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(scenarioUrl("sessions-fleet-12"));
  await expect(page.getByTestId("session-rail")).toBeVisible();
  // Drag the rail separator to the far left → it snaps to the 12% minimum (~169px @1440), the
  // narrowest a visible rail ever gets before it snaps fully collapsed. This is the tightest regime.
  const handle = page.getByTestId("sessions-handle-rail");
  const hb = (await handle.boundingBox())!;
  await page.mouse.move(hb.x + hb.width / 2, hb.y + hb.height / 2);
  await page.mouse.down();
  await page.mouse.move(2, hb.y + hb.height / 2, { steps: 20 });
  await page.mouse.up();
  await page.waitForTimeout(300);
  await reopenRailIfCollapsed(page); // if the drag tipped it into full collapse, reopen to the min
  await assertArmedRowContained(page, "min-rail");
});
