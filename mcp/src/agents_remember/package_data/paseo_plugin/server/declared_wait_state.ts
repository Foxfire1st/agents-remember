/**
 * The declared waits' own state on the turn-end tracker: which waits a seat's
 * open declaration holds, how one turn end opens, renews or ends it, the rows its
 * events write, when its check falls due, and the view the host-activity record
 * publishes so AR's surfaces show the seat as waiting. The tracker, its timers
 * and the gates stay in `turn_ended.ts`; this module only reads and writes the
 * declaration and its rows.
 */

import { backgroundDeclaredReason, isWaitCheck, waitNamesOf, waitRowKind } from "./background_wait.ts";
import type { BackgroundDeclaration, DeclaredWaitName, PublishedDeclaredWait } from "./background_wait.ts";
import { isoWithOffset, turnEndRow } from "./turn_log.ts";
import type { DeclaredWait, PendingSeatWake, TurnEndedFacts, TurnEndedTracker } from "./turn_ended.ts";

/** The parts of a wait its skip rows name; a check that was never armed has no more. */
export type WaitRowSubject = Pick<PendingSeatWake, "kind" | "facts" | "seat" | "ordinal">;

/** What one turn end declared, and whether the host already reports its seat archived. */
export interface DeclaringEnd {
  readonly seat: string;
  readonly facts: TurnEndedFacts;
  readonly subagents: boolean;
  readonly background: BackgroundDeclaration | null;
  readonly archived: boolean;
}

/** The clock, the timer stop and the bounds one judged end works under. */
export interface DeclaredEndBounds {
  readonly now: () => Date;
  readonly stopTimer: (handle: unknown) => void;
  /** The most open declarations the tracker keeps; the oldest seat's is evicted past it. */
  readonly limit: number;
  /** The helper wait's check delay, which a background wait's own time may undercut. */
  readonly helperCheckMs: number;
}

/**
 * A check's own row kinds — one `<wait>-wait-check-skipped` row
 * for each wait the check covers; every other wait keeps the row kind it is given.
 */
export function appendWaitRows(
  tracker: TurnEndedTracker,
  wait: WaitRowSubject,
  otherKind: string,
  reason: string,
  now: () => Date,
): void {
  const names = waitNamesOf(wait.kind);
  const kinds = names.length === 0 ? [otherKind] : names.map((name) => waitRowKind(name, "check-skipped"));
  for (const kind of kinds) {
    tracker.append(turnEndRow(kind, wait.facts, wait.seat, wait.ordinal, reason, isoWithOffset(now())));
  }
}

/** A skipped send's rows; a check writes its own skip kinds. */
export function appendSkipRows(tracker: TurnEndedTracker, wait: WaitRowSubject, reason: string, now: () => Date): void {
  appendWaitRows(tracker, wait, "send-skipped", reason, now);
}

/** The waits an open declaration holds, in row order. */
export function declaredNames(declared: DeclaredWait): DeclaredWaitName[] {
  const names: DeclaredWaitName[] = [];
  if (declared.subagents) names.push("subagent");
  if (declared.background !== null) names.push("background");
  return names;
}

/** The seat's open declared wait as the host-activity record shows it, or `null`. */
export function openDeclaredWait(tracker: TurnEndedTracker, seat: string): PublishedDeclaredWait | null {
  const open = tracker.declarations.get(seat);
  if (open === undefined) return null;
  const check = tracker.waits.get(seat);
  const due = check !== undefined && isWaitCheck(check.kind) ? new Date(check.dueAt).toISOString() : undefined;
  const detail = open.background === null || open.background.named === "" ? undefined : open.background.named;
  return {
    waitsFor: declaredNames(open),
    since: open.since,
    ...(due === undefined ? {} : { checkDueAt: due }),
    ...(detail === undefined ? {} : { detail }),
  };
}

/** Tell the host-activity record the seat's wait as it stands now. */
export function announceWait(tracker: TurnEndedTracker, seat: string): void {
  tracker.signals.declaredWait?.(seat, openDeclaredWait(tracker, seat));
}

/** One `<wait>-wait-ended` row for each named wait of an open declaration. */
export function appendEndedRows(
  tracker: TurnEndedTracker,
  seat: string,
  declared: DeclaredWait,
  names: readonly DeclaredWaitName[],
  reason: string,
  now: () => Date,
): void {
  for (const name of names) {
    tracker.append(turnEndRow(waitRowKind(name, "ended"), declared.facts, seat, declared.ordinal, reason, isoWithOffset(now())));
  }
}

