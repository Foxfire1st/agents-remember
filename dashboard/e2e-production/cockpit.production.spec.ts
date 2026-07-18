import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

import { capabilityEnvelope } from "../src/test/fixtures/capabilityEnvelopes";

const repoRoot = new URL("../../", import.meta.url);
const projection = JSON.parse(
  readFileSync(new URL("../src/fixtures/snapshot.json", import.meta.url), "utf-8"),
) as Record<string, unknown>;
const dashboardBuild = readFileSync(
  new URL("mcp/src/agents_remember/package_data/dashboard.fingerprint", repoRoot),
  "utf-8",
).trim();
const harnesses = {
  harnesses: [
    { id: "claude", name: "Claude Code", detected: true },
    { id: "codex", name: "Codex", detected: true },
    { id: "pi", name: "Pi.dev", detected: true },
  ],
};

type OpenDisposition =
  | "accept"
  | "network"
  | "http"
  | "harness"
  | "missing"
  | "malformed"
  | "contradictory";

async function routeProductionApis(page: Page) {
  let harnessReads = 0;
  let failNextHarnessRead = false;
  let nextOpen: OpenDisposition = "accept";
  const openRequests: Array<{ id: string; body: Record<string, unknown> }> = [];
  const sessions: Record<string, unknown>[] = [];
  await page.route("**/api/stream", (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: `retry: 60000\nevent: snapshot\ndata: ${JSON.stringify({
        ...projection,
        servingBuild: {
          version: "production-smoke",
          bootedAt: "2026-07-18T12:00:00Z",
          dashboardBuild,
        },
      })}\n\n`,
    }),
  );
  await page.route("**/api/events", (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: 'retry: 60000\nevent: ready\ndata: {"ready":true}\n\n',
    }),
  );
  await page.route("**/api/terminal/*", (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const match = path.match(/^\/api\/terminal\/([^/]+)$/);
    if (request.method() !== "POST" || !match) return route.fallback();
    const id = decodeURIComponent(match[1]);
    const body = (request.postDataJSON() ?? {}) as Record<string, unknown>;
    openRequests.push({ id, body });
    const disposition = nextOpen;
    nextOpen = "accept";
    if (disposition === "network") return route.abort("failed");
    if (disposition === "http") {
      return route.fulfill({
        status: 503,
        json: { status: "unavailable", detail: "terminal host unavailable" },
      });
    }
    if (disposition === "harness") {
      return route.fulfill({
        status: 400,
        json: { status: "bad-kind", detail: "harness not installed: 'claude'" },
      });
    }
    if (disposition === "missing") return route.fulfill({ status: 200, body: "" });
    if (disposition === "malformed") {
      return route.fulfill({ status: 200, contentType: "application/json", body: "not-json" });
    }
    if (disposition === "contradictory") {
      return route.fulfill({
        status: 200,
        json: {
          session: id,
          label: "Terminal 1",
          kind: "terminal",
          harness: "claude",
          status: "running",
          controlState: "ready",
        },
      });
    }
    const kind = body.kind === "harness" ? "harness" : "terminal";
    const label = typeof body.label === "string" ? body.label : kind === "harness" ? "Claude" : "Terminal";
    const row = {
      id,
      label,
      kind,
      ...(kind === "harness" && typeof body.harness === "string" ? { harness: body.harness } : {}),
      ...(typeof body.lifecycleId === "string" ? { lifecycleId: body.lifecycleId } : {}),
      ...(typeof body.leafKey === "string" ? { leafKey: body.leafKey } : {}),
      seatRole: kind === "harness" ? "chat" : "terminal",
      cwd: "/workspace",
      tmuxName: `ar-${id}`,
      createdAt: "2026-07-18T12:00:00Z",
      lastAttachedAt: "2026-07-18T12:00:00Z",
      status: "running",
      ...(kind === "harness" ? { controlState: "starting" } : {}),
      ...(typeof body.model === "string" ? { resolvedModel: body.model } : {}),
      ...(typeof body.effort === "string" ? { resolvedEffort: body.effort } : {}),
    };
    sessions.push(row);
    return route.fulfill({
      status: 200,
      json: {
        session: id,
        label,
        kind,
        ...(kind === "harness" && typeof body.harness === "string" ? { harness: body.harness } : {}),
        lifecycleId: typeof body.lifecycleId === "string" ? body.lifecycleId : null,
        leafKey: typeof body.leafKey === "string" ? body.leafKey : null,
        seatRole: row.seatRole,
        cwd: row.cwd,
        tmuxName: row.tmuxName,
        status: "running",
        ...(kind === "harness" ? { controlState: "starting" } : {}),
        resolvedModel: typeof body.model === "string" ? body.model : null,
        resolvedEffort: typeof body.effort === "string" ? body.effort : null,
      },
    });
  });
  await page.route("**/api/terminal/sessions", (route) =>
    route.fulfill({ status: 200, json: { sessions } }),
  );
  await page.route("**/api/harnesses/claude/capabilities**", (route) =>
    route.fulfill({ status: 200, json: capabilityEnvelope("claude", "hit") }),
  );
  await page.route("**/api/harnesses", (route) => {
    harnessReads += 1;
    if (failNextHarnessRead) {
      failNextHarnessRead = false;
      return route.abort("failed");
    }
    return route.fulfill({ status: 200, json: harnesses });
  });
  return {
    readCount: () => harnessReads,
    failNext: () => {
      failNextHarnessRead = true;
    },
    setNextOpen: (disposition: OpenDisposition) => {
      nextOpen = disposition;
    },
    openRequests: () => [...openRequests],
    lastSessionId: () => openRequests.at(-1)?.id ?? null,
  };
}

