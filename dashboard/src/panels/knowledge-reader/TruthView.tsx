// The reader's truth view (MIK-R29 rules 3 and 4): one record with every field, its states, its
// links both ways and its timeline, newest first, from the record file's log (meaning diffs), the
// history rows about it and the log of the entries that realize or prove it.
import type { ReactNode } from 'react';
import { css } from '../../../styled-system/css';
import type {
  LinkTarget,
  RecordViewAnswer,
  StatesHeader,
  Timeline,
  TimelineEvent,
} from '../../data/knowledgeReader';
import { locatorLabel } from '../../data/knowledgeReader';
import {
  DecisionCard,
  EntryRow,
  PathLink,
  ProseWithReferences,
  RecordLink,
  Section,
  StateBadge,
  TargetLink,
  card,
  list,
  muted,
} from './readerParts';

const heading = css({ margin: '0 0 0.3rem', fontSize: '0.95rem', overflowWrap: 'anywhere' });
const fields = css({
  display: 'grid',
  gridTemplateColumns: 'minmax(7rem, max-content) 1fr',
  gap: '0.2rem 0.7rem',
  margin: '0',
  fontSize: '0.84rem',
  '& dt': { color: 'muted' },
  '& dd': { margin: '0', overflowWrap: 'anywhere', whiteSpace: 'pre-wrap' },
});
const diff = css({ fontSize: '0.8rem', display: 'grid', gap: '0.15rem' });

const LEAD: Record<string, string> = {
  invariant: 'statement',
  family: 'guarantee',
  decision: 'context',
  incident: 'occurrence',
  assumption: 'proposition',
  limitation: 'limited',
  failure_mode: 'failure',
  scenario: 'situation',
  diagnostic: 'condition',
  term: 'definition',
};
const ORDER: Record<string, string[]> = {
  invariant: ['applicability', 'conditions', 'exclusions'],
  family: ['title'],
  decision: ['consequences', 'decider'],
  incident: ['cause', 'cause_uncertainty', 'recovery', 'corrective_actions'],
  assumption: ['basis', 'scope', 'validation', 'invalidated_when'],
  limitation: ['scope', 'reason', 'impact', 'workaround'],
  failure_mode: ['trigger', 'consequences', 'detection', 'mitigation'],
  scenario: ['given', 'when', 'then'],
  diagnostic: ['signals', 'interpretation', 'action'],
  term: ['term', 'scope'],
};
const FRAME = new Set([
  'schema',
  'id',
  'kind',
  'status',
  'revision',
  'admission',
  'origin',
  'links',
  'alternatives',
  'members',
  'routes',
  'supersedes',
]);

function show(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) return value.map(show).join('; ');
  if (typeof value === 'object')
    return Object.entries(value)
      .map(([key, item]) => `${key.replace(/_/g, ' ')}: ${show(item)}`)
      .join('; ');
  return String(value);
}
function Value({ value }: { value: unknown }): ReactNode {
  if (Array.isArray(value) && value.length === 0) return <span className={muted}>(none)</span>;
  if (Array.isArray(value))
    return (
      <ul className={list}>
        {value.map((item, index) => (
          <li key={index}>
            <Value value={item} />
          </li>
        ))}
      </ul>
    );
  if (value && typeof value === 'object')
    return (
      <dl className={fields}>
        {Object.entries(value).map(([key, item]) => (
          <div key={key} style={{ display: 'contents' }}>
            <dt>{key.replace(/_/g, ' ')}</dt>
            <dd>
              <Value value={item} />
            </dd>
          </div>
        ))}
      </dl>
    );
  return show(value);
}
function RecordFields({ document, kind }: { document: Record<string, unknown>; kind: string }) {
  const order = [...(ORDER[kind] ?? []), ...Object.keys(document).sort()];
  const names = [...new Set(order)].filter(
    (name) => name in document && !FRAME.has(name) && name !== LEAD[kind],
  );
  return (
    <div data-testid="record-fields">
      {names.map((name) => (
        <div key={name} data-field={name}>
          <h3 className={css({ color: 'muted', fontSize: '0.8rem', margin: '0.8rem 0 0.25rem' })}>
            {name.replace(/_/g, ' ')}
          </h3>
          <Value value={document[name]} />
        </div>
      ))}
    </div>
  );
}
function RecordOrigin({ answer }: { answer: RecordViewAnswer }) {
  const document = answer.record.document;
  return (
    <dl className={fields} data-testid="record-origin">
      <dt>file</dt>
      <dd>{answer.record.path}</dd>
      {['admission', 'origin'].map((name) =>
        document[name] ? (
          <div key={name} style={{ display: 'contents' }} data-field={name}>
            <dt>{name}</dt>
            <dd>
              <Value value={document[name]} />
            </dd>
          </div>
        ) : null,
      )}
    </dl>
  );
}

