import { useSyncExternalStore } from "react";

import { CHROME_CHORDS, COMPOSER_CHORDS, type ZoneChord } from "./chords";
import { BROWSER_FORBIDDEN } from "./reserved";

// FEUI-L8 owns only a local, versioned preference seam. The settings-page editor belongs to
// CFGUI; until then, same-tab callers use writeKeymapPreferences with this documented object
// (raw writes from another tab remain reactive through the native storage event):
// { "version": 1, "bindings": { "palette.open": "Control+P" },
//   "composerProfile": "vim" }
//
// Validation is deliberately strict because localStorage is user-editable and survives upgrades.
// Passing malformed tinykeys strings through would disable the whole keyboard layer; accepting a
// duplicate/browser-owned chord could create a keyboard trap. Invalid entries therefore fall back
// to their individual defaults and are named in the keyboard-reference overlay.

export const KEYMAP_STORAGE_KEY = "cockpit.sessions.keymap.v1";
const KEYMAP_CHANGED_EVENT = "cockpit:sessions-keymap-changed";

export type ComposerProfile = "emacs" | "vim";

export interface KeymapPreferences {
  bindings?: Record<string, string>;
  composerProfile?: ComposerProfile;
}

export interface EffectiveKeymap {
  bindings: readonly ZoneChord[];
  composerProfile: ComposerProfile;
  issues: readonly string[];
  /** Stable dependency for binding effects and CodeMirror compartments. */
  signature: string;
}

interface StoredKeymap extends KeymapPreferences {
  version: 1;
}

const DEFAULT_BINDINGS = [...CHROME_CHORDS, ...COMPOSER_CHORDS] as const;
const KNOWN_COMMANDS = new Set(DEFAULT_BINDINGS.map((entry) => entry.commandId));
const IMMUTABLE_COMPOSER_ESCAPE_COMMAND = "focus.nextRegion";
const MODIFIERS = new Map<string, string>([
  ["control", "Control"],
  ["ctrl", "Control"],
  ["alt", "Alt"],
  ["shift", "Shift"],
  ["meta", "Meta"],
  ["cmd", "Meta"],
  ["[shift]", "[Shift]"],
]);
const NAMED_KEYS = new Map<string, string>(
  [
    "ArrowUp",
    "ArrowDown",
    "ArrowLeft",
    "ArrowRight",
    "PageUp",
    "PageDown",
    "Enter",
    "Escape",
    "Backspace",
    "Delete",
    "Home",
    "End",
    "Tab",
    "Space",
    "Comma",
    "Period",
    "Semicolon",
    "Slash",
    "BracketLeft",
    "BracketRight",
  ].map((key) => [key.toLowerCase(), key]),
);

function normalizeKey(token: string): string | null {
  const named = NAMED_KEYS.get(token.toLowerCase());
  if (named) return named;
  if (/^f(?:[1-9]|1[0-2])$/i.test(token)) return token.toUpperCase();
  if (/^[a-z0-9?]$/i.test(token)) return token.toUpperCase();
  return null;
}

/** Parse the deliberately small tinykeys subset the cockpit supports. */
export function normalizeBinding(value: string): string | null {
  const rawTokens = value.split("+").map((token) => token.trim());
  if (rawTokens.length === 0 || rawTokens.some((token) => token.length === 0)) return null;
  const modifiers: string[] = [];
  let key: string | null = null;
  for (const token of rawTokens) {
    const modifier = MODIFIERS.get(token.toLowerCase());
    if (modifier) {
      if (modifiers.includes(modifier)) return null;
      modifiers.push(modifier);
      continue;
    }
    const normalizedKey = normalizeKey(token);
    if (!normalizedKey || key !== null) return null;
    key = normalizedKey;
  }
  if (!key || (modifiers.includes("Shift") && modifiers.includes("[Shift]"))) return null;
  const order = ["Control", "Alt", "Meta", "Shift", "[Shift]"];
  modifiers.sort((a, b) => order.indexOf(a) - order.indexOf(b));
  return [...modifiers, key].join("+");
}

function browserComparable(chord: string): string {
  return chord
    .replaceAll("Control", "ctrl")
    .replaceAll("Meta", "meta")
    .replaceAll("Arrow", "")
    .toLowerCase();
}

function zonesOverlap(a: ZoneChord, b: ZoneChord): boolean {
  return a.zones.some((zone) => b.zones.includes(zone));
}