async function openChatChooser(page: Page) {
  await page.getByRole("radio", { name: "Chats" }).click();
  await page.getByRole("button", { name: "New chat — choose Claude, Codex, or Pi" }).click();
  await expect(page.getByTestId("launch-flow")).toBeVisible();
}

test("fresh production bundle exposes the one Chats chooser, capabilities, and matching client identity", async ({
  page,
}) => {
  await routeProductionApis(page);
  await page.goto("/");
  await expect(page.getByTestId("serving-build")).toHaveAttribute(
    "data-client-build-current",
    "true",
  );
  await openChatChooser(page);
  await expect(page.getByTestId("launch-harness-claude")).toBeVisible();
  await expect(page.getByTestId("launch-harness-codex")).toBeVisible();
  await expect(page.getByTestId("launch-harness-pi")).toBeVisible();
  await page.getByTestId("launch-harness-claude").click();
  await expect(page.getByTestId("launch-model-list")).toBeVisible();
});

test("persistent production page recovers a failed catalog read in place", async ({ page }) => {
  const catalog = await routeProductionApis(page);
  await page.goto("/");
  await expect(page.getByTestId("serving-build")).toBeVisible();
  const readsBeforeChooser = catalog.readCount();
  catalog.failNext();
  await openChatChooser(page);
  await expect(page.getByTestId("launch-harness-error")).toContainText("network error");
  await page.getByTestId("launch-harness-retry").click();
  await expect(page.getByTestId("launch-harness-claude")).toBeVisible();
  expect(catalog.readCount()).toBe(readsBeforeChooser + 2);
});

for (const width of [400, 480]) {
  test(`${width}px production chooser keeps every harness control inside the viewport`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 760 });
    await routeProductionApis(page);
    await page.goto("/");
    await openChatChooser(page);
    for (const id of ["claude", "codex", "pi"]) {
      const box = await page.getByTestId(`launch-harness-${id}`).boundingBox();
      expect(box).not.toBeNull();
      expect(box?.x ?? -1).toBeGreaterThanOrEqual(0);
      expect((box?.x ?? width) + (box?.width ?? 1)).toBeLessThanOrEqual(width);
    }
  });
}

test("production raw terminal materializes and focuses only the one accepted server identity", async ({
  page,
}) => {
  const api = await routeProductionApis(page);
  await page.goto("/");
  await page.getByRole("radio", { name: "Chats" }).click();
  await page.getByTestId("chats-new-terminal").click();
  await expect.poll(api.lastSessionId).not.toBeNull();
  const id = api.lastSessionId();
  expect(id).not.toBeNull();
  await expect(page.getByTestId(`rail-row-${id}`)).toBeVisible();
  await expect(page.getByTestId(`pty-layer-${id}`)).toHaveAttribute("data-pty-visible", "true");
  expect(api.openRequests()).toHaveLength(1);
  expect(api.openRequests()[0]?.body).toMatchObject({ kind: "terminal", label: "Terminal 1" });
});

test("production canonical harness launch delegates to one opener and hydrates that identity", async ({
  page,
}) => {
  const api = await routeProductionApis(page);
  await page.goto("/");
  await openChatChooser(page);
  await page.getByTestId("launch-harness-claude").click();
  await page.getByTestId("launch-model-claude-fable-5[1m]").click();
  await page.getByTestId("launch-effort-max").click();
  await page.getByTestId("launch-submit").click();
  await expect(page.getByTestId("launch-flow")).toBeHidden();
  const id = api.lastSessionId();
  expect(id).not.toBeNull();
  await expect(page.getByTestId(`rail-row-${id}`)).toBeVisible();
  expect(api.openRequests()).toHaveLength(1);
  expect(api.openRequests()[0]?.body).toMatchObject({
    kind: "harness",
    harness: "claude",
    model: "claude-fable-5[1m]",
    effort: "max",
  });
});

for (const failure of ["network", "http", "missing", "malformed", "contradictory"] as const) {
  test(`production raw ${failure} failure stays visible with zero ghost rows`, async ({ page }) => {
    const api = await routeProductionApis(page);
    api.setNextOpen(failure);
    await page.goto("/");
    await page.getByRole("radio", { name: "Chats" }).click();
    await page.getByTestId("chats-new-terminal").click();
    await expect(page.getByTestId("chats-session-open-error")).toContainText(
      failure === "malformed" || failure === "contradictory"
        ? "session open protocol"
        : `session open ${failure === "missing" ? "missing-response" : failure}`,
    );
    await expect(page.locator('[data-testid^="rail-row-"]')).toHaveCount(0);
    await expect(page.getByTestId("stage-empty-identity")).toBeVisible();
    expect(api.openRequests()).toHaveLength(1);
  });
}

test("production rejected harness stays in the chooser with zero ghost rows", async ({ page }) => {
  const api = await routeProductionApis(page);
  api.setNextOpen("harness");
  await page.goto("/");
  await openChatChooser(page);
  await page.getByTestId("launch-harness-claude").click();
  await page.getByTestId("launch-model-claude-fable-5[1m]").click();
  await page.getByTestId("launch-effort-max").click();
  await page.getByTestId("launch-submit").click();
  await expect(page.getByTestId("launch-outcome-refused")).toContainText("bad-kind");
  await expect(page.locator('[data-testid^="rail-row-"]')).toHaveCount(0);
  expect(api.openRequests()).toHaveLength(1);
});
