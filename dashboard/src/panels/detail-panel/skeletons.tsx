// MIK-R127 fixed skeletons: one skeleton per form (rule 1). The blocks the forms share —
// bounds with views (rule 6), decisions, registers, further notes (rule 7) — live here; the
// head is docHead.tsx, the task-only blocks taskBlocks.tsx, the Now/Tasks rows
// task-groups.tsx, and the three readers in taskReader.tsx compose them in their fixed
// order (rules 3-5). A view of its own replaces the page until "back" returns to the place
// the reader left (rule 6).
import { Fragment, useCallback, useEffect, useRef, useState, type ReactNode } from 'react';

import { cx } from '../../../styled-system/css';
import { Markdown } from '../../grammar/Markdown';
import type { TaskArtifactReaderTarget as NotesReaderTarget } from '../../data/taskArtifacts';
import type {
  DiscardedSubTaskNode,
  TaskCodeExampleNode,
  TaskDecisionNode,
  TaskSummary,
  TaskEntry,
  TaskSectionNode,
} from '../../types/projection';
import {
  boundedText,
  dirName,
  labelWithId,
  orderDecisionsNewestFirst,
  sectionIntro,
  shortWhen,
  type ChainLink,
  type DocForm,
  type FurtherSection,
  type RegisterTable,
} from './model';
import { CloseoutQueue } from '../CloseoutQueue';
import { TaskNotes } from '../TaskNotes';
import type { ChangeSetTarget } from '../changeset/ChangeSetViewer';
import { DocChangeSetBar } from './changeSetBar';
import {
  Arrow,
  Block,
  Clip,
  MoreLink,
  Row,
  Rows,
  SubHead,
  opensOn,
  plural,
  wantsToOpen,
  type DocView,
} from './pageParts';
import {
  cellWrap,
  count,
  decisionAt,
  decisionNumber,
  decisionText,
  decisionWhy,
  dim,
  discardedWhy,
  headTools,
  intro as introStyle,
  link,
  none,
  page,
  prose,
  row,
  rowId,
  table,
  tableDetail,
  tableFrame,
  tableRow,
  viewBack,
  viewHead,
  viewTitle,
} from './pageStyles';
import { taskdocCode, taskdocCodeHead, taskdocCodeMeta, taskdocSnippet } from './styles';
import type { TaskDocumentBodyState } from '../../data/useTaskDocumentBody';

export type { DocForm, DocView };
export { Block, Clip };
export {
  DocHead,
  HeaderNotesViewBody,
  ObjectiveLead,
  type HeadCount,
  type HeadDoc,
} from './docHead';
export { FindingsViewBody, ReviewBlock, SkippedDisposition, StepsBlock } from './taskBlocks';

type ScrollOwner = { kind: 'window' } | { kind: 'element'; el: Element };

// The production page scrolls the inner detail-panel section, not the
// window; the dev page has no scrollable panel and keeps window behavior
// (L127-C-F03). The owner is captured when the view opens and reused on the
// way back, because opening the view itself changes what is scrollable.
function resolveScrollOwner(): ScrollOwner {
  const panel = document.querySelector('[data-testid="detail-panel"]');
  if (panel && panel.scrollHeight > panel.clientHeight + 1) {
    return { kind: 'element', el: panel };
  }
  return { kind: 'window' };
}

function readTop(owner: ScrollOwner): number {
  return owner.kind === 'window' ? window.scrollY : owner.el.scrollTop;
}

function writeTop(owner: ScrollOwner, top: number): void {
  // jsdom implements scrollTo as a throwing stub (window) or not at all
  // (elements); the page position is a browser concern, never a test
  // assertion. Unit tests observe the saved value and the call instead.
  try {
    if (owner.kind === 'window') {
      window.scrollTo(0, top);
    } else if (typeof (owner.el as Element & { scrollTo?: unknown }).scrollTo === 'function') {
      (owner.el as Element & { scrollTo: (x: number, y: number) => void }).scrollTo(0, top);
    } else {
      owner.el.scrollTop = top;
    }
  } catch {
    /* settles only in a real browser */
  }
}

