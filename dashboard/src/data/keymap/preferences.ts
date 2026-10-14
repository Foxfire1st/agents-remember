import { useSyncExternalStore } from "react";

import { REVIEW_CHORDS, type ZoneChord } from "./chords";
import { BROWSER_FORBIDDEN } from "./reserved";

// The bindings with a user's overrides. The overrides are a local, versioned object in
// localStorage. No screen writes it: a user edits it by hand, a load of the page reads it, and a
// write from another tab arrives through the native storage event.
// { "version": 1, "bindings": { "review.nextChange": "N" } }
//
// Validation is deliberately strict because localStorage is user-editable and survives upgrades.
// Passing malformed tinykeys strings through would disable the whole keyboard layer; accepting a
// duplicate/browser-owned chord could create a keyboard trap. Invalid entries therefore fall back
// to their individual defaults. A stored object can be older than this build: a binding of a
// command this build does not know, and every field besides `version` and `bindings`, is ignored.

// The key keeps the name the overrides were first stored under, so a user's object is still read.
export const KEYMAP_STORAGE_KEY = "cockpit.sessions.keymap.v1";

export interface EffectiveKeymap {
  bindings: readonly ZoneChord[];
}

interface StoredKeymap {
  version: 1;
  bindings?: Record<string, string>;
}

const DEFAULT_BINDINGS: readonly ZoneChord[] = REVIEW_CHORDS;
const KNOWN_COMMANDS = new Set(DEFAULT_BINDINGS.map((entry) => entry.commandId));
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
function collectBindingTokens(rawTokens: string[]): { modifiers: string[]; key: string } | null {
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
  return key === null ? null : { modifiers, key };
}

export function normalizeBinding(value: string): string | null {
  const rawTokens = value.split("+").map((token) => token.trim());
  if (rawTokens.length === 0 || rawTokens.some((token) => token.length === 0)) return null;
  const collected = collectBindingTokens(rawTokens);
  if (collected === null) return null;
  const { modifiers, key } = collected;
  if (modifiers.includes("Shift") && modifiers.includes("[Shift]")) return null;
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

function readStoredObject(value: unknown): StoredKeymap | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  const record = value as Record<string, unknown>;
  if (record.version !== 1) return null;
  return record as unknown as StoredKeymap;
}

/** The stored object, or null (the defaults) when it is absent, not JSON, or of another shape. */
function readRawKeymap(raw: string | null): StoredKeymap | null {
  if (raw === null) return null;
  try {
    return readStoredObject(JSON.parse(raw));
  } catch {
    return null;
  }
}

/** The validated chord of one stored binding, or null (its default) when it is not acceptable. */
function normalizeOverride(candidate: unknown): string | null {
  if (typeof candidate !== "string") return null;
  const chord = normalizeBinding(candidate);
  if (!chord) return null;
  if (BROWSER_FORBIDDEN.includes(browserComparable(chord) as (typeof BROWSER_FORBIDDEN)[number])) {
    return null;
  }
  return chord;
}

function collectOverrides(stored: StoredKeymap | null): Map<string, string> {
  const overrides = new Map<string, string>();
  const bindings: unknown = stored?.bindings;
  if (typeof bindings !== "object" || bindings === null || Array.isArray(bindings)) {
    return overrides;
  }
  for (const [commandId, candidate] of Object.entries(bindings)) {
    if (!KNOWN_COMMANDS.has(commandId)) continue;
    const chord = normalizeOverride(candidate);
    if (chord !== null) overrides.set(commandId, chord);
  }
  return overrides;
}

function findCollisions(
  withCandidates: ZoneChord[],
  overrides: Map<string, string>,
): Set<string> {
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
  return rejectedForCollision;
}

export function resolveKeymap(raw: string | null): EffectiveKeymap {
  const overrides = collectOverrides(readRawKeymap(raw));

  const withCandidates = DEFAULT_BINDINGS.map((entry) => ({
    ...entry,
    chord: overrides.get(entry.commandId) ?? entry.chord,
    label: overrides.has(entry.commandId)
      ? bindingLabel(overrides.get(entry.commandId)!)
      : entry.label,
  }));
  const rejectedForCollision = findCollisions(withCandidates, overrides);

  const bindings = withCandidates.map((entry) => {
    if (!rejectedForCollision.has(entry.commandId)) return entry;
    return DEFAULT_BINDINGS.find((fallback) => fallback.commandId === entry.commandId)!;
  });
  return { bindings };
}

function bindingLabel(chord: string): string {
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
  return () => {
    window.removeEventListener("storage", onStorage);
  };
}

export function useEffectiveKeymap(): EffectiveKeymap {
  return useSyncExternalStore(subscribe, readWindowSnapshot, () => DEFAULT_KEYMAP);
}

export function bindingFor(
  keymap: EffectiveKeymap,
  commandId: string,
): ZoneChord | undefined {
  return keymap.bindings.find((entry) => entry.commandId === commandId);
}
