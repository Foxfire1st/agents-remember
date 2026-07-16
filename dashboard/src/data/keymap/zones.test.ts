// The keyboard-zone routing contract (260715-FEUI-L1 S4/S5) — the PTY pass-through invariants
// are the load-bearing ones: a hosted harness must receive EVERY key except exactly the bound
// reserved set, and bare Esc is never claimed over a live PTY (Claude Code owns Esc Esc).
import { describe, expect, it } from "vitest";

import {
  BROWSER_FORBIDDEN,
  matchReservedChord,
  PTY_RESERVED,
  type KeyEventLike,
} from "./reserved";
import {
  isEditableTarget,
  isPrintable,
  routeKey,
  slashOpensPalette,
  zoneForTarget,
  type ZoneElementLike,
} from "./zones";

function key(over: Partial<KeyEventLike> & Pick<KeyEventLike, "key">): KeyEventLike {
  return { ctrlKey: false, altKey: false, shiftKey: false, metaKey: false, ...over };
}

function el(zone: string | null, over: Partial<ZoneElementLike> = {}): ZoneElementLike {
  return {
    closest: (selector: string) =>
      selector === "[data-kbzone]" && zone !== null
        ? { getAttribute: (name: string) => (name === "data-kbzone" ? zone : null) }
        : null,
    ...over,
  };
}

describe("zoneForTarget", () => {
  it("resolves pty and composer containers and defaults everything else to chrome", () => {
    expect(zoneForTarget(el("pty"))).toBe("pty");
    expect(zoneForTarget(el("composer"))).toBe("composer");
    expect(zoneForTarget(el("bogus"))).toBe("chrome");
    expect(zoneForTarget(el(null))).toBe("chrome");
    expect(zoneForTarget(null)).toBe("chrome");
  });
});

describe("PTY zone: everything passes through except exactly the bound reserved set", () => {
  it("passes bare Esc — and any Esc chord — to the hosted harness (Claude owns Esc Esc)", () => {
    expect(routeKey("pty", key({ key: "Escape" }))).toBe("passthrough");
    expect(routeKey("pty", key({ key: "Escape", shiftKey: true }))).toBe("passthrough");
  });

  it("passes harness-owned chords through (Codex alt+↑/alt+,/alt+. · Pi ctrl+alt+] · plain keys)", () => {
    expect(routeKey("pty", key({ key: "ArrowUp", altKey: true }))).toBe("passthrough");
    expect(routeKey("pty", key({ key: ",", altKey: true }))).toBe("passthrough");
    expect(routeKey("pty", key({ key: ".", altKey: true }))).toBe("passthrough");
    expect(routeKey("pty", key({ key: "]", ctrlKey: true, altKey: true }))).toBe("passthrough");
    expect(routeKey("pty", key({ key: "c", ctrlKey: true }))).toBe("passthrough"); // \x03 interrupt
    expect(routeKey("pty", key({ key: "k", ctrlKey: true }))).toBe("passthrough"); // ctrl+k is chrome-only
    expect(routeKey("pty", key({ key: "a" }))).toBe("passthrough");
  });

  it("handles exactly the bound reserved chords", () => {
    expect(routeKey("pty", key({ key: ";", ctrlKey: true }))).toBe("handle"); // palette
    expect(routeKey("pty", key({ key: "F6" }))).toBe("handle"); // exit to chrome
    expect(routeKey("pty", key({ key: "PageUp", ctrlKey: true, altKey: true }))).toBe("handle");
    expect(routeKey("pty", key({ key: "PageDown", ctrlKey: true, altKey: true }))).toBe("handle");
  });

  it("matches Ctrl+; by physical code too (layout robustness)", () => {
    expect(matchReservedChord(key({ key: "ö", code: "Semicolon", ctrlKey: true }))?.action).toBe(
      "palette.open",
    );
  });

  it("never intercepts the unbound clipboard reservations (they stay with the pane until L6)", () => {
    expect(routeKey("pty", key({ key: "C", ctrlKey: true, shiftKey: true }))).toBe("passthrough");
    const unbound = PTY_RESERVED.filter((chord) => !chord.bound);
    expect(unbound.map((chord) => chord.action)).toEqual([
      "clipboard.copySelection",
      "clipboard.copy",
    ]);
  });
});

