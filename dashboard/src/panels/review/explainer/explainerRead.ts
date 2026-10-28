/**
 * The Explanation view's reads (MIK-R106 v2 rule 6; MIK-R103 v2; MIK-R186 rule 6).
 *
 * Opening the view reads the saved state and the retained revisions. That is all it does: no build,
 * no model, no agent, no resume (MIK-R106 v2 rule 10). The two presses -- Update and Remove old
 * revisions -- are returned as functions the developer's own click calls, and each re-reads after it
 * so the view never shows a page or a list the host no longer holds.
 *
 * An answer that arrives for an unmounted view is dropped; a read that failed is a named problem,
 * never an empty page.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import {
  subscribeReviewExplainerMoved,
  subscribeReviewPublication,
  type ReviewExplainerMovedEvent,
} from '../../../data/reviewMemoryWatch';
import type { ReviewEntry } from '../../../data/review';
import type { ReviewTreeComparison, ReviewTreeEntry } from '../../../data/reviewTrees';
import {
  blockIdentityOf,
  matchesBlockReference,
  parseSelection,
  type HunkIdentity,
} from '../blockIdentity';
import type { EvidenceDestination } from '../canvas/canvasApi';
import { destinationLine } from '../canvas/canvasModel';
import type { ChangeSetCards, ChangeSetModel } from '../changeSetOrder';
import type { ReviewSelection } from '../reviewSelection';
import {
  ExplainerRefusal,
  readExplainerRevisions,
  readExplainerStale,
  readExplainerState,
  readPlannedReview,
  refusalInfo,
  regenerateExplainerStale,
  removeExplainerRevisions,
  updateExplainer,
  type BindingOwner,
  type ClaimLink,
  type ExplainerRevisions,
  type ExplainerStaleBadge,
  type ExplainerState,
  type ExplainerStateQuery,
  type ExplainerTarget,
  type ExplainerMoved,
  type ExplainerUpdate,
  type RefusalInfo,
} from './explainerApi';
import type { PlannedPage } from './planned/plannedPage';

export type UpdateOutcome =
  | { kind: 'updated'; counts?: ExplainerUpdate['counts'] }
  | { kind: 'refused'; reason: string; detail: string };

export type RegenerateOutcome =
  | { kind: 'regenerated'; attempt: string | null }
  | { kind: 'refused'; reason: string; detail: string };

export type ExplainerComparisonInput =
  | string
  | {
      reference?: string | null;
      number?: number | null;
      owner?: BindingOwner | string | null;
    }
  | undefined;

export interface ExplainerRead {
  state: ExplainerState | null;
  revisions: ExplainerRevisions | null;
  /** The read itself failed: the view says so and offers Retry. */
  problem: RefusalInfo | null;
  phase: 'reading' | 'idle';
  /** The last press's refusal: the task total's, or the host's other names. */
  refusal: RefusalInfo | null;
  busy: 'idle' | 'updating' | 'removing' | 'regenerating';
  /** The developer's Update press: what it left, or the refusal it answered with. */
  update: () => Promise<UpdateOutcome>;
  /** The developer's selective regeneration press for a live seat (MIK-R187 rule 8). */
  regenerate: (input: {
    message: string;
    stalePartIds: string[];
    staleGrades: string[];
  }) => Promise<RegenerateOutcome>;
  remove: (revisions: number[]) => Promise<void>;
  refresh: () => Promise<void>;
  dismissRefusal: () => void;
}

/** Whether an answer still belongs to the view on screen. */
function isCurrent(live: { current: boolean }, token: number, latest: number): boolean {
  return live.current && token === latest;
}

/** The problem a state answer carries, or null for a good one. */
function problemOfState(state: ExplainerState): RefusalInfo | null {
  if (state.state === 'ok') return null;
  return { reason: state.reason ?? 'unreadable-answer', detail: state.detail ?? '' };
}

function problemOfError(error: unknown): RefusalInfo {
  return refusalInfo(error) ?? { reason: 'read-failed', detail: String(error) };
}

function normalizeComparison(input: ExplainerComparisonInput): {
  comparison: string | undefined;
  comparisonNumber: number | undefined;
  comparisonOwner: BindingOwner | string | undefined;
} {
  if (!input) {
    return { comparison: undefined, comparisonNumber: undefined, comparisonOwner: undefined };
  }
  if (typeof input === 'string') {
    return { comparison: input, comparisonNumber: undefined, comparisonOwner: undefined };
  }
  return {
    comparison: input.reference ?? undefined,
    comparisonNumber: input.number ?? undefined,
    comparisonOwner: input.owner ?? undefined,
  };
}

