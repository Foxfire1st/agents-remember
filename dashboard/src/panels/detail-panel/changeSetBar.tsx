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

// What the catalogue read answered, as the task view needs it: every subject the pair offers with
// the labelled totals, and -- when the read did not answer with a subject list -- the reason, in
// the owner's own words.
interface ReviewCatalogueRead {
  loading: boolean;
  entries?: ReviewEntry[];
  totalSubjects?: number;
  invariantTotal?: number;
  familyTotal?: number;
  // The read answered `entries` with none: a known-empty answer about the pair's recorded subjects,
  // which is a fact about the datasets and not a failure. Zero subjects is a valid catalogue beside
  // the source inventory, and the entry below still opens that inventory.
  empty?: boolean;
  // The read refused (a typed refusal, a transport-level failure, or an answer this client does not
  // admit). It is carried rather than swallowed: the entry must be able to say why it cannot refine.
  problem?: ReviewFailure;
}

// The labelled subject catalogue of one live leaf, read from the server that owns the resolution.
// Every id returned is a recorded identity inside the pair the server resolved from canonical task
// context, so this hook chooses no candidate and invents no id: it asks, and every answer is carried
// -- the whole catalogue with its totals, a known-empty list, or a typed refusal whose code, reason
// and next action are shown beside the entry (ICR-R16). The route answers a refusal with its own
// status and the refusal in the body, so the shared review decode reads the body whatever the status;
// `getJson` would have thrown and the detail would have been lost. Nothing is fetched for a leaf that
// is not live, because there is no candidate to resolve and the working change-set is hidden for the
// same reason.
function useReviewCatalogue(
  live: boolean,
  repo: string,
  master: string,
  leaf?: string,
): ReviewCatalogueRead {
  const [read, setRead] = useState<ReviewCatalogueRead>({ loading: false });
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
          // The totals are the server's own; a body that predates them falls back to the page it
          // carried, so a short catalogue still reads as the whole answer it is.
          const totalSubjects =
            typeof result.total_subjects === "number" ? result.total_subjects : entries.length;
          const invariantTotal =
            typeof result.invariant_total === "number"
              ? result.invariant_total
              : entries.filter((entry) => entry.selector_kind === "invariant").length;
          const familyTotal =
            typeof result.family_total === "number"
              ? result.family_total
              : entries.filter((entry) => entry.selector_kind === "family").length;
          setRead({
            loading: false,
            entries,
            totalSubjects,
            invariantTotal,
            familyTotal,
            empty: entries.length === 0,
          });
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
// refusal here is a stated reason and not a missing control. A catalogue that answered carries its
// own picker and totals below instead of this state.
function ReviewEntryState({ read }: { read: ReviewCatalogueRead }) {
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

// One catalogue row's presence, in the reader's own words. A retired subject is still a subject:
// it is listed and selectable, marked for what it is rather than dropped to imply a smaller
// complete population.
function presenceMarker(presence: ReviewEntry["presence"] | undefined): string {
  if (presence === "before_only") return "retired · before-only";
  if (presence === "after_only") return "new · after-only";
  return "";
}

// The labelled subject catalogue beside the entry: every recorded subject is selectable here, with
// the server's own totals. The Intent review button opens the selected row; the task context (the
// whole task, no subject) stays reachable because the button carries `review: {}` while the read
// is loading, empty, or refused.
function ReviewCataloguePicker({
  read,
  selectedId,
  onSelect,
}: {
  read: ReviewCatalogueRead;
  selectedId: string | null;
  onSelect: (selectorId: string) => void;
}) {
  const entries = read.entries ?? [];
  if (!entries.length) return null;
  const effective = entries.find((entry) => entry.selector_id === selectedId) ?? entries[0];
  return (
    <span style={{ display: "inline-flex", gap: "0.4rem", alignItems: "center" }}>
      <select
        data-testid="review-subject-picker"
        aria-label="reviewed subject"
        value={effective.selector_id}
        onChange={(event) => onSelect(event.target.value)}
      >
        {entries.map((entry) => {
          const marker = presenceMarker(entry.presence);
          return (
            <option
              key={`${entry.selector_kind}:${entry.selector_id}`}
              value={entry.selector_id}
              data-testid="review-subject-option"
              data-selector-kind={entry.selector_kind}
              data-presence={entry.presence ?? ""}
            >
              {entry.selector_kind} · {entry.label}
              {marker ? ` · ${marker}` : ""}
            </option>
          );
        })}
      </select>
      <span style={{ color: "muted" }} data-testid="review-catalogue-totals">
        {read.totalSubjects ?? entries.length} subject(s)
        {read.invariantTotal !== undefined && read.familyTotal !== undefined
          ? ` · ${read.invariantTotal} invariant(s) · ${read.familyTotal} family/families`
          : ""}
      </span>
    </span>
  );
}

// The live leaf's own entries: the working change-set, the reviewer entry with its catalogue
// picker, and the entry read's own state. It mounts only while the leaf's enclosure is live, so
// the catalogue read and the selection state live here rather than in the bar above.
function LiveLeafEntries({
  repo,
  master,
  leaf,
  onOpen,
}: {
  repo: string;
  master: string;
  leaf: string;
  onOpen: (target: ChangeSetTarget) => void;
}) {
  const catalogue = useReviewCatalogue(true, repo, master, leaf);
  // The catalogue row the Intent review button opens. It defaults to the catalogue's first row
  // and follows the reader's own choice afterwards; a choice that outlives the catalogue (a new
  // answer that no longer lists it) falls back to the first row rather than opening a stale id.
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const listed = catalogue.entries ?? [];
  const selected =
    listed.length > 0
      ? (listed.find((entry) => entry.selector_id === selectedId) ?? listed[0])
      : undefined;
  // The working change-set, the reviewer entry and the entry read's own state. The entry is added
  // BESIDE the working/committed actions and never in their place: it is offered for an admitted
  // live curator candidate -- the same liveness the working change-set is gated on -- and **the
  // task context is the entry**: the target names the repo/master/leaf the server resolves the
  // candidate from and carries no filesystem path, because the browser never chooses the candidate.
  //
  // The server's subject catalogue is a REFINEMENT and never a gate. Every recorded subject is
  // offered in the picker beside the button, and the selected identity travels with the target
  // so the review is opened on it; when the read answers with no subject, refuses, or fails
  // outright (ICR-R16: the route puts its refusal in the body of a non-2xx response, and this
  // client reads it whatever the status), the target still carries `review: {}` and the review
  // opens on the task's complete source change inventory. The read's own answer is printed
  // beside the entry by `ReviewEntryState`, so a refusal is a visible reason rather than a
  // silently missing refinement. Offering the entry only for a subject is exactly how a task
  // with no knowledge lost its source review.
  return (
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
          review: selected
            ? {
                selectorKind: selected.selector_kind,
                selectorId: selected.selector_id,
              }
            : {},
        }}
        label="Intent review"
        onOpen={onOpen}
      />
      <ReviewCataloguePicker read={catalogue} selectedId={selectedId} onSelect={setSelectedId} />
      <ReviewEntryState read={catalogue} />
    </>
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
      {live ? <LiveLeafEntries repo={repo} master={master} leaf={leaf} onOpen={onOpen} /> : null}
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
