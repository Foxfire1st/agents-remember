import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { dismissOperatorInboxEntry } from "../../data/operatorInbox";
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
  rowHover,
  rowOf,
  seed,
  standInForCssEscape,
  taskDoc,
} from "./test-utils";

vi.mock("../../data/operatorInbox", async (original) => ({
  ...(await original<typeof import("../../data/operatorInbox")>()),
  dismissOperatorInboxEntry: vi.fn(),
}));

installLifecycleListCleanup();

describe("LifecycleList independent Operations signals", () => {
  function seedActivityProjection(
    agentPickups: Analytics["agentPickups"] = [],
    state: LifecycleProjection["state"] = "running",
    gate?: LifecycleProjection["gate"],
  ) {
    seed(
      projection({
        lifecycles: [
          lifecycle({
            id: "LC-ACTIVITY",
            repoId: "agents-remember",
            state,
            phase: "build",
            gate,
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
              docPath:
                "/tasks/agents-remember/260610_browser-dashboard/01_activity.json",
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
        taskDocumentRef: {
          repository: "agents-remember",
          path: "260610_browser-dashboard/01_activity.json",
        },
        lifecycleId: "LC-ACTIVITY",
        seatRole: "worker",
        turnState,
      },
    ]);
  }

  it("keeps a running task distinct from an idle chat", () => {
    seedActivityProjection();
    hydrateTurn("turn-ended");

    const { getByTestId, queryByTestId } = render(
      <LifecycleList selectedId={null} onSelect={vi.fn()} />,
    );

    expect(getByTestId("task-state").getAttribute("aria-label")).toBe(
      "Task progress: running; phase: build",
    );
    // An idle chat asks for nothing: the row's word stays the task's state, the hover says the chat.
    expect(getByTestId("task-word").textContent).toBe("running");
    expect(queryByTestId("chat-activity")).toBeNull();
    expect(rowHover(rowOf(getByTestId("task-state")))).toContain("Chat: idle. worker: idle");
  });

  // A lifecycle that reports hands `Dot` its RAW state string (rowState.ts). What Dot then does
  // with it is Dot.test.tsx's business; what these two assert is that this list hands over the
  // state at all.
  const rowMarkOf = (state: LifecycleProjection["state"]): string => {
    cleanup();
    seedActivityProjection([], state);
    hydrateTurn("turn-ended");
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
    seedActivityProjection([], "awaiting-developer");
    hydrateTurn("turn-ended");
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

    const { getByTestId, queryByTestId } = render(
      <LifecycleList selectedId={null} onSelect={vi.fn()} />,
    );

    // The hand-over is the row's word; what kind it is, how it was delivered and the idle chat
    // are two lines of the hover.
    expect(getByTestId("task-word").textContent).toBe("unacknowledged");
    expect(queryByTestId("agent-pickup-dismiss")).toBeNull();
    const hover = rowHover(rowOf(getByTestId("task-state"))).split("\n");
    expect(hover).toContain("Inbox: brief delivered, awaiting acknowledgment");
    expect(hover).toContain("Chat: idle. worker: idle");
  });

  it("offers the dismissal of an overdue hand-over beside the word, and it does not choose the row", async () => {
    vi.mocked(dismissOperatorInboxEntry).mockResolvedValue("dismissed");
    seedActivityProjection([
      {
        id: "pickup:OVERDUE",
        entryId: "OVERDUE",
        lifecycleId: "LC-ACTIVITY",
        messageKind: "message",
        deliveryState: "queued",
        attemptCount: 0,
        state: "check-chat",
        ttlSeconds: 300,
      },
    ]);
    const onSelect = vi.fn();
    const { getByTestId } = render(<LifecycleList selectedId={null} onSelect={onSelect} />);

    expect(getByTestId("task-word").textContent).toBe("check chat");
    expect(rowHover(rowOf(getByTestId("task-state")))).toContain(
      "Inbox: acknowledgment overdue; check chat",
    );
    const dismiss = getByTestId("agent-pickup-dismiss");
    expect(dismiss.getAttribute("aria-label")).toBe("Dismiss pickup warning");
    // A whole press, as a mouse makes it: the list chooses a row on the press, before the click.
    standInForCssEscape();
    await userEvent.click(dismiss);

    await waitFor(() => expect(dismissOperatorInboxEntry).toHaveBeenCalledWith("OVERDUE"));
    expect(onSelect).not.toHaveBeenCalled();
    // The row itself is still chosen by a press on it.
    await userEvent.click(getByTestId("task-word"));
    expect(onSelect).toHaveBeenCalledTimes(1);
  });

  it("keeps the dismissal of an overdue hand-over reachable while an open gate is the row's word", async () => {
    vi.mocked(dismissOperatorInboxEntry).mockClear();
    vi.mocked(dismissOperatorInboxEntry).mockResolvedValue("dismissed");
    seedActivityProjection(
      [
        {
          id: "pickup:OVERDUE",
          entryId: "OVERDUE",
          lifecycleId: "LC-ACTIVITY",
          messageKind: "message",
          deliveryState: "queued",
          attemptCount: 0,
          state: "check-chat",
          ttlSeconds: 300,
        },
      ],
      "blocked",
      {
        id: "gate-1",
        kind: "plan-approval",
        state: "open",
        decisions: ["approve", "revise"],
        packet: {},
        evidenceRefs: [],
        ts: "2026-06-24T06:00:40+00:00",
      },
    );
    const onSelect = vi.fn();
    const { getByTestId } = render(<LifecycleList selectedId={null} onSelect={onSelect} />);

    // The gate comes first and is the one word of the row.
    expect(getByTestId("task-word").textContent).toBe("plan-approval");
    // The control beside it says what it dismisses, so it cannot be read as the gate's.
    const dismiss = getByTestId("agent-pickup-dismiss");
    expect(dismiss.textContent).toBe("chat ×");
    expect(dismiss.getAttribute("aria-label")).toBe("Dismiss pickup warning (check chat)");
    expect(dismiss.getAttribute("title")).toContain("The gate stays open");
    standInForCssEscape();
    await userEvent.click(dismiss);

    // It dismisses that inbox entry, chooses no row and leaves the gate where it is.
    await waitFor(() => expect(dismissOperatorInboxEntry).toHaveBeenCalledWith("OVERDUE"));
    expect(dismissOperatorInboxEntry).toHaveBeenCalledTimes(1);
    expect(onSelect).not.toHaveBeenCalled();
    expect(getByTestId("task-word").textContent).toBe("plan-approval");
    expect(rowHover(rowOf(getByTestId("task-state")))).toContain("Gate: plan-approval");
  });

  it("reacts to live shared-session-store transitions while Operations stays mounted", async () => {
    seedActivityProjection();
    hydrateTurn("turn-ended");
    const { getByTestId, queryByTestId } = render(
      <LifecycleList selectedId={null} onSelect={vi.fn()} />,
    );
    expect(queryByTestId("chat-activity")).toBeNull();

    act(() => hydrateTurn("working"));

    await waitFor(() => expect(getByTestId("chat-activity").textContent).toBe("working"));
  });

  it("freezes the rendered collection while its kept-alive rail is hidden, then catches up", async () => {
    seedActivityProjection();
    hydrateTurn("turn-ended");
    const view = render(
      <LifecycleList selectedId={null} onSelect={vi.fn()} active />,
    );
    expect(view.getByTestId("task-word").textContent).toBe("running");

    view.rerender(<LifecycleList selectedId={null} onSelect={vi.fn()} active={false} />);
    act(() => hydrateTurn("working"));
    expect(view.getByTestId("task-word").textContent).toBe("running");
    expect(view.queryByTestId("chat-activity")).toBeNull();

    view.rerender(<LifecycleList selectedId={null} onSelect={vi.fn()} active />);
    await waitFor(() =>
      expect(view.getByTestId("chat-activity").textContent).toBe("working"),
    );
  });
});
