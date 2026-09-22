// The change-set buttons shown on a task-document READER: a master gets the series net button,
// a leaf gets committed (always) plus working (while its enclosure is live). Counters come from
// the changeset data layer; liveness is read from the dashboard store.
import { useEffect, useState } from "react";

import {
  type ChangeCounters,
  type MasterNetPins,
  leafChangeset,
  masterChangeset,
  taskChangeset,
} from "../../data/changeset";
import {
  type ReviewEntry,
  type ReviewFailure,
  intentReviewEntries,
  reviewProblemFromCause,
  reviewProblemFromRefusal,
  unreadableAnswer,
} from "../../data/review";
import { useDashboard } from "../../data/store";
import type { ChangeSetTarget } from "../changeset/ChangeSetViewer";
import {
  changeSetBar,
  changeSetBtn,
  changeSetCounts,
} from "./styles";

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
  useEffect(() => {
    let live = true;
    setCounters(null);
    setGeneration(null);
    const req = target.leaf
      ? leafChangeset(target.repo, target.master ?? "", target.leaf, target.mode ?? "committed")
      : target.master
        ? masterChangeset(target.repo, target.master, { includeLeaves: false })
        : taskChangeset(target.repo, target.scope ?? "");
    void req.then(
      (d) => {
        if (!live) return;
        setCounters(d.counters);
        setGeneration(
          "generation" in d && d.generation
            ? {
                codeBase: d.generation.codeBase,
                codeTip: d.generation.codeTip,
                memoryBase: d.generation.memoryBase,
                memoryTip: d.generation.memoryTip,
              }
            : null,
        );
      },
      () => {
        if (!live) return;
        setCounters(null);
        setGeneration(null);
      },
    );
    return () => {
      live = false;
    };
  }, [target.repo, target.scope, target.master, target.leaf, target.mode]);
  const total = counters
    ? `+${counters.code.insertions + counters.memory.insertions} −${counters.code.deletions + counters.memory.deletions}`
    : null;
  return (
    <button
      type="button"
      className={changeSetBtn}
      onClick={() => onOpen(generation ? { ...target, generation } : target)}
      data-testid="open-changeset"
    >
      ⇄ {label}
      {total ? <span className={changeSetCounts}>{total}</span> : null}
    </button>
  );
}

// What the entry read answered, as the task view needs it: the subject the pair offers (if any), and
// -- when the read did not answer with a subject list -- the reason, in the owner's own words.
interface ReviewSubjectRead {
  loading: boolean;
  entry?: ReviewEntry;
  // The read answered `entries` with none: a known-empty answer about the pair's recorded subjects,
  // which is a fact about the datasets and not a failure.
  empty?: boolean;
  // The read refused (a typed refusal, a transport-level failure, or an answer this client does not
  // admit). It is carried rather than swallowed: the entry must be able to say why it cannot refine.
  problem?: ReviewFailure;
}

// The reviewed subject of one live leaf, read from the server that owns the resolution. The id
// returned is a recorded identity inside the candidate the server resolved from canonical task
// context, so this hook chooses no candidate and invents no id: it asks, and every answer is carried
// -- a subject, a known-empty list, or a typed refusal whose code, reason and next action are shown
// beside the entry (ICR-R16). The route answers a refusal with its own status and the refusal in the
// body, so the shared review decode reads the body whatever the status; `getJson` would have thrown
// and the detail would have been lost. Nothing is fetched for a leaf that is not live, because there
// is no candidate to resolve and the working change-set is hidden for the same reason.
function useReviewSubject(
  live: boolean,
  repo: string,
  master: string,
  leaf?: string,
): ReviewSubjectRead {
  const [read, setRead] = useState<ReviewSubjectRead>({ loading: false });
  useEffect(() => {
    let current = true;
    if (!live || !leaf) {
      setRead({ loading: false });
      return () => void (current = false);
    }
    setRead({ loading: true });
    void intentReviewEntries(repo, master, leaf).then(
      (result) => {
        if (!current) return;
        if (result.state === "entries") {
          const entries = result.entries ?? [];
          setRead({ loading: false, entry: entries[0], empty: entries.length === 0 });
          return;
        }
        if (result.state === "refused") {
          setRead({
            loading: false,
            problem: result.refusal
              ? reviewProblemFromRefusal(result.refusal)
              : unreadableAnswer("refused"),
          });
          return;
        }
        setRead({ loading: false, problem: unreadableAnswer(result.state) });
      },
      (cause: unknown) => {
        if (current) setRead({ loading: false, problem: reviewProblemFromCause(cause) });
      },
    );
    return () => {
      current = false;
    };
  }, [live, repo, master, leaf]);
  return read;
}

