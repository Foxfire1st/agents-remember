import { fmtWait } from '../../data/selectors';
import { pathDir, pathStem } from '../../data/taskHierarchy';
import type { LifecycleProjection, TaskDocNode } from '../../types/projection';
import { statusLabel, statusTone, type StatusTone } from '../detail-panel/pageParts';
import type { RowProgress } from './progress';

// What a row of the task tree says about its task: one mark and one word (MIK-R144 rule 3).
//
// Two sources speak. A lifecycle that reported an event says its own state, with the dashboard's
// state mark (grammar/Dot). Where no lifecycle reported, the document's declared status speaks,
// with the task page's status dot and words, so the column and the task page never say two
// things about one task. The server assumes a paused lifecycle for every task with a worktree;
// one that never reported an event (`inferred`, no `lastEventTs`) is that assumption and nothing
// more, so it leaves the word to the document. A row without a document (a lifecycle that owns a
// worktree) then says what a document says that has not begun: "waiting". Its hover says that no
// event was seen.

// 0 asks for the developer, 1 is in work, 2 waits, 3 is over. The order under a parent.
export type RowBand = 0 | 1 | 2 | 3;

export type RowMark =
  | { kind: 'status'; tone: StatusTone }
  | { kind: 'state'; variant: string };

// `ask` and `fault` call for the developer; `run` says that something moves; `rest` is a state
// that asks for nothing.
export type WordTone = 'ask' | 'fault' | 'run' | 'rest';

export interface RowState {
  mark: RowMark;
  word: string;
  tone: WordTone;
  band: RowBand;
  // The word of a reporting lifecycle that is neither asking nor over carries its age.
  aged: boolean;
}

export interface RowWord {
  text: string;
  tone: WordTone;
  band: RowBand;
}

export interface RowFacts {
  status: string;
  lifecycle?: LifecycleProjection;
  ageSeconds?: number;
}

const STATUS_BANDS: Record<StatusTone, RowBand> = { work: 1, wait: 2, done: 3, gone: 3 };
// The status a row without a document speaks with while its lifecycle has said nothing.
const NOT_BEGUN = 'planning';

export function lifecycleReported(lifecycle: LifecycleProjection | undefined): boolean {
  return Boolean(lifecycle && !(lifecycle.inferred && !lifecycle.lastEventTs));
}

function documentState(status: string): RowState {
  const tone = statusTone(status);
  return {
    mark: { kind: 'status', tone },
    word: statusLabel(status),
    tone: 'rest',
    band: STATUS_BANDS[tone],
    aged: false,
  };
}

function lifecycleState(lifecycle: LifecycleProjection): RowState {
  const mark: RowMark = { kind: 'state', variant: lifecycle.state };
  switch (lifecycle.state) {
    case 'running':
      return { mark, word: 'running', tone: 'run', band: 1, aged: true };
    case 'paused':
      // The server sets an assumed pause once a lifecycle that reported goes quiet.
      return { mark, word: lifecycle.inferred ? 'quiet' : 'paused', tone: 'rest', band: 1, aged: true };
    case 'awaiting-developer':
      return { mark, word: 'your turn', tone: 'ask', band: 0, aged: false };
    case 'blocked':
      return { mark, word: 'blocked', tone: 'fault', band: 0, aged: false };
    case 'completed':
      return documentState('Completed');
    case 'abandoned':
      return documentState('abandoned');
    default:
      // An unknown state keeps its own word and the mark Dot gives what it cannot classify.
      return { mark, word: lifecycle.state, tone: 'rest', band: 1, aged: false };
  }
}

export function rowState(facts: Pick<RowFacts, 'status' | 'lifecycle'>): RowState {
  const { lifecycle, status } = facts;
  if (lifecycle && lifecycleReported(lifecycle)) return lifecycleState(lifecycle);
  return documentState(status || NOT_BEGUN);
}

// The word of the task's own state, with its age where a reporting lifecycle is neither asking
// nor over.
function stateText(state: RowState, facts: RowFacts): string {
  const aged = state.aged && lifecycleReported(facts.lifecycle) && facts.ageSeconds != null;
  return aged ? `${state.word} ${fmtWait(facts.ageSeconds)}` : state.word;
}

