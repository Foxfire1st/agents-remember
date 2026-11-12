// The change-set buttons shown on a task-document READER: a master gets the series net button, a leaf
// gets committed (always), working (while its enclosure is live) and the Intent review (always — the
// live candidate while the enclosure is live, and the leaf's own recorded comparison once it is
// closed, ICR-R12). Counters come from the changeset data layer; liveness is read from the dashboard
// store, and it selects WHICH record the review entry is addressed to rather than whether it exists.
// The Intent review control is its own component (`intentReviewEntry.tsx`): its counts are the
// comparison's changed intent, not this change set's line totals.
import { Fragment, useEffect, useState } from 'react';

import {
  type ChangeCounters,
  type MasterChangeset,
  type MasterCounters,
  type TaskChangeset,
  loadChangeSet,
  peekChangeSet,
} from '../../data/changeset';
import { isAbortedRead } from '../../data/abortableRead';
import { useMasterCounters } from '../../data/useMasterCounters';
import { MasterCounterStatus } from '../changeset/MasterCounterStatus';
import { useIntentEntryGeneration } from '../../data/intentEntryRevalidation';
import { type ReviewFailure, reviewProblemFromCause } from '../../data/review';
import type { TaskEntry } from '../../types/projection';
import type { ChangeSetTarget } from '../changeset/ChangeSetViewer';
import { EntryStateDetails, briefProblem, problemSentence } from './entryState';
import { IntentReviewEntry } from './intentReviewEntry';
import { dirName } from './model';
import { changeSetBar, changeSetBtn, changeSetCounts } from './styles';

// The net's leaf attribution as one phrase: how many leaves the master carries and how many of them
// have landed, or nothing at all when the read was not a master's (or predates the breakdown) -- an
// absent answer is not rendered as a zero.
function leafAttribution(leaves: MasterChangeset['leaves'] | null): string | null {
  if (!leaves || leaves.length === 0) return null;
  const committed = leaves.filter((leaf) => leaf.state === 'committed').length;
  const working = leaves.length - committed;
  return [
    `${leaves.length} leaf/leaves`,
    working > 0 ? `${committed} committed · ${working} working` : `${committed} committed`,
  ].join(' · ');
}

function LeafAttribution({ leaves }: { leaves: MasterChangeset['leaves'] | null }) {
  const attribution = leafAttribution(leaves);
  if (!attribution) return null;
  return (
    <span className={changeSetCounts} data-testid="changeset-leaf-attribution">
      {attribution}
    </span>
  );
}

// The net bar's total, or nothing when the range is unrecorded: `+0 −0` would present a zero of
// nothing as a measurement (B6). It lives at module level because `ChangeSetButton` sits at the lint
// rail's ceiling for `max-lines-per-function` and this is the extraction that keeps it under it,
// without dropping the state that makes the absence tellable apart.
function changesetTotal(
  counters: { code: ChangeCounters; memory: ChangeCounters } | null,
  unrecorded: string | null,
  unmeasured: string | null,
): string | null {
  // Truthiness, not `=== null`: a response that omits `counters` sets the state to `undefined`, and
  // the original inline expression treated that as "no total" rather than as a value to read.
  if (!counters || unrecorded !== null || unmeasured !== null) return null;
  const insertions = counters.code.insertions + counters.memory.insertions;
  const deletions = counters.code.deletions + counters.memory.deletions;
  return `+${insertions} −${deletions}`;
}

// A master answer that names no generation measured nothing: the selection resolved no base and no
// selected result, so the server's zero counters are the ABSENCE of a net range, not a measured
// empty one. The leaf view draws the same distinction with its own `unrecorded` state; without this
// the bar printed `+0 −0` for a series nothing had measured — a zero of nothing, which is exactly
// the reading this control must never publish (B6), and the one the developer saw frozen on a page
// whose read had answered while the series contract was unreadable.
function masterUnmeasured(data: TaskChangeset | MasterCounters): string | null {
  if (!('leaves' in data)) return null; // a leaf/task view has no generation to name
  if (data.generation) return null; // a measured generation is a real range, even an empty one
  if (data.currentness !== 'unmeasured') return null;
  return (
    'the server named no measurable generation for this series: no base and no selected result ' +
    'resolved, so there is no net range to count'
  );
}

