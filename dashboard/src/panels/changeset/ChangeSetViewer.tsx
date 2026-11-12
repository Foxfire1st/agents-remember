// The Change-Set Viewer screen (L4). Opened as a task-scoped takeover from a DetailPanel button
// (Cockpit clears it via `onBack`, restoring the railed Operations view). Layout:
//   column 1  two rows — changed CODE files / changed ONBOARDING files (counts + status)
//   column 2  the selected file's diff (ChangeSetPane, always visible once a row is picked)
//   column 3  the code<->sidecar partner, opened from a "split" affordance
// The target selects the range (precedence leaf > master > scope), all rendered the same way and all
// inspectable per file: a `scope` (one active enclosure) = base->worktree; a `master` = the series
// NET base->tip; a `leaf` (+ `mode`) = that leaf's committed (base->code_commit) or working
// (HEAD->worktree, uncommitted) delta — the L4a doc-reader views, which need no live enclosure.
import { useEffect, useRef, useState } from 'react';
import { Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels';

import { css } from '../../../styled-system/css';
import {
  type ChangedFile,
  type FileDiff,
  type LeafMode,
  type MasterChangeset,
  type MasterNetPins,
  type TaskChangeset,
  comparisonFileDiff,
  fileDiff,
  leafFileDiff,
  masterFileDiff,
} from '../../data/changeset';
import { type ReviewFailure, reviewProblemFromCause } from '../../data/review';
import { useInspectionCounter } from './useInspectionCounter';
import { MasterCounterStatus, CounterRow, SeriesGenerationTag } from './MasterCounterStatus';
import { useChangesetLoad } from './useInspectionChangeset';
import { useWorkingChangesetPoll } from './useWorkingChangeset';
import { EmptyStateBackdrop } from '../EmptyStateBackdrop';
import { SidebarToggle, sidebarHandleProps, sidebarPanelProps, useSidebar } from '../../grammar/SidebarToggle';
import type { ReviewSelectorKind } from '../../data/review';
import {
  reviewFileBadges,
  filteredChangeSet,
  type ReviewChangeContext,
  type ReviewChangeFilter,
} from './reviewChangeSet';
import { ChangeSetPane } from './ChangeSetPane';
import { ReviewFilters } from './ReviewFilters';

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
  comparison?: number;
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

type ViewerProps = ChangeSetTarget & {
  onBack: () => void;
  onOpenLeaf?: (target: ChangeSetTarget) => void;
  reviewContext?: ReviewChangeContext;
};
type ActiveFile = { kind: 'code' | 'memory'; path: string; hasSidecar?: boolean };

const screen = css({
  height: '100%',
  minHeight: '0',
  display: 'flex',
  flexDirection: 'column',
  background: 'bg',
});
const header = css({
  flexShrink: 0,
  display: 'flex',
  alignItems: 'center',
  gap: '0.8rem',
  paddingInline: '0.8rem',
  paddingBlock: '0.4rem',
  borderBottomWidth: '1px',
  borderBottomStyle: 'solid',
  borderBottomColor: 'grid',
  background: 'bgPanel',
});
const back = css({
  fontFamily: 'mono',
  fontSize: '0.74rem',
  color: 'amber',
  background: 'transparent',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '2px',
  paddingInline: '0.5rem',
  paddingBlock: '0.15rem',
  cursor: 'pointer',
  _hover: { borderColor: 'amber' },
});
const title = css({ fontSize: '0.82rem', color: 'ink', fontWeight: '600' });
const ins = css({ color: 'mint' });
const del = css({ color: 'amber' });
const colList = css({
  height: '100%',
  minHeight: '0',
  display: 'flex',
  flexDirection: 'column',
  background: 'bgPanel',
});
const section = css({ flex: '1', minHeight: '0', overflow: 'auto' });
const sectionHead = css({
  position: 'sticky',
  top: '0',
  background: 'bg',
  paddingInline: '0.6rem',
  paddingBlock: '0.25rem',
  fontSize: '0.68rem',
  letterSpacing: '0.05em',
  textTransform: 'uppercase',
  color: 'amber',
  borderBottomWidth: '1px',
  borderBottomStyle: 'solid',
  borderBottomColor: 'grid',
});
const row = css({
  display: 'flex',
  alignItems: 'center',
  gap: '0.3rem',
  paddingInline: '0.6rem',
  // Match the File Viewer tree's selected/hover amber wash — the old `background: bg` active state was
  // indistinguishable from the panel, so the selected file looked unselected.
  _hover: { background: 'color-mix(in oklab, var(--amber) 12%, transparent)' },
  '&[data-active=true]': { background: 'color-mix(in oklab, var(--amber) 20%, transparent)' },
});
const rowMain = css({
  flex: '1',
  minWidth: '0',
  display: 'flex',
  alignItems: 'center',
  gap: '0.4rem',
  textAlign: 'left',
  paddingBlock: '0.2rem',
  fontSize: '0.74rem',
  fontFamily: 'mono',
  color: 'ink',
  background: 'transparent',
  border: '0',
  cursor: 'pointer',
  _hover: { color: 'amber' },
  _disabled: { cursor: 'default' },
});
const pathText = css({ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' });
const statusChip = css({ width: '1.1em', textAlign: 'center', color: 'cyan' });
const counts = css({ marginLeft: 'auto', display: 'flex', gap: '0.4rem' });
const sidecarBtn = css({
  fontSize: '0.64rem',
  color: 'cyan',
  border: '1px solid token(colors.grid)',
  borderRadius: '2px',
  paddingInline: '0.25rem',
  background: 'transparent',
  cursor: 'pointer',
  _hover: { borderColor: 'cyan' },
});
const handle = css({
  width: '3px',
  flexShrink: '0',
  background: 'grid',
  cursor: 'col-resize',
  _hover: { background: 'amber' },
});
// The per-leaf state chip a series read reports ("committed" / "working") — what the row's click
// resolves, so the reader knows which record opens before opening it.
const leafState = css({
  flex: 'none',
  fontFamily: 'mono',
  fontSize: '0.64rem',
  color: 'muted',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '2px',
  paddingInline: '0.25rem',
});
const leafCounts = css({
  marginLeft: 'auto',
  fontFamily: 'mono',
  fontSize: '0.66rem',
  color: 'muted',
  whiteSpace: 'nowrap',
});
// The measured-empty statement, printed in the pane instead of the pick-a-file backdrop: an empty
// change-set is a measurement, and a reader is told which measurement they are looking at.
const emptyNotice = css({
  height: '100%',
  display: 'grid',
  placeItems: 'center',
  padding: '1rem',
  color: 'muted',
  fontSize: '0.8rem',
  textAlign: 'center',
});
const placeholder = css({
  height: '100%',
  display: 'grid',
  placeItems: 'center',
  padding: '1rem',
  color: 'muted',
  fontSize: '0.8rem',
  textAlign: 'center',
});
// EmptyStateBackdrop's flex:1 canvas needs a flex-column host to fill the diff Panel.
const emptyHost = css({ height: '100%', minHeight: '0', display: 'flex', flexDirection: 'column' });

type Row = ChangedFile & { leafCount?: number };

function Counts({ file }: { file: Row }) {
  return (
    <span className={counts}>
      <span className={ins}>+{file.insertions ?? '·'}</span>
      <span className={del}>−{file.deletions ?? '·'}</span>
    </span>
  );
}

// A memory path under onboarding/ for a 1:1 sidecar -> its partner code path (strip onboarding/ + .md).
function partnerCodePath(memPath: string): string | null {
  if (!memPath.startsWith('onboarding/') || !memPath.endsWith('.md')) return null;
  const base = memPath.slice('onboarding/'.length, -3);
  if (base.endsWith('/overview') || base.endsWith('/entities') || base.endsWith('.index'))
    return null;
  return base;
}

function diffRequestFor(
  repo: string,
  scope: string | undefined,
  master: string | undefined,
  leaf: string | undefined,
  mode: LeafMode | undefined,
  generation: MasterNetPins | undefined,
  kind: 'code' | 'memory',
  path: string,
  comparison?: number,
): Promise<FileDiff | null> {
  if (comparison !== undefined)
    return comparisonFileDiff(repo, master ?? '', leaf ?? '', comparison, kind, path);
  return leaf
    ? leafFileDiff(repo, master ?? '', leaf, kind, path, mode ?? 'committed')
    : master
      ? masterFileDiff(repo, master, kind, path, generation ?? {})
      : fileDiff(repo, scope ?? '', kind, path);
}

function partnerTargetFor(
  kind: 'code' | 'memory',
  path: string,
  hasSidecar?: boolean,
): { kind: 'code' | 'memory'; path: string } | null {
  if (kind === 'code') {
    return hasSidecar ? { kind: 'memory', path: `onboarding/${path}.md` } : null;
  }
  const code = partnerCodePath(path);
  return code ? { kind: 'code', path: code } : null;
}

function headerLabelFor(
  leaf: string | undefined,
  master: string | undefined,
  scope: string | undefined,
  isSeries: boolean,
  mode: LeafMode | undefined,
): string {
  if (leaf) return mode === 'working' ? `working · ${leaf} · uncommitted` : `committed · ${leaf}`;
  return isSeries ? `series ${master} · net since series start` : (scope ?? '');
}

function ChangeList({
  files,
  kind,
  active,
  onOpen,
  partnerHint,
  testPrefix,
  reviewContext,
}: {
  files: ChangedFile[];
  kind: 'code' | 'memory';
  active: { kind: 'code' | 'memory'; path: string; hasSidecar?: boolean } | null;
  onOpen: (kind: 'code' | 'memory', file: ChangedFile, withPartner?: boolean) => void;
  partnerHint: (file: ChangedFile) => boolean;
  testPrefix?: string;
  reviewContext?: ReviewChangeContext;
}) {
  const badges = reviewContext ? reviewFileBadges(reviewContext) : new Map<string, string>();
  return (
    <div className={section}>
      <div className={sectionHead}>
        changed {kind === 'code' ? 'code' : 'onboarding'} ({files.length})
      </div>
      {kind === 'code' && reviewContext?.page.filter === 'all'
        ? reviewContext.inventory.unrepresentable_paths.map((path) => (
            <p key={path.path_bytes} style={{ overflowWrap: 'anywhere' }}>
              {path.path_bytes} · cannot open: {path.detail}
            </p>
          ))
        : null}
      {files.map((f) => (
        <div
          key={f.path}
          className={row}
          data-active={active?.kind === kind && active.path === f.path}
        >
          <button type="button" className={rowMain} onClick={() => onOpen(kind, f)}>
            <span className={statusChip}>{f.status}</span>
            <span className={pathText}>{f.path}</span>
            {badges.has(f.path) ? <span>{badges.get(f.path)}</span> : null}
            <Counts file={f} />
          </button>
          {partnerHint(f) ? (
            <button
              type="button"
              className={sidecarBtn}
              title={
                kind === 'code'
                  ? 'open with its sidecar (3rd column)'
                  : 'open with its partner code file (3rd column)'
              }
              onClick={() => onOpen(kind, f, true)}
              data-testid={testPrefix}
            >
              {kind === 'code' ? '◇' : '↔'}
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
function leafCountText(counters: MasterChangeset['leaves'][number]['counters']): string {
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
  leaves: MasterChangeset['leaves'];
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
            data-leaf-state={leaf.state ?? ''}
            disabled={!onOpenLeaf}
            onClick={() =>
              onOpenLeaf?.(leaf.leafId, leaf.state === 'working' ? 'working' : 'committed')
            }
          >
            <span className={pathText}>{leaf.leafId}</span>
            <span className={leafState}>{leaf.state ?? ''}</span>
            <span className={leafCounts} data-testid="changeset-leaf-counters">
              {leafCountText(leaf.counters)}
            </span>
          </button>
        </div>
      ))}
    </div>
  );
}

function ChangeSetRefusal({ error }: { error: ReviewFailure }) {
  return (
    <div
      className={placeholder}
      data-testid="pane-placeholder"
      data-review-state={error.token}
      data-review-code={error.code}
    >
      this change-set could not be shown:{' '}
      <span data-testid="changeset-refusal">
        {error.code}
        {error.httpStatus ? ` (${error.httpStatus})` : ''}
      </span>
      {' — '}
      {error.detail}
      {error.offendingInput ? ` — offending input: ${error.offendingInput}` : ''}
      {error.nextAction ? ` — next: ${error.nextAction}` : ''}
    </div>
  );
}

// The middle pane: the picked file's diff, or one of the two states that are NOT a missing selection —
// a change-set measured empty in both halves (named, because a measurement is not an absence), and
// the pick-a-file backdrop that says a file is waiting to be picked.
function ChangeSetMainPane({
  diff,
  changed,
  error,
  narrow,
  filtered,
}: {
  diff: FileDiff | null;
  changed: number;
  filtered?: boolean;
  error?: ReviewFailure | null;
  narrow?: boolean;
}) {
  if (error) return <ChangeSetRefusal error={error} />;
  if (diff) return <ChangeSetPane diff={diff} keyPrefix="changeset.main" narrow={narrow} />;
  if (changed === 0) {
    return (
      <div className={emptyNotice} data-testid="changeset-empty">
        {filtered
          ? 'No changed file in this filter.'
          : 'no changed file in either half — this change-set is measured empty (code 0 file(s) · memory 0 file(s)).'}
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
  reviewContext,
}: {
  data: TaskChangeset | MasterChangeset;
  active: { kind: 'code' | 'memory'; path: string; hasSidecar?: boolean } | null;
  onOpen: (kind: 'code' | 'memory', file: ChangedFile, withPartner?: boolean) => void;
  leaves: MasterChangeset['leaves'];
  onOpenLeaf?: (leaf: string, mode: LeafMode) => void;
  reviewContext?: ReviewChangeContext;
}) {
  return (
    <div className={colList}>
      <ChangeList
        files={data.code ?? []}
        kind="code"
        active={active}
        onOpen={onOpen}
        partnerHint={(file) => file.hasSidecar === true}
        reviewContext={reviewContext}
        testPrefix="changeset-open-sidecar"
      />
      {reviewContext && reviewContext.page.filter !== 'all' ? null : (
        <ChangeList
          files={data.memory ?? []}
          kind="memory"
          active={active}
          onOpen={onOpen}
          partnerHint={(file) => partnerTargetFor('memory', file.path) !== null}
        />
      )}
      <LeafBreakdown leaves={leaves} onOpenLeaf={onOpenLeaf} />
    </div>
  );
}

function ChangeSetWorkspace({
  diffError,
  narrow,
  error,
  data,
  active,
  diff,
  partner,
  onOpen,
  leaves,
  onOpenLeaf,
  reviewContext,
  railOpen = true,
}: {
  // The changed-file list's shared toggle hid it (MIK-R89 v2 rule 8): it stays mounted, out of
  // the row, and the diff takes its width. Narrow, it is the stacked list above the diff.
  railOpen?: boolean;
  error: ReviewFailure | null;
  data: TaskChangeset | MasterChangeset | null;
  active: { kind: 'code' | 'memory'; path: string; hasSidecar?: boolean } | null;
  diff: FileDiff | null;
  diffError?: ReviewFailure | null;
  narrow?: boolean;
  partner: FileDiff | null;
  onOpen: (kind: 'code' | 'memory', file: ChangedFile, withPartner?: boolean) => void;
  leaves: MasterChangeset['leaves'];
  onOpenLeaf?: (leaf: string, mode: LeafMode) => void;
  reviewContext?: ReviewChangeContext;
}) {
  if (error) return <ChangeSetRefusal error={error} />;
  if (!data) {
    return (
      <div className={placeholder} data-testid="pane-placeholder">
        Loading change-set…
      </div>
    );
  }
  const railSize = narrow
    ? { defaultSize: 35, minSize: 10, maxSize: 40 }
    : { defaultSize: 26, minSize: 16 };
  return (
    <PanelGroup
      direction={narrow ? 'vertical' : 'horizontal'}
      autoSaveId={narrow ? 'changeset.narrow' : 'changeset.outer'}
      className={css({ flex: '1', minHeight: '0' })}
    >
      <Panel {...railSize} {...sidebarPanelProps(railOpen)}>
        <ChangeSetRail
          data={data}
          active={active}
          onOpen={onOpen}
          leaves={leaves}
          onOpenLeaf={onOpenLeaf}
          reviewContext={reviewContext}
        />
      </Panel>
      <PanelResizeHandle className={handle} {...sidebarHandleProps(railOpen)} />
      <Panel minSize={20}>
        <ChangeSetMainPane
          diff={diff}
          error={diffError}
          narrow={narrow}
          changed={data.code.length + data.memory.length}
          filtered={Boolean(reviewContext && reviewContext.page.filter !== 'all')}
        />
      </Panel>
      {partner ? (
        <>
          <PanelResizeHandle className={handle} />
          <Panel minSize={20}>
            <ChangeSetPane diff={partner} keyPrefix="changeset.partner" narrow={narrow} />
          </Panel>
        </>
      ) : null}
    </PanelGroup>
  );
}

function useChangesetState({
  repo,
  scope,
  master,
  leaf,
  mode,
  generation,
  comparison,
  reviewContext,
  onBack,
}: ViewerProps) {
  const [data, setData] = useState<TaskChangeset | MasterChangeset | null>(null);
  const [error, setError] = useState<ReviewFailure | null>(null);
  const [active, setActive] = useState<ActiveFile | null>(null);
  const [diff, setDiff] = useState<FileDiff | null>(null);
  const [filter, setFilter] = useState<ReviewChangeFilter>(reviewContext?.page.filter ?? 'all');
  const [diffError, setDiffError] = useState<ReviewFailure | null>(null);
  const narrowViewport = useNarrowViewer();
  const narrow = comparison !== undefined && narrowViewport;
  useComparisonBack(comparison, onBack);
  const [partner, setPartner] = useState<FileDiff | null>(null);
  // Selection precedence leaf > master > scope: a `leaf` is one leaf's committed/working delta
  // (qualified by `master`); `master` alone is the series net; otherwise an enclosure `scope`.
  const counter = useInspectionCounter({ repo, master, leaf, generation }, data, error);
  const { seriesMeta, leaves, boundGeneration } = counter;

  useChangesetLoad(
    repo,
    scope,
    master,
    leaf,
    mode,
    boundGeneration,
    setData,
    setError,
    setActive,
    setDiff,
    setPartner,
    comparison,
    counter.enabled,
  );

  // L4a: the WORKING view is the LIVE uncommitted delta, so it must not be a frozen snapshot taken
  // when the button was clicked. Refresh the change-set after each prior refresh settles so a file edited *after* opening
  // appears in the list (and the counters track), AND re-fetch the file currently open in the diff
  // column so an edit to the file you are LOOKING AT updates in place. The open-diff re-fetch is cheap
  // and non-disruptive: CodeMirror only rebuilds when the before/after content actually changed, so an
  // unchanged poll is a no-op (no flicker / scroll-reset) — it only re-renders when that file is the
  // one edited, which is exactly when you want it to. Only `working` polls — committed/series/scope
  // are immutable snapshots of committed state. (A server push would need a worktree watcher + SSE; a
  // settle-then-schedule loop is self-contained and enough on localhost.)
  useWorkingChangesetPoll({ mode, leaf, repo, master, comparison }, active, {
    setData,
    setDiff,
    setError,
    setDiffError,
  });

  // Each selector diffs its own range, all into the same MergeView: a leaf its committed/working
  // range, `master` the NET series range between the bound generation's endpoints, an enclosure
  // `scope` its base -> worktree.
  const loadDiff = (kind: 'code' | 'memory', path: string) =>
    diffRequestFor(repo, scope, master, leaf, mode, boundGeneration, kind, path, comparison);

  const { open, clear } = useChangedFileOpen([leaf, master, scope].some(Boolean), loadDiff, {
    setActive,
    setDiff,
    setPartner,
    setDiffError,
  });

  const shownContext = reviewContext
    ? { ...reviewContext, page: { ...reviewContext.page, filter } }
    : undefined;
  const shown =
    data && shownContext ? filteredChangeSet(data as TaskChangeset, shownContext, filter) : data;
  useComparisonFirstFile(comparison, data, reviewContext, filter, { open, clear });
  const counters = counter.counters;
  const headerLabel = viewerLabel({ repo, leaf, master, scope, mode, comparison });

  return {
    data,
    error: counter.error,
    counterAttribution: counter.attribution,
    active,
    diff,
    partner,
    filter,
    setFilter,
    diffError,
    narrow,
    seriesMeta,
    leaves,
    open,
    shownContext,
    shown,
    counters,
    headerLabel,
  };
}

function viewerLabel(target: ChangeSetTarget): string {
  if (target.comparison !== undefined) return `comparison ${target.comparison} · ${target.leaf}`;
  return headerLabelFor(
    target.leaf,
    target.master,
    target.scope,
    Boolean(target.master) && !target.leaf,
    target.mode,
  );
}

function useChangedFileOpen(
  eligible: boolean,
  loadDiff: (kind: 'code' | 'memory', path: string) => Promise<FileDiff | null>,
  actions: {
    setActive: (value: ActiveFile | null) => void;
    setDiff: (value: FileDiff | null) => void;
    setPartner: (value: FileDiff | null) => void;
    setDiffError: (value: ReviewFailure | null) => void;
  },
) {
  const { setActive, setDiff, setPartner, setDiffError } = actions;
  const requestId = useRef(0);
  const clear = () => {
    requestId.current += 1;
    setActive(null);
    setDiff(null);
    setPartner(null);
    setDiffError(null);
  };
  const open = (kind: 'code' | 'memory', file: Row, withPartner = false) => {
    if (!eligible) return;
    const requested = ++requestId.current;
    setDiffError(null);
    setActive({ kind, path: file.path, hasSidecar: file.hasSidecar });
    setDiff(null);
    setPartner(null);
    void loadDiff(kind, file.path).then(
      (value) => {
        if (requested === requestId.current) setDiff(value);
      },
      (cause) => {
        if (requested === requestId.current) setDiffError(reviewProblemFromCause(cause));
      },
    );
    const partnerRef = withPartner ? partnerTargetFor(kind, file.path, file.hasSidecar) : null;
    if (partnerRef)
      void loadDiff(partnerRef.kind, partnerRef.path).then(setPartner, () => setPartner(null));
  };

  return { open, clear };
}

function firstComparisonFile(
  data: TaskChangeset | MasterChangeset | null,
  context: ReviewChangeContext | undefined,
  filter: ReviewChangeFilter,
): { file: ChangedFile; kind: 'code' | 'memory' } | null {
  if (!data) return null;
  const shown = context ? filteredChangeSet(data as TaskChangeset, context, filter) : data;
  const kind = context?.page.kind ?? 'code';
  const list = kind === 'memory' ? shown.memory : shown.code;
  const file = list.find((row) => row.path === context?.page.path) ?? list[0];
  if (file) return { file, kind };
  return shown.memory[0] ? { file: shown.memory[0], kind: 'memory' } : null;
}
function useComparisonFirstFile(
  comparison: number | undefined,
  data: TaskChangeset | MasterChangeset | null,
  context: ReviewChangeContext | undefined,
  filter: ReviewChangeFilter,
  actions: { open: (kind: 'code' | 'memory', file: ChangedFile) => void; clear: () => void },
) {
  const target = firstComparisonFile(data, context, filter);
  const actionsRef = useRef(actions);
  actionsRef.current = actions;
  const targetRef = useRef(target);
  targetRef.current = target;
  const path = target?.file.path;
  const kind = target?.kind;
  useEffect(() => {
    if (comparison === undefined) return;
    if (!targetRef.current) actionsRef.current.clear();
    else actionsRef.current.open(targetRef.current.kind, targetRef.current.file);
  }, [comparison, path, kind, filter]);
}
function useNarrowViewer(): boolean {
  const [narrow, setNarrow] = useState(() => window.matchMedia('(max-width: 40rem)').matches);
  useEffect(() => {
    const media = window.matchMedia('(max-width: 40rem)');
    const update = () => setNarrow(media.matches);
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);
  return narrow;
}
function useComparisonBack(comparison: number | undefined, onBack: () => void): void {
  useEffect(() => {
    if (comparison === undefined) return;
    const escape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      event.stopPropagation();
      onBack();
    };
    document.addEventListener('keydown', escape, true);
    return () => document.removeEventListener('keydown', escape, true);
  }, [comparison, onBack]);
}
function ComparisonTreesTag({ data }: { data: TaskChangeset | MasterChangeset | null }) {
  if (!data || !('trees' in data) || !data.trees) return null;
  return (
    <span>
      {data.trees.codeBase.slice(0, 8)} → {data.trees.codeTip.slice(0, 8)}
    </span>
  );
}
export function ChangeSetViewer(
  props: ChangeSetTarget & {
    onBack: () => void;
    onOpenLeaf?: (target: ChangeSetTarget) => void;
    reviewContext?: ReviewChangeContext;
  },
) {
  const { repo, master, comparison, onBack, onOpenLeaf, reviewContext } = props;
  const {
    data,
    error,
    active,
    diff,
    partner,
    filter,
    setFilter,
    diffError,
    narrow,
    seriesMeta,
    leaves,
    open,
    shownContext,
    shown,
    counters,
    counterAttribution,
    headerLabel,
  } = useChangesetState(props);
  const rail = useSidebar('changeset', 'left');
  return (
    <div className={screen} data-testid="changeset-viewer" data-kbzone="changeset">
      <header className={header}>
        <button type="button" className={back} onClick={onBack} data-testid="changeset-back">
          {comparison === undefined ? '← back' : '← Back to intent review'}
        </button>
        <SidebarToggle
          side="left"
          open={rail.open}
          onToggle={rail.toggle}
          name="Changed files sidebar"
          testId="sidebar-toggle-changeset-left"
        />
        <span className={title}>change-set · {headerLabel}</span>
        <ComparisonTreesTag data={data} />
        <SeriesGenerationTag meta={seriesMeta} />
        {counters ? <CounterRow counters={counters} /> : null}
        {counterAttribution ? <MasterCounterStatus value={counterAttribution} /> : null}
      </header>

      <ReviewFilters context={reviewContext} data={data} filter={filter} onFilter={setFilter} />
      <ChangeSetWorkspace
        railOpen={rail.open}
        error={error}
        data={shown}
        diffError={diffError}
        narrow={narrow}
        reviewContext={shownContext}
        active={active}
        diff={diff}
        partner={partner}
        onOpen={open}
        leaves={leaves}
        onOpenLeaf={
          onOpenLeaf
            ? (id, leafMode) => onOpenLeaf({ repo, master, leaf: id, mode: leafMode })
            : undefined
        }
      />
    </div>
  );
}