describe("reserved-set hygiene (R5/R6)", () => {
  it("claims no bare-Esc sequence and no browser-forbidden chord", () => {
    for (const chord of PTY_RESERVED) {
      expect(chord.chord.toLowerCase()).not.toMatch(/(^|\+| )esc/);
      expect(BROWSER_FORBIDDEN).not.toContain(chord.chord.toLowerCase());
    }
  });

  it("carries a full five-source verification record on every reserved chord", () => {
    for (const chord of PTY_RESERVED) {
      const sources = chord.verifiedAgainst.map((entry) => entry.source).sort();
      expect(sources, chord.chord).toEqual(["chrome", "claude", "codex", "firefox", "pi"]);
    }
  });

  it("every BOUND chord verified fully clear; collisions only on unbound reserved slots", () => {
    for (const chord of PTY_RESERVED.filter((entry) => entry.bound)) {
      expect(
        chord.verifiedAgainst.every((entry) => entry.status === "clear"),
        `${chord.chord} must be collision-free to stay bound`,
      ).toBe(true);
    }
    // The recorded Firefox DevTools collision lives on the unbound ctrl+shift+c slot.
    const ctrlShiftC = PTY_RESERVED.find((entry) => entry.chord === "ctrl+shift+c");
    expect(ctrlShiftC?.bound).toBe(false);
    expect(ctrlShiftC?.verifiedAgainst.some((entry) => entry.status === "collision")).toBe(true);
  });
});

describe("chrome/composer zones: printable suppression (R7)", () => {
  const textarea = el("composer", { tagName: "TEXTAREA" });
  const input = el(null, { tagName: "INPUT", readOnly: false });

  it("printable keys never fire as bindings in editable targets", () => {
    expect(routeKey("chrome", key({ key: "?" , shiftKey: true }), input)).toBe("passthrough");
    expect(routeKey("composer", key({ key: "?", shiftKey: true }), textarea)).toBe("passthrough");
  });

  it("non-printable chords still handle in editable targets (ctrl+enter, F6)", () => {
    expect(routeKey("composer", key({ key: "Enter", ctrlKey: true }), textarea)).toBe("handle");
    expect(routeKey("composer", key({ key: "F6" }), textarea)).toBe("handle");
  });

  it("printable keys handle on non-editable targets", () => {
    expect(routeKey("chrome", key({ key: "?", shiftKey: true }), el(null, { tagName: "DIV" }))).toBe(
      "handle",
    );
  });
});

describe("editable/printable primitives", () => {
  it("classifies editables", () => {
    expect(isEditableTarget({ tagName: "TEXTAREA" })).toBe(true);
    expect(isEditableTarget({ tagName: "INPUT", readOnly: false })).toBe(true);
    expect(isEditableTarget({ tagName: "INPUT", readOnly: true })).toBe(false);
    expect(isEditableTarget({ tagName: "DIV", isContentEditable: true })).toBe(true);
    expect(isEditableTarget({ tagName: "DIV" })).toBe(false);
    expect(isEditableTarget(null)).toBe(false);
  });

  it("classifies printables (shift allowed, ctrl/alt/meta not)", () => {
    expect(isPrintable(key({ key: "?" , shiftKey: true }))).toBe(true);
    expect(isPrintable(key({ key: "a" }))).toBe(true);
    expect(isPrintable(key({ key: "a", ctrlKey: true }))).toBe(false);
    expect(isPrintable(key({ key: "F6" }))).toBe(false);
    expect(isPrintable(key({ key: "Escape" }))).toBe(false);
  });
});

describe("composer `/` rule", () => {
  it("opens the palette only at the start of a line", () => {
    expect(slashOpensPalette("", 0)).toBe(true);
    expect(slashOpensPalette("line one\n", 9)).toBe(true);
    expect(slashOpensPalette("mid sentence", 3)).toBe(false);
    expect(slashOpensPalette("x", null)).toBe(false);
  });
});
