/** Exact evidence navigation for the mounted explanation and review surfaces. */

import type { CanvasComparison, EvidenceDestination } from './canvasApi';

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
