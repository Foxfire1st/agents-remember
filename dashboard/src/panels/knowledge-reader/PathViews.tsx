// The reader's path-side views (MIK-R29 rule 2): a file's path view, a directory's bounded summary
// and its paged subtree, the without-proof list, the census view and a code file opened at its
// locator.
import { useRef, useState } from 'react';

import { css } from '../../../styled-system/css';
import {
  locatorLabel,
  readSubtree,
  type CensusAnswer,
  type CodeAnswer,
  type FamilyAtPath,
  type InvariantGroup,
  type LinkingRecord,
  type PathViewAnswer,
  type SubtreeAnswer,
  type SubtreeRow,
  type WithoutProofAnswer,
} from '../../data/knowledgeReader';
import {
  DecisionCard,
  EntryRow,
  PathLink,
  ProseWithReferences,
  RecordLink,
  Section,
  StateBadge,
  card,
  list,
  muted,
  useReaderNav,
} from './readerParts';

const heading = css({ margin: '0 0 0.3rem', fontSize: '0.95rem', overflowWrap: 'anywhere' });
const codeBlock = css({
  margin: '0',
  padding: '0.5rem',
  background: 'bg',
  overflow: 'auto',
  fontSize: '0.78rem',
  lineHeight: '1.45',
});
const located = css({ background: 'bgPanel', color: 'ink', display: 'block' });

function InvariantItem({ group }: { group: InvariantGroup }) {
  return (
    <li className={card} data-testid="reader-invariant" data-invariant={group.id}>
      <div>
        <RecordLink summary={group} />
        <StateBadge state={group.state} testid="invariant-state" />
      </div>
      {group.statement ? <div className={muted}>{group.statement}</div> : null}
      <ul className={list}>
        {group.entries.map((entry) => (
          <EntryRow key={entry.id} entry={entry} />
        ))}
      </ul>
    </li>
  );
}

function invariantsTitle(answer: PathViewAnswer): string {
  if (answer.testFile) return 'Proofs by invariant';
  if (answer.kind === 'directory') return 'Invariants realized or proved in files directly here';
  return 'Invariants realized or proved here';
}

function InvariantsSection({ answer }: { answer: PathViewAnswer }) {
  const reason = answer.currentness.unverifiableReason;
  return (
    <Section title={invariantsTitle(answer)} testid="reader-invariants">
      {reason ? (
        <p className={muted} data-testid="reader-unverifiable">
          states unverifiable: {reason}
        </p>
      ) : null}
      {answer.invariants.length === 0 ? (
        <p className={muted}>No realization or proof entry is recorded here.</p>
      ) : (
        <ul className={list}>
          {answer.invariants.map((group) => (
            <InvariantItem key={group.id} group={group} />
          ))}
        </ul>
      )}
    </Section>
  );
}

function FamilyItem({ family }: { family: FamilyAtPath }) {
  return (
    <li className={card} data-testid="reader-family" data-family={family.id}>
      <div>
        <RecordLink summary={family} />
        {family.staleMembers.length > 0 ? (
          <span className={muted}> · {family.staleMembers.length} stale member(s)</span>
        ) : null}
      </div>
      {family.guarantee ? <div className={muted}>{family.guarantee}</div> : null}
      <div className={muted}>
        {family.member ? 'contains an invariant here' : 'routed over this path'}
        {family.via.length > 0 ? ` · via route ${family.via.join(', ')}` : ''}
      </div>
      {family.otherLocations.length > 0 ? (
        <div data-testid="family-elsewhere">
          elsewhere:{' '}
          {family.otherLocations.map((location) => (
            <span key={location.path}>
              <PathLink path={location.path} /> ({location.entries.length}){' '}
            </span>
          ))}
        </div>
      ) : null}
    </li>
  );
}

