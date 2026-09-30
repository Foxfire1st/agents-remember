// Shared pieces of the knowledge reader (MIK-R29): navigation links, state badges, a decision in
// full, and onboarding prose whose `[n]` markers link to their resolved references.
//
// Every link is a navigation: it changes the reader's address (and so the URL), never local state.
import { createContext, useContext, useState, type ReactNode } from 'react';
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';

import { css, cx } from '../../../styled-system/css';
import {
  codeAddress,
  type Alternative,
  locatorLabel,
  type DecisionView,
  type EntryView,
  type FileRead,
  type LinkTarget,
  type ReaderAddress,
  type RecordSummary,
  type ReferenceItem,
  type ReferenceTarget,
} from '../../data/knowledgeReader';

export interface ReaderNav {
  repo: string;
  commit: string;
  go: (address: ReaderAddress) => void;
}

export const ReaderNavContext = createContext<ReaderNav | null>(null);

export function useReaderNav(): ReaderNav {
  const nav = useContext(ReaderNavContext);
  if (!nav) throw new Error('reader links render inside the knowledge reader');
  return nav;
}

// --- styles -------------------------------------------------------------------------------------

export const sectionTitle = css({
  margin: '0.9rem 0 0.35rem',
  fontSize: '0.74rem',
  letterSpacing: '0.08em',
  textTransform: 'uppercase',
  color: 'amber',
});
export const muted = css({ color: 'muted' });
export const list = css({
  listStyle: 'none',
  margin: '0',
  padding: '0',
  display: 'grid',
  gap: '0.3rem',
});
export const card = css({
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '3px',
  padding: '0.45rem 0.6rem',
  display: 'grid',
  gap: '0.25rem',
  minWidth: '0',
});
const linkButton = css({
  font: 'inherit',
  color: 'cyan',
  background: 'transparent',
  border: '0',
  padding: '0',
  cursor: 'pointer',
  textAlign: 'left',
  textDecoration: 'underline',
  overflowWrap: 'anywhere',
});
const badge = css({
  display: 'inline-block',
  fontSize: '0.68rem',
  letterSpacing: '0.05em',
  textTransform: 'uppercase',
  paddingInline: '0.3rem',
  borderRadius: '2px',
  borderWidth: '1px',
  borderStyle: 'solid',
  marginInline: '0.25rem',
  whiteSpace: 'nowrap',
});
const BADGE_TONE: Record<string, string> = {
  current: css({ color: 'mint', borderColor: 'mint' }),
  active: css({ color: 'mint', borderColor: 'mint' }),
  chosen: css({ color: 'mint', borderColor: 'mint' }),
  stale: css({ color: 'alarm', borderColor: 'alarm' }),
  superseded: css({ color: 'alarm', borderColor: 'alarm' }),
  rejected: css({ color: 'alarm', borderColor: 'alarm' }),
  unverifiable: css({ color: 'amber', borderColor: 'amber' }),
  unrealized: css({ color: 'amber', borderColor: 'amber' }),
  partial: css({ color: 'amber', borderColor: 'amber' }),
  under_reconsideration: css({ color: 'amber', borderColor: 'amber' }),
  deferred: css({ color: 'amber', borderColor: 'amber' }),
};
const prose = css({
  fontSize: '0.86rem',
  lineHeight: '1.55',
  '& p': { margin: '0 0 0.5rem', maxWidth: '82ch' },
  '& h1, & h2, & h3, & h4': {
    fontSize: '0.8rem',
    letterSpacing: '0.04em',
    textTransform: 'uppercase',
    color: 'amber',
    margin: '0.7rem 0 0.3rem',
  },
  '& code': { fontSize: '0.86em', background: 'bg', paddingInline: '0.25rem' },
  '& pre': { background: 'bg', padding: '0.5rem', overflow: 'auto' },
  '& ul, & ol': { paddingLeft: '1.2rem' },
  '& table': {
    borderCollapse: 'collapse',
    fontSize: '0.8rem',
    display: 'block',
    overflowX: 'auto',
  },
  '& th, & td': {
    borderWidth: '1px',
    borderStyle: 'solid',
    borderColor: 'grid',
    padding: '0.2rem 0.4rem',
  },
});
const marker = css({
  font: 'inherit',
  fontSize: '0.78em',
  verticalAlign: 'super',
  color: 'cyan',
  background: 'transparent',
  border: '0',
  padding: '0 0.1rem',
  cursor: 'pointer',
});
const highlighted = css({ outline: '1px solid', outlineColor: 'cyan' });

