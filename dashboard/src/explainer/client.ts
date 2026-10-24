// The explainer's four routes (MIK-R106 v2 rules 3, 4, 7 and 8) as the dashboard reads and posts
// them. The server side is `serving/explainer.py`; the shapes below are what this module defines
// and what the server must answer. Nothing here starts a model turn: `state` is a read, and the
// three posts are his Send, his Update and his Retire, each pressed by him.
//
//   GET  /api/explainer/{repository}/{master}/{leaf}/state   → ExplainerStateAnswer
//   POST /api/explainer/{repository}/{master}/{leaf}/build   → ExplainerAttemptAnswer (202/200)
//   POST /api/explainer/{repository}/{master}/{leaf}/update  → ExplainerAttemptAnswer (202/200)
//   POST /api/explainer/{repository}/{master}/{leaf}/retire  → ExplainerRetireAnswer (200)
//
// An answer the dashboard does not read is `unavailable` with a sentence, never a throw.
import type { TrayWirePick } from '../tray/types';

/** One leaf's explainer: keyed by repository, master folder and leaf id (MIK-R106 rule 8). */
export interface ExplainerAddress {
  repository: string;
  master: string;
  leaf: string;
}

export type ExplainerSeatKind = 'live' | 'closed' | 'missing';

export interface ExplainerCounts {
  kind: 'files' | 'bytes';
  done: number;
  total: number;
}

export type ExplainerStepState = 'waiting' | 'running' | 'done' | 'failed';

export interface ExplainerBuildStep {
  name: string;
  state: ExplainerStepState;
  counts?: ExplainerCounts;
  detail?: string;
  reason?: string;
}

export interface ExplainerBuild {
  attempt: string;
  state: 'running' | 'done' | 'failed';
  startedAt: string;
  steps: ExplainerBuildStep[];
  failedStep: string | null;
  reason: string | null;
}

export interface ExplainerBindingView {
  kind: 'planned' | 'recorded';
  comparison: number;
  label: string;
}

export interface ExplainerRevisionView {
  revision: number;
  binding: ExplainerBindingView;
  at: string;
  kind: 'global' | 'focused';
  focus?: string;
  agreed: boolean;
  pinned: boolean;
}

export interface ExplainerStalePart {
  id: string;
  title: string;
  reason: string;
}

export interface ExplainerState {
  seat: ExplainerSeatKind | null;
  agentId: string | null;
  workspaceId: string | null;
  generation: number;
  retiredAt: string | null;
  binding: ExplainerBindingView | null;
  build: ExplainerBuild | null;
  staleParts: ExplainerStalePart[];
}

export type ExplainerRead =
  | { kind: 'state'; value: ExplainerState }
  | { kind: 'unavailable'; detail: string };

export interface ExplainerHeldDelivery {
  reason: string | null;
  detail: string | null;
}

export interface ExplainerAttemptAnswer {
  attempt: string;
  state: ExplainerState;
  held?: ExplainerHeldDelivery | null;
}

export interface ExplainerRetireReceipt {
  receiptId: string;
  agentId: string;
  generation: number;
  state: 'pending' | 'finished' | 'failed';
  step: string;
  endedByMarker: number;
  endedByTree: number;
  remaining: number[];
  detail: string;
}

export interface ExplainerRetireAnswer {
  receipt: ExplainerRetireReceipt;
  state: ExplainerState;
}

export interface ExplainerBuildBody {
  requestId?: string;
  target: 'new-explainer';
  message: string;
  picks: TrayWirePick[];
  planned: boolean;
  comparisonNumber?: number | null;
  history?: 'live' | 'recorded' | 'planned';
  stalePartIds: string[];
  staleGrades?: string[];
}

export type ExplainerRoute = 'state' | 'build' | 'update' | 'retire';

let idSeq = 0;

/** A single-segment identifier for a build request or retire receipt (MIK-R186 rules 2 and 8). */
export function newExplainerId(prefix = 'req'): string {
  idSeq += 1;
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return `${prefix}-${crypto.randomUUID()}`;
  }
  return `${prefix}-${Date.now().toString(36)}-${idSeq}`;
}

