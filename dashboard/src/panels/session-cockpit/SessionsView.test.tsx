// The sessions view shell (260715-FEUI-L1 S2–S5): scaffold structure + the keyboard/palette
// foundation wired end-to-end under jsdom — zones resolved from real DOM markers, tinykeys at the
// window, cmdk palette pages, and the F6 focus cycle (design §5.3).
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { SessionsView } from "./SessionsView";

afterEach(() => {
  cleanup();
  window.localStorage.clear(); // react-resizable-panels persists layout under autoSaveId
});

describe("scaffold structure (S2)", () => {
  it("renders the scope root + rail/stage/inspector/statusline with markers and zones", () => {
    const { getByTestId } = render(<SessionsView active />);
    const root = getByTestId("sessions-view");
    expect(root.getAttribute("data-view")).toBe("sessions"); // the WebTUI scope root (S1)
    expect(root.classList.contains("sessions--view")).toBe(true);

    expect(getByTestId("sessions-rail").getAttribute("data-region")).toBe("rail");
    expect(getByTestId("sessions-stage").getAttribute("data-region")).toBe("stage");
    expect(getByTestId("sessions-inspector").getAttribute("data-region")).toBe("inspector");
    expect(getByTestId("sessions-statusline").getAttribute("data-region")).toBe("statusline");

    // The keyboard-zone markers the zone contract resolves against.
    expect(getByTestId("sessions-pty-placeholder").getAttribute("data-kbzone")).toBe("pty");
    expect(getByTestId("sessions-composer-placeholder").getAttribute("data-kbzone")).toBe(
      "composer",
    );
  });

  it("hides the ~80-col floor chip while the stage width is unmeasured (0 = hidden, never a false alarm)", () => {
    const { queryByTestId } = render(<SessionsView active />);
    expect(queryByTestId("sessions-pty-floor-chip")).toBeNull();
  });
});

// jsdom has no layout: pin an element's clientWidth so the measurement paths see real numbers.
function setClientWidth(element: Element, width: number) {
  Object.defineProperty(element, "clientWidth", { configurable: true, value: width });
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

    await waitFor(() => expect(queryByTestId("sessions-pty-floor-chip")).not.toBeNull());
  });

  it("clears a stale chip when a collapse widens the stage back over the floor (F1b: false alarm)", async () => {
    const { getByTestId, queryByTestId } = render(<SessionsView active />);
    const stage = getByTestId("sessions-stage");
    setClientWidth(stage, 500);
    fireEvent.keyDown(document.body, { key: "k", code: "KeyK", ctrlKey: true });
    const input = getByTestId("sessions-palette-input");
    fireEvent.change(input, { target: { value: "inspector" } });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" });
    await waitFor(() => expect(queryByTestId("sessions-pty-floor-chip")).not.toBeNull());

    // The stage widens (e.g. the collapse freed the width); the next layout event must CLEAR it.
    setClientWidth(stage, 900);
    fireEvent.click(getByTestId("sessions-reopen-inspector"));
    await waitFor(() => expect(queryByTestId("sessions-pty-floor-chip")).toBeNull());
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
      "react-resizable-panels:cockpit.sessions.panels",
      JSON.stringify({}),
    );
    const { getByTestId } = render(<SessionsView active />);
    setClientWidth(getByTestId("sessions-view"), 2560);
    fireEvent.keyDown(document.body, { key: "k", code: "KeyK", ctrlKey: true });
    const input = getByTestId("sessions-palette-input");
    fireEvent.change(input, { target: { value: "inspector" } });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" });

    const railPanel = getByTestId("sessions-rail").parentElement as HTMLElement;
    await waitFor(() => expect(getByTestId("sessions-reopen-inspector")).not.toBeNull());
    expect(railPanel.style.flexGrow).not.toBe("12"); // calibration skipped — persisted layout wins
  });
});

