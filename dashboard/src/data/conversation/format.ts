// Presentation conventions for the structured Chats surfaces, encoding the developer visual-findings
// rules (260720-developer-dashboard-visual-findings §A) as one shared, tested vocabulary so every
// component reads as the same product:
//  A1 — em-dash = a genuinely absent value; interpunct = separator. Empty states explain or collapse,
//       never chain dashes.
//  A4 — durations/ages are humanized with fixed sensible precision; a long-stale value is a QUIET
//       distinct tone (never cried-wolf red).
//  A5 — long labels truncate at a boundary and always keep a full-value affordance (title attr).
//  A8 — chip grammar is lowercase; the separator is a single interpunct.

/** The absent-value glyph (A1): a value that genuinely does not apply. Never chain these. */
export const ABSENT = "—";
/** The one separator convention (A1/A8): a spaced interpunct between chips on one line. */
export const SEP = " · ";

/** Join present chips with the single separator convention; empty input collapses to nothing (A2). */
export function joinChips(parts: ReadonlyArray<string | null | undefined>): string {
  return parts.filter((part): part is string => typeof part === "string" && part.length > 0).join(SEP);
}

/**
 * Humanize a millisecond duration with fixed, readable precision (A4): the two most significant
 * units, e.g. `6 d 0 h`, `3 m 12 s`, `800 ms`. No six-decimal seconds, no raw minutes.
 */
export function humanizeDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return ABSENT;
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const totalSeconds = Math.floor(ms / 1000);
  const days = Math.floor(totalSeconds / 86400);
  const hours = Math.floor((totalSeconds % 86400) / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (days > 0) return `${days} d ${hours} h`;
  if (hours > 0) return `${hours} h ${minutes} m`;
  if (minutes > 0) return `${minutes} m ${seconds} s`;
  return `${seconds} s`;
}

/** Humanize an ISO timestamp as an age relative to `now` (A4). Absent input renders the absent glyph. */
export function humanizeAge(iso: string | null | undefined, now = Date.now()): string {
  if (iso === null || iso === undefined || iso.length === 0) return ABSENT;
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return ABSENT;
  const delta = now - then;
  if (delta < 0) return "just now";
  return `${humanizeDuration(delta)} ago`;
}

export type FreshnessTone = "fresh" | "aging" | "stale" | "unknown";

/**
 * Map a freshness state + age to a QUIET-distinct tone (A4). A long-stale value degrades to a calm
 * `stale` tone, not an alarm — six-day staleness is expected for dormant/idle sessions, not a fault.
 */
export function freshnessTone(
  state: "fresh" | "stale" | "unknown",
  ageMs: number | null,
): FreshnessTone {
  if (state === "unknown") return "unknown";
  if (state === "fresh") return "fresh";
  // state === "stale": distinguish a brief lag ("aging") from long-stale ("stale", quiet).
  if (ageMs !== null && ageMs < 60_000) return "aging";
  return "stale";
}

/**
 * Truncate a label at a boundary with a stable ellipsis, keeping the head and a short tail so the
 * distinguishing suffix (e.g. a native id) survives (A5). The caller must always attach the full
 * value as a `title`/tooltip — this returns display text only.
 */
export function truncateMiddle(value: string, max = 48): string {
  if (value.length <= max) return value;
  const head = Math.ceil((max - 1) * 0.6);
  const tail = Math.floor((max - 1) * 0.4);
  return `${value.slice(0, head)}…${value.slice(value.length - tail)}`;
}

/**
 * A short display id (R6/B10): a long ULID/UUID collapses to its distinguishing suffix (`…ZKCZEP`)
 * so the rail/chrome never leaks a 26-char raw id; short ids pass through unchanged. The caller
 * attaches the full value as a `title`/tooltip so it stays reachable (and copyable).
 */
export function shortId(id: string, tail = 6): string {
  if (id.length <= 12) return id;
  return `…${id.slice(id.length - tail)}`;
}

/** A terse, lowercase, humanized harness label for chips (A8). */
export function harnessLabel(harnessId: string): string {
  switch (harnessId) {
    case "codex":
      return "codex";
    case "claude":
      return "claude";
    case "pi":
      return "pi";
    default:
      return harnessId;
  }
}
