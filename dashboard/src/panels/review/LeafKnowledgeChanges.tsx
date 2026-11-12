// The leaf-wide worklist and the selected record's items. This view reports recorded facts;
// changed knowledge files belong to the comparison's change-set viewer (MIK-R86 rules 21–22).
import { css } from '../../../styled-system/css';
import type {
  ReviewInvariantCurrentness,
  ReviewSideCurrentness,
  ReviewTreesRead,
  ReviewTreesResult,
  ReviewWorklistHistoryRow,
  ReviewWorklistItem,
  ReviewWorklistView,
} from '../../data/reviewTrees';
import { degradedKnowledgeSides } from '../../data/reviewTrees';
import { itemAnswer, itemAnswerText, fileAnswer, worklistAnswer } from './worklistState';
import { hunkLinkage, worklistGroups, type WorklistEntry } from './worklistGroups';

const shell = css({ minWidth: 0, display: 'grid', gap: '0.6rem', overflowWrap: 'anywhere' });
const label = css({ color: 'cyan', fontSize: '0.75rem', margin: '0.4rem 0' });
const muted = css({ color: 'muted', fontSize: '0.75rem', margin: '0.2rem 0' });
const list = css({ margin: 0, paddingLeft: '1rem', fontSize: '0.78rem' });
const link = css({
  color: 'amber',
  cursor: 'pointer',
  background: 'transparent',
  border: 'none',
  padding: 0,
  font: 'inherit',
  textDecoration: 'underline',
});
const NAMES: Record<string, string> = {
  touched_invariant: 'invariant touched',
  stale_invariant: 'invariant stale at the base',
  reached_family: 'family reached',
  family_route_condition: 'family route condition',
  onboarding_trace: 'onboarding trace',
  planned_untouched: 'planned effect not delivered',
  unexplained_hunk: 'change without an invariant',
  unexplained_file: 'change without an invariant',
};
const identityKey = (key: string) => /(^id$|digest|blob|content|identit|revision|^row$)/i.test(key);

// Read facts as names and values, keeping hashes in the group's identifiers rather than in prose.
function factText(value: unknown, includeIdentities = false): string {
  if (value === undefined || value === null) return 'none';
  if (Array.isArray(value))
    return value
      .map((one) => factText(one, includeIdentities))
      .filter(Boolean)
      .join(', ');
  if (typeof value === 'object')
    return Object.entries(value)
      .filter(([key]) => includeIdentities || !identityKey(key))
      .map(([key, one]) => `${key.replace(/_/g, ' ')}: ${factText(one, includeIdentities)}`)
      .join('; ');
  const text = String(value);
  return includeIdentities || !/^(sha256:)?[a-f0-9]{40,64}$/.test(text) ? text : '';
}

function currentnessReasons(trees: ReviewTreesResult, subject: string): string[] {
  return (['before', 'after'] as const).flatMap((side) => {
    const record = trees.currentness?.[side]?.invariants?.find((one) => one.id === subject);
    return (record?.entries ?? [])
      .filter((one) => one.state !== 'current')
      .map(
        (one) =>
          `${side} ${one.id} ${one.path} · ${one.state}${one.reason ? `: ${one.reason}` : ''}`,
      );
  });
}

function touchedFacts(facts: Record<string, unknown>): string {
  const record = facts.record as Record<string, unknown> | undefined;
  const count = (name: string) => (Array.isArray(facts[name]) ? facts[name].length : 0);
  return `revisions ${record?.base_revision ?? 'absent'} → ${record?.candidate_revision ?? 'absent'} · ${count('added')} added, ${count('retired')} retired, ${count('reanchored')} re-anchored entries`;
}
const FACT_FORMATS: Record<string, (facts: Record<string, unknown>) => string> = {
  touched_invariant: touchedFacts,
  reached_family: (facts) => factText(facts.reached_by),
  family_route_condition: (facts) =>
    `${factText(facts.condition).replace(/_/g, ' ')} · ${Array.isArray(facts.locations) ? facts.locations.length : 0} locations`,
  stale_invariant: (facts) =>
    Array.isArray(facts.entries)
      ? facts.entries
          .map((one) => {
            const entry = one as Record<string, unknown>;
            return `${entry.id} ${entry.path}`;
          })
          .join(', ')
      : '',
  unexplained_hunk: (facts) =>
    factText({ ranges: facts.hunks ?? facts.changed_lines, coverage: facts.coverage }),
  unexplained_file: (facts) => factText({ status: facts.status, coverage: facts.coverage }),
};
function itemFacts(item: ReviewWorklistItem): string {
  return (FACT_FORMATS[item.kind] ?? factText)(item.facts);
}

