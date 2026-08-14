import { expect, test, type Page } from "@playwright/test";

interface PtyStats {
  renderer: string;
  panes: number;
  linesPerSecondPerPane: number;
  seconds: number;
  frames: number;
  meanMs: number;
  p95Ms: number;
  maxMs: number;
  longFrames: number;
}

interface PtyProbe {
  done: boolean;
  stats?: PtyStats;
  error?: string;
}

interface CockpitProbe {
  requestCounts: Record<string, number>;
  totalRequests: number;
}

const CATALOG_REQUEST = "GET /api/terminal/sessions";

async function readPtyProbe(page: Page): Promise<PtyProbe> {
  return page.evaluate(() => window.__ptyBench ?? { done: false, error: "probe absent" });
}

async function readCockpitProbe(page: Page): Promise<CockpitProbe> {
  return page.evaluate(() => window.__cockpitBench ?? { requestCounts: {}, totalRequests: 0 });
}

function withoutCatalog(counts: Record<string, number>): Record<string, number> {
  return Object.fromEntries(Object.entries(counts).filter(([key]) => key !== CATALOG_REQUEST));
}

test.describe.configure({ mode: "serial" });

test("L6-selected DOM renderer stays within the fleet frame budget at 1/6/12 panes", async ({
  page,
}) => {
  const cells: PtyStats[] = [];
  for (const panes of [1, 6, 12]) {
    await page.goto(
      `/dev/pty-bench?renderer=dom&panes=${panes}&rate=20&seconds=3&settle=1`,
    );
    await page.waitForFunction(() => window.__ptyBench?.done === true, undefined, {
      timeout: 30_000,
    });
    const probe = await readPtyProbe(page);
    expect(probe.error).toBeUndefined();
    expect(probe.stats).toBeDefined();
    const stats = probe.stats!;
    expect(stats).toMatchObject({
      renderer: "dom",
      panes,
      linesPerSecondPerPane: 20,
      seconds: 3,
    });
    expect(stats.frames).toBeGreaterThan(120);
    // This is a regression tripwire, not a hardware ranking: the L6 baseline was ~16.7 ms with
    // zero >33.4 ms frames. The margin tolerates shared-runner jitter but rejects a 30 Hz collapse.
    expect(stats.meanMs).toBeLessThan(25);
    expect(stats.p95Ms).toBeLessThan(33.4);
    expect(stats.longFrames).toBeLessThanOrEqual(3);
    cells.push(stats);
  }
  console.log(`FEUI-L8 PTY PERF ${JSON.stringify(cells)}`);
});

test("24 client rerenders issue no fetch, while capacity remains explicitly unknown", async ({
  page,
}) => {
  await page.goto("/dev/bench?scenario=sessions-fleet-12&effects=off");
  await expect(page.getByTestId("session-rail")).toBeVisible();
  await page.waitForFunction(
    (catalogRequest) =>
      (window.__cockpitBench?.requestCounts[String(catalogRequest)] ?? 0) > 0,
    CATALOG_REQUEST,
  );

  const before = await readCockpitProbe(page);
  const toggle = page.getByTestId("rail-tree-toggle");
  const rail = page.getByTestId("session-rail");
  for (let index = 0; index < 24; index += 1) {
    await toggle.click();
    const actualRows = await rail.locator('[data-testid^="rail-row-"]').count();
    await expect(rail).toHaveAttribute("data-rendered-row-count", String(actualRows));
    await expect(rail).toHaveAttribute(
      "data-virtualized",
      String(actualRows > 50),
    );
  }
  const after = await readCockpitProbe(page);

  expect(withoutCatalog(after.requestCounts)).toEqual(withoutCatalog(before.requestCounts));
  const catalogDelta =
    (after.requestCounts[CATALOG_REQUEST] ?? 0) - (before.requestCounts[CATALOG_REQUEST] ?? 0);
  expect(catalogDelta).toBeLessThanOrEqual(1); // the hoisted 2.5 s driver may cross one beat
  await expect(page.getByTestId("status-ua5-slot")).toHaveText("ctx — / cost — (UA-5 slot)");

  console.log(
    `FEUI-L8 FETCH AUDIT ${JSON.stringify({ rerenders: 24, catalogDelta, before, after })}`,
  );
});