describe("command palette (S3)", () => {
  it("opens on ctrl+k from the chrome zone and closes on Escape, returning focus to the invoker", async () => {
    const { getByTestId, queryByTestId } = render(<SessionsView active />);
    const invoker = getByTestId("sessions-statusline").querySelector(
      "[data-focus-target]",
    ) as HTMLElement;
    invoker.focus();

    fireEvent.keyDown(invoker, { key: "k", code: "KeyK", ctrlKey: true });
    const input = getByTestId("sessions-palette-input");
    await waitFor(() => expect(document.activeElement).toBe(input));

    fireEvent.keyDown(input, { key: "Escape", code: "Escape" });
    expect(queryByTestId("sessions-palette")).toBeNull();
    expect(document.activeElement).toBe(invoker); // R7: close returns focus to the invoker
  });

  it("? opens the keyboard-reference page listing the real chord tables (one options source)", () => {
    const { getByTestId, getByText } = render(<SessionsView active />);
    fireEvent.keyDown(document.body, { key: "?", code: "Slash", shiftKey: true });
    expect(getByTestId("sessions-palette")).not.toBeNull();
    // The reserved set renders from data/keymap — bindings and reference can never drift.
    expect(getByText("Terminal — everything passes through except exactly")).not.toBeNull();
    expect(getByText("ctrl+alt+pagedown")).not.toBeNull();
    expect(getByText("ctrl+;")).not.toBeNull();
  });

  it("? typed into the composer is passthrough (printable suppression, R7)", () => {
    const { getByTestId, queryByTestId } = render(<SessionsView active />);
    fireEvent.keyDown(getByTestId("sessions-composer-placeholder"), {
      key: "?",
      code: "Slash",
      shiftKey: true,
    });
    expect(queryByTestId("sessions-palette")).toBeNull();
  });

  it("/ at the start of a composer line opens the palette (§5.2 composer rule)", () => {
    const { getByTestId } = render(<SessionsView active />);
    const composer = getByTestId("sessions-composer-placeholder") as HTMLTextAreaElement;
    composer.focus();
    fireEvent.keyDown(composer, { key: "/", code: "Slash" });
    expect(getByTestId("sessions-palette")).not.toBeNull();
  });

  it("runs a palette command: toggling the rail surfaces the reopen affordance (R3 reopenable)", async () => {
    const { getByTestId, queryByTestId } = render(<SessionsView active />);
    fireEvent.keyDown(document.body, { key: "k", code: "KeyK", ctrlKey: true });
    const input = getByTestId("sessions-palette-input");
    fireEvent.change(input, { target: { value: "rail" } });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" });
    await waitFor(() => expect(queryByTestId("sessions-reopen-rail")).not.toBeNull());
    expect(queryByTestId("sessions-palette")).toBeNull(); // action commands close the palette

    fireEvent.click(getByTestId("sessions-reopen-rail"));
    await waitFor(() => expect(queryByTestId("sessions-reopen-rail")).toBeNull());
  });

  it("stays closed to keys while the view is the hidden keep-alive layer (active=false)", () => {
    const { queryByTestId } = render(<SessionsView active={false} />);
    fireEvent.keyDown(document.body, { key: "k", code: "KeyK", ctrlKey: true });
    expect(queryByTestId("sessions-palette")).toBeNull();
  });
});

describe("keyboard zones over the PTY placeholder (S4)", () => {
  it("ctrl+; (reserved) opens the palette from the pty zone", () => {
    const { getByTestId } = render(<SessionsView active />);
    const pty = getByTestId("sessions-pty-placeholder");
    fireEvent.keyDown(pty, { key: ";", code: "Semicolon", ctrlKey: true });
    expect(getByTestId("sessions-palette")).not.toBeNull();
  });

  it("F6 from the pty zone exits to chrome (the stage header)", () => {
    const { getByTestId } = render(<SessionsView active />);
    const pty = getByTestId("sessions-pty-placeholder");
    pty.focus();
    fireEvent.keyDown(pty, { key: "F6", code: "F6" });
    const header = getByTestId("sessions-stage").querySelector("[data-stage-header]");
    expect(document.activeElement).toBe(header);
  });

  it("unreserved keys over the pty zone are never intercepted (Esc, ctrl+k, plain keys)", () => {
    const { getByTestId, queryByTestId } = render(<SessionsView active />);
    const pty = getByTestId("sessions-pty-placeholder");
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
  it("F6 cycles rail → stage(composer) → inspector → statusline → rail", () => {
    const { getByTestId } = render(<SessionsView active />);
    const regionOf = (element: Element | null) =>
      element?.closest("[data-region]")?.getAttribute("data-region");

    fireEvent.keyDown(document.body, { key: "F6", code: "F6" });
    expect(regionOf(document.activeElement)).toBe("rail");
    fireEvent.keyDown(document.activeElement as Element, { key: "F6", code: "F6" });
    expect(regionOf(document.activeElement)).toBe("stage");
    expect(document.activeElement).toBe(getByTestId("sessions-composer-placeholder"));
    fireEvent.keyDown(document.activeElement as Element, { key: "F6", code: "F6" });
    expect(regionOf(document.activeElement)).toBe("inspector");
    fireEvent.keyDown(document.activeElement as Element, { key: "F6", code: "F6" });
    expect(regionOf(document.activeElement)).toBe("statusline");
    fireEvent.keyDown(document.activeElement as Element, { key: "F6", code: "F6" });
    expect(regionOf(document.activeElement)).toBe("rail");
  });

  it("Shift+F6 cycles backward", () => {
    const { getByTestId } = render(<SessionsView active />);
    (getByTestId("sessions-rail").querySelector("[data-focus-target]") as HTMLElement).focus();
    fireEvent.keyDown(document.activeElement as Element, { key: "F6", code: "F6", shiftKey: true });
    expect(document.activeElement?.closest("[data-region]")?.getAttribute("data-region")).toBe(
      "statusline",
    );
  });

  it("Esc from the composer lands on the stage header", () => {
    const { getByTestId } = render(<SessionsView active />);
    const composer = getByTestId("sessions-composer-placeholder");
    composer.focus();
    fireEvent.keyDown(composer, { key: "Escape", code: "Escape" });
    const header = getByTestId("sessions-stage").querySelector("[data-stage-header]");
    expect(document.activeElement).toBe(header);
  });
});