/**
 * The check's delay: three minutes for helpers, the declared time for a
 * background process, and the earlier of the two when one end declared both.
 */
export function checkDelayMs(declared: DeclaredWait, helperCheckMs: number): number {
  const background = declared.background === null ? null : declared.background.checkMinutes * 60_000;
  if (background === null) return helperCheckMs;
  return declared.subagents ? Math.min(helperCheckMs, background) : background;
}

/** The seat's next end without a declaration ends its wait — every wait it holds. */
export function endDeclaredWait(
  tracker: TurnEndedTracker,
  seat: string,
  facts: TurnEndedFacts,
  now: () => Date,
  stopTimer: (handle: unknown) => void,
  reason = "a turn of the seat ended with no declaration",
): void {
  const open = tracker.declarations.get(seat);
  if (open === undefined) return;
  tracker.declarations.delete(seat);
  // The check this wait still holds is dropped with it; the wait's own row below
  // is the one row the turn-end log needs, so no second cancelled-wait row is written.
  const pending = tracker.waits.get(seat);
  if (pending !== undefined && isWaitCheck(pending.kind)) {
    tracker.waits.delete(seat);
    stopTimer(pending.timer);
    pending.finish();
  }
  appendEndedRows(tracker, seat, { ...open, facts }, declaredNames(open), reason, now);
}

/**
 * The declared wait judged at one turn end. An end without a declaration ends the
 * seat's open wait, and so does a declaring end of a seat the host already reports
 * archived, which opens nothing for the next one: no wait outlives its seat. Any
 * other declaring end opens or renews the wait and writes one declared row for each
 * wait it names. A wait that ends here is announced as ended; the wait this end
 * opens is returned, so its check can be armed before it is announced.
 */
export function judgeDeclaredEnd(
  tracker: TurnEndedTracker,
  end: DeclaringEnd,
  bounds: DeclaredEndBounds,
): DeclaredWait | null {
  const declares = end.subagents || end.background !== null;
  if (!declares || end.archived) {
    const wasOpen = tracker.declarations.has(end.seat);
    const reason = declares ? "the seat was archived" : undefined;
    endDeclaredWait(tracker, end.seat, end.facts, bounds.now, bounds.stopTimer, reason);
    if (wasOpen) announceWait(tracker, end.seat);
    return null;
  }
  const wait = rememberDeclaration(tracker, end, bounds);
  const row = (name: DeclaredWaitName, reason: string) =>
    turnEndRow(waitRowKind(name, "declared"), end.facts, end.seat, wait.ordinal, reason, isoWithOffset(bounds.now()));
  if (end.subagents) tracker.append(row("subagent", "declared a helper wait"));
  if (end.background !== null) {
    const effectiveMinutes = checkDelayMs(wait, bounds.helperCheckMs) / 60_000;
    tracker.append(row("background", backgroundDeclaredReason(end.background, effectiveMinutes)));
  }
  return wait;
}

/**
 * One declaration, its ordinal being the seat's next in the wait.
 * A wait the seat held and this end no longer declares ends here, with its row:
 * an end that declares only a background wait ends the helper wait, and the other
 * way round. Past the bound the oldest seat's declaration is evicted, and that seat
 * is announced as no longer waiting, so the host-activity record's copy of the
 * wait goes with it.
 */
function rememberDeclaration(tracker: TurnEndedTracker, end: DeclaringEnd, bounds: DeclaredEndBounds): DeclaredWait {
  const { seat, facts, subagents, background } = end;
  const { now } = bounds;
  const previous = tracker.declarations.get(seat);
  const since = previous?.since ?? new Date(now().getTime()).toISOString();
  const declared: DeclaredWait = { ordinal: (previous?.ordinal ?? 0) + 1, facts, subagents, background, since };
  if (previous !== undefined) {
    const dropped = declaredNames(previous).filter((name) => !declaredNames(declared).includes(name));
    appendEndedRows(tracker, seat, { ...previous, facts }, dropped, "a turn of the seat ended without this declaration", now);
  }
  tracker.declarations.delete(seat);
  tracker.declarations.set(seat, declared);
  while (tracker.declarations.size > bounds.limit) {
    const oldest = tracker.declarations.keys().next();
    if (oldest.done === true) break;
    tracker.declarations.delete(oldest.value);
    announceWait(tracker, oldest.value);
  }
  return declared;
}