/** Whether editable-target routing would deliberately pass this chord through as printable. */
function isPrintableBinding(chord: string): boolean {
  const tokens = chord.split("+");
  if (tokens.some((token) => token === "Control" || token === "Alt" || token === "Meta")) {
    return false;
  }
  const key = tokens[tokens.length - 1];
  return (
    /^[A-Z0-9?]$/.test(key) ||
    ["Space", "Comma", "Period", "Semicolon", "Slash", "BracketLeft", "BracketRight"].includes(
      key,
    )
  );
}

function readStoredObject(value: unknown): StoredKeymap | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  const record = value as Record<string, unknown>;
  if (record.version !== 1) return null;
  return record as unknown as StoredKeymap;
}

export function resolveKeymap(raw: string | null): EffectiveKeymap {
  const issues: string[] = [];
  let stored: StoredKeymap | null = null;
  if (raw !== null) {
    try {
      stored = readStoredObject(JSON.parse(raw));
      if (!stored) issues.push("stored keymap has an unsupported shape or version; defaults restored");
    } catch {
      issues.push("stored keymap is not valid JSON; defaults restored");
    }
  }

  let composerProfile: ComposerProfile = "emacs";
  if (stored?.composerProfile === "vim" || stored?.composerProfile === "emacs") {
    composerProfile = stored.composerProfile;
  } else if (stored?.composerProfile !== undefined) {
    issues.push("unknown composer profile; emacs restored");
  }

  const overrides = new Map<string, string>();
  if (stored?.bindings !== undefined) {
    if (typeof stored.bindings !== "object" || stored.bindings === null || Array.isArray(stored.bindings)) {
      issues.push("bindings must be an object; binding defaults restored");
    } else {
      for (const [commandId, candidate] of Object.entries(stored.bindings)) {
        if (!KNOWN_COMMANDS.has(commandId)) {
          issues.push(`${commandId}: unknown command ignored`);
          continue;
        }
        if (typeof candidate !== "string") {
          issues.push(`${commandId}: binding must be text; default restored`);
          continue;
        }
        const chord = normalizeBinding(candidate);
        if (!chord) {
          issues.push(`${commandId}: malformed binding; default restored`);
          continue;
        }
        if (BROWSER_FORBIDDEN.includes(browserComparable(chord) as (typeof BROWSER_FORBIDDEN)[number])) {
          issues.push(`${commandId}: browser-reserved ${bindingLabel(chord)} rejected; default restored`);
          continue;
        }
        const defaultEntry = DEFAULT_BINDINGS.find((entry) => entry.commandId === commandId)!;
        if (
          commandId === IMMUTABLE_COMPOSER_ESCAPE_COMMAND &&
          chord !== defaultEntry.chord
        ) {
          issues.push(`${commandId}: F6 is the fixed browser-safe composer escape; default restored`);
          continue;
        }
        if (defaultEntry.zones.includes("composer") && isPrintableBinding(chord)) {
          issues.push(`${commandId}: printable composer binding rejected; default restored`);
          continue;
        }
        overrides.set(commandId, chord);
      }
    }
  }

  const withCandidates = DEFAULT_BINDINGS.map((entry) => ({
    ...entry,
    chord: overrides.get(entry.commandId) ?? entry.chord,
    label: overrides.has(entry.commandId)
      ? bindingLabel(overrides.get(entry.commandId)!)
      : entry.label,
  }));
  const rejectedForCollision = new Set<string>();
  for (let left = 0; left < withCandidates.length; left += 1) {
    for (let right = left + 1; right < withCandidates.length; right += 1) {
      const a = withCandidates[left];
      const b = withCandidates[right];
      if (a.chord !== b.chord || !zonesOverlap(a, b)) continue;
      if (overrides.has(a.commandId)) rejectedForCollision.add(a.commandId);
      if (overrides.has(b.commandId)) rejectedForCollision.add(b.commandId);
    }
  }
  for (const commandId of rejectedForCollision) {
    issues.push(`${commandId}: duplicate binding rejected; default restored`);
  }

  const bindings = withCandidates.map((entry) => {
    if (!rejectedForCollision.has(entry.commandId)) return entry;
    return DEFAULT_BINDINGS.find((fallback) => fallback.commandId === entry.commandId)!;
  });
  const signature = JSON.stringify({
    bindings: bindings.map(({ commandId, chord }) => [commandId, chord]),
    composerProfile,
    issues,
  });
  return { bindings, composerProfile, issues, signature };
}

export function bindingLabel(chord: string): string {
  const labels: Record<string, string> = {
    Control: "ctrl",
    Alt: "alt",
    Shift: "shift",
    Meta: "meta",
    "[Shift]": "",
    ArrowUp: "↑",
    ArrowDown: "↓",
    ArrowLeft: "←",
    ArrowRight: "→",
    Enter: "↵",
    Escape: "esc",
    PageUp: "pageup",
    PageDown: "pagedown",
    Comma: ",",
    Period: ".",
    Semicolon: ";",
    Slash: "/",
  };
  return chord
    .split("+")
    .map((token) => labels[token] ?? token)
    .filter(Boolean)
    .join("+");
}