export function useDocView(): {
  view: DocView | null;
  openView: (view: DocView) => void;
  back: () => void;
} {
  const [view, setView] = useState<DocView | null>(null);
  const savedScroll = useRef(0);
  const savedOwner = useRef<ScrollOwner>({ kind: 'window' });
  const openView = (next: DocView): void => {
    const owner = resolveScrollOwner();
    savedOwner.current = owner;
    savedScroll.current = readTop(owner);
    setView(next);
    writeTop(owner, 0);
  };
  // The way back reads the saved place from the refs alone, so it is one function
  // for as long as the page lives.
  const back = useCallback((): void => {
    setView(null);
    const top = savedScroll.current;
    const owner = savedOwner.current;
    requestAnimationFrame(() => writeTop(owner, top));
  }, []);
  // Escape leads back while a view is open: the page listens from the opening to the closing.
  const open = view !== null;
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') back();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, back]);
  return { view, openView, back };
}

export function DocViewShell({
  title,
  count: viewCount,
  onBack,
  children,
}: {
  title: string;
  count?: string;
  onBack: () => void;
  children: ReactNode;
}) {
  return (
    <div className={page} data-testid="doc-view">
      <div className={viewHead}>
        <button
          type="button"
          className={cx(link, viewBack)}
          onClick={onBack}
          data-testid="doc-view-back"
        >
          ← back
        </button>
        <h3 className={viewTitle} data-testid="doc-view-title">
          {title}
        </h3>
        {viewCount ? <span className={count}>{viewCount}</span> : null}
      </div>
      {children}
    </div>
  );
}

// The discarded-before-start audit: number, name, and why and when it was discarded.
export function DiscardedSubTaskHistory({ items }: { items: DiscardedSubTaskNode[] }) {
  return (
    <Rows cols="discarded" testid="discarded-history">
      {items.map((item) => (
        <Row key={`${item.number}:${item.proof.fingerprint}`}>
          <span className={rowId({ tone: 'plain' })}>{item.number}</span>
          <span>{item.name}</span>
          <span className={discardedWhy}>
            <Clip text={`${item.reason} · ${item.discardedAt} · proof ${item.proof.fingerprint}`} />
          </span>
        </Row>
      ))}
    </Rows>
  );
}

export function CodeExample({ example }: { example: TaskCodeExampleNode }) {
  return (
    <div className={taskdocCode}>
      <div className={taskdocCodeHead}>{labelWithId(example.id, example.title)}</div>
      <div className={taskdocCodeMeta}>covers: {example.distinctChange}</div>
      <div className={taskdocCodeMeta}>why: {example.why}</div>
      {example.snippet ? <pre className={taskdocSnippet}>{example.snippet}</pre> : null}
    </div>
  );
}

// One decision: its number, its recorded time and one line of its text. The
// row opens the whole text and the rationale in place.
function DecisionRow({
  item,
  number,
  whole = false,
}: {
  item: TaskDecisionNode;
  number: number | undefined;
  whole?: boolean;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div
      className={row({ kind: 'body', opens: !whole, open: whole || open })}
      {...(whole ? {} : opensOn(() => setOpen((current) => !current)))}
      aria-expanded={whole ? undefined : open}
      title={`recorded ${item.at}`}
      data-testid="decision-row"
    >
      <span className={decisionNumber} data-testid="decision-number">
        {number === undefined ? '' : `#${number}`}
      </span>
      <span className={decisionAt} data-testid="decision-time">
        {shortWhen(item.at)}
      </span>
      <span className={decisionText} data-testid="decision-text">
        <Markdown inline>{item.decision}</Markdown>
      </span>
      {whole || open ? (
        <div className={decisionWhy} data-testid="decision-rationale">
          <Markdown inline>{item.rationale}</Markdown>
        </div>
      ) : null}
    </div>
  );
}

export function DecisionList({
  items,
  numbers,
  whole = false,
}: {
  items: TaskDecisionNode[];
  numbers?: Map<TaskDecisionNode, number>;
  whole?: boolean;
}) {
  return (
    <Rows cols="decisions" testid="decision-list">
      {items.map((item) => (
        <DecisionRow
          key={`${item.at}:${item.decision}`}
          item={item}
          number={numbers?.get(item)}
          whole={whole}
        />
      ))}
    </Rows>
  );
}

// Authored decisions numbered from the oldest; product records take no number.
export function decisionNumbers(items: TaskDecisionNode[]): Map<TaskDecisionNode, number> {
  const ordered = orderDecisionsNewestFirst(items.filter((item) => !item.product_record));
  return new Map(ordered.map((item, index) => [item, ordered.length - index] as const));
}

