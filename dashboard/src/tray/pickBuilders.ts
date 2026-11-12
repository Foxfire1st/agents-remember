// One builder per L89 reference kind (MIK-R188 rule 2). Each names its object exactly, as a
// structured reference; the server renders the written form with the call that opens it. The
// chip's title is "<id> · <title>", for example "RLZ-78JFRR · TurnEndedTracker · turn_ended.ts L99–144".
// A pick never carries the object's text.
import { blockIdentityOf, comparisonTrees, hunkIdentityOf } from '../panels/review/blockIdentity';
import type { ReviewLaneSpan } from '../data/reviewLane';
import type { ReviewTreeComparison, ReviewTreeEntry } from '../data/reviewTrees';
import type { TaskDocumentRef } from '../types/projection';
import type { ComparisonRef, TrayPickInput } from './types';

const fileName = (path: string): string => path.slice(path.lastIndexOf('/') + 1);
const span = (start: number, end: number): string => (start === end ? `L${start}` : `L${start}–${end}`);

// The comparison a review-page reference names. `repository` is the repository's id (the review
// answer's `repository_id`); the sides carry repository roots, not ids. The pin ref is the side's own
// ref, else the review ref every recorded comparison is pinned under.
export function comparisonRef(repository: string, comparison: ReviewTreeComparison): ComparisonRef {
  return {
    repository,
    task: comparison.task_id,
    leaf: comparison.leaf_id,
    number: comparison.number,
    pinRef:
      comparison.code_candidate.ref ?? `refs/ar/review/${comparison.task_id}/${comparison.leaf_id}/${comparison.number}`,
    trees: comparisonTrees(comparison),
  };
}

// An invariant or a family by its text id (INV-…/FAM-…, never the identity UUID the outline selects
// it by), with the revision the page knows and, on the review page, the comparison it was read in.
export function invariantPick(options: {
  repository: string;
  kind: 'invariant' | 'family';
  id: string;
  title: string;
  revisionId?: string;
  memoryTree: string;
  comparison?: ReviewTreeComparison;
}): TrayPickInput {
  const { repository, kind, id, title, revisionId, memoryTree, comparison } = options;
  return {
    kind: 'reference',
    title: `${id} · ${title}`,
    reference: {
      kind,
      repository,
      id,
      ...(revisionId ? { revisionId } : {}),
      memoryTree,
      ...(comparison ? { comparison: comparisonRef(repository, comparison) } : {}),
    },
  };
}

// A review block: one card-read entry, named by MIK-R89 v2 rule 13's identity.
export function reviewBlockPick(options: {
  repository: string;
  comparison: ReviewTreeComparison;
  entry: ReviewTreeEntry;
  invariantRevisionId?: string;
  symbol?: string;
}): TrayPickInput {
  const { repository, comparison, entry, invariantRevisionId, symbol } = options;
  const block = blockIdentityOf(entry, comparison);
  const where = `${fileName(block.path)}${block.range ? ` ${span(block.range.start, block.range.end)}` : ''}`;
  return {
    kind: 'reference',
    title: [entry.id, symbol, where].filter(Boolean).join(' · '),
    reference: {
      kind: 'review-block',
      comparison: comparisonRef(repository, comparison),
      block,
      invariant: entry.invariant,
      ...(invariantRevisionId ? { invariantRevisionId } : {}),
    },
  };
}

// Code without an invariant: a whole file of the lane, or one hunk named by its span.
export function laneCodePick(options: {
  repository: string;
  comparison: ReviewTreeComparison;
  path: string;
  hunk?: { before: ReviewLaneSpan; after: ReviewLaneSpan };
}): TrayPickInput {
  const { repository, comparison, path, hunk } = options;
  const where = hunk ? ` ${span(hunk.after.start, hunk.after.start + Math.max(hunk.after.count, 1) - 1)}` : '';
  return {
    kind: 'reference',
    title: `${fileName(path)}${where} · code without invariant`,
    reference: {
      kind: 'code-without-invariant',
      comparison: comparisonRef(repository, comparison),
      hunk: hunk ? hunkIdentityOf(path, hunk, comparison) : { kind: 'file', path },
    },
  };
}

export function taskPick(options: { reference: TaskDocumentRef; id: string; title: string }): TrayPickInput {
  const { reference, id, title } = options;
  return { kind: 'reference', title: `${id} · ${title}`, reference: { kind: 'task', reference, id } };
}

// File lines of the File Viewer: a repository, the viewer's scope ("mainline" or a worktree group),
// the path and the line range.
export function fileLinePick(options: {
  repository: string;
  scope: string;
  path: string;
  start: number;
  end: number;
}): TrayPickInput {
  const { repository, scope, path } = options;
  const start = Math.min(options.start, options.end);
  const end = Math.max(options.start, options.end);
  return {
    kind: 'reference',
    title: `${fileName(path)} ${span(start, end)} · ${repository}${scope === 'mainline' ? '' : ` (${scope})`}`,
    reference: { kind: 'file-line', repository, scope, path, start, end },
  };
}

// The change set: the review top line's comparison label carries its ⊕.
export function changeSetPick(options: { repository: string; comparison: ReviewTreeComparison }): TrayPickInput {
  const { repository, comparison } = options;
  return {
    kind: 'reference',
    title: `${comparison.leaf_id} @ #${comparison.number} · change set`,
    reference: { kind: 'change-set', comparison: comparisonRef(repository, comparison) },
  };
}