// The entry read's own state, printed beside the entry rather than hidden. It never gates the entry:
// the button beside it is offered for an admitted live candidate whatever this read answered, so a
// refusal here is a stated reason and not a missing control.
function ReviewEntryState({ read }: { read: ReviewSubjectRead }) {
  if (read.loading) {
    return (
      <span
        style={{ color: "muted" }}
        data-testid="review-entry-state"
        data-review-state="loading"
      >
        reading this candidate&apos;s recorded subjects…
      </span>
    );
  }
  if (read.empty) {
    return (
      <span
        style={{ color: "muted" }}
        data-testid="review-entry-state"
        data-review-state="known-empty"
      >
        no subject is recorded for this pair; the review opens on the task&apos;s complete source
        change inventory.
      </span>
    );
  }
  if (!read.problem) return null;
  return (
    <span
      style={{ color: "muted" }}
      data-testid="review-entry-state"
      data-review-state={read.problem.token}
      data-review-code={read.problem.code}
    >
      this candidate&apos;s recorded subjects could not be read ({read.problem.code}):{" "}
      {read.problem.detail}
      {read.problem.offendingInput ? ` — offending input: ${read.problem.offendingInput}` : ""}
      {read.problem.nextAction ? ` — next: ${read.problem.nextAction}` : ""}
    </span>
  );
}

// The change-set bar shown on a task-document READER (master or leaf), with identity taken from
// the doc node — so it appears with NO active enclosure (previously the change-set buttons only
// lived on the live enclosure spine). A master gets the SERIES net button; a leaf gets COMMITTED
// (always — its landed delta) plus WORKING (only while its enclosure is live — the uncommitted
// delta). Liveness is read from the store here, so callers thread only `onOpen`.
export function DocChangeSetBar({
  kind,
  repo,
  master,
  leaf,
  onOpen,
}: {
  kind: "master" | "leaf";
  repo: string;
  master: string;
  leaf?: string;
  onOpen?: (target: ChangeSetTarget) => void;
}) {
  const enclosures = useDashboard((s) => s.enclosures);
  const activeWorktreeGroups = useDashboard((s) => s.activeWorktreeGroups);
  const live = leaf ? leafIsLive(enclosures, activeWorktreeGroups, repo, leaf) : false;
  const subject = useReviewSubject(live, repo, master, leaf);
  if (!onOpen || !repo || !master) return null;
  if (kind === "master") {
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
        target={{ repo, master, leaf, mode: "committed" }}
        label="committed"
        onOpen={onOpen}
      />
      {live ? (
        // The working change-set, the reviewer entry and the entry read's own state, all gated on the
        // same liveness. The entry is added BESIDE the working/committed actions and never in their
        // place: it is offered for an admitted live curator candidate -- the same liveness the working
        // change-set is gated on -- and **the task context is the entry**: the target names the
        // repo/master/leaf the server resolves the candidate from and carries no filesystem path,
        // because the browser never chooses the candidate.
        //
        // The server's subject list is a REFINEMENT and never a gate. When it offers a recorded
        // subject, that identity travels with the target so the review is opened on it; when the read
        // answers with no subject, refuses, or fails outright (ICR-R16: the route puts its refusal in
        // the body of a non-2xx response, and this client reads it whatever the status), the target
        // still carries `review: {}` and the review opens on the task's complete source change
        // inventory. The read's own answer is printed beside the entry by `ReviewEntryState`, so a
        // refusal is a visible reason rather than a silently missing refinement. Offering the entry
        // only for a subject is exactly how a task with no knowledge lost its source review.
        <>
          <ChangeSetButton
            target={{ repo, master, leaf, mode: "working" }}
            label="working"
            onOpen={onOpen}
          />
          <ChangeSetButton
            target={{
              repo,
              master,
              leaf,
              review: subject.entry
                ? {
                    selectorKind: subject.entry.selector_kind,
                    selectorId: subject.entry.selector_id,
                  }
                : {},
            }}
            label="Intent review"
            onOpen={onOpen}
          />
          <ReviewEntryState read={subject} />
        </>
      ) : null}
    </div>
  );
}

// Whether THIS leaf's enclosure is live, which is what both the working change-set and the reviewer
// entry are gated on. One predicate for the two entries, so they cannot come to disagree about what
// "live" means and the bar's own branching stays readable.
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
      activeWorktreeGroups.includes(e.worktreeGroup.split("/").filter(Boolean).pop() ?? ""),
  );
}

// Drill-in match key: a SubTaskRef.file / a slice's docPath basename, minus extension. A
// master's index row resolves to the slice doc whose slug equals the ref's file stem.
