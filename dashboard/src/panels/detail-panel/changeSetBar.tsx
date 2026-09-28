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
  type MasterNetPins,
  leafChangeset,
  masterChangeset,
  taskChangeset,
} from '../../data/changeset';
import { useIntentEntryGeneration } from '../../data/intentEntryRevalidation';
import { type ReviewFailure, reviewProblemFromCause } from '../../data/review';
import { useDashboard } from '../../data/store';
import type { EnclosureNode } from '../../types/projection';
import type { ChangeSetTarget } from '../changeset/ChangeSetViewer';
import { EntryStateDetails, briefProblem, problemSentence } from './entryState';
import { IntentReviewEntry } from './intentReviewEntry';
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
): string | null {
  // Truthiness, not `=== null`: a response that omits `counters` sets the state to `undefined`, and
  // the original inline expression treated that as "no total" rather than as a value to read.
  if (!counters || unrecorded !== null) return null;
  const insertions = counters.code.insertions + counters.memory.insertions;
  const deletions = counters.code.deletions + counters.memory.deletions;
  return `+${insertions} −${deletions}`;
}

export function ChangeSetButton({
  target,
  label,
  onOpen,
}: {
  target: ChangeSetTarget;
  label: string;
  onOpen: (target: ChangeSetTarget) => void;
}) {
  const [counters, setCounters] = useState<{ code: ChangeCounters; memory: ChangeCounters } | null>(
    null,
  );
  // The generation the fetched net published, when it names one (only the master net does). The
  // entry opens the viewer bound to it, so the view -- and each file expansion inside it -- reads
  // the listed generation rather than re-resolving the live tip.
  const [generation, setGeneration] = useState<MasterNetPins | null>(null);
  // WHY THIS IS NOT "EMPTY" (B6). A `committed` read of a live leaf has no landed commit to read
  // yet. The route answers that state in the body (`state: "unrecorded"` plus its own sentence)
  // rather than as a 404, because a 404 for a state every live leaf passes through is a console
  // error on the page whose accepted criterion is zero console errors. It is carried here so the
  // control says WHICH absence this is: an unrecorded range is not a range measured empty, and the
  // counters beside it are a zero of nothing.
  const [unrecorded, setUnrecorded] = useState<string | null>(null);
  // The net's own leaves, when this read is a master's: how many the master carries and how many of
  // them have landed, which is what makes the total beside it attributable at a glance.
  const [leaves, setLeaves] = useState<MasterChangeset['leaves'] | null>(null);
  // WHAT THE READ SAID WHEN IT DID NOT ANSWER (L32/D01). The route publishes a refusal's own code
  // and its reason in the body of its non-2xx response, and the rejection below used to take no error
  // parameter at all: the refusal was in hand and discarded, so a REFUSED read rendered
  // byte-identically to one that had NOT ANSWERED -- `⇄ committed` either way, naming neither the
  // code nor the reason. It is carried here in the same `ReviewFailure` shape the catalogue read
  // beside it uses, and rendered by `ChangeSetReadState` below.
  const [problem, setProblem] = useState<ReviewFailure | null>(null);
  useEffect(() => {
    let live = true;
    setCounters(null);
    setGeneration(null);
    setLeaves(null);
    setProblem(null);
    setUnrecorded(null);
    const req = target.leaf
      ? leafChangeset(target.repo, target.master ?? '', target.leaf, target.mode ?? 'committed')
      : target.master
        ? // The master read asks for its per-leaf attribution (R33.2) -- the route answers one row
          // per leaf, and this control is where the reviewer sees that the net total IS those leaves
          // summed rather than a single number with no owner.
          masterChangeset(target.repo, target.master, { includeLeaves: true })
        : taskChangeset(target.repo, target.scope ?? '');
    void req.then(
      (d) => {
        if (!live) return;
        setCounters(d.counters);
        setUnrecorded('state' in d && d.state === 'unrecorded' ? (d.stateDetail ?? '') : null);
        setLeaves('leaves' in d ? (d.leaves ?? []) : null);
        setGeneration(
          'generation' in d && d.generation
            ? {
                codeBase: d.generation.codeBase,
                codeTip: d.generation.codeTip,
                memoryBase: d.generation.memoryBase,
                memoryTip: d.generation.memoryTip,
              }
            : null,
        );
      },
      (cause: unknown) => {
        // A refusal is an ANSWER and is filed as one. `live` still guards the write -- a read whose
        // props moved, or whose control unmounted, must not publish into state -- but it is no longer
        // what discards the refusal: while this is still the control on screen, the refusal it earned
        // is what it shows.
        if (!live) return;
        setCounters(null);
        setGeneration(null);
        setLeaves(null);
        setUnrecorded(null);
        setProblem(reviewProblemFromCause(cause));
      },
    );
    return () => {
      live = false;
    };
  }, [target.repo, target.scope, target.master, target.leaf, target.mode]);
  // An unrecorded range prints no total: `+0 −0` would present a zero of nothing as a measurement.
  const total = changesetTotal(counters, unrecorded);
  return (
    <Fragment>
      <button
        type="button"
        className={changeSetBtn}
        onClick={() => onOpen(generation ? { ...target, generation } : target)}
        data-testid="open-changeset"
      >
        ⇄ {label}
        {total ? <span className={changeSetCounts}>{total}</span> : null}
        <LeafAttribution leaves={leaves} />
        <ChangeSetReadState counters={counters} problem={problem} unrecorded={unrecorded} />
      </button>
      <ChangeSetStateDetails label={label} problem={problem} unrecorded={unrecorded} />
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
}: {
  counters: { code: ChangeCounters; memory: ChangeCounters } | null;
  problem: ReviewFailure | null;
  unrecorded: string | null;
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
      <span className={changeSetCounts} data-testid="changeset-state" data-review-state="unrecorded">
        unrecorded
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
}: {
  label: string;
  problem: ReviewFailure | null;
  unrecorded: string | null;
}) {
  if (problem) {
    return (
      <EntryStateDetails testId="changeset-state-details" label={label}>
        This change-set could not be read. {problemSentence(problem)}
      </EntryStateDetails>
    );
  }
  if (unrecorded === null) return null;
  return (
    <EntryStateDetails testId="changeset-state-details" label={label}>
      Nothing has recorded this change-set&apos;s endpoint yet: the range is unrecorded, not measured
      empty.{unrecorded ? ` ${unrecorded}` : ''}
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

// The change-set bar shown on a task-document READER (master or leaf), with identity taken from
// the doc node — so it appears with NO active enclosure (previously the change-set buttons only
// lived on the live enclosure spine). A master gets the SERIES net button; a leaf gets COMMITTED
// (always — its landed delta), WORKING (only while its enclosure is live — the uncommitted delta)
// and the INTENT REVIEW (always — the live candidate while the enclosure is live, and the leaf's
// own recorded comparison once it is closed, which is what `live` selects). Liveness is read from
// the store here, so callers thread only `onOpen`.
export function DocChangeSetBar({
  kind,
  repo,
  master,
  leaf,
  onOpen,
}: {
  kind: 'master' | 'leaf';
  repo: string;
  master: string;
  leaf?: string;
  onOpen?: (target: ChangeSetTarget) => void;
}) {
  const enclosures = useDashboard((s) => s.enclosures);
  const activeWorktreeGroups = useDashboard((s) => s.activeWorktreeGroups);
  const live = leaf ? leafIsLive(enclosures, activeWorktreeGroups, repo, leaf) : false;
  // The Intent review summary's invalidation signal (ICR-R17): THIS leaf's own lifecycle facts -- its
  // liveness and its enclosure's closeout/integration/cleanup state, which decide which comparison
  // the entry opens -- plus the entry's re-validation generation, which moves when the task detail is
  // shown again or the reviewer refreshes (`data/intentEntryRevalidation.tsx`; no dashboard signal
  // carries a knowledge write). It is deliberately not the serialized global analytics document,
  // which moves on every workspace publication and made the entry re-read on unrelated tasks.
  const generation = useIntentEntryGeneration();
  const facts = leaf ? `${leafFacts(enclosures, repo, leaf, live)}#${generation}` : '';
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

function leafEnclosure(
  enclosures: Record<string, EnclosureNode>,
  repo: string,
  leaf: string,
): EnclosureNode | undefined {
  return Object.values(enclosures).find(
    (e) => e.repoName === repo && e.leafId.toLowerCase() === leaf.toLowerCase(),
  );
}

// This leaf's lifecycle facts as one comparable value: equal while nothing about the leaf moved.
function leafFacts(
  enclosures: Record<string, EnclosureNode>,
  repo: string,
  leaf: string,
  live: boolean,
): string {
  const node = leafEnclosure(enclosures, repo, leaf);
  return JSON.stringify([
    live,
    node?.closeoutStatus ?? null,
    node?.integrationStatus ?? null,
    node?.cleanup ?? null,
    node?.codeWorktreeExists ?? null,
  ]);
}

// Whether THIS leaf's enclosure is live: what the working change-set is gated on, and which record
// the reviewer entry is addressed to. One predicate for both, so they cannot come to disagree about
// what "live" means and the bar's own branching stays readable.
function leafIsLive(
  enclosures: Record<string, { repoName: string; leafId: string; worktreeGroup: string }>,
  activeWorktreeGroups: string[],
  repo: string,
  leaf: string,
): boolean {
  return Object.values(enclosures).some(
    (e) =>
      e.repoName === repo &&
      e.leafId.toLowerCase() === leaf.toLowerCase() &&
      activeWorktreeGroups.includes(e.worktreeGroup.split('/').filter(Boolean).pop() ?? ''),
  );
}

// Drill-in match key: a SubTaskRef.file / a slice's docPath basename, minus extension. A
// master's index row resolves to the slice doc whose slug equals the ref's file stem.
