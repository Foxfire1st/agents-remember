// The PTY reserved set (260715-FEUI-L1 S4, design §5.2): the ONLY chords the PTY zone ever
// intercepts — every other key, including Esc and Esc Esc, passes through to the hosted harness
// (Claude Code owns Esc Esc for rewind; Codex owns plain Esc for interrupt). The set is DATA so
// the `?` reference overlay, the tinykeys binding, the future xterm attachCustomKeyEventHandler
// (L6), and the collision-verification record all read one source and can never drift apart.
//
// Collision audit (R6, run 2026-07-16 against the three harness keymaps + browser reserved lists;
// the full table lives in the leaf's decision log / worker report):
//   - codex  = openai/codex codex-rs/tui/src/keymap.rs built_in_defaults()
//   - pi     = earendil-works/pi packages/tui/src/keybindings.ts + coding-agent keybindings.ts
//   - claude = Claude Code 2.1.210 installed bundle (keybinding strings)
//   - chrome/firefox = non-preventable browser shortcut lists
// A collision replaces the CHORD, not the rule: the original Ctrl+Alt+[ / Ctrl+Alt+] pair was
// REPLACED by Ctrl+Alt+PageUp / Ctrl+Alt+PageDown because (a) Pi binds ctrl+alt+] as
// tui.editor.jumpBackward, and (b) the terminal encoding of Ctrl+Alt+[ is ESC ESC (\x1b\x1b) —
// exactly Claude Code's rewind — so any interception miss would fire a destructive harness action.
// Ctrl+Alt+PageUp/PageDown encode as distinct CSI sequences (CSI 5;7~ / 6;7~) that no audited
// harness binds, so even a leaked event is inert.

/** One source consulted by the collision audit, with the observed evidence. */
export interface ChordVerification {
  source: "codex" | "pi" | "claude" | "chrome" | "firefox";
  status: "clear" | "collision";
  evidence: string;
}

/** A structural chord matcher — matches KeyboardEvent-likes so tests need no real DOM events. */
export interface ChordMatch {
  /** `KeyboardEvent.key` values accepted (case-insensitive). */
  keys?: readonly string[];
  /** `KeyboardEvent.code` values accepted (layout-robust physical position). */
  codes?: readonly string[];
  ctrl: boolean;
  alt: boolean;
  shift: boolean;
  meta: boolean;
}

export interface ReservedChord {
  /** Human label, as shown by the `?` reference overlay. */
  chord: string;
  /** tinykeys binding string (bound entries only). */
  tinykeys?: string;
  match?: ChordMatch;
  action: string;
  /** False = a reserved SLOT that cockpit code must leave unbound (clipboard package, L6). */
  bound: boolean;
  verifiedAgainst: ChordVerification[];
  note?: string;
}

/** The minimal KeyboardEvent surface the matcher reads (jsdom/test friendly). */
export interface KeyEventLike {
  key: string;
  code?: string;
  ctrlKey: boolean;
  altKey: boolean;
  shiftKey: boolean;
  metaKey: boolean;
}

