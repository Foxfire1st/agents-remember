/**
 * What the review panel may say about a page, and what it must not imply (MIK-R105 rules 2, 8, 10).
 *
 * Pure functions only: the panel's rendering decisions are testable without a browser, and the
 * distinction the packet insists on -- a saved page, a stale page, a refused start, a presenter's
 * inference -- stays in one place instead of in JSX branches.
 *
 * The comparison on screen is an input to every decision here (L104-B11): a page built for one
 * comparison is never described as the page for another, and an evidence destination that belongs to
 * another comparison (or that this surface cannot represent exactly) is refused by name rather than
 * replaced with a weaker record.
 */

import type {
  CanvasAttempt,
  CanvasComparison,
  CanvasFrameState,
  EvidenceDestination,
} from './canvasApi';

export type PanelPhase =
  'unavailable' | 'empty' | 'ready' | 'stale' | 'generating' | 'failed' | 'recovery';

/** The one page lineage of a leaf. */
const FRAME_ID = 'review-page';

/**
 * One page lineage per leaf. It takes no comparison at all: the retained revision, its staleness and
 * its regeneration all hang off one stable frame, so a moved comparison marks that frame stale
 * instead of opening an empty sibling (L104-B12). A caller that could name a comparison here could
 * one day select a different folder with it; the signature is what makes that impossible.
 */
export function frameForComparison(): string {
  return FRAME_ID;
}

/**
 * The comparison number a saved binding or an evidence destination names, if it names one.
 *
 * The canvas host spells a binding `review-comparison:<task>/<leaf>/<number>`. A review payload's own
 * `comparison.reference` is a different identity (its binding digest), so only this spelling is
 * parsed here -- a digest that happens to end in digits must never be read as a lineage number.
 */
export function comparisonNumberOf(reference: string | null | undefined): number | undefined {
  const match = reference ? /^review-comparison:.+\/(\d+)$/.exec(reference) : null;
  return match ? Number(match[1]) : undefined;
}

/**
 * Whether a saved revision binds a comparison other than the one the reviewer is reading.
 *
 * `undefined` means the two cannot be compared (one side names no lineage number), and the caller
 * falls back to the server's own judgement rather than inventing one.
 */
function comparisonMoved(
  bound: string | null | undefined,
  shown?: CanvasComparison,
): boolean | undefined {
  const saved = comparisonNumberOf(bound);
  if (saved === undefined || shown?.number === undefined) return undefined;
  return saved !== shown.number;
}

/**
 * The comparison a saved revision is bound to, from the host's own answer.
 *
 * The host's `frame_state` names it on the ready manifest's binding, and repeats it on the presenter
 * view; a flatter answer may name it directly. Every spelling is the same identity, so all are read
 * rather than assuming one shape (the captured answer in `CanvasPanel.test.tsx` is the real one).
 */
export function savedComparison(state: CanvasFrameState | null | undefined): string | null {
  if (!state) return null;
  return (
    state.opening?.comparison ?? state.presenter?.comparison ?? manifestReference(state) ?? null
  );
}

/** The binding reference the ready manifest itself carries. */
function manifestReference(state: CanvasFrameState): string | null {
  return state.opening?.manifest?.binding?.comparison_reference ?? null;
}

/** The entrypoint a saved revision opens, from the same three spellings. */
export function savedEntrypoint(state: CanvasFrameState | null | undefined): string | null {
  if (!state) return null;
  return (
    state.opening?.entrypoint ?? state.presenter?.entrypoint ?? manifestEntrypoint(state) ?? null
  );
}

/** The entrypoint path the ready manifest itself carries. */
function manifestEntrypoint(state: CanvasFrameState): string | null {
  return state.opening?.manifest?.entrypoint?.path ?? null;
}

const LIVE_STATES = new Set(['started', 'running']);

/**
 * The saved phase of a frame that has no live attempt.
 *
 * The comparison on screen decides, when the panel can compare it (L104-B11): a ready revision that
 * binds another comparison is `stale`, and one that binds exactly the comparison being read is
 * `ready` even while the host judges it stale against whatever comparison the host reads now -- a
 * reviewer reading a recorded comparison is not shown a false "earlier comparison" (a historical
 * read). When either side names no lineage number the server's own staleness stands. A missing
 * retained revision is `recovery`, never `empty`: a page was built, its assets cannot be read, and a
 * fresh empty frame would hide that (L104-B12).
 */
function savedPhase(state: CanvasFrameState, shown?: CanvasComparison): PanelPhase {
  if (state.opening.state === 'ready') {
    const moved = comparisonMoved(savedComparison(state), shown);
    if (moved === true) return 'stale';
    if (moved === false) return 'ready';
    return state.staleness.state === 'stale' ? 'stale' : 'ready';
  }
  if (state.presenter.revision !== null) return 'recovery';
  return state.presenter.attempts.length > 0 ? 'failed' : 'empty';
}

