import { act, cleanup, render, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { sessionStore } from "../../data/sessions";
import type {
  Analytics,
  LifecycleProjection,
} from "../../types/projection";
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
  const leafKey = "agents-remember/260610_browser-dashboard/01";

  function seedActivityProjection(
    agentPickups: Analytics["agentPickups"] = [],
    state: LifecycleProjection["state"] = "running",
  ) {
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
          agentPickups,
          taskDocuments: [
            taskDoc({
              id: "01",
              lifecycleId: "LC-ACTIVITY",
              title: "Activity Leaf",
              docPath: "/tasks/260610_browser-dashboard/01_activity.json",
            }),
          ],
        },
      }),
    );
  }

  function hydrateTurn(turnState: string) {
    sessionStore.getState().hydrate([
      {
        id: "worker",
        label: "Worker",
        kind: "harness",
        status: "running",
        leafKey,
        lifecycleId: "LC-ACTIVITY",
        seatRole: "worker",
        turnState,
      },
    ]);
  }

  it("keeps a running task distinct from an idle chat", () => {
    seedActivityProjection();
    hydrateTurn("turn-ended");

    const { getByTestId } = render(<LifecycleList selectedId={null} onSelect={vi.fn()} />);

    expect(getByTestId("task-state").getAttribute("aria-label")).toBe(
      "Task progress: running; phase: build",
    );
    expect(getByTestId("chat-activity").textContent).toBe("idle");
    expect(getByTestId("chat-activity").getAttribute("aria-label")).toContain("worker: idle");
  });

  // The row builds its dot variant as `lifecycle?.state ?? statusVariant(doc.status)`, so a live
  // lifecycle hands `Dot` the RAW state string. What Dot then does with it is Dot.test.tsx's
  // business; what these two assert is that this list hands over the state at all.
  const rowMarkOf = (state: LifecycleProjection["state"]): string => {
    cleanup();
    seedActivityProjection([], state);
    hydrateTurn("turn-ended");
    const { getByTestId } = render(<LifecycleList selectedId={null} onSelect={vi.fn()} />);
    const mark = getByTestId("task-state").firstElementChild;
    if (!mark) throw new Error(`no dot rendered for '${state}'`);
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
    seedActivityProjection([], "awaiting-developer");
    hydrateTurn("turn-ended");
    const { getByTestId } = render(<LifecycleList selectedId={null} onSelect={vi.fn()} />);
    const cell = getByTestId("task-state");
    expect(cell.getAttribute("aria-label")).toBe("Task progress: awaiting-developer; phase: build");

    // Read the row's mark out before rendering anything else: both renders share document.body.
    const handoffRow = cell.firstElementChild?.outerHTML;
    expect(handoffRow).toBe(bareDotOf("awaiting-developer"));
    expect(handoffRow).not.toBe(bareDotOf("__no-such-variant__"));
  });

  it("keeps a paused row and an abandoned row apart in the same list", () => {
    // Both states reach `Dot` through `item.variant` and both are rendered by THIS list, so this is
    // not a cross-panel argument — a developer scanning one rail sees them side by side. They used
    // to be the same dormant dot, so a lifecycle waiting to be resumed looked exactly like a dead
    // one.
    expect(rowMarkOf("paused")).not.toBe(rowMarkOf("abandoned"));
  });

  it("shows pending inbox acknowledgment beside an idle chat without conflating them", () => {
    seedActivityProjection([
      {
        id: "pickup:BRIEF",
        entryId: "BRIEF",
        lifecycleId: "LC-ACTIVITY",
        messageKind: "dispatch-brief",
        deliveryState: "delivered",
        attemptCount: 0,
        state: "waiting-for-agent",
        ttlSeconds: 300,
      },
    ]);
    hydrateTurn("turn-ended");

    const { getByTestId, getByText } = render(
      <LifecycleList selectedId={null} onSelect={vi.fn()} />,
    );

    expect(getByTestId("chat-activity").textContent).toBe("idle");
    expect(getByText("brief unacknowledged")).toBeTruthy();
    expect(getByTestId("agent-pickup").getAttribute("title")).toContain("Inbox delivery");
  });

  it("reacts to live shared-session-store transitions while Operations stays mounted", async () => {
    seedActivityProjection();
    hydrateTurn("turn-ended");
    const { getByTestId } = render(<LifecycleList selectedId={null} onSelect={vi.fn()} />);
    expect(getByTestId("chat-activity").textContent).toBe("idle");

    act(() => hydrateTurn("working"));

    await waitFor(() => expect(getByTestId("chat-activity").textContent).toBe("working"));
  });

  it("freezes the rendered collection while its kept-alive rail is hidden, then catches up", async () => {
    seedActivityProjection();
    hydrateTurn("turn-ended");
    const view = render(
      <LifecycleList selectedId={null} onSelect={vi.fn()} active />,
    );
    expect(view.getByTestId("chat-activity").textContent).toBe("idle");

    view.rerender(<LifecycleList selectedId={null} onSelect={vi.fn()} active={false} />);
    act(() => hydrateTurn("working"));
    expect(view.getByTestId("chat-activity").textContent).toBe("idle");

    view.rerender(<LifecycleList selectedId={null} onSelect={vi.fn()} active />);
    await waitFor(() =>
      expect(view.getByTestId("chat-activity").textContent).toBe("working"),
    );
  });
});