// --- small parts ------------------------------------------------------------------------------------

export function StateBadge({ state, testid }: { state?: string | null; testid?: string }) {
  if (!state) return null;
  return (
    <span className={cx(badge, BADGE_TONE[state] ?? '')} data-testid={testid} data-state={state}>
      {state.replace(/_/g, ' ')}
    </span>
  );
}

export function Section({
  title,
  testid,
  children,
}: {
  title: string;
  testid?: string;
  children: ReactNode;
}) {
  return (
    <section data-testid={testid}>
      <h3 className={sectionTitle}>{title}</h3>
      {children}
    </section>
  );
}

/** A part of the view that could not be read: named, never shown as empty. */
export function Unavailable({ what, detail }: { what: string; detail?: string | null }) {
  return (
    <p className={muted} data-testid="reader-unavailable">
      {what} unavailable{detail ? ` — ${detail}` : ''}
    </p>
  );
}

export function RecordLink({ summary }: { summary: RecordSummary }) {
  const nav = useReaderNav();
  const label =
    summary.title && summary.title !== summary.id ? `${summary.id} · ${summary.title}` : summary.id;
  if (summary.missing) {
    return (
      <span className={muted} data-testid="record-missing">
        {summary.id} (not in this tree)
      </span>
    );
  }
  return (
    <button
      type="button"
      className={linkButton}
      data-testid="record-link"
      data-record={summary.id}
      onClick={() => nav.go({ repo: nav.repo, commit: nav.commit, view: 'record', id: summary.id })}
    >
      {label}
    </button>
  );
}

export function PathLink({ path, label }: { path: string; label?: string }) {
  const nav = useReaderNav();
  return (
    <button
      type="button"
      className={linkButton}
      data-testid="path-link"
      data-path={path}
      onClick={() => nav.go({ repo: nav.repo, commit: nav.commit, view: 'path', path })}
    >
      {label ?? path}
    </button>
  );
}

export function CodeLink({ path, anchor }: { path: string; anchor: EntryView['anchor'] }) {
  const nav = useReaderNav();
  if (!anchor) return <PathLink path={path} />;
  return (
    <button
      type="button"
      className={linkButton}
      data-testid="code-link"
      data-path={path}
      onClick={() => nav.go(codeAddress(nav, anchor, path))}
    >
      {path} · {locatorLabel(anchor)}
    </button>
  );
}

function OtherTarget({ target }: { target: ReferenceTarget | LinkTarget }) {
  if (target.kind === 'requirement' && target.requirement) {
    const requirement = target.requirement as { id?: string; version?: string; packet?: string };
    return (
      <span className={muted}>
        requirement {requirement.id}@{requirement.version} ({requirement.packet})
      </span>
    );
  }
  const described = target as ReferenceTarget;
  return (
    <span className={muted}>{described.text ?? JSON.stringify(described.document ?? target)}</span>
  );
}

function targetRecord(target: ReferenceTarget | LinkTarget): RecordSummary | undefined {
  const summary = (target as ReferenceTarget).record;
  if (summary) return summary;
  return target.id ? { id: target.id } : undefined;
}

function CodeTarget({ target }: { target: ReferenceTarget | LinkTarget }) {
  const path = target.path ?? target.anchor?.path ?? '';
  return <CodeLink path={path} anchor={target.anchor} />;
}

/** One end of a link or reference, as a navigation to what it names. */
export function TargetLink({ target }: { target: ReferenceTarget | LinkTarget }) {
  if (target.kind === 'code' || target.kind === 'test') return <CodeTarget target={target} />;
  if (target.kind === 'route') {
    return <PathLink path={target.path ?? '.'} label={`route ${target.path}`} />;
  }
  const record = targetRecord(target);
  return record ? <RecordLink summary={record} /> : <OtherTarget target={target} />;
}