function isClearedMoveEvent(event: ReviewExplainerMovedEvent): boolean {
  if (event.cleared === true || event.kind === 'cleared') return true;
  return event.moved === null && event.from === undefined && event.commitFrom === undefined;
}

function numOrNull(value: unknown): number | null {
  return typeof value === 'number' ? value : null;
}

/** The newer comparison a stream event names, for the top line's badge (F18); null once cleared. */
function movedFromEvent(event: ReviewExplainerMovedEvent): ExplainerMoved | null {
  if (isClearedMoveEvent(event)) return null;
  const result: ExplainerMoved = {
    from: numOrNull(event.from),
    to: numOrNull(event.to),
    blocks: numOrNull(event.blocksChanged) ?? numOrNull(event.blocks) ?? 0,
  };
  if (typeof event.commitFrom === 'string') result.commitFrom = event.commitFrom;
  if (typeof event.commitTo === 'string') result.commitTo = event.commitTo;
  if (typeof event.text === 'string') result.text = event.text;
  return result;
}

function applyMovedToState(
  previous: ExplainerState | null,
  nextMoved: ExplainerMoved | null,
): ExplainerState | null {
  if (!previous) {
    if (!nextMoved) return previous;
    return {
      state: 'ok',
      binding: null,
      seat: { state: 'none' },
      retired: false,
      retiredAt: null,
      explanation: null,
      building: null,
      stale: { parts: [] },
      parts: [],
      claims: [],
      moved: nextMoved,
      limit: null,
    };
  }
  return { ...previous, moved: nextMoved };
}

// The comparison a read asks about. The host reads a numbered comparison by its number and owner
// alone (`_query_int(request, "comparisonNumber", "comparison")`), so once the number is known the
// reference is left off the read: the page learns the same comparison's reference later (from the
// subject's payload), and that must not make it ask the same answer a second time (D569).
function readComparison(
  comparison: string | undefined,
  comparisonNumber: number | undefined,
): string | undefined {
  return comparisonNumber === undefined ? comparison : undefined;
}

function buildStateQuery(query: ExplainerStateQuery): ExplainerStateQuery | undefined {
  const { revision, comparison, comparisonNumber, comparisonOwner } = query;
  const hasAny =
    revision !== undefined ||
    comparison !== undefined ||
    comparisonNumber !== undefined ||
    comparisonOwner !== undefined;
  return hasAny ? { revision, comparison, comparisonNumber, comparisonOwner } : undefined;
}

function joinedExplainerRead(perform: (signal: AbortSignal) => Promise<void>) {
  let inFlight: Promise<void> | null = null;
  let again = false;
  let active = true;
  let controller: AbortController | null = null;
  const run = (): Promise<void> => {
    if (inFlight) { again = true; return inFlight; }
    inFlight = (async () => {
      do {
        again = false;
        const aborter = new AbortController();
        controller = aborter;
        try { await perform(aborter.signal); }
        finally {
          aborter.abort();
          if (controller === aborter) controller = null;
        }
      } while (again && active);
    })().finally(() => { inFlight = null; });
    return inFlight;
  };
  return Object.assign(run, {
    activate: () => { active = true; },
    cancel: () => { active = false; again = false; controller?.abort(); },
  });
}

