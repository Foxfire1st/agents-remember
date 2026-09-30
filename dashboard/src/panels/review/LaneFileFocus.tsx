// One changed file opened from the unexplained-changes lane (MIK-R32): the actual diff, focused on the
// hunks the destination is about, with the full file one control away.
//
// The hunks, their classes and their reasons are the server's per-file classification; the text is
// the landed source-content read of the listed generation (exact side blobs, the same read the source
// explorer makes and caches). Each focused hunk is its own diff window cut on the side line numbers
// the server named, so a displayed change region can never merge two of the server's hunks under one
// mark. The gate's own items for the file (MIK-R10, with the history rows that answer them) are shown
// beside, grouped as the leaf panel groups them; nothing here assesses, approves or explains a change.
import { useState } from 'react';

import { css } from '../../../styled-system/css';
import type {
  ReviewChangedFile,
  ReviewSourceExpansion,
  ReviewSourceInventory,
} from '../../data/review';
import {
  type LaneDestinationName,
  type ReviewFileClassification,
  type ReviewLaneHunk,
  useReviewFileClassification,
} from '../../data/reviewLane';
import type { ReviewWorklistView } from '../../data/reviewTrees';
import { DiffPane } from '../changeset/DiffPane';
import { FilePane } from '../file-viewer/FilePane';
import {
  CLASS_LABELS,
  FOCUSED_HUNK_LIMIT,
  type HunkWindow,
  focusedHunks,
  hunkWindow,
  sideLines,
  spanLabel,
} from './laneFocus';
import { UnexplainedGroups } from './LeafKnowledgeChanges';
import { ReviewProblemBlock } from './ReviewOutcome';
import { SourceContent, useSourceContentRead } from './SourceContent';
import type { DiffLayout } from './SourceExplorer';
import { worklistGroups } from './worklistGroups';

const focus = css({ display: 'grid', gap: '0.6rem', padding: '0.5rem 0 0.2rem', minWidth: 0 });
const muted = css({ color: 'muted', fontSize: '0.75rem', margin: '0.15rem 0' });
const hunkShell = css({
  border: '1px solid var(--grid)',
  borderRadius: '3px',
  background: 'bg',
  minWidth: 0,
});
const hunkHead = css({
  display: 'flex',
  gap: '0.5rem',
  flexWrap: 'wrap',
  alignItems: 'baseline',
  padding: '0.45rem 0.7rem',
  fontSize: '0.75rem',
});
const badge = css({
  border: '1px solid var(--amber)',
  color: 'amber',
  borderRadius: '2px',
  padding: '0.05rem 0.4rem',
  fontSize: '0.7rem',
  whiteSpace: 'nowrap',
});
const reasons = css({ margin: '0 0.7rem 0.4rem', paddingLeft: '1rem', fontSize: '0.72rem' });

// The gate's own items for the lane's files come from the leaf-wide read, which is slower than the
// lane: it is still reading, it answered for this comparison, or it gives none (with why).
export type GateRead =
  | { state: 'reading' }
  | { state: 'read'; worklist: ReviewWorklistView }
  | { state: 'none'; detail: string };

export interface LaneTask {
  repo: string;
  master: string;
  leaf: string;
  comparison: number | undefined;
}

