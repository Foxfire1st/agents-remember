// MIK-R122 rule 8 walk: the sidebar chat stays loaded and switches.
//
// Chat of the master → river → chat → a task → another task → the master → a document
// without an agent → the first task. The frame loads its document once during this walk.
//
// Load counting: every replacement of the `Role chats` iframe element and every change of
// its `src` counts one document load (a new element always loads; a new src always
// reloads). Rule 1 runs this walk BEFORE the change and records the count with causes.
import { expect, test, type Page } from "@playwright/test";

async function routeFrameHost(page: Page): Promise<void> {
  await page.route("**/h/**", async (route) => {
    const url = new URL(route.request().url());
    const target = `/dev/fake-paseo-host?${url.searchParams.toString()}`;
    await route.fulfill({ status: 302, headers: { location: target } });
  });
}

async function startLoadCount(page: Page): Promise<void> {
  await page.evaluate(() => {
    const w = window as unknown as {
      __arLoads: number;
      __arFrame: Element | null;
      __arSrc: string | null;
      __arObs: MutationObserver;
    };
    w.__arLoads = 0;
    w.__arFrame = null;
    w.__arSrc = null;
    const note = () => {
      const f = document.querySelector("iframe[title='Role chats']");
      const src = f?.getAttribute("src") ?? null;
      if (f !== w.__arFrame || (f && src !== w.__arSrc)) {
        w.__arLoads += 1;
        w.__arFrame = f;
        w.__arSrc = src;
      }
    };
    w.__arObs?.disconnect();
    w.__arObs = new MutationObserver(note);
    w.__arObs.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["src"] });
    note();
  });
}

async function loadCount(page: Page): Promise<number> {
  return page.evaluate(() => (window as unknown as { __arLoads: number }).__arLoads);
}

async function frameAgent(page: Page): Promise<string | null> {
  const host = page.frameLocator("iframe[title='Role chats']");
  return host.getByTestId("fake-host-agent").textContent().catch(() => null);
}

async function expectAgent(page: Page, agent: string): Promise<void> {
  await expect
    .poll(() => frameAgent(page), { timeout: 30_000, message: `${agent} never shown` })
    .toBe(agent);
}

test("rule-8 walk loads the chat document exactly once", async ({ page }) => {
  test.setTimeout(240_000);
  await routeFrameHost(page);
  await page.goto("/dev/document-chat-host?effects=off");
  await startLoadCount(page);

  const legs: Array<{ name: string; act: () => Promise<void> }> = [
    { name: "master chat (projects)", act: async () => { await expectAgent(page, "agent-architect"); } },
    { name: "river", act: async () => { await page.getByTestId("harness-rail-river").click(); } },
    { name: "chat again", act: async () => { await page.getByTestId("harness-rail-chat").click(); await expectAgent(page, "agent-architect"); } },
    { name: "task (leaf)", act: async () => { await page.getByTestId("harness-doc-leaf").click(); await expectAgent(page, "agent-worker"); } },
    { name: "another task (master)", act: async () => { await page.getByTestId("harness-doc-master").click(); await expectAgent(page, "agent-manager"); } },
    { name: "master (projects)", act: async () => { await page.getByTestId("harness-doc-projects").click(); await expectAgent(page, "agent-architect"); } },
    {
      name: "document without an agent",
      act: async () => {
        await page.getByTestId("harness-doc-ghost").click();
        await expect(page.getByText("No role can start here")).toBeVisible({ timeout: 30_000 });
      },
    },
    { name: "first task (leaf)", act: async () => { await page.getByTestId("harness-doc-leaf").click(); await expectAgent(page, "agent-worker"); } },
  ];

  const counts: Array<{ leg: string; loads: number }> = [];
  for (const leg of legs) {
    await leg.act();
    counts.push({ leg: leg.name, loads: await loadCount(page) });
  }
  console.log(`LOAD-CENSUS ${JSON.stringify(counts)}`);
  const total = counts.at(-1)?.loads ?? -1;
  expect(total, `walk loaded the chat document ${total} times, expected once; ${JSON.stringify(counts)}`).toBe(1);
});
