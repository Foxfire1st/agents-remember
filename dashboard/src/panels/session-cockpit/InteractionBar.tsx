import {
  useCallback,
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  type RefObject,
} from "react";

import {
  pendingInteractionAgentLabel,
  pendingInteractionPayloads,
  representPendingInteraction,
  retryStoredInteractionAnswer,
  submitInteractionAnswer,
} from "../../data/interactionAnswer";
import { sessionCockpitStore, useSessionCockpit } from "../../data/sessionCockpitStore";
import type { OpenSession } from "../../data/sessions";
import type { SessionComposerHandle } from "../SessionComposer";
import { INTERACTION_COMPOSER_MODE } from "./lifecycleCopy";
import { bar } from "./interactionStyles";
import {
  InteractionAnnounce,
  InteractionBody,
  InteractionHead,
  InteractionHint,
  InteractionStatusRow,
} from "./interactionParts";

// The InteractionBar (design §7.3; structured
// decision items): the ONE interaction axis. Sits directly ABOVE the composer — never replaces it —
// and appears only while the focused row carries `controlPendingInteraction` (or multiplexed
// sub-agent entries in `controlPendingInteractions`, one bar per pending interaction). Answer
// channels are
// picked by the interaction's shape (data/interactionAnswer): structured AskUserQuestion pages and
// allow/deny permissions POST the session-direct interaction-response route (no lifecycle needed);
// legacy kinds keep the gate-decision fallback. On controlled sessions the
// PTY can NEVER answer — a terminal-typed line queues an ordinary message, which is exactly what
// the honesty hint states. Kind-aware: structured questions → one option GROUP PER QUESTION (never
// the legacy flat concatenation), multiSelect toggles join into one answer per question, and the
// interaction submits only once EVERY question has an answer (the backend's all-or-nothing
// contract); other choices → buttons; free-text/confirm kinds → the composer becomes the answer
// input (gate-routed, visibly labeled); unrepresentable payloads say so instead of rendering dead
// chrome. Round-trip states are store-backed (survive view switches): answering… (disabled,
// in-flight) → verbatim error + retry, or answered — waiting for the agent (poll-bounded, and the
// bar says so). The bar never steals focus on appearance; when it clears while holding focus,
// focus returns to the element that had it before. The render parts live in interactionParts.tsx
// and the shared styles in interactionStyles.ts.

export interface InteractionBarHandle {
  submitComposerAnswer(text: string, revision: number): void;
}

export const InteractionBar = forwardRef<
  InteractionBarHandle,
  {
    session: OpenSession;
    /** The composer input — the answer input
   *  for non-choice kinds. Optional so the bar renders honestly without one. */
    composerRef?: RefObject<SessionComposerHandle | null>;
  }
>(function InteractionBar({ session, composerRef }, forwardedRef) {
  // Multiplexed sub-agent approvals: one bar per pending
  // interaction — the parent's singular slot first, then the agent entries — each labeled
  // with its adapter-bound agent badge and answered through the same channel.
  const payloads = pendingInteractionPayloads(session);
  const activeInteractionIds = payloads
    .map((payload) =>
      typeof payload.interactionId === "string" && payload.interactionId !== ""
        ? payload.interactionId
        : undefined,
    )
    .filter((id): id is string => id !== undefined);
  if (payloads.length === 0) return null;
  return (
    <>
      {payloads.map((payload, index) => (
        <SingleInteractionBar
          key={
            typeof payload.interactionId === "string" && payload.interactionId !== ""
              ? payload.interactionId
              : `unidentified-${index}`
          }
          ref={index === 0 ? forwardedRef : undefined}
          session={session}
          interaction={payload}
          activeInteractionIds={activeInteractionIds}
          composerRef={composerRef}
        />
      ))}
    </>
  );
});

function useInteractionState(
  session: OpenSession,
  interaction: Record<string, unknown>,
  activeInteractionIds: readonly string[],
) {
  const answerState = useSessionCockpit(
    (state) => state.perSession[session.id]?.interactionAnswer,
  );
  const representation = representPendingInteraction(interaction);
  const interactionId =
    representation && representation.mode !== "unrepresentable"
      ? representation.view.interactionId
      : undefined;

  // Clear a stale round-trip record when its interaction is no longer pending (a NEW question
  // must never inherit the previous answer's state). This includes a FOLLOWING unrepresentable
  // payload — its id is absent from `activeInteractionIds`, and the old "answered — waiting"
  // line must not render beside it. A sibling bar's different id is NOT staleness.
  useEffect(() => {
    if (!answerState) return;
    if (!activeInteractionIds.includes(answerState.interactionId)) {
      sessionCockpitStore.getState().setInteractionAnswer(session.id, undefined);
    }
  }, [answerState, activeInteractionIds, session.id]);

  // The round-trip record is per-session; with multiplexed bars it is THIS bar's status only
  // when its interactionId matches — a sibling bar never inherits it.
  const ownAnswerState =
    answerState !== undefined && answerState.interactionId === interactionId
      ? answerState
      : undefined;
  const inflight = ownAnswerState?.inflight === true;
  const answered = ownAnswerState?.answeredAt !== undefined;
  const disabled = inflight || answered;
  // A multiplexed sub-agent approval carries the adapter-bound label — badge WHO
  // is asking. Absent on the parent's singular slot; never fabricated.
  const agentBadge = pendingInteractionAgentLabel(interaction);
  const asker =
    agentBadge !== undefined ? `${agentBadge} (${session.label})` : session.label;
  return {
    representation,
    interactionId,
    ownAnswerState,
    inflight,
    answered,
    disabled,
    agentBadge,
    asker,
  };
}

