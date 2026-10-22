// MIK-R127 rule 10: the parts every block of the task page is built from — a block with
// its heading and count, one-line rows in aligned columns, the line of text that opens in
// place, and the status marks. The three skeletons compose them; none holds document logic.
import {
  createContext,
  useContext,
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
import { StepDot } from './StepDot';
import type { ProviderLimit, TaskEntry, TaskStepNode } from '../../types/projection';
import { fmtWait } from '../../data/selectors';
import { useTaskStatusNow } from '../../data/taskStatusClock';
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

export type TaskStatusKey =
  | TaskEntry['stage']
  | NonNullable<TaskEntry['activity']>
  // MIK-R190 rule 13: a paused task held up by a seat that hit its provider limit.
  | 'limited';

const STATUS_WORDS: Record<TaskStatusKey, [StatusTone, string]> = {
  planned: ['wait', 'planned'],
  started: ['wait', 'started'],
  works: ['work', 'works'],
  paused: ['wait', 'paused'],
  // The server says started, nothing works and a seat is limited: a paused task whose word
  // counts down. The limit is served; this module never decides that one exists.
  limited: ['wait', 'rate limited'],
  // Host activity unknown: the row says the task's state only; the header says once that host
  // activity is unavailable.
  unknown: ['wait', 'started'],
  completed: ['done', 'completed'],
  aborted: ['gone', 'aborted'],
};

/** Presentation only: stage and activity are already decided by the server. */
export const taskStatusKey = (
  task: Pick<TaskEntry, 'stage' | 'activity' | 'providerLimit'>,
): TaskStatusKey =>
  task.activity === 'paused' && task.providerLimit ? 'limited' : (task.activity ?? task.stage);

/** The state underneath the word: a served limit is a paused task, so every count, every group
 * and the header keep saying paused (MIK-R190 rule 13). */
export const underlyingStatusKey = (
  task: Pick<TaskEntry, 'stage' | 'activity' | 'providerLimit'>,
): TaskStatusKey => {
  const status = taskStatusKey(task);
  return status === 'limited' ? 'paused' : status;
};

export function statusTone(status: TaskStatusKey): StatusTone {
  return STATUS_WORDS[status][0];
}

export function statusLabel(status: TaskStatusKey): string {
  return STATUS_WORDS[status][1];
}

// The exact served instant, written with its date, its seconds, its fractional seconds and the
// reader's time zone. Nothing here reads a provider limit for itself: the value is the server's
// `until` / `resetsAt`, shown as served. Seconds are kept because the served moments are exact: two
// resets a minute apart are two different instants, and a detail that showed the same time for both
// would be a false one. Milliseconds are kept for the same reason at the scale the producer sends:
// `.001000Z` and `.999000Z` are two instants inside one second (F28). A served value finer than a
// millisecond is not thrown away either — it is written verbatim beside the localized time, so the
// producer's own precision is never rounded off by this formatter.
const LIMIT_TIME = new Intl.DateTimeFormat(undefined, {
  day: 'numeric',
  fractionalSecondDigits: 3,
  hour: '2-digit',
  hour12: false,
  minute: '2-digit',
  month: 'short',
  second: '2-digit',
  timeZoneName: 'short',
  year: 'numeric',
});

// A fractional part longer than three digits: the part past the millisecond is the producer's own
// remainder. Trailing zeros there are not a difference worth showing; anything else is.
const SUB_MILLISECOND = /\.(\d{4,})(?=Z|[+-]\d{2}:?\d{2}$|$)/;

export function formatLimitTime(iso: string): string {
  const at = Date.parse(iso);
  if (Number.isNaN(at)) return iso;
  const shown = LIMIT_TIME.format(at);
  const finer = SUB_MILLISECOND.exec(iso);
  return finer !== null && /[1-9]/.test(finer[1].slice(3)) ? `${shown} (served ${iso})` : shown;
}

/** One line per limited seat: its role and agent, the provider, the window, the exact reset. */
function limitSeatLines(limit: ProviderLimit): string[] {
  return limit.seats.map((seat) =>
    [
      seat.role,
      seat.agentId,
      `${seat.source} · ${seat.account}`,
      seat.window,
      `resets ${formatLimitTime(seat.resetsAt)}`,
    ].join(' · '),
  );
}

/** Every served seat on its own line; a limit naming no seat still names its exact end. */
export function limitLines(limit: ProviderLimit): string[] {
  const lines = limitSeatLines(limit);
  return lines.length ? lines : [`until ${formatLimitTime(limit.until)}`];
}

export function limitHover(limit: ProviderLimit): string {
  return limitLines(limit).join('\n');
}

/** The words a surface with no display clock shows, and the exact time it says in its title. */
export function statusTitle(status: TaskStatusKey, until?: string): string {
  const word = statusLabel(status);
  return status === 'limited' && until ? `${word}, until ${formatLimitTime(until)}` : word;
}

/** The hover of a word with no display clock: the task's own end, then one line per served seat.
 *
 * A surface that cannot count down still owes the reader the whole served fact: which seat waits,
 * on which provider account and window, and the exact moment that window resets (MIK-R190 rule 13).
 */
export function statusHover(
  status: TaskStatusKey,
  until?: string,
  limit?: ProviderLimit | null,
): string {
  if (status !== 'limited') return statusLabel(status);
  const end = until ?? limit?.until;
  const head = end ? `until ${formatLimitTime(end)}` : statusLabel('limited');
  const seats = limit?.seats?.length ? limitLines(limit) : [];
  return [head, ...seats].join('\n');
}

export function StatusWord({
  status,
  testid,
  until,
  limit,
}: {
  status: TaskStatusKey;
  testid?: string;
  until?: string;
  limit?: ProviderLimit | null;
}) {
  return (
    <span
      className={statusWord({ tone: statusTone(status) })}
      data-status={status}
      data-testid={testid}
      title={statusHover(status, until, limit)}
    >
      {statusLabel(status)}
    </span>
  );
}

// Undefined means no provider; null means a hidden surface whose words must stay stopped.
const StatusClock = createContext<number | null | undefined>(undefined);

/** Visible task surfaces share one coarse display clock; nested readers reuse context. */
export function TaskStatusClock({
  active = true,
  children,
}: {
  active?: boolean;
  children: ReactNode;
}) {
  const shared = useContext(StatusClock);
  const now = useTaskStatusNow(active && shared === undefined);
  return (
    <StatusClock.Provider value={shared === undefined ? (active ? now : null) : shared}>
      {children}
    </StatusClock.Provider>
  );
}

/** The display instant a surface shares with its words and marks; undefined where none is set. */
export function useTaskStatusInstant(): number | null | undefined {
  return useContext(StatusClock);
}

/** The served end of a task's limit, for a surface that can only name it. */
export function limitUntil(task: TaskEntry | undefined): string | undefined {
  return task?.providerLimit?.until;
}

/** The age of the host event, unless a countdown has replaced it. */
function eventAge(
  task: TaskEntry,
  now: number | null | undefined,
  counting: boolean,
): number | undefined {
  if (counting || !task.eventAt || now === undefined || now === null) return undefined;
  return Math.max(0, (now - Date.parse(task.eventAt)) / 1000);
}

/** The time left in a served window at one instant; nothing when no window is counting. */
function windowLeft(
  status: TaskStatusKey,
  limit: ProviderLimit | null | undefined,
  now: number | null | undefined,
): number | undefined {
  if (status !== 'limited' || !limit || now == null) return undefined;
  return secondsLeft(limit.until, now);
}

/** What one word says at one instant, and the status its mark and tone wear. */
function taskWordAt(
  task: TaskEntry,
  left: number | undefined,
): { word: string; shown: TaskStatusKey; counting: boolean } {
  const status = taskStatusKey(task);
  if (status !== 'limited') return { word: statusLabel(status), shown: status, counting: false };
  // A surface with no display clock cannot count down; it says the words alone.
  if (left === undefined) {
    return { word: statusLabel('limited'), shown: status, counting: false };
  }
  if (left > 0) {
    return { word: `${statusLabel('limited')}: ${fmtWait(left)}`, shown: status, counting: true };
  }
  // Never a zero or negative time left: a window the clock has reached is the paused task again.
  return { word: statusLabel('paused'), shown: 'paused', counting: false };
}

/** The hover of a word: the served seats, or the host time, or the word itself. */
function wordTitle(
  task: TaskEntry,
  status: TaskStatusKey,
  left: number | undefined,
  word: string,
): string {
  if (status !== 'limited' || left === 0) return task.eventAt ?? word;
  return task.providerLimit ? limitHover(task.providerLimit) : statusLabel('limited');
}

/** What one task's word says at one instant, and the status its mark and tone wear. */
export interface TaskStatusReading {
  /** The status the word, the tone and the mark wear at this instant. */
  shown: TaskStatusKey;
  /** The words themselves, countdown or host age included. */
  word: string;
  /** The hover: the served seats, or the host time, or the word itself. */
  hover: string;
}

/** One display instant, one answer: the word, the mark's status and the hover of a task (rule 13).
 *
 * A surface that shares this instant can never show one word in its row and another in its hover:
 * the countdown, the expiry back to `paused` and the served detail are all decided here.
 */
export function taskStatusReading(
  task: TaskEntry,
  now: number | null | undefined,
): TaskStatusReading {
  const status = taskStatusKey(task);
  const limit = status === 'limited' ? task.providerLimit : undefined;
  const left = windowLeft(status, limit, now);
  const { word, shown, counting } = taskWordAt(task, left);
  const age = eventAge(task, now, counting);
  return {
    shown,
    word: age == null ? word : `${word} ${fmtWait(age)}`,
    hover: wordTitle(task, status, left, word),
  };
}

/** The same served word and event age on every task surface. */
export function TaskStatusWord({
  task,
  testid,
  mark = true,
  active = true,
}: {
  task: TaskEntry;
  testid?: string;
  mark?: boolean;
  active?: boolean;
}) {
  const shared = useContext(StatusClock);
  const status = taskStatusKey(task);
  // A limited word counts down, so it needs the display clock even when the host timed no event.
  const limit = status === 'limited' ? task.providerLimit : undefined;
  const local = useTaskStatusNow(active && shared === undefined && Boolean(task.eventAt || limit));
  const now = shared ?? local;
  const reading = taskStatusReading(task, now);
  return (
    <span
      className={statusWord({ tone: statusTone(reading.shown), mark })}
      data-status={reading.shown}
      data-testid={testid}
      title={reading.hover}
    >
      {reading.word}
    </span>
  );
}

/** Whole seconds left in a served window; zero once the display clock has reached it. */
function secondsLeft(until: string, now: number): number | undefined {
  const at = Date.parse(until);
  if (Number.isNaN(at)) return undefined;
  return Math.max(0, Math.floor((at - now) / 1000));
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