// --- entries and decisions -------------------------------------------------------------------------

export function EntryRow({
  entry,
  showInvariant = false,
}: {
  entry: EntryView;
  showInvariant?: boolean;
}) {
  return (
    <li
      className={card}
      data-testid="reader-entry"
      data-entry={entry.id}
      data-state={entry.state ?? ''}
    >
      <div>
        <strong>{entry.id}</strong> <span className={muted}>{entry.kind}</span>
        <StateBadge state={entry.state} />
        {entry.role ? <span className={muted}> · {entry.role}</span> : null}
      </div>
      <div>
        <CodeLink path={entry.path} anchor={entry.anchor} />
      </div>
      {showInvariant ? (
        <div>
          <RecordLink summary={{ id: entry.invariant }} />
        </div>
      ) : null}
      {entry.facet ? <div data-testid="reader-facet">facet: {entry.facet}</div> : null}
      {entry.rationale ? <div className={muted}>{entry.rationale}</div> : null}
      {entry.reason ? <div className={muted}>{entry.reason}</div> : null}
    </li>
  );
}

function AlternativeItem({ alternative }: { alternative: Alternative }) {
  return (
    <li data-alternative={alternative.status}>
      <StateBadge state={alternative.status} /> {alternative.option}
      <div className={muted}>reason: {alternative.reason}</div>
      {alternative.reconsiderWhen ? (
        <div className={muted} data-testid="reconsider-when">
          reconsider when: {alternative.reconsiderWhen}
        </div>
      ) : null}
      {alternative.reconsiderOn.length > 0 ? (
        <div className={muted}>
          reopens on:{' '}
          {alternative.reconsiderOn.map((target, index) => (
            <TargetLink key={index} target={target} />
          ))}
        </div>
      ) : null}
    </li>
  );
}

function RecordRow({
  label,
  records,
  testid,
}: {
  label: string;
  records?: RecordSummary[];
  testid?: string;
}) {
  if (!records || records.length === 0) return null;
  return (
    <div data-testid={testid}>
      {label}:{' '}
      {records.map((one) => (
        <RecordLink key={one.id} summary={one} />
      ))}
    </div>
  );
}

function DecisionHeader({ decision }: { decision: DecisionView }) {
  const recorded =
    decision.storedStatus && decision.storedStatus !== decision.derivedStatus
      ? decision.storedStatus.replace(/_/g, ' ')
      : null;
  return (
    <div>
      <RecordLink summary={{ id: decision.id }} />
      <StateBadge state={decision.derivedStatus} testid="decision-status" />
      {recorded ? <span className={muted}> (recorded {recorded})</span> : null}
    </div>
  );
}