/** Joined reads abort on unmount or selection change; publication earns one trailing read. */
function useExplainerAnswer(
  target: ExplainerTarget,
  query: ExplainerStateQuery,
  enabled: boolean,
  watchWhileDisabled: boolean,
) {
  const { repository, master, leaf } = target;
  const { revision, comparisonNumber, comparisonOwner } = query;
  const comparison = readComparison(query.comparison ?? undefined, comparisonNumber ?? undefined);
  const [state, setState] = useState<ExplainerState | null>(null);
  const [revisions, setRevisions] = useState<ExplainerRevisions | null>(null);
  const [problem, setProblem] = useState<RefusalInfo | null>(null);
  const [phase, setPhase] = useState<'reading' | 'idle'>('reading');
  const live = useRef(true);
  const sequence = useRef(0);
  // F17: a publication of this leaf's explainer is worth one read even when the column's own read is
  // disabled. Measured before the fix: the review page's six /api/stream connections between 16:41Z
  // and 17:17Z carried no `publication` parameter at all, so the server never opened its publication
  // watcher and `review.explainer.changed` was never sent -- a new revision could not be seen without
  // a manual reload, while the backend process (pid 2649395) never restarted.
  const forced = useRef(false);

  const read = useMemo(() => joinedExplainerRead(async (signal) => {
    if ((!enabled && !forced.current) || signal.aborted) return;
    const token = (sequence.current += 1);
    const current = () => !signal.aborted && isCurrent(live, token, sequence.current);
    setPhase('reading');
    const stateQuery = buildStateQuery({
      revision,
      comparison,
      comparisonNumber,
      comparisonOwner,
    });
    try {
      const [nextState, nextRevisions] = await Promise.all([
        readExplainerState({ repository, master, leaf }, stateQuery, signal),
        readExplainerRevisions({ repository, master, leaf }, signal),
      ]);
      if (!current()) return;
      setState(nextState);
      setRevisions(nextRevisions);
      setProblem(problemOfState(nextState));
    } catch (error) {
      if (!current()) return;
      setProblem(problemOfError(error));
    } finally {
      forced.current = false;
      if (current()) setPhase('idle');
    }
  }), [repository, master, leaf, revision, comparison, comparisonNumber, comparisonOwner, enabled]);

  useEffect(() => {
    live.current = true;
    read.activate();
    void read();
    return () => {
      live.current = false;
      read.cancel();
      sequence.current += 1;
    };
  }, [read]);

  // The subscriptions hold the leaf only and call the latest read: re-subscribing whenever the read's
  // inputs change would move the stream's review scope, and every move reopens the stream.
  const latestRead = useRef(read);
  latestRead.current = read;
  const watching = useRef(enabled || watchWhileDisabled);
  watching.current = enabled || watchWhileDisabled;

  useEffect(() => {
    if (!enabled) return undefined;
    return subscribeReviewExplainerMoved({ repo: repository, master, leaf }, (event) => {
      setState((previous) => applyMovedToState(previous, movedFromEvent(event)));
      if (event.cleared) void latestRead.current();
    });
  }, [repository, master, leaf, enabled]);

  useEffect(() => {
    // F17: the subscription is never gated on `enabled`. A mounted review surface watches this
    // leaf's publications even while its own read is off; the callback forces one read, which is what
    // makes a newly published revision appear on an open page without a reload. A reader whose
    // publication is followed by another read on the page (the Review tab's badge, a view handed the
    // surface's read) opts out of the forced read: subscribed, so the stream carries the leaf, but
    // asking nothing until its own read is enabled.
    return subscribeReviewPublication({ repo: repository, master, leaf },
      () => { if (!watching.current) return; forced.current = true; void latestRead.current(); },
      (detail) => { setProblem({ reason: 'publication-watch-failed', detail }); },
    );
  }, [repository, master, leaf]);

  const clearMoved = useCallback(() => {
    setState((previous) => (previous ? { ...previous, moved: null } : previous));
  }, []);

  return { state, revisions, problem, phase, read, clearMoved };
}

function useRegenerateAction(
  target: ExplainerTarget,
  comparison: string | undefined,
  read: () => Promise<void>,
  live: { current: boolean },
  setBusy: (b: 'idle' | 'updating' | 'removing' | 'regenerating') => void,
  setRefusal: (r: RefusalInfo | null) => void,
) {
  const { repository, master, leaf } = target;
  const regenRequest = useRef<{ key: string; id: string }>({ key: '', id: '' });
  return useCallback(
    async (input: {
      message: string;
      stalePartIds: string[];
      staleGrades: string[];
    }): Promise<RegenerateOutcome> => {
      setBusy('regenerating');
      setRefusal(null);
      const key = `${repository}/${master}/${leaf}:${input.stalePartIds.join(',')}:${input.staleGrades.join(',')}:${input.message}`;
      if (regenRequest.current.key !== key) {
        regenRequest.current = { key, id: crypto.randomUUID() };
      }
      const requestId = regenRequest.current.id;
      try {
        const answer = await regenerateExplainerStale(
          { repository, master, leaf },
          {
            requestId,
            message: input.message,
            stalePartIds: input.stalePartIds,
            staleGrades: input.staleGrades,
            comparison,
          },
        );
        regenRequest.current = { key: '', id: '' };
        if (live.current) await read();
        return { kind: 'regenerated', attempt: answer.attempt };
      } catch (error) {
        const named = refusalInfo(error) ?? { reason: 'regenerate-failed', detail: String(error) };
        if (live.current) setRefusal(named);
        return { kind: 'refused', ...named };
      } finally {
        if (live.current) setBusy('idle');
      }
    },
    [repository, master, leaf, comparison, read, live, setBusy, setRefusal],
  );
}