export function explainerPath(address: ExplainerAddress, route: ExplainerRoute): string {
  const part = (value: string): string => encodeURIComponent(value);
  return `/api/explainer/${part(address.repository)}/${part(address.master)}/${part(address.leaf)}/${route}`;
}

export const sameExplainerAddress = (left: ExplainerAddress, right: ExplainerAddress): boolean =>
  left.repository === right.repository && left.master === right.master && left.leaf === right.leaf;

// ---- reading the server's answers ----

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);
const text = (value: unknown): string | null => (typeof value === 'string' ? value : null);
const nonEmptyText = (value: unknown): string | null =>
  typeof value === 'string' && value.trim() !== '' ? value : null;
const isNumber = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value);
const finiteNumber = (value: unknown): number | null => (isNumber(value) ? value : null);
const numberOr = (value: unknown, fallback: number): number => (isNumber(value) ? value : fallback);

function firstString(values: readonly unknown[]): string | null {
  for (const item of values) {
    const found = nonEmptyText(item);
    if (found !== null) return found;
  }
  return null;
}

function firstFiniteNumber(values: readonly unknown[], fallback: number): number {
  for (const item of values) {
    const found = finiteNumber(item);
    if (found !== null) return found;
  }
  return fallback;
}

const STEP_STATES = new Map<string, ExplainerStepState>([
  ['waiting', 'waiting'],
  ['pending', 'waiting'],
  ['running', 'running'],
  ['done', 'done'],
  ['completed', 'done'],
  ['failed', 'failed'],
]);
const BUILD_STATES = new Set<string>(['running', 'done', 'failed']);
const COUNT_KINDS = new Set<string>(['files', 'bytes']);

const countKind = (value: unknown): ExplainerCounts['kind'] | null =>
  typeof value === 'string' && COUNT_KINDS.has(value) ? (value as ExplainerCounts['kind']) : null;

function readCounts(value: unknown): ExplainerCounts | undefined {
  if (!isRecord(value)) return undefined;
  const kind = countKind(value.kind);
  const done = finiteNumber(value.done ?? value.written);
  const total = finiteNumber(value.total ?? value.files);
  if (kind === null || done === null || total === null || total <= 0) return undefined;
  return { kind, done: Math.max(0, done), total };
}

function readStepRecord(value: Record<string, unknown>): ExplainerBuildStep | null {
  const name = firstString([value.name, value.step, value.title]);
  const state = STEP_STATES.get(text(value.state) ?? 'running');
  if (!name || !state) return null;
  const counts = readCounts(value.counts ?? value);
  const detail = text(value.detail);
  const reason = text(value.reason);
  return {
    name,
    state,
    ...(counts ? { counts } : {}),
    ...(detail ? { detail } : {}),
    ...(reason ? { reason } : {}),
  };
}

function readStep(value: unknown): ExplainerBuildStep | null {
  if (typeof value === 'string' && value.trim()) return { name: value, state: 'done' };
  return isRecord(value) ? readStepRecord(value) : null;
}

function readSteps(value: unknown): ExplainerBuildStep[] {
  if (!Array.isArray(value)) return [];
  return value.map(readStep).filter((step): step is ExplainerBuildStep => step !== null);
}

function readBuild(value: unknown): ExplainerBuild | null {
  if (!isRecord(value)) return null;
  const state = text(value.state);
  if (!state || !BUILD_STATES.has(state)) return null;
  return {
    attempt: text(value.attempt) ?? '',
    state: state as ExplainerBuild['state'],
    startedAt: text(value.startedAt) ?? '',
    steps: readSteps(value.steps),
    failedStep: text(value.failedStep),
    reason: text(value.reason),
  };
}

function classifyBindingKind(owner: unknown, ref: string | null): 'planned' | 'recorded' | null {
  if (owner === 'planned' || Boolean(ref && ref.endsWith('/planned'))) return 'planned';
  if (typeof owner === 'string' && owner.trim() !== '') return 'recorded';
  return null;
}

