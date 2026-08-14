// The PtySurface: archetype honesty, keep-alive layers, accessible
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
    plainTextSelection?: boolean;
  }) => (
    <div
      data-testid={`mock-terminal-${props.sessionId}`}
      data-read-only={String(props.readOnly ?? false)}
      data-renderer={props.renderer}
      data-screen-reader={String(props.screenReaderMode ?? false)}
      data-has-hooks={props.hooks ? "true" : "false"}
      data-has-key-filter={props.keyEventFilter ? "true" : "false"}
      data-plain-text-selection={String(props.plainTextSelection ?? false)}
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
    // To declutter, the pane-chrome bar is gone — the archetype fact now
    // lives in the screen-reader toggle's title (+ the Inspector), not a `pty-archetype-note`.
    expect(
      getByTestId("pty-screen-reader-toggle").getAttribute("title"),
    ).toContain("runner line-log");
    const pane = await findByTestId("mock-terminal-l6-controlled");
    expect(pane.getAttribute("data-has-hooks")).toBe("false");
    expect(pane.getAttribute("data-plain-text-selection")).toBe("false");
    expect(
      getByTestId("pty-layer-l6-controlled").getAttribute("data-pty-archetype"),
    ).toBe("controlled");
  });

  it("legacy raw panes host the vendor TUI and DO get harvesting hooks", async () => {
    const { findByTestId, getByTestId } = render(
      <PtySurface focused={raw()} />,
    );
    // Archetype copy re-anchored off the removed `pty-archetype-note` onto the toggle title.
    expect(
      getByTestId("pty-screen-reader-toggle").getAttribute("title"),
    ).toContain("vendor TUI");
    const pane = await findByTestId("mock-terminal-l6-raw-vendor");
    expect(pane.getAttribute("data-has-hooks")).toBe("true");
    expect(pane.getAttribute("data-plain-text-selection")).toBe("true");
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

  it("no longer renders the reserved scrollback-paused badge slot (F-c ruling: pane chrome removed)", () => {
    // To declutter, the pane-chrome bar — and its reserved badge slot — is
    // gone; when the server exposes copy-mode state the badge will re-land elsewhere.
    const { queryByTestId } = render(<PtySurface focused={controlled()} />);
    expect(queryByTestId("pty-scrollback-badge-slot")).toBeNull();
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

// ── Hidden layer contract ──────────────────────────────────────────────────────────────────────
// The Chats stage keeps the PTY layer MOUNTED but hidden (keptHidden: visibility + aria-hidden)
// while a harness seat has the stage, so the terminal's xterm/socket/scrollback survive
// harness↔terminal switches. The hidden layer must drop every focus/zone affordance — the
// rail-click focus and the Focus-terminal command resolve by `data-focus-target` /
// `[data-kbzone="pty"]`, and those may only ever match the VISIBLE layer. The panes themselves
// are untouched: hidden is a layer concern, never a pane teardown.
describe("hidden layer contract (260723 B1)", () => {
  it("keeps the pane mounted while dropping the zone + focus affordances", async () => {
    const { findByTestId, getByTestId, rerender } = render(
      <PtySurface focused={raw()} />,
    );
    const pane = await findByTestId("mock-terminal-l6-raw-vendor");
    expect(getByTestId("pty-surface").getAttribute("data-kbzone")).toBe("pty");

    rerender(<PtySurface focused={raw()} hidden />);
    // The pane (xterm + socket) survives untouched — the same node, still the visible pane inside.
    expect(getByTestId("mock-terminal-l6-raw-vendor")).toBe(pane);
    expect(getByTestId("pty-layer-l6-raw-vendor").style.display).toBe("flex");
    // …but the layer leaves the pty zone so no keyboard/focus route can land here.
    expect(getByTestId("pty-surface").getAttribute("data-kbzone")).toBeNull();
    expect(getByTestId("pty-surface").getAttribute("data-focus-target")).toBeNull();

    rerender(<PtySurface focused={raw()} />);
    expect(getByTestId("pty-surface").getAttribute("data-kbzone")).toBe("pty");
  });

  it("suppresses a landed pane's focus target while hidden", async () => {
    const landed = fromTerminalSessionInfo({
      ...L6_LEGACY_RAW,
      id: "l6-landed-raw",
      status: "landed",
    });
    sessionStore.getState().hydrate([landed]);
    const { findByTestId, getByTestId, rerender } = render(
      <PtySurface focused={landed} />,
    );
    await findByTestId("mock-terminal-l6-landed-raw");
    expect(getByTestId("pty-surface").getAttribute("data-focus-target")).toBe(
      "true",
    );

    rerender(<PtySurface focused={landed} hidden />);
    expect(getByTestId("pty-surface").getAttribute("data-focus-target")).toBeNull();
    expect(getByTestId("pty-surface").getAttribute("data-kbzone")).toBeNull();
    expect(getByTestId("mock-terminal-l6-landed-raw")).not.toBeNull();
  });

  it("skips the ended state (a data-focus-target carrier) while hidden", () => {
    const exited = fromTerminalSessionInfo({
      ...L6_LEGACY_RAW,
      id: "l6-exited-raw",
      status: "exited",
      exitEvidence: "tmux-command-failed",
    });
    sessionStore.getState().hydrate([exited]);
    const { getByTestId, queryByTestId, rerender } = render(
      <PtySurface focused={exited} />,
    );
    expect(getByTestId("sessions-ended-state")).not.toBeNull();

    rerender(<PtySurface focused={exited} hidden />);
    expect(queryByTestId("sessions-ended-state")).toBeNull();

    rerender(<PtySurface focused={exited} />);
    expect(getByTestId("sessions-ended-state")).not.toBeNull();
  });

  it("suppresses the placeholder's zone marker while hidden", () => {
    const { getByTestId, rerender } = render(<PtySurface focused={undefined} />);
    expect(
      getByTestId("sessions-pty-placeholder").getAttribute("data-kbzone"),
    ).toBe("pty");

    rerender(<PtySurface focused={undefined} hidden />);
    expect(
      getByTestId("sessions-pty-placeholder").getAttribute("data-kbzone"),
    ).toBeNull();
  });

  it("a seat that was never focused mounts no pane (lazy-on-first-focus, B1 preserved)", async () => {
    // Both seats are live in the store; only the focused one may own a PTY. Eagerly mounting
    // every seat's socket is exactly what the mountedIds keep-alive contract forbids.
    const { findByTestId, queryByTestId } = render(
      <PtySurface focused={controlled()} />,
    );
    await findByTestId("mock-terminal-l6-controlled");
    expect(queryByTestId("mock-terminal-l6-raw-vendor")).toBeNull();
  });
});