/** The presses, and the refusal the last one answered with. */
function useExplainerPresses(
  target: ExplainerTarget,
  comparison: string | undefined,
  read: () => Promise<void>,
  clearMoved: () => void,
  enabled: boolean,
) {
  const { repository, master, leaf } = target;
  const [refusal, setRefusal] = useState<RefusalInfo | null>(null);
  const [busy, setBusy] = useState<'idle' | 'updating' | 'removing' | 'regenerating'>('idle');
  const live = useRef(true);
  useEffect(
    () => () => {
      live.current = false;
    },
    [],
  );

  const update = useCallback(async (): Promise<UpdateOutcome> => {
    setBusy('updating');
    setRefusal(null);
    let outcome: UpdateOutcome = {
      kind: 'refused',
      reason: 'unreachable',
      detail: 'the Update answered nothing',
    };
    try {
      const answer = await updateExplainer({ repository, master, leaf }, comparison);
      clearMoved();
      outcome = { kind: 'updated', ...(answer.counts ? { counts: answer.counts } : {}) };
    } catch (error) {
      const named = refusalInfo(error) ?? { reason: 'update-failed', detail: String(error) };
      setRefusal(named);
      outcome = { kind: 'refused', ...named };
      if (!(error instanceof ExplainerRefusal)) throw error;
    } finally {
      if (live.current) {
        setBusy('idle');
        await read();
      }
    }
    return outcome;
  }, [repository, master, leaf, comparison, read, clearMoved]);

  const regenerate = useRegenerateAction(
    target,
    comparison,
    read,
    live,
    setBusy,
    setRefusal,
  );

  const remove = useCallback(
    async (numbers: number[]) => {
      if (!numbers.length) return;
      setBusy('removing');
      try {
        await removeExplainerRevisions({ repository, master, leaf }, numbers);
        setRefusal(null);
      } catch (error) {
        if (live.current) {
          setRefusal(refusalInfo(error) ?? { reason: 'remove-failed', detail: String(error) });
        }
        if (!(error instanceof ExplainerRefusal)) throw error;
      } finally {
        if (live.current) {
          setBusy('idle');
          await read();
        }
      }
    },
    [repository, master, leaf, read],
  );

  const dismissRefusal = useCallback(() => setRefusal(null), []);
  if (!enabled)
    return { refusal, busy: 'idle' as const, update, regenerate, remove, dismissRefusal };
  return { refusal, busy, update, regenerate, remove, dismissRefusal };
}

export function useExplainer(
  target: ExplainerTarget,
  comparison?: ExplainerComparisonInput,
  enabled = true,
  revision?: number | null,
  options: { watchWhileDisabled?: boolean } = {},
): ExplainerRead {
  const normalized = normalizeComparison(comparison);
  const answer = useExplainerAnswer(
    target,
    {
      revision: revision ?? undefined,
      comparison: normalized.comparison,
      comparisonNumber: normalized.comparisonNumber,
      comparisonOwner: normalized.comparisonOwner,
    },
    enabled,
    options.watchWhileDisabled ?? true,
  );
  const presses = useExplainerPresses(
    target,
    normalized.comparison,
    answer.read,
    answer.clearMoved,
    enabled,
  );
  return {
    state: answer.state,
    revisions: answer.revisions,
    problem: answer.problem,
    phase: answer.phase,
    refresh: answer.read,
    ...presses,
  };
}

export interface ExplainerBadgeRead {
  /** The last `/stale` answer for the comparison on screen; null before the first one. */
  badge: ExplainerStaleBadge | null;
  /** The read itself failed: the badge claims nothing. */
  problem: RefusalInfo | null;
  /** Whether a `/stale` read has answered or failed yet; a notice applied before it is no answer. */
  settled: boolean;
  refresh: () => Promise<void>;
}

function movedIntoBadge(
  previous: ExplainerStaleBadge | null,
  moved: ExplainerMoved | null,
): ExplainerStaleBadge | null {
  if (previous) return { ...previous, moved };
  return moved
    ? { state: 'ok', stale: null, explanation: null, moved, building: null, retired: false }
    : previous;
}

