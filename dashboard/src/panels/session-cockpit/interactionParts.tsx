import { useState } from "react";
import { Button } from "react-aria-components";

import type {
  InteractionQuestion,
  InteractionRepresentation,
  PendingInteractionView,
} from "../../data/interactionAnswer";
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
import {
  announce,
  answeredText,
  choiceButton,
  choicesRow,
  errorText,
  headRow,
  hint,
  kindChip,
  promptText,
  questionBlock,
  questionsGrid,
  statusRow,
} from "./interactionStyles";

function QuestionOption({
  option,
  disabled,
  pressed,
  testId,
  onPress,
}: {
  option: InteractionQuestion["options"][number];
  disabled: boolean;
  pressed: boolean;
  testId: string;
  onPress: () => void;
}) {
  return (
    <button
      type="button"
      className={choiceButton}
      disabled={disabled}
      aria-pressed={pressed}
      data-selected={pressed ? "true" : undefined}
      onClick={onPress}
      title={option.description}
      data-testid={testId}
    >
      {option.label}
    </button>
  );
}

function QuestionEntry({
  question,
  disabled,
  selected,
  recordedAnswer,
  answeredCount,
  totalCount,
  onRecord,
  onToggle,
}: {
  question: InteractionQuestion;
  disabled: boolean;
  selected: string[];
  recordedAnswer: string | undefined;
  answeredCount: number;
  totalCount: number;
  onRecord: (question: InteractionQuestion, answer: string) => void;
  onToggle: (question: InteractionQuestion, label: string) => void;
}) {
  const renderOption = (option: InteractionQuestion["options"][number]) =>
    question.multiSelect ? (
      <QuestionOption
        key={option.label}
        option={option}
        disabled={disabled}
        pressed={selected.includes(option.label)}
        testId="interaction-bar-question-toggle"
        onPress={() => onToggle(question, option.label)}
      />
    ) : (
      <QuestionOption
        key={option.label}
        option={option}
        disabled={disabled}
        pressed={recordedAnswer === option.label}
        testId="interaction-bar-question-option"
        onPress={() => onRecord(question, option.label)}
      />
    );

  return (
    <div className={questionBlock} key={question.text} data-testid="interaction-bar-question">
      <div className={headRow}>
        {question.header ? (
          <span className={kindChip} data-testid="interaction-bar-question-header">{question.header}</span>
        ) : null}
        <span className={promptText} data-testid="interaction-bar-question-text">{question.text}</span>
      </div>
      <div className={choicesRow} data-testid="interaction-bar-question-options">
        {question.options.map(renderOption)}
        {question.multiSelect ? (
          <button type="button" className={choiceButton} disabled={disabled || selected.length === 0} onClick={() => onRecord(question, selected.join(", "))} data-testid="interaction-bar-question-confirm">
            {INTERACTION_MULTISELECT_CONFIRM(selected.length)}
          </button>
        ) : null}
      </div>
      {question.multiSelect ? (
        <span className={hint} data-testid="interaction-bar-multiselect-hint">{INTERACTION_MULTISELECT_HINT}</span>
      ) : null}
      {recordedAnswer !== undefined && answeredCount < totalCount ? (
        <span className={hint} data-testid="interaction-bar-question-recorded">{INTERACTION_QUESTION_RECORDED(recordedAnswer)}</span>
      ) : null}
    </div>
  );
}

/**
 * Structured AskUserQuestion pages: each question renders its header, text,
 * and ITS OWN option group. Single-select options record on click (re-click changes the record
 * while siblings are still open); multiSelect options toggle and join into ONE answer per
 * question (", "-joined labels — the serialization claude's `_question_answers` accepts). The
 * interaction submits exactly once, when EVERY question holds an answer (the direct route's
 * all-or-nothing contract). Local state resets per interactionId via the parent's `key`.
 */
export function QuestionsBody({
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
    <div className={questionsGrid} data-testid="interaction-bar-questions">
      {view.questions.length > 1 ? (
        <span className={hint} data-testid="interaction-bar-progress">
          {INTERACTION_QUESTIONS_PROGRESS(answeredCount, view.questions.length)}
        </span>
      ) : null}
      {view.questions.map((question) => (
        <QuestionEntry
          key={question.text}
          question={question}
          disabled={disabled}
          selected={toggles[question.text] ?? []}
          recordedAnswer={recorded[question.text]}
          answeredCount={answeredCount}
          totalCount={view.questions.length}
          onRecord={record}
          onToggle={toggle}
        />
      ))}
    </div>
  );
}

export function InteractionAnnounce({
  representation,
  sessionLabel,
}: {
  representation: InteractionRepresentation;
  sessionLabel: string;
}) {
  return (
    <div
      role="alert"
      data-testid="interaction-bar-announce"
      className={announce}
    >
      {representation.mode === "unrepresentable"
        ? `pending interaction from ${sessionLabel}`
        : `pending question from ${sessionLabel}: ${
            representation.view.prompt ||
            representation.view.questions[0]?.text ||
            "(no prompt text)"
          }`}
    </div>
  );
}

export function InteractionHead({
  representation,
  agentBadge,
}: {
  representation: InteractionRepresentation;
  agentBadge: string | undefined;
}) {
  if (representation.mode === "unrepresentable") {
    return (
      <div className={headRow}>
        <span className={promptText} data-testid="interaction-bar-unrepresentable">
          {representation.reason}
        </span>
      </div>
    );
  }
  return (
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
  );
}

export function InteractionBody({
  representation,
  disabled,
  onSubmit,
  submitAnswer,
  answerFromComposer,
}: {
  representation: InteractionRepresentation;
  disabled: boolean;
  onSubmit: (answers: Record<string, string>) => void;
  submitAnswer: (answer: string) => void;
  answerFromComposer: () => void;
}) {
  if (representation.mode === "unrepresentable") return null;
  if (representation.mode === "questions") {
    return (
      <QuestionsBody
        key={representation.view.interactionId}
        view={representation.view}
        disabled={disabled}
        onSubmit={onSubmit}
      />
    );
  }
  if (representation.mode === "choices" || representation.mode === "permission") {
    return (
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
    );
  }
  return (
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
  );
}

export function InteractionStatusRow({
  inflight,
  error,
  answered,
  onRetry,
}: {
  inflight: boolean;
  error: string | undefined;
  answered: boolean;
  onRetry: () => void;
}) {
  if (!inflight && !error && !answered) return null;
  return (
    <div className={statusRow}>
      {inflight ? (
        <span data-testid="interaction-bar-inflight">{INTERACTION_ANSWERING}</span>
      ) : error ? (
        <>
          <span className={errorText} role="alert" data-testid="interaction-bar-error">
            {error}
          </span>
          <Button
            className={choiceButton}
            onPress={onRetry}
            data-testid="interaction-bar-retry"
          >
            retry
          </Button>
        </>
      ) : (
        <span
          className={answeredText}
          role="status"
          data-testid="interaction-bar-answered"
        >
          {INTERACTION_ANSWERED}
        </span>
      )}
    </div>
  );
}

export function InteractionHint({
  unrepresentable,
}: {
  unrepresentable: boolean;
}) {
  if (unrepresentable) return null;
  return (
    <span className={hint} data-testid="interaction-bar-hint">
      {INTERACTION_HONESTY_HINT}
    </span>
  );
}
