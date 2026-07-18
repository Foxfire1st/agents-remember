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

async function routeProductionApis(page: Page) {
  let harnessReads = 0;
  let failNextHarnessRead = false;
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
  await page.route("**/api/terminal/sessions", (route) =>
    route.fulfill({ status: 200, json: { sessions: [] } }),
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
