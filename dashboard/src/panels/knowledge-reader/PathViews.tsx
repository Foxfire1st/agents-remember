// The reader's path-side views (MIK-R29 rule 2): a file's path view, a directory's bounded summary
// and its paged subtree, the without-proof list, the census view and a code file opened at its
// locator.
import { useRef, useState } from 'react';

import { css } from '../../../styled-system/css';
import {
  locatorLabel,
  readSubtree,
  type CensusAnswer,
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
  ReferenceList,
  RecordLink,
  Section,
  StateBadge,
  card,
  list,
  muted,
  useReaderNav,
} from './readerParts';

const heading = css({ margin: '0 0 0.3rem', fontSize: '0.95rem', overflowWrap: 'anywhere' });
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
    <Section
      title={`${invariantsTitle(answer)} (${answer.invariants.length})`}
      testid="reader-invariants"
    >
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

function Breadcrumbs({ path }: { path: string }) {
  const parts = path === '.' ? [] : path.split('/');
  return (
    <nav
      aria-label="Document path"
      className={css({
        position: 'sticky',
        top: '0',
        background: 'bgPanel',
        zIndex: '1',
        paddingBlock: '0.35rem',
        fontSize: '0.72rem',
      })}
    >
      <PathLink path="." label="repository" />
      {parts.map((part, index) => (
        <span key={index}>
          {' '}
          / <PathLink path={parts.slice(0, index + 1).join('/')} label={part} />
        </span>
      ))}
    </nav>
  );
}

function DocumentFacts({ answer }: { answer: PathViewAnswer }) {
  const nav = useReaderNav();
  const facts = [
    ['reader-invariants', 'invariants', answer.invariants.length],
    ['reader-families', 'families', answer.families.length],
    ['reader-linked-records', 'records', answer.records.length],
    ['reader-references-section', 'references', answer.references.items.length],
  ] as const;
  return (
    <div
      data-testid="reader-facts"
      className={css({
        display: 'flex',
        flexWrap: 'wrap',
        gap: '0.7rem',
        color: 'muted',
        fontSize: '0.75rem',
        marginBottom: '0.8rem',
      })}
    >
      {facts.map(([id, label, count]) =>
        id === 'reader-references-section' && answer.references.state === 'unavailable' ? (
          <span key={id}>references unavailable</span>
        ) : count ? (
          <a
            key={id}
            href={`#${id}`}
            onClick={(event) => {
              event.preventDefault();
              document.getElementById(id)?.scrollIntoView({ block: 'start' });
            }}
          >
            {count} {label}
          </a>
        ) : (
          <span key={id}>0 {label}</span>
        ),
      )}
      {answer.subtree?.entries ? (
        <button
          type="button"
          data-testid="open-subtree"
          onClick={() =>
            nav.go({ repo: nav.repo, commit: nav.commit, view: 'subtree', path: answer.path })
          }
        >
          all {answer.subtree.entries} entries
        </button>
      ) : null}
    </div>
  );
}

export function PathView({ answer }: { answer: PathViewAnswer }) {
  const text = answer.prose.text ?? '';
  const first = /^#\s+(.+)$/m.exec(text);
  const title = first?.[1] ?? `${pathLabel(answer)} ${answer.path === '.' ? '' : answer.path}`;
  const body = first
    ? text.slice(0, first.index) + text.slice(first.index + first[0].length)
    : text;
  return (
    <article data-testid="reader-path-view" data-kind={answer.kind}>
      <Breadcrumbs path={answer.path} />
      <h1 className={css({ fontSize: '1.35rem', color: 'amber', margin: '0.5rem 0' })}>{title}</h1>
      <DocumentFacts answer={answer} />
      <ProseWithReferences
        read={{ ...answer.prose, text: body }}
        references={answer.references.items}
        referencesState={answer.references}
      />
      {answer.currentness.unverifiableReason && !answer.invariants.length ? (
        <p className={muted} data-testid="reader-unverifiable">
          states unverifiable: {answer.currentness.unverifiableReason}
        </p>
      ) : null}
      <DocumentSections answer={answer} text={text} />
    </article>
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
          <li
            key={row.id}
            data-testid="without-proof-row"
            title={`${row.title ?? row.id} · ${row.realizationPaths.join(' · ')}`}
            className={css({
              display: 'flex',
              gap: '0.5rem',
              whiteSpace: 'nowrap',
              overflowX: 'auto',
              overflowY: 'hidden',
              scrollbarWidth: 'none',
              '& > *': { flexShrink: 0 },
              '& button': { whiteSpace: 'nowrap' },
            })}
          >
            <RecordLink summary={row} />
            <span className={muted}>
              {row.realizationPaths.map((path) => (
                <span key={path}>
                  <PathLink path={path} />{' '}
                </span>
              ))}
            </span>
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

// --- a directory's subtree, page by page -----------------------------------------------------------

function SubtreeRowItem({ row, state }: { row: SubtreeRow; state?: string | null }) {
  return (
    <li
      data-testid="subtree-row"
      data-entry={row.id}
      className={css({ whiteSpace: 'nowrap', overflowX: 'auto' })}
    >
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

function DocumentSections({ answer, text }: { answer: PathViewAnswer; text: string }) {
  return (
    <>
      {answer.invariants.length ? <InvariantsSection answer={answer} /> : null}
      {answer.families.length ? (
        <Section title={`Families (${answer.families.length})`} testid="reader-families">
          <ul className={list}>
            {answer.families.map((family) => (
              <FamilyItem key={family.id} family={family} />
            ))}
          </ul>
        </Section>
      ) : null}
      {answer.records.length ? (
        <Section
          title={`Decisions, incidents and other records (${answer.records.length})`}
          testid="reader-linked-records"
        >
          <ul className={list}>
            {answer.records.map((row) => (
              <LinkedRecordItem key={row.record.id} row={row} />
            ))}
          </ul>
        </Section>
      ) : null}
      {answer.references.items.length ? (
        <Section
          title={`References (${answer.references.items.length})`}
          testid="reader-references-section"
        >
          <ReferenceList references={answer.references.items} text={text} />
        </Section>
      ) : null}
    </>
  );
}