/** A decision in full, wherever it appears (MIK-R13 rule 6). */
export function DecisionCard({ decision }: { decision: DecisionView }) {
  if (decision.unreadable) {
    return <Unavailable what={`decision ${decision.id}`} detail={decision.unreadable} />;
  }
  const governs = decision.governs ?? [];
  return (
    <div className={card} data-testid="decision-card" data-decision={decision.id}>
      <DecisionHeader decision={decision} />
      {decision.context ? <div>{decision.context}</div> : null}
      <ol className={list} data-testid="decision-alternatives">
        {(decision.alternatives ?? []).map((alternative) => (
          <AlternativeItem key={alternative.index} alternative={alternative} />
        ))}
      </ol>
      <RecordRow label="supersedes" records={decision.supersedes} />
      <RecordRow label="superseded by" records={decision.supersededBy} testid="superseded-by" />
      {governs.length > 0 ? (
        <div>
          governs:{' '}
          {governs.map((link, index) => (
            <span key={index}>
              {link.relation} <TargetLink target={link.target} />{' '}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

// --- prose with references ------------------------------------------------------------------------------

// A `[n]` marker in the prose's text. It is linked only in text nodes of the parsed Markdown, so a
// `reports[0]` inside a code span or a fenced block is never rewritten (review F4); a real link or a
// link definition that happens to be numbered is left as authored.
const MARKER = /\[(\d+)\]/g;
const HAS_MARKER = /\[\d+\]/;
// Code spans and fences hold their text in `value`, never in text children, so only an existing
// link's text needs skipping (a marker is never linked inside a link).
const IN_LINK = new Set(['link', 'linkReference']);

interface MdNode {
  type: string;
  value?: string;
  url?: string;
  children?: MdNode[];
}

function splitMarkers(value: string): MdNode[] {
  const nodes: MdNode[] = [];
  let last = 0;
  for (const match of value.matchAll(MARKER)) {
    const at = match.index ?? 0;
    if (at > last) nodes.push({ type: 'text', value: value.slice(last, at) });
    nodes.push({
      type: 'link',
      url: `#reference-${match[1]}`,
      children: [{ type: 'text', value: match[0] }],
    });
    last = at + match[0].length;
  }
  if (last < value.length) nodes.push({ type: 'text', value: value.slice(last) });
  return nodes;
}

function linkMarkers(node: MdNode): void {
  if (!node.children) return;
  node.children = node.children.flatMap((child) => {
    if (child.type === 'text' && child.value && HAS_MARKER.test(child.value)) {
      return splitMarkers(child.value);
    }
    if (!IN_LINK.has(child.type)) linkMarkers(child);
    return [child];
  });
}

/** The remark plugin that turns the prose's `[n]` markers into reference links. */
export function remarkReferenceMarkers() {
  return (tree: unknown) => linkMarkers(tree as MdNode);
}

function ReferenceList({
  references,
  focused,
}: {
  references: ReferenceItem[];
  focused: string | null;
}) {
  if (references.length === 0) return null;
  return (
    <ol className={list} data-testid="reader-references">
      {references.map((item) => (
        <li
          key={item.number}
          id={`knowledge-reference-${item.number}`}
          className={cx(card, focused === item.number ? highlighted : '')}
          data-testid="reader-reference"
          data-reference={item.number}
        >
          <div>
            <strong>[{item.number}]</strong>
            {item.note ? <span className={muted}> {item.note}</span> : null}
          </div>
          {item.targets.map((target, index) => (
            <div key={index} data-testid="reference-target" data-kind={target.kind}>
              <span className={muted}>{target.kind}: </span>
              <TargetLink target={target} />
            </div>
          ))}
        </li>
      ))}
    </ol>
  );
}

/** Onboarding Markdown whose `[n]` markers link to their numbered references, listed below it. */
export function ProseWithReferences({
  read,
  references,
  referencesState,
}: {
  read: FileRead;
  references: ReferenceItem[];
  referencesState?: { state: string; detail?: string };
}) {
  const [focused, setFocused] = useState<string | null>(null);
  const known = new Set(references.map((item) => item.number));
  const components: Components = {
    a: ({ href, children }) => {
      const number = href?.startsWith('#reference-') ? href.slice('#reference-'.length) : null;
      if (number && known.has(number)) {
        return (
          <button
            type="button"
            className={marker}
            data-testid="reference-marker"
            data-reference={number}
            onClick={() => {
              setFocused(number);
              document
                .getElementById(`knowledge-reference-${number}`)
                ?.scrollIntoView?.({ block: 'nearest' });
            }}
          >
            [{number}]
          </button>
        );
      }
      return <span>{children}</span>;
    },
  };
  return (
    <div data-testid="reader-prose">
      {read.state === 'present' ? (
        <div className={prose}>
          <ReactMarkdown
            remarkPlugins={[remarkGfm, remarkReferenceMarkers]}
            components={components}
          >
            {read.text ?? ''}
          </ReactMarkdown>
        </div>
      ) : read.state === 'absent' ? (
        <p className={muted}>No onboarding prose is recorded at {read.path}.</p>
      ) : (
        <Unavailable what={`prose ${read.path}`} detail={read.detail} />
      )}
      {referencesState && referencesState.state === 'unavailable' ? (
        <Unavailable what="references" detail={referencesState.detail} />
      ) : null}
      <ReferenceList references={references} focused={focused} />
    </div>
  );
}