export function LaneFileFocus({
  task,
  path,
  destination,
  inventory,
  layout,
  gate,
}: {
  task: LaneTask;
  path: string;
  destination: LaneDestinationName;
  inventory: ReviewSourceInventory;
  layout: DiffLayout;
  gate: GateRead;
}) {
  const read = useReviewFileClassification(
    task.repo,
    task.master,
    task.leaf,
    task.comparison,
    path,
  );
  if (read === null || read.phase === 'loading')
    return (
      <p className={muted} data-testid="review-lane-file-loading">
        classifying {path}…
      </p>
    );
  if (read.phase === 'unavailable')
    return (
      <ReviewProblemBlock
        origin="failure"
        subject="this file's classification"
        problem={read.problem}
      />
    );
  const file = read.value;
  const hunks = focusedHunks(file, destination);
  const entry = inventory.entries.find((one) => one.path === path) ?? fallbackEntry(file);
  const generation = {
    before: inventory.before_code_tree_id,
    after: inventory.after_code_tree_id,
  };
  return (
    <div
      className={focus}
      data-testid="review-lane-file-focus"
      data-path={path}
      data-bucket={file.bucket}
    >
      <FileFacts file={file} />
      {generation.before && generation.after ? (
        <>
          <FocusedDiffs
            task={task}
            path={path}
            generation={{ before: generation.before, after: generation.after }}
            hunks={hunks}
            layout={layout}
          />
          <FullFile
            task={task}
            entry={entry}
            generation={{ before: generation.before, after: generation.after }}
            layout={layout}
          />
        </>
      ) : (
        <p className={muted}>The listing names no code generation, so no content can be opened.</p>
      )}
      <GateItems gate={gate} path={path} />
    </div>
  );
}

function fallbackEntry(file: ReviewFileClassification): ReviewChangedFile {
  return {
    path: file.path,
    status: file.status as ReviewChangedFile['status'],
    content: file.content as ReviewChangedFile['content'],
    mode_change: file.mode_change,
  };
}

function FileFacts({ file }: { file: ReviewFileClassification }) {
  const counts = file.counts;
  return (
    <div data-testid="review-lane-file-facts">
      <p className={muted}>
        {file.bucket.replace(/_/g, ' ')} · {counts.hunks} hunk(s): {counts.linked} linked ·{' '}
        {counts.unexplained} unexplained · {counts.attribution_unknown} attribution unknown
      </p>
      <details data-testid="review-lane-file-reason">
        <summary className={muted}>Why this attribution</summary>
        <p className={muted}>{file.reason}</p>
      </details>
      {file.non_text ? (
        <p className={muted} data-testid="review-lane-non-text" data-gate={file.non_text.gate}>
          non-text change ({file.non_text.content}
          {file.non_text.mode_change ? ', mode changed' : ''}) · classified at file level · the gate
          holds it {file.non_text.gate === 'unknown' ? 'of unknown linkage' : file.non_text.gate}
        </p>
      ) : null}
      {file.sides
        .filter((side) => side.knowledge === 'unavailable')
        .map((side) => (
          <p key={side.side} className={muted} data-testid="review-lane-side-unavailable">
            {side.side} knowledge unavailable: {side.detail}
          </p>
        ))}
    </div>
  );
}

function FocusedDiffs({
  task,
  path,
  generation,
  hunks,
  layout,
}: {
  task: LaneTask;
  path: string;
  generation: { before: string; after: string };
  hunks: ReviewLaneHunk[];
  layout: DiffLayout;
}) {
  const { result, problem, retry } = useSourceContentRead({
    repo: task.repo,
    master: task.master,
    leaf: task.leaf,
    path,
    beforeCodeTreeId: generation.before,
    afterCodeTreeId: generation.after,
  });
  if (!hunks.length) return null;
  if (problem !== null)
    return (
      <ReviewProblemBlock
        origin="failure"
        subject="this file's content"
        problem={problem}
        onRetry={retry}
      />
    );
  if (result === null) return <p className={muted}>opening {path} at the listed generation…</p>;
  const expansion = result.expansion;
  if (!expansion)
    return <p className={muted}>{result.refusal?.detail ?? 'The content could not be opened.'}</p>;
  const shown = hunks.slice(0, FOCUSED_HUNK_LIMIT);
  return (
    <div data-testid="review-lane-hunks" data-hunks={hunks.length}>
      {shown.map((hunk) => (
        <FocusedHunk
          key={`${hunk.before.start}:${hunk.before.count}:${hunk.after.start}:${hunk.after.count}`}
          hunk={hunk}
          expansion={expansion}
          layout={layout}
        />
      ))}
      {hunks.length > shown.length ? (
        <p className={muted}>
          {hunks.length - shown.length} more hunk(s) of this class; the full file shows every one.
        </p>
      ) : null}
    </div>
  );
}

