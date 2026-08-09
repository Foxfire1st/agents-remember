import {
  act,
  fireEvent,
  render,
  waitFor,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  activeConversationStore,
} from "../../../data/conversation/store";
import { fromTerminalSessionInfo, sessionStore } from "../../../data/sessions";
import { lifecycleNoticeStore } from "../../../data/sessionLifecycle";
import { sessionCockpitStore } from "../../../data/sessionCockpitStore";
import {
  FLEET,
  L6_INTERACTION_FREETEXT,
} from "../../../test/fixtures/catalogRows";
import { SessionsView } from "./SessionsView";
import {
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

describe("L6: stage surface — InteractionBar and stop residuals", () => {
  beforeEach(() => {
    sessionStore.getState().hydrate(FLEET.map(fromTerminalSessionInfo));
    sessionCockpitStore.setState({ focusedSessionId: null });
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

  it("routes the focused lifecycle-free composer answer once by exact session and never /submit", async () => {
    const session = fromTerminalSessionInfo({
      ...L6_INTERACTION_FREETEXT,
      id: "sessions-answer",
      lifecycleId: undefined,
      controlPendingInteraction: {
        ...L6_INTERACTION_FREETEXT.controlPendingInteraction,
        interactionId: "ix-sessions-answer",
      },
    });
    sessionStore.getState().hydrate([session]);
    sessionCockpitStore.setState({ focusedSessionId: null, perSession: {} });
    const urls: string[] = [];
    const responseBodies: Record<string, unknown>[] = [];
    let release: (response: Response) => void = () => {};
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        urls.push(url);
        if (url.endsWith("/submission-authority")) {
          return {
            ok: true,
            json: async () => ({ bridgeEpoch: "bridge-sessions-answer" }),
          } as Response;
        }
        if (url === "/api/terminal/sessions-answer/interaction-response") {
          responseBodies.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
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
    await waitFor(() => expect(responseBodies).toHaveLength(1));
    expect(responseBodies[0]).toEqual({
      interactionId: "ix-sessions-answer",
      expectedBridgeEpoch: "bridge-sessions-answer",
      response: "use ar/base",
    });
    expect(urls.some((url) => url.endsWith("/submit"))).toBe(false);
    release({ ok: true, status: 200, json: async () => ({ status: "accepted" }) } as Response);
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