// The host may spell the owner `kind`, `owner`, or `comparison_owner`, and the number
// `comparison`, `number`, or `comparison_number` (MIK-R103 v2, MIK-R106 v2).
export function parseExplainerBinding(value: unknown): ExplainerBindingView | null {
  if (!isRecord(value)) return null;
  const inner = isRecord(value.binding) ? value.binding : value;
  const owner = firstString([inner.kind, inner.owner, inner.comparison_owner, inner.comparisonOwner]);
  const ref = firstString([inner.reference, inner.comparison_reference, inner.comparisonReference]);
  const kind = classifyBindingKind(owner, ref);
  if (!kind) return null;
  const comparison = firstFiniteNumber(
    [inner.comparison, inner.number, inner.comparison_number, inner.comparisonNumber],
    0,
  );
  const label = text(inner.label) ?? (kind === 'planned' ? 'planned · no changes' : `#${comparison}`);
  return { kind, comparison, label };
}

function readRevisionRow(row: unknown): ExplainerRevisionView[] {
  if (!isRecord(row)) return [];
  const revision = finiteNumber(row.revision);
  const binding = parseExplainerBinding(row.binding);
  if (revision === null || !binding) return [];
  const focus = text(row.focus);
  return [
    {
      revision,
      binding,
      at: firstString([row.at, row.writtenAt]) ?? '',
      kind: text(row.kind) === 'focused' ? 'focused' : 'global',
      ...(focus ? { focus } : {}),
      agreed: row.agreed === true,
      pinned: row.pinned === true,
    },
  ];
}

export function parseExplainerRevisions(answer: unknown): ExplainerRevisionView[] {
  const rows = Array.isArray(answer)
    ? answer
    : isRecord(answer) && Array.isArray(answer.revisions)
      ? answer.revisions
      : [];
  return rows.flatMap(readRevisionRow);
}

const SEAT_MAP = new Map<string, ExplainerSeatKind | null>([
  ['live', 'live'],
  ['closed', 'closed'],
  ['missing', 'missing'],
  ['none', null],
  ['archived', null],
  ['retired', null],
]);

function readSeat(value: unknown): { seat: ExplainerSeatKind | null } | { problem: string } | null {
  const raw = typeof value === 'string' ? value : isRecord(value) ? firstString([value.state, value.seat]) : null;
  if (raw === null) return null;
  if (SEAT_MAP.has(raw)) return { seat: SEAT_MAP.get(raw) ?? null };
  return { problem: `the explainer’s seat answered “${raw}”, which the dashboard does not read` };
}

const BUILDING_STATES = new Map<string, ExplainerBuild['state']>([
  ['starting', 'running'],
  ['building', 'running'],
  ['running', 'running'],
  ['failed', 'failed'],
  ['done', 'done'],
]);

function buildingSteps(value: Record<string, unknown>, state: ExplainerBuild['state']): ExplainerBuildStep[] {
  const listed = Array.isArray(value.steps) ? readSteps(value.steps) : readSteps(value.progress);
  if (listed.length > 0) return listed;
  const singleName = firstString([value.step, value.failedStep]);
  if (!singleName) return [];
  const counts = readCounts(value.counts ?? value);
  const reason = firstString([value.reason, value.detail]);
  const stepState: ExplainerStepState = state === 'done' ? 'done' : state === 'failed' ? 'failed' : 'running';
  return [{ name: singleName, state: stepState, ...(counts ? { counts } : {}), ...(reason ? { reason } : {}) }];
}

function readBuilding(value: unknown): ExplainerBuild | null {
  if (!isRecord(value)) return null;
  const state = BUILDING_STATES.get(text(value.state) ?? '');
  if (!state) return null;
  return {
    attempt: text(value.attempt) ?? '',
    state,
    startedAt: text(value.startedAt) ?? '',
    steps: buildingSteps(value, state),
    failedStep: text(value.failedStep) ?? (state === 'failed' ? text(value.step) : null),
    reason: firstString([value.reason, value.detail]),
  };
}