function changeSetRead(data: TaskChangeset | MasterCounters | undefined) {
  if (!data) {
    return { counters: null, unrecorded: null, unmeasured: null, leaves: null, generation: null };
  }
  return {
    counters: data.counters,
    unrecorded: 'state' in data && data.state === 'unrecorded' ? (data.stateDetail ?? '') : null,
    unmeasured: masterUnmeasured(data),
    leaves: 'leaves' in data ? data.leaves : null,
    generation: 'generation' in data ? (data.generation ?? null) : null,
  };
}

interface ControlProps {
  target: ChangeSetTarget;
  label: string;
  onOpen: (target: ChangeSetTarget) => void;
}

export function ChangeSetButton(props: ControlProps) {
  const master = props.target.master;
  return master && !props.target.leaf && props.target.comparison === undefined
    ? <MasterChangeSetButton {...props} master={master} />
    : <TaskChangeSetButton {...props} />;
}

function MasterChangeSetButton({ target, label, onOpen, master }: ControlProps & { master: string }) {
  const generation = useIntentEntryGeneration();
  const read = useMasterCounters({ repo: target.repo, master, pins: target.generation }, generation);
  return <ChangeSetControl target={target} label={label} onOpen={onOpen}
    data={read.data} problem={read.problem} attribution={read.attribution} />;
}

function TaskChangeSetButton({ target, label, onOpen }: ControlProps) {
  const key = JSON.stringify(target);
  const revalidation = useIntentEntryGeneration();
  const [settled, setSettled] = useState<{
    key: string;
    data?: Awaited<ReturnType<typeof loadChangeSet>>;
    problem?: ReviewFailure;
  } | null>(null);
  const data = settled?.key === key ? settled.data : peekChangeSet(target);
  const problem = settled?.key === key ? (settled.problem ?? null) : null;
  useEffect(() => {
    let live = true;
    const controller = new AbortController();
    void loadChangeSet({ repo: target.repo, scope: target.scope, master: target.master,
      leaf: target.leaf, mode: target.mode }, controller.signal).then(
      (next) => live && setSettled({ key, data: next }),
      (cause: unknown) => {
        if (live && !isAbortedRead(cause)) setSettled({ key, problem: reviewProblemFromCause(cause) });
      },
    );
    return () => { live = false; controller.abort(); };
  }, [key, revalidation, target.repo, target.scope, target.master, target.leaf, target.mode]);
  return <ChangeSetControl target={target} label={label} onOpen={onOpen} data={data} problem={problem} />;
}

function ChangeSetControl({ target, label, onOpen, data, problem, attribution }: ControlProps & {
  data?: TaskChangeset | MasterCounters;
  problem: ReviewFailure | null;
  attribution?: import('../../data/useMasterCounters').MasterAttribution;
}) {
  const { counters, unrecorded, unmeasured, leaves, generation } = changeSetRead(data);
  const total = changesetTotal(counters, unrecorded, unmeasured);
  return (
    <Fragment>
      <button type="button" className={changeSetBtn}
        onClick={() => onOpen(generation ? { ...target, generation } : target)} data-testid="open-changeset">
        ⇄ {label}
        {total ? <span className={changeSetCounts}>{total}</span> : null}
        <LeafAttribution leaves={attribution?.leaves ?? leaves} />
        {attribution ? <MasterCounterStatus value={attribution} className={changeSetCounts} /> : null}
        <ChangeSetReadState counters={counters} problem={problem} unrecorded={unrecorded} unmeasured={unmeasured} />
      </button>
      <ChangeSetStateDetails label={label} problem={problem} unrecorded={unrecorded} unmeasured={unmeasured} />
    </Fragment>
  );
}

