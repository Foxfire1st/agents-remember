import type { GateNode, LifecycleProjection } from "../types/projection";
import { postGateDecisionDetailed } from "./actions";
import { sessionCockpitStore } from "./sessionCockpitStore";
import type { OpenSession } from "./sessions";
import { dashboardStore } from "./store";
import {
  clearSubmissionAuthorityCache,
  readSubmissionAuthority,
} from "./submissionLifecycleClient";

// The InteractionBar's answer paths (design §7.3; structured
// decision items). TWO honest channels, picked by the interaction's own shape:
//   1. STRUCTURED (AskUserQuestion `questions` — top-level additive list, with the claude-native
//      `raw.input.questions` fallback for pre-fix runners) and PERMISSION (allow/deny)
//      interactions answer through the session-direct route POST
//      /api/terminal/{session}/interaction-response — no lifecycle required (a
//      lifecycle-less seat's question used to be unanswerable). The route is
//      all-or-nothing for structured questions: the answers map must cover EVERY question by its
//      exact text (claude_stream_protocol.py `_question_answers`).
//   2. LEGACY fallback: every other kind (free-text, exotic choice sets) rides the landed
//      gate-decision channel — pending interactions project into `agent-question` gates
//      server-side and the decision note returns VERBATIM to the interaction. A structured gate
//      whose note is NOT the JSON answers map is now honestly REOPENED server-side, so structured
//      interactions NEVER take this path.
// NEVER a PTY write: on controlled sessions a terminal-typed line is queued as an ordinary user
// message and can never answer the interaction (harness_control_runner.py stdin path).

/** One structured AskUserQuestion option (additive wire: controlPendingInteraction.questions). */
export interface InteractionQuestionOption {
  label: string;
  description?: string;
}

/** One structured question page: its own text, optional header, and ITS OWN option group. */
export interface InteractionQuestion {
  text: string;
  header?: string;
  options: InteractionQuestionOption[];
  multiSelect: boolean;
}

/** The pending interaction as the catalog row serializes it, validated for rendering. */
export interface PendingInteractionView {
  interactionId: string;
  kind: string;
  /** Never fabricated: empty string when the vendor sent no prompt text (the bar says so). */
  prompt: string;
  choices: string[];
  /** The additive structured questions — [] for permission-kind and legacy payloads. */
  questions: InteractionQuestion[];
}

export type InteractionRepresentation =
  | { mode: "questions"; view: PendingInteractionView }
  /** Permission-kind: choices are exactly allow/deny — the direct route carries `response`. */
  | { mode: "permission"; view: PendingInteractionView }
  | { mode: "choices"; view: PendingInteractionView }
  /** Free-text/confirm kinds: the composer becomes the answer input, routed through the gate. */
  | { mode: "composer"; view: PendingInteractionView }
  /** Present but unanswerable — the bar states this honestly instead of rendering dead buttons. */
  | { mode: "unrepresentable"; reason: string };

const text = (value: unknown): string | undefined =>
  typeof value === "string" && value.trim().length > 0 ? value : undefined;

/** Permission responses the direct route (and the claude permission protocol) accepts, exactly. */
const PERMISSION_RESPONSES = new Set(["allow", "deny"]);

/**
 * Validate one questions array. Malformed entries are DROPPED (never rendered as dead options);
 * a list with no valid question falls back to the legacy modes below. `textKey` is the wire's
 * question-text key: `text` on the additive top-level list, `question` in the claude-native
 * `raw.input.questions` shape — the answers map must key on that EXACT text either way.
 */
function parseQuestions(raw: unknown, textKey: "text" | "question"): InteractionQuestion[] {
  if (!Array.isArray(raw)) return [];
  const questions: InteractionQuestion[] = [];
  for (const entry of raw) {
    if (typeof entry !== "object" || entry === null) continue;
    const record = entry as Record<string, unknown>;
    const questionText = text(record[textKey]);
    if (!questionText) continue;
    const options: InteractionQuestionOption[] = Array.isArray(record.options)
      ? record.options.flatMap((option) => {
          if (typeof option !== "object" || option === null) return [];
          const candidate = option as Record<string, unknown>;
          const label = text(candidate.label);
          if (!label) return [];
          const description = text(candidate.description);
          return [{ label, ...(description ? { description } : {}) }];
        })
      : [];
    const header = text(record.header);
    questions.push({
      text: questionText,
      ...(header ? { header } : {}),
      options,
      multiSelect: record.multiSelect === true,
    });
  }
  return questions;
}

