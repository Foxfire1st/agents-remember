// MIK-R127 rule 2: the head, on every form. Form, id and status on one line, then the
// title; then one strip of short facts, the label above the value; then the status note
// and the header notes as labelled lines that open in place. The objective is no
// longer drawn here: under MIK-R150 rule 14 (D237) it is the first block after
// the head, drawn by the skeletons.
import { Fragment, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from 'react';

import { cx } from '../../../styled-system/css';
import { Markdown } from '../../grammar/Markdown';
import type { TaskDocNode, TaskHeaderNoteNode } from '../../types/projection';
import {
  boundedText,
  docForm,
  primaryRequirement,
  shortDocId,
  type ChainLink,
  type DocForm,
  type PrimaryRequirement,
} from './model';
import { Clip, MoreLink, StatusWord, type DocView } from './pageParts';
import {
  arrow,
  blockLabel,
  dim,
  docId,
  docTitle,
  fact,
  factLabel,
  facts,
  factValue,
  formBadge,
  headNotes,
  headTop,
  lead,
  leadText,
  leadTools,
  link,
} from './pageStyles';

export type HeadDoc = Pick<
  TaskDocNode,
  | 'id'
  | 'title'
  | 'status'
  | 'kind'
  | 'orchestrates'
  | 'form'
  | 'type'
  | 'createdAt'
  | 'objective'
  | 'statusNote'
  | 'statusNoteTruncated'
  | 'headerNotes'
  | 'headerNotesTotal'
  | 'requirements'
  | 'executionNature'
  | 'integrationBranch'
>;

// A count the strip states beside the facts: the masters of a sprint, the tasks of a master.
export interface HeadCount {
  label: string;
  value: number;
}

// Header-note labels the strip already shows: the chain holds the master and
// portfolio lines and the primary-requirement fact holds its line — but only
// where that fact exists, so nothing is lost on an older cache.
const CONSUMED_WITH_CHAIN = new Set(['Master', 'Parent portfolio']);
// A note this short reads as a fact of the strip; a longer one is a line below it.
const FACT_CHARS = 64;
const HEAD_NOTE_BOUND = 6;
// The objective's first lines without a layout to measure: four lines of the panel.
const LEAD_LINES = 4;
const LEAD_CHARS = 320;

export function DocHead({
  doc,
  chain,
  onJump,
  openView,
  counts = [],
}: {
  doc: HeadDoc;
  chain: ChainLink[];
  onJump: (id: string) => void;
  openView: (view: DocView) => void;
  counts?: HeadCount[];
}) {
  const form = docForm(doc);
  const primary = primaryRequirement(doc);
  const notes = shownHeadNotes(doc, chain, primary);
  return (
    <header data-testid="doc-head" data-block="head" data-form={form}>
      <div className={headTop}>
        <span className={formBadge({ form })} data-testid="doc-form">
          {form}
        </span>
        <span className={docId} data-testid="doc-id">
          {doc.id}
        </span>
        <StatusWord status={doc.status} testid="doc-status" />
      </div>
      <h2 className={docTitle} data-testid="doc-title">
        {doc.title}
      </h2>
      <div className={facts} data-testid="doc-facts">
        <ChainFact doc={doc} form={form} chain={chain} onJump={onJump} />
        <PrimaryFact primary={primary} />
        <DocFacts doc={doc} counts={counts} notes={notes.facts} />
      </div>
      <HeadNotes doc={doc} notes={notes} openView={openView} />
    </header>
  );
}

function isFactNote(note: TaskHeaderNoteNode): boolean {
  return note.label !== 'Dependencies' && note.value.length <= FACT_CHARS;
}

function visibleHeadNotes(
  headerNotes: TaskHeaderNoteNode[],
  chain: ChainLink[],
  primary: PrimaryRequirement | undefined,
): TaskHeaderNoteNode[] {
  return headerNotes.filter((note) => {
    if (CONSUMED_WITH_CHAIN.has(note.label) && chain.length > 0) return false;
    if (note.label === 'Primary requirement' && primary) return false;
    return true;
  });
}

interface ShownHeadNotes {
  /** Short notes, drawn as facts of the strip. */
  facts: TaskHeaderNoteNode[];
  /** Longer notes, drawn as lines that open in place. */
  lines: TaskHeaderNoteNode[];
  /** How many notes the document holds. */
  total: number;
  /** Whether the document holds a note the head does not show. */
  more: boolean;
}

// The header notes the head shows (rule 2): at most six, the short ones as
// facts and the others as lines. A note the chain or the primary-requirement
// fact already states counts as shown.
function shownHeadNotes(
  doc: HeadDoc,
  chain: ChainLink[],
  primary: PrimaryRequirement | undefined,
): ShownHeadNotes {
  const all = doc.headerNotes ?? [];
  const visible = visibleHeadNotes(all, chain, primary);
  const shown = visible.slice(0, HEAD_NOTE_BOUND);
  // An older cache names no total; the page reads the length it holds then.
  const total = doc.headerNotesTotal ?? all.length;
  const stated = all.length - visible.length;
  return {
    facts: shown.filter(isFactNote),
    lines: shown.filter((note) => !isFactNote(note)),
    total,
    more: total > stated + shown.length,
  };
}

// How tall the objective may stand whole, and from where it is held to its
// first lines: a text only a line or two over the bound is not worth a cut.
const LEAD_WHOLE_REM = 8.2;

// The natural height of the text in root-font units; null without a layout.
function useNaturalHeight(ref: RefObject<HTMLDivElement | null>, watch: string): number | null {
  const [height, setHeight] = useState<number | null>(null);
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return undefined;
    const measure = (): void => {
      if (element.clientWidth === 0) {
        setHeight(null);
        return;
      }
      const root = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
      setHeight(element.scrollHeight / root);
    };
    measure();
    if (typeof ResizeObserver === 'undefined') return undefined;
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref, watch]);
  return height;
}

