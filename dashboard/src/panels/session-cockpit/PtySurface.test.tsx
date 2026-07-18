// The PtySurface (260715-FEUI-L6 R1/R2/R3): archetype honesty, keep-alive layers, accessible
// pane names, the opt-in screen-reader toggle, reserved slots. xterm stays OUT of jsdom — the
// Terminal module is mocked; the real terminal behavior is Terminal.tsx's (unchanged rules).
import { cleanup, render, fireEvent, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ptyHarvestStore } from "../../data/ptyHarvest";
import { fromTerminalSessionInfo, sessionStore } from "../../data/sessions";
import {
  L6_CONTROLLED_WORKING,
  L6_LEGACY_RAW,
} from "../../test/fixtures/catalogRows";
import { PtySurface, PTY_RENDERER } from "./PtySurface";

vi.mock("../Terminal", () => ({
  Terminal: (props: {
    sessionId: string;
    readOnly?: boolean;
    renderer?: string;
    screenReaderMode?: boolean;
    ariaLabel?: string;
    hooks?: unknown;
    keyEventFilter?: unknown;
  }) => (
    <div
      data-testid={`mock-terminal-${props.sessionId}`}
      data-read-only={String(props.readOnly ?? false)}
      data-renderer={props.renderer}
      data-screen-reader={String(props.screenReaderMode ?? false)}
      data-has-hooks={props.hooks ? "true" : "false"}
      data-has-key-filter={props.keyEventFilter ? "true" : "false"}
      aria-label={props.ariaLabel}
    />
  ),
}));

const controlled = () => fromTerminalSessionInfo(L6_CONTROLLED_WORKING);
const raw = () => fromTerminalSessionInfo(L6_LEGACY_RAW);

beforeEach(() => {
  sessionStore.getState().hydrate([controlled(), raw()]);
  ptyHarvestStore.setState({ bySession: {} });
});

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  sessionStore.getState().hydrate([]);
});

describe("two archetypes (R1)", () => {
  it("controlled panes are labeled as the runner line-log and get NO harvesting hooks", async () => {
    const { findByTestId, getByTestId } = render(
      <PtySurface focused={controlled()} />,
    );
    expect(getByTestId("pty-archetype-note").textContent).toContain(
      "runner line-log",
    );
    const pane = await findByTestId("mock-terminal-l6-controlled");
    expect(pane.getAttribute("data-has-hooks")).toBe("false");
    expect(
      getByTestId("pty-layer-l6-controlled").getAttribute("data-pty-archetype"),
    ).toBe("controlled");
  });

  it("legacy raw panes host the vendor TUI and DO get harvesting hooks", async () => {
    const { findByTestId, getByTestId } = render(
      <PtySurface focused={raw()} />,
    );
    expect(getByTestId("pty-archetype-note").textContent).toContain(
      "vendor TUI",
    );
    const pane = await findByTestId("mock-terminal-l6-raw-vendor");
    expect(pane.getAttribute("data-has-hooks")).toBe("true");
    expect(
      getByTestId("pty-layer-l6-raw-vendor").getAttribute("data-pty-archetype"),
    ).toBe("legacy-raw");
  });

  it("passes the measured renderer decision through to every pane", async () => {
    const { findByTestId } = render(<PtySurface focused={controlled()} />);
    const pane = await findByTestId("mock-terminal-l6-controlled");
    expect(pane.getAttribute("data-renderer")).toBe(PTY_RENDERER);
  });
});