// The one word of a row: the task's own state. Every other fact of the row is in its hover text.
export function rowWord(facts: RowFacts): RowWord {
  const state = rowState(facts);
  return { text: stateText(state, facts), tone: state.tone, band: state.band };
}

export interface RowProgressFacts extends RowProgress {
  discarded?: number;
}

// The figure at the end of a row: steps done of a task, tasks completed of a master.
export function progressFigure(progress: RowProgressFacts | undefined): string {
  return progress && progress.total > 0 ? `${progress.done}/${progress.total}` : '';
}

// The long form of the figure: what the row has no room for. A master whose tasks were all
// abandoned has no figure on its row and still says so here.
function progressLine(noun: string, progress: RowProgressFacts): string | undefined {
  const extra = [
    progress.abandoned ? `${progress.abandoned} abandoned` : '',
    progress.discarded ? `${progress.discarded} discarded` : '',
  ].filter(Boolean);
  if (progress.total <= 0 && extra.length === 0) return undefined;
  return [`${noun}: ${progress.done} of ${progress.total}`, ...extra].join(', ');
}

function lifecycleLine(lifecycle: LifecycleProjection, ageSeconds: number | undefined): string {
  const age = ageSeconds == null ? '' : fmtWait(ageSeconds);
  const seen = !lifecycle.inferred
    ? age && `last event ${age} ago`
    : lifecycle.lastEventTs
      ? `inferred: no event for ${age || 'a while'}`
      : 'inferred from the contract: no event was seen';
  return `Lifecycle: ${[
    lifecycle.id,
    lifecycle.state,
    `phase ${lifecycle.phase}`,
    seen,
    lifecycle.fleeting ? 'fleeting' : '',
  ]
    .filter(Boolean)
    .join(' · ')}`;
}

export interface RowHoverFacts extends RowFacts {
  heading: string;
  kind: string;
  repo: string;
  progress?: RowProgressFacts;
  progressNoun: string;
  // A lifecycle without a document of its own that several task documents name.
  documents?: number;
  currentStep?: string;
  worktree: boolean;
}

// What the document says of the task: its status, the long progress, its current step.
function documentLines(facts: RowHoverFacts): string[] {
  const { progress } = facts;
  return [
    facts.status ? `Document status: ${facts.status}` : '',
    progress ? progressLine(facts.progressNoun, progress) : '',
    facts.documents ? `Task documents: ${facts.documents}` : '',
    facts.currentStep ? `Current step: ${facts.currentStep}` : '',
  ].filter((line): line is string => Boolean(line));
}

// What runs around the task: its lifecycle and its worktree.
function signalLines(facts: RowHoverFacts): string[] {
  const { lifecycle } = facts;
  return [
    lifecycle ? lifecycleLine(lifecycle, facts.ageSeconds) : '',
    facts.worktree ? 'Worktree: on disk' : '',
    facts.repo && facts.repo !== '—' ? `Repo: ${facts.repo}` : '',
  ].filter(Boolean);
}

// Everything a row knows, one fact a line: the whole title and every signal the line itself has
// no room for (kind, document status, the long progress, the lifecycle's facts).
export function rowHover(facts: RowHoverFacts): string {
  return [
    facts.heading,
    `State: ${rowWord(facts).text}`,
    `Kind: ${facts.kind}`,
    ...documentLines(facts),
    ...signalLines(facts),
  ].join('\n');
}

// The id of every master, by the folder its document lies in: a master's document is the
// task.json of its folder, and its tasks lie beside it.
export function masterIdsByFolder(docs: readonly TaskDocNode[]): Map<string, string> {
  const ids = new Map<string, string>();
  for (const doc of docs) {
    if (doc.id && pathStem(doc.docPath) === 'task') ids.set(pathDir(doc.docPath), doc.id);
  }
  return ids;
}

// A task's id as its row shows it under its master: the task "260928-MIK-L144" of the master
// "260928-MIK" is "L144". A sprint and a master show no id.
export function shortTaskId(
  doc: Pick<TaskDocNode, 'id' | 'kind' | 'docPath'>,
  masterIds: ReadonlyMap<string, string>,
): string {
  if (doc.kind === 'master') return '';
  const masterId = pathStem(doc.docPath) === 'task' ? undefined : masterIds.get(pathDir(doc.docPath));
  return masterId && doc.id.startsWith(`${masterId}-`) ? doc.id.slice(masterId.length + 1) : doc.id;
}