// The governing row prints its recorded effect after its disposition (MIK-R43): the
// effect the leaf-wide view's row document already returns. A changed row without one
// was written before the effect label became required.
function rowEffect(row: ReviewWorklistHistoryRow): string {
  const effect = (row.row as Record<string, unknown> | undefined)?.effect;
  if (typeof effect === 'string' && effect) return ` · ${effect}`;
  if (row.disposition === 'changed') return ' · effect not recorded';
  return '';
}

function ItemLine({ entry, trees }: { entry: WorklistEntry; trees: ReviewTreesResult }) {
  const { item, rows } = entry;
  const answer = itemAnswer(trees.worklist, item);
  const witness = rows.find((row) => row.id === answer?.witness);
  const state = itemAnswerText(answer) + (witness ? rowEffect(witness) : '');
  const reasons = currentnessReasons(trees, item.subject);
  return (
    <li
      data-testid="review-worklist-item"
      data-review-content="worklist"
      data-kind={item.kind}
      data-answer={answer?.state}
      data-subject={item.subject}
      data-planning={item.planning}
    >
      {item.subject} · {NAMES[item.kind] ?? item.kind.replace(/_/g, ' ')}
      {item.planning ? ` · ${item.planning}` : ''} · {state} · {itemFacts(item)}
      {reasons.map((reason) => (
        <div key={reason}>{reason}</div>
      ))}
    </li>
  );
}

function identifiers(value: unknown, prefix = ''): string[] {
  if (Array.isArray(value))
    return value.flatMap((one, index) => identifiers(one, `${prefix}[${index}]`));
  if (!value || typeof value !== 'object') return [];
  return Object.entries(value).flatMap(([key, one]) =>
    identityKey(key)
      ? [`${prefix}${key}: ${factText(one, true)}`]
      : identifiers(one, `${prefix}${key}.`),
  );
}

function Identifiers({ entries }: { entries: WorklistEntry[] }) {
  return (
    <details data-disclosure="identifiers">
      <summary>Identifiers</summary>
      <ul className={list}>
        {entries.map(({ item }) => (
          <li key={item.id}>
            {item.id}
            {identifiers(item.facts).map((text, index) => (
              <div key={index}>{text}</div>
            ))}
          </li>
        ))}
      </ul>
    </details>
  );
}

function Group({
  title,
  entries,
  trees,
  testid,
}: {
  title: string;
  entries: WorklistEntry[];
  trees: ReviewTreesResult;
  testid?: string;
}) {
  if (!entries.length) return null;
  return (
    <section data-testid={testid}>
      <h3 className={label}>
        {title} · {entries.length} items
      </h3>
      <ul className={list}>
        {entries.map((entry) => (
          <ItemLine key={entry.item.id} entry={entry} trees={trees} />
        ))}
      </ul>
      <Identifiers entries={entries} />
    </section>
  );
}

function sideCounts(side?: ReviewSideCurrentness): string {
  if (!side) return 'not read';
  if (side.unverifiable_reason) return `unverifiable: ${side.unverifiable_reason}`;
  return (['current', 'stale', 'unverifiable', 'unrealized'] as const)
    .map((state) => `${side.counts?.[state] ?? 0} ${state}`)
    .join(' · ');
}

function Currentness({ trees }: { trees: ReviewTreesResult }) {
  const named = new Set(trees.worklist?.items.map((item) => item.subject));
  return (
    <section data-testid="review-knowledge-currentness">
      <h3 className={label}>Currentness per side</h3>
      {(['before', 'after'] as const).map((name) => (
        <div key={name} data-side={name}>
          <p className={muted}>
            {name} at the code {name === 'before' ? 'base' : 'candidate'}:{' '}
            {sideCounts(trees.currentness?.[name])}
          </p>
          <ul className={list}>
            {(trees.currentness?.[name]?.invariants ?? [])
              .filter((one) => one.state !== 'current' && !named.has(one.id))
              .map((one: ReviewInvariantCurrentness) => (
                <li key={one.id} data-review-content="currentness">
                  {one.id} · {one.state}
                  {one.entries
                    .filter((entry) => entry.state !== 'current')
                    .map((entry) => (
                      <div key={entry.id}>
                        {entry.id} {entry.path} · {entry.state}
                        {entry.reason ? `: ${entry.reason}` : ''}
                      </div>
                    ))}
                </li>
              ))}
          </ul>
        </div>
      ))}
    </section>
  );
}