function readStaleParts(value: unknown): ExplainerStalePart[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((part) => {
    if (!isRecord(part)) return [];
    const id = text(part.id);
    if (!id) return [];
    return [{ id, title: text(part.title) ?? id, reason: text(part.reason) ?? '' }];
  });
}

const UNREADABLE_STATE: ExplainerRead = {
  kind: 'unavailable',
  detail: 'the explainer’s state answered in a form the dashboard does not read',
};

const emptyState = (): ExplainerState => ({
  seat: null,
  agentId: null,
  workspaceId: null,
  generation: 0,
  retiredAt: null,
  binding: null,
  build: null,
  staleParts: [],
});

function retiredAtFromField(value: unknown): string | null {
  if (typeof value === 'string') return value;
  if (value === true) return '';
  if (isRecord(value)) return text(value.at) ?? '';
  return null;
}

function retiredAtOf(answer: Record<string, unknown>, explainer: Record<string, unknown> | null): string | null {
  const candidates = [answer.retiredAt, answer.retired, explainer?.retiredAt, explainer?.retired];
  for (const item of candidates) {
    const parsed = retiredAtFromField(item);
    if (parsed !== null) return parsed;
  }
  return null;
}

function stalePartsOf(answer: Record<string, unknown>): ExplainerStalePart[] {
  const stale = answer.staleParts ?? (isRecord(answer.stale) ? answer.stale.parts : null);
  return readStaleParts(stale);
}

function buildOf(answer: Record<string, unknown>, explainer: Record<string, unknown> | null): ExplainerBuild | null {
  return (
    readBuild(answer.build) ??
    readBuilding(answer.building) ??
    (explainer ? readBuild(explainer.build) ?? readBuilding(explainer.building) : null)
  );
}

function bindingOf(answer: Record<string, unknown>, explainer: Record<string, unknown> | null): ExplainerBindingView | null {
  const explanation = isRecord(answer.explanation) ? answer.explanation : null;
  return (
    parseExplainerBinding(answer.binding) ??
    parseExplainerBinding(explainer?.binding) ??
    parseExplainerBinding(explanation?.binding)
  );
}

function nestedRecord(record: Record<string, unknown>, key: string): Record<string, unknown> | null {
  return isRecord(record[key]) ? (record[key] as Record<string, unknown>) : null;
}

function stateValue(answer: Record<string, unknown>, seat: ExplainerSeatKind | null): ExplainerState {
  const seatRec = nestedRecord(answer, 'seat');
  const explainer = nestedRecord(answer, 'explainer');
  const sources = [answer, seatRec, explainer];
  return {
    seat,
    agentId: firstString(sources.flatMap((src) => (src ? [src.agentId, src.agent_id] : []))),
    workspaceId: firstString(sources.flatMap((src) => (src ? [src.workspaceId, src.workspace] : []))),
    generation: firstFiniteNumber(sources.map((src) => src?.generation), 0),
    retiredAt: retiredAtOf(answer, explainer),
    binding: bindingOf(answer, explainer),
    build: buildOf(answer, explainer),
    staleParts: stalePartsOf(answer),
  };
}

export function parseExplainerState(answer: unknown): ExplainerRead {
  if (!isRecord(answer)) return UNREADABLE_STATE;
  if (answer.kind === 'unavailable') {
    return { kind: 'unavailable', detail: text(answer.detail) ?? 'the explainer state is unavailable' };
  }
  const explainer = nestedRecord(answer, 'explainer');
  const seat = readSeat(answer.seat ?? explainer?.seat ?? null);
  if (seat && 'problem' in seat) return { kind: 'unavailable', detail: seat.problem };
  return { kind: 'state', value: stateValue(answer, seat ? seat.seat : null) };
}

function stateCandidate(answer: Record<string, unknown>): Record<string, unknown> {
  if (isRecord(answer.state)) return answer.state;
  if (isRecord(answer.seat) && ('agentId' in answer.seat || 'workspace' in answer.seat || 'workspaceId' in answer.seat)) {
    return answer.seat;
  }
  return answer;
}

function stateOf(answer: Record<string, unknown>): ExplainerState {
  const read = parseExplainerState(stateCandidate(answer));
  return read.kind === 'state' ? read.value : emptyState();
}

