import {
  useCallback,
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type RefObject,
} from "react";
import { Button } from "react-aria-components";

import { css } from "../../../styled-system/css";
import {
  pendingInteractionAgentLabel,
  pendingInteractionPayloads,
  representPendingInteraction,
  retryStoredInteractionAnswer,
  submitInteractionAnswer,
  type InteractionQuestion,
  type PendingInteractionView,
} from "../../data/interactionAnswer";
import { sessionCockpitStore, useSessionCockpit } from "../../data/sessionCockpitStore";
import type { OpenSession } from "../../data/sessions";
import type { SessionComposerHandle } from "../SessionComposer";
import {
  INTERACTION_ANSWERED,
  INTERACTION_ANSWERING,
  INTERACTION_COMPOSER_MODE,
  INTERACTION_HONESTY_HINT,
  INTERACTION_MULTISELECT_CONFIRM,
  INTERACTION_MULTISELECT_HINT,
  INTERACTION_NO_PROMPT_TEXT,
  INTERACTION_QUESTION_RECORDED,
  INTERACTION_QUESTIONS_PROGRESS,
} from "./lifecycleCopy";

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
// focus returns to the element that had it before.

const bar = css({
  display: "grid",
  gap: "0.35rem",
  flexShrink: 0,
  padding: "0.4rem 0.55rem",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "amber",
  borderRadius: "3px",
  background: "oklch(0.82 0.16 75 / 0.07)",
  fontSize: "0.74rem",
});
const headRow = css({ display: "flex", alignItems: "baseline", gap: "0.45rem", minWidth: "0" });
const kindChip = css({
  flex: "none",
  fontSize: "0.62rem",
  letterSpacing: "0.06em",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "color-mix(in oklch, token(colors.amber) 45%, transparent)",
  borderRadius: "2px",
  paddingInline: "0.3rem",
  color: "amber",
});
const promptText = css({ color: "ink", minWidth: "0", overflowWrap: "anywhere" });
const choicesRow = css({ display: "flex", gap: "0.35rem", flexWrap: "wrap" });
const choiceButton = css({
  font: "inherit",
  fontSize: "0.72rem",
  paddingInline: "0.55rem",
  paddingBlock: "0.16rem",
  borderRadius: "2px",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "amber",
  color: "amber",
  background: "transparent",
  cursor: "pointer",
  _hover: { background: "oklch(0.82 0.16 75 / 0.12)" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
  _disabled: { opacity: 0.55, cursor: "default" },
  // A recorded single-select answer / toggled multiSelect option — visibly held, still changeable
  // until the all-or-nothing submit fires.
  "&[data-selected='true']": { background: "oklch(0.82 0.16 75 / 0.2)" },
});
const questionBlock = css({
  display: "grid",
  gap: "0.25rem",
  minWidth: "0",
  paddingBlock: "0.15rem",
});
const hint = css({ color: "muted", fontSize: "0.66rem" });
const statusRow = css({ display: "flex", alignItems: "baseline", gap: "0.45rem", minWidth: "0" });
const errorText = css({ color: "alarm", overflowWrap: "anywhere", minWidth: "0" });
const answeredText = css({ color: "mint" });

export interface InteractionBarHandle {
  submitComposerAnswer(text: string, revision: number): void;
}

/**
 * Structured AskUserQuestion pages: each question renders its header, text,
 * and ITS OWN option group. Single-select options record on click (re-click changes the record
 * while siblings are still open); multiSelect options toggle and join into ONE answer per
 * question (", "-joined labels — the serialization claude's `_question_answers` accepts). The
 * interaction submits exactly once, when EVERY question holds an answer (the direct route's
 * all-or-nothing contract). Local state resets per interactionId via the parent's `key`.
 */
function QuestionsBody({
  view,
  disabled,
  onSubmit,
}: {
  view: PendingInteractionView;
  disabled: boolean;
  onSubmit: (answers: Record<string, string>) => void;
}) {
  const [recorded, setRecorded] = useState<Record<string, string>>({});
  const [toggles, setToggles] = useState<Record<string, string[]>>({});
  const answeredCount = view.questions.filter(
    (question) => recorded[question.text] !== undefined,
  ).length;

  const record = (question: InteractionQuestion, answer: string) => {
    const next = { ...recorded, [question.text]: answer };
    setRecorded(next);
    if (view.questions.every((candidate) => next[candidate.text] !== undefined)) {
      onSubmit(next);
    }
  };

  const toggle = (question: InteractionQuestion, label: string) => {
    setToggles((current) => {
      const selected = current[question.text] ?? [];
      return {
        ...current,
        [question.text]: selected.includes(label)
          ? selected.filter((candidate) => candidate !== label)
          : [...selected, label],
      };
    });
  };

  return (
    <div className={css({ display: "grid", gap: "0.3rem", minWidth: "0" })} data-testid="interaction-bar-questions">
      {view.questions.length > 1 ? (
        <span className={hint} data-testid="interaction-bar-progress">
          {INTERACTION_QUESTIONS_PROGRESS(answeredCount, view.questions.length)}
        </span>
      ) : null}
      {view.questions.map((question) => {
        const selected = toggles[question.text] ?? [];
        const recordedAnswer = recorded[question.text];
        return (
          <div className={questionBlock} key={question.text} data-testid="interaction-bar-question">
            <div className={headRow}>
              {question.header ? (
                <span className={kindChip} data-testid="interaction-bar-question-header">
                  {question.header}
                </span>
              ) : null}
              <span className={promptText} data-testid="interaction-bar-question-text">
                {question.text}
              </span>
            </div>
            <div className={choicesRow} data-testid="interaction-bar-question-options">
              {question.options.map((option) =>
                question.multiSelect ? (
                  <button
                    type="button"
                    key={option.label}
                    className={choiceButton}
                    disabled={disabled}
                    aria-pressed={selected.includes(option.label)}
                    data-selected={selected.includes(option.label) ? "true" : undefined}
                    onClick={() => toggle(question, option.label)}
                    title={option.description}
                    data-testid="interaction-bar-question-toggle"
                  >
                    {option.label}
                  </button>
                ) : (
                  <button
                    type="button"
                    key={option.label}
                    className={choiceButton}
                    disabled={disabled}
                    aria-pressed={recordedAnswer === option.label}
                    data-selected={recordedAnswer === option.label ? "true" : undefined}
                    onClick={() => record(question, option.label)}
                    title={option.description}
                    data-testid="interaction-bar-question-option"
                  >
                    {option.label}
                  </button>
                ),
              )}
              {question.multiSelect ? (
                <button
                  type="button"
                  className={choiceButton}
                  disabled={disabled || selected.length === 0}
                  onClick={() => record(question, selected.join(", "))}
                  data-testid="interaction-bar-question-confirm"
                >
                  {INTERACTION_MULTISELECT_CONFIRM(selected.length)}
                </button>
              ) : null}
            </div>
            {question.multiSelect ? (
              <span className={hint} data-testid="interaction-bar-multiselect-hint">
                {INTERACTION_MULTISELECT_HINT}
              </span>
            ) : null}
            {recordedAnswer !== undefined && answeredCount < view.questions.length ? (
              <span className={hint} data-testid="interaction-bar-question-recorded">
                {INTERACTION_QUESTION_RECORDED(recordedAnswer)}
              </span>
            ) : null}
          </div>
        );
      })}
    </div>
  );
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
  const answerState = useSessionCockpit(
    (state) => state.perSession[session.id]?.interactionAnswer,
  );
  const representation = representPendingInteraction(interaction);
  const interactionId = representation && representation.mode !== "unrepresentable"
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

  // Composer answer-mode marking (non-choice kinds): the composer below is visibly labeled as
  // the answer input while this bar is in composer mode.
  const composerMode = representation?.mode === "composer";
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

  const submitAnswer = useCallback((text: string, draftRevision?: number) => {
    if (!interactionId) return;
    void submitInteractionAnswer({
      session,
      interactionId,
      answer: text,
      draftRevision,
    });
  }, [interactionId, session]);

  // Structured questions submit the ALL-OR-NOTHING answers map through the direct route.
  const submitAnswers = useCallback((answers: Record<string, string>) => {
    if (!interactionId) return;
    void submitInteractionAnswer({ session, interactionId, answers });
  }, [interactionId, session]);

  const answerFromComposer = useCallback((providedText?: string, providedRevision?: number) => {
    const current = sessionCockpitStore.getState().perSession[session.id]?.composer;
    const text = providedText ?? composerRef?.current?.getDraft() ?? current?.draft ?? "";
    if (!text) {
      if (interactionId) {
        sessionCockpitStore.getState().setInteractionAnswer(session.id, {
          interactionId,
          inflight: false,
          answer: "",
          draftRevision: providedRevision ?? current?.draftRevision,
          error: "the answer text is empty — type it in the composer below, then send",
        });
      }
      return;
    }
    submitAnswer(text, providedRevision ?? current?.draftRevision);
  }, [composerRef, interactionId, session.id, submitAnswer]);

  useImperativeHandle(
    forwardedRef,
    () => ({
      submitComposerAnswer: (text, revision) => answerFromComposer(text, revision),
    }),
    [answerFromComposer],
  );

  if (!representation) return null;

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
  const asker = agentBadge !== undefined ? `${agentBadge} (${session.label})` : session.label;

  return (
    <div
      ref={rootRef}
      className={bar}
      role="group"
      aria-label={`pending question from ${asker}`}
      data-testid="interaction-bar"
    >
      {/* Assertive announce (design §7.3): the bar's appearance is poll-bounded — the live
          region covers the gap for AT users without moving focus. */}
      <div role="alert" data-testid="interaction-bar-announce" className={css({ position: "absolute", width: "1px", height: "1px", overflow: "hidden", clipPath: "inset(50%)" })}>
        {representation.mode === "unrepresentable"
          ? `pending interaction from ${session.label}`
          : `pending question from ${session.label}: ${
              representation.view.prompt ||
              representation.view.questions[0]?.text ||
              "(no prompt text)"
            }`}
      </div>
      {representation.mode === "unrepresentable" ? (
        <div className={headRow}>
          <span className={promptText} data-testid="interaction-bar-unrepresentable">
            {representation.reason}
          </span>
        </div>
      ) : (
        <>
          <div className={headRow}>
            <span className={kindChip} data-testid="interaction-bar-kind">
              {representation.view.kind}
            </span>
            {agentBadge !== undefined ? (
              <span className={kindChip} data-testid="interaction-bar-agent">
                {agentBadge}
              </span>
            ) : null}
            {/* Structured questions render their OWN per-question text below — the legacy flat
                `prompt` concatenation must never double as the question text. */}
            {representation.mode !== "questions" ? (
              <span className={promptText} data-testid="interaction-bar-prompt">
                {representation.view.prompt || INTERACTION_NO_PROMPT_TEXT}
              </span>
            ) : null}
          </div>
          {representation.mode === "questions" ? (
            <QuestionsBody
              key={representation.view.interactionId}
              view={representation.view}
              disabled={disabled}
              onSubmit={submitAnswers}
            />
          ) : representation.mode === "choices" || representation.mode === "permission" ? (
            <div className={choicesRow} data-testid="interaction-bar-choices">
              {representation.view.choices.map((choice) => (
                <Button
                  key={choice}
                  className={choiceButton}
                  isDisabled={disabled}
                  onPress={() => submitAnswer(choice)}
                  data-testid="interaction-bar-choice"
                >
                  {choice}
                </Button>
              ))}
            </div>
          ) : (
            <div className={choicesRow}>
              <span className={hint} data-testid="interaction-bar-composer-mode">
                {INTERACTION_COMPOSER_MODE}
              </span>
              <Button
                className={choiceButton}
                isDisabled={disabled}
                onPress={() => answerFromComposer()}
                data-testid="interaction-bar-composer-send"
              >
                send composer text as the answer
              </Button>
            </div>
          )}
        </>
      )}
      {inflight || ownAnswerState?.error || answered ? (
        <div className={statusRow}>
          {inflight ? (
            <span data-testid="interaction-bar-inflight">{INTERACTION_ANSWERING}</span>
          ) : ownAnswerState?.error ? (
            <>
              <span className={errorText} role="alert" data-testid="interaction-bar-error">
                {ownAnswerState.error}
              </span>
              <Button
                className={choiceButton}
                onPress={() => {
                  if (interactionId) void retryStoredInteractionAnswer(session, interactionId);
                }}
                data-testid="interaction-bar-retry"
              >
                retry
              </Button>
            </>
          ) : (
            <span className={answeredText} role="status" data-testid="interaction-bar-answered">
              {INTERACTION_ANSWERED}
            </span>
          )}
        </div>
      ) : null}
      {representation.mode !== "unrepresentable" ? (
        <span className={hint} data-testid="interaction-bar-hint">
          {INTERACTION_HONESTY_HINT}
        </span>
      ) : null}
    </div>
  );
});
