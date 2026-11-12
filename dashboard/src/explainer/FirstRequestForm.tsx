// The first request's form (MIK-R106 v2 rule 3): the locked role capsule, his references with
// their notes, his words, the fixed target and his Send. Nothing here sends until he presses it;
// the form only says what will go out.
import type { ExplainerBindingView, ExplainerStalePart } from './client';
import {
  CAPSULE_LINE,
  bindingWords,
  canSend,
  firstRequestLine,
  fixedTarget,
  type FormReference,
} from './model';
import { body, card, failureLine, fixedField, line, primaryButton, quietButton, ref, refText, refs, removeButton, row, spacer, statusWord, wish } from './columnStyles';

/** One reference in the form, with his note and the × that removes it. */
function RequestReference({
  reference,
  onRemove,
  onNote,
}: {
  reference: FormReference;
  onRemove: (id: string) => void;
  onNote: (id: string, note: string) => void;
}) {
  return (
    <li className={ref} data-testid="explainer-reference">
      <span aria-hidden>⊕</span>
      <span className={refText}>
        <span>{reference.title}</span>
        <span className={statusWord}>{reference.detail}</span>
        <input
          className={wish}
          aria-label={`Your note on ${reference.title}`}
          placeholder="+ note"
          value={reference.note}
          onChange={(event) => onNote(reference.id, event.target.value)}
        />
      </span>
      <button
        type="button"
        className={removeButton}
        aria-label={`Remove ${reference.title} from this request`}
        data-testid="explainer-reference-remove"
        onClick={() => onRemove(reference.id)}
      >
        ×
      </button>
    </li>
  );
}

/** Every reference this request will carry, each with its note. */
function RequestReferences({
  references,
  onRemove,
  onNote,
}: {
  references: FormReference[];
  onRemove: (id: string) => void;
  onNote: (id: string, note: string) => void;
}) {
  return (
    <ul className={refs} aria-label="References in this request" data-testid="explainer-references">
      {references.map((reference) => (
        <RequestReference key={reference.id} reference={reference} onRemove={onRemove} onNote={onNote} />
      ))}
    </ul>
  );
}

/** What a form with no reference says: a ⊕ on a block or an invariant adds the first one. */
function NoReferencesYet() {
  return (
    <p className={line} data-testid="explainer-references-empty">
      No references yet. Press ⊕ on a block or an invariant; more ⊕ clicks add references to this form.
    </p>
  );
}

/** The target, his Cancel, and his Send. Send is refused until there is something to send. */
function FormActions({
  task,
  sendable,
  sending,
  onCancel,
  onSend,
}: {
  task: string;
  sendable: boolean;
  sending: boolean;
  onCancel: () => void;
  onSend: () => void;
}) {
  return (
    <div className={row}>
      <span className={line} data-testid="explainer-fixed-target">
        to: {fixedTarget(task)}
      </span>
      <span className={spacer} />
      <button type="button" className={quietButton} data-testid="explainer-cancel" onClick={onCancel}>
        Cancel
      </button>
      <button type="button" className={primaryButton} disabled={!sendable} data-testid="explainer-send" onClick={onSend}>
        {sending ? 'Sending…' : 'Send'}
      </button>
    </div>
  );
}

/** Why his Send could not go out: his request stays in the form for another Send. */
function SendFailure({ failure }: { failure: { step: string; reason: string } | null }) {
  if (!failure) return null;
  return (
    <p className={failureLine} role="alert" data-testid="explainer-send-failure" data-step={failure.step}>
      {failure.step} failed: {failure.reason}. Your request is kept; press Send again when it can run.
    </p>
  );
}

export interface FirstRequestFormProps {
  task: string;
  references: FormReference[];
  message: string;
  binding: ExplainerBindingView | null;
  prefill: ExplainerStalePart[];
  staleGrades?: string[];
  failure: { step: string; reason: string } | null;
  sending: boolean;
  onMessage: (text: string) => void;
  onRemove: (id: string) => void;
  onNote: (id: string, note: string) => void;
  onCancel: () => void;
  onSend: () => void;
}

export function FirstRequestForm({
  task,
  references,
  message,
  binding,
  prefill,
  staleGrades = [],
  failure,
  sending,
  onMessage,
  onRemove,
  onNote,
  onCancel,
  onSend,
}: FirstRequestFormProps) {
  return (
    <div className={body} data-testid="explainer-first-request">
      <div className={card}>
        <div className={fixedField} data-testid="explainer-capsule">
          <span aria-hidden>▤</span>
          <span>
            Role capsule · <b>{CAPSULE_LINE}</b>
          </span>
          <span aria-hidden>🔒</span>
        </div>
        {prefill.length > 0 || staleGrades.length > 0 ? (
          <p className={line} data-testid="explainer-stale-prefill">
            Pre-filled with the stale parts: {prefill.map((part) => part.title).join(', ')}
            {staleGrades.length > 0 ? ` (${staleGrades.length} stale grades)` : ''}.
          </p>
        ) : null}
        <RequestReferences references={references} onRemove={onRemove} onNote={onNote} />
        {references.length === 0 ? <NoReferencesYet /> : null}
        <textarea
          className={wish}
          rows={4}
          aria-label="What do you want explained?"
          placeholder="What do you want explained? Leave it empty for the end-to-end review."
          value={message}
          onChange={(event) => onMessage(event.target.value)}
        />
        <FormActions
          task={task}
          sendable={!sending && canSend(references, message, prefill, staleGrades)}
          sending={sending}
          onCancel={onCancel}
          onSend={onSend}
        />
        <SendFailure failure={failure} />
        <p className={line} data-testid="explainer-form-line">
          {firstRequestLine(references, message)}
        </p>
        <p className={line}>
          Nothing is sent until you press Send. Sending builds the explainer’s workspace for{' '}
          <b data-testid="explainer-binding">{bindingWords(binding)}</b>, then delivers this as its first turn.
        </p>
      </div>
    </div>
  );
}