// The decision log front page (MIK-R126 rule 6 under the lifecycle-records ruling): the
// five newest authored decisions, newest first. Product-written records — marked by the
// server from the writers' own constants — count toward nothing and are not drawn here.
// The link opens the whole log on a page of its own; it appears whenever at least one
// entry is omitted from this page and names only the authored-decision count
// (MIK-R128 rule 13). Records remain reachable, even in a records-only log.
export function DecisionPreview({
  items,
  repo,
  master,
  title,
  onOpenNotes,
}: {
  items: TaskDecisionNode[];
  repo: string;
  master: string;
  title: string;
  onOpenNotes?: (target: NotesReaderTarget) => void;
}) {
  const authored = items.filter((item) => !item.product_record);
  const newest = orderDecisionsNewestFirst(authored).slice(0, 5);
  const omitted = items.length - newest.length;
  const linkLabel = `all ${plural(authored.length, 'decision')}`;
  return (
    <>
      <DecisionList items={newest} numbers={decisionNumbers(items)} />
      {omitted > 0 ? (
        <MoreLink
          onClick={() =>
            onOpenNotes?.({ kind: 'decisions', repo, master, title, decisions: items })
          }
          testid="decision-log-all-link"
          title="open the whole decision log"
        >
          {linkLabel}
        </MoreLink>
      ) : null}
    </>
  );
}

// ---------------------------------------------------------------------------
// Bounded blocks (rule 6): eight requirement lines, five register rows, each
// with a link naming all; the link opens the view and "back" returns.
// ---------------------------------------------------------------------------

export const REQUIREMENT_BOUND = 8;
export const REGISTER_ROW_BOUND = 5;
export const REFERENCE_BOUND = 8;

export function RequirementsBlock({
  requirements,
  openView,
}: {
  requirements: string[];
  openView: (view: DocView) => void;
}) {
  if (requirements.length === 0) return null;
  return (
    <Block title="Requirements" block="requirements" count={requirements.length}>
      <Rows cols="one">
        {requirements.slice(0, REQUIREMENT_BOUND).map((line, index) => (
          <Row key={`${index}:${line}`}>
            <Clip text={line} tone="ink" />
          </Row>
        ))}
      </Rows>
      {requirements.length > REQUIREMENT_BOUND ? (
        <MoreLink onClick={() => openView({ kind: 'requirements' })} testid="requirements-all-link">
          all {requirements.length} requirement lines
        </MoreLink>
      ) : null}
    </Block>
  );
}

// Whole lines, wrapped: the view of all of a list holds every byte of each line.
function WholeLines({ lines }: { lines: string[] }) {
  return (
    <Rows cols="one">
      {lines.map((line, index) => (
        <Row key={`${index}:${line}`}>
          <span className={cellWrap}>
            <Markdown inline>{line}</Markdown>
          </span>
        </Row>
      ))}
    </Rows>
  );
}

export function RequirementsViewBody({ requirements }: { requirements: string[] }) {
  return <WholeLines lines={requirements} />;
}

// A long prose block (rule 6): the first lines on the page, the whole text
// one counted link away. Short bodies render whole with no link. Long means
// more than eight lines or more than a viewport of characters: a single
// 21,021-character paragraph is long even with one newline chunk.
export function LongTextBlock({
  title,
  block,
  text,
  viewKind,
  openView,
}: {
  title: string;
  block: string;
  text: string;
  viewKind: 'objective' | 'design';
  openView: (view: DocView) => void;
}) {
  if (!text) return null;
  const bounded = boundedText(text);
  return (
    <Block title={title} block={block}>
      <div className={prose}>
        <Markdown>{bounded.preview}</Markdown>
      </div>
      {bounded.long ? (
        <MoreLink onClick={() => openView({ kind: viewKind })} testid={`${block}-all-link`}>
          {bounded.extent} of {title.toLowerCase()}
        </MoreLink>
      ) : null}
    </Block>
  );
}

// Optional structured Markdown stays at its fixed place. Its long text opens
// in place; it needs no new reader route or document-view kind.
export function FixedMarkdownBlock({
  title,
  block,
  text,
}: {
  title: string;
  block: string;
  text?: string | null;
}) {
  const [open, setOpen] = useState(false);
  if (!text) return null;
  const bounded = boundedText(text);
  return (
    <Block title={title} block={block}>
      <div className={prose}>
        <Markdown>{open ? text : bounded.preview}</Markdown>
      </div>
      {bounded.long ? (
        <button
          type="button"
          className={link}
          aria-expanded={open}
          onClick={() => setOpen((current) => !current)}
          data-testid={`${block}-toggle`}
        >
          {open ? 'less' : `all ${bounded.extent} of ${title.toLowerCase()}`}
        </button>
      ) : null}
    </Block>
  );
}