function completeWorklist(worklist: ReviewWorklistView | undefined): boolean {
  return Boolean(
    worklist &&
    worklist.source !== 'absent' &&
    worklist.state === 'complete' &&
    worklist.bound &&
    worklist.incomplete.length === 0,
  );
}
function worklistStatus(worklist?: ReviewWorklistView): string {
  if (!worklist) return 'worklist unavailable';
  if (worklist.source === 'absent') return 'No worklist applies or was persisted · source absent';
  const count = completeWorklist(worklist)
    ? (worklistAnswer(worklist) ?? `${worklist.items.length} items; gate states unavailable`)
    : `${worklist.items.length} reported items · partial or unbound measurement`;
  return `worklist ${worklist.state ?? 'state unknown'} · ${worklist.source} · ${count} · ${worklist.bound ? 'bound to this comparison' : 'not bound to this comparison'}`;
}
function WorklistProblems({ worklist }: { worklist?: ReviewWorklistView }) {
  if (completeWorklist(worklist) && !worklist?.detail) return null;
  return (
    <div data-testid="review-worklist-problems" data-review-content="worklist-state" role="status">
      {!completeWorklist(worklist) ? (
        <p>
          {worklistStatus(worklist)}. Absence of an item and complete gate linkage have not been
          measured.
        </p>
      ) : null}
      {worklist?.detail ? <p>{worklist.detail}</p> : null}
      {worklist?.incomplete.map((one, index) => (
        <p key={index}>{factText(one)}</p>
      ))}
    </div>
  );
}

function ReadNotice({ read }: { read?: ReviewTreesRead | null }) {
  return (
    <p className={muted} role="status" data-review-content="read-state">
      {!read || read.phase === 'loading'
        ? "Reading the leaf's worklist…"
        : read.phase === 'not-converted'
          ? 'The worklist is not available because this leaf’s memory is not converted.'
          : read.phase === 'unavailable'
            ? `${read.problem.detail}${read.problem.nextAction ? ` Next: ${read.problem.nextAction}.` : ''}`
            : 'The leaf’s worklist is unavailable.'}
    </p>
  );
}

interface SubjectItemsProps {
  read?: ReviewTreesRead | null;
  recordId?: string;
  reviewComparison?: number;
}
function SubjectItemsBody({ read, recordId, reviewComparison }: SubjectItemsProps) {
  if (read?.phase !== 'trees') return <ReadNotice read={read} />;
  const trees = read.trees;
  if (reviewComparison !== undefined && trees.comparison?.number !== reviewComparison)
    return (
      <p role="status">
        Reading the leaf's worklist… The available answer belongs to comparison{' '}
        {trees.comparison?.number}.
      </p>
    );
  if (!recordId)
    return (
      <p role="status" data-review-content="record-identity">
        The selected record's canonical identity is unavailable; its worklist items cannot be
        matched.
      </p>
    );
  return <RecordWorklistItems trees={trees} recordId={recordId} />;
}
function RecordWorklistItems({ trees, recordId }: { trees: ReviewTreesResult; recordId: string }) {
  const worklist = trees.worklist;
  if (!worklist) return <WorklistProblems />;
  const entries = worklist.items
    .filter((item) => item.subject === recordId)
    .map((item) => ({
      item,
      rows: worklist.history_rows.filter((row) => row.subject === item.subject),
    }));
  const complete = completeWorklist(worklist);
  return (
    <>
      {!complete ? <WorklistProblems worklist={worklist} /> : null}
      {!entries.length && complete ? (
        <p className={muted}>No worklist item names this record.</p>
      ) : null}
      {entries.length ? (
        <ul className={list} data-worklist-complete={complete}>
          {entries.map((entry) => (
            <ItemLine key={entry.item.id} entry={entry} trees={trees} />
          ))}
        </ul>
      ) : null}
    </>
  );
}
export function SubjectWorklistItems(props: SubjectItemsProps) {
  return (
    <section className={shell} data-testid="review-subject-worklist">
      <h3 className={label}>Worklist items about this record</h3>
      <SubjectItemsBody {...props} />
    </section>
  );
}