/**
 * The top bar's badge on the Review tab (F18): one `/stale` read for the comparison on screen,
 * renewed by this leaf's publication (F17) and a cleared notice, with a moved notice applied as it
 * arrives. It reads no seat, no part map and no revision list: those are the Explanation tab's, and
 * are read when that tab opens.
 */
export function useExplainerBadge(
  target: ExplainerTarget,
  comparison: ExplainerComparisonInput,
  enabled: boolean,
): ExplainerBadgeRead {
  const { repository, master, leaf } = target;
  const normalized = normalizeComparison(comparison);
  const { comparisonNumber, comparisonOwner } = normalized;
  const reference = readComparison(normalized.comparison, comparisonNumber);
  const [badge, setBadge] = useState<ExplainerStaleBadge | null>(null);
  const [problem, setProblem] = useState<RefusalInfo | null>(null);
  const [settled, setSettled] = useState(false);
  const live = useRef(true);
  const sequence = useRef(0);

  const read = useMemo(
    () =>
      joinedExplainerRead(async (signal) => {
        if (!enabled || signal.aborted) return;
        const token = (sequence.current += 1);
        const current = () => !signal.aborted && isCurrent(live, token, sequence.current);
        const query = buildStateQuery({ comparison: reference, comparisonNumber, comparisonOwner });
        try {
          const next = await readExplainerStale({ repository, master, leaf }, query, signal);
          if (!current()) return;
          setBadge(next);
          setProblem(null);
          setSettled(true);
        } catch (error) {
          if (!current()) return;
          setProblem(problemOfError(error));
          setSettled(true);
        }
      }),
    [repository, master, leaf, reference, comparisonNumber, comparisonOwner, enabled],
  );

  useEffect(() => {
    live.current = true;
    read.activate();
    void read();
    return () => {
      live.current = false;
      read.cancel();
      sequence.current += 1;
    };
  }, [read]);

  const latestRead = useRef(read);
  latestRead.current = read;

  useEffect(() => {
    if (!enabled) return undefined;
    return subscribeReviewExplainerMoved({ repo: repository, master, leaf }, (event) => {
      setBadge((previous) => movedIntoBadge(previous, movedFromEvent(event)));
      if (event.cleared) void latestRead.current();
    });
  }, [repository, master, leaf, enabled]);

  useEffect(() => {
    if (!enabled) return undefined;
    return subscribeReviewPublication(
      { repo: repository, master, leaf },
      () => {
        void latestRead.current();
      },
      (detail) => {
        setProblem({ reason: 'publication-watch-failed', detail });
      },
    );
  }, [repository, master, leaf, enabled]);

  return { badge, problem, settled, refresh: read };
}

/** The refusal a view shows: the host's own `limit` on the saved state, else the last press's. */
export function limitRefusal(state: ExplainerState | null): RefusalInfo | null {
  const limit = state?.limit;
  if (!limit || limit.state !== 'refused') return null;
  return { reason: limit.reason ?? 'refused', detail: limit.detail ?? '' };
}

/** Reads the planned-mode review page when the leaf is in `planning` mode (MIK-R189 rule 5). */
export function usePlannedReview(target: ExplainerTarget, enabled = true): PlannedPage | null {
  const { repository, master, leaf } = target;
  const [planned, setPlanned] = useState<PlannedPage | null>(null);
  useEffect(() => {
    if (!enabled) {
      setPlanned(null);
      return undefined;
    }
    let live = true;
    void readPlannedReview({ repository, master, leaf }).then((page) => {
      if (live) setPlanned(page);
    });
    return () => {
      live = false;
    };
  }, [repository, master, leaf, enabled]);
  return planned;
}

export interface ClaimResolutionContext {
  model: ChangeSetModel;
  cards: ChangeSetCards;
  comparison: ReviewTreeComparison | null;
  comparisonNumber: number | undefined;
  comparisonReference?: string | null;
  laneHunks?: readonly HunkIdentity[];
  catalogue?: readonly ReviewEntry[];
}

function sameComparisonClaim(
  claimed: string | undefined,
  context: ClaimResolutionContext,
): boolean {
  if (!claimed) return true;
  if (context.comparisonReference && claimed === context.comparisonReference) return true;
  const match = /\/(\d+)$/.exec(claimed);
  if (match && context.comparisonNumber !== undefined) {
    return Number(match[1]) === context.comparisonNumber;
  }
  return !context.comparisonReference;
}