function composerDraftText(
  providedText: string | undefined,
  providedRevision: number | undefined,
  composerRef: RefObject<SessionComposerHandle | null> | undefined,
  session: OpenSession,
) {
  const current = sessionCockpitStore.getState().perSession[session.id]?.composer;
  return {
    text:
      providedText ?? composerRef?.current?.getDraft() ?? current?.draft ?? "",
    draftRevision: providedRevision ?? current?.draftRevision,
  };
}

function useInteractionSubmit(
  session: OpenSession,
  interactionId: string | undefined,
  composerMode: boolean,
  composerRef?: RefObject<SessionComposerHandle | null>,
) {
  // Composer answer-mode marking (non-choice kinds): the composer below is visibly labeled as
  // the answer input while this bar is in composer mode.
  useEffect(() => {
    const composer = composerRef?.current?.getElement();
    if (!composer || !composerMode) return undefined;
    composer.setAttribute("data-answer-mode", "true");
    composer.setAttribute("aria-description", INTERACTION_COMPOSER_MODE);
    return () => {
      composer.removeAttribute("data-answer-mode");
      composer.removeAttribute("aria-description");
    };
  }, [composerMode, composerRef]);

  const submitAnswer = useCallback(
    (text: string, draftRevision?: number) => {
      if (!interactionId) return;
      void submitInteractionAnswer({
        session,
        interactionId,
        answer: text,
        draftRevision,
      });
    },
    [interactionId, session],
  );

  const submitAnswers = useCallback(
    (answers: Record<string, string>) => {
      if (!interactionId) return;
      void submitInteractionAnswer({ session, interactionId, answers });
    },
    [interactionId, session],
  );

  const answerFromComposer = useCallback(
    (providedText?: string, providedRevision?: number) => {
      const { text, draftRevision } = composerDraftText(
        providedText,
        providedRevision,
        composerRef,
        session,
      );
      if (!text) {
        if (interactionId) {
          sessionCockpitStore.getState().setInteractionAnswer(session.id, {
            interactionId,
            inflight: false,
            answer: "",
            draftRevision,
            error: "the answer text is empty — type it in the composer below, then send",
          });
        }
        return;
      }
      submitAnswer(text, draftRevision);
    },
    [composerRef, interactionId, session, submitAnswer],
  );
  return { submitAnswer, submitAnswers, answerFromComposer };
}

const SingleInteractionBar = forwardRef<
  InteractionBarHandle,
  {
    session: OpenSession;
    /** The ONE pending interaction payload this bar renders and answers. */
    interaction: Record<string, unknown>;
    /** Every interactionId currently pending on the session — a stored round-trip record is
     *  stale only when its id is no longer among them (never when a SIBLING bar's id differs). */
    activeInteractionIds: readonly string[];
    composerRef?: RefObject<SessionComposerHandle | null>;
  }
>(function SingleInteractionBar(
  { session, interaction, activeInteractionIds, composerRef },
  forwardedRef,
) {
  const state = useInteractionState(session, interaction, activeInteractionIds);
  const { submitAnswer, submitAnswers, answerFromComposer } = useInteractionSubmit(
    session,
    state.interactionId,
    state.representation?.mode === "composer",
    composerRef,
  );
  useImperativeHandle(
    forwardedRef,
    () => ({
      submitComposerAnswer: (text, revision) => answerFromComposer(text, revision),
    }),
    [answerFromComposer],
  );

  // Focus honesty: never steal focus on appearance; if the bar disappears while holding focus,
  // return it to the element focus came FROM (the invoker).
  const rootRef = useRef<HTMLDivElement>(null);
  const invokerRef = useRef<HTMLElement | null>(null);
  useEffect(() => {
    const root = rootRef.current;
    if (!root) return undefined;
    const remember = (event: FocusEvent) => {
      const from = event.relatedTarget;
      if (from instanceof HTMLElement && !root.contains(from)) invokerRef.current = from;
    };
    root.addEventListener("focusin", remember);
    return () => {
      root.removeEventListener("focusin", remember);
      // Cleanup runs after DOM removal: focus fell to <body> ⇒ the bar held it — hand it back.
      if (document.activeElement === document.body && invokerRef.current?.isConnected) {
        invokerRef.current.focus();
      }
    };
  }, []);

  if (!state.representation) return null;

  return (
    <div
      ref={rootRef}
      className={bar}
      role="group"
      aria-label={`pending question from ${state.asker}`}
      data-testid="interaction-bar"
    >
      {/* Assertive announce (design §7.3): the bar's appearance is poll-bounded — the live
          region covers the gap for AT users without moving focus. */}
      <InteractionAnnounce
        representation={state.representation}
        sessionLabel={session.label}
      />
      <InteractionHead
        representation={state.representation}
        agentBadge={state.agentBadge}
      />
      <InteractionBody
        representation={state.representation}
        disabled={state.disabled}
        onSubmit={submitAnswers}
        submitAnswer={submitAnswer}
        answerFromComposer={() => answerFromComposer()}
      />
      <InteractionStatusRow
        inflight={state.inflight}
        error={state.ownAnswerState?.error}
        answered={state.answered}
        onRetry={() => {
          if (state.interactionId) {
            void retryStoredInteractionAnswer(session, state.interactionId);
          }
        }}
      />
      <InteractionHint unrepresentable={state.representation.mode === "unrepresentable"} />
    </div>
  );
});
