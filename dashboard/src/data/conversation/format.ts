// Presentation conventions, encoding the developer visual-findings rules
// (260720-developer-dashboard-visual-findings §A) as one tested vocabulary:
//  A1 — em-dash = a genuinely absent value. Never chain dashes.
//  A4 — durations are humanized with fixed sensible precision.

/** The absent-value glyph (A1): a value that genuinely does not apply. Never chain these. */
const ABSENT = "—";

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