function deliveryRecord(answer: Record<string, unknown>, seat: Record<string, unknown> | null): Record<string, unknown> | null {
  if (isRecord(answer.delivery)) return answer.delivery;
  return seat ? nestedRecord(seat, 'delivery') : null;
}

function isHeldRecord(record: Record<string, unknown> | null): boolean {
  return Boolean(record && (record.state === 'held' || record.held === true));
}

function readHeldDelivery(answer: Record<string, unknown>): ExplainerHeldDelivery | null {
  const seat = nestedRecord(answer, 'seat');
  const delivery = deliveryRecord(answer, seat);
  if (isHeldRecord(delivery)) {
    return { reason: text(delivery!.reason), detail: text(delivery!.detail) };
  }
  if (isHeldRecord(answer) || isHeldRecord(seat)) {
    return { reason: firstString([answer.reason, seat?.reason]), detail: firstString([answer.detail, seat?.detail]) };
  }
  return null;
}

export function parseAttemptAnswer(answer: unknown): ExplainerAttemptAnswer | null {
  if (!isRecord(answer)) return null;
  const held = readHeldDelivery(answer);
  return {
    attempt: text(answer.attempt) ?? '',
    state: stateOf(answer),
    ...(held ? { held } : {}),
  };
}

function receiptState(value: unknown): ExplainerRetireReceipt['state'] {
  return value === 'finished' || value === 'failed' ? value : 'pending';
}

function receiptRemaining(value: unknown): number[] {
  return Array.isArray(value) ? value.filter((pid): pid is number => typeof pid === 'number') : [];
}

function readReceipt(receipt: Record<string, unknown>): ExplainerRetireReceipt {
  const remainingSource = receipt.remaining ?? receipt.remainingPids ?? receipt.remaining_pids;
  return {
    receiptId: firstString([receipt.receiptId, receipt.receipt_id]) ?? '',
    agentId: firstString([receipt.agentId, receipt.agent_id]) ?? '',
    generation: numberOr(receipt.generation, 0),
    state: receiptState(receipt.state),
    step: text(receipt.step) ?? '',
    endedByMarker: firstFiniteNumber([receipt.endedByMarker, receipt.ended_by_marker], 0),
    endedByTree: firstFiniteNumber([receipt.endedByTree, receipt.ended_by_tree], 0),
    remaining: receiptRemaining(remainingSource),
    detail: text(receipt.detail) ?? '',
  };
}

function retireStateOf(
  answer: Record<string, unknown>,
  rawReceipt: Record<string, unknown>,
  receipt: ExplainerRetireReceipt,
): ExplainerState {
  if (isRecord(answer.state)) return stateOf(answer);
  const completed =
    receipt.state === 'finished' && receipt.remaining.length === 0 && answer.state !== 'pending';
  return {
    ...emptyState(),
    seat: completed ? null : 'closed',
    agentId: receipt.agentId || null,
    generation: receipt.generation,
    retiredAt: completed ? (firstString([rawReceipt.finishedAt, rawReceipt.finished_at]) ?? '') : null,
  };
}

export function parseRetireAnswer(answer: unknown): ExplainerRetireAnswer | null {
  if (!isRecord(answer)) return null;
  const retireRec = nestedRecord(answer, 'retire');
  const rawReceipt = nestedRecord(answer, 'receipt') ?? (retireRec ? nestedRecord(retireRec, 'receipt') : null);
  if (!rawReceipt) return null;
  const receipt = readReceipt(rawReceipt);
  return { receipt, state: retireStateOf(answer, rawReceipt, receipt) };
}

export function serverDetail(answer: unknown): string | null {
  if (!isRecord(answer)) return null;
  const detail = answer.detail;
  return typeof detail === 'string' && detail.trim() ? detail.trim() : null;
}

// ---- the calls ----

type Fetcher = typeof fetch;

