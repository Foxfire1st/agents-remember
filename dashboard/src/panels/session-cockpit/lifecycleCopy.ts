import type { OpenSession } from "../../data/sessions";
import { seatVisualState } from "../../data/stateGrammar";
import { leafIdFromKey } from "../../data/taskIdentity";

// THE lifecycle/interaction copy module — copy is centralized here. Every confirm,
// residual, and round-trip string the terminate/retire/interaction surfaces render comes from
// here, so the honesty rules live in ONE place:
//   - confirms NAME the object (session · leaf · state) — never a bare "are you sure".
//   - stop residuals are INFORMATIONAL lines on an already-successfully-terminated/retired row —
//     the words "termination failed" must never appear for them, and they are never discarded.
//   - the InteractionBar's copy states the real answer channel and the real PTY truth.

/** Honest terminate confirm: names the session, its leaf, and its current state. */
export function terminateConfirmCopy(session: OpenSession): string {
  const state = seatVisualState(session).word;
  const leaf = session.leafKey
    ? ` · leaf ${leafIdFromKey(session.leafKey)}`
    : "";
  // An unclassified seat's state word is itself an em-dash; joining it before the "— kills"
  // consequence dash printed "state — —". Drop the empty state clause so the dashes never collide.
  const stateClause = state === "—" ? "" : ` · state ${state}`;
  return `end ${session.label}${leaf}${stateClause} — kills the tmux session; transcripts are kept`;
}

/**
 * The terminate route's `controlStopDetail` (e.g. "control command queue is stopped" from a
 * startup-failed bridge): the session IS terminated; the graceful control stop just had nothing
 * healthy to talk to. Informational — never a failure state.
 */
export function terminateResidualCopy(label: string, detail: string): string {
  return `${label} terminated · control-stop note (informational): ${detail}`;
}

/** A retired row's `retireControlStopError` — same posture as the terminate residual. */
export function retireResidualCopy(label: string, detail: string): string {
  return `${label} retired · control-stop note (informational): ${detail}`;
}

/** Bulk landed-cleanup outcome — the route's own counts, skipped reasons kept, never discarded. */
export function cleanupOutcomeCopy(result: {
  closed: number;
  skipped: number;
  skippedSessions: Array<{ session: string; reason: string }>;
}): string {
  const skipped =
    result.skipped > 0
      ? ` · skipped ${result.skipped} (${result.skippedSessions
          .map((entry) => `${entry.session}: ${entry.reason}`)
          .join(", ")})`
      : "";
  return `ended ${result.closed}${skipped}`;
}

/** A lost/non-OK cleanup response cannot prove whether the server mutated anything. */
export function cleanupFailureCopy(failure: {
  targets: Array<{ id: string; label: string }>;
}): string {
  return `cleanup result unavailable — intended rows: ${failure.targets
    .map((target) => `${target.label} (${target.id})`)
    .join(", ")}`;
}

// ── The WorkingLine + Stop-turn (design §9.7) ─────────────────────────────

/** The cockpit has NO interrupt route yet — the control names the gap, honestly. */
export const STOP_TURN_DISABLED_REASON =
  "interrupt requires UA-7 — no cancel-turn route exists on the control bridge yet";

// ── The InteractionBar (design §7.3) ────────────────────────────────────────────────────

/** The honesty hint: the PTY can NEVER answer an interaction on a controlled session. */
export const INTERACTION_HONESTY_HINT =
  "answer here — text typed in the terminal becomes a queued message, not an answer";

export const INTERACTION_ANSWERING = "answering…";

/** Success is poll-bounded and the bar says so (the pending row clears with the catalog poll). */
export const INTERACTION_ANSWERED =
  "answered — waiting for the agent (the question clears with the next catalog poll, ≤ ~2.5 s)";

/** Composer answer-mode label (non-choice kinds): routed through the gate channel, not /submit. */
export const INTERACTION_COMPOSER_MODE =
  "the composer below is answering the pending question — routed through the gate channel, not the terminal";

export const INTERACTION_NO_PROMPT_TEXT =
  "the harness sent a question without prompt text — raw payload in the inspector";

// ── Structured decision items ─────────────────────────────────────────────
// The direct session route answers structured questions ALL-OR-NOTHING: every question needs an
// answer before anything is sent, and the copy says so.

/** Head line for a multi-question interaction — the all-or-nothing contract, stated. */
export const INTERACTION_QUESTIONS_PROGRESS = (answered: number, total: number): string =>
  `${answered} of ${total} questions answered — all answers are sent together`;

/** multiSelect questions join their toggled labels into ONE answer per question. */
export const INTERACTION_MULTISELECT_HINT = "choose one or more, then confirm";

export const INTERACTION_MULTISELECT_CONFIRM = (count: number): string =>
  count === 0 ? "confirm selection" : `confirm ${count} selected`;

/** A question's recorded answer while sibling questions are still open. */
export const INTERACTION_QUESTION_RECORDED = (answer: string): string =>
  `recorded: ${answer}`;

// ── PTY archetypes (design §1.4) ────────────────────────────────────────────────────────

/** True ⇒ a controlled session: the PTY renders the runner's line-log; no vendor TUI exists. */
export function isControlledSession(
  session: Pick<OpenSession, "controlState">,
): boolean {
  return (
    session.controlState !== undefined && session.controlState !== "unsupported"
  );
}

export function paneArchetypeCopy(
  session: Pick<OpenSession, "controlState">,
): string {
  return isControlledSession(session)
    ? "controlled — the pane shows the runner line-log; typed lines queue as messages"
    : "legacy raw — the vendor TUI runs in this pane";
}

/** The accessible pane name: label + harness + state, per pane. */
export function paneAccessibleName(session: OpenSession): string {
  const state = seatVisualState(session).word;
  return `terminal: ${session.label}${session.harness ? ` · ${session.harness}` : ""} · ${state}`;
}

/** screenReaderMode's honest cost note. */
export const SCREEN_READER_MODE_NOTE =
  "screen-reader mode: xterm maintains an accessibility tree of the buffer — costs rendering performance, so it is opt-in";
