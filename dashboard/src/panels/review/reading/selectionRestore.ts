// A reload restores the selection the URL names (MIK-R89 v2 rule 9, W20), by rule 13's identity, a
// lane part, or a subject with the family it was selected under. The URL's value is checked against
// the comparison on screen once the reads it needs have answered: a block against the card read, a
// lane file against the lane read, a lane hunk against the lane read and then its spans against its
// file's classification, a subject and its family against the outline. A selection outside the
// comparison opens the page unselected, with one line saying why (Failure and Recovery). Nothing is
// selected on a guess.
import type { LaneRead, ReviewLaneSpan, ReviewUnexplainedLane } from '../../../data/reviewLane';
import type { ReviewTreeComparison } from '../../../data/reviewTrees';
import type { ReviewCatalogueRead } from '../../../data/useReviewCatalogue';
import {
  blockIdentityOf,
  comparisonTrees,
  formatSelection,
  inComparison,
  parseSelection,
  sameBlock,
  type BlockIdentity,
  type HunkIdentity,
  type ReviewSubjectRef,
} from '../blockIdentity';
import type { ChangeSetCards } from '../changeSetOrder';
import type { ReviewSelection } from '../reviewSelection';
import { laneFiles } from './LaneSection';

export type Restored =
  | { state: 'wait' }
  | { state: 'select'; selection: ReviewSelection }
  | { state: 'outside'; reason: string };

// A lane file's hunks as its classification read gave them (rule 13: a hunk is named by its spans
// through the file's diff), read for a hunk the URL names.
export type FileHunks =
  | { state: 'reading' }
  | { state: 'ready'; spans: readonly { before: ReviewLaneSpan; after: ReviewLaneSpan }[] }
  | { state: 'unavailable'; reason: string };

export interface RestoreFacts {
  number: number | undefined;
  comparison: ReviewTreeComparison | null;
  cards: ChangeSetCards;
  catalogue: ReviewCatalogueRead;
  lane: LaneRead<ReviewUnexplainedLane> | null;
  // The classification of the file a URL's hunk names, by path, once it is asked for.
  files?: ReadonlyMap<string, FileHunks>;
}

const WAIT: Restored = { state: 'wait' };

function outside(facts: RestoreFacts, why: string): Restored {
  const number = facts.number === undefined ? '' : ` #${facts.number}`;
  return { state: 'outside', reason: `The link's selection is not part of comparison${number}: ${why} It opens unselected.` };
}

export function knownSubjects(catalogue: ReviewCatalogueRead): Set<string> {
  const ids = new Set<string>();
  for (const row of catalogue.outline?.invariants ?? []) ids.add(`invariant:${row.selector_id}`);
  for (const row of catalogue.outline?.families ?? []) ids.add(`family:${row.selector_id}`);
  for (const row of catalogue.entries ?? []) ids.add(`${row.selector_kind}:${row.selector_id}`);
  return ids;
}

// The page's own parts of the URL form beside rule 13's (`formatSelection`): the lane, `l:` with the
// file's path or nothing for the "Code without invariant" row, and the family a member was selected
// under, `@<family>` after its subject. `encodeURIComponent` never leaves an `@` in a subject's id.
const LANE = 'l:';
const FAMILY = '@';

export function encodeSelection(selection: ReviewSelection | null): string | null {
  if (!selection) return null;
  if (selection.kind === 'lane') return `${LANE}${selection.path === undefined ? '' : encodeURIComponent(selection.path)}`;
  if (selection.kind === 'block') return formatSelection({ kind: 'block', block: selection.block });
  const subject = formatSelection({ kind: 'subject', subject: selection.subject });
  return selection.family ? `${subject}${FAMILY}${encodeURIComponent(selection.family)}` : subject;
}

function decoded(text: string): string | null {
  try {
    return decodeURIComponent(text);
  } catch {
    return null;
  }
}

function restoreSubject(subject: ReviewSubjectRef, family: string | undefined, facts: RestoreFacts): Restored {
  const { catalogue } = facts;
  if (catalogue.loading || (catalogue.entries === undefined && catalogue.problem === undefined)) return WAIT;
  const known = knownSubjects(catalogue);
  if (!known.has(`${subject.kind}:${subject.id}`))
    return outside(facts, `it names a ${subject.kind} that this comparison does not record.`);
  if (family !== undefined && !known.has(`family:${family}`))
    return outside(facts, 'it names a family that this comparison does not record.');
  return { state: 'select', selection: { kind: 'subject', subject, ...(family !== undefined ? { family } : {}) } };
}