/**
 * The structured questions of one `controlPendingInteraction` payload, from TWO honest sources:
 *   1. the additive top-level `questions` list (the landed wire, text key `text`);
 *   2. FALLBACK for seats whose RUNNER predates the runner-PYTHONPATH fix: those rows never flatten
 *      `questions` top-level but DO carry the full claude-native structure at
 *      `raw.input.questions` (text key `question`).
 * Anything else (absent, non-list, all-malformed) yields [] → the legacy modes below.
 */
function parseWireQuestions(payload: Record<string, unknown>): InteractionQuestion[] {
  const direct = parseQuestions(payload.questions, "text");
  if (direct.length > 0) return direct;
  const rawField = payload.raw;
  if (typeof rawField !== "object" || rawField === null) return [];
  const input = (rawField as Record<string, unknown>).input;
  if (typeof input !== "object" || input === null) return [];
  return parseQuestions((input as Record<string, unknown>).questions, "question");
}

/**
 * Classify one `controlPendingInteraction` payload (kind-aware; structured). Structured
 * questions → per-question pages answered together through the direct route; allow/deny choices →
 * permission mode (direct route `response`); other choices → legacy gate buttons; absent →
 * composer answer-mode; no usable `interactionId` → unrepresentable (there is nothing either
 * channel could target — the raw payload stays inspectable, never silently dropped).
 *
 * A questions payload holding an OPTION-LESS question also lands on unrepresentable. Such a
 * question is backend-legal (`_question_pages` checks only `isinstance(options, list)`) but has
 * nothing to pick, so the operator can never record an answer for it; and the direct route is
 * all-or-nothing (every question or none), so the combined submit could NEVER fire. Rendering it
 * as `questions` would strand the operator at a submit that can never complete, so the WHOLE
 * payload falls back instead — the prompt is surfaced in the reason, never silently dropped.
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
  const questions = parseWireQuestions(raw);
  const view: PendingInteractionView = {
    interactionId,
    kind: text(raw.kind) ?? "unknown",
    prompt: text(raw.prompt) ?? "",
    choices,
    questions,
  };
  if (questions.length > 0) {
    // Even one option-less question makes the all-or-nothing set uncompletable (the operator
    // can never record an answer for a question with nothing to pick, and a partial map is refused
    // both client- and server-side). Fall the whole payload back to unrepresentable rather than
    // render a `questions` form whose combined submit can never fire — surfacing the real prompt so
    // it is not hidden, and keeping the raw payload inspectable exactly like the no-interactionId case.
    const optionless = questions.filter((question) => question.options.length === 0);
    if (optionless.length > 0) {
      return {
        mode: "unrepresentable",
        reason:
          "a structured question here carries no options to choose from, so the harness's " +
          "all-or-nothing answer (every question or none) can never be completed — this " +
          "interaction cannot be answered from the cockpit (raw payload in the inspector). " +
          `Unanswerable question${optionless.length > 1 ? "s" : ""}: ${optionless
            .map((question) => `'${question.text}'`)
            .join("; ")}`,
      };
    }
    return { mode: "questions", view };
  }
  if (choices.length > 0 && choices.every((choice) => PERMISSION_RESPONSES.has(choice))) {
    return { mode: "permission", view };
  }
  return choices.length > 0 ? { mode: "choices", view } : { mode: "composer", view };
}

/**
 * The adapter-bound sub-agent label on a multiplexed pending interaction: the
 * codex adapter binds `raw.agentLabel` when a sub-agent thread raises the request. Absent on the
 * parent thread's singular slot — the bar badges WHO is asking only from this evidence, never a
 * fabricated name.
 */
export function pendingInteractionAgentLabel(
  raw: Record<string, unknown> | undefined,
): string | undefined {
  if (!raw) return undefined;
  const rawField = raw.raw;
  if (typeof rawField !== "object" || rawField === null) return undefined;
  return text((rawField as Record<string, unknown>).agentLabel);
}

/**
 * Every pending interaction payload on the row: the parent-thread
 * singular slot first, then the multiplexed sub-agent entries from the additive plural
 * `controlPendingInteractions`, de-duplicated by interactionId (L7 bridges carry the parent in
 * both slots). Entries without an interactionId still render (the bar says why they cannot be
 * answered) but never dedupe against each other.
 */