// Decisions with their bound (MIK-R126 rule 6, kept), then the open questions
// where the document records any. The introducing text of a `sharedDecisions`
// section stands with the block (rule 7), never as a second list.
export function DecisionsBlock({
  entry,
  doc,
  onOpenNotes,
}: {
  entry: TaskEntry;
  doc: Pick<TaskSummary, 'decisions' | 'openQuestions' | 'sections'>;
  onOpenNotes?: (target: NotesReaderTarget) => void;
}) {
  const intro = sectionIntro(doc.sections, 'sharedDecisions');
  // A block without content is absent — unless it carries introducing prose:
  // an intro-only shared-decisions section keeps its text, never a bare
  // heading, and never vanishes.
  if (!entry.reference || (doc.decisions.length === 0 && doc.openQuestions.length === 0 && !intro)) return null;
  const authored = doc.decisions.filter((item) => !item.product_record).length;
  return (
    <Block
      title="Decisions"
      block="decisions"
      count={authored > 0 ? `${authored.toLocaleString('en-US')}, newest first` : undefined}
    >
      {intro ? (
        <div className={introStyle} data-testid="decisions-intro">
          <Markdown>{intro}</Markdown>
        </div>
      ) : null}
      {doc.decisions.length > 0 ? (
        <DecisionPreview
          items={doc.decisions}
          repo={entry.reference.repository}
          master={dirName(entry.reference.path)}
          title={entry.title}
          onOpenNotes={onOpenNotes}
        />
      ) : (
        <p className={none}>No decision recorded.</p>
      )}
      {doc.openQuestions.length > 0 ? (
        <>
          <SubHead title="Open questions" count={doc.openQuestions.length} />
          <Rows cols="one" testid="open-questions">
            {doc.openQuestions.map((question, index) => (
              <Row key={`${index}:${question}`}>
                <Clip text={question} tone="ink" />
              </Row>
            ))}
          </Rows>
        </>
      ) : null}
    </Block>
  );
}

function sectionSize(section: FurtherSection): string {
  const tables =
    section.tables > 0
      ? ` · ${plural(section.tables, 'table')}, ${plural(section.rows, 'row')}`
      : '';
  return `${section.chars.toLocaleString('en-US')} characters${tables}`;
}

// Every free-form section that is not one of the blocks above is one line at
// the end (rule 7): its heading and its size; the line opens the section in a
// view of its own. On a master or sprint the references stand as one such
// line with their own view.
export function FurtherNotesBlock({
  further,
  references,
  openView,
}: {
  further: FurtherSection[];
  references: string[];
  openView: (view: DocView) => void;
}) {
  if (further.length === 0 && references.length === 0) return null;
  return (
    <Block
      title="Further notes"
      block="further"
      count={further.length > 0 ? plural(further.length, 'free-form section') : undefined}
    >
      <Rows cols="sized">
        {further.map((section, index) => (
          <Row key={section.heading}>
            <button
              type="button"
              className={link}
              onClick={() => openView({ kind: 'section', heading: section.heading })}
              data-testid={`further-open-${index}`}
            >
              <span>{section.heading}</span>
              <Arrow />
            </button>
            <span className={dim} data-testid={`further-size-${index}`}>
              {sectionSize(section)}
            </span>
          </Row>
        ))}
        {references.length > 0 ? (
          <Row>
            <button
              type="button"
              className={link}
              onClick={() => openView({ kind: 'references' })}
              data-testid="references-all-link"
            >
              <span>References</span>
              <Arrow />
            </button>
            <span className={dim}>{plural(references.length, 'entry', 'entries')}</span>
          </Row>
        ) : null}
      </Rows>
    </Block>
  );
}

// A register table (rule 3/6): the five newest rows (last written first); the
// first four columns show and the rest open per row, so a wide register never
// overflows the page and every cell stays one click away.
const REGISTER_SHOWN_COLUMNS = 4;

