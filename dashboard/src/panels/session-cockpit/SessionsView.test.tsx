// The sessions view shell: scaffold structure + the keyboard/palette
// foundation wired end-to-end under jsdom — zones resolved from real DOM markers, tinykeys at the
// window, cmdk palette pages, and the F6 focus cycle (design §5.3).
import {
  act,
  cleanup,
  fireEvent,
  render,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { sessionCockpitStore } from "../../data/sessionCockpitStore";
import { lifecycleNoticeStore } from "../../data/sessionLifecycle";
import { emptyProjection } from "../../data/conversation/reducer";
import {
  activeConversationStore,
  connectConversation,
  disconnectConversation,
} from "../../data/conversation/store";
import type {
  ActiveConversationRef,
  ActiveEventCursor,
  ConversationCapabilities,
  ConversationItem,
  ConversationPage,
  ConversationStatus,
} from "../../data/conversation/types";
import { fromTerminalSessionInfo, sessionStore } from "../../data/sessions";
import { dashboardStore } from "../../data/store";
import { capabilityEnvelope } from "../../test/fixtures/capabilityEnvelopes";
import {
  catalogRow,
  FLEET,
  L6_INTERACTION_FREETEXT,
} from "../../test/fixtures/catalogRows";
import type { LifecycleProjection } from "../../types/projection";
import { SessionsView } from "./SessionsView";

function seedReadyComposerSession() {
  const row = FLEET.find((candidate) => candidate.id === "architect")!;
  sessionStore.getState().hydrate([fromTerminalSessionInfo(row)]);
  sessionCockpitStore.setState({ focusedSessionId: null });
}

// A legacy-raw terminal (no controlState) keeps its interactive PTY as the primary
// stage body — the remaining home of the `pty` keyboard zone. The zone-contract tests target it.
function seedLegacyRawSession() {
  sessionStore
    .getState()
    .hydrate([
      fromTerminalSessionInfo(
        catalogRow({
          id: "legacy-raw",
          label: "legacy raw terminal",
          kind: "terminal",
          harness: undefined,
          seatRole: "terminal",
          status: "running",
        }),
      ),
    ]);
  sessionCockpitStore.setState({ focusedSessionId: "legacy-raw" });
}

// A projected conversation status seeded straight into the active-conversation
// store. The stage's own connect is neutralized by a never-resolving fetch stub in each such test,
// so the seeded stream phase stays exactly where the test puts it.
const L5Q_IDENTITY: ActiveConversationRef = {
  harnessId: "codex",
  vendorConversationId: "v",
  projectScope: "/r",
  identityDigest: "d",
  arSessionId: "worker-l4",
  bridgeEpoch: "e1",
};

function l5qStatus(turnState: ConversationStatus["turn"]["state"]): ConversationStatus {
  return {
    identity: L5Q_IDENTITY,
    revision: 1,
    observedAt: "2026-07-20T00:00:00Z",
    freshness: {
      state: "fresh",
      lastEvidenceAt: null,
      ageMs: null,
      staleAfterMs: 1,
      observationBound: "poll",
    },
    process: { state: "connected", generation: "g" },
    turn: { state: turnState, turnId: "turn-live-1", stateSince: null },
    evidence: { strength: "exact", origin: "codex" },
  };
}

function seedLiveProjection(sessionId: string, turnState: ConversationStatus["turn"]["state"]): void {
  activeConversationStore.setState({
    bySession: {
      [sessionId]: {
        ...emptyProjection(L5Q_IDENTITY),
        stream: "live",
        status: l5qStatus(turnState),
      },
    },
  });
}

/** fetch that never resolves: the stage's conversation connect/hydrate can never clobber a seeded projection. */
function stubHangingFetch(): void {
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
}

// xterm cannot mount under jsdom — the PtySurface's lazy
// Terminal resolves to this inert stand-in; the real terminal rules live in Terminal.tsx.
// The mount/unmount ledgers make a dispose/recreate observable at the composition level:
// Terminal.tsx's unmount cleanup is the socket dispose + xterm dispose, so an empty unmount
// ledger across a switch IS the no-dispose proof.
const mockTerminalMounts: string[] = [];
const mockTerminalUnmounts: string[] = [];
vi.mock("../Terminal", async () => {
  const { useEffect } = await import("react");
  return {
    Terminal: ({
      sessionId,
      readOnly,
    }: {
      sessionId: string;
      readOnly?: boolean;
    }) => {
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

afterEach(() => {
  cleanup();
  window.localStorage.clear(); // react-resizable-panels persists layout under autoSaveId
  sessionStore.getState().hydrate([]);
  sessionCockpitStore.setState({ focusedSessionId: null, perSession: {} });
  activeConversationStore.getState().reset();
  dashboardStore.setState({ lifecycles: {} });
  lifecycleNoticeStore.setState({
    residuals: [],
    cleanupOutcome: null,
    cleanupFailure: null,
    sweptRetire: {},
  });
  vi.unstubAllGlobals();
});

describe("scaffold structure (S2)", () => {
  it("renders the scope root + rail/stage/inspector with markers and zones (F-c: no statusline region)", () => {
    const { getByRole, getByTestId, queryByRole, queryByTestId } = render(
      <SessionsView active />,
    );
    const root = getByTestId("sessions-view");
    expect(root.getAttribute("data-view")).toBe("sessions"); // the WebTUI scope root (S1)
    expect(root.classList.contains("sessions--view")).toBe(true);

    expect(getByTestId("sessions-rail").getAttribute("data-region")).toBe(
      "rail",
    );
    expect(getByTestId("sessions-stage").getAttribute("data-region")).toBe(
      "stage",
    );
    expect(getByTestId("sessions-inspector").getAttribute("data-region")).toBe(
      "inspector",
    );
    // The StatusLine bar is unmounted and its F6 `statusline`
    // focus region is gone — the inspector/rail toggles moved into the stage header actions.
    expect(queryByTestId("sessions-statusline")).toBeNull();
    expect(getByTestId("sessions-toggle-inspector").closest("[data-testid='stage-header-actions']")).not.toBeNull();
    expect(
      getByTestId("sessions-toggle-inspector").getAttribute("aria-expanded"),
    ).toBe("false");
    expect(
      (getByTestId("sessions-inspector").parentElement as HTMLElement).style
        .flexGrow,
    ).toBe("0");
    expect(getByRole("separator", { name: "Resize chat rail" })).not.toBeNull();
    expect(queryByRole("separator", { name: "Resize inspector" })).toBeNull();
    const inspectorHandle = getByTestId("sessions-handle-inspector");
    expect(inspectorHandle.getAttribute("aria-hidden")).toBe("true");
    expect(inspectorHandle.getAttribute("tabindex")).toBe("-1");
    expect(
      inspectorHandle.getAttribute("data-panel-resize-handle-enabled"),
    ).toBe("false");

    // The empty stage no longer hosts a PTY placeholder (the structured body only
    // mounts for a focused session); it invites the operator to pick/launch a chat.
    expect(queryByTestId("sessions-pty-placeholder")).toBeNull();
    expect(getByTestId("stage-empty-identity")).not.toBeNull();
    expect(queryByTestId("session-composer")).toBeNull();
  });

  it("hides the ~80-col floor chip while the stage width is unmeasured (0 = hidden, never a false alarm)", () => {
    const { queryByTestId } = render(<SessionsView active />);
    expect(queryByTestId("sessions-pty-floor-chip")).toBeNull();
  });
});

// jsdom has no layout: pin an element's clientWidth so the measurement paths see real numbers.
function setClientWidth(element: Element, width: number) {
  Object.defineProperty(element, "clientWidth", {
    configurable: true,
    value: width,
  });
}

describe("~80-col floor chip re-measures on panel-layout changes (review round 2, finding 1)", () => {
  // Both failure paths go through PanelGroup onLayout — the trigger that fires on divider drags
  // AND on collapse/expand — with NO view-root resize involved (the root observer never fires).

  it("shows the chip when a layout change squeezes the stage below the floor (F1a: missed warning)", async () => {
    const { getByTestId, queryByTestId } = render(<SessionsView active />);
    setClientWidth(getByTestId("sessions-stage"), 500); // below the ~640px floor
    expect(queryByTestId("sessions-pty-floor-chip")).toBeNull(); // stale until a layout event — the old trap

    // A palette-driven inspector collapse is a panel-layout change with no root resize.
    fireEvent.keyDown(document.body, { key: "k", code: "KeyK", ctrlKey: true });
    const input = getByTestId("sessions-palette-input");
    fireEvent.change(input, { target: { value: "inspector" } });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" });

    await waitFor(() =>
      expect(queryByTestId("sessions-pty-floor-chip")).not.toBeNull(),
    );
  });

  it("clears a stale chip when a collapse widens the stage back over the floor (F1b: false alarm)", async () => {
    const { getByTestId, queryByTestId } = render(<SessionsView active />);
    const stage = getByTestId("sessions-stage");
    setClientWidth(stage, 500);
    fireEvent.keyDown(document.body, { key: "k", code: "KeyK", ctrlKey: true });
    const input = getByTestId("sessions-palette-input");
    fireEvent.change(input, { target: { value: "inspector" } });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" });
    await waitFor(() =>
      expect(queryByTestId("sessions-pty-floor-chip")).not.toBeNull(),
    );

    // The stage widens (e.g. the collapse freed the width); the next layout event must CLEAR it.
    setClientWidth(stage, 900);
    fireEvent.click(getByTestId("sessions-toggle-inspector"));
    await waitFor(() =>
      expect(queryByTestId("sessions-pty-floor-chip")).toBeNull(),
    );
  });
});

describe("~280px rail calibration on first real measurement (review round 2, finding 4)", () => {
  it("resizes the rail to the ~280px-equivalent percentage when no layout is persisted", async () => {
    const { getByTestId } = render(<SessionsView active />);
    setClientWidth(getByTestId("sessions-view"), 2560); // wide monitor: 22% would be ~563px
    // Any panel-layout event re-attempts the one-shot calibration with the now-real root width.
    fireEvent.keyDown(document.body, { key: "k", code: "KeyK", ctrlKey: true });
    const input = getByTestId("sessions-palette-input");
    fireEvent.change(input, { target: { value: "inspector" } });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" });

    // 280/2560 = 10.9% → clamped to the rail's 12% minimum; the Panel div carries size as flexGrow.
    const railPanel = getByTestId("sessions-rail").parentElement as HTMLElement;
    await waitFor(() => expect(railPanel.style.flexGrow).toBe("12"));
  });

  it("never overrides a persisted user layout", async () => {
    window.localStorage.setItem(
      "react-resizable-panels:cockpit.chats.panels",
      JSON.stringify({}),
    );
    const { getByTestId } = render(<SessionsView active />);
    setClientWidth(getByTestId("sessions-view"), 2560);
    fireEvent.keyDown(document.body, { key: "k", code: "KeyK", ctrlKey: true });
    const input = getByTestId("sessions-palette-input");
    fireEvent.change(input, { target: { value: "inspector" } });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" });

    const railPanel = getByTestId("sessions-rail").parentElement as HTMLElement;
    await waitFor(() =>
      expect(
        getByTestId("sessions-toggle-inspector").getAttribute("aria-expanded"),
      ).toBe("true"),
    );
    expect(railPanel.style.flexGrow).not.toBe("12"); // calibration skipped — persisted layout wins
  });
});

describe("command palette (S3)", () => {
  it("opens on ctrl+k from the chrome zone and closes on Escape, returning focus to the invoker", async () => {
    const { getByTestId, queryByTestId } = render(<SessionsView active />);
    // The statusline invoker is gone — the inspector toggle
    // now lives in the stage-header actions and is an ordinary chrome-zone control.
    const invoker = getByTestId("sessions-toggle-inspector");
    invoker.focus();

    fireEvent.keyDown(invoker, { key: "k", code: "KeyK", ctrlKey: true });
    const input = getByTestId("sessions-palette-input");
    await waitFor(() => expect(document.activeElement).toBe(input));

    fireEvent.keyDown(input, { key: "Escape", code: "Escape" });
    expect(queryByTestId("sessions-palette")).toBeNull();
    expect(document.activeElement).toBe(invoker); // close returns focus to the invoker
  });

  it("? opens the keyboard-reference page listing the real chord tables (one options source)", () => {
    const { getByTestId, getByText } = render(<SessionsView active />);
    fireEvent.keyDown(document.body, {
      key: "?",
      code: "Slash",
      shiftKey: true,
    });
    expect(getByTestId("sessions-palette")).not.toBeNull();
    // The reserved set renders from data/keymap — bindings and reference can never drift.
    expect(
      getByText("Terminal — everything passes through except exactly"),
    ).not.toBeNull();
    expect(getByText("ctrl+alt+pagedown")).not.toBeNull();
    expect(getByText("ctrl+;")).not.toBeNull();
  });

  it("? typed into the composer is passthrough (printable suppression, R7)", async () => {
    seedReadyComposerSession();
    const { findByTestId, queryByTestId } = render(<SessionsView active />);
    const composer = (
      await findByTestId("session-composer-editor")
    ).querySelector(".cm-content")!;
    fireEvent.keyDown(composer, {
      key: "?",
      code: "Slash",
      shiftKey: true,
    });
    expect(queryByTestId("sessions-palette")).toBeNull();
  });

  it("/ at the start of a composer line opens the palette pre-filtered (§5.2 composer rule)", async () => {
    seedReadyComposerSession();
    const { findByTestId, getByTestId } = render(<SessionsView active />);
    const composer = (
      await findByTestId("session-composer-editor")
    ).querySelector(".cm-content") as HTMLElement;
    composer.focus();
    fireEvent.keyDown(composer, { key: "/", code: "Slash" });
    expect(getByTestId("sessions-palette")).not.toBeNull();
    expect(
      (getByTestId("sessions-palette-input") as HTMLInputElement).value,
    ).toBe("/");
    expect(getByTestId("palette-cmd-session.launch")).not.toBeNull();
  });

  it("runs a palette command: toggling the rail surfaces the reopen affordance (R3 reopenable)", async () => {
    const { getByTestId, queryByTestId } = render(<SessionsView active />);
    fireEvent.keyDown(document.body, { key: "k", code: "KeyK", ctrlKey: true });
    const input = getByTestId("sessions-palette-input");
    fireEvent.change(input, { target: { value: "rail" } });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" });
    await waitFor(() =>
      expect(queryByTestId("sessions-reopen-rail")).not.toBeNull(),
    );
    expect(queryByTestId("sessions-palette")).toBeNull(); // action commands close the palette

    fireEvent.click(getByTestId("sessions-reopen-rail"));
    await waitFor(() =>
      expect(queryByTestId("sessions-reopen-rail")).toBeNull(),
    );
  });

  it("stays closed to keys while the view is the hidden keep-alive layer (active=false)", () => {
    const { queryByTestId } = render(<SessionsView active={false} />);
    fireEvent.keyDown(document.body, { key: "k", code: "KeyK", ctrlKey: true });
    expect(queryByTestId("sessions-palette")).toBeNull();
  });
});

describe("keyboard zones over the legacy-raw PTY (S4)", () => {
  it("ctrl+; (reserved) opens the palette from the pty zone", async () => {
    seedLegacyRawSession();
    const { getByTestId, findByTestId } = render(<SessionsView active />);
    const pty = await findByTestId("pty-surface");
    fireEvent.keyDown(pty, { key: ";", code: "Semicolon", ctrlKey: true });
    expect(getByTestId("sessions-palette")).not.toBeNull();
  });

  it("F6 from the pty zone exits to chrome (the stage header)", async () => {
    seedLegacyRawSession();
    const { getByTestId, findByTestId } = render(<SessionsView active />);
    const pty = await findByTestId("pty-surface");
    pty.focus();
    fireEvent.keyDown(pty, { key: "F6", code: "F6" });
    const header = getByTestId("sessions-stage").querySelector(
      "[data-stage-header]",
    );
    expect(document.activeElement).toBe(header);
  });

  it("unreserved keys over the pty zone are never intercepted (Esc, ctrl+k, plain keys)", async () => {
    seedLegacyRawSession();
    const { findByTestId, queryByTestId } = render(<SessionsView active />);
    const pty = await findByTestId("pty-surface");
    for (const init of [
      { key: "Escape", code: "Escape" },
      { key: "k", code: "KeyK", ctrlKey: true },
      { key: "a", code: "KeyA" },
      { key: "ArrowUp", code: "ArrowUp", altKey: true },
    ]) {
      const intercepted = !fireEvent.keyDown(pty, init); // false ⇔ preventDefault was called
      expect(intercepted, JSON.stringify(init)).toBe(false);
    }
    expect(queryByTestId("sessions-palette")).toBeNull();
  });
});

describe("focus model (S4, design §5.3)", () => {
  it("F6 skips the default-closed inspector, then includes it after deliberate reopen", async () => {
    seedReadyComposerSession();
    const { findByTestId, getByTestId } = render(<SessionsView active />);
    const composer = (
      await findByTestId("session-composer-editor")
    ).querySelector(".cm-content");
    const regionOf = (element: Element | null) =>
      element?.closest("[data-region]")?.getAttribute("data-region");

    fireEvent.keyDown(document.body, { key: "F6", code: "F6" });
    expect(regionOf(document.activeElement)).toBe("rail");
    fireEvent.keyDown(document.activeElement as Element, {
      key: "F6",
      code: "F6",
    });
    expect(regionOf(document.activeElement)).toBe("stage");
    expect(document.activeElement).toBe(composer);
    fireEvent.keyDown(document.activeElement as Element, {
      key: "F6",
      code: "F6",
    });
    // The statusline region is gone. With the inspector default-closed the
    // cycle is rail → stage → rail, so F6 from the stage skips the closed inspector and wraps to rail.
    expect(regionOf(document.activeElement)).toBe("rail");

    fireEvent.click(getByTestId("sessions-toggle-inspector"));
    await waitFor(() =>
      expect(
        getByTestId("sessions-toggle-inspector").getAttribute("aria-expanded"),
      ).toBe("true"),
    );
    (
      getByTestId("sessions-rail").querySelector(
        "[data-focus-target]",
      ) as HTMLElement
    ).focus();
    fireEvent.keyDown(document.activeElement as Element, {
      key: "F6",
      code: "F6",
    });
    expect(regionOf(document.activeElement)).toBe("stage");
    fireEvent.keyDown(document.activeElement as Element, {
      key: "F6",
      code: "F6",
    });
    expect(regionOf(document.activeElement)).toBe("inspector");
  });

  it("persists a deliberate inspector opt-in across a remount", async () => {
    const first = render(<SessionsView active />);
    fireEvent.click(first.getByTestId("sessions-toggle-inspector"));
    await waitFor(() =>
      expect(
        first
          .getByTestId("sessions-toggle-inspector")
          .getAttribute("aria-expanded"),
      ).toBe("true"),
    );
    first.unmount();

    const second = render(<SessionsView active />);
    expect(
      second
        .getByTestId("sessions-toggle-inspector")
        .getAttribute("aria-expanded"),
    ).toBe("true");
  });

  it("keeps deliberate inspector intent across responsive collapse, narrow reload, and recovery", async () => {
    let rootWidth = 1400;
    const observers: Array<{
      callback: ResizeObserverCallback;
      active: boolean;
    }> = [];
    vi.stubGlobal(
      "ResizeObserver",
      class {
        entry: { callback: ResizeObserverCallback; active: boolean };
        constructor(callback: ResizeObserverCallback) {
          this.entry = { callback, active: true };
          observers.push(this.entry);
        }
        observe() {}
        unobserve() {}
        disconnect() {
          this.entry.active = false;
        }
      },
    );
    const widthSpy = vi
      .spyOn(HTMLElement.prototype, "clientWidth", "get")
      .mockImplementation(function (this: HTMLElement) {
        if (this.getAttribute("data-testid") === "sessions-view")
          return rootWidth;
        if (this.getAttribute("data-testid") === "sessions-stage") return 900;
        return 0;
      });
    const resize = () => {
      act(() => {
        for (const observer of observers) {
          if (observer.active) observer.callback([], {} as ResizeObserver);
        }
      });
    };

    const first = render(<SessionsView active />);
    fireEvent.click(first.getByTestId("sessions-toggle-inspector"));
    await waitFor(() =>
      expect(
        first
          .getByTestId("sessions-toggle-inspector")
          .getAttribute("aria-expanded"),
      ).toBe("true"),
    );
    expect(window.localStorage.getItem("cockpit.chats.inspector-open.v1")).toBe(
      "1",
    );

    rootWidth = 1000;
    resize();
    await waitFor(() =>
      expect(
        first
          .getByTestId("sessions-toggle-inspector")
          .getAttribute("aria-expanded"),
      ).toBe("false"),
    );
    expect(window.localStorage.getItem("cockpit.chats.inspector-open.v1")).toBe(
      "1",
    );
    first.unmount();

    const second = render(<SessionsView active />);
    await waitFor(() =>
      expect(
        second
          .getByTestId("sessions-toggle-inspector")
          .getAttribute("aria-expanded"),
      ).toBe("false"),
    );
    expect(window.localStorage.getItem("cockpit.chats.inspector-open.v1")).toBe(
      "1",
    );

    rootWidth = 1400;
    resize();
    await waitFor(() =>
      expect(
        second
          .getByTestId("sessions-toggle-inspector")
          .getAttribute("aria-expanded"),
      ).toBe("true"),
    );
    expect(
      second.getByRole("separator", { name: "Resize inspector" }),
    ).not.toBeNull();

    rootWidth = 1000;
    resize();
    await waitFor(() =>
      expect(
        second
          .getByTestId("sessions-toggle-inspector")
          .getAttribute("aria-expanded"),
      ).toBe("false"),
    );
    // Reopenable below the threshold, then a deliberate close cancels width recovery.
    fireEvent.click(second.getByTestId("sessions-toggle-inspector"));
    await waitFor(() =>
      expect(
        second
          .getByTestId("sessions-toggle-inspector")
          .getAttribute("aria-expanded"),
      ).toBe("true"),
    );
    fireEvent.click(second.getByTestId("sessions-toggle-inspector"));
    await waitFor(() =>
      expect(
        second
          .getByTestId("sessions-toggle-inspector")
          .getAttribute("aria-expanded"),
      ).toBe("false"),
    );
    expect(window.localStorage.getItem("cockpit.chats.inspector-open.v1")).toBe(
      "0",
    );
    rootWidth = 1400;
    resize();
    expect(
      second
        .getByTestId("sessions-toggle-inspector")
        .getAttribute("aria-expanded"),
    ).toBe("false");
    widthSpy.mockRestore();
  });

  it("palette collapse restores an inspector invoker to the visible toggle", async () => {
    const { getByTestId } = render(<SessionsView active />);
    fireEvent.click(getByTestId("sessions-toggle-inspector"));
    await waitFor(() =>
      expect(
        getByTestId("sessions-toggle-inspector").getAttribute("aria-expanded"),
      ).toBe("true"),
    );
    const invoker = getByTestId("sessions-inspector").querySelector(
      "[data-focus-target]",
    ) as HTMLElement;
    invoker.focus();
    fireEvent.keyDown(invoker, { key: "k", code: "KeyK", ctrlKey: true });
    const input = getByTestId("sessions-palette-input");
    fireEvent.change(input, { target: { value: "inspector" } });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" });

    await waitFor(() =>
      expect(
        getByTestId("sessions-toggle-inspector").getAttribute("aria-expanded"),
      ).toBe("false"),
    );
    expect(document.activeElement).toBe(
      getByTestId("sessions-toggle-inspector"),
    );
  });

  it("Shift+F6 cycles backward", async () => {
    // With the statusline region removed the cycle is rail → stage →
    // inspector. Land on the stage composer, then step BACKWARD to the rail (the previous region).
    seedReadyComposerSession();
    const { findByTestId } = render(<SessionsView active />);
    const composer = (
      await findByTestId("session-composer-editor")
    ).querySelector(".cm-content") as HTMLElement;
    composer.focus();
    fireEvent.keyDown(composer, {
      key: "F6",
      code: "F6",
      shiftKey: true,
    });
    expect(
      document.activeElement
        ?.closest("[data-region]")
        ?.getAttribute("data-region"),
    ).toBe("rail");
  });

  it("Esc from the composer lands on the stage header", async () => {
    seedReadyComposerSession();
    const { findByTestId, getByTestId } = render(<SessionsView active />);
    const composer = (
      await findByTestId("session-composer-editor")
    ).querySelector(".cm-content") as HTMLElement;
    composer.focus();
    fireEvent.keyDown(composer, { key: "Escape", code: "Escape" });
    const header = getByTestId("sessions-stage").querySelector(
      "[data-stage-header]",
    );
    expect(document.activeElement).toBe(header);
  });
});

describe("S5 legacy duty parity", () => {
  it("launches a raw terminal with selected-lifecycle inheritance, then focuses and persists it", async () => {
    const id = "00000000-0000-4000-8000-000000000005";
    vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValue(id);
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          session: id,
          label: "Terminal 1",
          kind: "terminal",
          lifecycleId: "LC-S5",
          leafKey: null,
          seatRole: "terminal",
          status: "running",
        }),
        { status: 200 },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { getByTestId } = render(
      <SessionsView active selectedLifecycleId="LC-S5" />,
    );

    fireEvent.click(getByTestId("chats-new-terminal"));

    await waitFor(() =>
      expect(sessionCockpitStore.getState().focusedSessionId).toBe(id),
    );
    expect(sessionStore.getState().activeId).toBe(id);
    expect(
      window.localStorage.getItem("ar-dashboard:last-active-chat-session"),
    ).toBe(id);
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/terminal/${id}`,
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          kind: "terminal",
          label: "Terminal 1",
          lifecycleId: "LC-S5",
        }),
      }),
    );
  });

  it("restores an explicitly persisted live chat instead of replacing it with smart-default focus", async () => {
    window.localStorage.setItem(
      "ar-dashboard:last-active-chat-session",
      "worker-l4",
    );
    sessionStore
      .getState()
      .hydrate(FLEET.map(fromTerminalSessionInfo), "worker-l4");
    sessionCockpitStore.setState({ focusedSessionId: null });

    render(<SessionsView active />);

    await waitFor(() =>
      expect(sessionCockpitStore.getState().focusedSessionId).toBe("worker-l4"),
    );
    expect(
      window.localStorage.getItem("ar-dashboard:last-active-chat-session"),
    ).toBe("worker-l4");
  });

  it("does not let a catalog hydrate steal focus from a deliberately inspected landed row", async () => {
    const rows = FLEET.map(fromTerminalSessionInfo);
    sessionStore.getState().hydrate(rows);
    sessionCockpitStore.setState({ focusedSessionId: null });
    render(<SessionsView active />);
    await waitFor(() =>
      expect(sessionCockpitStore.getState().focusedSessionId).toBe(
        "worker-tui",
      ),
    );

    const landed = rows.find((session) => session.status === "landed");
    expect(landed).toBeDefined();
    act(() => sessionCockpitStore.getState().setFocusedSession(landed!.id));
    expect(sessionCockpitStore.getState().focusedSessionId).toBe(landed!.id);
    expect(sessionStore.getState().activeId).toBe("worker-tui");
    expect(
      window.localStorage.getItem("ar-dashboard:last-active-chat-session"),
    ).toBe("worker-tui");
    act(() =>
      sessionStore
        .getState()
        .hydrate(
          rows,
          window.localStorage.getItem("ar-dashboard:last-active-chat-session"),
        ),
    );

    await waitFor(() =>
      expect(sessionCockpitStore.getState().focusedSessionId).toBe(landed!.id),
    );
    expect(sessionStore.getState().activeId).toBe("worker-tui");
    expect(
      window.localStorage.getItem("ar-dashboard:last-active-chat-session"),
    ).toBe("worker-tui");
  });
});

describe("smart-default focus + handoff + session cycling (L2: R9, F17)", () => {
  beforeEach(() => {
    sessionStore.getState().hydrate(FLEET.map(fromTerminalSessionInfo));
    sessionCockpitStore.setState({ focusedSessionId: null });
  });

  it("view entry focuses the awaiting-input seat first — never an empty landing (R9)", async () => {
    const { getByTestId } = render(<SessionsView active />);
    await waitFor(() =>
      expect(
        getByTestId("rail-row-worker-tui").getAttribute("data-selected"),
      ).toBe("true"),
    );
    // The stage shows the focused seat's HeaderStrip.
    expect(getByTestId("header-strip").textContent).toContain(
      "worker-tui-shell",
    );
  });

  it("hands focus off with a note when the FOCUSED seat lands under us (F17)", async () => {
    const { getByTestId, queryByTestId } = render(<SessionsView active />);
    await waitFor(() =>
      expect(sessionCockpitStore.getState().focusedSessionId).toBe(
        "worker-tui",
      ),
    );
    fireEvent.click(getByTestId("rail-row-worker-l4"));
    await waitFor(() =>
      expect(
        getByTestId("rail-row-worker-l4").getAttribute("data-selected"),
      ).toBe("true"),
    );
    expect(queryByTestId("stage-handoff-note")).toBeNull();

    act(() => {
      sessionStore.getState().patch("worker-l4", {
        status: "landed",
        landedReason: "leaf integrated",
      });
    });
    await waitFor(() =>
      expect(queryByTestId("stage-handoff-note")).not.toBeNull(),
    );
    expect(queryByTestId("stage-handoff-note")?.textContent).toContain(
      "leaf integrated",
    );
    // Focus moved by the smart-default priority (awaiting-input first).
    expect(sessionCockpitStore.getState().focusedSessionId).toBe("worker-tui");
  });

  it("alt+↓ / alt+↑ cycle the rail order from the chrome zone", async () => {
    render(<SessionsView active />);
    await waitFor(() =>
      expect(sessionCockpitStore.getState().focusedSessionId).toBe(
        "worker-tui",
      ),
    );
    fireEvent.keyDown(document.body, {
      key: "ArrowDown",
      code: "ArrowDown",
      altKey: true,
    });
    expect(sessionCockpitStore.getState().focusedSessionId).toBe("scout");
    fireEvent.keyDown(document.body, {
      key: "ArrowUp",
      code: "ArrowUp",
      altKey: true,
    });
    expect(sessionCockpitStore.getState().focusedSessionId).toBe("worker-tui");
  });
});

describe("authoritative landed cleanup through rail and palette callers (F5-S5-2)", () => {
  it("surfaces a sprint network failure with exact targets, then preserves partial success truth", async () => {
    const rows = [
      catalogRow({
        id: "cleanup-live",
        label: "live operator",
        controlState: "ready",
        turnState: "working",
      }),
      catalogRow({ id: "cleanup-a", label: "landed alpha", status: "landed" }),
      catalogRow({ id: "cleanup-b", label: "landed beta", status: "landed" }),
    ];
    sessionStore.getState().hydrate(rows.map(fromTerminalSessionInfo));
    sessionCockpitStore.setState({ focusedSessionId: null });
    const cleanupCalls: string[][] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        const target = String(url);
        if (target.includes("landed-cleanup")) {
          const ids = (
            JSON.parse(String(init?.body)) as { sessionIds: string[] }
          ).sessionIds;
          cleanupCalls.push(ids);
          if (cleanupCalls.length === 1)
            throw new Error("network disconnected");
          return {
            ok: true,
            json: async () => ({
              closed: 1,
              skipped: 1,
              closedSessions: ["cleanup-a"],
              skippedSessions: [
                { session: "cleanup-b", reason: "status:running" },
              ],
            }),
          } as Response;
        }
        if (target.includes("/api/terminal/sessions")) {
          return {
            ok: true,
            json: async () => ({ sessions: rows }),
          } as Response;
        }
        return { ok: true, json: async () => ({}) } as Response;
      }),
    );
    const { findByTestId, getByTestId, queryByTestId } = render(
      <SessionsView active />,
    );
    await waitFor(() =>
      expect(sessionCockpitStore.getState().focusedSessionId).toBe(
        "cleanup-live",
      ),
    );

    fireEvent.click(getByTestId("rail-bulk-sprint"));
    fireEvent.click(getByTestId("rail-bulk-execute"));
    const failure = await findByTestId("landed-cleanup-failure");
    expect(failure.getAttribute("role")).toBe("alert");
    expect(failure.textContent).toContain("landed alpha (cleanup-a)");
    expect(failure.textContent).toContain("landed beta (cleanup-b)");
    expect(cleanupCalls).toEqual([["cleanup-a", "cleanup-b"]]);

    fireEvent.click(getByTestId("landed-cleanup-retry"));
    const outcome = await findByTestId("landed-cleanup-outcome");
    expect(outcome.textContent).toContain(
      "ended 1 · skipped 1 (cleanup-b: status:running)",
    );
    expect(cleanupCalls).toEqual([
      ["cleanup-a", "cleanup-b"],
      ["cleanup-a", "cleanup-b"],
    ]);
    await waitFor(() => expect(queryByTestId("rail-row-cleanup-a")).toBeNull());
    expect(getByTestId("rail-row-cleanup-b")).not.toBeNull();
  });

  it("keeps a focused landed row on palette failure, then hands it to the smart live default after retry", async () => {
    const master = "repo/cleanup-master";
    const rows = [
      catalogRow({
        id: "cleanup-smart-live",
        label: "live awaiting input",
        controlState: "ready",
        turnState: "awaiting-input",
        controlPendingInteraction: { prompt: "continue?" },
      }),
      catalogRow({
        id: "cleanup-focused-landed",
        label: "focused landed row",
        status: "landed",
        leafKey: `${master}/leaf-1`,
        landedReason: "leaf integrated",
      }),
    ];
    sessionStore.getState().hydrate(rows.map(fromTerminalSessionInfo));
    sessionCockpitStore.setState({ focusedSessionId: null });
    let cleanupAttempt = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        const target = String(url);
        if (target.includes("landed-cleanup")) {
          cleanupAttempt += 1;
          if (cleanupAttempt === 1) {
            return { ok: false, status: 503 } as Response;
          }
          return {
            ok: true,
            json: async () => ({
              closed: 1,
              skipped: 0,
              closedSessions: ["cleanup-focused-landed"],
              skippedSessions: [],
            }),
          } as Response;
        }
        if (target.includes("/api/terminal/sessions")) {
          return {
            ok: true,
            json: async () => ({ sessions: rows }),
          } as Response;
        }
        return { ok: true, json: async () => ({}) } as Response;
      }),
    );
    const { findByTestId, getByTestId } = render(<SessionsView active />);
    await waitFor(() =>
      expect(sessionCockpitStore.getState().focusedSessionId).toBe(
        "cleanup-smart-live",
      ),
    );
    // A controlled seat's live body is now the structured ConversationSurface, not
    // the PTY, so this test asserts the focus-handoff subject only; conversation keep-alive across
    // focus is the LRU'd active-conversation store (covered by its own store tests).
    fireEvent.click(getByTestId(`rail-done-toggle-${master}`));
    fireEvent.click(getByTestId("rail-row-cleanup-focused-landed"));
    await waitFor(() =>
      expect(sessionCockpitStore.getState().focusedSessionId).toBe(
        "cleanup-focused-landed",
      ),
    );
    expect(sessionStore.getState().activeId).toBe("cleanup-smart-live");

    fireEvent.keyDown(getByTestId("rail-row-cleanup-focused-landed"), {
      key: "k",
      code: "KeyK",
      ctrlKey: true,
    });
    fireEvent.click(
      await findByTestId(`palette-cmd-sessions.endDone.${master}`),
    );
    await findByTestId("landed-cleanup-failure");
    expect(sessionCockpitStore.getState().focusedSessionId).toBe(
      "cleanup-focused-landed",
    );
    expect(getByTestId("rail-row-cleanup-focused-landed")).not.toBeNull();

    fireEvent.click(getByTestId("landed-cleanup-retry"));
    await findByTestId("landed-cleanup-outcome");
    await waitFor(() =>
      expect(sessionCockpitStore.getState().focusedSessionId).toBe(
        "cleanup-smart-live",
      ),
    );
    expect(sessionStore.getState().activeId).toBe("cleanup-smart-live");
    expect(
      window.localStorage.getItem("ar-dashboard:last-active-chat-session"),
    ).toBe("cleanup-smart-live");
    expect(getByTestId("stage-handoff-note").textContent).toContain(
      "focus handed off",
    );
  });
});

describe("L6: stage surface, WorkingLine, InteractionBar, stop residuals", () => {
  beforeEach(() => {
    sessionStore.getState().hydrate(FLEET.map(fromTerminalSessionInfo));
    sessionCockpitStore.setState({ focusedSessionId: null });
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
    const items: ConversationItem[] = Array.from({ length: count }, (_, index) => ({
      itemId: `worker-l4-item-${index + 1}`,
      globalOrdinal: index + 1,
      revision: 1,
      lane: "harness",
      source: "harness-live",
      provenance: { strength: "exact", origin: "codex" },
      role: "assistant",
      kind: "message",
      phase: "completed",
      blocks: [
        {
          blockId: `worker-l4-item-${index + 1}-b`,
          type: "markdown",
          markdown: `message ${index + 1}`,
        },
      ],
    })) as unknown as ConversationItem[];
    const page: ConversationPage = {
      identity: L5Q_IDENTITY,
      items,
      page: { olderCursor: null, hasOlder: false },
      eventCursor: "evt-0" as ActiveEventCursor,
      hydrationId: "h",
      status: l5qStatus("ready"),
      capabilities: undefined as unknown as ConversationCapabilities,
    };
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

  it("renders the InteractionBar on the interaction axis: above the composer, never replacing it", async () => {
    const { findByTestId, getByTestId } = render(<SessionsView active />);
    await waitFor(() =>
      expect(sessionCockpitStore.getState().focusedSessionId).toBe(
        "worker-tui",
      ),
    );
    const bar = await findByTestId("interaction-bar");
    const composer = getByTestId("session-composer-editor");
    expect(composer).not.toBeNull(); // the composer is never replaced
    // DOCUMENT_POSITION_FOLLOWING = 4: the composer renders after (below) the bar.
    expect(bar.compareDocumentPosition(composer) & 4).toBe(4);
    expect(getByTestId("interaction-bar-prompt").textContent).toContain(
      "harness_control_api",
    );
  });

  it("routes the focused shared composer answer once through the gate and never /submit", async () => {
    const session = fromTerminalSessionInfo({
      ...L6_INTERACTION_FREETEXT,
      id: "sessions-answer",
      lifecycleId: "lc-sessions-answer",
      controlPendingInteraction: {
        ...L6_INTERACTION_FREETEXT.controlPendingInteraction,
        interactionId: "ix-sessions-answer",
      },
    });
    sessionStore.getState().hydrate([session]);
    sessionCockpitStore.setState({ focusedSessionId: null, perSession: {} });
    dashboardStore.setState({
      lifecycles: {
        "lc-sessions-answer": {
          id: "lc-sessions-answer",
          gate: {
            id: "gate-sessions-answer",
            kind: "agent-question",
            state: "open",
            decisions: [],
            ts: "2026-07-17T09:00:00Z",
            packet: {
              adapterInteraction: {
                sessionId: session.id,
                interactionId: "ix-sessions-answer",
              },
            },
          },
        } as unknown as LifecycleProjection,
      },
    });
    const urls: string[] = [];
    let release: (response: Response) => void = () => {};
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        urls.push(url);
        if (url === "/api/actions/approve") {
          return new Promise<Response>((resolve) => (release = resolve));
        }
        return {
          ok: false,
          status: 503,
          json: async () => ({
            status: "control-unavailable",
            detail: "test background fetch",
          }),
        } as Response;
      }),
    );
    const { findByTestId } = render(<SessionsView active />);
    await findByTestId("session-composer-answer-mode");
    act(() =>
      sessionCockpitStore
        .getState()
        .setComposerDraft(session.id, "use ar/base"),
    );
    const send = await findByTestId("session-composer-send");
    fireEvent.click(send);
    fireEvent.click(send);
    await waitFor(() =>
      expect(urls.filter((url) => url === "/api/actions/approve")).toHaveLength(
        1,
      ),
    );
    expect(urls.some((url) => url.endsWith("/submit"))).toBe(false);
    release({ status: 202, text: async () => "" } as Response);
    await waitFor(() =>
      expect(
        sessionCockpitStore.getState().perSession[session.id]?.interactionAnswer
          ?.answeredAt,
      ).toBeDefined(),
    );
  });

  it("records a retired seat's stop residual in the lifecycle store — informational, never a failure — with NO stacked DOM notice (F-f ruling)", async () => {
    const { getByTestId, queryByTestId } = render(<SessionsView active />);
    await waitFor(() =>
      expect(sessionCockpitStore.getState().focusedSessionId).toBe(
        "worker-tui",
      ),
    );
    fireEvent.click(getByTestId("rail-row-worker-l4"));
    await waitFor(() =>
      expect(
        getByTestId("rail-row-worker-l4").getAttribute("data-selected"),
      ).toBe("true"),
    );
    act(() => {
      sessionStore.getState().patch("worker-l4", {
        status: "terminated",
        retiredAt: "2026-07-17T10:00:00Z",
        retiredReason: "seat superseded",
        controlRaw: {
          retireControlStopError: "control command queue is stopped",
        },
      });
    });
    // StopResidualNotes is unmounted from SessionsView. The lifecycle
    // store still RECORDS the residual (the never-silently-discarded guarantee), so assert the
    // store state instead of DOM. The residual is a `retire` note — never a "termination failed".
    await waitFor(() =>
      expect(
        lifecycleNoticeStore
          .getState()
          .residuals.some((residual) => residual.sessionId === "worker-l4"),
      ).toBe(true),
    );
    const residual = lifecycleNoticeStore
      .getState()
      .residuals.find((entry) => entry.sessionId === "worker-l4")!;
    expect(residual.kind).toBe("retire");
    expect(residual.detail).toBe("control command queue is stopped");
    expect(residual.detail.toLowerCase()).not.toContain("fail");
    // No stop-residual DOM surface renders any more.
    expect(queryByTestId("stop-residual-worker-l4")).toBeNull();
    // The focus-handoff note still renders (role=status, visually hidden — do not assert visibility).
    expect(queryByTestId("stage-handoff-note")?.getAttribute("role")).toBe(
      "status",
    );
  });

  it("hides the conversation.stop palette command when no turn is interruptible — no phantom 'unavailable' entry (L4 F2)", async () => {
    // Without a live conversation projection there is no resolvable working turn, so the real
    // `conversation.stop` command is simply absent (never a stale disabled/unavailable placeholder).
    const { getByTestId, queryByTestId, queryByText } = render(<SessionsView active />);
    await waitFor(() =>
      expect(sessionCockpitStore.getState().focusedSessionId).toBe("worker-tui"),
    );
    fireEvent.click(getByTestId("rail-row-worker-l4"));
    fireEvent.keyDown(document.body, { key: "k", code: "KeyK", ctrlKey: true });
    const input = getByTestId("sessions-palette-input");
    fireEvent.change(input, { target: { value: "stop turn" } });
    expect(queryByTestId("palette-cmd-conversation.stop")).toBeNull();
    expect(queryByText(/Stop turn — unavailable/)).toBeNull();
  });

  it("Stop-turn gates on the WorkingLine's OWN grammar state — never offered when the line is absent (review finding 3)", async () => {
    const { getByTestId, queryByText, queryByTestId } = render(
      <SessionsView active />,
    );
    await waitFor(() =>
      expect(sessionCockpitStore.getState().focusedSessionId).toBe(
        "worker-tui",
      ),
    );
    fireEvent.click(getByTestId("rail-row-worker-l4"));
    await waitFor(() => expect(queryByTestId("working-line")).not.toBeNull());
    // A pending interaction yields the grammar to awaiting-input: the WorkingLine unmounts...
    act(() => {
      sessionStore.getState().patch("worker-l4", {
        controlPendingInteraction: {
          interactionId: "ix-stop-gate",
          kind: "input",
          prompt: "?",
        },
      });
    });
    await waitFor(() => expect(queryByTestId("working-line")).toBeNull());
    // ...so the palette must not offer a stop control that is not interruptible.
    fireEvent.keyDown(document.body, { key: "k", code: "KeyK", ctrlKey: true });
    const input = getByTestId("sessions-palette-input");
    fireEvent.change(input, { target: { value: "stop turn" } });
    expect(queryByText(/Stop turn — unavailable/)).toBeNull();
    expect(queryByTestId("palette-cmd-conversation.stop")).toBeNull();
  });

  it("on a live stream the line and the stop read the SAME projection evidence — a sweep-lagged catalog patch cannot unmount them (L5Q)", async () => {
    stubHangingFetch();
    const { getByTestId, queryByTestId } = render(<SessionsView active />);
    await waitFor(() =>
      expect(sessionCockpitStore.getState().focusedSessionId).toBe(
        "worker-tui",
      ),
    );
    fireEvent.click(getByTestId("rail-row-worker-l4"));
    act(() => seedLiveProjection("worker-l4", "working"));
    await waitFor(() => expect(queryByTestId("working-line")).not.toBeNull());
    // Working + a resolvable turn id on the live wire: the welded stop is actionable —
    // hosted in the composer beside send, never on the line.
    expect(queryByTestId("working-line-stop")).toBeNull();
    expect((getByTestId("session-composer-stop") as HTMLButtonElement).disabled).toBe(false);
    // The catalog's sweep-lagged duplicate of the same interaction must not unmount the fresher
    // SSE-driven line (the catalog-only line would yield to awaiting-input).
    act(() => {
      sessionStore.getState().patch("worker-l4", {
        controlPendingInteraction: {
          interactionId: "ix-stale",
          kind: "input",
          prompt: "?",
        },
      });
    });
    expect(queryByTestId("working-line")).not.toBeNull();
    // Only the projection's OWN state change ends turn theater — line and stop leave together.
    act(() => {
      const current = activeConversationStore.getState().bySession["worker-l4"];
      activeConversationStore.setState({
        bySession: { "worker-l4": { ...current, status: l5qStatus("ready") } },
      });
    });
    await waitFor(() => expect(queryByTestId("working-line")).toBeNull());
  });

  it("captures an UNFOCUSED seat's retire residual in the store — never silently discarded (review F1, sev-3)", async () => {
    const { queryByTestId } = render(<SessionsView active />);
    await waitFor(() =>
      expect(sessionCockpitStore.getState().focusedSessionId).toBe(
        "worker-tui",
      ),
    );
    // worker-caps was never focused; another actor retires it with a failed graceful stop.
    act(() => {
      sessionStore.getState().patch("worker-caps", {
        status: "terminated",
        retiredAt: "2026-07-17T11:00:00Z",
        retiredReason: "seat superseded",
        controlRaw: {
          retireControlStopError: "control command queue is stopped",
        },
      });
    });
    // The residual is RECORDED in the lifecycle store by the focus-independent sweep
    // even though StopResidualNotes no longer renders any DOM surface — assert the store state.
    await waitFor(() =>
      expect(
        lifecycleNoticeStore
          .getState()
          .residuals.some((residual) => residual.sessionId === "worker-caps"),
      ).toBe(true),
    );
    const residual = lifecycleNoticeStore
      .getState()
      .residuals.find((entry) => entry.sessionId === "worker-caps")!;
    expect(residual.kind).toBe("retire");
    expect(residual.detail.toLowerCase()).not.toContain("fail");
    expect(queryByTestId("stop-residual-worker-caps")).toBeNull();
    // No handoff fired — focus never touched this seat.
    expect(sessionCockpitStore.getState().focusedSessionId).toBe("worker-tui");
    expect(queryByTestId("stage-handoff-note")).toBeNull();
  });
});

describe("launch flow + failed-launch banner integration (L3: R5, R6)", () => {
  beforeEach(() => {
    sessionStore.getState().hydrate(FLEET.map(fromTerminalSessionInfo));
    sessionCockpitStore.setState({ focusedSessionId: null });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        const body =
          url === "/api/harnesses"
            ? {
                harnesses: [
                  { id: "claude", name: "Claude Code", detected: true },
                ],
              }
            : url.startsWith("/api/harnesses/claude/capabilities")
              ? capabilityEnvelope("claude", "hit")
              : { sessions: [] };
        return { ok: true, status: 200, json: async () => body } as Response;
      }),
    );
  });

  afterEach(() => vi.unstubAllGlobals());

  it("the palette lists 'Launch session…' and running it opens the flow", async () => {
    const { getByTestId, findByTestId } = render(<SessionsView active />);
    fireEvent.keyDown(document.body, { key: "k", code: "KeyK", ctrlKey: true });
    const input = getByTestId("sessions-palette-input");
    fireEvent.change(input, { target: { value: "launch session" } });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" });
    expect(await findByTestId("launch-flow")).not.toBeNull();
  });

  it("a focused FAILED seat renders the refusal banner; 'Launch corrected…' opens the flow pre-filled", async () => {
    const { getByTestId, findByTestId, queryByTestId } = render(
      <SessionsView active />,
    );
    await waitFor(() =>
      expect(sessionCockpitStore.getState().focusedSessionId).toBe(
        "worker-tui",
      ),
    );
    expect(queryByTestId("failed-launch-banner")).toBeNull(); // never on a healthy seat
    fireEvent.click(getByTestId("rail-row-scout"));
    const banner = await findByTestId("failed-launch-banner");
    expect(banner.textContent).toContain(
      'requested model "ar-unknown-model" is absent from the dynamic catalog',
    ); // the FLEET scout bridgeError, verbatim
    fireEvent.click(getByTestId("failed-launch-correct"));
    const flow = await findByTestId("launch-flow");
    // the failed seat's harness is pre-selected; the catalog is fetched live
    await waitFor(() =>
      expect(
        flow
          .querySelector("[data-testid='launch-harness-claude']")
          ?.getAttribute("aria-pressed"),
      ).toBe("true"),
    );
  });
});

// ── harness↔terminal archetype switches keep the whole PTY stack alive ──────────────
// A prior glitch: switching from a harness seat to a terminal seat (and back)
// redrew and rescrolled the terminal. Root cause: the stage rendered EITHER PtySurface OR the
// conversation pool — the archetype switch UNMOUNTED the entire PTY stack (Terminal unmount ⇒
// conn.dispose socket teardown + xterm dispose), and the return paid full boot. The fix keeps
// BOTH layers mounted as persistent siblings (keptHidden: visibility + aria-hidden). These tests
// pin the contract end-to-end through the real view (rail clicks, real stores): the terminal's
// xterm instance + connection are never disposed/recreated, the hidden layer is out of the
// a11y/zone/focus contract, and the rail-click focus lands in the VISIBLE layer only.
describe("B1: harness↔terminal keeps the PTY stack alive", () => {
  beforeEach(() => {
    mockTerminalMounts.length = 0;
    mockTerminalUnmounts.length = 0;
    sessionStore.getState().hydrate([
      ...FLEET.map(fromTerminalSessionInfo),
      fromTerminalSessionInfo(
        catalogRow({
          id: "raw-term",
          label: "raw terminal seat",
          kind: "terminal",
          harness: undefined,
          seatRole: "terminal",
          status: "running",
        }),
      ),
    ]);
    sessionCockpitStore.setState({ focusedSessionId: null });
    // No conversation backend here: the harness seats' epoch resolve fails quietly (bounded
    // window), the composition contract is what's under test.
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () => ({ ok: false, status: 503, json: async () => ({}) }) as Response,
      ),
    );
  });

  it("harness → terminal → harness → terminal: no dispose, no recreate — only layer visibility flips", async () => {
    const { findByTestId, getByTestId, queryByTestId } = render(
      <SessionsView active />,
    );
    await waitFor(() =>
      expect(sessionCockpitStore.getState().focusedSessionId).toBe("worker-tui"),
    );
    // Harness only: no PTY layer at all — lazy-on-first-terminal-focus, composer present.
    expect(queryByTestId("pty-layer")).toBeNull();
    expect(getByTestId("session-composer")).not.toBeNull();

    // → terminal: the layer mounts; the composer leaves (the one honest box change that the
    // terminal's ResizeObserver refits to — no mount cascade).
    fireEvent.click(getByTestId("rail-row-raw-term"));
    await waitFor(() =>
      expect(sessionCockpitStore.getState().focusedSessionId).toBe("raw-term"),
    );
    const terminalNode = await findByTestId("mock-terminal-raw-term");
    expect(queryByTestId("session-composer")).toBeNull();
    expect(getByTestId("pty-layer").getAttribute("aria-hidden")).toBeNull();
    expect(getByTestId("conversation-layer").getAttribute("aria-hidden")).toBe(
      "true",
    );

    // → harness: THE FIX — xterm instance + connection survive the archetype switch.
    fireEvent.click(getByTestId("rail-row-worker-tui"));
    await waitFor(() =>
      expect(sessionCockpitStore.getState().focusedSessionId).toBe("worker-tui"),
    );
    expect(mockTerminalUnmounts).toEqual([]); // no dispose (Terminal's cleanup is the socket teardown)
    expect(getByTestId("mock-terminal-raw-term")).toBe(terminalNode); // no recreate — same node
    expect(getByTestId("pty-layer").getAttribute("aria-hidden")).toBe("true");
    expect(getByTestId("conversation-layer").getAttribute("aria-hidden")).toBeNull();
    expect(getByTestId("session-composer")).not.toBeNull();
    // The hidden layer dropped out of the pty zone/focus contract…
    expect(getByTestId("pty-surface").getAttribute("data-kbzone")).toBeNull();
    expect(getByTestId("pty-surface").getAttribute("data-focus-target")).toBeNull();

    // → terminal again: the SAME stack re-shows. One mount across the whole round trip.
    fireEvent.click(getByTestId("rail-row-raw-term"));
    await waitFor(() =>
      expect(sessionCockpitStore.getState().focusedSessionId).toBe("raw-term"),
    );
    expect(getByTestId("mock-terminal-raw-term")).toBe(terminalNode);
    expect(mockTerminalMounts.filter((id) => id === "raw-term")).toHaveLength(1);
    expect(mockTerminalUnmounts).toEqual([]);
    expect(getByTestId("pty-layer").getAttribute("aria-hidden")).toBeNull();
    expect(getByTestId("pty-surface").getAttribute("data-kbzone")).toBe("pty");
  });

  it("the F-l rail-click focus lands in the VISIBLE layer only — the hidden terminal can't steal it", async () => {
    const { findByTestId, getByTestId } = render(<SessionsView active />);
    await waitFor(() =>
      expect(sessionCockpitStore.getState().focusedSessionId).toBe("worker-tui"),
    );
    // Visit the terminal so the (later hidden) layer exists; the raw seat has no composer, so the
    // focus lands inside the PTY layer itself.
    fireEvent.click(getByTestId("rail-row-raw-term"));
    await waitFor(() =>
      expect(sessionCockpitStore.getState().focusedSessionId).toBe("raw-term"),
    );
    await findByTestId("mock-terminal-raw-term");
    await waitFor(() =>
      expect(
        (document.activeElement as HTMLElement | null)?.closest(
          "[data-testid='pty-layer']",
        ),
      ).not.toBeNull(),
    );

    // Back to the harness seat: the deferred rail-click focus must land in the composer — never
    // inside the hidden PTY stack.
    fireEvent.click(getByTestId("rail-row-worker-tui"));
    await waitFor(() =>
      expect(sessionCockpitStore.getState().focusedSessionId).toBe("worker-tui"),
    );
    const composer = (
      await findByTestId("session-composer-editor")
    ).querySelector(".cm-content");
    await waitFor(() => expect(document.activeElement).toBe(composer));
    expect(
      (document.activeElement as HTMLElement | null)?.closest(
        "[data-testid='pty-layer']",
      ),
    ).toBeNull();
  });
});
