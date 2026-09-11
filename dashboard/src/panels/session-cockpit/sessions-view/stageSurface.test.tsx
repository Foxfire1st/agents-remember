import {
  act,
  fireEvent,
  render,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  activeConversationStore,
  connectConversation,
  disconnectConversation,
} from "../../../data/conversation/store";
import type {
  ConversationItem,
  ConversationPage,
} from "../../../data/conversation/types";
import { fromTerminalSessionInfo, sessionStore } from "../../../data/sessions";
import { sessionCockpitStore } from "../../../data/sessionCockpitStore";
import { catalogRow, FLEET } from "../../../test/fixtures/catalogRows";
import {
  conversationItem,
  conversationPage,
} from "../../../test/fixtures/conversationWire";
import { SessionsView } from "./SessionsView";
import {
  L5Q_IDENTITY,
  l5qStatus,
  seedLiveProjection,
  stubHangingFetch,
} from "./test-utils";

vi.mock("../../Terminal", async () => {
  const { useEffect } = await import("react");
  const { mockTerminalMounts, mockTerminalUnmounts } = await import(
    "./test-utils"
  );
  return {
    Terminal: ({ sessionId, readOnly }: { sessionId: string; readOnly?: boolean }) => {
      useEffect(() => {
        mockTerminalMounts.push(sessionId);
        return () => {
          mockTerminalUnmounts.push(sessionId);
        };
      }, [sessionId]);
      return (
        <div
          data-testid={`mock-terminal-${sessionId}`}
          data-read-only={String(readOnly ?? false)}
        />
      );
    },
  };
});

