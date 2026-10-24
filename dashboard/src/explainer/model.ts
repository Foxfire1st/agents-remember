// The explainer column's pure rules (MIK-R106 v2 rules 1–5, 7, 8 and 10): which chat the column
// selects, the placeholder's words, the first request's form, the build panel's steps, the later
// request's references, and Retire's two lists. No network, no storage, no DOM.
import type { TrayPick, TrayPickInput, TraySeat } from '../tray/types';
import type {
  ExplainerAddress,
  ExplainerBindingView,
  ExplainerBuild,
  ExplainerBuildStep,
  ExplainerRead,
  ExplainerStalePart,
  ExplainerState,
} from './client';

/**
 * What the column shows. It selects the leaf's explainer or its placeholder and nothing else: the
 * Worker and the other seats of the task appear in the header's menu, never as the default
 * selection (MIK-R106 rule 1).
 */
export type ExplainerPhase =
  | 'placeholder'
  | 'first-request'
  | 'building'
  | 'live'
  | 'closed'
  | 'retired';

export type ExplainerSelection = 'explainer' | 'placeholder';

/** No seat is the placeholder; a live, closed or building seat is the explainer (MIK-R106 rule 8). */
export function columnSelection(phase: ExplainerPhase): ExplainerSelection {
  return phase === 'placeholder' || phase === 'first-request' || phase === 'retired'
    ? 'placeholder'
    : 'explainer';
}

function statePhase(state: ExplainerState): ExplainerPhase {
  if (state.seat === 'live') return 'live';
  if (state.seat === 'closed') return 'closed';
  if (state.retiredAt !== null) return 'retired';
  return 'placeholder';
}

/**
 * The phase the column is in. `formOpen` is his Explain or ⊕ with no live seat (MIK-R106 rule 3);
 * a running build takes precedence while it runs.
 */
export function phaseOf(read: ExplainerRead | null, formOpen: boolean): ExplainerPhase {
  const state = read?.kind === 'state' ? read.value : null;
  if (state?.build?.state === 'running') return 'building';
  if (formOpen) return 'first-request';
  return state ? statePhase(state) : 'placeholder';
}

/** The status word beside the header's name, in the mockup's words. */
export function phaseWords(phase: ExplainerPhase, state: ExplainerState | null): string {
  if (phase === 'building') return 'starting';
  if (phase === 'live') return 'live · idle';
  if (phase === 'closed') return 'closed · resumes when shown';
  if (phase === 'retired') return `retired${state?.retiredAt ? ` ${clock(state.retiredAt)}` : ''}`.trim();
  if (phase === 'first-request') return 'new request · not sent';
  return 'not started';
}

function clock(iso: string): string {
  if (!iso) return '';
  const time = new Date(iso);
  if (Number.isNaN(time.getTime())) return iso;
  return `${String(time.getHours()).padStart(2, '0')}:${String(time.getMinutes()).padStart(2, '0')}`;
}

// ---- the placeholder (MIK-R106 rule 2) ----

/** The settings role the explainer runs on (MIK-R106 rule 8). */
export const EXPLAINER_ROLE = 'Explainer';
export const EXPLAINER_SETTINGS_ROLE = 'review-presenter';

export interface PlaceholderWords {
  headline: string;
  line: string;
  /** The role is fixed; the mockup marks it 🔒. */
  role: string;
  settingsRole: string;
  retiredLine: string | null;
}

/**
 * The placeholder: the role fixed, the agent from the settings role `review-presenter`, and one
 * line saying nothing runs until he asks. It has no Start button; Explain and ⊕ open the form.
 */
export function placeholderWords(state: ExplainerState | null): PlaceholderWords {
  const retired = state?.retiredAt ?? null;
  const clocked = retired ? clock(retired) : '';
  return {
    headline:
      retired !== null
        ? clocked
          ? `The explainer was retired at ${clocked}.`
          : 'The explainer was retired.'
        : 'No explainer for this task yet.',
    line:
      retired !== null
        ? 'Its chat and its workspace are gone; the saved explanation stays readable. Nothing restarts by itself.'
        : 'Nothing runs and no agent exists until you ask.',
    role: EXPLAINER_ROLE,
    settingsRole: EXPLAINER_SETTINGS_ROLE,
    retiredLine:
      retired !== null
        ? 'Press ✦ Explain or ⊕ on any block to start a new one. It gets a fresh workspace in the same environment and builds nothing again.'
        : null,
  };
}

// ---- the first request's form (MIK-R106 rule 3) ----

/** The target is fixed while no seat exists (MIK-R188 rule 7). */
export const fixedTarget = (task: string): string => `a new explainer for ${task}`;

/** What the form says it will build: "#8", or "planned · no changes" for a planning leaf. */
export function bindingWords(binding: ExplainerBindingView | null): string {
  if (!binding) return 'the comparison on screen';
  return binding.kind === 'planned' ? 'planned · no changes' : binding.label;
}

