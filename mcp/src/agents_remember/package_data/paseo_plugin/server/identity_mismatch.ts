/**
 * Identity-mismatch turn ends (L51-R4): a started seat that discovered its
 * binding does not match its launch identity ends its turn with a line
 * beginning `IDENTITY MISMATCH:`.
 *
 * Pure text recognition and parent-line composition only: no host calls or
 * tracker mutations live here. `turn_ended.ts` calls these helpers before its
 * declared-wait and seat-wake paths so a mismatched seat never receives a wake
 * message and its line reaches its parent unchanged.
 */

import type { TurnEndedFacts, TurnLastText } from "./turn_ended.ts";

/** L51-R4: case-sensitive prefix marking a seat's identity-mismatch stop line. */
export const IDENTITY_MISMATCH_PREFIX = "IDENTITY MISMATCH:";

/** Turn-end log row kind written when an identity-mismatch line is routed to the parent. */
export const IDENTITY_MISMATCH_LOG_KIND = "identity-mismatch";

const DEFAULT_LAST_TEXT_LIMIT = 300;
const DEFAULT_LINE_LIMIT = 1000;

/**
 * Leading whitespace, Markdown quote/bullet/heading markers, and wrapping
 * quotes/emphasis/backticks that may precede `IDENTITY MISMATCH:` on the line.
 */
const LEADING_MARKERS = /^[ \t*_>#`"-]*/;

/** Trailing symmetric wrapper characters immediately before the prefix. */
const SYMMETRIC_OPENERS = /[`*_"]+$/;

/**
 * Strip matching closing wrappers only when the same delimiters opened before
 * `IDENTITY MISMATCH:`, so interior or trailing backticks inside the mismatch
 * payload remain intact.
 */
function stripMatchingClosingWrappers(lead: string, text: string): string {
  const opener = SYMMETRIC_OPENERS.exec(lead)?.[0] ?? "";
  if (opener === "") return text;
  const closer = [...opener].reverse().join("");
  if (!text.endsWith(closer)) return text;
  return text.slice(0, -closer.length).trimEnd();
}

/**
 * L51-R4: return the normalized `IDENTITY MISMATCH: ...` line when the ended
 * turn's last line carries that prefix (case-sensitive, behind optional leading
 * whitespace or Markdown markers), truncated to `maxLength`; else `null`.
 */
export function extractIdentityMismatchLine(
  lastText: TurnLastText,
  maxLength = DEFAULT_LAST_TEXT_LIMIT,
): string | null {
  if (lastText.kind !== "text") return null;
  const trimmed = lastText.text.trim();
  const lead = LEADING_MARKERS.exec(trimmed)?.[0] ?? "";
  const body = trimmed.slice(lead.length);
  if (!body.startsWith(IDENTITY_MISMATCH_PREFIX)) return null;
  const cleaned = stripMatchingClosingWrappers(lead, body);
  return cleaned.length <= maxLength ? cleaned : cleaned.slice(0, maxLength);
}

/**
 * L51-R4: parent-bound turn-end line carrying the mismatched seat's identity
 * (role, agent id, task), its `IDENTITY MISMATCH:` line verbatim, and its
 * report path — with no silent-end mark.
 */
export function composeIdentityMismatchParentLine(
  agentId: string,
  facts: TurnEndedFacts,
  mismatchLine: string,
  lineLimit = DEFAULT_LINE_LIMIT,
): string {
  const line =
    `turn ended: ${facts.role} ${agentId} · ${facts.task}; ` +
    `last line: ${mismatchLine}; newest report: ${facts.reportPath}`;
  return line.length <= lineLimit ? line : line.slice(0, lineLimit);
}
