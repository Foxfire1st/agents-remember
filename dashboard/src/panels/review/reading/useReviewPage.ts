// The review page's state above its reads (MIK-R89 v2 rules 2, 9, 10, 11 and 13): the comparison the
// reader chose, the tree the reader walked (MIK-R39), the one selection and its URL, the change-set
// model the tree and the reading column share, the column's one scroll to a selection, and the
// `j`/`k` stops (the model's changed blocks, then the lane's hunks as their files are read).
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';

import type { ReviewFailure, ReviewHistory, ReviewPayload } from '../../../data/review';
import type { LaneRead, ReviewUnexplainedLane } from '../../../data/reviewLane';
import type { HunkIdentity } from '../blockIdentity';
import type { SubjectNode } from '../changeSetOrder';
import { blockStops, laneStops, type TraversalStop } from '../changeTraversal';
import type { ReviewNavigation } from '../ReviewNavigation';
import type { ReviewRead } from '../ReviewOutcome';
import type { ReviewPageRequest } from '../ReviewReadCycle';
import type { ReviewSelection } from '../reviewSelection';
import { walkedTreeOf, type ReadingStatus } from '../ReviewWorkspace';
import type { TreeGroupName } from '../tree/TreeRows';
import { advanceWalk, type TreeIntent, type Walk } from '../walkedTree';
import type { WorkspaceState } from '../workspaceState';
import { groupDomId } from './blockDom';
import { laneFiles, type LaneFileFacts } from './LaneSection';
import { useChangeSet } from './useChangeSet';
import { useReviewSelection, useSelectionUrl, type SelectionDeps } from './useReviewSelection';
import { useScrollOnce } from './useScrollOnce';

// What the page takes from the surface's reads.
export interface PageReads {
  repo: string;
  master: string;
  leaf: string;
  history?: ReviewHistory;
  // Leave the recorded frame the target named, so the page reads its own default again (the landed
  // leaf's delivered comparison). The delivered link is that request (MIK-R195 F06).
  leaveHistory: () => void;
  navigation: ReviewNavigation;
  workspace: WorkspaceState;
  // The last admitted answer of the task context, and the answer for the subject on screen.
  frame: ReviewPayload | null;
  shown: ReviewPayload | null;
  reading: ReadingStatus | null;
  read: ReviewRead;
  // The failure the reader chose to read the task context instead of (null: the subject is read).
  instead: ReviewFailure | null;
  setInstead: (failure: ReviewFailure | null) => void;
  setPage: (page: ReviewPageRequest | undefined) => void;
  // The selection the target names (a subject), else none.
  initial: ReviewSelection | null;
  // The comparison chosen in the selector, null for the latest; the surface holds it because the
  // navigation reads that comparison's outline.
  chosen: number | null;
  setChosen: (chosen: number | null) => void;
}

const SEED: Walk = {
  scope: '',
  intent: 0,
  generation: null,
  source: null,
  families: [],
  current: new Set(),
  replaceNext: false,
  notice: null,
};

// The walk (walkedTree.ts), advanced with every answer the page shows; none before the first.
function useWalk(payload: ReviewPayload | null, scope: string, intent: TreeIntent): Walk | null {
  const [walk, setWalk] = useState<Walk>(SEED);
  if (!payload) return null;
  const next = advanceWalk(walk, payload, scope, intent);
  if (next !== walk) setWalk(next);
  return next;
}

const NO_FILES: ReadonlyMap<string, LaneFileFacts> = new Map();
const sameFacts = (left: LaneFileFacts | undefined, right: LaneFileFacts) =>
  left !== undefined && left.lines === right.lines && left.hunks.length === right.hunks.length;

// The lane files' facts the reading column has read, for the comparison on screen.
function useLaneFacts(number: number | undefined) {
  const [facts, setFacts] = useState<{ number?: number; files: ReadonlyMap<string, LaneFileFacts> }>({ files: NO_FILES });
  const onFile = useCallback(
    (path: string, next: LaneFileFacts) =>
      setFacts((previous) => {
        const files = previous.number === number ? previous.files : NO_FILES;
        if (sameFacts(files.get(path), next)) return previous;
        return { number, files: new Map(files).set(path, next) };
      }),
    [number],
  );
  const files = facts.number === number ? facts.files : NO_FILES;
  const lines = useMemo(() => new Map([...files].map(([path, file]) => [path, file.lines])), [files]);
  return { files, lines, onFile };
}

// A new target on the same mounted page (another review entry opened over it) selects what that
// target names, as an outside selection: the walk starts afresh.
function useTargetSelection(initial: ReviewSelection | null, select: ReturnType<typeof useReviewSelection>['select']) {
  const key = JSON.stringify(initial);
  const seen = useRef(key);
  const latest = useRef(initial);
  useEffect(() => {
    latest.current = initial;
  });
  useEffect(() => {
    if (seen.current === key) return;
    seen.current = key;
    select(latest.current, { keepTree: false });
  }, [key, select]);
}