/**
 * Render a validated tinykeys chord as a WAI-ARIA `aria-keyshortcuts` token (e.g.
 * `Control+Shift+Period` -> `Control+Shift+.`). The modifier tokens (Control/Alt/Shift/Meta) already
 * match the ARIA modifier names; only the named punctuation keys map to their `KeyboardEvent.key`
 * character, and the optional-shift marker is dropped. Derived (not hardcoded) so a rebind stays
 * truthful to assistive tech (F25).
 */
export function ariaKeyshortcuts(chord: string): string {
  const keyValues: Record<string, string> = {
    Comma: ",",
    Period: ".",
    Semicolon: ";",
    Slash: "/",
    Space: " ",
  };
  return chord
    .split("+")
    .filter((token) => token !== "[Shift]")
    .map((token) => keyValues[token] ?? token)
    .join("+");
}

/** Convert the validated tinykeys subset into CodeMirror's key notation. */
export function codeMirrorBinding(chord: string): string {
  const keys: Record<string, string> = {
    Control: "Ctrl",
    Comma: ",",
    Period: ".",
    Semicolon: ";",
    Slash: "/",
  };
  return chord
    .split("+")
    .filter((token) => token !== "[Shift]")
    .map((token) => keys[token] ?? token)
    .join("-");
}

const DEFAULT_KEYMAP = resolveKeymap(null);
let cachedRaw: string | null | undefined;
let cachedSnapshot = DEFAULT_KEYMAP;

function readWindowSnapshot(): EffectiveKeymap {
  if (typeof window === "undefined") return DEFAULT_KEYMAP;
  let raw: string | null = null;
  try {
    raw = window.localStorage.getItem(KEYMAP_STORAGE_KEY);
  } catch {
    return DEFAULT_KEYMAP;
  }
  if (raw === cachedRaw) return cachedSnapshot;
  cachedRaw = raw;
  cachedSnapshot = resolveKeymap(raw);
  return cachedSnapshot;
}

function subscribe(listener: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  const onStorage = (event: StorageEvent) => {
    if (event.key === KEYMAP_STORAGE_KEY) listener();
  };
  window.addEventListener("storage", onStorage);
  window.addEventListener(KEYMAP_CHANGED_EVENT, listener);
  return () => {
    window.removeEventListener("storage", onStorage);
    window.removeEventListener(KEYMAP_CHANGED_EVENT, listener);
  };
}

export function useEffectiveKeymap(): EffectiveKeymap {
  return useSyncExternalStore(subscribe, readWindowSnapshot, () => DEFAULT_KEYMAP);
}

/**
 * Persist a complete v1 preference object and notify subscribers in this tab. Other tabs receive
 * the browser's native storage event. This is the public write seam for the future CFGUI editor;
 * direct same-tab localStorage writes cannot be reactive because browsers do not emit `storage`
 * back into the source document.
 */
export function writeKeymapPreferences(preferences: KeymapPreferences): void {
  if (typeof window === "undefined") return;
  const next: StoredKeymap = { version: 1, ...preferences };
  try {
    window.localStorage.setItem(KEYMAP_STORAGE_KEY, JSON.stringify(next));
  } catch {
    return;
  }
  cachedRaw = undefined;
  window.dispatchEvent(new Event(KEYMAP_CHANGED_EVENT));
}

export function setComposerProfile(profile: ComposerProfile): void {
  if (typeof window === "undefined") return;
  let current: StoredKeymap = { version: 1 };
  try {
    const parsed = readStoredObject(JSON.parse(window.localStorage.getItem(KEYMAP_STORAGE_KEY) ?? "null"));
    if (parsed) current = parsed;
  } catch {
    // Corrupt persisted input is replaced by the new valid preference below.
  }
  writeKeymapPreferences({ bindings: current.bindings, composerProfile: profile });
}

export function bindingFor(
  keymap: EffectiveKeymap,
  commandId: string,
): ZoneChord | undefined {
  return keymap.bindings.find((entry) => entry.commandId === commandId);
}

/** Vim owns Escape inside CodeMirror; every consumer uses this predicate so runtime and the
 * effective overlay suppress the same binding. */
export function keymapCommandIsActive(keymap: EffectiveKeymap, commandId: string): boolean {
  return !(keymap.composerProfile === "vim" && commandId === "focus.stageHeader");
}
