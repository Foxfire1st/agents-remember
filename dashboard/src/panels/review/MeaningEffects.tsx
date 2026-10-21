// The recorded effect of a meaning change (MIK-R43): the tree badge, the reading
// area's block, and the nine label explanations. Every value rendered here is the
// server's fact; the component derives no label and states no net effect.
import { useState } from 'react';
import { css } from '../../../styled-system/css';
import type {
  RecordMeaningEffects,
  ReviewMeaningEffects,
  ReviewOutlineMeaning,
  ReviewPayload,
} from '../../data/review';
import { muted as quiet, sublabel } from './reading/readingStyles';

const muted = css({ color: 'muted', fontSize: '0.75rem', margin: '0.2rem 0' });
const prose = css({ overflowWrap: 'anywhere' });
const badge = css({
  display: 'inline-block',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderRadius: '2px',
  borderColor: 'cyan',
  color: 'cyan',
  padding: '0 0.35rem',
  fontSize: '0.68rem',
  letterSpacing: '0.04em',
  lineHeight: '1.5',
  whiteSpace: 'nowrap',
  // The tree row is one flex line with an ellipsizing title: an extreme badge
  // (two unrecorded old labels) shrinks with an ellipsis instead of pushing past
  // the rail. The full sequence stays in the title attribute and the row's
  // accessible name.
  flexShrink: 1,
  minWidth: 0,
  overflow: 'hidden',
  textOverflow: 'ellipsis',
});
const list = css({ margin: '0.3rem 0', paddingLeft: '1.1rem', fontSize: '0.78rem' });
// The block in the reading column (MIK-R89 v2 rule 1, 9498d6e4: the page compared with the mockup by
// eye): its heading in the column's section-label look (`sublabel`, the mockup's `.label`), unbolded;
// its steps one quiet line each, as the mockup's
// `.smeta`, with no list marker: a step's line says its attempt, and an ordinal marker of a list was
// cut to ".." at the column's edge. The explanations' toggle is quiet text, not a boxed control.
const labelWeight = css({ fontWeight: 'normal' });
const steps = css({ margin: '0.3rem 0', display: 'grid', gap: '0.5rem' });
const stepLine = css({ overflowWrap: 'anywhere' });
const toggleLook = css({
  background: 'transparent',
  border: 'none',
  padding: 0,
  color: 'muted',
  fontFamily: 'inherit',
  fontSize: '0.72rem',
  cursor: 'pointer',
  textDecorationLine: 'underline',
  textDecorationStyle: 'dotted',
  textUnderlineOffset: '3px',
  _hover: { color: 'amber' },
});

// Rule 6's fixed sentences, in the vocabulary's own order. They explain the label; they
// do not say whether the curator's judgment is right. The backend pins the same nine
// against ADMITTED_EFFECT_LABELS, and a case fails when the two sides differ.
export const EFFECT_EXPLANATIONS: { label: string; sentence: string }[] = [
  { label: 'restore', sentence: 'brings back an obligation that had been dropped or broken.' },
  { label: 'clarify', sentence: 'rewords the obligation; what it requires stays the same.' },
  { label: 'introduce', sentence: 'states an obligation that was not recorded before.' },
  { label: 'strengthen', sentence: 'adds to the obligation or tightens it.' },
  { label: 'weaken', sentence: 'relaxes the obligation or removes part of it.' },
  { label: 'replace', sentence: 'puts a different obligation in its place.' },
  { label: 'split', sentence: 'divides one obligation into several records.' },
  { label: 'merge', sentence: 'joins several records into one obligation.' },
  { label: 'retire', sentence: 'ends the obligation.' },
];

export const OLD_ROW_SENTENCE =
  'This row was written before the effect label became required.';
export const MULTI_STEP_LINE = "These are the leaf's successive recorded judgments.";
export const NONE_TEXT = 'No meaning effect recorded by this leaf';

// The tree badge: the labels in attempt order joined by an arrow; more than three
// steps show the first, an ellipsis and the last.
export function meaningBadgeText(labels: readonly string[]): string {
  if (labels.length <= 3) return labels.join(' → ');
  return `${labels[0]} → … → ${labels[labels.length - 1]}`;
}