function findLocationSelection(
  blockRef: string,
  context: ClaimResolutionContext,
): ReviewSelection | null {
  for (const subject of context.model.subjects) {
    for (const loc of [...subject.changed, ...subject.unchanged, ...subject.notComparable]) {
      const entryIds = loc.card.entries.map((entry) => entry.id);
      const drawnKeys = loc.draws.length ? loc.draws : [loc.key];
      if (matchesBlockReference(loc.identity, drawnKeys, entryIds, blockRef)) {
        return { kind: 'block', block: loc.identity };
      }
    }
  }
  if (context.cards.state === 'ready') {
    const found: ReviewTreeEntry | undefined = context.cards.entries.find(
      (entry) => entry.id === blockRef,
    );
    if (found) {
      return { kind: 'block', block: blockIdentityOf(found, context.cards.comparison) };
    }
  }
  return null;
}

function findHunkOrEncodedSelection(
  blockRef: string,
  context: ClaimResolutionContext,
): ReviewSelection | null {
  if (blockRef.startsWith('b:') || blockRef.startsWith('h:') || blockRef.startsWith('s:')) {
    const parsed = parseSelection(blockRef);
    if (parsed) return parsed;
  }
  const hunkMatch = context.laneHunks?.find((h) => matchesBlockReference(h, [], [], blockRef));
  if (hunkMatch) return { kind: 'block', block: hunkMatch };
  if (blockRef.startsWith('hunk:')) {
    const pathPart = blockRef.slice('hunk:'.length).split('@')[0];
    if (pathPart) return { kind: 'lane', path: pathPart };
  }
  return null;
}

function findSubjectSelection(
  blockRef: string,
  context: ClaimResolutionContext,
): ReviewSelection | null {
  const subject = context.model.subjects.find(
    (s) => s.row.id === blockRef || s.row.selectorId === blockRef,
  );
  if (subject) {
    return { kind: 'subject', subject: { kind: 'invariant', id: subject.row.selectorId } };
  }
  const group = context.model.groups.find(
    (g) => g.family && (g.family.row.id === blockRef || g.family.row.selectorId === blockRef),
  );
  if (group?.family) {
    return { kind: 'subject', subject: { kind: 'family', id: group.family.row.selectorId } };
  }
  const catEntry = context.catalogue?.find(
    (entry) => entry.label === blockRef || entry.selector_id === blockRef,
  );
  if (catEntry) {
    return { kind: 'subject', subject: { kind: catEntry.selector_kind, id: catEntry.selector_id } };
  }
  return null;
}

/**
 * Resolves a claim link or block reference to a native `ReviewSelection` on the shown comparison
 * (MIK-R106 v2 rule 6; MIK-R89 v2 rule 9).
 */
export function resolveClaimSelection(
  claim: ClaimLink,
  context: ClaimResolutionContext,
): ReviewSelection | null {
  if (!sameComparisonClaim(claim.comparison, context) || !claim.block) return null;
  const blockRef = claim.block;
  return (
    findLocationSelection(blockRef, context) ??
    findHunkOrEncodedSelection(blockRef, context) ??
    findSubjectSelection(blockRef, context)
  );
}

function locationEntryId(location: Record<string, unknown> | undefined): string | null {
  if (!location) return null;
  if (typeof location.entry === 'string') return location.entry;
  if (typeof location.record_id === 'string') return location.record_id;
  return null;
}

/**
 * Resolves an `open-evidence` destination from a frame to a native `ReviewSelection`.
 */
export function resolveEvidenceSelection(
  destination: EvidenceDestination,
  target: ExplainerTarget,
  context: ClaimResolutionContext,
): ReviewSelection | null {
  if (destination.state !== 'opened' || !sameComparisonClaim(destination.comparison, context)) {
    return null;
  }
  const candidates = [destination.reference, locationEntryId(destination.location)].filter(
    (val): val is string => Boolean(val),
  );
  for (const ref of candidates) {
    const resolved = resolveClaimSelection(
      { block: ref, label: ref, comparison: destination.comparison },
      context,
    );
    if (resolved) return resolved;
  }
  if (!context.comparisonReference) return null;
  const opened = destinationLine(destination, {
    repository: target.repository,
    master: target.master,
    leaf: target.leaf,
    comparison: { reference: context.comparisonReference, number: context.comparisonNumber },
  });
  return opened.selector ? { kind: 'subject', subject: opened.selector } : null;
}
