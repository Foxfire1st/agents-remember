/**
 * The planned-mode review page (MIK-R189 rule 5).
 *
 * A leaf that has not started has no change set, so this page shows the task's intended changes and
 * nothing that only a build could know: the `planned · <effect>` rows, the packets with their body
 * and scope, the Prepared context (or `absent`) and the reference files, under the one top line
 * `planned · no changes`.
 *
 * Its only controls are the deterministic sorts -- declaration order, the declared subject, the
 * effect label -- and navigable code-tip addresses for resolved Scope paths. No control starts a
 * role, a model turn or a build: there is no fetch here and no host channel.
 */

import { useMemo, useState } from 'react';

import { css } from '../../../../../styled-system/css';
import {
  PLANNED_TOP_LINE,
  rowDetail,
  scopeCaption,
  sortRows,
  type PlannedPage,
  type PlannedRow,
  type PlannedSortId,
} from './plannedPage';

const view = css({ display: 'grid', gap: '0.5rem', minWidth: 0, paddingBottom: '1rem' });
const topLine = css({
  fontFamily: 'mono',
  fontSize: '0.78rem',
  color: 'muted',
  margin: 0,
});
const notice = css({ fontFamily: 'mono', fontSize: '0.7rem', color: 'amber', margin: 0 });
const section = css({
  display: 'grid',
  gap: '0.4rem',
  justifyItems: 'start',
  padding: '0.9rem 1rem',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '3px',
  background: 'bgPanel',
});
const heading = css({ fontSize: '0.95rem', margin: 0 });
const rows = css({ display: 'grid', gap: '0.3rem', listStyle: 'none', margin: 0, padding: 0 });
const row = css({ display: 'grid', gap: '0.1rem', minWidth: 0 });
const rowTitle = css({ fontFamily: 'mono', fontSize: '0.78rem', color: 'purple' });
const detail = css({ fontSize: '0.78rem', color: 'muted', margin: 0, maxWidth: '46rem' });
const quiet = css({ fontFamily: 'mono', fontSize: '0.68rem', color: 'muted', margin: 0 });
const sortBar = css({ display: 'flex', gap: '0.3rem', flexWrap: 'wrap' });
const sortButton = css({
  fontFamily: 'mono',
  fontSize: '0.7rem',
  padding: '0.15rem 0.45rem',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '3px',
  background: 'transparent',
  color: 'inherit',
  cursor: 'pointer',
});
const body = css({
  fontSize: '0.8rem',
  color: 'inherit',
  margin: 0,
  maxWidth: '46rem',
  whiteSpace: 'pre-wrap',
});
const list = css({ display: 'grid', gap: '0.25rem', listStyle: 'none', margin: 0, padding: 0 });
const scopeLink = css({
  color: 'cyan',
  textDecoration: 'underline',
  cursor: 'pointer',
  fontFamily: 'mono',
  fontSize: '0.78rem',
});
const treeRow = css({
  display: 'flex',
  gap: '0.4rem',
  alignItems: 'baseline',
  fontFamily: 'mono',
  fontSize: '0.72rem',
  cursor: 'pointer',
});

export interface PlannedTipAddress {
  path: string;
  codeTip: string;
  codeTree: string;
  tipAddress: string | null;
}

export interface PlannedReviewProps {
  /** The page's payload, read by the product's route. Nothing here asks for it. */
  payload: PlannedPage;
  /** Opens a resolved Scope path at the bound code tip. */
  onOpenAddress?: (address: PlannedTipAddress) => void;
}