describe("L6: stage surface, WorkingLine, InteractionBar, stop residuals", () => {
  beforeEach(() => {
    sessionStore.getState().hydrate(FLEET.map(fromTerminalSessionInfo));
    sessionCockpitStore.setState({ focusedSessionId: null });
  });

  afterEach(async () => {
    // RTL auto-cleanup unmounts the tree, but the conversation timeline's virtualizer
    // debounces scroll (150 ms): a real-timer test that fired wheel/scroll can leave that
    // callback pending past jsdom teardown, where React has no `window` to schedule
    // against. Flush it while jsdom is still alive.
    await new Promise((resolve) => setTimeout(resolve, 200));
  });

  it("defaults a controlled seat to the structured surface; the PTY is a default-off read-only diagnostic (R2/R7)", async () => {
    // No conversation backend in this unit test: connect fails cleanly and the structured body still
    // composes. We assert the composition contract, not live conversation content.
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 503, json: async () => ({}) }) as Response));
    const { findByTestId, getByTestId, queryByTestId } = render(<SessionsView active />);
    await waitFor(() =>
      expect(sessionCockpitStore.getState().focusedSessionId).toBe("worker-tui"),
    );
    // The controlled default is the structured body, NOT the PTY, and no diagnostic is open.
    const stageBody = await findByTestId("chats-stage-body");
    expect(stageBody.getAttribute("data-mode")).toBe("active-conversation");
    expect(queryByTestId("pty-surface")).toBeNull();
    expect(getByTestId("terminal-diagnostics-drawer").getAttribute("data-open")).toBe("false");
    expect(queryByTestId("mock-terminal-worker-tui")).toBeNull();

    // Opening the diagnostics drawer surfaces the controlled runner log read-only (§12.6).
    fireEvent.keyDown(document.body, { key: "k", code: "KeyK", ctrlKey: true });
    const input = getByTestId("sessions-palette-input");
    fireEvent.change(input, { target: { value: "terminal diagnostics" } });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" });
    const terminal = await findByTestId("mock-terminal-worker-tui");
    expect(terminal.getAttribute("data-read-only")).toBe("true");
  });

  it("distinguishes exited/retired ended chats from landed read-only terminal inspection", async () => {
    const exited = fromTerminalSessionInfo(
      catalogRow({
        id: "ended-exited",
        label: "restored exited chat",
        status: "exited",
        exitEvidence: "tmux-command-failed",
      }),
    );
    const landed = fromTerminalSessionInfo(
      catalogRow({
        id: "ended-landed",
        label: "landed transcript",
        status: "landed",
        landedReason: "leaf integrated",
      }),
    );
    const retired = fromTerminalSessionInfo(
      catalogRow({
        id: "ended-retired",
        label: "retired chat",
        status: "terminated",
        retiredReason: "seat superseded",
      }),
    );
    sessionStore.getState().hydrate([exited, landed, retired]);
    sessionCockpitStore.setState({ focusedSessionId: exited.id });
    const { findByTestId, getByTestId, queryByTestId } = render(
      <SessionsView active />,
    );

    let ended = await findByTestId("sessions-ended-state");
    expect(ended.textContent).toContain("restored exited chat · exited");
    expect(ended.textContent).toContain("tmux-command-failed");
    expect(queryByTestId("pty-pane-chrome")).toBeNull();
    expect(queryByTestId("mock-terminal-ended-exited")).toBeNull();
    expect(queryByTestId("session-composer")).toBeNull();
    expect(queryByTestId("interaction-bar")).toBeNull();

    act(() => sessionCockpitStore.getState().setFocusedSession(landed.id));
    const terminal = await findByTestId("mock-terminal-ended-landed");
    expect(terminal.getAttribute("data-read-only")).toBe("true");
    expect(queryByTestId("sessions-ended-state")).toBeNull();
    expect(queryByTestId("session-composer")).toBeNull();
    expect(getByTestId("pty-surface").getAttribute("data-focus-target")).toBe(
      "true",
    );

    act(() => sessionCockpitStore.getState().setFocusedSession(retired.id));
    ended = await findByTestId("sessions-ended-state");
    expect(ended.textContent).toContain("retired chat · retired");
    expect(ended.textContent).toContain("seat superseded");
    expect(queryByTestId("mock-terminal-ended-retired")).toBeNull();
    // The landed transcript remains mounted but hidden while the ended overview has focus.
    expect(getByTestId("pty-layer-ended-landed").style.display).toBe("none");
  });

  it("renders the WorkingLine in the reserved slot ONLY for a working focused seat", async () => {
    const { getByTestId, queryByTestId } = render(<SessionsView active />);
    await waitFor(() =>
      expect(sessionCockpitStore.getState().focusedSessionId).toBe(
        "worker-tui",
      ),
    );
    // worker-tui is awaiting-input — no turn theater.
    expect(queryByTestId("working-line")).toBeNull();
    fireEvent.click(getByTestId("rail-row-worker-l4")); // working seat
    await waitFor(() => expect(queryByTestId("working-line")).not.toBeNull());
    expect(
      getByTestId("stage-working-line-slot").contains(
        getByTestId("working-line"),
      ),
    ).toBe(true);
    // The slot docks between the conversation and the
    // composer — after the stage body, before the composer — not in the stage's top chrome.
    const slot = getByTestId("stage-working-line-slot");
    const stageBody = getByTestId("chats-stage-body");
    const composer = getByTestId("session-composer");
    expect(stageBody.compareDocumentPosition(slot) & 4).toBe(4);
    expect(slot.compareDocumentPosition(composer) & 4).toBe(4);
    expect(getByTestId("working-line-verb").textContent).toBe("working");
    // The stop control docks in the composer footer beside send —
    // the working line carries none.
    expect(queryByTestId("working-line-stop")).toBeNull();
    const stop = getByTestId("session-composer-stop") as HTMLButtonElement;
    expect(stop.disabled).toBe(true);
    expect(stop.getAttribute("data-disabled-reason")).toContain("UA-7");
    // Beside send: the stop immediately precedes the send control in the footer.
    expect(stop.compareDocumentPosition(getByTestId("session-composer-send")) & 4).toBe(4);
  });

  it("source-selects the conversation-driven WorkingLine while the harness seat's stream is live (L5Q)", async () => {
    stubHangingFetch();
    const { getByTestId, queryByTestId } = render(<SessionsView active />);
    await waitFor(() =>
      expect(sessionCockpitStore.getState().focusedSessionId).toBe(
        "worker-tui",
      ),
    );
    fireEvent.click(getByTestId("rail-row-worker-l4")); // catalog: working
    await waitFor(() => expect(queryByTestId("working-line")).not.toBeNull());
    // No live stream yet: the catalog-driven line can only say the plain word.
    expect(getByTestId("working-line-verb").textContent).toBe("working");
    // A live projection takes over the slot — a canonical wire state word can only come from the
    // conversation source.
    act(() => seedLiveProjection("worker-l4", "settling"));
    await waitFor(() =>
      expect(getByTestId("working-line-verb").textContent).toBe("settling"),
    );
    // The stream dropping to reconnecting hands the slot back to the catalog-driven line.
    act(() => {
      const current = activeConversationStore.getState().bySession["worker-l4"];
      activeConversationStore.setState({
        bySession: { "worker-l4": { ...current, stream: "reconnecting" } },
      });
    });
    await waitFor(() =>
      expect(getByTestId("working-line-verb").textContent).toBe("working"),
    );
  });

  it("keeps the catalog-driven WorkingLine for a legacy raw seat even with a live projection (L5Q)", async () => {
    sessionStore.getState().hydrate([
      fromTerminalSessionInfo(
        catalogRow({
          id: "legacy-working",
          label: "legacy raw worker",
          kind: "terminal",
          harness: undefined,
          seatRole: "terminal",
          status: "running",
          turnState: "working",
          turnStateChangedAt: "2026-07-16T09:15:00Z",
        }),
      ),
    ]);
    sessionCockpitStore.setState({ focusedSessionId: "legacy-working" });
    // A live projection showing a canonical state word must NOT leak onto a non-harness seat.
    act(() => seedLiveProjection("legacy-working", "settling"));
    const { getByTestId, queryByTestId } = render(<SessionsView active />);
    await waitFor(() => expect(queryByTestId("working-line")).not.toBeNull());
    expect(getByTestId("working-line-verb").textContent).toBe("working");
  });

  it("keeps a switched-away chat's surface mounted end-to-end: focus switches preserve the SAME viewport DOM node (F-j)", async () => {
    // A prior glitch: switching chats unloaded the conversation in
    // React, so the timeline remounted at the top and yanked back to the bottom. Through the real
    // view (rail clicks, real stores), the focused chat's surface must round-trip untouched.
    stubHangingFetch();
    // Give worker-l4 a REAL warm conversation (live runtime + projection): connectConversation
    // creates the runtime (its hydrate hangs on the stubbed fetch), then the seeded projection
    // puts the stream live — exactly the warm state a previously focused chat is in.
    connectConversation("worker-l4", "e1", {
      fetchImpl: vi.fn(() => new Promise<Response>(() => {})) as unknown as typeof fetch,
    });
    seedLiveProjection("worker-l4", "ready");
    const { getByTestId } = render(<SessionsView active />);
    await waitFor(() =>
      expect(sessionCockpitStore.getState().focusedSessionId).toBe("worker-tui"),
    );
    fireEvent.click(getByTestId("rail-row-worker-l4"));
    await waitFor(() =>
      expect(sessionCockpitStore.getState().focusedSessionId).toBe("worker-l4"),
    );
    const wrapper = getByTestId("conversation-keepalive-worker-l4");
    expect(wrapper.getAttribute("aria-hidden")).toBeNull();
    const viewport = within(wrapper).getByTestId("conversation-viewport");

    fireEvent.click(getByTestId("rail-row-worker-tui"));
    await waitFor(() =>
      expect(sessionCockpitStore.getState().focusedSessionId).toBe("worker-tui"),
    );
    // Mounted but hidden — never unloaded.
    expect(
      getByTestId("conversation-keepalive-worker-l4").getAttribute("aria-hidden"),
    ).toBe("true");
    expect(
      within(getByTestId("conversation-keepalive-worker-l4")).queryByTestId(
        "conversation-viewport",
      ),
    ).not.toBeNull();

    fireEvent.click(getByTestId("rail-row-worker-l4"));
    await waitFor(() =>
      expect(sessionCockpitStore.getState().focusedSessionId).toBe("worker-l4"),
    );
    const restored = getByTestId("conversation-keepalive-worker-l4");
    expect(restored.getAttribute("aria-hidden")).toBeNull();
    expect(
      within(restored).getByTestId("conversation-viewport"),
    ).toBe(viewport);
    disconnectConversation("worker-l4");
  });

  // A warm projection WITH items (the earlier seeds carry status only), so the timeline has
  // scrollable content for the scroll-restore test.
  function seedWorkerL4Items(count: number): void {
    const items: ConversationItem[] = Array.from({ length: count }, (_, index) =>
      conversationItem({
        itemId: `worker-l4-item-${index + 1}`,
        globalOrdinal: index + 1,
        blocks: [
          {
            blockId: `worker-l4-item-${index + 1}-b`,
            type: "markdown",
            markdown: `message ${index + 1}`,
          },
        ],
      }),
    );
    const page: ConversationPage = conversationPage({
      identity: L5Q_IDENTITY,
      items,
      status: l5qStatus("ready"),
    });
    activeConversationStore.getState().applyPage("worker-l4", page, "initial");
    activeConversationStore.getState().setStreamPhase("worker-l4", "live");
  }

  it("restores the focused chat's scroll position across a cockpit view switch (F-ac)", async () => {
    // A prior defect: switching cockpit TABS (Chats ↔ Operations ↔
    // Files) reopened the chat at the START — the layer hides with display:none, which destroys
    // the DOM scroll offset. Through the real view: the position is remembered on scroll and
    // restored on re-show, on the SAME viewport node.
    stubHangingFetch();
    connectConversation("worker-l4", "e1", {
      fetchImpl: vi.fn(() => new Promise<Response>(() => {})) as unknown as typeof fetch,
    });
    seedWorkerL4Items(6);
    const { getByTestId, rerender } = render(<SessionsView active />);
    await waitFor(() =>
      expect(sessionCockpitStore.getState().focusedSessionId).toBe("worker-tui"),
    );
    fireEvent.click(getByTestId("rail-row-worker-l4"));
    await waitFor(() =>
      expect(sessionCockpitStore.getState().focusedSessionId).toBe("worker-l4"),
    );
    const viewport = within(
      getByTestId("conversation-keepalive-worker-l4"),
    ).getByTestId("conversation-viewport");
    // jsdom has no layout: pin the scroll geometry, then read mid-conversation.
    Object.defineProperty(viewport, "scrollHeight", { configurable: true, value: 2000 });
    Object.defineProperty(viewport, "clientHeight", { configurable: true, value: 600 });
    // The operator's scroll arrives with trusted input — the intent lock's disengagement is
    // input-gated, so a bare scroll event (content-driven semantics) would never flip the lock.
    fireEvent.wheel(viewport);
    viewport.scrollTop = 456;
    fireEvent.scroll(viewport);

    // Away: the chats layer goes display:none (the browser resets the offset silently — applied
    // by hand here, jsdom has no layout to destroy it).
    rerender(<SessionsView active={false} />);
    viewport.scrollTop = 0;

    // Back: the remembered offset is restored pre-paint on the same node.
    rerender(<SessionsView active />);
    const restored = within(
      getByTestId("conversation-keepalive-worker-l4"),
    ).getByTestId("conversation-viewport");
    expect(restored).toBe(viewport);
    expect(restored.scrollTop).toBe(456);
    disconnectConversation("worker-l4");
  });

});
