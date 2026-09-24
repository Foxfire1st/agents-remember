// The Change-Set Viewer screen (L4). Opened as a task-scoped takeover from a DetailPanel button
// (Cockpit clears it via `onBack`, restoring the railed Operations view). Layout:
//   column 1  two rows — changed CODE files / changed ONBOARDING files (counts + status)
//   column 2  the selected file's diff (ChangeSetPane, always visible once a row is picked)
//   column 3  the code<->sidecar partner, opened from a "split" affordance
// The target selects the range (precedence leaf > master > scope), all rendered the same way and all
// inspectable per file: a `scope` (one active enclosure) = base->worktree; a `master` = the series
// NET base->tip; a `leaf` (+ `mode`) = that leaf's committed (base->code_commit) or working
// (HEAD->worktree, uncommitted) delta — the L4a doc-reader views, which need no live enclosure.
import { useEffect, useState } from "react";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";

import { css } from "../../../styled-system/css";
import {
  type ChangedFile,
  type FileDiff,
  type LeafMode,
  type MasterChangeset,
  type MasterNetPins,
  type TaskChangeset,
  fileDiff,
  leafChangeset,
  leafFileDiff,
  masterChangeset,
  masterFileDiff,
  taskChangeset,
} from "../../data/changeset";
import { type ReviewFailure, reviewProblemFromCause } from "../../data/review";
import { EmptyStateBackdrop } from "../EmptyStateBackdrop";
import type { ReviewSelectorKind } from "../../data/review";
import { ChangeSetPane } from "./ChangeSetPane";

export interface ChangeSetTarget {
  repo: string;
  scope?: string; // one active enclosure (full base->worktree diff)
  master?: string; // a series master (net base->selected result); also QUALIFIES a `leaf`
  leaf?: string; // a single leaf (committed/working), resolved by leaf-id; needs `master` + `mode`
  mode?: LeafMode; // committed = landed delta (base->code_commit), working = uncommitted delta (live)
  // The master net's generation pins: the exact recorded endpoints a listing published. Empty /
  // absent means the declared integrated result. Carried into the list request (a pinned list
  // reopens the recorded net after the branch advances) and -- via the list response -- into
  // each file expansion, so an opened entry stays bound to its generation (R03's idiom).
  generation?: MasterNetPins;
  // The Intent Reviewer's own selector: the reviewed subject's recorded identity, when the server
  // offered one. Its PRESENCE is what marks this target as a review and the cockpit's takeover
  // dispatch is what reads it -- the change-set viewer is never mounted for one, so no change-set
  // request is made from a review. An EMPTY object is the task-context entry: the review is opened
  // from the task alone and lists the complete source inventory, which is what a task with no
  // recorded invariant still has.
  //
  // `historical` says which record that review is read from (ICR-R12): absent is the live candidate,
  // and true is the leaf's own recorded comparison -- the entry a closed leaf offers, where the
  // worktree is gone and the durable generation is the only comparison there is.
  review?: { selectorKind?: ReviewSelectorKind; selectorId?: string; historical?: boolean };
}

