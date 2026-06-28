import { expect, test } from "@playwright/test";

// Playwright runs against the gallery (`/dev/bench`), which hydrates panels from fixtures — no
// live server/sim needed, so assertions are deterministic. `?effects=off` freezes animation.

test("attention queue ranks alarms first and surfaces the gate", async ({ page }) => {
  await page.goto("/dev/bench?state=alarm&effects=off");
  const queue = page.getByTestId("attention-queue");
  await expect(queue).toContainText("waiting");
  await expect(page.getByTestId("attn-item").first()).toContainText("down"); // alarm sorts first
});

test("blocked state shows a display-only gate banner in the detail panel", async ({ page }) => {
  await page.goto("/dev/bench?state=blocked&effects=off");
  await page.getByTestId("attention-queue").getByRole("button", { name: "Open" }).click();
  const gate = page.getByTestId("gate-banner");
  await expect(gate).toContainText("Approve the plan?");
  await expect(gate).toContainText("slice 06");
});

test("operation tree pivots between repo and lifecycle", async ({ page }) => {
  await page.goto("/dev/bench?state=calm&effects=off");
  const tree = page.getByTestId("operation-tree");
  await expect(tree).toBeVisible();
  await tree.getByRole("button", { name: "BY LIFECYCLE" }).click();
  await expect(tree.getByRole("button", { name: "BY LIFECYCLE" })).toHaveClass(/is-active/);
});

test("empty state renders without items", async ({ page }) => {
  await page.goto("/dev/bench?state=empty&effects=off");
  await expect(page.getByTestId("attention-queue")).toContainText("Queue clear");
  await expect(page.getByTestId("session-strip")).toContainText("No active sessions");
});
