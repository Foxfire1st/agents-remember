// The leaf's knowledge changes for a tree comparison (MIK-R25 rules 2 and 3, rendered by MIK-R31):
// the Git diff of the two memory trees by record and by source path, each side's MIK-R03
// currentness, and the worklist items with the history rows about them, MIK-R11's planning marks
// and the planned effects no row delivered. Unexplained changes are grouped by file and coverage
// state; history rows (MIK-R10) answer them, and neither this panel nor the lane decides them.
import { css } from '../../../styled-system/css';
import type {
  ReviewKnowledgeFileChange,
  ReviewSideCurrentness,
  ReviewTreesResult,
  ReviewWorklistView,
} from '../../data/reviewTrees';
import type { ReviewTreesRead } from '../../data/reviewTrees';
import { degradedKnowledgeSides } from '../../data/reviewTrees';
import {
  hunkLinkage,
  worklistGroups,
  type UnexplainedGroup,
  type WorklistEntry,
} from './worklistGroups';

const shell = css({
  border: '1px solid var(--grid)',
  borderRadius: '3px',
  background: 'bg',
  padding: '0.7rem 0.8rem',
  display: 'grid',
  gap: '0.6rem',
  minWidth: 0,
});
const label = css({
  color: 'cyan',
  fontSize: '0.72rem',
  letterSpacing: '0.1em',
  textTransform: 'uppercase',
  margin: 0,
});
const muted = css({ color: 'muted', fontSize: '0.75rem', margin: '0.15rem 0' });
const list = css({
  margin: 0,
  paddingLeft: '1rem',
  fontSize: '0.78rem',
  display: 'grid',
  gap: '0.3rem',
  overflowWrap: 'anywhere',
});
const mark = css({ color: 'amber' });
const patch = css({ whiteSpace: 'pre-wrap', fontSize: '0.7rem', margin: '0.3rem 0' });

export function LeafKnowledgeChanges({
  trees,
  reviewComparison,
  open = false,
}: {
  trees: ReviewTreesResult;
  reviewComparison?: number;
  open?: boolean;
}) {
  const diff = trees.knowledge_diff;
  const worklist = trees.worklist;
  const other = trees.comparison?.number;
  return (
    <details className={shell} open={open} data-testid="review-leaf-knowledge">
      <summary className={label}>Knowledge changes in this leaf</summary>
      <p className={muted} data-testid="review-leaf-knowledge-status">
        {diff ? `${diff.changed_files} knowledge file(s) changed` : 'knowledge diff unavailable'} ·{' '}
        {worklist ? worklistStatus(worklist) : 'no worklist'}
        {other !== undefined && reviewComparison !== undefined && other !== reviewComparison
          ? ` · this view reads comparison ${other}, the review shows comparison ${reviewComparison}`
          : ''}
      </p>
      {degradedKnowledgeSides(trees).map((side) => (
        <p key={side.side} className={mark} data-testid="review-leaf-knowledge-degraded">
          {side.side} knowledge {side.state}
          {side.index_state === 'partial' ? ' · partial index' : ''}
        </p>
      ))}
      {diff ? <KnowledgeDiff diff={diff} /> : null}
      <Currentness trees={trees} />
      {worklist ? <Worklist worklist={worklist} /> : null}
    </details>
  );
}

// While the leaf-wide read computes (the first view of a large leaf takes several seconds), and when it
// finally gives no answer, the panel says so instead of being absent: a missing panel reads as "this
// leaf changed no knowledge".
export function LeafKnowledgeNotice({ read }: { read: ReviewTreesRead }) {
  if (read.phase !== 'loading' && read.phase !== 'unavailable') return null;
  const computing = read.phase === 'loading';
  const notice = computing
    ? {
        id: 'review-leaf-knowledge-pending',
        tone: muted,
        statusId: 'review-leaf-knowledge-computing',
      }
    : { id: 'review-leaf-knowledge-unavailable', tone: mark, statusId: undefined };
  return (
    <section
      className={shell}
      data-testid={notice.id}
      data-review-code={read.phase === 'unavailable' ? read.problem.code : undefined}
      data-review-state={read.phase === 'unavailable' ? read.problem.token : undefined}
    >
      <h3 className={label}>Knowledge changes in this leaf</h3>
      <p className={notice.tone} role="status" data-testid={notice.statusId}>
        {read.phase === 'loading'
          ? 'Computing the knowledge changes of this leaf. The first read of a large leaf takes several seconds; the review stays usable.'
          : `The knowledge changes of this leaf are unavailable: ${read.problem.detail}`}
      </p>
      {read.phase === 'unavailable' && read.problem.nextAction ? (
        <p className={muted}>Next: {read.problem.nextAction}.</p>
      ) : null}
    </section>
  );
}

