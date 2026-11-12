// The keyboard-zone routing contract: which container owns a key press, and that a printable key
// never fires as a binding while the target takes typing.
import { describe, expect, it } from "vitest";

import {
  isEditableTarget,
  isPrintable,
  routeKey,
  zoneForTarget,
  type KeyEventLike,
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
  it("resolves the reviewer's container and defaults everything else to chrome", () => {
    expect(zoneForTarget(el("review"))).toBe("review");
    // The change-set viewer marks its own container; the reviewer's chords are not handled in it.
    expect(zoneForTarget(el("changeset"))).toBe("chrome");
    expect(zoneForTarget(el("bogus"))).toBe("chrome");
    expect(zoneForTarget(el(null))).toBe("chrome");
    expect(zoneForTarget(null)).toBe("chrome");
  });
});

describe("printable suppression (R7)", () => {
  const textarea = el("review", { tagName: "TEXTAREA" });
  const input = el(null, { tagName: "INPUT", readOnly: false });

  it("printable keys never fire as bindings in editable targets", () => {
    expect(routeKey(key({ key: "?" , shiftKey: true }), input)).toBe("passthrough");
    expect(routeKey(key({ key: "?", shiftKey: true }), textarea)).toBe("passthrough");
    expect(routeKey(key({ key: "j" }), textarea)).toBe("passthrough");
  });

  it("non-printable chords still handle in editable targets (ctrl+enter, F6)", () => {
    expect(routeKey(key({ key: "Enter", ctrlKey: true }), textarea)).toBe("handle");
    expect(routeKey(key({ key: "F6" }), textarea)).toBe("handle");
  });

  it("printable keys handle on non-editable targets", () => {
    expect(routeKey(key({ key: "?", shiftKey: true }), el(null, { tagName: "DIV" }))).toBe(
      "handle",
    );
    expect(routeKey(key({ key: "j" }), el("review", { tagName: "BUTTON" }))).toBe("handle");
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