export function pendingInteractionPayloads(
  session: OpenSession,
): Record<string, unknown>[] {
  const payloads: Record<string, unknown>[] = [];
  if (session.controlPendingInteraction) payloads.push(session.controlPendingInteraction);
  for (const entry of session.controlPendingInteractions ?? []) {
    if (typeof entry !== "object" || entry === null) continue;
    const payload = entry as Record<string, unknown>;
    const id = text(payload.interactionId);
    if (id !== undefined && payloads.some((existing) => text(existing.interactionId) === id)) {
      continue;
    }
    payloads.push(payload);
  }
  return payloads;
}

/**
 * The representation of ONE pending interaction by id, looked up across the singular slot AND
 * the multiplexed sub-agent entries: answer-channel routing must see an agent
 * permission/questions payload exactly like the parent's — routing against only the singular
 * slot silently dropped agent answers into the legacy gate fallback.
 */
export function representSessionPendingInteraction(
  session: OpenSession,
  interactionId: string,
): InteractionRepresentation | null {
  for (const payload of pendingInteractionPayloads(session)) {
    const representation = representPendingInteraction(payload);
    if (
      representation !== null &&
      representation.mode !== "unrepresentable" &&
      representation.view.interactionId === interactionId
    ) {
      return representation;
    }
  }
  return null;
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

/**
 * The honest record a hosted-interaction gate carries after the operator's decision could NOT be
 * applied to the vendor (hosted_interactions.py `_with_delivery_failure`). It exists ONLY on a gate
 * that was decided, failed to deliver, and reopened to `open` — a never-answered gate has none — so
 * a reopened gate can show what happened to the last attempt instead of looking untouched.
 *
 * `delivery` is the single retry-safety fact and the ONLY thing the UI may assert about where the
 * answer went: `"not-sent"` proves zero request bytes reached the harness (deciding again is safe);
 * `"unknown"` means the harness may already hold it (deciding again could answer twice). Nothing
 * else about delivery is provable, so nothing else is claimed.
 */
export interface AdapterDecisionFailure {
  /** `"not-sent"` (proven undelivered) or `"unknown"` (may already be held) — verbatim from the wire. */
  delivery: string;
  /** The gate state that WAS decided before delivery failed (approved / rejected / …). */
  decision?: string;
  /** The operator's prior answer note — the plain reason, or the structured answers-map JSON. */
  decisionNote?: string;
  /** The vendor/transport failure, verbatim. */
  reason?: string;
  /** How many delivery attempts this one decision has spent (bounded server-side). */
  attempts?: number;
}

/**
 * Read the delivery-failure record off a gate packet, defensively. The projection types `packet`
 * as an opaque bag (`Record<string, unknown>`), so every field is validated rather than trusted; a
 * packet with no `adapterDecisionFailure`, or a record missing its `delivery` word, yields null (no
 * failure notice — exactly the normal open-gate case). Fields the notice does not use (the decided
 * attribution) are dropped rather than carried forward.
 */
export function readAdapterDecisionFailure(
  packet: Record<string, unknown> | undefined,
): AdapterDecisionFailure | null {
  const raw = packet?.adapterDecisionFailure;
  if (typeof raw !== "object" || raw === null) return null;
  const record = raw as Record<string, unknown>;
  const delivery = text(record.delivery);
  if (!delivery) return null;
  const decision = text(record.decision);
  const decisionNote = text(record.decisionNote);
  const reason = text(record.reason);
  const attempts = typeof record.attempts === "number" ? record.attempts : undefined;
  return {
    delivery,
    ...(decision ? { decision } : {}),
    ...(decisionNote ? { decisionNote } : {}),
    ...(reason ? { reason } : {}),
    ...(attempts !== undefined ? { attempts } : {}),
  };
}

export type InteractionAnswerOutcome =
  | { status: "answered" }
  | { status: "error"; error: string };

export type InteractionAnswerDispatchOutcome =
  | InteractionAnswerOutcome
  | { status: "blocked"; reason: "inflight" | "answered" | "missing-retry" };

// Parity with the conversation clients: a hung transport must become an ordinary failure the
// retry affordance can re-attempt, never a forever-pending answer.
const INTERACTION_RESPONSE_TIMEOUT_MS = 15_000;

type InteractionRouteResult =
  | { status: "accepted" }
  | { status: "error"; error: string; epochMismatch: boolean };

/**
 * POST the session-direct interaction-response route (NO lifecycle required).
 * Body is exactly one of {interactionId, expectedBridgeEpoch, answers} (AskUserQuestion) or
 * {interactionId, expectedBridgeEpoch, response} (permission-kind). Failures keep the server's
 * own status word + detail verbatim (200 `accepted` / 409 `not-pending` / 409
 * `bridge-epoch-mismatch` / 404·409 `unsupported` / 503).
 */
async function postInteractionResponse(
  sessionId: string,
  body: Record<string, unknown>,
): Promise<InteractionRouteResult> {
  let response: Response;
  try {
    response = await fetch(
      `/api/terminal/${encodeURIComponent(sessionId)}/interaction-response`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(INTERACTION_RESPONSE_TIMEOUT_MS),
      },
    );
  } catch (error) {
    return {
      status: "error",
      epochMismatch: false,
      error: `interaction response POST failed (transport): ${
        error instanceof Error ? error.message : String(error)
      }`,
    };
  }
  let parsed: unknown;
  try {
    parsed = await response.json();
  } catch {
    parsed = undefined;
  }
  const record =
    typeof parsed === "object" && parsed !== null
      ? (parsed as Record<string, unknown>)
      : undefined;
  const statusWord = typeof record?.status === "string" ? record.status : undefined;
  if (response.ok && statusWord === "accepted") return { status: "accepted" };
  const detail = typeof record?.detail === "string" ? record.detail : "";
  const word = statusWord ?? `HTTP ${response.status}`;
  return {
    status: "error",
    epochMismatch: word === "bridge-epoch-mismatch",
    error: detail
      ? `interaction response POST failed (${word}): ${detail}`
      : `interaction response POST failed (${word})`,
  };
}