describe("keep-alive layers (Chats' pattern preserved)", () => {
  it("a previously focused pane stays mounted (hidden) across a focus switch", async () => {
    const { findByTestId, getByTestId, rerender } = render(
      <PtySurface focused={controlled()} />,
    );
    await findByTestId("mock-terminal-l6-controlled");
    rerender(<PtySurface focused={raw()} />);
    await findByTestId("mock-terminal-l6-raw-vendor");
    // Both layers exist; only the focused one is visible.
    const controlledLayer = getByTestId("pty-layer-l6-controlled");
    expect(controlledLayer.style.display).toBe("none");
    expect(controlledLayer.getAttribute("aria-hidden")).toBe("true");
    const rawLayer = getByTestId("pty-layer-l6-raw-vendor");
    expect(rawLayer.style.display).toBe("flex");
    expect(rawLayer.getAttribute("data-pty-visible")).toBe("true");
  });

  it("keeps the exact visited terminal node across a transient removed-focus gap", async () => {
    const { findByTestId, getByTestId, rerender } = render(
      <PtySurface focused={controlled()} />,
    );
    const original = await findByTestId("mock-terminal-l6-controlled");
    original.setAttribute("data-scrollback-sentinel", "kept-through-handoff");

    // A removed focused row is absent for one render before SessionsView's smart handoff effect.
    // The PTY owner must survive that gap instead of disposing every visited terminal/socket.
    rerender(<PtySurface focused={undefined} />);
    expect(getByTestId("sessions-pty-placeholder")).not.toBeNull();
    expect(getByTestId("mock-terminal-l6-controlled")).toBe(original);
    expect(getByTestId("pty-layer-l6-controlled").style.display).toBe("none");

    rerender(<PtySurface focused={controlled()} />);
    const restored = getByTestId("mock-terminal-l6-controlled");
    expect(restored).toBe(original);
    expect(restored.getAttribute("data-scrollback-sentinel")).toBe(
      "kept-through-handoff",
    );
    expect(getByTestId("pty-layer-l6-controlled").style.display).toBe("flex");
  });

  it("landed panes render read-only", async () => {
    const landed = fromTerminalSessionInfo({
      ...L6_CONTROLLED_WORKING,
      id: "l6-landed",
      status: "landed",
    });
    sessionStore.getState().hydrate([landed]);
    const { findByTestId } = render(<PtySurface focused={landed} />);
    const pane = await findByTestId("mock-terminal-l6-landed");
    expect(pane.getAttribute("data-read-only")).toBe("true");
  });

  it("renders an explicit ended state without sacrificing a previously mounted landed pane", async () => {
    const landed = fromTerminalSessionInfo({
      ...L6_CONTROLLED_WORKING,
      id: "l6-landed",
      label: "landed inspection",
      status: "landed",
    });
    const exited = fromTerminalSessionInfo({
      ...L6_CONTROLLED_WORKING,
      id: "l6-exited",
      label: "exited chat",
      status: "exited",
      exitEvidence: "tmux-command-failed",
    });
    sessionStore.getState().hydrate([landed, exited]);
    const { findByTestId, getByTestId, queryByTestId, rerender } = render(
      <PtySurface focused={landed} />,
    );
    expect(
      (await findByTestId("mock-terminal-l6-landed")).getAttribute(
        "data-read-only",
      ),
    ).toBe("true");

    rerender(<PtySurface focused={exited} />);
    const ended = getByTestId("sessions-ended-state");
    expect(ended.textContent).toContain("Chat ended");
    expect(ended.textContent).toContain("exited chat · exited");
    expect(ended.textContent).toContain("tmux-command-failed");
    expect(queryByTestId("pty-pane-chrome")).toBeNull();
    expect(queryByTestId("mock-terminal-l6-exited")).toBeNull();
    expect(getByTestId("pty-surface").getAttribute("data-kbzone")).toBeNull();
    expect(getByTestId("pty-layer-l6-landed").style.display).toBe("none");
  });
});

describe("accessibility (R2) + reserved slots (R3)", () => {
  it("every pane carries the accessible name: label + harness + state", async () => {
    const { findByTestId } = render(<PtySurface focused={controlled()} />);
    const pane = await findByTestId("mock-terminal-l6-controlled");
    const label = pane.getAttribute("aria-label") ?? "";
    expect(label).toContain("worker-l6-controlled");
    expect(label).toContain("claude");
    expect(label).toContain("working");
  });

  it("screen-reader mode is a discoverable opt-in toggle with the perf cost named", async () => {
    const { findByTestId, getByTestId } = render(
      <PtySurface focused={controlled()} />,
    );
    const toggle = getByTestId("pty-screen-reader-toggle");
    expect(toggle.getAttribute("aria-pressed")).toBe("false");
    expect(toggle.getAttribute("title")).toContain("performance");
    let pane = await findByTestId("mock-terminal-l6-controlled");
    expect(pane.getAttribute("data-screen-reader")).toBe("false");
    fireEvent.click(toggle);
    await waitFor(async () => {
      pane = await findByTestId("mock-terminal-l6-controlled");
      expect(pane.getAttribute("data-screen-reader")).toBe("true");
    });
    // Persisted per user (the calm-cockpit/localStorage idiom).
    expect(
      window.localStorage.getItem("cockpit.sessions.screen-reader-mode"),
    ).toBe("1");
  });

  it("honors the reserved scrollback-paused badge slot (empty until the server fields land)", () => {
    const { getByTestId } = render(<PtySurface focused={controlled()} />);
    const slot = getByTestId("pty-scrollback-badge-slot");
    expect(slot.getAttribute("data-slot")).toBe("scrollback-paused-badge");
    expect(slot.textContent).toBe(""); // never faked
  });

  it("passes the reserved-chord key filter to every pane (clipboard chords stay unbound)", async () => {
    const { findByTestId } = render(<PtySurface focused={controlled()} />);
    const pane = await findByTestId("mock-terminal-l6-controlled");
    expect(pane.getAttribute("data-has-key-filter")).toBe("true");
  });
});

describe("bell acknowledgment (R7)", () => {
  it("focusing a seat acknowledges its pending bell", () => {
    ptyHarvestStore.getState().recordBell("l6-raw-vendor", Date.now());
    render(<PtySurface focused={raw()} />);
    expect(
      ptyHarvestStore.getState().bySession["l6-raw-vendor"].bellPending,
    ).toBe(false);
  });
});