export function PlannedReview({ payload, onOpenAddress }: PlannedReviewProps) {
  const [sort, setSort] = useState<PlannedSortId>('declared');
  const [selectedSubject, setSelectedSubject] = useState<string | null>(null);
  const [openedAddress, setOpenedAddress] = useState<PlannedTipAddress | null>(null);
  const ordered = useMemo(() => sortRows(payload.rows, sort), [payload.rows, sort]);
  return (
    <section
      className={view}
      data-testid="planned-review"
      data-mode="planned"
      data-comparison={payload.binding.reference}
    >
      <p className={topLine} data-testid="planned-top-line">
        {PLANNED_TOP_LINE}
      </p>
      <p className={notice} data-testid="planned-intended-notice">
        {payload.intendedNotice}
      </p>
      {openedAddress ? (
        <p className={quiet} data-testid="planned-opened-address">
          {`code tip ${openedAddress.codeTree || openedAddress.codeTip} · ${openedAddress.path}`}
        </p>
      ) : null}
      <Effects
        rows={ordered}
        sorts={payload.sorts}
        sort={sort}
        selectedSubject={selectedSubject}
        onSelectSubject={setSelectedSubject}
        onSort={setSort}
      />
      <Packets
        packets={payload.packets}
        tips={payload.tips}
        onOpenAddress={(addr) => {
          setOpenedAddress(addr);
          onOpenAddress?.(addr);
        }}
      />
      <PreparedContext context={payload.preparedContext} />
      <ReferenceFiles files={payload.referenceFiles} />
    </section>
  );
}

function EffectsTree({
  entries,
  selectedSubject,
  onSelectSubject,
}: {
  entries: PlannedRow[];
  selectedSubject: string | null;
  onSelectSubject: (subject: string) => void;
}) {
  return (
    <ul
      role="tree"
      aria-label="Planned knowledge effects tree"
      className={list}
      data-testid="planned-effects-tree"
    >
      {entries.map((entry) => (
        <li
          key={`tree:${entry.subject}#${entry.effect}`}
          role="treeitem"
          tabIndex={0}
          className={treeRow}
          data-testid="planned-tree-row"
          data-subject={entry.subject}
          data-effect={entry.effect}
          data-selected={selectedSubject === entry.subject ? 'true' : undefined}
          aria-selected={selectedSubject === entry.subject}
          onClick={() => onSelectSubject(entry.subject)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' || event.key === ' ') {
              event.preventDefault();
              onSelectSubject(entry.subject);
            }
          }}
        >
          <span data-testid="planned-tree-row-subject">{entry.subject}</span>
          <span className={quiet} data-testid="planned-tree-row-effect">
            {entry.title}
          </span>
        </li>
      ))}
    </ul>
  );
}