/**
 * Answer one pending interaction through the session-direct route. The expectedBridgeEpoch comes
 * from the same submission-authority descriptor the submit flow uses (cached); on a
 * bridge-epoch-mismatch the cache is dropped and the POST is retried ONCE on the fresh epoch —
 * an interaction that died with the old bridge then refuses as `not-pending` in the server's own
 * words instead of looping on a stale epoch.
 */
async function answerViaInteractionRoute(args: {
  sessionId: string;
  interactionId: string;
  payload: { answers: Record<string, string> } | { response: string };
}): Promise<InteractionAnswerOutcome> {
  const readEpoch = async (): Promise<{ epoch: string } | { error: string }> => {
    try {
      return { epoch: (await readSubmissionAuthority(args.sessionId)).bridgeEpoch };
    } catch (error) {
      return {
        error: `submission authority unavailable: ${error instanceof Error ? error.message : String(error)}`,
      };
    }
  };
  let epochResult = await readEpoch();
  if ("error" in epochResult) return { status: "error", error: epochResult.error };
  const epoch = epochResult.epoch;
  let result = await postInteractionResponse(args.sessionId, {
    interactionId: args.interactionId,
    expectedBridgeEpoch: epoch,
    ...args.payload,
  });
  if (result.status === "error" && result.epochMismatch) {
    clearSubmissionAuthorityCache(args.sessionId);
    epochResult = await readEpoch();
    if ("error" in epochResult) return { status: "error", error: epochResult.error };
    result = await postInteractionResponse(args.sessionId, {
      interactionId: args.interactionId,
      expectedBridgeEpoch: epochResult.epoch,
      ...args.payload,
    });
  }
  return result.status === "accepted"
    ? { status: "answered" }
    : { status: "error", error: result.error };
}

/**
 * LEGACY fallback: answer one pending interaction through the landed gate-decision channel. Only
 * kinds the direct route cannot carry (free-text, exotic choice sets) take this path, and only
 * seats WITH a lifecycle ever get the projected gate. The answer text rides as the decision
 * note — the backend synchronizer returns it verbatim to the vendor interaction. Failures come
 * back with the server's words (verbatim, retryable).
 */