const columnName = (heading: string): string => heading.replace(/\s*\(.*$/, '');

export function RegisterTableView({ table: register }: { table: RegisterTable }) {
  const [openRow, setOpenRow] = useState<number | null>(null);
  const shown = register.cols.slice(0, REGISTER_SHOWN_COLUMNS);
  const hidden = register.cols.map((col, index) => ({ col, index })).slice(REGISTER_SHOWN_COLUMNS);
  return (
    <div className={tableFrame}>
      <table className={table} data-testid="register-table">
        <thead>
          <tr>
            {shown.map((col) => (
              <th key={col} title={col}>
                {columnName(col)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {register.rows.map((cells, rowIndex) => {
            const open = openRow === rowIndex;
            const rest = hidden.filter(({ index }) => cells[index]);
            return (
              <Fragment key={rowIndex}>
                <tr
                  className={tableRow({ open })}
                  // A link in a cell opens its target, not the row.
                  onClick={(event) => {
                    if (wantsToOpen(event)) setOpenRow(open ? null : rowIndex);
                  }}
                  aria-expanded={open}
                  data-testid={`register-row-${rowIndex}`}
                >
                  {shown.map((col, colIndex) => (
                    <td key={col}>
                      {cells[colIndex] ? <Markdown inline>{cells[colIndex]}</Markdown> : '—'}
                    </td>
                  ))}
                </tr>
                {open && rest.length > 0 ? (
                  <tr className={tableDetail} data-testid={`register-row-${rowIndex}-detail`}>
                    <td colSpan={shown.length}>
                      {rest.map(({ col, index }) => (
                        <div key={col}>
                          <span className={dim}>{columnName(col)}: </span>
                          <Markdown inline>{cells[index]}</Markdown>
                        </div>
                      ))}
                    </td>
                  </tr>
                ) : null}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function newestRegisterRows(register: RegisterTable): string[][] {
  return register.rows.slice().reverse().slice(0, REGISTER_ROW_BOUND);
}

export function RegistersBlock({
  registers,
  openView,
}: {
  registers: RegisterTable[];
  openView: (view: DocView) => void;
}) {
  if (registers.length === 0) return null;
  return (
    <Block title="Registers" block="registers">
      {registers.map((register, index) => (
        <Fragment key={register.heading}>
          <SubHead
            title={columnName(register.heading)}
            count={`${plural(register.rows.length, 'row')}, last written first`}
            testid={`register-head-${index}`}
          />
          <RegisterTableView table={{ ...register, rows: newestRegisterRows(register) }} />
          {/*
            Every section byte one link away for short as well as long
            tables (rule 7): the preview shows at most five rows, so even a
            one-row table keeps its surrounding prose behind this link.
          */}
          <MoreLink
            onClick={() => openView({ kind: 'register', heading: register.heading })}
            testid={`register-all-link-${index}`}
          >
            all {plural(register.rows.length, 'row')} of{' '}
            {columnName(register.heading).toLowerCase()}
          </MoreLink>
        </Fragment>
      ))}
    </Block>
  );
}

export function RegisterViewBody({ section }: { section: TaskSectionNode }) {
  // A register's own view keeps every section byte (rule 7): the surrounding
  // prose with the table, not the parsed preview rows.
  return <Markdown>{section.body}</Markdown>;
}

// Proposed code as titles, each opening a view of its own (rule 5).
export function CodeTitlesBlock({
  examples,
  openView,
}: {
  examples: TaskCodeExampleNode[];
  openView: (view: DocView) => void;
}) {
  if (examples.length === 0) return null;
  return (
    <Block title="Proposed code" block="code" count={examples.length}>
      <Rows cols="pair">
        {examples.map((example) => (
          <Row key={example.id}>
            <button
              type="button"
              className={link}
              onClick={() => openView({ kind: 'codeExample', id: example.id })}
              data-testid={`code-open-${example.id}`}
            >
              <span>{labelWithId(example.id, example.title)}</span>
              <Arrow />
            </button>
            <span className={dim}>{example.language}</span>
          </Row>
        ))}
      </Rows>
    </Block>
  );
}

// ---------------------------------------------------------------------------
// Reader chrome shared by the three skeletons: the change-set bar under the
// head, and the task boundary that keeps requirement links inside the doc.
// ---------------------------------------------------------------------------

export function ReaderHead({
  entry,
  bodyState,
  kind,
  onOpenChangeSet,
}: {
  entry: TaskEntry;
  bodyState: TaskDocumentBodyState | undefined;
  kind: 'master' | 'leaf';
  onOpenChangeSet?: (target: ChangeSetTarget) => void;
}) {
  // A master's series counter uses the served entry identity, not the document body.
  // Keep leaf and sprint controls behind their existing body boundary.
  if (bodyState === 'loading' && (kind !== 'master' || entry.summary?.form !== 'master')) return null;
  return (
    <div className={headTools}>
      <DocChangeSetBar kind={kind} entry={entry} onOpen={onOpenChangeSet} />
    </div>
  );
}

export { TaskNotes };
export { CloseoutQueue };
export type { FurtherSection, RegisterTable, ChainLink };