/** The `planned · <effect>` rows in both the planned tree and reading list, with deterministic sorts. */
function Effects({
  rows: entries,
  sorts,
  sort,
  selectedSubject,
  onSelectSubject,
  onSort,
}: {
  rows: PlannedRow[];
  sorts: PlannedPage['sorts'];
  sort: PlannedSortId;
  selectedSubject: string | null;
  onSelectSubject: (subject: string) => void;
  onSort: (next: PlannedSortId) => void;
}) {
  return (
    <section className={section} data-part="effects">
      <h3 className={heading}>Planned effects</h3>
      <div className={sortBar} role="group" aria-label="sort the planned effects">
        {sorts.map((entry) => (
          <button
            key={entry.id}
            type="button"
            className={sortButton}
            data-testid={`planned-sort-${entry.id}`}
            aria-pressed={sort === entry.id}
            onClick={() => onSort(entry.id)}
          >
            {entry.label}
          </button>
        ))}
      </div>
      {entries.length === 0 ? (
        <p className={detail} data-testid="planned-effects-none">
          This leaf declares no expected knowledge effect.
        </p>
      ) : (
        <>
          <EffectsTree
            entries={entries}
            selectedSubject={selectedSubject}
            onSelectSubject={onSelectSubject}
          />
          <ul className={rows} data-testid="planned-effects">
            {entries.map((entry) => (
              <li
                key={`${entry.subject}#${entry.effect}`}
                className={row}
                data-testid="planned-row"
                data-subject={entry.subject}
                data-kind={entry.kind}
                data-selected={selectedSubject === entry.subject ? 'true' : undefined}
              >
                <span className={rowTitle} data-testid="planned-row-title">
                  {entry.title}
                </span>
                <p className={detail} data-testid="planned-row-detail">
                  {rowDetail(entry)}
                </p>
                <span className={quiet}>
                  {entry.subject}
                  {entry.requirement ? ` · ${entry.requirement}` : ''}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

function ScopePathItem({
  entry,
  tips,
  onOpenAddress,
}: {
  entry: PlannedPage['packets'][number]['scope'][number];
  tips: PlannedPage['tips'];
  onOpenAddress: (address: PlannedTipAddress) => void;
}) {
  const resolved =
    entry.state === 'resolved' && entry.exists !== false
      ? (entry.resolvedPath ?? entry.path)
      : null;
  const href =
    entry.tipAddress ??
    (resolved ? `#code-tip/${encodeURIComponent(tips.codeTree || tips.code)}/${resolved}` : undefined);
  return (
    <li
      className={detail}
      data-testid="planned-scope-path"
      data-scope={entry.state}
    >
      {resolved && href ? (
        <a
          href={href}
          className={scopeLink}
          data-testid="planned-scope-address"
          data-resolved-path={resolved}
          data-code-tree={tips.codeTree}
          onClick={(event) => {
            event.preventDefault();
            onOpenAddress({
              path: resolved,
              codeTip: tips.code,
              codeTree: tips.codeTree,
              tipAddress: entry.tipAddress ?? null,
            });
          }}
        >
          {scopeCaption(entry)}
        </a>
      ) : (
        scopeCaption(entry)
      )}
    </li>
  );
}

/** The packets, each with its safe rendered body and its scope paths at the code tip or plain text. */
function Packets({
  packets,
  tips,
  onOpenAddress,
}: {
  packets: PlannedPage['packets'];
  tips: PlannedPage['tips'];
  onOpenAddress: (address: PlannedTipAddress) => void;
}) {
  return (
    <section className={section} data-part="packets">
      <h3 className={heading}>Packets</h3>
      {packets.length === 0 ? (
        <p className={detail} data-testid="planned-packets-none">
          This leaf names no requirement packet.
        </p>
      ) : (
        <ul className={list} data-testid="planned-packets">
          {packets.map((packet) => (
            <li key={packet.path} className={row} data-testid="planned-packet">
              <span className={rowTitle}>{packet.title || packet.path}</span>
              <span className={quiet}>
                {packet.ref || packet.path}
                {packet.state === 'unread' ? ' · text not read' : ''}
              </span>
              {packet.state === 'rendered' && packet.body ? (
                <div className={body} data-testid="planned-packet-body">
                  {packet.body}
                </div>
              ) : null}
              {packet.scope.length ? (
                <ul className={list} data-testid="planned-scope">
                  {packet.scope.map((entry) => (
                    <ScopePathItem
                      key={entry.path}
                      entry={entry}
                      tips={tips}
                      onOpenAddress={onOpenAddress}
                    />
                  ))}
                </ul>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** The task document's Prepared-context section, or the word `absent`. */
function PreparedContext({ context }: { context: PlannedPage['preparedContext'] }) {
  return (
    <section className={section} data-part="prepared-context">
      <h3 className={heading}>{context.heading}</h3>
      <p className={body} data-testid="planned-prepared-context" data-state={context.state}>
        {context.state === 'present' ? context.body : 'absent'}
      </p>
    </section>
  );
}

/** The names in `notes/reference/<leaf>/`, including nested paths, shown as plain text. */
function ReferenceFiles({ files }: { files: PlannedPage['referenceFiles'] }) {
  return (
    <section className={section} data-part="reference-files">
      <h3 className={heading}>Reference files</h3>
      {files.length === 0 ? (
        <p className={detail} data-testid="planned-reference-files-none">
          The leaf&apos;s reference folder holds no file.
        </p>
      ) : (
        <ul className={list} data-testid="planned-reference-files">
          {files.map((file) => (
            <li
              key={file.path}
              className={quiet}
              data-testid="planned-reference-file"
              data-name={file.name}
            >
              {file.path}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
