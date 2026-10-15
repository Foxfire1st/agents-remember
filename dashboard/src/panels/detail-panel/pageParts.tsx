// MIK-R127 rule 10: the parts every block of the task page is built from — a block with
// its heading and count, one-line rows in aligned columns, the line of text that opens in
// place, and the status marks. The three skeletons compose them; none holds document logic.
import {
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type MouseEvent,
  type ReactNode,
  type RefObject,
} from 'react';

import { cx } from '../../../styled-system/css';
import { Markdown } from '../../grammar/Markdown';
import type { TaskStepNode } from '../../types/projection';
import { StepDot } from './StepDot';
import {
  arrow,
  bar,
  barDone,
  barWork,
  block,
  blockHead,
  blockLabel,
  chip,
  clip,
  clipLabel,
  clipMore,
  clipText,
  count as countStyle,
  link,
  more,
  pip,
  pips,
  row,
  rowId,
  rowLink,
  rowStatic,
  rows,
  statusWord,
  sub,
} from './pageStyles';

// A view of its own (rule 6): every bound block opens one, and "back" returns
// to the place the reader left.
export type DocView =
  | { kind: 'requirements' }
  | { kind: 'references' }
  | { kind: 'headerNotes' }
  | { kind: 'register'; heading: string }
  | { kind: 'section'; heading: string }
  | { kind: 'objective' }
  | { kind: 'design' }
  | { kind: 'codeExample'; id: string }
  | { kind: 'group'; status: 'done' | 'gone' }
  | { kind: 'rows' }
  | { kind: 'orderGraph' }
  | { kind: 'findings' };

export type StatusTone = 'work' | 'wait' | 'done' | 'gone';

const STATUS_WORDS: Record<string, [StatusTone, string]> = {
  inProgress: ['work', 'in work'],
  planning: ['wait', 'waiting'],
  Completed: ['done', 'completed'],
  abandoned: ['gone', 'abandoned'],
};

// The word and the colour of a declared status. A status the page does not
// know keeps its own word: every state stays readable as it was written.
export function statusTone(status: string): StatusTone {
  return STATUS_WORDS[status]?.[0] ?? 'wait';
}

export function statusLabel(status: string): string {
  return STATUS_WORDS[status]?.[1] ?? status;
}

export function StatusWord({ status, testid }: { status: string; testid?: string }) {
  return (
    <span
      className={statusWord({ tone: statusTone(status) })}
      data-status={status}
      data-testid={testid}
      title={status}
    >
      {statusLabel(status)}
    </span>
  );
}

export function plural(value: number, one: string, many?: string): string {
  return `${value.toLocaleString('en-US')} ${value === 1 ? one : (many ?? `${one}s`)}`;
}

export function Arrow() {
  return (
    <span className={arrow} aria-hidden="true">
      {' →'}
    </span>
  );
}

// A block of the page: the heading in capitals, what it counts, then the content.
export function Block({
  title,
  block: blockName,
  id,
  count,
  children,
}: {
  title: string;
  block?: string;
  id?: string;
  count?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className={block} data-block={blockName ?? title} id={id}>
      <div className={blockHead}>
        <h3 className={blockLabel}>{title}</h3>
        {count !== undefined && count !== null ? (
          <span className={countStyle} data-testid={`${blockName ?? title}-count`}>
            {count}
          </span>
        ) : null}
      </div>
      {children}
    </section>
  );
}

// The name of a group inside a block, with how many it holds.
export function SubHead({
  title,
  count,
  testid,
}: {
  title: string;
  count?: ReactNode;
  testid?: string;
}) {
  return (
    <h4 className={sub} data-testid={testid}>
      {title}
      {count !== undefined && count !== null ? <span className={countStyle}>{count}</span> : null}
    </h4>
  );
}

// A link that names how many its view holds ("all 12 decisions →").
export function MoreLink({
  onClick,
  testid,
  title,
  children,
}: {
  onClick: () => void;
  testid?: string;
  title?: string;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      className={cx(link, more)}
      onClick={onClick}
      data-testid={testid}
      title={title}
    >
      <span>{children}</span>
      <Arrow />
    </button>
  );
}

export type RowColumns = NonNullable<NonNullable<Parameters<typeof rows>[0]>['cols']>;

// A list of one-line rows whose columns line up over the whole list.
export function Rows({
  cols,
  testid,
  children,
}: {
  cols: RowColumns;
  testid?: string;
  children: ReactNode;
}) {
  return (
    <div className={rows({ cols })} data-testid={testid}>
      {children}
    </div>
  );
}

export function Row({
  testid,
  state,
  title,
  children,
}: {
  testid?: string;
  state?: string;
  title?: string;
  children: ReactNode;
}) {
  return (
    <div className={row({ kind: 'body' })} data-testid={testid} data-state={state} title={title}>
      {children}
    </div>
  );
}

export function RowHead({ children }: { children: ReactNode }) {
  return <div className={row({ kind: 'head' })}>{children}</div>;
}

// The id and the name of a row: one link over both columns where the row
// leads somewhere, the same two cells as plain text where it does not.
export function RowLink({
  id,
  name,
  onOpen,
  testid,
  title,
}: {
  id: string;
  name: string;
  onOpen?: () => void;
  testid?: string;
  title?: string;
}) {
  if (!onOpen) {
    return (
      <span className={rowStatic} data-testid={testid} title={title}>
        <span className={rowId({ tone: 'plain' })}>{id}</span>
        <span>{name}</span>
      </span>
    );
  }
  return (
    <button type="button" className={rowLink} onClick={onOpen} data-testid={testid} title={title}>
      <span className={rowId({ tone: 'link' })}>{id}</span>
      <span>{name}</span>
    </button>
  );
}

