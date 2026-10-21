// The review page's one selection (MIK-R89 v2 rules 2, 9, 13): nothing at the opening, then what the
// reader selects in the tree, in the reading column, by `j`/`k`, by a marker, or what a reload restores
// from the URL. A selection reads its subject (`/api/review/intent?selector…`) unless an older
// comparison is shown (W17), takes its cards from read 5 (W4), goes into the URL by
// `history.replaceState` (W20), and arms the column's one scroll to it (rule 9).
import { useCallback, useEffect, useState } from 'react';

import { parseReviewHash, readReviewHashState, writeReviewHashState } from '../../../cockpit/reviewHash';
import type { ReviewFailure } from '../../../data/review';
import { readFileClassification } from '../../../data/reviewLane';
import { blockKey } from '../blockIdentity';
import type { ChangeSetCards, ChangeSetModel } from '../changeSetOrder';
import type { FamilySelection } from '../FamilyTree';
import type { ReviewSubject } from '../ReviewNavigation';
import type { ReviewPageRequest } from '../ReviewReadCycle';
import type { ReviewSelection } from '../reviewSelection';
import type { WorkspaceState } from '../workspaceState';
import { encodeSelection, hunkFileOf, restoreSelection, type FileHunks, type RestoreFacts } from './selectionRestore';
import type { ScrollTarget } from './useScrollOnce';

export interface SelectOptions {
  // A selection made inside the page keeps the tree the reader walked (MIK-R39); the URL's restore
  // and a named target start it afresh.
  keepTree?: boolean;
  page?: ReviewPageRequest;
  // The family-tree position the selection names, when the caller knows it (a member of a family).
  context?: FamilySelection | null;
}

export interface SelectionDeps {
  navigationSelect: (subject: ReviewSubject | undefined, context?: FamilySelection, options?: { keepTree?: boolean; page?: ReviewPageRequest }) => void;
  workspace: WorkspaceState;
  setInstead: (failure: ReviewFailure | null) => void;
  setPage: (page: ReviewPageRequest | undefined) => void;
  arm: (target: ScrollTarget | null) => void;
  cards: ChangeSetCards;
  model: ChangeSetModel;
  older: boolean;
}

// The subject a selection reads: its own, or a block's invariant; a lane part and an older comparison
// read none.
export function readSubjectOf(selection: ReviewSelection | null, cards: ChangeSetCards, older: boolean): ReviewSubject | undefined {
  if (older || !selection) return undefined;
  if (selection.kind === 'subject') return selection.subject;
  if (selection.kind !== 'block' || selection.block.kind !== 'entry') return undefined;
  const id = selection.block.entry;
  const entry = cards.state === 'ready' ? cards.entries.find((one) => one.id === id) : undefined;
  return entry ? { kind: 'invariant', id: entry.invariant_key } : undefined;
}

function invariantOf(selection: ReviewSelection, subject: ReviewSubject | undefined): { id?: string; family?: string } {
  if (selection.kind === 'subject') return { id: selection.subject.id, family: selection.family };
  return { id: subject?.kind === 'invariant' ? subject.id : undefined };
}

// The family-tree position of a selection: a family, or an invariant at its member row of the family
// it was selected under (else its first place).
function familyContextOf(selection: ReviewSelection | null, subject: ReviewSubject | undefined, model: ChangeSetModel): FamilySelection | null {
  if (!selection) return null;
  if (selection.kind === 'subject' && selection.subject.kind === 'family') return { familyId: selection.subject.id };
  const { id, family } = invariantOf(selection, subject);
  if (!id) return null;
  const rows = model.groups.flatMap((group) => group.subjects.map((node) => node.row));
  const row = rows.find((one) => one.selectorId === id && (family === undefined || one.familyId === family));
  return row?.familyId ? { familyId: row.familyId, memberRevisionId: row.revisionId } : null;
}

function scrollTargetOf(selection: ReviewSelection | null, model: ChangeSetModel): ScrollTarget | null {
  if (!selection) return null;
  if (selection.kind === 'lane') return { kind: 'lane', path: selection.path };
  if (selection.kind === 'block') return { kind: 'block', key: blockKey(selection.block) };
  if (selection.subject.kind === 'family') return { kind: 'subject', key: `family:${selection.subject.id}` };
  const id = selection.subject.id;
  const node = model.subjects.find((one) => one.row.selectorId === id);
  return node ? { kind: 'subject', key: node.key } : null;
}