export async function answerPendingInteraction(args: {
  lifecycles: Record<string, LifecycleProjection>;
  sessionId: string;
  /** The seat's lifecycle binding: gates project UNDER a lifecycle, so a seat without one has
   *  a question that will NEVER appear as an answerable gate — the copy must say "cannot",
   *  not "retry in a moment". */
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
 * Start one interaction answer while synchronously acquiring the per-interaction lock. The lock
 * lives in the shared store rather than React button state, because two surface callbacks can
 * fire before either component re-renders. The exact answer (single string, or the structured
 * answers map) and draft revision stay with an error so Retry can resend the same payload and
 * clear only the unchanged draft after success.
 *
 * Channel routing is derived from the session's CURRENT pending interaction (never from the
 * caller's say-so): structured questions → direct-route answers map covering EVERY question
 * (the backend's all-or-nothing contract — a partial map is refused client-side first);
 * permission-kind → direct-route response; anything else → the legacy gate fallback.
 */
export async function submitInteractionAnswer(args: {
  session: OpenSession;
  interactionId: string;
  /** Single-string answer: a clicked choice, a permission response, or composer text. */
  answer?: string;
  /** Structured answers map keyed by EXACT question text (questions mode). */
  answers?: Record<string, string>;
  draftRevision?: number;
}): Promise<InteractionAnswerDispatchOutcome> {
  const answer = args.answer?.trim() ?? "";
  if (!args.answers && !answer) return { status: "error", error: "the answer text is empty" };
  const before = sessionCockpitStore.getState().perSession[args.session.id]?.interactionAnswer;
  if (before?.interactionId === args.interactionId) {
    if (before.inflight) return { status: "blocked", reason: "inflight" };
    if (before.answeredAt !== undefined) return { status: "blocked", reason: "answered" };
  }
  // The retained single-string form: the answer itself, or an honest summary of the map (retry
  // re-sends the MAP, never this summary).
  const retained =
    args.answers !== undefined ? Object.values(args.answers).join(" · ") : answer;
  sessionCockpitStore.getState().setInteractionAnswer(args.session.id, {
    interactionId: args.interactionId,
    inflight: true,
    answer: retained,
    ...(args.answers ? { answers: args.answers } : {}),
    draftRevision: args.draftRevision,
  });
  // Channel routing sees the multiplexed agent entries too —
  // never only the parent's singular slot.
  const representation = representSessionPendingInteraction(args.session, args.interactionId);
  let outcome: InteractionAnswerOutcome;
  if (args.answers) {
    if (
      representation?.mode !== "questions" ||
      representation.view.interactionId !== args.interactionId
    ) {
      outcome = {
        status: "error",
        error:
          "the pending questions changed or cleared before the answers could be sent — wait for the catalog poll",
      };
    } else {
      const missing = representation.view.questions.filter(
        (question) => args.answers?.[question.text] === undefined,
      );
      if (missing.length > 0) {
        outcome = {
          status: "error",
          error: `${missing.length} question(s) still unanswered — the harness requires every question answered before anything is sent`,
        };
      } else {
        // The body map carries EXACTLY the wire's question texts (the backend compares key sets).
        const answers = Object.fromEntries(
          representation.view.questions.map((question) => [
            question.text,
            args.answers?.[question.text] ?? "",
          ]),
        );
        outcome = await answerViaInteractionRoute({
          sessionId: args.session.id,
          interactionId: args.interactionId,
          payload: { answers },
        });
      }
    }
  } else if (
    representation?.mode === "permission" &&
    representation.view.interactionId === args.interactionId
  ) {
    if (!PERMISSION_RESPONSES.has(answer)) {
      outcome = {
        status: "error",
        error: "a permission answer must be exactly allow or deny",
      };
    } else {
      outcome = await answerViaInteractionRoute({
        sessionId: args.session.id,
        interactionId: args.interactionId,
        payload: { response: answer },
      });
    }
  } else {
    outcome = await answerPendingInteraction({
      lifecycles: dashboardStore.getState().lifecycles,
      sessionId: args.session.id,
      sessionLifecycleId: args.session.lifecycleId,
      interactionId: args.interactionId,
      answer,
    });
  }
  const store = sessionCockpitStore.getState();
  if (outcome.status === "answered") {
    store.setInteractionAnswer(args.session.id, {
      interactionId: args.interactionId,
      inflight: false,
      answer: retained,
      ...(args.answers ? { answers: args.answers } : {}),
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
      answer: retained,
      ...(args.answers ? { answers: args.answers } : {}),
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
    ...(previous.answers ? { answers: previous.answers } : { answer: previous.answer }),
    draftRevision: previous.draftRevision,
  });
}
