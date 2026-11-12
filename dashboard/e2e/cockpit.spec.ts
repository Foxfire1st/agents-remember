import { createServer } from "node:http";

import { expect, test } from "@playwright/test";

// Playwright runs against the gallery (`/dev/bench`), which hydrates panels from fixtures — no
// live server/sim needed, so assertions are deterministic. `?effects=off` freezes animation.
//
// The attention queue is a bell in the head of the tasks column; its entries are in the layer
// the bell opens (MIK-R144 rule 3a). The three tests that read the queue open the bell first.
// Cockpit.test.tsx asserts the same on the same test ids against the same gallery states.

test("attention queue ranks alarms first", async ({ page }) => {
  await page.goto("/dev/bench?state=alarm&effects=off");
  await page.getByTestId("attention-bell").click();
  const queue = page.getByTestId("attention-queue");
  await expect(queue).toContainText("waiting");
  // The alarm entry sorts first. The gallery's alarm is its actionable-drift item: the
  // provider-down item that used to hold this slot went with the search providers (MIK-R199).
  await expect(page.getByTestId("attn-item").first()).toContainText("3 actionable drift");
  await expect(page.getByTestId("attn-severity").first()).toHaveAttribute(
    "aria-label",
    "Severity: alarm",
  );
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
  await page.getByTestId("attention-bell").click();
  await expect(page.getByTestId("attention-queue")).toContainText(
    "nothing waiting",
  );
  await expect(page.getByTestId("attn-item")).toHaveCount(0);
  // An open layer takes the rest of the page out of the accessibility tree: close it first.
  await page.keyboard.press("Escape");
  await expect(page.getByRole("heading", { name: "Detail" })).toBeVisible();
  await expect(
    page.getByText("Select a task to inspect its steps, review, requirements and reports."),
  ).toBeVisible();
});

test('Role chats receives clipboard Permissions Policy in its cross-origin frame', async ({
  page,
}) => {
  const frameServer = createServer((_request, response) => {
    response.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
    response.end(`<!doctype html><html><head><script>
      const policy = document.permissionsPolicy ?? document.featurePolicy;
      const allows = (feature) => Boolean(policy?.allowsFeature?.(feature));
      const allowlist = (feature) => JSON.stringify(policy?.getAllowlistForFeature?.(feature) ?? null);
      document.documentElement.dataset.policyApi = String(Boolean(policy?.allowsFeature));
      document.documentElement.dataset.clipboardRead = String(allows("clipboard-read"));
      document.documentElement.dataset.clipboardWrite = String(allows("clipboard-write"));
      document.documentElement.dataset.clipboardReadAllowlist = allowlist("clipboard-read");
      document.documentElement.dataset.clipboardWriteAllowlist = allowlist("clipboard-write");
    </script></head><body>clipboard policy probe</body></html>`);
  });
  await new Promise<void>((resolve, reject) => {
    frameServer.once('error', reject);
    frameServer.listen(0, '127.0.0.1', resolve);
  });
  const address = frameServer.address();
  if (!address || typeof address === 'string')
    throw new Error('clipboard frame server did not bind TCP');
  // The frame route names the base URL for this dashboard origin; the pane builds the frame URL
  // from it and grants clipboard access to that origin only.
  const frameOrigin = `http://127.0.0.1:${address.port}`;
  const frameUrl = `${frameOrigin}/`;

  try {
    await page.route('**/api/role-launch/frame', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          available: true,
          frameBaseUrl: frameOrigin,
          serverId: 'srv_clipboard_policy_e2e',
          projectsWorkspaceId: null,
          projectsWorkspaceDetail: null,
        }),
      }),
    );
    await page.route('**/api/role-launch/options', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          roleDefaults: { agent: 'codex', model: null, effort: null },
          agents: [{ id: 'codex', label: 'Codex', models: [] }],
          catalogOrigin: 'clipboard-policy-e2e',
          execution: null,
          executions: [],
        }),
      }),
    );

    // The application's own page, not a bench scenario: a scenario answers every /api request
    // from its in-page authority, so the two routes mocked above would never be asked there.
    await page.goto('/?effects=off');
    await page.getByRole('radio', { name: 'Chats' }).click();
    const frame = page.frameLocator('iframe[title="Role chats"]');
    await expect(frame.locator('html')).toHaveAttribute('data-policy-api', 'true');
    await expect(frame.locator('html')).toHaveAttribute('data-clipboard-read', 'true');
    await expect(frame.locator('html')).toHaveAttribute('data-clipboard-write', 'true');
    const embeddedOriginOnly = JSON.stringify([frameOrigin]);
    await expect(frame.locator('html')).toHaveAttribute(
      'data-clipboard-read-allowlist',
      embeddedOriginOnly,
    );
    await expect(frame.locator('html')).toHaveAttribute(
      'data-clipboard-write-allowlist',
      embeddedOriginOnly,
    );

    await page.evaluate((url) => {
      const control = document.createElement('iframe');
      control.title = 'Clipboard policy control without delegation';
      control.src = url;
      document.body.append(control);
    }, frameUrl);
    const control = page.frameLocator(
      'iframe[title="Clipboard policy control without delegation"]',
    );
    await expect(control.locator('html')).toHaveAttribute('data-clipboard-read', 'false');
    await expect(control.locator('html')).toHaveAttribute('data-clipboard-write', 'false');
  } finally {
    await page.goto('about:blank').catch(() => undefined);
    await new Promise<void>((resolve) => frameServer.close(() => resolve()));
  }
});