function restoreLane(rest: string, facts: RestoreFacts): Restored {
  const path = rest === '' ? undefined : decoded(rest);
  if (path === null) return outside(facts, 'its selection could not be read.');
  const { lane } = facts;
  if (lane === null || lane.phase === 'loading') return WAIT;
  if (lane.phase === 'unavailable') return outside(facts, `its lane could not be read (${lane.problem.detail}).`);
  if (path !== undefined && !laneFiles(lane.value).some((file) => file.path === path))
    return outside(facts, 'it names a file that is not code without an invariant here.');
  return { state: 'select', selection: { kind: 'lane', ...(path !== undefined ? { path } : {}) } };
}

// A subject's value and the family it was selected under, if the value names one.
function splitFamily(value: string): { base: string; family?: string | null } {
  const at = value.startsWith('s:') ? value.indexOf(FAMILY) : -1;
  if (at === -1) return { base: value };
  return { base: value.slice(0, at), family: decoded(value.slice(at + 1)) };
}

function restoreBlock(block: BlockIdentity, facts: RestoreFacts): Restored {
  const { cards } = facts;
  if (cards.state === 'reading') return WAIT;
  if (cards.state === 'unavailable') return outside(facts, `its code locations could not be read (${cards.reason}).`);
  if (!inComparison(block, comparisonTrees(cards.comparison)))
    return outside(facts, 'it names the trees of another comparison.');
  const entry = cards.entries.find((one) => one.id === block.entry);
  if (!entry || !sameBlock(blockIdentityOf(entry, cards.comparison), block))
    return outside(facts, 'it names a block this comparison does not hold.');
  return { state: 'select', selection: { kind: 'block', block } };
}

// A hunk's trees and file, against the comparison and the lane: an answer, or null when only its
// spans are left to check against the file's classification.
function hunkInLane(hunk: HunkIdentity, facts: RestoreFacts): Restored | null {
  const { lane, comparison } = facts;
  if (lane === null || lane.phase === 'loading' || comparison === null) return WAIT;
  if (lane.phase === 'unavailable') return outside(facts, `its lane could not be read (${lane.problem.detail}).`);
  if (!inComparison(hunk, comparisonTrees(comparison))) return outside(facts, 'it names the trees of another comparison.');
  if (!laneFiles(lane.value).some((file) => file.path === hunk.path))
    return outside(facts, 'it names a file that is not code without an invariant here.');
  return null;
}

const sameSpan = (left: ReviewLaneSpan, right: ReviewLaneSpan) => left.start === right.start && left.count === right.count;

// A hunk is selected only when its file's classification holds both its spans (rule 13's complete
// identity); until that read answers the restore waits.
function restoreHunk(hunk: HunkIdentity, facts: RestoreFacts): Restored {
  const lane = hunkInLane(hunk, facts);
  if (lane) return lane;
  const file = facts.files?.get(hunk.path);
  if (file === undefined || file.state === 'reading') return WAIT;
  if (file.state === 'unavailable') return outside(facts, `its file could not be read (${file.reason}).`);
  if (!file.spans.some((one) => sameSpan(one.before, hunk.before) && sameSpan(one.after, hunk.after)))
    return outside(facts, 'it names a hunk that its file does not hold.');
  return { state: 'select', selection: { kind: 'block', block: hunk } };
}

// The file whose classification the URL's selection waits for: a hunk's, once its trees and file are
// this comparison's.
export function hunkFileOf(value: string, facts: RestoreFacts): string | null {
  const parsed = parseSelection(value);
  if (parsed?.kind !== 'block' || parsed.block.kind !== 'hunk') return null;
  return hunkInLane(parsed.block, facts) === null ? parsed.block.path : null;
}

export function restoreSelection(value: string, facts: RestoreFacts): Restored {
  if (value.startsWith(LANE)) return restoreLane(value.slice(LANE.length), facts);
  const { base, family } = splitFamily(value);
  const parsed = family === null ? null : parseSelection(base);
  if (!parsed) return outside(facts, 'its selection could not be read.');
  if (parsed.kind === 'subject') return restoreSubject(parsed.subject, family ?? undefined, facts);
  return parsed.block.kind === 'entry' ? restoreBlock(parsed.block, facts) : restoreHunk(parsed.block, facts);
}
