// The review surface's non-payload outcomes, in one place: the loading state, the known-empty note
// and the refusal/failure block every read that is not a review renders through (ICR-R16).
//
// What a reader is owed distinctly, and where it comes from:
//
//   loading            the read is in flight; nothing is claimed about the answer yet.
//   known-empty        the read *answered* and there is nothing to show: `knownEmpty` is the
//                      measured shape of that (a measured inventory of zero paths, no knowledge
//                      comparison, and no recorded evidence or assessment). It is never rendered as
//                      a failure, and a failure is never rendered as this.
//   not-initialized    `candidate_dataset_absent`: the named dataset half does not exist.
//   unavailable-history the leaf's live candidate, its enclosure contract, or the review adapter
//                      itself cannot be resolved here (`candidate_not_live`, `candidate_unresolved`,
//                      `review_adapter_unavailable`, the route's own unwired answer).
//   validation         `bad-request`: an input the route does not admit.
//   authority          `bad-path`: the named repository is outside the configured authority.
//   domain-refused     a subject- or comparison-level refusal, carried by its own code.
//   network            no HTTP response at all -- the one state that offers an explicit retry.
//   unreadable         an HTTP response that is not this route's answer.
//
// Every block carries the owner's own words: its code, its reason, the offending input it named and
// the next action it published. Nothing here invents a recovery route, retries automatically, or
// renders an absent answer as an empty review. The one control beside a refusal is the task's own
// source change inventory, offered only for a refusal that answers for the *intent* half alone and
// only as a separate question the reader asks -- the client still names no dataset.

import type { ReviewFailure, ReviewPayload, ReviewResult } from '../../data/review';
import { intentOnlyRefusal, reviewProblemFromRefusal, unreadableAnswer } from '../../data/review';

const mutedStyle = { color: 'var(--muted)', margin: '0.2rem 0' } as const;

// The read's four phases, in the one module that renders the three that are not panes.
export type ReviewRead =
  | { phase: 'loading' }
  | { phase: 'reviewed'; payload: ReviewPayload }
  | { phase: 'refused'; problem: ReviewFailure }
  | { phase: 'failed'; problem: ReviewFailure };

// One typed result as the phase it is. A refusal is a phase and never a degraded success: a result
// whose `state` this client does not admit is a failure, so an unrecognized body can never be read as
// a review of an empty candidate.
export function readFrom(result: ReviewResult): ReviewRead {
  if (result.state === 'review' && result.payload) {
    return { phase: 'reviewed', payload: result.payload };
  }
  if (result.state === 'refused') {
    return {
      phase: 'refused',
      problem: result.refusal
        ? reviewProblemFromRefusal(result.refusal)
        : unreadableAnswer('refused'),
    };
  }
  return { phase: 'failed', problem: unreadableAnswer(result.state) };
}

// The payload the panes should render: the answer, or -- for a read that never answered -- the last
// comparison the surface really read. A typed refusal is the owner's answer about the leaf's current
// state, so it replaces the panes rather than sitting beside a comparison the server has just said it
// will not stand behind.
export function shownPayload(
  read: ReviewRead,
  lastCoherent: ReviewPayload | null,
): ReviewPayload | null {
  if (read.phase === 'reviewed') return read.payload;
  if (read.phase === 'failed') return lastCoherent;
  return null;
}

export const problemOf = (read: ReviewRead): ReviewFailure | null =>
  read.phase === 'refused' || read.phase === 'failed' ? read.problem : null;

// The read is in flight. It is a state of its own rather than a blank surface: a reader must be able
// to tell "nothing has answered yet" from "the answer was empty" and from every refusal below.
export function ReviewLoading() {
  return (
    <p style={mutedStyle} data-testid="review-loading" data-review-state="loading">
      reading this task's review…
    </p>
  );
}

// A successful answer with nothing in it. `measured` is what makes it known: the pair's two Git
// trees were really compared and differ at no path. Anything unmeasured, or any recorded knowledge,
// evidence or assessment, is not this state and does not reach here.
export function knownEmpty(payload: ReviewPayload): boolean {
  return (
    payload.source.inventory.state === 'measured' &&
    payload.source.inventory.listed_total === 0 &&
    payload.comparison === undefined &&
    payload.evidence.evidence_links.length === 0 &&
    payload.evidence.assessments.length === 0
  );
}

export function KnownEmptyNote() {
  return (
    <p
      style={{ margin: '0.2rem 0' }}
      data-testid="review-known-empty"
      data-review-state="known-empty"
    >
      known empty: the review answered — the bound pair differs at no path, no knowledge subject was
      compared, and no evidence or assessment is recorded. This is a measured empty result, not an
      unavailable one.
    </p>
  );
}

