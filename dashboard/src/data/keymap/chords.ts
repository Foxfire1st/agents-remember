// The chrome/composer chord tables (design §5.2) — data, not code, so the
// tinykeys binding, the `?` keyboard-reference palette page, and the collision tests all read one
// source. Zone scoping is per-chord: a chord fires only when the event's zone is listed — chords
// the harnesses bind (Alt+Up/Down, Alt+,/.) are deliberately chrome-only so they always pass
// through over a live PTY (Codex: alt+up = edit_queued_message, alt+,/. = reasoning effort;
// Pi: alt+up = dequeue). Browser-reserved chords (BROWSER_FORBIDDEN) are banned everywhere.

import type { Zone } from "./zones";

export interface ZoneChord {
  /** tinykeys binding string ("[Shift]" marks an optional modifier). */
  chord: string;
  /** Human label for the `?` reference overlay. */
  label: string;
  commandId: string;
  /** Zones where the chord is handled; every other zone passes it through. */
  zones: readonly Zone[];
}

export const CHROME_CHORDS: ZoneChord[] = [
  {
    chord: "Control+K",
    label: "ctrl+k",
    commandId: "palette.open",
    zones: ["chrome", "composer"],
  },
  {
    chord: "Alt+ArrowUp",
    label: "alt+↑",
    commandId: "session.prev",
    zones: ["chrome"],
  },
  {
    chord: "Alt+ArrowDown",
    label: "alt+↓",
    commandId: "session.next",
    zones: ["chrome"],
  },
  {
    chord: "Alt+Comma",
    label: "alt+,",
    commandId: "effort.decrease",
    zones: ["chrome"],
  },
  {
    chord: "Alt+Period",
    label: "alt+.",
    commandId: "effort.increase",
    zones: ["chrome"],
  },
  {
    chord: "F6",
    label: "F6",
    commandId: "focus.nextRegion",
    zones: ["chrome", "composer"],
  },
  {
    chord: "Shift+F6",
    label: "shift+F6",
    commandId: "focus.prevRegion",
    zones: ["chrome", "composer"],
  },
  {
    chord: "[Shift]+?",
    label: "?",
    commandId: "keyboard.reference",
    zones: ["chrome"],
    // Printable — never fires in editable targets. That suppression is GENERIC: routeKey's
    // isPrintable/isEditableTarget contract covers every printable chord; no per-chord flag.
  },
  {
    // The exact-turn interrupt (design §9.5): the one rebindable non-Escape
    // stop chord. Control-based on every platform (matching the app's Control+K convention), fires in
    // chrome + composer, and is excluded from the raw-PTY zone (which only handles PTY_RESERVED). The
    // `conversation.stop` command's `when` gate keeps it inert unless a working turn is interruptible.
    chord: "Control+Shift+Period",
    label: "ctrl+shift+.",
    commandId: "conversation.stop",
    zones: ["chrome", "composer"],
  },
];

export const COMPOSER_CHORDS: ZoneChord[] = [
  {
    // Plain Enter SENDS in harness chats — the TUI
    // convention; Shift+Enter inserts the newline (bound in SessionComposer beside this chord).
    chord: "Enter",
    label: "↵",
    commandId: "composer.submit",
    zones: ["composer"],
  },
  {
    chord: "Alt+ArrowUp",
    label: "alt+↑",
    commandId: "composer.popBack",
    zones: ["composer"],
  },
  {
    chord: "Escape",
    label: "esc",
    commandId: "focus.stageHeader",
    zones: ["composer"],
  },
];

// The reviewer's change traversal (MIK-R33, adopting ICR-R32 rule 7): next and previous change in the
// family tree. Printable, so the generic routing contract keeps them inert in inputs, textareas and
// contenteditable regions; handled only in the reviewer's own zone, which is never the PTY zone.
export const REVIEW_CHORDS: ZoneChord[] = [
  {
    chord: "J",
    label: "j",
    commandId: "review.nextChange",
    zones: ["review"],
  },
  {
    chord: "K",
    label: "k",
    commandId: "review.previousChange",
    zones: ["review"],
  },
];
