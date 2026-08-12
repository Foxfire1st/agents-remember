// The client half of the change-gate's volatile-age contract.
//
// The server's SSE diff compares STABLE forms: the now-relative age fields listed in
// VOLATILE_AGE_FIELDS are recomputed from the tick clock every projection, so comparing
// them re-emitted ~the full payload every tick (~780 KB/s measured live) even when nothing
// real changed. With them excluded server-side (serving/delta.py), a node's served age is
// only fresh at the moment the node last ARRIVED — so this module (1) mirrors the exact
// field set for the store's stable-equality merge, and (2) anchors every applied node to
// its arrival time so display sites can advance ages locally. "Server-anchored, client-
// advanced": the value is still never *computed* from the render clock, only aged forward
// from the served anchor (localhost clocks; sub-second skew is far below display grain).

import { useEffect, useRef, useState } from "react";

/** Mirror of serving/delta.py VOLATILE_AGE_FIELDS — keep the two sets in lockstep. */
export const VOLATILE_AGE_FIELDS: ReadonlySet<string> = new Set([
  "staleSeconds",
  "snapshotStaleSeconds",
  "ageSeconds",
  "waitSeconds",
  "heartbeatAgeSeconds",
  "elapsedSeconds",
]);

/**
 * Deep equality over parsed wire payloads, ignoring the volatile age keys. Used by the
 * store merge so an arriving node that only differs in its ages (a reconnect snapshot, a
 * redundant delta) is recognized as unchanged and keeps its existing object identity —
 * zero store write, zero downstream re-render, and the node keeps its original age anchor.
 */
function sameRecord(left: Record<string, unknown>, right: Record<string, unknown>): boolean {
  const leftKeys = Object.keys(left).filter((key) => !VOLATILE_AGE_FIELDS.has(key));
  const rightKeys = Object.keys(right).filter((key) => !VOLATILE_AGE_FIELDS.has(key));
  if (leftKeys.length !== rightKeys.length) return false;
  return leftKeys.every(
    (key) => Object.prototype.hasOwnProperty.call(right, key) && stableEquals(left[key], right[key]),
  );
}

function sameArray(left: unknown[], right: unknown[]): boolean {
  if (left.length !== right.length) return false;
  return left.every((item, index) => stableEquals(item, right[index]));
}

export function stableEquals(a: unknown, b: unknown): boolean {
  if (Object.is(a, b)) return true;
  if (Array.isArray(a) || Array.isArray(b)) {
    if (!Array.isArray(a) || !Array.isArray(b)) return false;
    return sameArray(a, b);
  }
  if (typeof a !== "object" || typeof b !== "object" || a === null || b === null) return false;
  return sameRecord(a as Record<string, unknown>, b as Record<string, unknown>);
}

// Arrival anchors, keyed by node object identity. A WeakMap so anchors never retain nodes
// the store has dropped (the long-session rule: no side table may outlive its subject).
const anchors = new WeakMap<object, number>();

/** Anchor one applied node to its arrival time. The store calls this for every node it applies. */
export function stampServed(node: object, atMs: number = Date.now()): void {
  anchors.set(node, atMs);
}

/**
 * A served age advanced to `nowMs`: the node's served seconds plus the time elapsed since
 * the node arrived. An unanchored node (fixtures, tests) serves its value as-is; a missing
 * served value stays missing (never fabricated).
 */
export function servedAgeSeconds(
  node: object | undefined | null,
  servedSeconds: number | undefined | null,
  nowMs: number,
): number | undefined {
  if (servedSeconds == null) return undefined;
  if (node == null) return servedSeconds;
  const at = anchors.get(node);
  if (at == null) return servedSeconds;
  return servedSeconds + Math.max(0, (nowMs - at) / 1000);
}

/**
 * A coarse ticking clock for age display. Panels that render served ages re-render on this
 * step (default 10 s) instead of riding the old per-second full-payload churn; fmtWait's
 * display grain (s → m → h → d) makes a 10 s step visually seamless above the first minute.
 *
 * Kept-mounted cockpit layers pass `active=false` while hidden. Their last value then remains
 * frozen without scheduling React work, and the clock catches up once when the layer is shown.
 */
export function useNowMs(stepMs = 10_000, active = true): number {
  const [now, setNow] = useState(() => Date.now());
  const wasActive = useRef(active);
  useEffect(() => {
    if (!active) {
      wasActive.current = false;
      return;
    }
    if (!wasActive.current) setNow(Date.now());
    wasActive.current = true;
    const id = window.setInterval(() => setNow(Date.now()), stepMs);
    return () => window.clearInterval(id);
  }, [active, stepMs]);
  return now;
}
