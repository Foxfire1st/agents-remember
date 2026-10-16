import { css,cva } from '../../styled-system/css';
import { hasLiveWorktree,selectWorktrees } from '../data/selectors';
import { useDashboard } from '../data/store';
import { taskEntrySelectionKey } from '../data/taskReference';
import { Affordance } from '../grammar/Affordance';
import { Panel } from '../grammar/Panel';
import { TaskStatusWord } from './detail-panel/pageParts';

const sizing = css({ flex: '1' });
const list = css({ listStyle: 'none', margin: '0', padding: '0', display: 'grid', gap: '0.5rem' });
const row = cva({
  base: {
    display: 'grid',
    gap: '0.3rem',
    padding: '0.5rem 0.6rem',
    background: 'bg',
    borderWidth: '1px',
    borderStyle: 'solid',
    borderColor: 'grid',
    borderLeftWidth: '3px',
    borderLeftColor: 'dormant',
    borderRadius: '3px',
  },
  variants: { stale: { true: { borderLeftColor: 'amber' } } },
});
const head = css({
  display: 'flex',
  alignItems: 'baseline',
  justifyContent: 'space-between',
  gap: '0.5rem',
});
const ghost = css({
  font: 'inherit',
  color: 'inherit',
  background: 'transparent',
  borderStyle: 'none',
  cursor: 'pointer',
  textAlign: 'left',
});
const badges = css({ display: 'flex', flexWrap: 'wrap', gap: '0.3rem' });
const badge = css({
  fontSize: '0.68rem',
  paddingInline: '0.35rem',
  paddingBlock: '0.05rem',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '2px',
  color: 'muted',
  whiteSpace: 'nowrap',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
});
const operationBadge = css({ flex: '1 1 18rem', minWidth: '0', maxWidth: '100%' });
const actions = css({ display: 'flex', gap: '0.3rem' });

export function Hangar({ onSelect }: { onSelect: (id: string) => void }) {
  const worktrees = useDashboard(selectWorktrees);
  // Bound task words come from the canonical entry through TaskStatusWord.
  // Review, closeout, integration and cleanup badges come from the enclosure.
  const rows = worktrees
    .filter((row) => hasLiveWorktree(row.enclosure))
    .sort((a, b) => a.enclosure.enclosure.localeCompare(b.enclosure.enclosure));

  return (
    <Panel testid="hangar" title={`Hangar · ${rows.length} worktrees`} className={sizing}>
      {rows.length === 0 ? (
        <p className="muted">Hangar empty — no live persistent worktrees.</p>
      ) : (
        <ul className={list}>
          {rows.map(({ enclosure, entry }) => {
            return (
              <li key={enclosure.enclosure} className={row()} data-testid="hangar-row">
                <div className={head}>
                  <button
                    type="button"
                    className={ghost}
                    disabled={!entry}
                    onClick={() => {
                      if (entry) onSelect(taskEntrySelectionKey(entry.key));
                    }}
                  >
                    {enclosure.repoName} · {entry?.title ?? enclosure.taskName}
                  </button>
                </div>
                <div className={badges}>
                  {entry ? (
                    <span className={badge}>
                      <TaskStatusWord task={entry} />
                    </span>
                  ) : null}
                  <span className={badge}>review {enclosure.humanReviewStatus}</span>
                  <span className={badge}>closeout {enclosure.closeoutStatus}</span>
                  <span className={badge}>integrate {enclosure.integrationStatus}</span>
                  <span className={badge}>cleanup {enclosure.cleanup}</span>
                  {enclosure.lifecycleOperation ? (
                    <span
                      className={`${badge} ${operationBadge}`}
                      data-testid="hangar-lifecycle-operation"
                      title={enclosure.lifecycleOperation.currentCommand}
                    >
                      {enclosure.lifecycleOperation.kind} {enclosure.lifecycleOperation.status} ·{' '}
                      {enclosure.lifecycleOperation.phase} ·{' '}
                      {enclosure.lifecycleOperation.currentCommand}
                    </span>
                  ) : null}
                </div>
                {enclosure.actions.length > 0 ? (
                  <div className={actions}>
                    {enclosure.actions.map((action) => (
                      <Affordance key={action.action} action={action} />
                    ))}
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}
