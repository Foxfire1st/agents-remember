// One source renderer for selected intent expressions and unattributed source inspection.
import { css } from '../../../styled-system/css';
import type { ReviewChangedFile, ReviewFamilyMember, ReviewPayload } from '../../data/review';
import { selectedRevision } from './SubjectReview';
import type { ReviewSubject } from './ReviewNavigation';
import { SourceContent } from './SourceContent';
import type { DiffLayout } from './SourceExplorer';

const bar = css({
  display: 'flex',
  gap: '0.5rem',
  alignItems: 'center',
  flexWrap: 'wrap',
  justifyContent: 'space-between',
  marginBottom: '0.75rem',
});
const muted = css({ color: 'muted', fontSize: '0.75rem' });
const card = css({
  border: '1px solid var(--grid)',
  borderRadius: '3px',
  minWidth: 0,
  marginTop: '0.7rem',
  background: 'bg',
});
const pathButton = css({
  display: 'block',
  width: '100%',
  textAlign: 'left',
  padding: '0.8rem',
  color: 'ink',
  background: 'transparent',
  overflowWrap: 'anywhere',
  cursor: 'pointer',
  font: 'inherit',
  border: 0,
});

export function ReviewExpressions({
  payload,
  members,
  subject,
  layout,
  onLayout,
  fullFile,
  onFullFile,
  openPath,
  onOpenPath,
}: {
  payload: ReviewPayload;
  members?: ReviewFamilyMember[];
  subject?: ReviewSubject;
  layout: DiffLayout;
  onLayout: (next: DiffLayout) => void;
  fullFile: boolean;
  onFullFile: (next: boolean) => void;
  openPath: string | null | undefined;
  onOpenPath: (path: string | null) => void;
}) {
  const { paths, linked, entries } = expressionSelection(payload, members, openPath, subject);
  const active = openPath === undefined && members ? entries[0]?.path : openPath;
  return (
    <section data-testid="review-expression-diffs">
      <ExpressionControls
        layout={layout}
        onLayout={onLayout}
        fullFile={fullFile}
        onFullFile={onFullFile}
      />
      <p className={muted} data-testid="review-display-state">
        {fullFile ? 'Full file' : 'Changed regions'} · {layout} diff ·{' '}
        {openPath ? `expanded: ${openPath} · ` : ''}
        {members === undefined
          ? 'All changed files are available in the source explorer.'
          : `${linked.length} linked changed files. The complete source inventory remains in the rail.`}
      </p>
      {!entries.length ? (
        <p className={muted}>
          No linked changed file is recorded for this selection. Use the source explorer to inspect
          the complete change.
        </p>
      ) : null}
      {entries.map((entry) => (
        <ExpressionCard
          key={entry.path}
          entry={entry}
          payload={payload}
          active={active}
          outsideIntent={Boolean(members && !paths.has(entry.path))}
          layout={layout}
          fullFile={fullFile}
          onOpenPath={onOpenPath}
        />
      ))}
    </section>
  );
}

function ExpressionControls({
  layout,
  onLayout,
  fullFile,
  onFullFile,
}: {
  layout: DiffLayout;
  onLayout: (next: DiffLayout) => void;
  fullFile: boolean;
  onFullFile: (next: boolean) => void;
}) {
  return (
    <div className={bar}>
      <h3
        className={css({
          color: 'cyan',
          fontSize: '0.75rem',
          letterSpacing: '0.1em',
          textTransform: 'uppercase',
        })}
      >
        Code & test expressions
      </h3>
      <div className={bar} data-testid="review-center-display-controls">
        <label className={muted} htmlFor="review-center-diff-layout">
          Layout
        </label>
        <select
          id="review-center-diff-layout"
          data-testid="review-center-diff-layout"
          value={layout}
          onChange={(event) => onLayout(event.target.value === 'inline' ? 'inline' : 'split')}
        >
          <option value="split">Side by side</option>
          <option value="inline">Inline</option>
        </select>
        <button
          type="button"
          data-testid="review-center-full-file"
          aria-pressed={fullFile}
          onClick={() => onFullFile(!fullFile)}
        >
          {fullFile ? 'Show changed regions' : 'Expand full file'}
        </button>
      </div>
    </div>
  );
}

function expressionSelection(
  payload: ReviewPayload,
  members: ReviewFamilyMember[] | undefined,
  openPath: string | null | undefined,
  subject?: ReviewSubject,
) {
  const inventory = payload.source.inventory;
  const paths = new Set(
    (members ?? []).flatMap((member) =>
      member.sources.flatMap((source) => (source.path ? [source.path] : [])),
    ),
  );
  const revisions = expressionRevisions(payload, members, subject);
  for (const location of payload.source.locations) {
    if (location.invariant_revision_id && revisions.has(location.invariant_revision_id))
      paths.add(location.path);
  }
  const linked = inventory.entries.filter((entry) => paths.has(entry.path));
  const selected = inventory.entries.find((entry) => entry.path === openPath);
  if (members === undefined)
    return {
      inventory,
      paths,
      linked,
      entries: selected ? [selected] : inventory.entries.slice(0, 1),
    };
  const entries = selected && !paths.has(selected.path) ? [...linked, selected] : linked;
  return { inventory, paths, linked, entries };
}

function ExpressionCard({
  entry,
  payload,
  active,
  outsideIntent,
  layout,
  fullFile,
  onOpenPath,
}: {
  entry: ReviewChangedFile;
  payload: ReviewPayload;
  active: string | null | undefined;
  outsideIntent: boolean;
  layout: DiffLayout;
  fullFile: boolean;
  onOpenPath: (path: string | null) => void;
}) {
  const inventory = payload.source.inventory;
  return (
    <article className={card} data-testid="review-expression-card" data-path={entry.path}>
      <button
        type="button"
        className={pathButton}
        data-path={entry.path}
        aria-expanded={entry.path === active}
        onClick={() => onOpenPath(entry.path === active ? null : entry.path)}
      >
        {entry.path === active ? '▾ ' : '▸ '}
        {entry.path}
        <span className={muted}>
          {' '}
          · {entry.status}
          {outsideIntent ? ' · outside selected intent' : ''}
        </span>
      </button>
      {entry.path === active && inventory.before_code_tree_id && inventory.after_code_tree_id ? (
        <div
          className={css({
            borderTop: '1px solid var(--grid)',
            padding: '0.7rem',
            minWidth: 0,
          })}
        >
          <SourceContent
            repo={payload.candidate.repository_id}
            master={payload.candidate.master}
            leaf={payload.candidate.leaf_id}
            entry={entry}
            beforeCodeTreeId={inventory.before_code_tree_id}
            afterCodeTreeId={inventory.after_code_tree_id}
            mode={layout}
            collapse={!fullFile}
          />
        </div>
      ) : null}
    </article>
  );
}

function expressionRevisions(
  payload: ReviewPayload,
  members: ReviewFamilyMember[] | undefined,
  subject: ReviewSubject | undefined,
): Set<string> {
  const bound = subject ? selectedRevision(payload.knowledge, subject) : null;
  return new Set(
    bound?.record_kind === 'invariant'
      ? [...bound.before_retained, ...bound.after_retained]
      : (members ?? []).map((member) => member.invariant_revision_id),
  );
}