export const PTY_RESERVED: ReservedChord[] = [
  {
    chord: "ctrl+;",
    tinykeys: "Control+Semicolon",
    match: { keys: [";"], codes: ["Semicolon"], ctrl: true, alt: false, shift: false, meta: false },
    action: "palette.open",
    bound: true,
    verifiedAgainst: [
      { source: "codex", status: "clear", evidence: "no Char(';') binding in keymap.rs defaults" },
      { source: "pi", status: "clear", evidence: "no ctrl+; in tui/coding-agent keybindings" },
      { source: "claude", status: "clear", evidence: "no 'ctrl+;' string in the 2.1.210 bundle" },
      { source: "chrome", status: "clear", evidence: "unreserved" },
      { source: "firefox", status: "clear", evidence: "unreserved" },
    ],
  },
  {
    chord: "F6",
    tinykeys: "F6",
    match: { keys: ["F6"], ctrl: false, alt: false, shift: false, meta: false },
    action: "focus.exitToChrome",
    bound: true,
    verifiedAgainst: [
      { source: "codex", status: "clear", evidence: "no default F-key bindings (F12 only in tests)" },
      { source: "pi", status: "clear", evidence: "no F-key bindings" },
      { source: "claude", status: "clear", evidence: "no F6 keybinding string in the bundle" },
      { source: "chrome", status: "clear", evidence: "F6 cycles browser panes but is preventable from content" },
      { source: "firefox", status: "clear", evidence: "same — content keydown preventDefault is honored" },
    ],
  },
  {
    chord: "ctrl+alt+pageup",
    tinykeys: "Control+Alt+PageUp",
    match: { keys: ["PageUp"], ctrl: true, alt: true, shift: false, meta: false },
    action: "session.prev",
    bound: true,
    note: "Replaced Ctrl+Alt+[ — its terminal encoding is ESC ESC (Claude rewind) if ever leaked.",
    verifiedAgainst: [
      { source: "codex", status: "clear", evidence: "PageUp bound plain-only (pager/list)" },
      { source: "pi", status: "clear", evidence: "pageUp bound plain-only" },
      { source: "claude", status: "clear", evidence: "no ctrl/alt+pageup strings in the bundle" },
      { source: "chrome", status: "clear", evidence: "Ctrl+PageUp is tab-switch; the Alt variant is unreserved" },
      { source: "firefox", status: "clear", evidence: "unreserved with Alt" },
    ],
  },
  {
    chord: "ctrl+alt+pagedown",
    tinykeys: "Control+Alt+PageDown",
    match: { keys: ["PageDown"], ctrl: true, alt: true, shift: false, meta: false },
    action: "session.next",
    bound: true,
    note: "Replaced Ctrl+Alt+] — COLLISION: Pi binds ctrl+alt+] (tui.editor.jumpBackward).",
    verifiedAgainst: [
      { source: "codex", status: "clear", evidence: "PageDown bound plain-only (pager/list)" },
      { source: "pi", status: "clear", evidence: "pageDown bound plain-only" },
      { source: "claude", status: "clear", evidence: "no ctrl/alt+pagedown strings in the bundle" },
      { source: "chrome", status: "clear", evidence: "Ctrl+PageDown is tab-switch; the Alt variant is unreserved" },
      { source: "firefox", status: "clear", evidence: "unreserved with Alt" },
    ],
  },
  // ── Clipboard reservations (pane-freeze/clipboard companion spec, 260710; leaf 06 R3) ──
  // These slots ride along as reserved DATA but stay UNBOUND by cockpit code until the L6
  // clipboard package lands; matchReservedChord never fires for them.
  {
    chord: "ctrl+c (with selection)",
    action: "clipboard.copySelection",
    bound: false,
    note: "Selection-aware only: without a selection Ctrl+C passes \\x03 to the pane (interrupt).",
    verifiedAgainst: [
      { source: "codex", status: "clear", evidence: "ctrl+c = quit/interrupt — passthrough preserved when no selection" },
      { source: "pi", status: "clear", evidence: "ctrl+c = clear/copy — passthrough preserved when no selection" },
      { source: "claude", status: "clear", evidence: "ctrl+c = interrupt — passthrough preserved when no selection" },
      { source: "chrome", status: "clear", evidence: "unreserved" },
      { source: "firefox", status: "clear", evidence: "unreserved" },
    ],
  },
  {
    chord: "ctrl+shift+c",
    action: "clipboard.copy",
    bound: false,
    note: "COLLISION recorded: Firefox DevTools inspect-element steals Ctrl+Shift+C non-preventably; Chrome honors preventDefault. Flagged to the L6 clipboard leaf — prefer selection-aware Ctrl+C + copy-on-select.",
    verifiedAgainst: [
      { source: "codex", status: "clear", evidence: "terminal-level chord, not bound by the TUI" },
      { source: "pi", status: "clear", evidence: "not bound" },
      { source: "claude", status: "clear", evidence: "not bound" },
      { source: "chrome", status: "clear", evidence: "DevTools inspect, but content preventDefault is honored" },
      { source: "firefox", status: "collision", evidence: "DevTools inspect-element is NOT preventable from content" },
    ],
  },
];

/** Chords no zone may EVER bind: browsers reserve them non-preventably (design §5.2 ruling). */
export const BROWSER_FORBIDDEN = [
  "ctrl+w",
  "ctrl+t",
  "ctrl+n",
  "ctrl+shift+w",
  "ctrl+shift+t",
  "ctrl+shift+n",
  "ctrl+shift+q",
  "ctrl+shift+k",
  "ctrl+shift+p",
  "ctrl+shift+o",
  "ctrl+tab",
  "ctrl+shift+tab",
  "ctrl+1",
  "ctrl+2",
  "ctrl+3",
  "ctrl+4",
  "ctrl+5",
  "ctrl+6",
  "ctrl+7",
  "ctrl+8",
  "ctrl+9",
  "alt+f4",
  "f11",
] as const;

function matches(match: ChordMatch, ev: KeyEventLike): boolean {
  if (ev.ctrlKey !== match.ctrl) return false;
  if (ev.altKey !== match.alt) return false;
  if (ev.shiftKey !== match.shift) return false;
  if (ev.metaKey !== match.meta) return false;
  const keyOk = match.keys?.some((k) => k.toLowerCase() === ev.key.toLowerCase()) ?? false;
  const codeOk = match.codes?.some((c) => c === ev.code) ?? false;
  return keyOk || codeOk;
}

/**
 * The PTY zone's single gate: returns the matching BOUND reserved chord, or null — null means
 * the event belongs to the hosted harness (passthrough), no exceptions.
 */
export function matchReservedChord(ev: KeyEventLike): ReservedChord | null {
  for (const chord of PTY_RESERVED) {
    if (!chord.bound || !chord.match) continue;
    if (matches(chord.match, ev)) return chord;
  }
  return null;
}
