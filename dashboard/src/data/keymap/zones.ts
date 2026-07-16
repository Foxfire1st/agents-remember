// Keyboard ownership zones (260715-FEUI-L1 S4, design §5.2) — pure logic, xterm-free, so vitest
// covers the routing contract without a terminal. Three zones:
//   chrome   — the shell around the panes: chords may be handled; printable-key bindings never
//              fire when the target is editable (R7).
//   composer — the editor owns its keys; only composer-declared chords (Ctrl+Enter, Esc, palette)
//              are handled; everything else passes to the editor.
//   pty      — EVERY key passes to the hosted harness except exactly the bound reserved set
//              (reserved.ts). No bare-Esc sequence is ever claimed over a live PTY.
// Zone membership is carried by `data-kbzone` markers on containers so the React binding and the
// tests resolve zones the same way.

import { matchReservedChord, type KeyEventLike } from "./reserved";

export type Zone = "chrome" | "composer" | "pty";

export type KeyRoute = "handle" | "passthrough";

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
  return zone === "pty" || zone === "composer" ? zone : "chrome";
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

/**
 * The routing contract (the leaf's code example, verbatim in spirit):
 * - pty: handle ONLY a bound reserved chord; everything else — including Esc — passes through.
 * - chrome/composer: printable keys never fire as bindings when the target is editable.
 */
export function routeKey(zone: Zone, ev: KeyEventLike, target?: ZoneElementLike | null): KeyRoute {
  if (zone === "pty") return matchReservedChord(ev) ? "handle" : "passthrough";
  if (isEditableTarget(target) && isPrintable(ev)) return "passthrough";
  return "handle";
}

/**
 * Composer rule (design §5.2): `/` at the start of a line opens the palette pre-filtered.
 * Pure over the editor's value + caret so CM6 (L5) and the placeholder textarea share it.
 */
export function slashOpensPalette(value: string, selectionStart: number | null): boolean {
  if (selectionStart === null) return false;
  return selectionStart === 0 || value.charAt(selectionStart - 1) === "\n";
}
