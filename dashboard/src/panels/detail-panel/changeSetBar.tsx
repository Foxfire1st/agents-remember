// The change-set buttons shown on a task-document READER: a master gets the series net button,
// a leaf gets committed (always) plus working (while its enclosure is live). Counters come from
// the changeset data layer; liveness is read from the dashboard store.
import { useEffect, useState } from "react";

import {
  type ChangeCounters,
  leafChangeset,
  masterChangeset,
  taskChangeset,
} from "../../data/changeset";
import { type ReviewEntry, intentReviewEntries } from "../../data/review";
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
  useEffect(() => {
    let live = true;
    setCounters(null);
    const req = target.leaf
      ? leafChangeset(target.repo, target.master ?? "", target.leaf, target.mode ?? "committed")
      : target.master
        ? masterChangeset(target.repo, target.master, { includeLeaves: false })
        : taskChangeset(target.repo, target.scope ?? "");
    void req.then(
      (d) => live && setCounters(d.counters),
      () => live && setCounters(null),
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
      onClick={() => onOpen(target)}
      data-testid="open-changeset"
    >
      ⇄ {label}
      {total ? <span className={changeSetCounts}>{total}</span> : null}
    </button>
  );
}

// The reviewed subject of one live leaf, read from the server that owns the resolution. The id
// returned is a recorded identity inside the candidate the server resolved from canonical task
// context, so this hook chooses no candidate and invents no id: it asks, and a refusal or an empty
// list is a normal answer that leaves the entry hidden. Nothing is fetched for a leaf that is not
// live, because there is no candidate to resolve and the working change-set is hidden for the same
// reason.
function useReviewSubject(
  live: boolean,
  repo: string,
  master: string,
  leaf?: string,
): ReviewEntry | undefined {
  const [entry, setEntry] = useState<ReviewEntry | undefined>(undefined);
  useEffect(() => {
    let current = true;
    setEntry(undefined);
    if (!live || !leaf) return () => void (current = false);
    void intentReviewEntries(repo, master, leaf).then(
      (result) => current && setEntry(result.state === "entries" ? result.entries?.[0] : undefined),
      () => current && setEntry(undefined),
    );
    return () => {
      current = false;
    };
  }, [live, repo, master, leaf]);
  return entry;
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
        <ChangeSetButton
          target={{ repo, master, leaf, mode: "working" }}
          label="working"
          onOpen={onOpen}
        />
      ) : null}
      {live && subject ? (
        // The reviewer entry, added BESIDE the working/committed actions and never in their place.
        // It is offered only for an admitted live curator candidate -- the same liveness the
        // working change-set is gated on -- and it carries the reviewed subject's recorded identity
        // rather than a filesystem path, because the browser never chooses the candidate. The
        // identity itself comes from the server's own resolution over the candidate pair; when no
        // candidate is admitted, or the pair selects no subject, `subject` stays undefined and no
        // entry is offered rather than a broken one.
        <ChangeSetButton
          target={{
            repo,
            master,
            leaf,
            review: { selectorKind: subject.selector_kind, selectorId: subject.selector_id },
          }}
          label="Intent review"
          onOpen={onOpen}
        />
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