// The counter read's own state, as a brief word inside the control -- one span, `data-review-state`
// for the state it is in and `data-review-code` for the owner's own code -- because five different
// things have to stay tellable apart and only three of them are states of the change-set itself:
//
//   * `loading`     -- the read is in flight: nothing was measured, so nothing is claimed;
//   * `unrecorded`  -- the read ANSWERED and the mode's own endpoints are not recorded yet (a
//                      `committed` view of a live leaf). It is its own state and NOT `known-empty`:
//                      an unrecorded range was never measured, and the counter total is withheld
//                      (see `total` above) so the control shows a state where a measured range would
//                      show a number. The route's own sentence naming the missing endpoint is in the
//                      disclosure beside the control;
//   * `known-empty` -- the read ANSWERED and the delta is measured empty (no changed file in either
//                      half). A measured zero is a measurement, so the zero is printed with `empty`;
//   * an answer carrying changed files prints no state here at all: the counters ARE the answer;
//   * a refusal, an unreadable answer or no answer at all -- a brief word under the shared token
//                      (`reviewFailureToken`), with the code, the owner's reason, the input it named
//                      and its next action in the disclosure (ICR-R16: brief, never swallowed).
function ChangeSetReadState({
  counters,
  problem,
  unrecorded,
  unmeasured,
}: {
  counters: { code: ChangeCounters; memory: ChangeCounters } | null;
  problem: ReviewFailure | null;
  unrecorded: string | null;
  unmeasured: string | null;
}) {
  if (problem) {
    return (
      <span
        className={changeSetCounts}
        data-testid="changeset-state"
        data-review-state={problem.token}
        data-review-code={problem.code}
      >
        {briefProblem(problem)}
      </span>
    );
  }
  if (unrecorded !== null) {
    return (
      <span
        className={changeSetCounts}
        data-testid="changeset-state"
        data-review-state="unrecorded"
      >
        unrecorded
      </span>
    );
  }
  // A series the server named no generation for was never measured: it is its own state, not a
  // `known-empty` zero, and the total is withheld above so the control cannot print `+0 −0`.
  if (unmeasured !== null) {
    return (
      <span
        className={changeSetCounts}
        data-testid="changeset-state"
        data-review-state="unmeasured"
        title="no base and no selected result resolved: this series has no net range to count"
      >
        unmeasured
      </span>
    );
  }
  if (!counters) {
    return (
      <span className={changeSetCounts} data-testid="changeset-state" data-review-state="loading">
        …
      </span>
    );
  }
  // `files` and not the line totals: a rename or a mode change lists a file with no line delta, so a
  // zero insertion/deletion count is not a zero change-set.
  if (counters.code.files > 0 || counters.memory.files > 0) return null;
  return (
    <span
      className={changeSetCounts}
      data-testid="changeset-state"
      data-review-state="known-empty"
      title="no changed file in either half: this change-set is measured empty"
    >
      empty
    </span>
  );
}

// The explanation behind a refused or unrecorded counter read, one click away beside the control.
function ChangeSetStateDetails({
  label,
  problem,
  unrecorded,
  unmeasured,
}: {
  label: string;
  problem: ReviewFailure | null;
  unrecorded: string | null;
  unmeasured: string | null;
}) {
  if (problem) {
    return (
      <EntryStateDetails testId="changeset-state-details" label={label}>
        This change-set could not be read. {problemSentence(problem)}
      </EntryStateDetails>
    );
  }
  if (unmeasured !== null) {
    return (
      <EntryStateDetails testId="changeset-state-details" label={label}>
        This series change-set has no measured range.{unmeasured ? ` ${unmeasured}` : ''}
      </EntryStateDetails>
    );
  }
  if (unrecorded === null) return null;
  return (
    <EntryStateDetails testId="changeset-state-details" label={label}>
      Nothing has recorded this change-set&apos;s endpoint yet: the range is unrecorded, not
      measured empty.{unrecorded ? ` ${unrecorded}` : ''}
    </EntryStateDetails>
  );
}