function LinkedRecordItem({ row }: { row: LinkingRecord }) {
  return (
    <li data-testid="reader-linked-record" data-record={row.record.id}>
      {row.decision ? (
        <DecisionCard decision={row.decision} />
      ) : (
        <div className={card}>
          <div>
            <RecordLink summary={row.record} /> <span className={muted}>{row.record.kind}</span>
          </div>
        </div>
      )}
      <div className={muted}>
        {row.links.map((link) => `${link.relation} → ${link.target}`).join(' · ')}
      </div>
    </li>
  );
}

function pathLabel(answer: PathViewAnswer): string {
  if (answer.kind === 'directory') return answer.path === '.' ? 'Repository summary' : 'Directory';
  return answer.testFile ? 'Test file' : 'File';
}

/** A directory's children that hold knowledge, and the way to its full, paged entry list. */
function DirectorySummary({ answer }: { answer: PathViewAnswer }) {
  const nav = useReaderNav();
  const children = answer.children ?? [];
  const subtree = answer.subtree;
  return (
    <Section title="Knowledge below this directory" testid="reader-directory-children">
      {children.length === 0 ? (
        <p className={muted}>No entry is recorded below this directory.</p>
      ) : (
        <ul className={list}>
          {children.map((child) => (
            <li key={child.path} data-testid="directory-child" data-path={child.path}>
              <PathLink path={child.path} />{' '}
              <span className={muted}>
                {child.entries} entr{child.entries === 1 ? 'y' : 'ies'}
              </span>
            </li>
          ))}
        </ul>
      )}
      {subtree && subtree.entries > 0 ? (
        <button
          type="button"
          data-testid="open-subtree"
          onClick={() =>
            nav.go({ repo: nav.repo, commit: nav.commit, view: 'subtree', path: answer.path })
          }
        >
          list all {subtree.entries} entries under{' '}
          {answer.path === '.' ? 'the repository' : answer.path}
        </button>
      ) : null}
    </Section>
  );
}

export function PathView({ answer }: { answer: PathViewAnswer }) {
  return (
    <div data-testid="reader-path-view" data-kind={answer.kind}>
      <h2 className={heading}>
        {pathLabel(answer)} {answer.path === '.' ? '' : answer.path}
      </h2>
      {/* A directory leads with its knowledge summary; its overview prose follows. */}
      {answer.kind === 'directory' ? <DirectorySummary answer={answer} /> : null}
      <ProseWithReferences
        read={answer.prose}
        references={answer.references.items}
        referencesState={answer.references}
      />
      <InvariantsSection answer={answer} />
      <Section title="Families" testid="reader-families">
        {answer.families.length === 0 ? (
          <p className={muted}>No family contains these invariants or routes over this path.</p>
        ) : (
          <ul className={list}>
            {answer.families.map((family) => (
              <FamilyItem key={family.id} family={family} />
            ))}
          </ul>
        )}
      </Section>
      <Section title="Decisions, incidents and other records" testid="reader-linked-records">
        {answer.records.length === 0 ? (
          <p className={muted}>No record links to this path or its invariants.</p>
        ) : (
          <ul className={list}>
            {answer.records.map((row) => (
              <LinkedRecordItem key={row.record.id} row={row} />
            ))}
          </ul>
        )}
      </Section>
    </div>
  );
}