// Whether the objective stands taller than its first lines. Without a layout
// to measure, the count of its lines and characters answers.
function isTallLead(text: string, height: number | null): boolean {
  if (height !== null) return height > LEAD_WHOLE_REM;
  return text.split('\n').length > LEAD_LINES || text.length > LEAD_CHARS;
}

// Under the objective: "more" where it is held to its first lines, and the
// link to the whole text where it is longer than the long-text bound (rule 6).
function LeadTools({
  tall,
  open,
  onToggle,
  extent,
  onOpenAll,
}: {
  tall: boolean;
  open: boolean;
  onToggle: () => void;
  extent: string | undefined;
  onOpenAll: () => void;
}) {
  if (!tall && !extent) return null;
  return (
    <div className={leadTools}>
      {tall ? (
        <button
          type="button"
          className={link}
          onClick={onToggle}
          aria-expanded={open}
          data-testid="objective-toggle"
        >
          {open ? 'less' : 'more'}
        </button>
      ) : null}
      {extent ? (
        <MoreLink onClick={onOpenAll} testid="objective-all-link">
          {extent} of objective
        </MoreLink>
      ) : null}
    </div>
  );
}

// The objective, first block after the head on every form (MIK-R150 rule 14,
// D237), under a heading like every block of the page (D223). Closed it shows
// its first lines, so the facts and the first rows stay on the first screen;
// "more" opens it in place. A text beyond the long-text bound (rule 6) opens to
// that bound, and the link to the view that holds it whole stands beside "more"
// from the start. Its bound, its link and what a reader opened stay as they are
// (MIK-R127 rules 2 and 6): only the place changed. The skeletons draw it, not
// the head.
export function ObjectiveLead({ text, openView }: { text: string; openView: (view: DocView) => void }) {
  const bounded = boundedText(text);
  const ref = useRef<HTMLDivElement | null>(null);
  const [open, setOpen] = useState(false);
  const height = useNaturalHeight(ref, bounded.preview);
  if (!text) return null;
  const tall = isTallLead(text, height);
  const whole = open || !tall;
  return (
    <div className={lead} data-block="objective" data-testid="objective-lead">
      <h3 className={blockLabel}>Objective</h3>
      <div
        ref={ref}
        className={leadText({ clamped: !whole })}
        data-open={whole ? 'true' : 'false'}
        data-testid="objective-text"
      >
        <Markdown>{bounded.preview}</Markdown>
      </div>
      <LeadTools
        tall={tall}
        open={open}
        onToggle={() => setOpen((current) => !current)}
        extent={bounded.long ? bounded.extent : undefined}
        onOpenAll={() => openView({ kind: 'objective' })}
      />
    </div>
  );
}

function Fact({
  label,
  testid,
  title,
  children,
}: {
  label: string;
  testid?: string;
  title?: string;
  children: ReactNode;
}) {
  return (
    <div className={fact} data-testid={testid} title={title}>
      <span className={factLabel}>{label}</span>
      <span className={factValue}>{children}</span>
    </div>
  );
}