// One refusal or failure, rendered from the owner's own fields. `origin` names which of the route's
// two shapes produced it -- its typed refusal, or a transport-level body -- so a reader (and a test)
// can tell the two apart while both go through this single renderer. `subject` names what could not
// be opened (the review itself, or one entry's content in the Source pane); every field the owner
// published is rendered, so a caller holding a `ReviewFailure` never has to condense it.
export function ReviewProblemBlock({
  problem,
  origin,
  subject = 'the review',
  onRetry,
  onOpenTaskContext,
}: {
  problem: ReviewFailure;
  origin: 'refusal' | 'failure';
  subject?: string;
  onRetry?: () => void;
  onOpenTaskContext?: () => void;
}) {
  return (
    <div
      data-testid={origin === 'refusal' ? 'review-refusal' : 'review-failure'}
      data-review-state={problem.token}
      data-review-code={problem.code}
    >
      <p style={{ margin: '0.2rem 0' }}>
        {subject} unavailable · {problem.code}
      </p>
      <details>
        <summary>Reason and recovery</summary>
        <p style={{ margin: '0.2rem 0' }}>
          {subject} could not be opened ({problem.code}): {problem.detail}
        </p>
        {problem.offendingInput ? (
          <p style={mutedStyle} data-testid="review-offending-input">
            offending input: {problem.offendingInput}
          </p>
        ) : (
          <p style={mutedStyle} data-testid="review-no-offending-input">
            the server named no offending input for this failure.
          </p>
        )}
        {problem.nextAction ? (
          <p style={mutedStyle} data-testid="review-next-action">
            next: {problem.nextAction}
          </p>
        ) : (
          <p style={mutedStyle} data-testid="review-no-next-action">
            the server published no next action for this failure.
          </p>
        )}
      </details>
      {problem.token === 'network' && onRetry ? (
        <p style={{ margin: '0.2rem 0' }}>
          <button type="button" data-testid="review-retry" onClick={onRetry}>
            retry the review read
          </button>
        </p>
      ) : null}
      {onOpenTaskContext && intentOnlyRefusal(problem.code) ? (
        <p style={{ margin: '0.2rem 0' }} data-testid="review-source-instead-offer">
          <button type="button" data-testid="review-source-instead" onClick={onOpenTaskContext}>
            Review source changes
          </button>
        </p>
      ) : null}
    </div>
  );
}

// The note shown above the source inventory when the reader asked for it after a subject review
// refused. It keeps the refusal's own reason, offending input and recovery route visible: what is
// shown instead is an answer to a different, explicitly asked question, never a silent substitution
// for the review.
export function TaskContextInsteadNote({ problem }: { problem: ReviewFailure }) {
  return (
    <p style={{ margin: '0.2rem 0' }} data-testid="review-source-instead-note">
      the subject review refused ({problem.code}): {problem.detail}
      {problem.offendingInput ? ` · offending input: ${problem.offendingInput}` : ''} — shown below
      is this task&apos;s complete source change inventory, asked as its own question with no
      subject.
      {problem.nextAction
        ? ` the subject review's own next action still stands: ${problem.nextAction}`
        : ''}
    </p>
  );
}

// The label on a comparison that is still on screen although the read that would have replaced it
// failed. Its last sentence is decided by what the retained generation actually is: a real comparison
// is one this surface will not let a failed read deny, while a retained MEASURED-EMPTY generation is
// an earlier read's own measured answer -- stated as the known-empty result it is, never denied.
export function RetainedGenerationNote({ payload }: { payload: ReviewPayload }) {
  const measuredNothing = knownEmpty(payload);
  return (
    <p style={{ margin: '0.2rem 0' }} data-testid="review-retained-generation">
      the comparison below is the last one this surface read
      {payload.comparison ? ` (${payload.comparison.reference})` : ''} — the failed read did not
      replace it
      {measuredNothing
        ? '; that read measured no change, and this surface makes no claim for the read that failed.'
        : ' and no empty review is claimed for it.'}
    </p>
  );
}

// Every note the surface shows for a read, in one place: the loading state, the refusal/failure
// block, the note that keeps a refusal visible beside the inventory the reader asked for instead, and
// the two statements about a shown payload that is not a written review -- the known-empty result it
// measured, and the comparison retained from an earlier read.
//
// EXACTLY ONE of those two is rendered for any shown payload, and that is a property of this
// function rather than of its callers: `RetainedGenerationNote` requires a retained generation that
// is NOT known-empty, while `KnownEmptyNote` requires a shown payload that IS. So a retained
// known-empty is stated once, as the measured result it is, and never also denied -- which is what a
// payload measuring nothing would otherwise have claimed twice in one DOM. `lastCoherent` is
// non-null only for a read that failed without answering.
export function ReviewOutcomeRegion({
  read,
  instead,
  shown,
  lastCoherent,
  onRetry,
  onOpenTaskContext,
}: {
  read: ReviewRead;
  instead: ReviewFailure | null;
  shown: ReviewPayload | null;
  lastCoherent: ReviewPayload | null;
  onRetry?: () => void;
  onOpenTaskContext?: () => void;
}) {
  const problem = problemOf(read);
  const measuredNothing = shown !== null && knownEmpty(shown);
  const retainedIsReal = lastCoherent !== null && !knownEmpty(lastCoherent);
  return (
    <>
      {read.phase === 'loading' ? <ReviewLoading /> : null}
      {problem ? (
        <ReviewProblemBlock
          origin={read.phase === 'refused' ? 'refusal' : 'failure'}
          problem={problem}
          onRetry={onRetry}
          onOpenTaskContext={onOpenTaskContext}
        />
      ) : null}
      {instead && read.phase === 'reviewed' ? <TaskContextInsteadNote problem={instead} /> : null}
      {measuredNothing ? <KnownEmptyNote /> : null}
      {retainedIsReal ? <RetainedGenerationNote payload={lastCoherent} /> : null}
    </>
  );
}