export async function readExplainerState(
  address: ExplainerAddress,
  fetcher: Fetcher = fetch,
): Promise<ExplainerRead> {
  try {
    const response = await fetcher(explainerPath(address, 'state'));
    const answer: unknown = response.ok ? await response.json() : null;
    if (!response.ok) {
      const said = serverDetail(answer);
      return {
        kind: 'unavailable',
        detail: `the dashboard server answered ${response.status}${said ? `: ${said}` : ''}`,
      };
    }
    return parseExplainerState(answer);
  } catch (error) {
    return {
      kind: 'unavailable',
      detail: error instanceof Error ? error.message : 'the dashboard server could not be reached',
    };
  }
}

export const fetchExplainerState = readExplainerState;

export type ExplainerPost =
  | { kind: 'accepted'; answer: ExplainerAttemptAnswer }
  | { kind: 'refused'; reason: string; detail: string };

function refusalFromResponse(answer: unknown, status: number): { kind: 'refused'; reason: string; detail: string } {
  return {
    kind: 'refused',
    reason: isRecord(answer) && typeof answer.reason === 'string' ? answer.reason : `http-${status}`,
    detail: serverDetail(answer) ?? `the dashboard server answered ${status}`,
  };
}

async function post(
  address: ExplainerAddress,
  route: 'build' | 'update',
  body: ExplainerBuildBody,
  fetcher: Fetcher,
): Promise<ExplainerPost> {
  try {
    const payload: ExplainerBuildBody = { ...body, requestId: body.requestId || newExplainerId('req') };
    const response = await fetcher(explainerPath(address, route), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const answer: unknown = await response.json().catch(() => null);
    if (!response.ok) return refusalFromResponse(answer, response.status);
    const parsed = parseAttemptAnswer(answer);
    if (!parsed) {
      return {
        kind: 'refused',
        reason: 'unreadable-answer',
        detail: 'the build answered in a form the dashboard does not read',
      };
    }
    return { kind: 'accepted', answer: parsed };
  } catch (error) {
    return {
      kind: 'refused',
      reason: 'unreachable',
      detail: error instanceof Error ? error.message : 'the dashboard server could not be reached',
    };
  }
}

export const buildExplainer = (address: ExplainerAddress, body: ExplainerBuildBody, fetcher: Fetcher = fetch) =>
  post(address, 'build', body, fetcher);

export const updateExplainer = (address: ExplainerAddress, body: ExplainerBuildBody, fetcher: Fetcher = fetch) =>
  post(address, 'update', body, fetcher);

export type ExplainerRetireCall =
  | { kind: 'retired'; answer: ExplainerRetireAnswer }
  | { kind: 'refused'; reason: string; detail: string };

function resolveRetireArgs(
  receiptIdOrFetcher?: string | Fetcher,
  maybeFetcher?: Fetcher,
): { receiptId: string; fetcher: Fetcher } {
  const receiptId =
    typeof receiptIdOrFetcher === 'string' && receiptIdOrFetcher.trim()
      ? receiptIdOrFetcher
      : newExplainerId('retire');
  const fetcher: Fetcher =
    typeof receiptIdOrFetcher === 'function' ? receiptIdOrFetcher : (maybeFetcher ?? fetch);
  return { receiptId, fetcher };
}

export async function retireExplainer(
  address: ExplainerAddress,
  receiptIdOrFetcher?: string | Fetcher,
  maybeFetcher?: Fetcher,
): Promise<ExplainerRetireCall> {
  const { receiptId, fetcher } = resolveRetireArgs(receiptIdOrFetcher, maybeFetcher);
  try {
    const response = await fetcher(explainerPath(address, 'retire'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ receiptId }),
    });
    const answer: unknown = await response.json().catch(() => null);
    if (!response.ok) return refusalFromResponse(answer, response.status);
    const parsed = parseRetireAnswer(answer);
    if (!parsed) {
      return {
        kind: 'refused',
        reason: 'unreadable-answer',
        detail: 'Retire answered in a form the dashboard does not read',
      };
    }
    return { kind: 'retired', answer: parsed };
  } catch (error) {
    return {
      kind: 'refused',
      reason: 'unreachable',
      detail: error instanceof Error ? error.message : 'the dashboard server could not be reached',
    };
  }
}