// `deps` is filled by the page after each render (its model is derived from the selection, so it
// comes after this hook); a selection reads it when it is made.
export function useReviewSelection(initial: ReviewSelection | null, deps: React.RefObject<SelectionDeps | null>) {
  const [selection, setSelection] = useState<ReviewSelection | null>(initial);
  const select = useCallback(
    (next: ReviewSelection | null, options: SelectOptions = {}) => {
      const current = deps.current;
      if (!current) return;
      const { navigationSelect, workspace, setInstead, setPage, arm, cards, model, older } = current;
      const subject = readSubjectOf(next, cards, older);
      const context = options.context !== undefined ? options.context : familyContextOf(next, subject, model);
      const keepTree = options.keepTree ?? true;
      setSelection(next);
      arm(scrollTargetOf(next, model));
      workspace.focusSelection.current = { from: document.activeElement, after: workspace.treeIntent.id };
      workspace.beginSelection(keepTree);
      navigationSelect(subject, context ?? undefined, { keepTree, page: options.page });
      setInstead(null);
      setPage(options.page);
      workspace.setChosen(context);
    },
    [deps],
  );
  return { selection, select };
}

// The classification of the file a URL's hunk names (rule 13): read once for that file and comparison,
// through the review page's target in the same URL.
async function readHunks(path: string, number: number): Promise<FileHunks> {
  const target = parseReviewHash(window.location.hash);
  if (!target?.master || !target.leaf) return { state: 'unavailable', reason: 'the URL names no review target' };
  const read = await readFileClassification(target.repo, target.master, target.leaf, number, path);
  if (read.phase === 'ready') return { state: 'ready', spans: read.value.hunks.map(({ before, after }) => ({ before, after })) };
  return { state: 'unavailable', reason: read.phase === 'unavailable' ? read.problem.detail : 'its read did not answer' };
}

function useHunkFile(path: string | null, number: number | undefined): ReadonlyMap<string, FileHunks> | undefined {
  const key = path === null || number === undefined ? null : `${number}:${path}`;
  const [read, setRead] = useState<{ key: string; path: string; hunks: FileHunks } | null>(null);
  useEffect(() => {
    if (key === null || path === null || number === undefined) return undefined;
    let live = true;
    setRead({ key, path, hunks: { state: 'reading' } });
    void readHunks(path, number).then((hunks) => {
      if (live) setRead({ key, path, hunks });
    });
    return () => {
      live = false;
    };
  }, [key, path, number]);
  return read !== null && read.key === key ? new Map([[read.path, read.hunks]]) : undefined;
}

// The URL's part of the selection: restored once the reads it needs have answered, then written on
// every selection (and with the comparison shown when it is not the latest). A selection made while
// the restore waits supersedes it (rule 9): the restore is dropped, so its late answer neither
// replaces that selection nor its URL, and arms no landing.
export function useSelectionUrl(input: {
  selection: ReviewSelection | null;
  chosen: number | null;
  older: boolean;
  facts: RestoreFacts;
  select: (selection: ReviewSelection, options: SelectOptions) => void;
}): string | null {
  const [pending, setPending] = useState<{ value: string; under: ReviewSelection | null } | null>(() => {
    const value = readReviewHashState().sel;
    return value === null ? null : { value, under: input.selection };
  });
  const [outsideLine, setOutside] = useState<string | null>(null);
  const restored = pending === null;
  const { facts, select, selection } = input;
  const files = useHunkFile(pending === null ? null : hunkFileOf(pending.value, facts), facts.number);
  useEffect(() => {
    if (pending === null) return;
    if (selection !== pending.under) {
      setPending(null);
      return;
    }
    const result = restoreSelection(pending.value, files ? { ...facts, files } : facts);
    if (result.state === 'wait') return;
    setPending(null);
    if (result.state === 'select') select(result.selection, { keepTree: false });
    else setOutside(result.reason);
  }, [pending, facts, files, select, selection]);
  const sel = encodeSelection(input.selection);
  const comparison = input.older ? input.chosen : null;
  useEffect(() => {
    if (!restored) return;
    writeReviewHashState({ sel, comparison });
  }, [restored, sel, comparison]);
  // A new selection makes the restore's line moot.
  useEffect(() => {
    if (input.selection) setOutside(null);
  }, [input.selection]);
  return outsideLine;
}