function worklistStatus(worklist: ReviewWorklistView): string {
  if (worklist.source === 'absent') return 'no worklist applies';
  return `worklist ${worklist.state ?? 'state unknown'} · ${worklist.items.length} item(s) · ${
    worklist.source
  }${worklist.bound ? '' : ' · not bound to this comparison'}`;
}

function FileChange({ file }: { file: ReviewKnowledgeFileChange }) {
  return (
    <details>
      <summary>
        {file.path} · {file.status}
      </summary>
      <pre className={patch}>
        {file.patch}
        {file.truncated ? '\n… the patch is longer; git diff reproduces it whole' : ''}
      </pre>
    </details>
  );
}

function KnowledgeDiff({ diff }: { diff: NonNullable<ReviewTreesResult['knowledge_diff']> }) {
  return (
    <div data-testid="review-knowledge-diff">
      <p className={label}>By record</p>
      <ul className={list}>
        {diff.records.map((group) => (
          <li
            key={group.record_id}
            data-testid="review-knowledge-record"
            data-record={group.record_id}
          >
            {group.record_id} · {group.kind}
            {group.entries.length
              ? ` · entries ${group.entries.map(([id, , how]) => `${id} ${how}`).join(', ')}`
              : ''}
            {group.files.map((file) => (
              <FileChange key={file.path} file={file} />
            ))}
          </li>
        ))}
      </ul>
      {diff.sources.length ? (
        <>
          <p className={label}>By source path</p>
          <ul className={list}>
            {diff.sources.map((group) => (
              <li key={group.source_path} data-testid="review-knowledge-source">
                {group.source_path} · {group.records.join(', ') || 'no record named'}
                {group.files.map((file) => (
                  <FileChange key={file.path} file={file} />
                ))}
              </li>
            ))}
          </ul>
        </>
      ) : null}
      {diff.history.length || diff.other.length ? (
        <>
          <p className={label}>History and other knowledge files</p>
          <ul className={list}>
            {[...diff.history, ...diff.other].map((file) => (
              <li key={file.path}>
                <FileChange file={file} />
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </div>
  );
}

function sideCounts(side?: ReviewSideCurrentness): string {
  if (!side) return 'not read';
  if (side.unverifiable_reason) return `unverifiable: ${side.unverifiable_reason}`;
  const counts: Partial<Record<string, number>> = side.counts ?? {};
  return (['current', 'stale', 'unverifiable', 'unrealized'] as const)
    .map((state) => `${counts[state] ?? 0} ${state}`)
    .join(' · ');
}

function Currentness({ trees }: { trees: ReviewTreesResult }) {
  const sides = [
    ['before', 'at the code base'],
    ['after', 'at the code candidate'],
  ] as const;
  return (
    <div data-testid="review-knowledge-currentness">
      <p className={label}>Currentness per side</p>
      {sides.map(([name, where]) => {
        const side = trees.currentness?.[name];
        const off = (side?.invariants ?? []).filter((one) => one.state !== 'current');
        return (
          <details key={name} data-side={name}>
            <summary className={muted}>
              {name} {where}: {sideCounts(side)}
            </summary>
            <ul className={list}>
              {off.map((one) => (
                <li key={one.id}>
                  {one.id} · {one.state}
                  {one.entries
                    .filter((entry) => entry.state !== 'current')
                    .map(
                      (entry) =>
                        ` · ${entry.id} ${entry.state}${entry.reason ? `: ${entry.reason}` : ''}`,
                    )
                    .join('')}
                </li>
              ))}
            </ul>
          </details>
        );
      })}
    </div>
  );
}

function Rows({ entry }: { entry: WorklistEntry }) {
  if (!entry.rows.length) return <span className={muted}> · no history row about it</span>;
  return (
    <span className={muted}>
      {' · rows: '}
      {entry.rows.map((row) => `${row.owner} ${row.disposition} (${row.id})`).join(', ')}
    </span>
  );
}

function ItemLine({ entry }: { entry: WorklistEntry }) {
  const { item } = entry;
  return (
    <li
      data-testid="review-worklist-item"
      data-kind={item.kind}
      data-subject={item.subject}
      data-planning={item.planning}
    >
      {item.subject} · {item.kind.replace(/_/g, ' ')}
      {item.planning ? <span className={mark}> · {item.planning}</span> : null}
      {item.satisfied_by ? ' · answered' : ''}
      <Rows entry={entry} />
      <details>
        <summary className={muted}>Item facts</summary>
        <pre className={patch}>{JSON.stringify(item.facts, null, 1)}</pre>
      </details>
    </li>
  );
}

// The structure the unexplained-changes lane (MIK-R32) plugs into: one group per file, split by
// coverage state, each item listed with its rows. No disposition is offered or decided here.
export function UnexplainedGroups({ groups }: { groups: UnexplainedGroup[] }) {
  const total = groups.reduce((sum, group) => sum + group.count, 0);
  return (
    <div data-testid="review-unexplained-groups" data-count={total}>
      <p className={label}>
        Unexplained changes · {total} item(s) in {groups.length} file(s)
      </p>
      {groups.map((group) => (
        <details key={group.path} data-testid="review-unexplained-file" data-path={group.path}>
          <summary className={muted}>
            {group.path} ·{' '}
            {group.coverage.map((one) => `${one.entries.length} ${one.state}`).join(' · ')}
          </summary>
          {group.coverage.map((one) => (
            <div key={one.state} data-coverage={one.state}>
              <p className={muted}>{one.state}</p>
              <ul className={list}>
                {one.entries.map((entry) => (
                  <ItemLine key={entry.item.id} entry={entry} />
                ))}
              </ul>
            </div>
          ))}
        </details>
      ))}
    </div>
  );
}

function Worklist({ worklist }: { worklist: ReviewWorklistView }) {
  const groups = worklistGroups(worklist);
  return (
    <div data-testid="review-worklist">
      <p className={label}>Worklist</p>
      {worklist.detail ? <p className={muted}>{worklist.detail}</p> : null}
      {groups.knowledge.length ? (
        <ul className={list} data-testid="review-worklist-knowledge">
          {groups.knowledge.map((entry) => (
            <ItemLine key={entry.item.id} entry={entry} />
          ))}
        </ul>
      ) : null}
      {groups.plannedUntouched.length ? (
        <>
          <p className={muted}>Planned effects no row has delivered</p>
          <ul className={list} data-testid="review-worklist-planned-untouched">
            {groups.plannedUntouched.map((entry) => (
              <ItemLine key={entry.item.id} entry={entry} />
            ))}
          </ul>
        </>
      ) : null}
      {groups.other.map((group) => (
        <details key={group.kind}>
          <summary className={muted}>
            {group.kind.replace(/_/g, ' ')} · {group.entries.length}
          </summary>
          <ul className={list}>
            {group.entries.map((entry) => (
              <ItemLine key={entry.item.id} entry={entry} />
            ))}
          </ul>
        </details>
      ))}
      {groups.unexplained.length ? <UnexplainedGroups groups={groups.unexplained} /> : null}
      {worklist.changes.length ? (
        <details data-testid="review-worklist-linkage">
          <summary className={muted}>Gate linkage of the changed files</summary>
          <ul className={list}>
            {worklist.changes.map((change) => {
              const linkage = hunkLinkage(change);
              return (
                <li key={change.path}>
                  {change.path} · {linkage.linked} of {linkage.hunks} hunk(s) linked
                  {linkage.fileLinked === undefined
                    ? ''
                    : ` · file ${linkage.fileLinked ? 'linked' : 'unexplained'}`}
                </li>
              );
            })}
          </ul>
        </details>
      ) : null}
    </div>
  );
}
