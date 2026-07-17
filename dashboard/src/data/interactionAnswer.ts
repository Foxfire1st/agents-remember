import type { GateNode, LifecycleProjection } from "../types/projection";
import { postGateDecisionDetailed } from "./actions";
import { sessionCockpitStore } from "./sessionCockpitStore";
import type { OpenSession } from "./sessions";
import { dashboardStore } from "./store";

// The InteractionBar's answer path (260715-FEUI-L6 R4, design §7.3 — the ONE ruled channel):
// pending vendor interactions are already projected into `agent-question` gates server-side
// (serving/hosted_interactions.py), and a developer gate decision's note is returned VERBATIM to
// the exact pending interaction (`_gate_response` prefers `decisionNote`). So answering =
// POST /api/actions/approve with the answer text as the note, against the matching gate.
// NEVER a PTY write: on controlled sessions a terminal-typed line is queued as an ordinary
// user message and can never answer the interaction (harness_control_runner.py stdin path).

/** The pending interaction as the catalog row serializes it, validated for rendering. */
export interface PendingInteractionView {
  interactionId: string;
  kind: string;
  /** Never fabricated: empty string when the vendor sent no prompt text (the bar says so). */
  prompt: string;
  choices: string[];
}

export type InteractionRepresentation =
  | { mode: "choices"; view: PendingInteractionView }
  /** Free-text/confirm kinds: the composer becomes the answer input, routed through the gate. */
  | { mode: "composer"; view: PendingInteractionView }
  /** Present but unanswerable — the bar states this honestly instead of rendering dead buttons. */
  | { mode: "unrepresentable"; reason: string };

const text = (value: unknown): string | undefined =>
  typeof value === "string" && value.trim().length > 0 ? value : undefined;

/**
 * Classify one `controlPendingInteraction` payload (kind-aware, F8). Choices present → buttons;
 * absent → composer answer-mode; no usable `interactionId` → unrepresentable (there is nothing
 * the gate channel could target — the raw payload stays inspectable, never silently dropped).
 */
export function representPendingInteraction(
  raw: Record<string, unknown> | undefined,
): InteractionRepresentation | null {
  if (!raw) return null;
  const interactionId = text(raw.interactionId);
  if (!interactionId) {
    return {
      mode: "unrepresentable",
      reason:
        "a pending interaction exists but carries no interactionId — it cannot be answered from here (raw payload in the inspector)",
    };
  }
  const choices = Array.isArray(raw.choices)
    ? raw.choices.filter((choice): choice is string => typeof choice === "string" && choice.length > 0)
    : [];
  const view: PendingInteractionView = {
    interactionId,
    kind: text(raw.kind) ?? "unknown",
    prompt: text(raw.prompt) ?? "",
    choices,
  };
  return choices.length > 0 ? { mode: "choices", view } : { mode: "composer", view };
}

export interface InteractionGateRef {
  lifecycleId: string;
  gate: GateNode;
}

/**
 * The projected `agent-question` gate matching one (session, interaction) pair. The synchronizer
 * stamps `packet.adapterInteraction.{sessionId,interactionId}`; the gate rides its lifecycle's
 * projection. Null = the gate has not appeared in the projection yet (its creation is bounded by
 * the observe/poll cadence) — the bar states that instead of inventing an answer path.
 */
export function findInteractionGate(
  lifecycles: Record<string, LifecycleProjection>,
  sessionId: string,
  interactionId: string,
): InteractionGateRef | null {
  for (const lifecycle of Object.values(lifecycles)) {
    const gate = lifecycle.gate;
    if (!gate || gate.kind !== "agent-question" || gate.state !== "open") continue;
    const adapter = gate.packet?.adapterInteraction;
    if (typeof adapter !== "object" || adapter === null) continue;
    const identity = adapter as Record<string, unknown>;
    if (identity.sessionId === sessionId && identity.interactionId === interactionId) {
      return { lifecycleId: lifecycle.id, gate };
    }
  }
  return null;
}

export type InteractionAnswerOutcome =
  | { status: "answered" }
  | { status: "error"; error: string };

export type InteractionAnswerDispatchOutcome =
  | InteractionAnswerOutcome
  | { status: "blocked"; reason: "inflight" | "answered" | "missing-retry" };

/**
 * Answer one pending interaction through the landed gate-decision channel (the SOLE answer path).
 * The answer text rides as the decision note — the backend synchronizer returns it verbatim to
 * the vendor interaction. Failures come back with the server's words (F7: verbatim, retryable).
 */
