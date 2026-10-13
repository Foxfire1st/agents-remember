// MIK-R127 rule 5: the blocks only a task has. Steps as a table — mark, number, title and
// the state at the right, with the progress in the heading — and the review round with its
// open findings.
import { Markdown } from '../../grammar/Markdown';
import type { TaskReviewStateNode, TaskStepNode } from '../../types/projection';
import {
  firstOpenStepId,
  stepDisplayState,
  stepMarkStates,
  substepDisplayState,
  type StepDisplayState,
} from './model';
import {
  Block,
  Clip,
  MoreLink,
  Pip,
  Row,
  Rows,
  StepMarks,
  SubHead,
  plural,
  type DocView,
} from './pageParts';
import {
  apart,
  dim,
  findingText,
  lineRow,
  muted,
  none,
  rowId,
  stepId,
  stepMark,
  stepState,
  stepTitle,
  stepUnder,
  substep,
  substepList,
  warn,
} from './pageStyles';

export const REVIEW_INLINE_BOUND = 8;

export function SkippedDisposition({ reason }: { reason: string }) {
  return (
    <span data-testid="skipped-disposition">
      <Clip text={`Skipped: ${reason}`} />
    </span>
  );
}

function stepWord(step: TaskStepNode, state: StepDisplayState): string {
  return step.disposition ? 'skipped' : state;
}

function StepSubList({ step }: { step: TaskStepNode }) {
  if (step.substeps.length === 0) return null;
  return (
    <ul className={substepList}>
      {step.substeps.map((item) => (
        <li
          key={item.id}
          className={substep({ hot: item.status === 'inProgress' })}
          data-testid={`substep-${step.id}-${item.id}`}
          data-state={item.status}
        >
          <Pip state={substepDisplayState(item.status)} />
          <span className={dim}>{item.id}</span>
          <span>
            <Markdown inline>{item.title}</Markdown>
          </span>
          {item.disposition ? (
            <span className={dim}>
              skipped: <Markdown inline>{item.disposition.reason}</Markdown>
            </span>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

// One step: its mark, its number, its title and its state. The note or the
// outcome is one muted line under the title and opens in place; substeps and a
// skip reason stand there too.
function StepRow({ step, firstOpen }: { step: TaskStepNode; firstOpen: string | undefined }) {
  const state = stepDisplayState(step, firstOpen);
  const note = step.note || step.outcome;
  const hot = !step.disposition && (state === 'next' || state === 'current');
  return (
    <Row testid={`step-${step.id}`} state={state}>
      <Pip state={state} className={stepMark} />
      <span className={stepId}>{step.id}</span>
      <span className={stepTitle({ done: state === 'done' })} title={step.title}>
        <Markdown inline>{step.title}</Markdown>
      </span>
      <span
        className={stepState({ hot })}
        data-testid={state === 'next' ? `step-next-${step.id}` : `step-state-${step.id}`}
      >
        {stepWord(step, state)}
      </span>
      {note ? (
        <div className={stepUnder}>
          <Clip
            text={note}
            testid={`step-note-${step.id}`}
            preview={step.note ? step.noteTruncated : step.outcomeTruncated}
          />
        </div>
      ) : null}
      {step.disposition ? (
        <div className={stepUnder}>
          <SkippedDisposition reason={step.disposition.reason} />
        </div>
      ) : null}
      {step.substeps.length > 0 ? (
        <div className={stepUnder}>
          <StepSubList step={step} />
        </div>
      ) : null}
    </Row>
  );
}

// The steps of a task (rule 5): one line per step with its state; the first
// step that is not done is marked.
export function StepsBlock({
  steps,
  done,
  total,
}: {
  steps: TaskStepNode[];
  done: number;
  total: number;
}) {
  if (steps.length === 0) return null;
  const firstOpen = firstOpenStepId(steps);
  return (
    <Block
      title="Steps"
      block="steps"
      count={
        <>
          <StepMarks states={stepMarkStates(steps)} />
          <span role="img" aria-label="steps done" data-testid="steps-progress">
            {done} of {total} done
          </span>
        </>
      }
    >
      <Rows cols="steps" testid="steps-list">
        {steps.map((step) => (
          <StepRow key={step.id} step={step} firstOpen={firstOpen} />
        ))}
      </Rows>
    </Block>
  );
}

function roundLine(review: TaskReviewStateNode): string {
  const granted =
    review.additionalRounds > 0
      ? ` · ${plural(review.additionalRounds, 'additional round')} granted`
      : '';
  const pending = review.pending ? ' · the verdict of this round is open' : '';
  return `Round ${review.round}${granted}${pending}`;
}

// The review of a task (rule 5): the round and the open findings, where the
// document records a review. The whole round is one counted link away.
export function ReviewBlock({
  review,
  openView,
}: {
  review: TaskReviewStateNode | undefined;
  openView: (view: DocView) => void;
}) {
  if (!review || review.round === 0) return null;
  // The count is the whole round's. The rows are the open findings this document
  // carries: a summary holds the first findings only, the rest is one link away.
  const openCount = review.openFindings;
  const shownOpen = review.findings.filter((finding) => finding.open).slice(0, REVIEW_INLINE_BOUND);
  return (
    <Block title="Review" block="review" count={`round ${review.round}`}>
      <div className={lineRow} data-testid="review-line">
        <span>{roundLine(review)}</span>
        {review.totalFindings > 0 ? (
          <span className={openCount > 0 ? warn : muted} data-testid="review-open-count">
            {openCount} of {review.totalFindings} findings open
          </span>
        ) : (
          <span className={muted}>no finding recorded</span>
        )}
      </div>
      {shownOpen.length > 0 ? (
        <>
          <SubHead title="Open findings" count={openCount} />
          <Rows cols="finding" testid="review-open-findings">
            {shownOpen.map((finding) => (
              <Row key={finding.findingId}>
                <span className={rowId({ tone: 'plain' })}>{finding.findingId}</span>
                <Clip
                  text={finding.description}
                  tone="ink"
                  preview={finding.descriptionTruncated}
                />
              </Row>
            ))}
          </Rows>
        </>
      ) : null}
      {review.totalFindings > 0 ? (
        <MoreLink onClick={() => openView({ kind: 'findings' })} testid="findings-all-link">
          all {plural(review.totalFindings, 'finding')}
        </MoreLink>
      ) : null}
      {review.developerApproval ? (
        <div className={apart}>
          <Clip label="granted by" text={review.developerApproval} testid="review-approval" />
        </div>
      ) : null}
    </Block>
  );
}

export function FindingsViewBody({ review }: { review: TaskReviewStateNode }) {
  if (review.findings.length === 0) return <p className={none}>No finding recorded.</p>;
  return (
    <Rows cols="findingAll">
      {review.findings.map((finding) => (
        <Row key={finding.findingId} testid={`finding-${finding.findingId}`}>
          <span className={rowId({ tone: 'plain' })}>{finding.findingId}</span>
          <span className={finding.open ? warn : dim}>{finding.open ? 'open' : 'closed'}</span>
          <span className={findingText}>
            <Clip text={finding.description} tone="ink" preview={finding.descriptionTruncated} />
          </span>
        </Row>
      ))}
    </Rows>
  );
}