// One leaf's own entries: the working change-set (live only) and the Intent review control.
//
// `live` selects WHICH record the review entry is addressed to (ICR-R12) and nothing else: a live
// leaf's review is the candidate it holds now, and a closed leaf's is the comparison its own durable
// generation bound. It is not a gate. A closed leaf keeps the Intent review entry -- its worktree is
// gone and the recorded comparison is the only comparison there is -- while the WORKING change-set
// stays live-gated, since "what is not committed yet" does not exist once the enclosure is closed.
//
// The Intent review is NOT a `ChangeSetButton`: that control reads the committed change set (a second,
// identical committed request beside the committed button) and would show its line totals as if they
// were the review's.
function LeafEntries({
  repo,
  master,
  leaf,
  live,
  facts,
  onOpen,
}: {
  repo: string;
  master: string;
  leaf: string;
  live: boolean;
  facts: string;
  onOpen: (target: ChangeSetTarget) => void;
}) {
  return (
    <>
      {live ? (
        <ChangeSetButton
          target={{ repo, master, leaf, mode: 'working' }}
          label="working"
          onOpen={onOpen}
        />
      ) : null}
      <IntentReviewEntry
        repo={repo}
        master={master}
        leaf={leaf}
        live={live}
        facts={facts}
        onOpen={onOpen}
      />
    </>
  );
}

// The entry supplies the exact task address and its already-bound worktrees.
// Liveness selects the working/recorded review endpoint; it does not decide task status.
export function DocChangeSetBar({
  kind,
  entry,
  onOpen,
}: {
  kind: 'master' | 'leaf';
  entry: TaskEntry;
  onOpen?: (target: ChangeSetTarget) => void;
}) {
  const repo = entry.reference?.repository;
  const master = entry.reference ? dirName(entry.reference.path) : undefined;
  const leaf = kind === 'leaf' ? entry.id : undefined;
  const live = entry.worktrees.some((node) => node.codeWorktreeExists || node.memoryWorktreeExists);
  // The Intent review summary's invalidation signal (ICR-R17): THIS leaf's own lifecycle facts -- its
  // liveness and its enclosure's closeout/integration/cleanup state, which decide which comparison
  // the entry opens -- plus the entry's re-validation generation, which moves when the task detail is
  // shown again or the reviewer refreshes (`data/intentEntryRevalidation.tsx`; no dashboard signal
  // carries a knowledge write). It is deliberately not the serialized global analytics document,
  // which moves on every workspace publication and made the entry re-read on unrelated tasks.
  const generation = useIntentEntryGeneration();
  const facts = leaf ? `${leafFacts(entry, live)}#${generation}` : '';
  if (!onOpen || !repo || !master) return null;
  if (kind === 'master') {
    return (
      <div className={changeSetBar}>
        <ChangeSetButton target={{ repo, master }} label="series" onOpen={onOpen} />
      </div>
    );
  }
  if (!leaf) return null;
  return (
    <div className={changeSetBar}>
      <ChangeSetButton
        target={{ repo, master, leaf, mode: 'committed' }}
        label="committed"
        onOpen={onOpen}
      />
      <LeafEntries
        repo={repo}
        master={master}
        leaf={leaf}
        live={live}
        facts={facts}
        onOpen={onOpen}
      />
    </div>
  );
}

// These worktrees were bound by the server to this exact entry. All of their
// facts participate so a later contract cannot silently reuse an older summary.
function leafFacts(entry: TaskEntry, live: boolean): string {
  return JSON.stringify([
    live,
    entry.worktrees.map((node) => [
      node.enclosure,
      node.closeoutStatus,
      node.integrationStatus,
      node.cleanup,
      node.codeWorktreeExists,
      node.memoryWorktreeExists,
    ]),
  ]);
}