// Whether a one-line element is cut. Without a layout (a test document, a
// hidden panel) the answer is unknown and the caller falls back on the length.
function useCut(
  ref: RefObject<HTMLElement | null>,
  axis: 'x' | 'y',
  watch: unknown,
): boolean | null {
  const [cut, setCut] = useState<boolean | null>(null);
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return undefined;
    const measure = (): void => {
      if (element.clientWidth === 0) {
        setCut(null);
        return;
      }
      setCut(
        axis === 'x'
          ? element.scrollWidth > element.clientWidth + 1
          : element.scrollHeight > element.clientHeight + 1,
      );
    };
    measure();
    if (typeof ResizeObserver === 'undefined') return undefined;
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref, axis, watch]);
  return cut;
}

// Opens in place: "more" is offered where the closed line is cut, and stays as
// "less" while the line is open.
function useOpensInPlace<T extends HTMLElement>(
  axis: 'x' | 'y',
  text: string,
  longWithoutLayout: boolean,
): { ref: RefObject<T | null>; open: boolean; offered: boolean; toggle: () => void } {
  const ref = useRef<T | null>(null);
  const [open, setOpen] = useState(false);
  const cut = useCut(ref, axis, open ? null : text);
  const offered = open || (cut ?? longWithoutLayout);
  return { ref, open, offered, toggle: () => setOpen((current) => !current) };
}

// A line counts as long without a layout when it could not fit one row of the page.
const LONG_LINE = 140;

// One line of text that opens in place (rule 2 status note and header notes,
// rule 5 step notes): a single clipped line with a more/less control where the
// text is cut. `preview` marks a bounded summary: the open line says so, and
// the whole string lives one view away.
export function Clip({
  label,
  text,
  tone = 'muted',
  testid,
  preview,
}: {
  label?: string;
  text: string;
  tone?: 'muted' | 'ink';
  testid?: string;
  preview?: boolean;
}) {
  const { ref, open, offered, toggle } = useOpensInPlace<HTMLSpanElement>(
    'x',
    text,
    text.length > LONG_LINE,
  );
  return (
    <div className={clip} data-testid={testid}>
      {label ? (
        <span className={clipLabel} title={label}>
          {label}
        </span>
      ) : null}
      <span ref={ref} className={clipText({ open, tone })}>
        <Markdown inline>{text}</Markdown>
        {open && preview ? ' (preview)' : null}
      </span>
      {offered ? (
        <button
          type="button"
          className={cx(link, clipMore)}
          onClick={toggle}
          aria-expanded={open}
          data-testid={testid ? `${testid}-toggle` : undefined}
        >
          {open ? 'less' : 'more'}
        </button>
      ) : null}
    </div>
  );
}

// A click on a link inside a row, or one that ends a text selection, is not a
// wish to open the row.
export function wantsToOpen(event: MouseEvent<HTMLElement>): boolean {
  if ((event.target as HTMLElement).closest('a, button')) return false;
  return !String(window.getSelection?.() ?? '');
}

// A row or a line that opens on a click or on Enter/Space.
export function opensOn(toggle: () => void): {
  role: 'button';
  tabIndex: 0;
  onClick: (event: MouseEvent<HTMLElement>) => void;
  onKeyDown: (event: KeyboardEvent<HTMLElement>) => void;
} {
  return {
    role: 'button',
    tabIndex: 0,
    onClick: (event) => {
      if (wantsToOpen(event)) toggle();
    },
    onKeyDown: (event) => {
      if (event.target !== event.currentTarget) return;
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        toggle();
      }
    },
  };
}

export type PipState = 'done' | 'current' | 'blocked' | 'next' | 'pending';

export function Pip({ state, className }: { state: PipState; className?: string }) {
  return <span className={cx(pip({ state }), className)} aria-hidden="true" />;
}

// One mark per step. A long list of steps is a bar: marks stay readable only
// while each one can be seen.
const PIP_LIMIT = 12;

export function StepMarks({ states, steps }: { states: PipState[]; steps?: TaskStepNode[] }) {
  if (states.length === 0) return null;
  if (states.length > PIP_LIMIT) {
    const done = states.filter((state) => state === 'done').length;
    const work = states.filter((state) => state === 'current').length;
    return <ProgressBar done={done} work={work} total={states.length} />;
  }
  return (
    <span className={pips} aria-hidden="true">
      {states.map((state, index) => (
        <StepDot
          key={steps?.[index]?.id ?? index}
          state={state}
          step={steps?.[index]}
          index={index}
        />
      ))}
    </span>
  );
}

export function ProgressBar({ done, work, total }: { done: number; work: number; total: number }) {
  const share = (value: number): string => `${total > 0 ? (100 * value) / total : 0}%`;
  return (
    <span className={bar} aria-hidden="true">
      <span className={barDone} style={{ width: share(done) }} />
      <span className={barWork} style={{ width: share(work) }} />
    </span>
  );
}

export function Chip({
  tone,
  title,
  children,
}: {
  tone?: StatusTone;
  title?: string;
  children: ReactNode;
}) {
  return (
    <span className={chip({ tone })} title={title}>
      {children}
    </span>
  );
}