export function WithoutProofView({ answer }: { answer: WithoutProofAnswer }) {
  return (
    <div data-testid="reader-without-proof">
      <h2 className={heading}>
        Invariants without proof{answer.path ? ` under ${answer.path}` : ''}
      </h2>
      <p className={muted}>
        {answer.invariants.length} shown of {answer.total} live invariant(s) that no proof entry
        names. Information, not a gate.
      </p>
      <ul className={list}>
        {answer.invariants.map((row) => (
          <li key={row.id} className={card} data-testid="without-proof-row">
            <RecordLink summary={row} />
            <div className={muted}>
              {row.realizationPaths.map((path) => (
                <span key={path}>
                  <PathLink path={path} />{' '}
                </span>
              ))}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function CensusView({ answer }: { answer: CensusAnswer }) {
  if (answer.censuses.length === 0) {
    return (
      <p className={muted} data-testid="reader-census">
        {answer.detail ?? 'No census is recorded under knowledge/census/ in this tree.'}
      </p>
    );
  }
  return (
    <div data-testid="reader-census">
      {(answer.problems ?? []).length > 0 ? (
        <p className={muted} data-testid="census-problems">
          {answer.problems?.length} census file problem(s):{' '}
          {answer.problems?.map((one) => `${one.path}: ${one.message}`).join('; ')}
        </p>
      ) : null}
      {answer.censuses.map((report) => (
        <section key={report.census} className={card} data-census={report.census}>
          <h2 className={heading}>Census {report.census}</h2>
          <div className={muted}>
            {report.inventory.sources} source file(s), {report.inventory.artifacts} onboarding
            artifact(s), {report.inventory.routes} route(s)
          </div>
          <Section title="Measures" testid="census-measures">
            <ul className={list}>
              {report.measures.map((measure) => (
                <li key={measure.name} data-testid="census-measure">
                  {measure.name}: {measure.state}
                  {measure.denominator !== undefined
                    ? ` (${measure.numerator ?? 0}/${measure.denominator})`
                    : ''}
                  {measure.reason ? <span className={muted}> — {measure.reason}</span> : null}
                </li>
              ))}
            </ul>
            <div className={muted}>
              counts:{' '}
              {Object.entries(report.counts)
                .map(([name, count]) => `${name} ${count}`)
                .join(' · ')}
            </div>
          </Section>
          <Section title="Routes" testid="census-routes">
            <div className={muted}>
              {Object.entries(report.routeStatuses)
                .map(([status, count]) => `${status} ${count}`)
                .join(' · ')}
            </div>
            <ul className={list}>
              {report.routes.map((route) => (
                <li key={route.route} data-testid="census-route">
                  <PathLink path={route.route} /> <StateBadge state={route.status} />
                  {route.history.length > 0 ? (
                    <span className={muted}>
                      {' '}
                      history:{' '}
                      {route.history
                        .map(
                          (entry) =>
                            `${entry.status}${entry.provenance?.at ? ` @ ${entry.provenance.at}` : ''}`,
                        )
                        .join(' → ')}
                    </span>
                  ) : (
                    <span className={muted}> no status recorded</span>
                  )}
                </li>
              ))}
            </ul>
          </Section>
        </section>
      ))}
    </div>
  );
}

function shownLines(answer: CodeAnswer, total: number) {
  const span = answer.locator?.state === 'resolved' ? answer.locator.lines : undefined;
  const [first, last] = span ?? [1, Math.min(total, 80)];
  return { span, first, last, from: Math.max(1, first - 3), to: Math.min(total, last + 3) };
}

function CodeLines({ answer }: { answer: CodeAnswer }) {
  const lines = (answer.text ?? '').split('\n');
  const { span, first, last, from, to } = shownLines(answer, lines.length);
  return (
    <>
      {span ? (
        <p className={muted}>
          lines {first}–{last}
        </p>
      ) : null}
      <pre className={codeBlock} data-testid="reader-code-lines">
        {lines.slice(from - 1, to).map((text, offset) => {
          const number = from + offset;
          const inside = span !== undefined && number >= first && number <= last;
          return (
            <span
              key={number}
              className={inside ? located : undefined}
              data-line={number}
              data-located={inside ? 'true' : undefined}
            >
              {String(number).padStart(5, ' ')} {text}
              {'\n'}
            </span>
          );
        })}
      </pre>
    </>
  );
}

export function CodeView({ answer }: { answer: CodeAnswer }) {
  if (answer.state !== 'present') {
    return (
      <p className={muted} data-testid="reader-code">
        {answer.path}: {answer.state} {answer.detail ? `— ${answer.detail}` : ''}
      </p>
    );
  }
  return (
    <div data-testid="reader-code" data-locator={answer.locator?.state ?? 'none'}>
      <h2 className={heading}>
        <PathLink path={answer.path} />{' '}
        <span className={muted}>at blob {answer.blob?.slice(0, 12)}</span>
      </h2>
      {answer.locator?.state === 'unresolved' ? (
        <p className={muted} data-testid="locator-unresolved">
          The locator does not resolve here: {answer.locator.detail}
        </p>
      ) : null}
      <CodeLines answer={answer} />
    </div>
  );
}

// --- a directory's subtree, page by page -----------------------------------------------------------

function SubtreeRowItem({ row, state }: { row: SubtreeRow; state?: string | null }) {
  return (
    <li data-testid="subtree-row" data-entry={row.id}>
      <PathLink path={row.path} />{' '}
      <span className={muted}>{locatorLabel({ locator: row.locator ?? { kind: 'file' } })}</span>{' '}
      <strong>{row.id}</strong> <span className={muted}>{row.kind}</span>{' '}
      <RecordLink summary={{ id: row.invariant }} />
      <StateBadge state={state} />
      {row.facet ? <span className={muted}> · {row.facet}</span> : null}
    </li>
  );
}

// The pages read so far, and the next one on request.
function useSubtreePages(answer: SubtreeAnswer) {
  const nav = useReaderNav();
  const [pages, setPages] = useState<SubtreeAnswer[]>([answer]);
  const [failure, setFailure] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  // One page in flight at a time: a second click before it lands asks nothing more (R2 note).
  const inFlight = useRef(false);
  const last = pages[pages.length - 1];
  const more = () => {
    if (inFlight.current || !last.continuation) return;
    inFlight.current = true;
    setLoading(true);
    readSubtree(
      { repo: nav.repo, commit: nav.commit, view: 'subtree', path: answer.directory },
      last.continuation,
    )
      .then(
        (next) =>
          next.state === 'view'
            ? setPages((known) => [...known, next])
            : setFailure(`${next.state}: ${next.detail ?? ''}`),
        (error: unknown) => setFailure(String(error)),
      )
      .finally(() => {
        inFlight.current = false;
        setLoading(false);
      });
  };
  const states: Record<string, string | null> = {};
  for (const page of pages) Object.assign(states, page.states ?? {});
  return { rows: pages.flatMap((page) => page.rows ?? []), states, last, failure, more, loading };
}

function SubtreeRefused({ answer }: { answer: SubtreeAnswer }) {
  return (
    <p className={muted} data-testid="reader-subtree-refused">
      {answer.state} {answer.code ? `(${answer.code})` : ''}: {answer.detail}
    </p>
  );
}

function SubtreeNextPageFailure({ failure }: { failure: string | null }) {
  if (!failure) return null;
  return (
    <p className={muted} data-testid="subtree-failed">
      the next page could not be read: {failure}
    </p>
  );
}

/** Every entry under a directory, in the pages the server cut; "more" follows the continuation. */
export function SubtreeView({ answer }: { answer: SubtreeAnswer }) {
  const { rows, states, last, failure, more, loading } = useSubtreePages(answer);
  if (answer.state !== 'view') return <SubtreeRefused answer={answer} />;
  return (
    <div data-testid="reader-subtree">
      <h2 className={heading}>
        Every entry under <PathLink path={answer.directory ?? '.'} />
      </h2>
      <p className={muted} data-testid="subtree-count">
        {rows.length} of {answer.page?.total ?? rows.length} entries shown
      </p>
      {last.statesProblem ? (
        <p className={muted} data-testid="reader-unverifiable">
          states unverifiable: {last.statesProblem}
        </p>
      ) : null}
      <ul className={list}>
        {rows.map((row) => (
          <SubtreeRowItem key={row.id} row={row} state={states[row.invariant]} />
        ))}
      </ul>
      <SubtreeNextPageFailure failure={failure} />
      {last.continuation ? (
        <button type="button" data-testid="subtree-more" onClick={more} disabled={loading}>
          more ({last.page?.remaining ?? 0} remaining)
        </button>
      ) : null}
    </div>
  );
}
