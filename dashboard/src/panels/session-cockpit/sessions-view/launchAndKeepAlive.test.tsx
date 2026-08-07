import { fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fromTerminalSessionInfo, sessionStore } from "../../../data/sessions";
import { sessionCockpitStore } from "../../../data/sessionCockpitStore";
import { capabilityEnvelope } from "../../../test/fixtures/capabilityEnvelopes";
import { catalogRow, FLEET } from "../../../test/fixtures/catalogRows";
import { SessionsView } from "./SessionsView";
// Registers the shared afterEach (store/localStorage reset) for this split file.
import "./test-utils";

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

import {
  mockTerminalMounts,
  mockTerminalUnmounts,
} from "./test-utils";

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
    expect(mockTerminalMounts.filter((id: string) => id === "raw-term")).toHaveLength(1);
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