export function MeaningBadge({ meaning }: { meaning: ReviewOutlineMeaning }) {
  if (meaning.state !== 'steps') return null;
  const text = meaningBadgeText(meaning.labels);
  return (
    <span className={badge} data-testid="review-meaning-badge" title={text}>
      {text}
    </span>
  );
}

export function meaningFor(
  effects: ReviewMeaningEffects | undefined,
  record: string,
): RecordMeaningEffects | undefined {
  return effects?.records.find((entry) => entry.record === record);
}

function StepLine({ step }: { step: RecordMeaningEffects['steps'][number] }) {
  const effect = step.effect ?? 'effect not recorded';
  const revision =
    step.revision === undefined ? 'family revision not recorded' : `revision ${step.revision}`;
  return (
    <div role="listitem" className={stepLine} data-testid="review-recorded-step" data-row={step.row_id}>
      <p className={quiet} data-testid="review-recorded-step-line">
        attempt {step.attempt} · {revision} · {effect} · {step.row_id}
      </p>
      <p className={muted}>{step.reason}</p>
      <p className={muted}>
        {step.closed ? 'history file closed' : 'history file still open'}
        {step.effect === undefined ? ` · ${OLD_ROW_SENTENCE}` : ''}
      </p>
      <details data-disclosure="identifiers">
        <summary>Row record</summary>
        <p className={prose}>
          row {step.row_id} · {step.history_path}
        </p>
      </details>
    </div>
  );
}

function Explanations() {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button
        type="button"
        className={toggleLook}
        data-own-look
        data-testid="review-meaning-explanations-toggle"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        What the effect labels mean
      </button>
      {open ? (
        <ul className={list} data-testid="review-meaning-explanations">
          {EFFECT_EXPLANATIONS.map(({ label, sentence }) => (
            <li key={label}>
              {label}: {sentence}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

// The reading area's block beside the intent diff of the selected invariant and beside
// the guarantee diff of the selected family. Never inside a disclosure; a step's row
// id and history path may be (they are, above). A missing field is a failure, not none.
export function RecordedStepsBlock({ answer }: { answer: RecordMeaningEffects }) {
  return (
    <section data-testid="review-recorded-steps">
      <h3 className={`${sublabel} ${labelWeight}`}>Recorded steps</h3>
      {answer.state === 'steps' ? (
        <>
          {answer.steps.length > 1 ? <p className={muted}>{MULTI_STEP_LINE}</p> : null}
          <div role="list" className={steps}>
            {answer.steps.map((step) => (
              <StepLine key={`${step.attempt}:${step.row_id}`} step={step} />
            ))}
          </div>
        </>
      ) : (
        <p className={muted} data-testid="review-recorded-steps-state">
          {answer.state === 'none' ? NONE_TEXT : answer.detail}
        </p>
      )}
      <Explanations />
    </section>
  );
}

export function MissingMeaningEffects() {
  return (
    <section data-testid="review-recorded-steps-missing">
      <h3 className={`${sublabel} ${labelWeight}`}>Recorded steps</h3>
      <p className={muted}>
        The review answer carries no recorded meaning effects: a current build without
        the field is an invalid answer, shown as a failure rather than as no recorded
        effect.
      </p>
    </section>
  );
}

// The required-field guard for the branches no MeaningBlock covers (MIK-R43 rule 9):
// the no-subject, source-only and member-without-identity centers. A current review
// answer without meaning_effects is invalid on those branches exactly as on the
// selected subject. A field that is present but empty stays valid.
export function MeaningFieldGuard({ payload }: { payload: ReviewPayload }) {
  if (payload.meaning_effects !== undefined) return null;
  return <MissingMeaningEffects />;
}

// The reading area's join: the block for one record beside its diff. A missing field
// is a failure; a record the answer does not cover renders nothing.
export function MeaningBlock({
  payload,
  record,
}: {
  payload: ReviewPayload;
  record: string;
}) {
  const effects = payload.meaning_effects;
  if (effects === undefined) return <MissingMeaningEffects />;
  const answer = meaningFor(effects, record);
  if (answer === undefined) return null;
  return <RecordedStepsBlock answer={answer} />;
}