const screen = css({
  height: "100%",
  minHeight: "0",
  display: "flex",
  flexDirection: "column",
  background: "bg",
});
const header = css({
  flexShrink: 0,
  display: "flex",
  alignItems: "center",
  gap: "0.8rem",
  paddingInline: "0.8rem",
  paddingBlock: "0.4rem",
  borderBottomWidth: "1px",
  borderBottomStyle: "solid",
  borderBottomColor: "grid",
  background: "bgPanel",
});
const back = css({
  fontFamily: "mono",
  fontSize: "0.74rem",
  color: "amber",
  background: "transparent",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  paddingInline: "0.5rem",
  paddingBlock: "0.15rem",
  cursor: "pointer",
  _hover: { borderColor: "amber" },
});
const title = css({ fontSize: "0.82rem", color: "ink", fontWeight: "600" });
const counterRow = css({ display: "flex", gap: "0.8rem", marginLeft: "auto", fontSize: "0.74rem", fontFamily: "mono" });
// The bound net generation: short digest + currentness + the one scope this view ever serves.
// Rendered only when the list response names its generation, so older payloads read unchanged.
const generationTag = css({ fontSize: "0.7rem", fontFamily: "mono", color: "muted" });
const ins = css({ color: "mint" });
const del = css({ color: "amber" });
const colList = css({ height: "100%", minHeight: "0", display: "flex", flexDirection: "column", background: "bgPanel" });
const section = css({ flex: "1", minHeight: "0", overflow: "auto" });
const sectionHead = css({
  position: "sticky",
  top: "0",
  background: "bg",
  paddingInline: "0.6rem",
  paddingBlock: "0.25rem",
  fontSize: "0.68rem",
  letterSpacing: "0.05em",
  textTransform: "uppercase",
  color: "amber",
  borderBottomWidth: "1px",
  borderBottomStyle: "solid",
  borderBottomColor: "grid",
});
const row = css({
  display: "flex",
  alignItems: "center",
  gap: "0.3rem",
  paddingInline: "0.6rem",
  // Match the File Viewer tree's selected/hover amber wash — the old `background: bg` active state was
  // indistinguishable from the panel, so the selected file looked unselected.
  _hover: { background: "color-mix(in oklab, var(--amber) 12%, transparent)" },
  "&[data-active=true]": { background: "color-mix(in oklab, var(--amber) 20%, transparent)" },
});
const rowMain = css({
  flex: "1",
  minWidth: "0",
  display: "flex",
  alignItems: "center",
  gap: "0.4rem",
  textAlign: "left",
  paddingBlock: "0.2rem",
  fontSize: "0.74rem",
  fontFamily: "mono",
  color: "ink",
  background: "transparent",
  border: "0",
  cursor: "pointer",
  _hover: { color: "amber" },
  _disabled: { cursor: "default" },
});
const pathText = css({ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" });
const statusChip = css({ width: "1.1em", textAlign: "center", color: "cyan" });
const counts = css({ marginLeft: "auto", display: "flex", gap: "0.4rem" });
const sidecarBtn = css({
  fontSize: "0.64rem",
  color: "cyan",
  border: "1px solid token(colors.grid)",
  borderRadius: "2px",
  paddingInline: "0.25rem",
  background: "transparent",
  cursor: "pointer",
  _hover: { borderColor: "cyan" },
});
const handle = css({ width: "3px", flexShrink: "0", background: "grid", cursor: "col-resize", _hover: { background: "amber" } });
// The per-leaf state chip a series read reports ("committed" / "working") — what the row's click
// resolves, so the reader knows which record opens before opening it.
const leafState = css({
  flex: "none",
  fontFamily: "mono",
  fontSize: "0.64rem",
  color: "muted",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  paddingInline: "0.25rem",
});
const leafCounts = css({
  marginLeft: "auto",
  fontFamily: "mono",
  fontSize: "0.66rem",
  color: "muted",
  whiteSpace: "nowrap",
});
// The measured-empty statement, printed in the pane instead of the pick-a-file backdrop: an empty
// change-set is a measurement, and a reader is told which measurement they are looking at.
const emptyNotice = css({
  height: "100%",
  display: "grid",
  placeItems: "center",
  padding: "1rem",
  color: "muted",
  fontSize: "0.8rem",
  textAlign: "center",
});
const placeholder = css({ height: "100%", display: "grid", placeItems: "center", padding: "1rem", color: "muted", fontSize: "0.8rem", textAlign: "center" });
// EmptyStateBackdrop's flex:1 canvas needs a flex-column host to fill the diff Panel.
const emptyHost = css({ height: "100%", minHeight: "0", display: "flex", flexDirection: "column" });

type Row = ChangedFile & { leafCount?: number };

function Counts({ file }: { file: Row }) {
  return (
    <span className={counts}>
      <span className={ins}>+{file.insertions ?? "·"}</span>
      <span className={del}>−{file.deletions ?? "·"}</span>
    </span>
  );
}

// A memory path under onboarding/ for a 1:1 sidecar -> its partner code path (strip onboarding/ + .md).
function partnerCodePath(memPath: string): string | null {
  if (!memPath.startsWith("onboarding/") || !memPath.endsWith(".md")) return null;
  const base = memPath.slice("onboarding/".length, -3);
  if (base.endsWith("/overview") || base.endsWith("/entities") || base.endsWith(".index")) return null;
  return base;
}

function changesetListRequest(
  repo: string,
  scope: string | undefined,
  master: string | undefined,
  leaf: string | undefined,
  mode: LeafMode | undefined,
  generation: MasterNetPins | undefined,
): Promise<TaskChangeset | MasterChangeset> {
  return leaf
    ? leafChangeset(repo, master ?? "", leaf, mode ?? "committed")
    : master
      ? // The master net's own attribution (R33.2): the route already answers with one row per
        // leaf, and asking for it is what makes the net reviewable leaf by leaf instead of as a
        // single total nobody can attribute.
        masterChangeset(repo, master, { includeLeaves: true, ...(generation ? { pins: generation } : {}) })
      : taskChangeset(repo, scope ?? "");
}

function diffRequestFor(
  repo: string,
  scope: string | undefined,
  master: string | undefined,
  leaf: string | undefined,
  mode: LeafMode | undefined,
  generation: MasterNetPins | undefined,
  kind: "code" | "memory",
  path: string,
): Promise<FileDiff | null> {
  return leaf
    ? leafFileDiff(repo, master ?? "", leaf, kind, path, mode ?? "committed")
    : master
      ? masterFileDiff(repo, master, kind, path, generation ?? {})
      : fileDiff(repo, scope ?? "", kind, path);
}

function partnerTargetFor(
  kind: "code" | "memory",
  path: string,
  hasSidecar?: boolean,
): { kind: "code" | "memory"; path: string } | null {
  if (kind === "code") {
    return hasSidecar ? { kind: "memory", path: `onboarding/${path}.md` } : null;
  }
  const code = partnerCodePath(path);
  return code ? { kind: "code", path: code } : null;
}

function headerLabelFor(
  leaf: string | undefined,
  master: string | undefined,
  scope: string | undefined,
  isSeries: boolean,
  mode: LeafMode | undefined,
): string {
  if (leaf) return mode === "working" ? `working · ${leaf} · uncommitted` : `committed · ${leaf}`;
  return isSeries ? `series ${master} · net since series start` : (scope ?? "");
}

function CounterRow({ counters }: { counters: TaskChangeset["counters"] }) {
  return (
    <span className={counterRow} data-testid="changeset-counters">
      <span>
        code <span className={ins}>+{counters.code.insertions}</span>{" "}
        <span className={del}>−{counters.code.deletions}</span> ({counters.code.files})
      </span>
      <span>
        memory <span className={ins}>+{counters.memory.insertions}</span>{" "}
        <span className={del}>−{counters.memory.deletions}</span> ({counters.memory.files})
      </span>
    </span>
  );
}

// The series list response when it names its bound generation, else null. A task/leaf payload
// never carries one, so the check is the discriminant -- not the target the view opened with.
function seriesListMeta(
  isSeries: boolean,
  data: TaskChangeset | MasterChangeset | null,
): MasterChangeset | null {
  if (!isSeries || data === null || !("generation" in data)) return null;
  return data;
}

// The per-leaf attribution a series read answered with (R33.2). A task/leaf payload has no `leaves`
// field at all, so the discriminant is the field's presence -- and a body that predates the
// breakdown reads as no breakdown rather than as an invented one.
function seriesLeaves(
  isSeries: boolean,
  data: TaskChangeset | MasterChangeset | null,
): MasterChangeset["leaves"] {
  if (!isSeries || data === null || !("leaves" in data)) return [];
  return data.leaves ?? [];
}

// The generation the open series view is bound to: the list response's own generation when it
// names one (it is newer than the entry's), else the entry's pins. Every file expansion below
// carries it, so an opened entry stays bound after the branch advances.
function boundSeriesGeneration(
  isSeries: boolean,
  data: TaskChangeset | MasterChangeset | null,
  entry: MasterNetPins | undefined,
): MasterNetPins | undefined {
  const listed = seriesListMeta(isSeries, data)?.generation ?? undefined;
  return listed ?? entry;
}

// The bound net generation as a header caption: short digest + currentness + the one scope this
// view ever serves. Rendered only when the list response names its generation, so older
// payloads read unchanged.
function SeriesGenerationTag({ meta }: { meta: MasterChangeset | null }) {
  const generation = meta?.generation;
  if (!generation) return null;
  const currentness = meta?.currentness ?? "unmeasured";
  return (
    <span
      className={generationTag}
      data-testid="changeset-generation"
      title={`net generation ${generation.digest} · scope integrated · ${currentness}`}
    >
      gen {generation.digest.slice(0, 8)} · {currentness} · integrated
    </span>
  );
}

function ChangeList({
  files,
  kind,
  active,
  onOpen,
  partnerHint,
  testPrefix,
}: {
  files: ChangedFile[];
  kind: "code" | "memory";
  active: { kind: "code" | "memory"; path: string; hasSidecar?: boolean } | null;
  onOpen: (kind: "code" | "memory", file: ChangedFile, withPartner?: boolean) => void;
  partnerHint: (file: ChangedFile) => boolean;
  testPrefix?: string;
}) {
  return (
    <div className={section}>
      <div className={sectionHead}>
        changed {kind === "code" ? "code" : "onboarding"} ({files.length})
      </div>
      {files.map((f) => (
        <div
          key={f.path}
          className={row}
          data-active={active?.kind === kind && active.path === f.path}
        >
          <button type="button" className={rowMain} onClick={() => onOpen(kind, f)}>
            <span className={statusChip}>{f.status}</span>
            <span className={pathText}>{f.path}</span>
            <Counts file={f} />
          </button>
          {partnerHint(f) ? (
            <button
              type="button"
              className={sidecarBtn}
              title={
                kind === "code"
                  ? "open with its sidecar (3rd column)"
                  : "open with its partner code file (3rd column)"
              }
              onClick={() => onOpen(kind, f, true)}
              data-testid={testPrefix}
            >
              {kind === "code" ? "◇" : "↔"}
            </button>
          ) : null}
        </div>
      ))}
    </div>
  );
}

// How much of the net ONE leaf accounts for, printed beside it (R33.2): both halves with their file
// count and their insertions/deletions, so the reviewer can attribute the master's total to a leaf
// and then open that leaf's own change-set from the same row.
function leafCountText(counters: MasterChangeset["leaves"][number]["counters"]): string {
  const half = (part: { files: number; insertions: number; deletions: number }) =>
    `${part.files} file(s) +${part.insertions} −${part.deletions}`;
  return `code ${half(counters.code)} · memory ${half(counters.memory)}`;
}

// One row per leaf the series read reported, under the net counters it is the attribution OF. A
// landed leaf opens its COMMITTED change-set -- the historical route L12 built, which needs no live
// worktree (R33.3) -- and a leaf the read reports as still working opens its working delta, which is
// the range that actually exists for it.
function LeafBreakdown({
  leaves,
  onOpenLeaf,
}: {
  leaves: MasterChangeset["leaves"];
  onOpenLeaf?: (leaf: string, mode: LeafMode) => void;
}) {
  if (leaves.length === 0) return null;
  return (
    <div className={section} data-testid="changeset-leaves">
      <div className={sectionHead}>by leaf ({leaves.length})</div>
      {leaves.map((leaf) => (
        <div key={leaf.leafId} className={row} data-testid="changeset-leaf-row">
          <button
            type="button"
            className={rowMain}
            data-leaf-id={leaf.leafId}
            data-leaf-state={leaf.state ?? ""}
            disabled={!onOpenLeaf}
            onClick={() =>
              onOpenLeaf?.(leaf.leafId, leaf.state === "working" ? "working" : "committed")
            }
          >
            <span className={pathText}>{leaf.leafId}</span>
            <span className={leafState}>{leaf.state ?? ""}</span>
            <span className={leafCounts} data-testid="changeset-leaf-counters">
              {leafCountText(leaf.counters)}
            </span>
          </button>
        </div>
      ))}
    </div>
  );
}

function useChangesetLoad(
  repo: string,
  scope: string | undefined,
  master: string | undefined,
  leaf: string | undefined,
  mode: LeafMode | undefined,
  generation: MasterNetPins | undefined,
  setData: (data: TaskChangeset | MasterChangeset | null) => void,
  setError: (error: ReviewFailure | null) => void,
  setActive: (active: { kind: "code" | "memory"; path: string; hasSidecar?: boolean } | null) => void,
  setDiff: (diff: FileDiff | null) => void,
  setPartner: (diff: FileDiff | null) => void,
): void {
  useEffect(() => {
    let live = true;
    setData(null);
    setError(null);
    setActive(null);
    setDiff(null);
    setPartner(null);
    const req = changesetListRequest(repo, scope, master, leaf, mode, generation);
    void req.then(
      (d) => live && setData(d),
      // The refusal is an ANSWER and is carried as one (the same doctrine as the change-set bar's
      // own counter read, L32/D01): the route publishes its code, its reason, the input it refused
      // and the next action, and all of it is kept so this pane can NAME what it cannot show rather
      // than printing a bare status. A cause that is not this route's refusal is named as the
      // failure it is rather than guessed into one.
      (cause: unknown) => live && setError(reviewProblemFromCause(cause)),
    );
    return () => {
      live = false;
    };
  }, [repo, scope, master, leaf, mode, generation, setData, setError, setActive, setDiff, setPartner]);
}

function useWorkingChangesetPoll(
  mode: LeafMode | undefined,
  leaf: string | undefined,
  repo: string,
  master: string | undefined,
  active: { kind: "code" | "memory"; path: string; hasSidecar?: boolean } | null,
  hasData: boolean,
  setData: (data: TaskChangeset | MasterChangeset | null) => void,
  setDiff: (diff: FileDiff | null) => void,
): void {
  useEffect(() => {
    if (mode !== "working" || !leaf || !hasData) return;
    let live = true;
    const m = master ?? "";
    let timer: number | undefined;
    const refresh = async () => {
      const listRequest = leafChangeset(repo, m, leaf, "working");
      const diffRequest = active
        ? leafFileDiff(repo, m, leaf, active.kind, active.path, "working")
        : Promise.resolve(null);
      const [listResult, diffResult] = await Promise.allSettled([listRequest, diffRequest]);
      if (!live) return;
      if (listResult.status === "fulfilled") setData(listResult.value);
      if (diffResult.status === "fulfilled" && diffResult.value) setDiff(diffResult.value);
      timer = window.setTimeout(refresh, 2500);
    };
    timer = window.setTimeout(refresh, 2500);
    return () => {
      live = false;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [mode, leaf, repo, master, active, hasData, setData, setDiff]);
}

// WHAT COULD NOT BE SHOWN, NAMED (R33.3). A refused read says which read it was (the owner's own code,
// kept in its own element so the status line still reads exactly as it always did), the reason in the
// owner's words, the input it refused and the next action it published -- never an empty pane that
// leaves the reader guessing whether there was nothing to show or nothing was read.
function ChangeSetRefusal({ error }: { error: ReviewFailure }) {
  return (
    <div
      className={placeholder}
      data-testid="pane-placeholder"
      data-review-state={error.token}
      data-review-code={error.code}
    >
      this change-set could not be shown:{" "}
      <span data-testid="changeset-refusal">
        {error.code}
        {error.httpStatus ? ` (${error.httpStatus})` : ""}
      </span>
      {" — "}
      {error.detail}
      {error.offendingInput ? ` — offending input: ${error.offendingInput}` : ""}
      {error.nextAction ? ` — next: ${error.nextAction}` : ""}
    </div>
  );
}

// The middle pane: the picked file's diff, or one of the two states that are NOT a missing selection —
// a change-set measured empty in both halves (named, because a measurement is not an absence), and
// the pick-a-file backdrop that says a file is waiting to be picked.
function ChangeSetMainPane({ diff, changed }: { diff: FileDiff | null; changed: number }) {
  if (diff) return <ChangeSetPane diff={diff} keyPrefix="changeset.main" />;
  if (changed === 0) {
    return (
      <div className={emptyNotice} data-testid="changeset-empty">
        no changed file in either half — this change-set is measured empty (code 0 file(s) · memory 0
        file(s)).
      </div>
    );
  }
  return (
    // No file picked yet: the same faint boomerang backdrop the File Viewer / Operations use.
    <div className={emptyHost}>
      {/* Brighter than the shared 0.14 default — the siege-tank clip reads darker; matches DualPane. */}
      <EmptyStateBackdrop src="/assets/sc2-siege-tank-boomerang.mp4" opacity={0.18}>
        Select a changed file
      </EmptyStateBackdrop>
    </div>
  );
}

// The changed files of one half plus, for a series read, the net's own per-leaf attribution beneath
// them: one row per leaf (R33.2), each opening that leaf's own range (R33.3).
function ChangeSetRail({
  data,
  active,
  onOpen,
  leaves,
  onOpenLeaf,
}: {
  data: TaskChangeset | MasterChangeset;
  active: { kind: "code" | "memory"; path: string; hasSidecar?: boolean } | null;
  onOpen: (kind: "code" | "memory", file: ChangedFile, withPartner?: boolean) => void;
  leaves: MasterChangeset["leaves"];
  onOpenLeaf?: (leaf: string, mode: LeafMode) => void;
}) {
  return (
    <div className={colList}>
      <ChangeList
        files={data.code ?? []}
        kind="code"
        active={active}
        onOpen={onOpen}
        partnerHint={(file) => file.hasSidecar === true}
        testPrefix="changeset-open-sidecar"
      />
      <ChangeList
        files={data.memory ?? []}
        kind="memory"
        active={active}
        onOpen={onOpen}
        partnerHint={(file) => partnerTargetFor("memory", file.path) !== null}
      />
      <LeafBreakdown leaves={leaves} onOpenLeaf={onOpenLeaf} />
    </div>
  );
}

function ChangeSetWorkspace({
  error,
  data,
  active,
  diff,
  partner,
  onOpen,
  leaves,
  onOpenLeaf,
}: {
  error: ReviewFailure | null;
  data: TaskChangeset | MasterChangeset | null;
  active: { kind: "code" | "memory"; path: string; hasSidecar?: boolean } | null;
  diff: FileDiff | null;
  partner: FileDiff | null;
  onOpen: (kind: "code" | "memory", file: ChangedFile, withPartner?: boolean) => void;
  leaves: MasterChangeset["leaves"];
  onOpenLeaf?: (leaf: string, mode: LeafMode) => void;
}) {
  if (error) return <ChangeSetRefusal error={error} />;
  if (!data) {
    return (
      <div className={placeholder} data-testid="pane-placeholder">
        Loading change-set…
      </div>
    );
  }
  return (
    <PanelGroup direction="horizontal" autoSaveId="changeset.outer" className={css({ flex: "1", minHeight: "0" })}>
      <Panel defaultSize={26} minSize={16}>
        <ChangeSetRail
          data={data}
          active={active}
          onOpen={onOpen}
          leaves={leaves}
          onOpenLeaf={onOpenLeaf}
        />
      </Panel>
      <PanelResizeHandle className={handle} />
      <Panel minSize={20}>
        <ChangeSetMainPane
          diff={diff}
          changed={(data.code?.length ?? 0) + (data.memory?.length ?? 0)}
        />
      </Panel>
      {partner ? (
        <>
          <PanelResizeHandle className={handle} />
          <Panel minSize={20}>
            <ChangeSetPane diff={partner} keyPrefix="changeset.partner" />
          </Panel>
        </>
      ) : null}
    </PanelGroup>
  );
}

export function ChangeSetViewer({
  repo,
  scope,
  master,
  leaf,
  mode,
  generation,
  onBack,
  onOpenLeaf,
}: ChangeSetTarget & { onBack: () => void; onOpenLeaf?: (target: ChangeSetTarget) => void }) {
  const [data, setData] = useState<TaskChangeset | MasterChangeset | null>(null);
  const [error, setError] = useState<ReviewFailure | null>(null);
  const [active, setActive] = useState<{ kind: "code" | "memory"; path: string; hasSidecar?: boolean } | null>(null);
  const [diff, setDiff] = useState<FileDiff | null>(null);
  const [partner, setPartner] = useState<FileDiff | null>(null);
  // Selection precedence leaf > master > scope: a `leaf` is one leaf's committed/working delta
  // (qualified by `master`); `master` alone is the series net; otherwise an enclosure `scope`.
  const isSeries = Boolean(master) && !leaf;
  const hasData = data !== null;
  const seriesMeta = seriesListMeta(isSeries, data);
  const leaves = seriesLeaves(isSeries, data);
  const boundGeneration = boundSeriesGeneration(isSeries, data, generation);

  useChangesetLoad(repo, scope, master, leaf, mode, generation, setData, setError, setActive, setDiff, setPartner);

  // L4a: the WORKING view is the LIVE uncommitted delta, so it must not be a frozen snapshot taken
  // when the button was clicked. Refresh the change-set after each prior refresh settles so a file edited *after* opening
  // appears in the list (and the counters track), AND re-fetch the file currently open in the diff
  // column so an edit to the file you are LOOKING AT updates in place. The open-diff re-fetch is cheap
  // and non-disruptive: CodeMirror only rebuilds when the before/after content actually changed, so an
  // unchanged poll is a no-op (no flicker / scroll-reset) — it only re-renders when that file is the
  // one edited, which is exactly when you want it to. Only `working` polls — committed/series/scope
  // are immutable snapshots of committed state. (A server push would need a worktree watcher + SSE; a
  // settle-then-schedule loop is self-contained and enough on localhost.)
  useWorkingChangesetPoll(mode, leaf, repo, master, active, hasData, setData, setDiff);

  // Each selector diffs its own range, all into the same MergeView: a leaf its committed/working
  // range, `master` the NET series range between the bound generation's endpoints, an enclosure
  // `scope` its base -> worktree.
  const loadDiff = (kind: "code" | "memory", path: string) =>
    diffRequestFor(repo, scope, master, leaf, mode, boundGeneration, kind, path);

  const open = (kind: "code" | "memory", file: Row, withPartner = false) => {
    if (!leaf && !master && !scope) return;
    setActive({ kind, path: file.path, hasSidecar: file.hasSidecar });
    setDiff(null);
    setPartner(null);
    void loadDiff(kind, file.path).then(setDiff, () => setDiff(null));
    const partnerRef = withPartner ? partnerTargetFor(kind, file.path, file.hasSidecar) : null;
    if (partnerRef) void loadDiff(partnerRef.kind, partnerRef.path).then(setPartner, () => setPartner(null));
  };

  const counters = data?.counters;
  const headerLabel = headerLabelFor(leaf, master, scope, isSeries, mode);

  return (
    <div className={screen} data-testid="changeset-viewer">
      <header className={header}>
        <button type="button" className={back} onClick={onBack} data-testid="changeset-back">
          ← back
        </button>
        <span className={title}>change-set · {headerLabel}</span>
        <SeriesGenerationTag meta={seriesMeta} />
        {counters ? <CounterRow counters={counters} /> : null}
      </header>

      <ChangeSetWorkspace
        error={error}
        data={data}
        active={active}
        diff={diff}
        partner={partner}
        onOpen={open}
        leaves={leaves}
        onOpenLeaf={
          onOpenLeaf ? (id, leafMode) => onOpenLeaf({ repo, master, leaf: id, mode: leafMode }) : undefined
        }
      />
    </div>
  );
}