function laneHunks(lane: LaneRead<ReviewUnexplainedLane> | null, files: ReadonlyMap<string, LaneFileFacts>): HunkIdentity[] {
  if (lane?.phase !== 'ready') return [];
  return laneFiles(lane.value).flatMap((file) => files.get(file.path)?.hunks ?? []);
}

// The lane's file paths in display order, so a file not read yet keeps its own place among the stops
// (rule 13: next and previous go from the displayed position).
function lanePaths(lane: LaneRead<ReviewUnexplainedLane> | null): string[] | undefined {
  return lane?.phase === 'ready' ? laneFiles(lane.value).map((file) => file.path) : undefined;
}

export function useReviewPage(reads: PageReads) {
  const { repo, master, leaf, history, navigation, workspace, chosen, setChosen } = reads;
  const payload = reads.shown ?? reads.frame;
  const walk = useWalk(payload, `${repo}/${master}/${leaf}/${history ?? 'live'}`, workspace.treeIntent);
  const entries = navigation.catalogue.entries;
  const walked = useMemo(() => (walk && payload ? walkedTreeOf(walk, payload, { entries }) : undefined), [walk, payload, entries]);
  const scroller = useRef<HTMLDivElement>(null);
  const scroll = useScrollOnce(scroller);
  const deps = useRef<SelectionDeps | null>(null);
  const { selection, select } = useReviewSelection(reads.initial, deps);
  useTargetSelection(reads.initial, select);
  const changeSet = useChangeSet({ repo, master, leaf, frame: reads.frame, chosen, catalogue: navigation.catalogue, selection, walked });
  useLayoutEffect(() => {
    deps.current = {
      navigationSelect: navigation.onSelect,
      workspace,
      setInstead: reads.setInstead,
      setPage: reads.setPage,
      arm: scroll.arm,
      cards: changeSet.cards,
      model: changeSet.model,
      older: changeSet.older,
    };
  });
  // After a selection's read has answered (or failed), the column scrolls once to it (rule 9).
  const pending = reads.read.phase === 'loading';
  const land = scroll.land;
  useLayoutEffect(() => {
    if (!pending) land();
  });
  const outsideLine = useSelectionUrl({
    selection,
    chosen,
    older: changeSet.older,
    facts: { number: changeSet.number, comparison: changeSet.comparison, cards: changeSet.cards, catalogue: navigation.catalogue, lane: changeSet.lane },
    select,
  });
  const lanes = useLaneFacts(changeSet.number);
  const stops = useMemo<TraversalStop[]>(
    () => [...blockStops(changeSet.model), ...laneStops(laneHunks(changeSet.lane, lanes.files), lanePaths(changeSet.lane))],
    [changeSet.model, changeSet.lane, lanes.files],
  );
  // The comparison the page opens by default: the leaf's resolved one, which a landed leaf's
  // delivered pair is (MIK-R195 rule 2). Any other number shows that comparison read-only (W17).
  const resolved = changeSet.resolved;
  // A number names a comparison inside the frame the page was opened in; it is not the same request
  // as "open the delivered pair". The older-comparison control passes the number this frame resolves
  // to, which in a recorded frame is the recorded comparison, so it keeps its own meaning here.
  const chooseComparison = useCallback(
    (number: number | null) => {
      setChosen(number === null || number === resolved ? null : number);
      select(null, { keepTree: false });
    },
    [resolved, select, setChosen],
  );
  // The delivered view is the page's own default, and every control that opens *it* -- the mismatch
  // line and now the menu's delivered row (MIK-R195 F06) -- leaves a recorded frame as well as
  // clearing the chosen number. Clearing only the number left the page reading the recorded
  // comparison the target had asked for.
  const leaveHistory = reads.leaveHistory;
  const chooseDelivered = useCallback(() => {
    leaveHistory();
    chooseComparison(null);
  }, [chooseComparison, leaveHistory]);
  // A tree group row opens that group in the column and brings it into view (rule 5).
  const openWorkspaceGroup = workspace.openGroup;
  const arm = scroll.arm;
  const openGroup = useCallback(
    (subject: SubjectNode, group: TreeGroupName) => {
      const kind = group === 'unchanged' ? 'unchanged' : 'not-comparable';
      openWorkspaceGroup(groupDomId(subject.key, kind));
      arm({ kind: 'group', subjectKey: subject.key, group: kind });
    },
    [openWorkspaceGroup, arm],
  );
  return { chosen, chooseComparison, chooseDelivered, walk, walked, scroller, selection, select, changeSet, outsideLine, lanes, stops, openGroup };
}

export type ReviewPageState = ReturnType<typeof useReviewPage>;
