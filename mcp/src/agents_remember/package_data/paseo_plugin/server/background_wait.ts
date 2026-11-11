/**
 * The background wait: a turn that ends while a process the seat started keeps
 * running is not a silent end when the seat says so.
 *
 * A seat declares it with a line of its last assistant message that starts
 * `WAITING FOR BACKGROUND:` and names, for each process, what it is, how to see
 * it (its process id or its log file) and when it should end. A detached process
 * outlives every harness's own background tracking, so any seat may declare this
 * wait whatever its provider, and the helper wait (`WAITING FOR SUBAGENTS:`) too.
 * A turn the harness itself opens at a tracked process's or helper's end is the
 * seat's own continuation: the wait ends at the seat's next end without a
 * declaration, as for any other turn.
 *
 * The plugin's check comes three minutes after the declaring end unless the
 * declaration names `check in <N> minutes`, N from 1 to 30; a value outside that
 * range is clamped and the declaration's row says so. One end may declare both
 * waits, and its one check then asks about both: it comes at the earlier of the
 * two times, so neither wait is checked later than it asked.
 *
 * Pure text and naming only: the wait's state, its timer and its gates are the
 * seat-wake machinery's in `turn_ended.ts`, which calls these functions.
 */

import type { TurnEndedFacts } from "./turn_ended.ts";

/** The declaration's keyword, and the first line of the check the plugin sends for it. */
export const BACKGROUND_WAIT_KEYWORD = "WAITING FOR BACKGROUND:";
export const BACKGROUND_WAIT_FIRST_LINE = "[ar-plugin] background wait:";
/** The first line of the one check an end that declared both waits is sent. */
export const BOTH_WAITS_FIRST_LINE = "[ar-plugin] subagent and background wait:";

/** The check's default delay and the range a declaration may name, in minutes. */
export const BACKGROUND_WAIT_DEFAULT_MINUTES = 3;
export const BACKGROUND_WAIT_MIN_MINUTES = 1;
export const BACKGROUND_WAIT_MAX_MINUTES = 30;

/** How much of the declaration line the check quotes back to the seat. */
export const BACKGROUND_WAIT_QUOTE_LIMIT = 300;

/**
 * Any seat may declare either wait: a seat that is not on an ACP connection declares the
 * helper wait too, so its seat-back message names that declaration. The ACP
 * seat keeps its own wake sentence, which already names both.
 */
export const HELPER_WAKE_SENTENCE =
  ' If you are waiting for helpers, end your turn with a line that starts "WAITING FOR SUBAGENTS:", followed by each helper\'s progress.';

/**
 * The sentence the seat-back message adds for every seat, so a seat that ended
 * while its own process runs learns how to say so.
 */
export const BACKGROUND_WAKE_SENTENCE =
  ' If you are waiting for a process you started, end your turn with a line that starts "WAITING FOR BACKGROUND:" naming each process, its process id or log file, and when it should end.';

/**
 * Every marker a line may carry before a keyword: leading spaces, the Markdown
 * markers `*`, `_`, `>`, `-` and `#`, and the quoting marks `` ` `` and `"`, also
 * where they wrap the keyword itself. The helper wait reads its keyword the same way.
 */