function FocusedHunk({
  hunk,
  expansion,
  layout,
}: {
  hunk: ReviewLaneHunk;
  expansion: ReviewSourceExpansion;
  layout: DiffLayout;
}) {
  return (
    <section
      className={hunkShell}
      data-testid="review-lane-hunk"
      data-hunk-class={hunk.classification}
      data-before={spanLabel(hunk.before)}
      data-after={spanLabel(hunk.after)}
    >
      <div className={hunkHead}>
        <span className={badge}>{CLASS_LABELS[hunk.classification]}</span>
        <span>
          before {spanLabel(hunk.before)} → after {spanLabel(hunk.after)}
        </span>
      </div>
      {hunk.unknown.length ? (
        <ul className={reasons} data-testid="review-lane-hunk-reasons">
          {hunk.unknown.map((one) => (
            <li key={one.side}>
              {one.side}: {one.detail}
            </li>
          ))}
        </ul>
      ) : null}
      <HunkExcerpt hunk={hunk} expansion={expansion} layout={layout} />
    </section>
  );
}

// One side's window around the hunk, when the content read returned that side as text.
function sideWindow(
  hunk: ReviewLaneHunk,
  expansion: ReviewSourceExpansion,
  side: 'before' | 'after',
): HunkWindow | null {
  const opened = expansion[side];
  if (opened.state !== 'present') return null;
  return hunkWindow(hunk[side], sideLines(opened.text ?? ''));
}

function HunkExcerpt({
  hunk,
  expansion,
  layout,
}: {
  hunk: ReviewLaneHunk;
  expansion: ReviewSourceExpansion;
  layout: DiffLayout;
}) {
  const beforeWindow = sideWindow(hunk, expansion, 'before');
  const afterWindow = sideWindow(hunk, expansion, 'after');
  if (beforeWindow?.beyond || afterWindow?.beyond)
    return (
      <p className={muted} data-testid="review-lane-hunk-beyond">
        This hunk lies past the bounded text the content read returned; the full file&apos;s
        reproduce command reaches it.
      </p>
    );
  if (beforeWindow && afterWindow)
    return (
      <DiffPane
        before={beforeWindow.text}
        after={afterWindow.text}
        language={expansion.language}
        mode={layout}
        collapse={false}
        firstLine={{ before: beforeWindow.first, after: afterWindow.first }}
        fit
      />
    );
  const only = beforeWindow ?? afterWindow;
  if (!only) return <p className={muted}>Neither side of this hunk is text the read returned.</p>;
  return <FilePane content={only.text} language={expansion.language} firstLine={only.first} fit />;
}

function FullFile({
  task,
  entry,
  generation,
  layout,
}: {
  task: LaneTask;
  entry: ReviewChangedFile;
  generation: { before: string; after: string };
  layout: DiffLayout;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button
        type="button"
        data-testid="review-lane-full-file"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        {open ? 'Hide full file' : 'Full file'}
      </button>
      {open ? (
        <SourceContent
          repo={task.repo}
          master={task.master}
          leaf={task.leaf}
          entry={entry}
          beforeCodeTreeId={generation.before}
          afterCodeTreeId={generation.after}
          mode={layout}
          collapse={false}
        />
      ) : null}
    </div>
  );
}

// The gate's own items for this file (MIK-R10), with the rows that answer them; the gate may differ
// from the lane's classification, and neither is changed by the other.
function GateItems({ gate, path }: { gate: GateRead; path: string }) {
  if (gate.state !== 'read')
    return (
      <p className={muted} data-testid="review-lane-gate-state" data-gate-state={gate.state}>
        {gate.state === 'reading'
          ? "Reading the gate's items for this file…"
          : `The gate's items are not shown: ${gate.detail}`}
      </p>
    );
  const groups = worklistGroups(gate.worklist).unexplained.filter((group) => group.path === path);
  if (!groups.length)
    return (
      <p className={muted} data-testid="review-lane-gate-none">
        The gate raised no unexplained item for this file.
      </p>
    );
  return <UnexplainedGroups groups={groups} />;
}