function WorklistGroupsView({
  trees,
  onOpenPath,
}: {
  trees: ReviewTreesResult;
  onOpenPath: (path: string) => void;
}) {
  const groups = trees.worklist ? worklistGroups(trees.worklist) : undefined;
  const unexplained =
    groups?.unexplained.flatMap((group) => group.coverage.flatMap((one) => one.entries)) ?? [];
  return groups ? (
    <div data-testid="review-worklist">
      <Group
        title="Invariants and families"
        testid="review-worklist-knowledge"
        entries={groups.knowledge}
        trees={trees}
      />
      <Group
        title="Planned effects no row has delivered"
        testid="review-worklist-planned-untouched"
        entries={groups.plannedUntouched}
        trees={trees}
      />
      {groups.other.map((group) => (
        <Group
          key={group.kind}
          title={NAMES[group.kind] ?? group.kind.replace(/_/g, ' ')}
          entries={group.entries}
          trees={trees}
        />
      ))}
      {unexplained.length ? (
        <section data-testid="review-unexplained-groups">
          <h3 className={label}>Changes without an invariant · {unexplained.length} items</h3>
          {groups.unexplained.map((group) => (
            <div key={group.path} data-testid="review-unexplained-file" data-path={group.path}>
              <p>
                <button type="button" className={link} onClick={() => onOpenPath(group.path)}>
                  {group.path}
                </button>{' '}
                · {group.coverage.map((one) => `${one.entries.length} ${one.state}`).join(' · ')}
                {fileAnswer(trees.worklist, group.path)
                  ? ` · ${fileAnswer(trees.worklist, group.path)}`
                  : ''}
              </p>
              <ul className={list}>
                {group.coverage
                  .flatMap((one) => one.entries)
                  .map((entry) => (
                    <ItemLine key={entry.item.id} entry={entry} trees={trees} />
                  ))}
              </ul>
            </div>
          ))}
          <Identifiers entries={unexplained} />
        </section>
      ) : null}
    </div>
  ) : (
    <p className={muted}>The leaf’s worklist is unavailable.</p>
  );
}
function WorklistStatus({
  trees,
  reviewComparison,
}: {
  trees: ReviewTreesResult;
  reviewComparison?: number;
}) {
  return (
    <>
      <p className={muted} data-testid="review-worklist-status" data-review-content="state">
        {worklistStatus(trees.worklist)}
        {reviewComparison !== undefined && trees.comparison?.number !== reviewComparison
          ? ` · this answer belongs to comparison ${trees.comparison?.number}; the review shows ${reviewComparison}`
          : ''}
      </p>
      {degradedKnowledgeSides(trees).map((side) => (
        <p key={side.side} data-review-content="reason">
          {side.side} knowledge: {side.detail ?? 'This knowledge side could not be read.'}
          {side.state === 'legacy-unavailable'
            ? ' The code changes of this leaf are shown in full.'
            : ''}
          {side.index_state === 'partial' ? ' · partial index' : ''}
          {side.problems.map(([path, reason]) => (
            <span key={path}>
              {' '}
              · {path}: {reason}
            </span>
          ))}
        </p>
      ))}
    </>
  );
}
function GateLinkage({ worklist }: { worklist?: ReviewWorklistView }) {
  const complete = completeWorklist(worklist);
  if (!worklist || (!complete && !worklist.changes.length))
    return (
      <p data-testid="review-worklist-linkage" data-review-content="reason">
        Gate linkage has not been completely measured for this comparison.
      </p>
    );
  const linkage = (worklist?.changes ?? []).map(hunkLinkage);
  return (
    <p data-testid="review-worklist-linkage">
      {complete ? '' : 'Partial reported linkage: '}
      {linkage.reduce((sum, one) => sum + one.linked, 0)} of{' '}
      {linkage.reduce((sum, one) => sum + one.hunks, 0)} changed hunks in {worklist.changes.length}{' '}
      files are linked to recorded knowledge
      {complete
        ? '; the others are the unexplained items above.'
        : '; complete gate linkage has not been measured.'}
    </p>
  );
}
export function LeafWorklistView({
  read,
  reviewComparison,
  onOpenChanges,
  onOpenPath,
}: {
  read?: ReviewTreesRead | null;
  reviewComparison?: number;
  onOpenChanges: () => void;
  onOpenPath: (path: string) => void;
}) {
  const answer = read?.phase === 'trees' ? read.trees : undefined;
  const trees =
    answer && reviewComparison !== undefined && answer.comparison?.number !== reviewComparison
      ? { ...answer, worklist: answer.worklist ? { ...answer.worklist, bound: false } : undefined }
      : answer;
  return (
    <section className={shell} data-testid="review-worklist-view">
      <h2 className={label}>Worklist</h2>
      {trees ? (
        <>
          <WorklistStatus trees={trees} reviewComparison={reviewComparison} />
          <p>
            Changed knowledge files are read in the change-set viewer.{' '}
            <button type="button" className={link} onClick={onOpenChanges}>
              Open changed knowledge files
            </button>
          </p>
          <Currentness trees={trees} />
          <WorklistProblems worklist={trees.worklist} />
          <WorklistGroupsView trees={trees} onOpenPath={onOpenPath} />
          <GateLinkage worklist={trees.worklist} />
        </>
      ) : (
        <ReadNotice read={read} />
      )}
    </section>
  );
}