export function panelPhase(
  state: CanvasFrameState | null,
  live?: CanvasAttempt | null,
  comparison?: CanvasComparison,
): PanelPhase {
  // A body that is not a frame state at all (no opening, presenter or staleness) is unreadable, not
  // empty: every later sentence here reads those sections.
  if (!state || state.state !== 'ok' || !state.opening || !state.presenter || !state.staleness) {
    return 'unavailable';
  }
  // A live attempt is the phase even before its first revision exists: the builder is working and the
  // panel must show that rather than an empty page that hides it.
  if (live && LIVE_STATES.has(live.state)) return 'generating';
  return savedPhase(state, comparison);
}

export function generateLabel(phase: PanelPhase): string {
  if (phase === 'stale') return 'Regenerate for this comparison';
  if (phase === 'failed') return 'Try again';
  if (phase === 'ready') return 'Regenerate';
  if (phase === 'recovery') return 'Generate a new page';
  return 'Generate the page';
}

/** Whether the control may be pressed: a start needs a comparison and no live attempt. */
export function canGenerate(state: CanvasFrameState | null, phase: PanelPhase): boolean {
  if (!state || state.state !== 'ok') return false;
  if (state.launch?.state !== 'ok') return false;
  return phase !== 'generating';
}

/** What one explicit regeneration starts from, or the refusal the panel must state instead. */
export type Regeneration =
  { state: 'retained'; revision: number } | { state: 'unavailable'; detail: string };

const NO_RETAINED =
  'The revision this page was built from can no longer be read, so there is nothing to regenerate ' +
  'from. Generate a new page for this comparison instead.';

/**
 * The retained revision a regeneration starts from (MIK-R105 rule 6).
 *
 * Only a ready revision is retained assets. The panel sends this revision with the start so the
 * request names the page the reviewer is looking at, and starts nothing when it is absent: a
 * regeneration that silently began from scratch would be a first generation wearing the word.
 */
export function regeneration(state: CanvasFrameState | null): Regeneration {
  const revision = state?.opening?.state === 'ready' ? state.opening.revision : null;
  if (revision !== null) return { state: 'retained', revision };
  return { state: 'unavailable', detail: NO_RETAINED };
}

export function progressLines(attempt: CanvasAttempt | null): string[] {
  if (!attempt) return [];
  const lines = attempt.activity.map((entry) => `${entry.kind}: ${entry.text}`);
  if (attempt.state === 'failed' && attempt.detail) lines.push(`failed: ${attempt.detail}`);
  if (attempt.state === 'cancelled' && attempt.detail) lines.push(`cancelled: ${attempt.detail}`);
  return lines;
}

/** The sentence that explains why no page is shown, in the packet's own vocabulary. */
const PHASE_LINES: Record<
  PanelPhase,
  (state: CanvasFrameState, comparison?: CanvasComparison) => string
> = {
  unavailable: (state) => state.detail ?? 'the page could not be read',
  empty: () => 'No page has been generated for this comparison.',
  stale: (state, comparison) => staleLine(state, comparison),
  generating: () => 'The presenter is building the page.',
  failed: (state) => state.presenter.detail || 'The last attempt did not finish.',
  recovery: (state) =>
    `The page saved for this comparison cannot be read: ${
      state.opening.detail || state.presenter.detail
    } Generate a new page for this comparison, or ask the task owner to restore the retained revision.`,
  ready: (state) =>
    `Ready: revision ${state.opening.revision} of ${savedComparison(state) ?? 'this comparison'}.`,
};

/** Why the saved page is stale: the comparison on screen first, then the host's own change list. */
function staleLine(state: CanvasFrameState, comparison?: CanvasComparison): string {
  if (comparisonMoved(savedComparison(state), comparison) === true)
    return movedLine(state, comparison);
  const changed = state.staleness.changed ?? [];
  if (changed.length)
    return `This page explains an earlier comparison; ${changed.join(', ')} moved.`;
  return movedLine(state, comparison);
}

/** The sentence that names the comparison a page explains beside the one on screen. */
function movedLine(state: CanvasFrameState, comparison?: CanvasComparison): string {
  const bound = comparisonNumberOf(savedComparison(state));
  return (
    `This page explains comparison ${bound ?? 'another'}; comparison ` +
    `${comparison?.number ?? 'another'} is on screen, so it stays as it was saved and is stale here.`
  );
}

export function phaseLine(
  state: CanvasFrameState | null,
  phase: PanelPhase,
  comparison?: CanvasComparison,
): string {
  if (!state) return 'Reading the saved page…';
  return PHASE_LINES[phase](state, comparison);
}