export async function answerPendingInteraction(args: {
  lifecycles: Record<string, LifecycleProjection>;
  sessionId: string;
  /** The seat's lifecycle binding: gates project UNDER a lifecycle, so a seat without one has
   *  a question that will NEVER appear as an answerable gate — the copy must say "cannot",
   *  not "retry in a moment" (review finding 2). */
  sessionLifecycleId: string | undefined;
  interactionId: string;
  answer: string;
}): Promise<InteractionAnswerOutcome> {
  const answer = args.answer.trim();
  if (!answer) return { status: "error", error: "the answer text is empty" };
  const ref = findInteractionGate(args.lifecycles, args.sessionId, args.interactionId);
  if (!ref) {
    return {
      status: "error",
      error: args.sessionLifecycleId
        ? "the answer channel (agent-question gate) has not appeared in the projection yet — gate creation is poll-bounded; retry in a moment"
        : "this seat has no lifecycle, so its question is never projected as an answerable gate — it cannot be answered from the cockpit (a gate-id-only projection is an upstream ask)",
    };
  }
  const result = await postGateDecisionDetailed(ref.lifecycleId, "approve", {
    gateId: ref.gate.id,
    note: answer,
  });
  if (result.status === "recorded") return { status: "answered" };
  return {
    status: "error",
    error: result.detail
      ? `gate decision POST failed (${result.status}): ${result.detail}`
      : `gate decision POST failed (${result.status})`,
  };
}

/** Store-backed lock shared by every SessionComposer and InteractionBar surface. */
export function interactionAnswerIsLocked(sessionId: string, interactionId: string): boolean {
  const state = sessionCockpitStore.getState().perSession[sessionId]?.interactionAnswer;
  return Boolean(
    state?.interactionId === interactionId &&
      (state.inflight || state.answeredAt !== undefined),
  );
}

/**
 * Start one gate answer while synchronously acquiring the per-interaction lock. The lock lives in
 * the shared store rather than React button state, because two surface callbacks can fire before
 * either component re-renders. The exact answer and draft revision stay with an error so Retry can
 * clear only the unchanged draft after success.
 */
export async function submitInteractionAnswer(args: {
  session: OpenSession;
  interactionId: string;
  answer: string;
  draftRevision?: number;
}): Promise<InteractionAnswerDispatchOutcome> {
  const before = sessionCockpitStore.getState().perSession[args.session.id]?.interactionAnswer;
  if (before?.interactionId === args.interactionId) {
    if (before.inflight) return { status: "blocked", reason: "inflight" };
    if (before.answeredAt !== undefined) return { status: "blocked", reason: "answered" };
  }
  sessionCockpitStore.getState().setInteractionAnswer(args.session.id, {
    interactionId: args.interactionId,
    inflight: true,
    answer: args.answer,
    draftRevision: args.draftRevision,
  });
  const outcome = await answerPendingInteraction({
    lifecycles: dashboardStore.getState().lifecycles,
    sessionId: args.session.id,
    sessionLifecycleId: args.session.lifecycleId,
    interactionId: args.interactionId,
    answer: args.answer,
  });
  const store = sessionCockpitStore.getState();
  if (outcome.status === "answered") {
    store.setInteractionAnswer(args.session.id, {
      interactionId: args.interactionId,
      inflight: false,
      answer: args.answer,
      draftRevision: args.draftRevision,
      answeredAt: Date.now(),
    });
    if (args.draftRevision !== undefined) {
      store.clearComposerDraftIfRevision(args.session.id, args.draftRevision);
    }
  } else {
    store.setInteractionAnswer(args.session.id, {
      interactionId: args.interactionId,
      inflight: false,
      answer: args.answer,
      draftRevision: args.draftRevision,
      error: outcome.error,
    });
  }
  return outcome;
}

export function retryStoredInteractionAnswer(
  session: OpenSession,
  interactionId: string,
): Promise<InteractionAnswerDispatchOutcome> {
  const previous = sessionCockpitStore.getState().perSession[session.id]?.interactionAnswer;
  if (
    !previous ||
    previous.interactionId !== interactionId ||
    !previous.error ||
    previous.inflight ||
    previous.answeredAt !== undefined
  ) {
    return Promise.resolve({ status: "blocked", reason: "missing-retry" });
  }
  return submitInteractionAnswer({
    session,
    interactionId,
    answer: previous.answer,
    draftRevision: previous.draftRevision,
  });
}