/** One reference in the form: the tray's pick, its kind's words and his note. */
export interface FormReference {
  id: string;
  title: string;
  detail: string;
  note: string;
}

export function formReferences(picks: readonly TrayPick[], kindWords: (pick: TrayPick) => string): FormReference[] {
  return picks.map((pick) => ({
    id: pick.id,
    title: pick.input.title,
    detail: kindWords(pick),
    note: pick.note,
  }));
}

/** "One message: 2 references the agent opens with AR's tools, 1 note, your text." */
export function firstRequestLine(references: readonly FormReference[], message: string): string {
  const notes = references.filter((reference) => reference.note.trim() !== '').length;
  const parts = [
    references.length
      ? `${references.length} reference${references.length === 1 ? '' : 's'} the agent opens with AR's tools`
      : '',
    notes ? `${notes} note${notes === 1 ? '' : 's'}` : '',
    message.trim() ? 'your text' : '',
  ].filter(Boolean);
  return parts.length ? `One message: ${parts.join(', ')}.` : 'Nothing to send yet: add a reference or write your request.';
}

/** Send is refused until there is something to send: a reference, stale work, or his words. */
export function canSend(
  references: readonly FormReference[],
  message: string,
  staleParts: readonly ExplainerStalePart[] = [],
  staleGrades: readonly string[] = [],
): boolean {
  return (
    references.length > 0 ||
    message.trim() !== '' ||
    staleParts.length > 0 ||
    staleGrades.length > 0
  );
}

/** What goes out: the role capsule with the first request only, and never again (MIK-R106 rule 5). */
export const CAPSULE_LINE = 'included, with the first request only';

// ---- the build's progress (MIK-R106 rule 4.1) ----

export const STEP_MARK: Record<ExplainerBuildStep['state'], string> = {
  done: '✓',
  running: '◌',
  waiting: '·',
  failed: '✗',
};

/** A step's own percentage, for a step that counts files or bytes; null when it counts nothing. */
export function stepPercent(step: ExplainerBuildStep): number | null {
  const counts = step.counts;
  if (!counts || counts.total <= 0) return null;
  const share = Math.min(1, Math.max(0, counts.done / counts.total));
  return Math.floor(share * 100);
}

function stepShare(step: ExplainerBuildStep): number {
  const percent = stepPercent(step);
  if (percent !== null) return percent / 100;
  if (step.state === 'done') return 1;
  return 0;
}

/** The whole build's percentage: each step's share, equally weighted. */
export function buildPercent(build: ExplainerBuild): number {
  if (build.steps.length === 0) return build.state === 'done' ? 100 : 0;
  const total = build.steps.reduce((sum, step) => sum + stepShare(step), 0);
  return Math.round((total / build.steps.length) * 100);
}

/** A step's second line: its own count and detail, or why it failed. */
export function stepDetail(step: ExplainerBuildStep): string | null {
  if (step.state === 'failed' && step.reason) return step.reason;
  const percent = stepPercent(step);
  const count = step.counts
    ? `${step.counts.done.toLocaleString('en-US')} of ${step.counts.total.toLocaleString('en-US')} ${step.counts.kind}`
    : null;
  const parts = [count, step.detail ?? ''].filter(Boolean);
  if (percent !== null) parts.push(`${percent}%`);
  return parts.length ? parts.join(' · ') : null;
}

/** The failed step's name and reason, when the build stopped (MIK-R106 rule 4). */
export function buildFailure(build: ExplainerBuild): { step: string; reason: string } | null {
  if (build.state !== 'failed') return null;
  const failed = build.steps.find((step) => step.state === 'failed');
  return {
    step: build.failedStep ?? failed?.name ?? 'the build',
    reason: build.reason ?? failed?.reason ?? 'no reason was given',
  };
}

/** The step the build is on, or null when it is not running. */
export function runningStep(build: ExplainerBuild): ExplainerBuildStep | null {
  return build.steps.find((step) => step.state === 'running') ?? null;
}

// ---- later requests (MIK-R106 rule 5) ----

/**
 * The change-set reference the global ✦ Explain adds while an explainer is live. The column never
 * sends it: it goes into the tray, and his Send sends it (MIK-R106 rule 5; MIK-R188 rule 7).
 */
export function changeSetPick(
  address: ExplainerAddress,
  title: string,
  comparison: number | null,
): TrayPickInput {
  return {
    kind: 'reference',
    title,
    reference: {
      kind: 'change-set',
      comparison: {
        repository: address.repository,
        task: address.master,
        leaf: address.leaf,
        number: comparison ?? 0,
        pinRef: `refs/ar/review/${address.master}/${address.leaf}/${comparison ?? 0}`,
        trees: { codeBase: '', codeCandidate: '', memoryBase: '', memoryCandidate: '' },
      },
    },
  };
}