/** What the panel is showing, so an evidence destination can be checked against it. */
export interface EvidenceContext {
  repository: string;
  master: string;
  leaf: string;
  comparison?: CanvasComparison;
}

/** What an opened evidence reference became: a sentence, and a navigation only when it is exact. */
export interface EvidenceLine {
  line: string;
  selector?: { kind: 'invariant' | 'family'; id: string };
  location?: Record<string, unknown>;
}

/**
 * What an opened evidence reference is, and whether this mounted review can show it in place.
 *
 * The host has already resolved the reference against the admitted revision; the panel must not
 * re-derive or widen it. A destination that names another review target or another comparison is
 * refused by name, and a destination this surface cannot represent (a source blob at its tree, a
 * judgment row at its attempt, a proof entry at its record) is named and left unopened -- it is never
 * replaced with today's record or with a different history (L104-B11).
 */
export function destinationLine(
  destination: EvidenceDestination,
  context: EvidenceContext,
): EvidenceLine {
  const reference = destination.reference ?? '';
  if (destination.state === 'refused') {
    return { line: `Not opened: ${destination.detail ?? reference}` };
  }
  if (destination.state === 'unavailable') {
    return { line: `Unavailable: ${destination.detail ?? reference}` };
  }
  const identity = destinationIdentity(destination);
  const foreign = foreignReason(destination, context);
  if (foreign) return { line: `Not opened here: ${identity}; ${foreign}.` };
  const selector = reviewSelector(destination.location?.record_id);
  if (!selector) {
    return {
      line: `Not opened here: ${identity}; this review surface does not represent that exact destination, so nothing was opened in its place.`,
    };
  }
  return {
    line: `Opening ${identity} in this review.`,
    selector,
    location: destination.location ?? {},
  };
}

/** The full identity a destination travels with: kind, reference, its tree/revision/row and target. */
function destinationIdentity(destination: EvidenceDestination): string {
  const where = locationIdentity(destination.location ?? {});
  const comparison = destination.comparison ?? destination.target?.comparison;
  return [
    `${destination.kind ?? 'reference'} ${destination.reference ?? ''}`.trim(),
    where,
    comparison ? `in comparison ${comparison}` : '',
  ]
    .filter(Boolean)
    .join(' ');
}

/** The typed part of a destination's identity, named only when the owner supplied it. */
function locationIdentity(location: Record<string, unknown>): string {
  const names = ['path', 'tree', 'record_id', 'row_id', 'proof_id'] as const;
  const parts = names
    .filter((name) => typeof location[name] === 'string' && location[name])
    .map((name) => `${name} ${String(location[name])}`);
  const revision = location.record_revision;
  if (revision !== undefined && revision !== null) parts.push(`revision ${String(revision)}`);
  return parts.length ? `at ${parts.join(', ')}` : '';
}

/** Why this destination does not belong to the review on screen, or nothing when it does. */
function foreignReason(
  destination: EvidenceDestination,
  context: EvidenceContext,
): string | undefined {
  const target = destination.target;
  if (!target?.repository || !target.master || !target.leaf) {
    return 'the host named no review target for it, so this surface cannot bind it to this comparison';
  }
  const belongs = `${target.repository}/${target.master}/${target.leaf}`;
  if (belongs !== `${context.repository}/${context.master}/${context.leaf}`) {
    return `it belongs to review ${belongs}`;
  }
  return foreignComparison(destination.comparison ?? target.comparison, context.comparison);
}

/** Whether the destination's comparison is the one on screen; unknown identities never pass. */
function foreignComparison(
  bound: string | undefined,
  shown?: CanvasComparison,
): string | undefined {
  const number = comparisonNumberOf(bound);
  if (number === undefined || shown?.number === undefined) {
    return 'its comparison and the comparison on screen cannot be compared, so none was substituted';
  }
  if (number !== shown.number) {
    return `it belongs to comparison ${number}, not comparison ${shown.number} on screen`;
  }
  return undefined;
}

/** The only destinations this surface can navigate to itself: the two review selector kinds. */
function reviewSelector(
  recordId: unknown,
): { kind: 'invariant' | 'family'; id: string } | undefined {
  if (typeof recordId !== 'string') return undefined;
  if (recordId.startsWith('INV-')) return { kind: 'invariant', id: recordId };
  if (recordId.startsWith('FAM-')) return { kind: 'family', id: recordId };
  return undefined;
}

/** The presenter's authorship is its own, and the panel says so wherever a claim is shown. */
export const AUTHORSHIP_NOTE =
  'The presenter wrote this page. Its interpretation is not a review verdict, not accepted knowledge and not a gate decision.';