// The chain of parents as links, ending in the document itself: where it stands.
function ChainFact({
  doc,
  form,
  chain,
  onJump,
}: {
  doc: HeadDoc;
  form: DocForm;
  chain: ChainLink[];
  onJump: (id: string) => void;
}) {
  const parentId = chain.length > 0 ? chain[chain.length - 1].text : undefined;
  return (
    <Fact label="chain" testid="doc-chain">
      {chain.map((item, index) => (
        <Fragment key={item.targetKey}>
          <span className={dim}>{item.form}</span>
          <button
            type="button"
            className={link}
            onClick={() => onJump(item.targetKey)}
            data-testid={`doc-chain-${index}`}
            title={item.title}
          >
            {item.text}
          </button>
          <span className={arrow} aria-hidden="true">
            →
          </span>
        </Fragment>
      ))}
      <span className={dim}>{form}</span>
      <span data-testid="doc-chain-self">{shortDocId(doc.id, parentId)}</span>
    </Fact>
  );
}

// The primary requirement as its link and not its text. The packet opens in the
// reader where the task folder holds it; an id without a packet stays text.
function PrimaryFact({ primary }: { primary: PrimaryRequirement | undefined }) {
  if (!primary) return null;
  return (
    <Fact label="primary requirement" testid="doc-primary-requirement" title={primary.text}>
      {primary.address ? (
        <Markdown inline>{`[${primary.label}](${primary.address})`}</Markdown>
      ) : (
        <span>{primary.label}</span>
      )}
    </Fact>
  );
}

function DocFacts({
  doc,
  counts,
  notes,
}: {
  doc: HeadDoc;
  counts: HeadCount[];
  notes: TaskHeaderNoteNode[];
}) {
  const shortType = doc.type && doc.type.length <= FACT_CHARS;
  return (
    <>
      {doc.executionNature ? <Fact label="execution">{doc.executionNature}</Fact> : null}
      {doc.integrationBranch ? <Fact label="branch">{doc.integrationBranch}</Fact> : null}
      {counts.map((entry) => (
        <Fact key={entry.label} label={entry.label} testid={`doc-count-${entry.label}`}>
          {entry.value.toLocaleString('en-US')}
        </Fact>
      ))}
      {notes.map((note) => (
        <Fact key={note.label} label={note.label} testid={`header-note-${note.label}`}>
          <Markdown inline>{note.value}</Markdown>
        </Fact>
      ))}
      {shortType ? <Fact label="type">{doc.type}</Fact> : null}
      {doc.createdAt ? <Fact label="created">{doc.createdAt.slice(0, 10)}</Fact> : null}
    </>
  );
}

// The status note in one line, then the longer header notes as lines of label
// and value, with a link to all where the document holds more than the head shows.
function HeadNotes({
  doc,
  notes,
  openView,
}: {
  doc: HeadDoc;
  notes: ShownHeadNotes;
  openView: (view: DocView) => void;
}) {
  const shown = notes.lines;
  const longType = doc.type && doc.type.length > FACT_CHARS ? doc.type : undefined;
  const more = notes.more;
  if (!doc.statusNote && shown.length === 0 && !longType && !more) return null;
  return (
    <div className={headNotes} data-testid="header-notes">
      {doc.statusNote ? (
        <Clip
          label="status note"
          text={doc.statusNote}
          tone="ink"
          testid="status-note"
          preview={doc.statusNoteTruncated}
        />
      ) : null}
      {shown.map((note) => (
        <Clip
          key={note.label}
          label={note.label}
          text={note.value}
          testid={`header-note-${note.label}`}
        />
      ))}
      {longType ? <Clip label="type" text={longType} testid="header-note-type" /> : null}
      {more ? (
        <MoreLink onClick={() => openView({ kind: 'headerNotes' })} testid="header-notes-all-link">
          all {notes.total} header notes
        </MoreLink>
      ) : null}
    </div>
  );
}

export function HeaderNotesViewBody({ doc }: { doc: Pick<HeadDoc, 'headerNotes'> }) {
  return (
    <div className={cx(headNotes)}>
      {(doc.headerNotes ?? []).map((note) => (
        <Clip key={note.label} label={note.label} text={note.value} tone="ink" />
      ))}
    </div>
  );
}