// ---- Retire (MIK-R106 rule 7) ----

export interface RetireConfirmation {
  headline: string;
  /** What ends. */
  ends: string[];
  /** What stays. */
  stays: string[];
  line: string;
}

/** The confirmation names what ends and what stays (MIK-R106 rule 7). */
export function retireConfirmation(task: string, state: ExplainerState | null): RetireConfirmation {
  const marker = state?.workspaceId ?? 'this leaf’s explainer workspace';
  return {
    headline: `Retire the explainer of ${task}?`,
    ends: [
      'The seat: this chat, and the explainer’s agent in Paseo.',
      'Every process the seat started, ended by its recorded process tree and by the marker AR_EXPLAINER_WORKSPACE.',
      `Its workspace (${marker}): its binding, its snapshot, its scratch and its drafts.`,
    ],
    stays: [
      'The saved explanation pages, readable and marked “from a retired explainer”.',
      'The grades, readable as they are.',
    ],
    line:
      'Its chat and its workspace are archived and its process ends. The saved explanation and the grades stay readable, marked as from a retired explainer. You can start a new explainer later with ✦ Explain.',
  };
}

/** Whether the column offers Retire at all: a seat exists to retire. */
export function canRetire(state: ExplainerState | null): boolean {
  return state?.seat === 'live' || state?.seat === 'closed';
}

// ---- the header's agent menu (MIK-R106 rule 1) ----

export interface ExplainerMenuScope {
  repository?: string;
  master?: string;
  leaf?: string;
  taskPath?: string;
  masterPath?: string;
  selectedAgentId?: string | null;
}

export interface ExplainerMenuRow {
  agentId: string;
  workspaceId?: string | null;
  role: string;
  task: string;
  state: string;
  /** The row the column is showing. */
  current: boolean;
}

const capitalized = (role: string): string => (role ? role[0].toUpperCase() + role.slice(1) : role);

function seatFolder(path: string): string {
  const slash = path.lastIndexOf('/');
  return slash >= 0 ? path.slice(0, slash) : '';
}

function pathMatchesScope(path: string, scope?: ExplainerMenuScope): boolean {
  if (!scope) return true;
  if (scope.taskPath) {
    return path === scope.taskPath || path === scope.masterPath;
  }
  if (!scope.master) return true;
  const folder = seatFolder(path);
  return !folder || folder === scope.master;
}

function seatMatchesScope(seat: TraySeat, task: string, scope?: ExplainerMenuScope): boolean {
  const seatTask = seat.task;
  if (!seatTask) return false;
  if (scope?.repository && seatTask.repository !== scope.repository) return false;
  if (!pathMatchesScope(seatTask.path, scope)) return false;
  const leafId = scope?.leaf || task;
  return seatTask.id === leafId || seatTask.id === task;
}

/**
 * The chats of this task, from MIK-R188's host-wide seat list, filtered by repository, master and
 * task (MIK-R106 rule 1). The explainer is the first row; the task's other seats follow.
 */
export function agentMenu(
  seats: readonly TraySeat[],
  task: string,
  explainer: { agentId: string | null; workspaceId?: string | null; live: boolean },
  scope?: ExplainerMenuScope,
): ExplainerMenuRow[] {
  const selectedId = scope?.selectedAgentId ?? null;
  const rows: ExplainerMenuRow[] = explainer.agentId
    ? [
        {
          agentId: explainer.agentId,
          workspaceId: explainer.workspaceId ?? null,
          role: EXPLAINER_ROLE,
          task,
          state: explainer.live ? 'live' : 'closed',
          current: selectedId ? selectedId === explainer.agentId : true,
        },
      ]
    : [];
  const others = seats
    .filter(
      (seat) =>
        seatMatchesScope(seat, task, scope) &&
        seat.role.toLowerCase() !== EXPLAINER_ROLE.toLowerCase() &&
        seat.agentId !== explainer.agentId,
    )
    .map((seat) => ({
      agentId: seat.agentId,
      workspaceId: seat.workspaceId ?? null,
      role: capitalized(seat.role),
      task,
      state: 'live',
      current: selectedId === seat.agentId,
    }));
  return [...rows, ...others];
}

// ---- Send aimed at an archived explainer (MIK-R106 rule 3) ----

/**
 * A Send aimed at an explainer that is archived or missing opens the first-request form with the
 * tray's picks. It is not refused, and nothing is sent.
 */
export function sendAimedAtArchived(state: ExplainerState | null): boolean {
  if (!state) return false;
  return state.seat === null || state.seat === 'missing';
}

// ---- Regenerate stale parts with no seat (MIK-R106 rule 3) ----

/** The parts the form is pre-filled with when there is no seat to regenerate them in. */
export function stalePrefill(state: ExplainerState | null): ExplainerStalePart[] {
  return state?.staleParts ?? [];
}