function States({ header }: { header: StatesHeader }) {
  return (
    <p className={muted} data-testid="record-code-tree">
      states at code tree {header.codeTree ? header.codeTree.treeId.slice(0, 12) : '(none)'}
      {header.unverifiableReason ? ` — unverifiable: ${header.unverifiableReason}` : ''}
    </p>
  );
}

function TruthHeader({ answer }: { answer: RecordViewAnswer }) {
  const { record } = answer;
  // A decision's status is the derived one: `superseded` is never stored (MIK-R13, review F9).
  const status = answer.decision?.derivedStatus ?? record.status;
  return (
    <h2 className={heading}>
      {record.kind} {record.id}
      <StateBadge state={answer.invariant?.state} testid="record-state" />
      {status ? (
        <span className={muted} data-testid="record-status">
          {' '}
          · {status.replace(/_/g, ' ')}
        </span>
      ) : null}
      {record.revision ? <span className={muted}> · revision {record.revision}</span> : null}
    </h2>
  );
}

function OutgoingSection({ answer }: { answer: RecordViewAnswer }) {
  const unreadable = answer.outgoingState;
  if (!unreadable && !answer.outgoing.length)
    return (
      <p className={muted} data-testid="record-outgoing">
        This record records no outgoing link.
      </p>
    );
  return (
    <Section
      title={
        unreadable ? 'Outgoing links (unavailable)' : `Outgoing links (${answer.outgoing.length})`
      }
      testid="record-outgoing"
    >
      {unreadable ? (
        <p className={muted} data-testid="record-outgoing-unavailable">
          the record&apos;s links could not be read: {unreadable.detail}
        </p>
      ) : null}
      {answer.outgoing.length === 0 && !unreadable ? (
        <p className={muted}>This record records no outgoing link.</p>
      ) : (
        <ul className={list}>
          {answer.outgoing.map((link, index) => (
            <li key={index} data-relation={link.relation}>
              <span className={muted}>{link.relation}</span>
              {link.alternative !== undefined ? (
                <span className={muted}> (alternative {link.alternative})</span>
              ) : null}{' '}
              <TargetLink target={link.target} />
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

function IncomingSource({ link }: { link: RecordViewAnswer['incoming'][number] }) {
  if (link.sourceRecord) return <RecordLink summary={link.sourceRecord} />;
  if (link.sourceKind === 'file' || link.sourceKind === 'route') {
    return <PathLink path={link.source} />;
  }
  // A history row has no page of its own: the link names the record the row is about and leads
  // there (MIK-R29 rule 5).
  if (link.sourceSubject) {
    return (
      <>
        <RecordLink summary={link.sourceSubject} /> <span className={muted}>row {link.source}</span>
      </>
    );
  }
  return <span>{link.source}</span>;
}

function IncomingSection({ answer }: { answer: RecordViewAnswer }) {
  if (!answer.incoming.length)
    return (
      <p className={muted} data-testid="record-incoming">
        Nothing in this tree links to this record.
      </p>
    );
  return (
    <Section title={`Incoming links (${answer.incoming.length})`} testid="record-incoming">
      {answer.incoming.length === 0 ? (
        <p className={muted}>Nothing in this tree links to this record.</p>
      ) : (
        <ul className={list}>
          {answer.incoming.map((link, index) => (
            <li key={index} data-relation={link.relation}>
              <IncomingSource link={link} />{' '}
              <span className={muted}>
                {link.relation} ({link.sourceKind}, {link.originPath})
              </span>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

export function TruthView({ answer }: { answer: RecordViewAnswer }) {
  const { record } = answer;
  return (
    <div data-testid="reader-truth-view" data-kind={record.kind} data-record={record.id}>
      <TruthHeader answer={answer} />
      <p
        data-testid="record-lead"
        className={css({ fontSize: '1.05rem', lineHeight: '1.6', margin: '0.6rem 0' })}
      >
        {show(record.document[LEAD[record.kind ?? '']] ?? record.title)}
      </p>
      <RecordFields document={record.document} kind={record.kind ?? ''} />
      {answer.prose.state === 'present' ? (
        <Section title="Explanation" testid="record-prose">
          <ProseWithReferences read={answer.prose} references={[]} />
        </Section>
      ) : null}
      {answer.invariant ? <InvariantPart part={answer.invariant} /> : null}
      {answer.family ? <FamilyPart part={answer.family} /> : null}
      {answer.decision ? (
        <Section title="Decision" testid="record-decision">
          <DecisionCard decision={answer.decision} detailsOnly />
        </Section>
      ) : null}
      <OutgoingSection answer={answer} />
      <IncomingSection answer={answer} />
      <TimelineSection timeline={answer.timeline} />
      <RecordOrigin answer={answer} />
    </div>
  );
}

function InvariantPart({ part }: { part: NonNullable<RecordViewAnswer['invariant']> }) {
  return (
    <>
      {part.realizations.length ? (
        <Section
          title={`Realizations (${part.realizations.length})`}
          testid="invariant-realizations"
        >
          <States header={part.currentness} />
          {part.realizations.length === 0 ? (
            <p className={muted}>No realization entry names this invariant.</p>
          ) : (
            <ul className={list}>
              {part.realizations.map((entry) => (
                <EntryRow key={entry.id} entry={entry} />
              ))}
            </ul>
          )}
        </Section>
      ) : null}
      {part.proofs.length ? (
        <Section title={`Proofs (${part.proofs.length})`} testid="invariant-proofs">
          {part.proofs.length === 0 ? (
            <p className={muted}>No proof entry names this invariant.</p>
          ) : (
            <ul className={list}>
              {part.proofs.map((entry) => (
                <EntryRow key={entry.id} entry={entry} />
              ))}
            </ul>
          )}
        </Section>
      ) : null}
      {part.families.length ? (
        <Section title={`Families (${part.families.length})`} testid="invariant-families">
          {part.families.length === 0 ? (
            <p className={muted}>No family contains this invariant.</p>
          ) : (
            <ul className={list}>
              {part.families.map((family) => (
                <li key={family.id}>
                  <RecordLink summary={family} />
                </li>
              ))}
            </ul>
          )}
        </Section>
      ) : null}
      {part.linked.length ? (
        <Section
          title={`Decisions, incidents and other records (${part.linked.length})`}
          testid="invariant-linked"
        >
          {part.linked.length === 0 ? (
            <p className={muted}>No record links to this invariant.</p>
          ) : (
            <ul className={list}>
              {part.linked.map((row) => (
                <li key={row.record.id} data-record={row.record.id}>
                  {row.decision ? (
                    <DecisionCard decision={row.decision} />
                  ) : (
                    <RecordLink summary={row.record} />
                  )}
                  <div className={muted}>{row.links.map((link) => link.relation).join(' · ')}</div>
                </li>
              ))}
            </ul>
          )}
        </Section>
      ) : null}
    </>
  );
}

function FamilyPart({ part }: { part: NonNullable<RecordViewAnswer['family']> }) {
  return (
    <>
      {part.members.length ? (
        <Section title={`Members (${part.members.length})`} testid="family-members">
          <States header={part.currentness} />
          <ul className={list}>
            {part.members.map((member) => (
              <li key={member.id} className={card} data-member={member.id}>
                <div>
                  <RecordLink summary={member} />
                  <StateBadge state={member.state} />
                </div>
                {member.statement ? <div className={muted}>{member.statement}</div> : null}
              </li>
            ))}
          </ul>
          {part.staleMembers.length > 0 ? (
            <p className={muted}>{part.staleMembers.length} stale member(s)</p>
          ) : null}
        </Section>
      ) : null}
      {part.routes.length ? (
        <Section title={`Routes (${part.routes.length})`} testid="family-routes">
          {part.routes.length === 0 ? (
            <p className={muted}>This family records no route yet.</p>
          ) : (
            <ul className={list}>
              {part.routes.map((route) => (
                <li key={route}>
                  <PathLink path={route} />
                </li>
              ))}
            </ul>
          )}
        </Section>
      ) : null}
      {part.locations.length ? (
        <Section title={`Member locations (${part.locations.length})`} testid="family-locations">
          <ul className={list}>
            {part.locations.map((location) => (
              <li key={location.path}>
                <PathLink path={location.path} />
                <ul className={list}>
                  {location.entries.map((entry) => (
                    <EntryRow key={entry.id} entry={entry} showInvariant />
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        </Section>
      ) : null}
    </>
  );
}

// A history row's `because` (MIK-R07 rule 2): a record ID navigates, a requirement is named.
function becauseTargets(document: TimelineEvent['document']): LinkTarget[] {
  const because = document?.because;
  if (!Array.isArray(because)) return [];
  return because.map((one) =>
    typeof one === 'string'
      ? { kind: 'record', id: one }
      : { kind: 'requirement', requirement: one as Record<string, unknown> },
  );
}

function HistoryLine({ event }: { event: TimelineEvent }) {
  const reason = event.document?.reason;
  const effect = event.document?.effect;
  const because = becauseTargets(event.document);
  return (
    <>
      <strong>{event.owner}</strong> {event.disposition}
      {typeof effect === 'string' ? <span data-testid="history-effect"> · {effect}</span> : null}
      {event.closed === false ? <span className={muted}> (open)</span> : null}
      {because.length > 0 ? (
        <div data-testid="history-because">
          <span className={muted}>because</span>
          {because.map((target, index) => (
            <span key={index}>
              {index > 0 ? ',' : ''} <TargetLink target={target} />
            </span>
          ))}
        </div>
      ) : null}
      {typeof reason === 'string' ? <div className={muted}>{reason}</div> : null}
    </>
  );
}

function Side({ word, side }: { word: string; side: TimelineEvent['before'] }) {
  if (!side?.path) return null;
  return (
    <span className={muted}>
      {' '}
      {word} {side.path} {locatorLabel(side.anchor)}
    </span>
  );
}

function EntryLine({ event }: { event: TimelineEvent }) {
  return (
    <>
      {event.entry} {event.change}
      <Side word="from" side={event.before} />
      <Side word="to" side={event.after} />
    </>
  );
}

function RecordLine({ event }: { event: TimelineEvent }) {
  const meaning = event.meaning ?? [];
  const added = event.change === 'added';
  return (
    <>
      record {event.change}
      {!added && meaning.length > 0 ? (
        <div className={diff} data-testid="meaning-diff">
          {meaning.map((change) => (
            <div key={change.field} data-field={change.field}>
              <span className={muted}>{change.field}:</span>{' '}
              {added ? show(change.after) : `${show(change.before)} → ${show(change.after)}`}
            </div>
          ))}
        </div>
      ) : null}
    </>
  );
}

function EventLine({ event }: { event: TimelineEvent }) {
  if (event.source === 'history') return <HistoryLine event={event} />;
  if (event.source === 'entries') return <EntryLine event={event} />;
  return <RecordLine event={event} />;
}

function TimelineSection({ timeline }: { timeline: Timeline }) {
  return (
    <Section title="Timeline" testid="record-timeline">
      <p className={muted} data-testid="timeline-sources">
        {Object.entries(timeline.sources).map(([name, source]) =>
          source.state === 'read' ? (
            <span key={name}>{`${name}: ${source.events ?? 0} `}</span>
          ) : (
            // A source that could not be read is named on the timeline, never shown as no history.
            <span key={name} data-testid="timeline-source-unavailable" data-source={name}>
              {`${name}: ${source.state}${source.detail ? ` (${source.detail})` : ''} `}
            </span>
          ),
        )}
      </p>
      <ol className={list}>
        {timeline.events.map((event, index) => (
          <li
            key={index}
            className={card}
            data-testid="timeline-event"
            data-source={event.source}
            data-change={event.change ?? event.disposition ?? ''}
          >
            <div className={muted}>
              {event.commit ? event.commit.slice(0, 9) : 'uncommitted'}
              {event.date ? ` · ${event.date}` : ''}
              {event.subject && event.commit ? ` · ${event.subject}` : ''} · {event.source}
            </div>
            <div>
              <EventLine event={event} />
            </div>
          </li>
        ))}
      </ol>
    </Section>
  );
}
