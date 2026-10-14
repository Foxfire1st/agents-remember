// Keyboard ownership zones — pure logic, so vitest covers the routing contract without a DOM.
// Two zones:
//   chrome   — everything outside the reviewer, and every container marked with another name.
//   review   — the intent reviewer (MIK-R33): its own chords (j/k change traversal) fire only while
//              focus is inside it.
// In both, a printable-key binding never fires when the target is editable.
// Zone membership is carried by `data-kbzone` markers on containers so the React binding and the
// tests resolve zones the same way.

export type Zone = "chrome" | "review";

export type KeyRoute = "handle" | "passthrough";

/** The minimal KeyboardEvent surface the routing reads (jsdom/test friendly). */
export interface KeyEventLike {
  key: string;
  ctrlKey: boolean;
  altKey: boolean;
  shiftKey: boolean;
  metaKey: boolean;
}

/** The minimal element surface zone resolution reads (Element in prod, fakes in tests). */
export interface ZoneElementLike {
  closest?: (selector: string) => ZoneElementLike | null;
  getAttribute?: (name: string) => string | null;
  tagName?: string;
  isContentEditable?: boolean;
  readOnly?: boolean;
}

/** Resolve the zone that owns an event target via the nearest `data-kbzone` container. */
export function zoneForTarget(target: ZoneElementLike | null | undefined): Zone {
  const host = target?.closest?.("[data-kbzone]");
  const zone = host?.getAttribute?.("data-kbzone");
  return zone === "review" ? zone : "chrome";
}

/** True when the target consumes plain typing (inputs, textareas, contenteditable). */
export function isEditableTarget(target: ZoneElementLike | null | undefined): boolean {
  if (!target) return false;
  if (target.isContentEditable) return true;
  const tag = target.tagName?.toUpperCase();
  if (tag === "TEXTAREA" || tag === "SELECT") return true;
  if (tag === "INPUT") return !target.readOnly;
  return false;
}

/** True for printable single-character presses (no ctrl/alt/meta; shift allowed — `?` etc.). */
export function isPrintable(ev: KeyEventLike): boolean {
  return ev.key.length === 1 && !ev.ctrlKey && !ev.altKey && !ev.metaKey;
}

/** The routing contract: printable keys never fire as bindings when the target is editable. */
export function routeKey(ev: KeyEventLike, target?: ZoneElementLike | null): KeyRoute {
  if (isEditableTarget(target) && isPrintable(ev)) return "passthrough";
  return "handle";
}
