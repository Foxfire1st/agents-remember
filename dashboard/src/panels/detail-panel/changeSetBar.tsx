// The change-set buttons shown on a task-document READER: a master gets the series net button, a leaf
// gets committed (always), working (while its enclosure is live) and the Intent review (always — the
// live candidate while the enclosure is live, and the leaf's own recorded comparison once it is
// closed, ICR-R12). Counters come from the changeset data layer; liveness is read from the dashboard
// store, and it selects WHICH record the review entry is addressed to rather than whether it exists.
import { useEffect, useRef, useState } from "react";

import {
  type ChangeCounters,
  type MasterChangeset,
  type MasterNetPins,
  leafChangeset,
  masterChangeset,
  taskChangeset,
} from "../../data/changeset";
import {
  type ReviewEntry,
  type ReviewEntryListResult,
  type ReviewFailure,
  intentReviewEntries,
  reviewProblemFromCause,
  reviewProblemFromRefusal,
  unreadableAnswer,
} from "../../data/review";
import { useDashboard } from "../../data/store";
import type { Analytics } from "../../types/projection";
import type { ChangeSetTarget } from "../changeset/ChangeSetViewer";
import {
  changeSetBar,
  changeSetBtn,
  changeSetCounts,
} from "./styles";

// The net's leaf attribution as one phrase: how many leaves the master carries and how many of them
// have landed, or nothing at all when the read was not a master's (or predates the breakdown) -- an
// absent answer is not rendered as a zero.
function leafAttribution(leaves: MasterChangeset["leaves"] | null): string | null {
  if (!leaves || leaves.length === 0) return null;
  const committed = leaves.filter((leaf) => leaf.state === "committed").length;
  const working = leaves.length - committed;
  return [
    `${leaves.length} leaf/leaves`,
    working > 0 ? `${committed} committed · ${working} working` : `${committed} committed`,
  ].join(" · ");
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
  // The net's own leaves, when this read is a master's: how many the master carries and how many of
  // them have landed, which is what makes the total beside it attributable at a glance.
  const [leaves, setLeaves] = useState<MasterChangeset["leaves"] | null>(null);
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
    const req = target.leaf
      ? leafChangeset(target.repo, target.master ?? "", target.leaf, target.mode ?? "committed")
      : target.master
        ? // The master read asks for its per-leaf attribution (R33.2) -- the route answers one row
          // per leaf, and this control is where the reviewer sees that the net total IS those leaves
          // summed rather than a single number with no owner.
          masterChangeset(target.repo, target.master, { includeLeaves: true })
        : taskChangeset(target.repo, target.scope ?? "");
    void req.then(
      (d) => {
        if (!live) return;
        setCounters(d.counters);
        setLeaves("leaves" in d ? (d.leaves ?? []) : null);
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
      (cause: unknown) => {
        // A refusal is an ANSWER and is filed as one. `live` still guards the write -- a read whose
        // props moved, or whose control unmounted, must not publish into state -- but it is no longer
        // what discards the refusal: while this is still the control on screen, the refusal it earned
        // is what it shows.
        if (!live) return;
        setCounters(null);
        setGeneration(null);
        setLeaves(null);
        setProblem(reviewProblemFromCause(cause));
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
      {leafAttribution(leaves) ? (
        <span className={changeSetCounts} data-testid="changeset-leaf-attribution">
          {leafAttribution(leaves)}
        </span>
      ) : null}
      <ChangeSetReadState counters={counters} problem={problem} />
    </button>
  );
}

// The counter read's own state, printed in the control rather than hidden in the absence of a total,
// and modelled on `ReviewEntryState` below -- one span, `data-review-state` for the state it is in
// and `data-review-code` for the owner's own code -- because five different things have to stay
// tellable apart and only three of them are states of the change-set itself:
//
//   * `loading`     -- the read is in flight: nothing was measured, so nothing is claimed;
//   * `known-empty` -- the read ANSWERED and the delta is measured empty (no changed file in either
//                      half). A measured zero is a measurement, and it is neither a failure nor an
//                      absence of an answer -- so it is printed as the zero it is;
//   * an answer carrying changed files prints no state here at all: the counters beside it ARE the
//                      answer, exactly as the catalogue is the answer to the entry read;
//   * a refusal     -- the route named it. Its code, its reason in the owner's own words and the
//                      identifier the route echoed are all shown (and the next action too, when a
//                      route publishes one — this family's refusals put their guidance in the reason
//                      itself), so a reader is never left with a bare count and no explanation;
//   * an unreadable or unreachable answer -- the same span under its own token (`unreadable`: a
//                      response this route did not produce; `network`: no response at all), each
//                      saying which of the two happened.
//
// The token is the shared one (`reviewFailureToken`), so this control and the review surface cannot
// come to disagree about what a refusal's code means. What is NOT claimed here is anything about a
// delta that did not answer: no code, no total, and no "empty".
function ChangeSetReadState({
  counters,
  problem,
}: {
  counters: { code: ChangeCounters; memory: ChangeCounters } | null;
  problem: ReviewFailure | null;
}) {
  if (problem) {
    return (
      <span
        className={changeSetCounts}
        data-testid="changeset-state"
        data-review-state={problem.token}
        data-review-code={problem.code}
      >
        this change-set could not be read ({problem.code}): {problem.detail}
        {problem.offendingInput ? ` — offending input: ${problem.offendingInput}` : ""}
        {problem.nextAction ? ` — next: ${problem.nextAction}` : ""}
      </span>
    );
  }
  if (!counters) {
    return (
      <span className={changeSetCounts} data-testid="changeset-state" data-review-state="loading">
        reading this change-set…
      </span>
    );
  }
  // `files` and not the line totals: a rename or a mode change lists a file with no line delta, so a
  // zero insertion/deletion count is not a zero change-set.
  if (counters.code.files > 0 || counters.memory.files > 0) return null;
  return (
    <span className={changeSetCounts} data-testid="changeset-state" data-review-state="known-empty">
      no changed file in either half — this change-set is measured empty.
    </span>
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
  // The workspace facts this answer was read from, and whether those facts have since moved
  // (ICR-R17). `stale` is not a failure and not a claim that the candidate changed: it says the
  // workspace projection was republished after this answer, so the catalogue beside the entry may no
  // longer be the candidate's -- which is what the explicit refresh re-reads.
  facts: string;
  stale: boolean;
}

// One workspace fact the entry's catalogue depends on, as a value that changes when the fact does.
//
// WHY THE ENTRY NEEDS THIS AT ALL (ICR-R17, the packet's defect). The catalogue read used to depend on
// the props alone, so the only way to discover data published after the panel opened was to close and
// reopen it. The dashboard already has the invalidation infrastructure for this: `/api/state` and its
// delta channel republish the workspace projection whenever the task documents, drift snapshots,
// ledgers or series a repository records move, and `analytics` is that projection's own value --
// replaced when content changed and otherwise identity-preserving (data/store.ts). The entry asks
// again when that content moves, which is how first ingest becomes visible while the panel stays open.
//
// It is deliberately the projection and not a timer: no interval, no retry ladder, no unbounded loop.
// An idle store republishes an equal projection, an equal projection serializes to an equal string,
// and nothing is re-read at all. The projection is a bounded document the browser already holds, so
// this is one bounded comparison per render.
//
// WHAT IT IS HONEST ABOUT. It reports that the workspace facts moved -- never that the candidate
// changed. The projection carries no candidate digest, so claiming a new generation here would assert
// a measurement nobody made. The review surface is where a generation is actually compared against
// the identity a read carried (see `ReviewRefresh`).
function reviewDependencyFacts(analytics: Analytics | null): string {
  return analytics === null ? "no-projection" : JSON.stringify(analytics);
}

// The entry's explicit refresh control. It is always offered -- the reader's own way to ask again,
// which is the packet's "explicit refresh control" and the reason the workspace signal above can stay
// a signal rather than a polling loop -- and it is marked for exactly as long as the list beside it is
// behind the workspace: from the publication that moved the projection until the answer for that
// projection is filed. The mark is therefore observable in the state it describes (a settled DOM while
// the re-read is in flight), and it is gone once the re-read answers -- including when the answer is a
// refusal or a failure, because then the projection HAS been answered and the list beside it is the
// best available reading of it.
function ReviewCatalogueRefresh({ stale, onRefresh }: { stale: boolean; onRefresh: () => void }) {
  return (
    <button
      type="button"
      onClick={onRefresh}
      data-testid="review-catalogue-refresh"
      data-catalogue-stale={stale ? "true" : "false"}
      title="re-read this pair's recorded subjects from the candidate as it is now"
    >
      ⟳ refresh subjects
      {stale ? (
        <span data-testid="review-catalogue-stale"> · workspace facts changed</span>
      ) : null}
    </button>
  );
}

// The labelled subject catalogue of one leaf, read from the server that owns the resolution. Every id
// returned is a recorded identity inside the pair the server resolved from canonical task context --
// the live candidate while the enclosure is live, and the leaf's own recorded comparison once it is
// closed (ICR-R12) -- so this hook chooses no candidate and invents no id: it asks, and every answer
// is carried: the whole catalogue with its totals, a known-empty list, or a typed refusal whose code,
// reason and next action are shown beside the entry (ICR-R16). The route answers a refusal with its
// own status and the refusal in the body, so the shared review decode reads the body whatever the
// status; `getJson` would have thrown and the detail would have been lost. The read is made for any
// named leaf and needs no liveness: a closed leaf's catalogue is what its record holds, and the entry
// beside it stays openable either way.
//
// THE READ IS INVALIDATED, NOT REPEATED (ICR-R17). It is re-asked when the workspace facts it
// depends on move (the store's own projection channel) or when the reader clicks refresh -- never on
// a timer. Two properties hold across those re-reads:
//
//   * a superseded answer is dropped. Each read takes the next sequence number and only the newest
//     may write state, so a response for a previous leaf (the props changed while it was in flight)
//     can never overwrite the catalogue of the leaf on screen now;
//   * the reader's selected identity survives an answer that still lists it. The selection lives with
//     the picker and is only ever narrowed to the catalogue, so a refresh that records the same
//     subjects keeps the exact row the reader chose (see `LeafEntries`).
// One answer from the entry route, as the read state it is. The mapping is a pure function rather
// than a branch inside the hook so the hook stays one read cycle: what "this body means" and "when to
// ask" are different questions, and a body this client does not admit is answered as the failure it
// is rather than read as a catalogue.
function catalogueAnswer(result: ReviewEntryListResult, facts: string): ReviewCatalogueRead {
  if (result.state === "entries") {
    const entries = result.entries ?? [];
    // The totals are the server's own; a body that predates them falls back to the page it carried,
    // so a short catalogue still reads as the whole answer it is.
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
    return {
      loading: false,
      entries,
      totalSubjects,
      invariantTotal,
      familyTotal,
      empty: entries.length === 0,
      facts,
      stale: false,
    };
  }
  if (result.state === "refused") {
    return {
      loading: false,
      problem: result.refusal
        ? reviewProblemFromRefusal(result.refusal)
        : unreadableAnswer("refused"),
      facts,
      stale: false,
    };
  }
  return { loading: false, problem: unreadableAnswer(result.state), facts, stale: false };
}

function useReviewCatalogue(
  repo: string,
  master: string,
  leaf: string | undefined,
  facts: string,
): ReviewCatalogueRead & { refresh: () => void } {
  const [read, setRead] = useState<ReviewCatalogueRead>({ loading: false, facts, stale: false });
  const [nonce, setNonce] = useState(0);
  const reads = useRef(0);
  const factsRef = useRef(facts);
  factsRef.current = facts;
  // Whether the list on screen was read from facts the workspace has since moved past. It is derived
  // from the facts recorded WITH THE LAST ANSWER, not from the live value: that is what makes it a
  // statement about the list beside it ("this was read before the projection moved") rather than a
  // guess about a read that has not happened. Deliberately NOT gated on `loading` -- gating it there
  // is what made the mark appear and vanish inside one flush, so a reader was never told at all.
  const stale = read.facts !== facts;
  useEffect(() => {
    let mounted = true;
    // The facts in force when the read starts, taken from the ref so the answer is filed under the
    // question it actually answered even if the projection moves again while it is in flight.
    const askedFor = factsRef.current;
    const seq = ++reads.current;
    const live = () => mounted && reads.current === seq;
    setRead((previous) => ({ ...previous, loading: true, stale: false }));
    if (!leaf) {
      setRead({ loading: false, facts: askedFor, stale: false });
      return () => void (mounted = false);
    }
    void intentReviewEntries(repo, master, leaf).then(
      (result) => {
        if (live()) setRead(catalogueAnswer(result, askedFor));
      },
      (cause: unknown) => {
        if (live()) setRead({ loading: false, problem: reviewProblemFromCause(cause), facts: askedFor, stale: false });
      },
    );
    return () => {
      mounted = false;
    };
  }, [repo, master, leaf, facts, nonce]);
  const refresh = () => {
    // The reader's own re-read. The effect above is what asks; this only says "ask again", so there
    // is one read path and a click cannot become a second way of composing the request.
    setNonce((value) => value + 1);
  };
  return { ...read, stale, refresh };
}

// The entry read's own state, printed beside the entry rather than hidden. It never gates the entry:
// the button beside it is offered for the leaf whatever this read answered -- for a live candidate and
// for a closed leaf's recorded comparison alike -- so a refusal here is a stated reason and not a
// missing control. A catalogue that answered carries its own picker and totals below instead of this
// state.
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

// One leaf's own entries: the working change-set (live only), the reviewer entry with its
// catalogue picker, and the entry read's own state. The catalogue read and the selection state live
// here rather than in the bar above, because they belong to one leaf's question.
//
// `live` selects WHICH record the entry is addressed to (ICR-R12) and nothing else: a live leaf's
// review is the candidate it holds now, and a closed leaf's is the comparison its own durable
// generation bound. It is not a gate. A closed leaf keeps the Intent review entry -- that is the
// whole point of the packet, because its worktree is gone and the recorded comparison is the only
// comparison there is -- while the WORKING change-set stays live-gated, since "what is not committed
// yet" genuinely does not exist once the enclosure is closed.
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
  // The workspace facts this leaf's catalogue depends on, as the value whose movement invalidates
  // the read (ICR-R17). The bar reads it from the store and threads it down, so the hook asks again
  // exactly when the projection its answer came from was republished.
  facts: string;
  onOpen: (target: ChangeSetTarget) => void;
}) {
  const catalogue = useReviewCatalogue(repo, master, leaf, facts);
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
  // BESIDE the working/committed actions and never in their place: it is offered for the leaf -- a
  // live curator candidate while the enclosure is live, and the leaf's own recorded comparison once
  // it is closed (ICR-R12) -- and **the task context is the entry**: the target names the
  // repo/master/leaf the server resolves the candidate from and carries no filesystem path, because
  // the browser never chooses the candidate.
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
      {live ? (
        <ChangeSetButton
          target={{ repo, master, leaf, mode: "working" }}
          label="working"
          onOpen={onOpen}
        />
      ) : null}
      <ChangeSetButton
        target={{
          repo,
          master,
          leaf,
          review: {
            ...(selected
              ? {
                  selectorKind: selected.selector_kind,
                  selectorId: selected.selector_id,
                }
              : {}),
            ...(live ? {} : { historical: true }),
          },
        }}
        label={live ? "Intent review" : "Intent review (recorded)"}
        onOpen={onOpen}
      />
      <ReviewCataloguePicker read={catalogue} selectedId={selectedId} onSelect={setSelectedId} />
      <ReviewEntryState read={catalogue} />
      <ReviewCatalogueRefresh stale={catalogue.stale} onRefresh={catalogue.refresh} />
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
  kind: "master" | "leaf";
  repo: string;
  master: string;
  leaf?: string;
  onOpen?: (target: ChangeSetTarget) => void;
}) {
  const enclosures = useDashboard((s) => s.enclosures);
  const activeWorktreeGroups = useDashboard((s) => s.activeWorktreeGroups);
  // The invalidation signal the review entry depends on (ICR-R17). `analytics` is the projection the
  // workspace republishes whenever a task document, drift snapshot or ledger moves -- the store keeps
  // its identity while nothing changed and replaces it when anything did -- so reading it here is
  // what makes a publication after the panel opened visible without closing the panel. It is a
  // subscription to an existing channel, not a poll: an idle workspace performs no read at all.
  const analytics = useDashboard((s) => s.analytics);
  const facts = reviewDependencyFacts(analytics);
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
      activeWorktreeGroups.includes(e.worktreeGroup.split("/").filter(Boolean).pop() ?? ""),
  );
}

// Drill-in match key: a SubTaskRef.file / a slice's docPath basename, minus extension. A
// master's index row resolves to the slice doc whose slug equals the ref's file stem.
