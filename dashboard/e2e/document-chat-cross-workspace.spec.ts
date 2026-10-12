// MIK-R121 rule 1: the chat of a selected task opens in the sidebar.
//
// The rig is `/dev/document-chat-host` (the real document chat against stubbed role-launch
// answers) framed against `/dev/fake-paseo-host` (a fake two-workspace Paseo application that
// runs the REAL plugin bridge with native-faithful navigation and display markup). The host
// starts in the Projects workspace and resolves every opened agent's workspace from its own
// session, like the pinned native application.
//
// This rig is scoped bridge diagnostics, not a native host certificate: it proves request
// propagation, display acknowledgement and deadline/fallback behavior of the real dashboard
// and bridge code, while the application's navigation/display behavior is modeled. The native
// two-workspace witness is a separate evidence duty.
//
// No role in this sandbox uses a harness: there is no Paseo daemon, no provider, no Claude.
import { expect, test, type Page } from "@playwright/test";

async function routeFrameHost(page: Page, extra: () => Record<string, string>): Promise<void> {
  await page.route("**/h/**", async (route) => {
    const url = new URL(route.request().url());
    const query = url.searchParams;
    for (const [key, value] of Object.entries(extra())) query.set(key, value);
    const target = `/dev/fake-paseo-host?${query.toString()}`;
    await route.fulfill({ status: 302, headers: { location: target } });
  });
}

async function frameState(page: Page): Promise<{ workspace: string | null; agent: string | null; log: unknown }> {
  const host = page.frameLocator("iframe[title='Role chats']");
  const workspace = await host.getByTestId("fake-host-workspace").textContent().catch(() => null);
  const agent = await host.getByTestId("fake-host-agent").textContent().catch(() => null);
  const raw = await host.getByTestId("fake-open-log").textContent().catch(() => "[]");
  return { workspace, agent, log: JSON.parse(raw ?? "[]") };
}

async function describeFrame(page: Page): Promise<string> {
  const state = await frameState(page);
  return `workspace=${state.workspace} agent=${state.agent} opens=${JSON.stringify(state.log)}`;
}

async function shownAgent(page: Page): Promise<string | null> {
  return (await frameState(page)).agent;
}

test("selecting a task shows its agent's chat in the sidebar", async ({ page }) => {
  test.setTimeout(120_000);
  await routeFrameHost(page, () => ({}));
  await page.goto("/dev/document-chat-host?effects=off");

  // The Projects selection shows the architect's chat first.
  await expect
    .poll(() => shownAgent(page), { timeout: 30_000, message: "architect chat never appeared" })
    .toBe("agent-architect");
  await page.evaluate(() => {
    (window as unknown as { __frame: unknown }).__frame = document.querySelector("iframe[title='Role chats']");
  });

  // Selecting the task moves the same frame to the worker's workspace and shows its chat:
  // the application resolves the agent's workspace from its own session and the bridge
  // reports the screen only on its post-open display observation.
  await page.getByTestId("harness-doc-leaf").click();
  await expect
    .poll(() => shownAgent(page), { timeout: 30_000 })
    .toBe("agent-worker");
  await expect(page.getByTestId("paseo-frame-agent-problem")).toHaveCount(0);

  // Back to the master: the manager's chat, still the same frame element.
  await page.getByTestId("harness-doc-master").click();
  await expect
    .poll(() => shownAgent(page), { timeout: 30_000 })
    .toBe("agent-manager");

  const sameFrame = await page.evaluate(
    () => document.querySelector("iframe[title='Role chats']") === (window as unknown as { __frame: unknown }).__frame,
  );
  expect(sameFrame, `frame reloaded across workspace switches; ${await describeFrame(page)}`).toBe(true);
  await page.screenshot({ path: test.info().outputPath("task-chat-shown.png") });
});

test("a chat the message cannot bring is waited for, and Retry loads its page", async ({ page }) => {
  test.setTimeout(180_000);
  let stall = true;
  await routeFrameHost(page, (): Record<string, string> => (stall ? { stall: "1" } : {}));
  await page.goto("/dev/document-chat-host?effects=off");
  await expect(page.frameLocator("iframe[title='Role chats']").getByTestId("fake-paseo-host")).toBeVisible({ timeout: 30_000 });

  // Nothing the message asks for can appear, while the application itself still answers: the
  // answer is late, not lost. The pane says that it still waits, keeps
  // the frame it has, and loads nothing on its own.
  await page.getByTestId("harness-doc-leaf").click();
  const waiting = page.getByTestId("paseo-frame-waiting");
  // The task's request is a new one: first it waits, and only after its deadline it is late.
  await expect(waiting).not.toContainText("Still waiting");
  await expect(waiting, `no waiting line; ${await describeFrame(page)}`).toContainText("Still waiting", { timeout: 30_000 });
  const problem = page.getByTestId("paseo-frame-agent-problem");
  await expect(problem).toHaveCount(0);
  await expect(page.getByTestId("paseo-frame-control-banner")).toHaveCount(0);
  const kept = await page.locator("iframe[title='Role chats']").getAttribute("src");
  expect(kept, "a late answer loaded the frame again").not.toContain("open=agent:agent-worker");

  // The host recovers: Retry loads the page without the stall and the worker's chat appears.
  stall = false;
  await waiting.getByRole("button", { name: "Retry" }).click();
  await expect
    .poll(() => shownAgent(page), { timeout: 30_000 })
    .toBe("agent-worker");
  const src = await page.locator("iframe[title='Role chats']").getAttribute("src");
  expect(src, "Retry loads the worker's page").toContain("workspace/wks_leaf?open=agent:agent-worker");
  await expect(problem).toHaveCount(0);
});

test("a compact frame reports the mounted chat without any selected tab", async ({ page }) => {
  test.setTimeout(120_000);
  await routeFrameHost(page, (): Record<string, string> => ({ compact: "1" }));
  await page.goto("/dev/document-chat-host?effects=off");

  // Below the desktop breakpoint there are no agent tabs; the mounted chat still reports.
  await page.getByTestId("harness-doc-leaf").click();
  await expect
    .poll(() => shownAgent(page), { timeout: 30_000 })
    .toBe("agent-worker");
  await expect(page.getByTestId("paseo-frame-agent-problem")).toHaveCount(0);
  const host = page.frameLocator("iframe[title='Role chats']");
  await expect(host.getByTestId("workspace-tab-switcher-trigger")).toBeVisible();
});
