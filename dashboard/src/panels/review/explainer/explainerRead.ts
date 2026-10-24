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

import { useCallback, useEffect, useRef, useState } from 'react';

import {
  subscribeReviewExplainerMoved,
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
  readExplainerState,
  readPlannedReview,
  refusalInfo,
  regenerateExplainerStale,
  removeExplainerRevisions,
  updateExplainer,
  type BindingOwner,
  type ClaimLink,
  type ExplainerRevisions,
  type ExplainerState,
  type ExplainerStateQuery,
  type ExplainerTarget,
  type ExplainerUpdate,
  type MovedNoticeData,
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

function attachMovedStrings(result: MovedNoticeData, event: ReviewExplainerMovedEvent) {
  if (event.kind === 'master-moved' || event.kind === 'comparison-moved') result.kind = event.kind;
  if (typeof event.commitFrom === 'string') result.commitFrom = event.commitFrom;
  if (typeof event.commitTo === 'string') result.commitTo = event.commitTo;
  if (typeof event.text === 'string') result.text = event.text;
}

function movedFromEvent(event: ReviewExplainerMovedEvent): MovedNoticeData | null {
  if (isClearedMoveEvent(event)) return null;
  const blocksChanged = numOrNull(event.blocksChanged) ?? undefined;
  const blocks = blocksChanged ?? numOrNull(event.blocks) ?? 0;
  const result: MovedNoticeData = {
    from: numOrNull(event.from),
    to: numOrNull(event.to),
    blocks,
  };
  if (blocksChanged !== undefined) result.blocksChanged = blocksChanged;
  if (typeof event.updateOffered === 'boolean') result.updateOffered = event.updateOffered;
  attachMovedStrings(result, event);
  return result;
}

function applyMovedToState(
  previous: ExplainerState | null,
  nextMoved: MovedNoticeData | null,
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

function buildStateQuery(query: ExplainerStateQuery): ExplainerStateQuery | undefined {
  const { revision, comparison, comparisonNumber, comparisonOwner } = query;
  const hasAny =
    revision !== undefined ||
    comparison !== undefined ||
    comparisonNumber !== undefined ||
    comparisonOwner !== undefined;
  return hasAny ? { revision, comparison, comparisonNumber, comparisonOwner } : undefined;
}

/** The two reads, dropped if the view unmounts or a newer read starts. */
function useExplainerAnswer(
  target: ExplainerTarget,
  query: ExplainerStateQuery,
  enabled: boolean,
) {
  const { repository, master, leaf } = target;
  const { revision, comparison, comparisonNumber, comparisonOwner } = query;
  const [state, setState] = useState<ExplainerState | null>(null);
  const [revisions, setRevisions] = useState<ExplainerRevisions | null>(null);
  const [problem, setProblem] = useState<RefusalInfo | null>(null);
  const [phase, setPhase] = useState<'reading' | 'idle'>('reading');
  const live = useRef(true);
  const sequence = useRef(0);

  const read = useCallback(async () => {
    if (!enabled) return;
    const token = (sequence.current += 1);
    setPhase('reading');
    const stateQuery = buildStateQuery({
      revision,
      comparison,
      comparisonNumber,
      comparisonOwner,
    });
    try {
      const [nextState, nextRevisions] = await Promise.all([
        readExplainerState({ repository, master, leaf }, stateQuery),
        readExplainerRevisions({ repository, master, leaf }),
      ]);
      if (!isCurrent(live, token, sequence.current)) return;
      setState(nextState);
      setRevisions(nextRevisions);
      setProblem(problemOfState(nextState));
    } catch (error) {
      if (!isCurrent(live, token, sequence.current)) return;
      setProblem(problemOfError(error));
    } finally {
      if (isCurrent(live, token, sequence.current)) setPhase('idle');
    }
  }, [repository, master, leaf, revision, comparison, comparisonNumber, comparisonOwner, enabled]);

  useEffect(() => {
    live.current = true;
    void read();
    return () => {
      live.current = false;
      sequence.current += 1;
    };
  }, [read]);

  useEffect(() => {
    if (!enabled) return undefined;
    return subscribeReviewExplainerMoved({ repo: repository, master, leaf }, (event) => {
      setState((previous) => applyMovedToState(previous, movedFromEvent(event)));
    });
  }, [repository, master, leaf, enabled]);

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
