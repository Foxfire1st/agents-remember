import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { LifecycleProjection } from "../../types/projection";
import { Dot } from "../../grammar/Dot";
import { LifecycleList } from "./LifecycleList";
import {
  EMPTY_ANALYTICS,
  enclosure,
  installLifecycleListCleanup,
  lifecycle,
  projection,
  seed,
  taskDoc,
} from "./test-utils";

installLifecycleListCleanup();

describe("LifecycleList independent Operations signals", () => {
  function seedActivityProjection(state: LifecycleProjection["state"] = "running") {
    seed(
      projection({
        lifecycles: [
          lifecycle({
            id: "LC-ACTIVITY",
            repoId: "agents-remember",
            state,
            phase: "build",
          }),
        ],
        enclosures: [
          enclosure({
            enclosure: "/contracts/activity",
            lifecycleId: "LC-ACTIVITY",
            leafId: "01",
          }),
        ],
        analytics: {
          ...EMPTY_ANALYTICS,
          taskDocuments: [
            taskDoc({
              id: "01",
              lifecycleId: "LC-ACTIVITY",
              title: "Activity Leaf",
              docPath:
                "/tasks/agents-remember/260610_browser-dashboard/01_activity.json",
            }),
          ],
        },
      }),
    );
  }

  it("names a running task's progress and phase", () => {
    seedActivityProjection();

    const { getByTestId } = render(<LifecycleList selectedId={null} onSelect={vi.fn()} />);

    expect(getByTestId("task-state").getAttribute("aria-label")).toBe(
      "Task progress: running; phase: build",
    );
    // The row's one word is the task's own state.
    expect(getByTestId("task-word").textContent).toBe("running");
  });

  // A lifecycle that reports hands `Dot` its RAW state string (rowState.ts). What Dot then does
  // with it is Dot.test.tsx's business; what these two assert is that this list hands over the
  // state at all.
  const rowMarkOf = (state: LifecycleProjection["state"]): string => {
    cleanup();
    seedActivityProjection(state);
    const view = render(<LifecycleList selectedId={null} onSelect={vi.fn()} />);
    // A row that is over stands behind the line of completed ones: open it.
    const line = view.container.querySelector("[data-fold]");
    if (line) fireEvent.click(line);
    const mark = view.getByTestId("task-state").firstElementChild;
    if (!mark) throw new Error(`no mark rendered for '${state}'`);
    return mark.outerHTML;
  };
  const bareDotOf = (variant: string): string => {
    const mark = render(<Dot variant={variant} />).container.firstElementChild;
    if (!mark) throw new Error(`Dot rendered nothing for '${variant}'`);
    return mark.outerHTML;
  };

  it("gives a live awaiting-developer row the handoff dot, not the one an unknown state gets", () => {
    // `awaiting-developer` was in neither the statusVariant map nor Dot's variant list, so it fell
    // through to the base and the developer-facing handoff row looked like any other nominal row.
    cleanup();
    seedActivityProjection("awaiting-developer");
    const { getByTestId } = render(<LifecycleList selectedId={null} onSelect={vi.fn()} />);
    const cell = getByTestId("task-state");
    expect(cell.getAttribute("aria-label")).toBe("Task progress: your turn; phase: build");

    // Read the row's mark out before rendering anything else: both renders share document.body.
    const handoffRow = cell.firstElementChild?.outerHTML;
    expect(handoffRow).toBe(bareDotOf("awaiting-developer"));
    expect(handoffRow).not.toBe(bareDotOf("__no-such-variant__"));
  });

  it("keeps a paused row and an abandoned row apart in the same list", () => {
    // Both are rendered by THIS list, so this is not a cross-panel argument — a developer scanning
    // one rail sees them side by side. They used to be the same dormant dot, so a lifecycle
    // waiting to be resumed looked exactly like a dead one. A paused lifecycle keeps the state
    // mark; an abandoned one is over and takes the task page's status dot.
    expect(rowMarkOf("paused")).not.toBe(rowMarkOf("abandoned"));
  });

  const RUNNING = "Task progress: running; phase: build";
  const PAUSED = "Task progress: paused; phase: build";

  it("follows a served state change while Operations stays mounted", async () => {
    seedActivityProjection();
    const { getByTestId } = render(<LifecycleList selectedId={null} onSelect={vi.fn()} />);
    expect(getByTestId("task-state").getAttribute("aria-label")).toBe(RUNNING);

    act(() => seedActivityProjection("paused"));

    await waitFor(() => expect(getByTestId("task-state").getAttribute("aria-label")).toBe(PAUSED));
  });

  it("freezes the rendered collection while its kept-alive rail is hidden, then catches up", async () => {
    seedActivityProjection();
    const view = render(
      <LifecycleList selectedId={null} onSelect={vi.fn()} active />,
    );
    expect(view.getByTestId("task-state").getAttribute("aria-label")).toBe(RUNNING);

    view.rerender(<LifecycleList selectedId={null} onSelect={vi.fn()} active={false} />);
    act(() => seedActivityProjection("paused"));
    expect(view.getByTestId("task-state").getAttribute("aria-label")).toBe(RUNNING);

    view.rerender(<LifecycleList selectedId={null} onSelect={vi.fn()} active />);
    await waitFor(() =>
      expect(view.getByTestId("task-state").getAttribute("aria-label")).toBe(PAUSED),
    );
  });
});