const KEYWORD_LEAD = /^[ \t*_>#`"-]*/;

/** `check in <N> minutes`, anywhere from the declaration line on; N is an integer. */
const CHECK_IN = /\bcheck in\s+(-?\d+)\s+minutes?\b/i;

/** The index of the first line that starts with the keyword behind the markers, else -1. */
export function declarationLineIndex(lines: readonly string[], keyword: string): number {
  return lines.findIndex((line) => line.replace(KEYWORD_LEAD, "").startsWith(keyword));
}

/** Whether any line of the text starts with the keyword behind the markers. */
export function textDeclares(text: string | null, keyword: string): boolean {
  if (text === null) return false;
  return declarationLineIndex(text.split(/\r?\n/), keyword) >= 0;
}

/** One background declaration as the plugin reads it. */
export interface BackgroundDeclaration {
  /** The minutes the check waits, after clamping. */
  readonly checkMinutes: number;
  /** The N the declaration named, or `null` when it named none. */
  readonly askedMinutes: number | null;
  /** The declaration line after the keyword, shortened, so the check can quote it. */
  readonly named: string;
}

/**
 * The background declaration in the ended turn's last assistant message, or
 * `null` when the message declares none. The keyword is case-sensitive and must
 * open a line behind the markers; `check in <N> minutes` is read from the
 * declaration line and the lines after it.
 */
export function readBackgroundDeclaration(text: string | null): BackgroundDeclaration | null {
  if (text === null) return null;
  const lines = text.split(/\r?\n/);
  const index = declarationLineIndex(lines, BACKGROUND_WAIT_KEYWORD);
  if (index < 0) return null;
  const line = lines[index].replace(KEYWORD_LEAD, "");
  const named = line
    .slice(BACKGROUND_WAIT_KEYWORD.length)
    .replace(/^[ \t*_`"]+/, "")
    .trim();
  const matched = CHECK_IN.exec(lines.slice(index).join("\n"));
  const askedMinutes = matched === null ? null : Number.parseInt(matched[1], 10);
  const checkMinutes =
    askedMinutes === null
      ? BACKGROUND_WAIT_DEFAULT_MINUTES
      : Math.min(BACKGROUND_WAIT_MAX_MINUTES, Math.max(BACKGROUND_WAIT_MIN_MINUTES, askedMinutes));
  return {
    checkMinutes,
    askedMinutes,
    named: named.length <= BACKGROUND_WAIT_QUOTE_LIMIT ? named : named.slice(0, BACKGROUND_WAIT_QUOTE_LIMIT),
  };
}

/**
 * The declared row's reason: the check time the plugin uses and where it came
 * from — the default, the named value, or the clamped one. `effectiveMinutes` is
 * the time the one check actually waits when a helper wait set an earlier one.
 */
export function backgroundDeclaredReason(declaration: BackgroundDeclaration, effectiveMinutes: number): string {
  const asked = declaration.askedMinutes;
  const source =
    asked === null
      ? `check in ${declaration.checkMinutes} minutes (default)`
      : asked === declaration.checkMinutes
        ? `check in ${asked} minutes (named)`
        : `check in ${asked} minutes is outside ${BACKGROUND_WAIT_MIN_MINUTES} to ${BACKGROUND_WAIT_MAX_MINUTES}, clamped to ${declaration.checkMinutes}`;
  const earlier =
    effectiveMinutes < declaration.checkMinutes
      ? `; the helper wait's check comes first, at ${effectiveMinutes} minutes`
      : "";
  return `declared a background wait; ${source}${earlier}`;
}

/** The two declared waits a check may cover, as their rows name them. */
export type DeclaredWaitName = "subagent" | "background";

/**
 * An open declared wait as the host-activity record publishes it, so AR's
 * surfaces can show the seat as waiting rather than inactive: what it waits for,
 * since when the wait has been open, the plugin's next check while one is armed,
 * and the background declaration's own words.
 */
export interface PublishedDeclaredWait {
  readonly waitsFor: readonly DeclaredWaitName[];
  readonly since: string;
  readonly checkDueAt?: string;
  readonly detail?: string;
}

/** The one value a publication compares, so an unchanged wait writes nothing. */
export function declaredWaitSignature(wait: PublishedDeclaredWait | undefined): string {
  if (wait === undefined) return "";
  return [wait.waitsFor.join("+"), wait.since, wait.checkDueAt ?? "", wait.detail ?? ""].join("|");
}

/** The kinds of a pending check, by the waits it covers. */
export type WaitCheckKind = "subagent-check" | "background-check" | "subagent-and-background-check";

/** The check kind for the waits one end declared. */
export function waitCheckKind(subagents: boolean, background: boolean): WaitCheckKind {
  if (subagents && background) return "subagent-and-background-check";
  return background ? "background-check" : "subagent-check";
}

/** The waits a pending wait's kind covers; none for a wait that is not a check. */
export function waitNamesOf(kind: string): readonly DeclaredWaitName[] {
  if (kind === "subagent-check") return ["subagent"];
  if (kind === "background-check") return ["background"];
  if (kind === "subagent-and-background-check") return ["subagent", "background"];
  return [];
}

/** Whether a pending wait is a declared wait's check. */
export function isWaitCheck(kind: string): boolean {
  return waitNamesOf(kind).length > 0;
}

/** A declared wait's row kind: `<wait>-wait-<event>`, as the helper wait's rows already are. */
export function waitRowKind(
  name: DeclaredWaitName,
  event: "declared" | "check-sent" | "check-skipped" | "ended",
): string {
  return `${name}-wait-${event}`;
}

/** Whether a first line is one of the background checks' own first lines. */
export function startsBackgroundCheck(firstLine: string): boolean {
  return firstLine.startsWith(BACKGROUND_WAIT_FIRST_LINE) || firstLine.startsWith(BOTH_WAITS_FIRST_LINE);
}

/**
 * The check for an end that declared a background wait, alone or with a helper
 * wait. It names the seat as the seat message does, quotes what the seat
 * declared, asks for each process's state (and, with `subagentKeyword`, for each
 * helper's progress) and says how to declare again; the seat's answer stays in
 * its own chat.
 */
export function composeBackgroundWaitCheck(
  agentId: string,
  facts: TurnEndedFacts,
  declaration: BackgroundDeclaration,
  subagentKeyword: string | null,
): string {
  const role = facts.role === "" ? "" : facts.role[0].toUpperCase() + facts.role.slice(1);
  const first = subagentKeyword === null ? BACKGROUND_WAIT_FIRST_LINE : BOTH_WAITS_FIRST_LINE;
  const quoted = declaration.named === "" ? "" : `you declared: ${declaration.named}\n`;
  const processes =
    "Report each background process's state here: what it is, whether it still runs by its process id or log file, and what it left.";
  const asked =
    subagentKeyword === null
      ? `${processes} Collect what finished. If a process still runs, end your turn with a new "${BACKGROUND_WAIT_KEYWORD}" line; otherwise go on with your stage.`
      : "Report your helpers' progress here: each helper, what it does, its status, and what came back. " +
        `${processes} Collect what finished. If helpers still run, end your turn with a new "${subagentKeyword}" line, ` +
        `and if a process still runs, with a new "${BACKGROUND_WAIT_KEYWORD}" line; otherwise go on with your stage.`;
  return `${first} ${role} ${agentId} · ${facts.task}\n${quoted}${asked}`;
}
