/**
 * The Explanation view's routes, from the review page's side (MIK-R106 v2 rule 6; MIK-R103 v2;
 * MIK-R104 v3).
 *
 *   GET  /api/explainer/<repository>/<master>/<leaf>/state            the binding, the seat, the saved
 *     page, the trusted stale parts, the claim map and the newer comparison the leaf moved to;
 *   GET  /api/explainer/<repository>/<master>/<leaf>/revisions        the retained revisions, with
 *     their binding, time, kind and the agreed (pinned) one;
 *   GET  /api/explainer/<repository>/<master>/<leaf>/stale            the top bar's badge alone: the
 *     staleness `/state` carries, the shown revision and the newer-comparison notice, no seat;
 *   POST /api/explainer/<repository>/<master>/<leaf>/update           the developer's press
 *     (MIK-R186 rule 6);
 *   POST /api/explainer/<repository>/<master>/<leaf>/cancel           the developer's end to a stuck
 *     build (MIK-R211 v2 rules 4 and 5), named by the build's own request id;
 *   POST /api/explainer/<repository>/<master>/<leaf>/remove-revisions the developer's removal of old
 *     revisions (MIK-R103 v2 rule 8), never the pinned one.
 *
 * The first and third are the same routes the chat column's client reads
 * (`src/explainer/client.ts`), and this module reads the same answer: the owner may be `kind` or
 * `owner`, the number `comparison` or `number`, the seat a string or `{state}`, and the stale parts
 * `stale.parts` or `staleParts`. Nothing here fails on a spelling the other client accepts.
 *
 * A saved page is shown by opening the existing isolated-host channel for its exact revision
 * (`canvasApi.openSession`, which already carries `revision`), so this module adds no second host and
 * no second frame instance: the frames here are MIK-R104 v3's, on MIK-R103 v2's revisions.
 *
 * Nothing in this module starts a model, an agent or a build. The two presses are the developer's
 * own, and no read resumes anything (MIK-R106 v2 rule 10).
 */

import { sharedRead } from '../../../data/abortableRead';
import { openingFetch } from '../../../data/reviewOpen';
import { CanvasRefusal, revokeSession, type CanvasSession } from '../canvas/canvasApi';
import {
  parseExplainerBuild,
  type ExplainerAddress,
  type ExplainerBuild,
} from '../../../explainer/client';
import { readCategories, readGrades } from './explainerGradeParser';
import { firstText, isRecord, text } from './explainerValues';
import { readPlannedPage, type PlannedPage } from './planned/plannedPage';

/** The explainer frame's lineage ID (MIK-R103 v2; MIK-R106 v2). */
export const EXPLAINER_FRAME_ID = 'explainer';

/** The leaf the explainer belongs to: one explainer per leaf (MIK-R106 v2 rule 8). */
export type ExplainerTarget = ExplainerAddress;

/** The comparison a revision is bound to: `#n` for a recorded comparison, `planned` before the go. */
export type BindingOwner = 'recorded' | 'planned';

export interface ExplainerBinding {
  owner: BindingOwner;
  /** The lineage number; 0 for a planned binding (MIK-R103 v2 item 3). */
  number: number;
  /** `review-comparison:<task>/<leaf>/<n>`, or `.../planned`, when the host names one. */
  reference: string | null;
  /** The label as the host spells it: `#8`, or `planned · no changes`. Never `#0` for planned. */
  label: string | null;
  /** The full raw binding record when the host supplied one. */
  raw?: Record<string, unknown>;
}

/** The seat's three states, and the two that end it (MIK-R106 v2 rules 7 and 8). */
export type SeatState = 'none' | 'live' | 'closed' | 'archived' | 'missing' | 'retired';

/** A claim's MIK-R102 basis: one of the four classes the legend names. */
export type ClaimBasis = 'recorded-fact' | 'mechanical-signal' | 'presenter-inference' | 'intended';

/**
 * One stale part as the trusted list holds it (MIK-R104 v3 rule 3).
 *
 * The reasons never travel into the frame: they live here, outside it, where a page cannot change
 * them. `title` is the part's own title from the product-written part map.
 */
export interface StalePart {
  id: string;
  title: string;
  reason: string;
}

/** Selective regeneration metadata for stale parts and grades (MIK-R187 rule 8). */
export interface StaleRegeneration {
  label: string;
  message: string;
  partCount: number;
  gradeCount: number;
}

/** One scale point of a judgment category, highest first (MIK-R187 rule 2). */
export interface ExplainerScalePoint {
  grade: string;
  meaning?: string;
}

/** One judgment category defined by the explainer (MIK-R187 rule 2). */
export interface ExplainerCategory {
  name: string;
  meaning?: string;
  scale: ExplainerScalePoint[];
}

/** One block grade recorded by the explainer (MIK-R187 rules 2–6). */
export interface ExplainerGrade {
  reference: string;
  category: string;
  grade: string;
  reason: string;
  stale: boolean;
  staleReason?: string | null;
  carried?: Array<{
    reference?: string;
    category: string;
    grade: string;
    reason: string;
    stale?: boolean;
    staleReason?: string | null;
  }>;
}

/** The block a claim links to: MIK-R89 v2's block identity, by id. */
export interface ClaimLink {
  /** An entry id, or a hunk's subject `hunk:<path>@<base>..<candidate>`. */
  block: string;
  /** The link's text, shown verbatim. */
  label: string;
  /** The comparison the claim was written at, when the host names one. */
  comparison?: string;
}

/** One claim of the revision's claim map, as the product recorded it (MIK-R104 v3 rule 4). */
export interface ExplanationClaim {
  id: string;
  /** The part of the part map that holds it (the innermost `data-part`). */
  part: string;
  basis: ClaimBasis;
  /** The claim's text, when the host carries it; shown as plain text. */
  text?: string;
  link?: ClaimLink;
}

/** The saved page: one revision of one frame, bound to one comparison. */
export interface SavedExplanation {
  revision: number;
  /** When it was written, ISO 8601. */
  at: string | null;
  entrypoint: string | null;
  comparison: string | null;
  binding?: ExplainerBinding | null;
  /** What its interaction check reached, as the revision states it: "2 of 5 passes" (MIK-R211 v5). */
  coverage?: string;
}

/** The leaf moved past the binding: the newer comparison the top line's badge names (F18). */
export interface ExplainerMoved {
  from: number | null;
  to: number | null;
  /** The master line's tip commits, for a planned binding (MIK-R186 rule 7). */
  commitFrom?: string | null;
  commitTo?: string | null;
  blocks: number;
  /** The line the watcher published on `/api/stream`, shown as it was published. */
  text?: string;
}

/** Why a new revision was refused: the task total, the per-revision ceiling, or another name. */
export interface ExplainerLimit {
  state: 'ok' | 'refused';
  reason?: string;
  detail?: string;
}

export interface ExplainerIdentityRecord {
  agentId: string | null;
  workspace: string | null;
  workspaceId: string | null;
  generation: number;
  present?: boolean;
}

/**
 * `GET .../state`.
 *
 * `explanation` is null while none exists; `building` says the environment is being built, in which
 * case the view shows a still placeholder and sends the reader to the chat (rule 4.1, rule 6).
 */
export interface ExplainerState {
  state: 'ok' | 'refused';
  reason?: string;
  detail?: string;
  binding: ExplainerBinding | null;
  seat: {
    state: SeatState;
    detail?: string;
    since?: string;
    agentId?: string | null;
    workspaceId?: string | null;
    generation?: number;
  };
  explainer?: ExplainerIdentityRecord | null;
  retired: boolean;
  retiredAt: string | null;
  explanation: SavedExplanation | null;
  building: { state: 'idle' | 'building' | 'starting'; step?: string } | null;
  /**
   * The server's own build record, whatever its state (MIK-R106 v2 rule 4): the same record the
   * chat column reads, so a stopped build names the step and the sentence the server wrote in
   * `error`. The view draws it where the developer pressed Explain.
   */
  build?: ExplainerBuild | null;
  /** The trusted stale parts and grades, computed by the host; `total` is the true count when more are stale. */
  stale: {
    parts: StalePart[];
    total?: number;
    unavailable?: string;
    wholePage?: boolean;
    staleGrades?: string[];
    trusted?: string[];
    regeneration?: StaleRegeneration;
  } | null;
  /** The product-written part map's titles, in page order. */
  parts: Array<{ id: string; title: string }>;
  claims: ExplanationClaim[];
  categories?: ExplainerCategory[];
  grades?: ExplainerGrade[];
  moved: ExplainerMoved | null;
  limit: ExplainerLimit | null;
}

/**
 * `GET .../stale`: what the top bar's badge and the Review tab's judgment chips read (F18), and
 * nothing seat-related. Its fields are `ExplainerState`'s own, so the badge's model reads either.
 */
export type ExplainerStaleBadge = Pick<
  ExplainerState,
  'state' | 'stale' | 'categories' | 'grades' | 'explanation' | 'moved' | 'building' | 'retired'
>;

/** One retained revision (MIK-R103 v2 rules 1, 8). */
export interface ExplainerRevision {
  revision: number;
  binding: ExplainerBinding;
  at: string;
  kind: 'global' | 'focused';
  /** What a focused revision explains; absent for a global one. */
  focus?: string;
  /** What its interaction check reached, as the revision states it: "2 of 5 passes" (MIK-R211 v5). */
  coverage?: string;
  /** `intentReview.agreedRevision` names it: pinned, never offered for removal. */
  agreed: boolean;
  pinned: boolean;
  /** A revision the developer removed keeps its unavailable receipt (MIK-R103 v2 evidence 5). */
  removed?: boolean;
  bytes?: number;
  parts?: Array<{ id: string; title: string }>;
  claims?: ExplanationClaim[];
  stale?: ExplainerState['stale'];
}

/** `GET .../revisions`, with the task total the byte ceiling is measured against. */
export interface ExplainerRevisions {
  state: 'ok' | 'refused';
  reason?: string;
  detail?: string;
  revisions: ExplainerRevision[];
  /** The agreed revision's number, as `intentReview.agreedRevision` writes it (MIK-R189 rule 9). */
  pinned?: number | null;
  total?: { used: number; limit: number };
}

/** What an Update's press answers: the attempt it joined, and its counts when the host gives them. */
export interface ExplainerUpdate {
  attempt: string | null;
  counts?: {
    blocks?: number;
    parts?: number;
    grades?: number;
    files?: number;
    bytes?: number;
  };
}

export interface ExplainerRemoval {
  state: 'ok' | 'refused';
  reason?: string;
  detail?: string;
  removed?: number[];
  remaining?: number;
}

/** A named refusal, carried from the host rather than swallowed. */
export class ExplainerRefusal extends Error {
  readonly reason: string;

  constructor(reason: string, detail: string) {
    super(detail);
    this.reason = reason;
  }
}

/** `POST .../cancel`'s answer: the request it ended, and what the seat did (MIK-R105 v1 rule 9). */
export interface ExplainerCancel {
  /** The request the cancel named; the build's own `requestId`, echoed back. */
  requestId: string | null;
  seat: string | null;
  /** The host stopped the turn it was running. */
  turnStopped: boolean;
  /** The ledger did not name this request: the cancel is reported, not claimed. */
  requestUncertain: boolean;
  /** No page was written between the route's two reads of the frame store. */
  publicationPrevented: boolean;
}

export interface RefusalInfo {
  reason: string;
  detail: string;
}

/** A thrown refusal as the view shows it, or null for anything that is not one. */
export function refusalInfo(error: unknown): RefusalInfo | null {
  if (error instanceof ExplainerRefusal) return { reason: error.reason, detail: error.message };
  return null;
}

// ---- reading the host's answers, leniently ----

const number = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null;

async function bodyOf(response: Response): Promise<Record<string, unknown>> {
  let answer: unknown;
  try {
    answer = await response.json();
  } catch {
    answer = null;
  }
  if (!isRecord(answer)) {
    throw new ExplainerRefusal('no-answer', `the host answered nothing for ${response.url}`);
  }
  if (!response.ok || answer.state === 'refused') {
    throw new ExplainerRefusal(
      text(answer.reason) ?? `http-${response.status}`,
      text(answer.detail) ?? response.statusText,
    );
  }
  return answer;
}

// ---- the address ----

const API = '/api/explainer';

/** The same three encoded segments as `explainerPath` in `src/explainer/client.ts`. */
export function leafUrl(target: ExplainerTarget, rest = ''): string {
  const part = (value: string) => encodeURIComponent(value);
  return `${API}/${part(target.repository)}/${part(target.master)}/${part(target.leaf)}${rest}`;
}

export interface ExplainerStateQuery {
  revision?: number | null;
  comparison?: string | null;
  comparisonNumber?: number | null;
  comparisonOwner?: BindingOwner | string | null;
}

export function stateUrl(target: ExplainerTarget, query?: ExplainerStateQuery): string {
  const base = leafUrl(target, '/state');
  if (!query) return base;
  const params = new URLSearchParams();
  if (query.revision !== undefined && query.revision !== null) {
    params.set('revision', String(query.revision));
  }
  if (query.comparison) {
    params.set('comparison', query.comparison);
  }
  if (query.comparisonNumber !== undefined && query.comparisonNumber !== null) {
    params.set('comparisonNumber', String(query.comparisonNumber));
  }
  if (query.comparisonOwner) {
    params.set('comparisonOwner', query.comparisonOwner);
  }
  const search = params.toString();
  return search ? `${base}?${search}` : base;
}

export function staleUrl(target: ExplainerTarget, query?: ExplainerStateQuery): string {
  const state = stateUrl(target, query);
  const search = state.indexOf('?');
  return `${leafUrl(target, '/stale')}${search < 0 ? '' : state.slice(search)}`;
}

export function revisionsUrl(target: ExplainerTarget): string {
  return leafUrl(target, '/revisions');
}

export function plannedUrl(target: ExplainerTarget): string {
  return leafUrl(target, '/planned');
}

export function cancelUrl(target: ExplainerTarget): string {
  return leafUrl(target, '/cancel');
}

// ---- the parsers ----

const RECORDED_OWNERS = new Set([
  'recorded',
  'agents_remember.application.review_tree_comparison',
  'review-tree-comparison',
]);

function firstNumber(values: readonly unknown[]): number | null {
  for (const val of values) {
    if (typeof val === 'number' && Number.isFinite(val)) return val;
  }
  return null;
}

const PLANNED_OWNERS = new Set<string>(['planned', 'review-planned-comparison']);

function resolveBindingOwner(
  owner: unknown,
  hasNumberOrRef: boolean,
  ref: string | null,
): BindingOwner | null {
  if (typeof owner === 'string') {
    if (PLANNED_OWNERS.has(owner)) return 'planned';
    if (RECORDED_OWNERS.has(owner)) return 'recorded';
    if (owner.trim() !== '') return owner.includes('planned') ? 'planned' : 'recorded';
  }
  return hasNumberOrRef && ref?.endsWith('/planned') ? 'planned' : null;
}

export function readBinding(
  value: unknown,
  fallbackLabel?: string | null,
): ExplainerBinding | null {
  if (!isRecord(value)) return null;
  const record = isRecord(value.binding) ? value.binding : value;
  const rawNum = firstNumber([
    record.comparison,
    record.number,
    record.comparison_number,
    record.comparisonNumber,
  ]);
  const ref = firstText([
    record.reference,
    record.comparison_reference,
    record.comparisonReference,
  ]);
  const rawOwner = firstText([
    record.kind,
    record.owner,
    record.comparison_owner,
    record.comparisonOwner,
  ]);
  const kind = resolveBindingOwner(rawOwner, rawNum !== null || ref !== null, ref);
  if (!kind) return null;
  const label = firstText([record.label, value.label, fallbackLabel]);
  return {
    owner: kind,
    number: rawNum ?? 0,
    reference: ref,
    label,
    raw: record,
  };
}

function hasIdentityValue(
  agentId: string | null,
  workspace: string | null,
  generation: number,
  present: boolean | undefined,
): boolean {
  return Boolean(agentId || workspace || generation > 0 || present !== undefined);
}

function readExplainerIdentity(answer: Record<string, unknown>): ExplainerIdentityRecord | null {
  const nested = isRecord(answer.explainer) ? answer.explainer : {};
  const seatRec = isRecord(answer.seat) ? answer.seat : {};
  const agentId = firstText([
    answer.agentId,
    answer.agent_id,
    nested.agentId,
    nested.agent_id,
    seatRec.agentId,
    seatRec.agent_id,
  ]);
  const workspace = firstText([
    answer.workspace,
    answer.workspaceId,
    nested.workspace,
    nested.workspaceId,
    seatRec.workspace,
    seatRec.workspaceId,
  ]);
  const workspaceId =
    firstText([answer.workspaceId, nested.workspaceId, nested.workspace]) ?? workspace;
  const generation = firstNumber([answer.generation, nested.generation, seatRec.generation]) ?? 0;
  const present = typeof nested.present === 'boolean' ? nested.present : undefined;
  if (!hasIdentityValue(agentId, workspaceId, generation, present)) return null;
  return {
    agentId,
    workspace,
    workspaceId,
    generation,
    ...(present !== undefined ? { present } : {}),
  };
}

const SEAT_STATES = new Set<string>(['none', 'live', 'closed', 'archived', 'missing', 'retired']);

function resolveSeatState(value: unknown, rec: Record<string, unknown> | null): SeatState {
  const raw = typeof value === 'string' ? value : rec ? text(rec.state) : null;
  return raw && SEAT_STATES.has(raw) ? (raw as SeatState) : 'none';
}

function attachSeatIdentity(
  result: ExplainerState['seat'],
  identity: ExplainerIdentityRecord | null,
) {
  if (!identity) return;
  if (identity.agentId) result.agentId = identity.agentId;
  if (identity.workspaceId) result.workspaceId = identity.workspaceId;
  if (identity.generation > 0) result.generation = identity.generation;
}

function readSeat(
  value: unknown,
  identity: ExplainerIdentityRecord | null,
): ExplainerState['seat'] {
  const rec = isRecord(value) ? value : null;
  const result: ExplainerState['seat'] = {
    state: resolveSeatState(value, rec),
  };
  const detail = firstText([rec?.detail]);
  const since = firstText([rec?.since]);
  if (detail) result.detail = detail;
  if (since) result.since = since;
  attachSeatIdentity(result, identity);
  return result;
}

function readRegeneration(value: unknown): StaleRegeneration | undefined {
  if (!isRecord(value)) return undefined;
  const label = text(value.label);
  const message = text(value.message);
  if (!label && !message) return undefined;
  const partCount = firstNumber([value.partCount, value.part_count]) ?? 0;
  const gradeCount = firstNumber([value.gradeCount, value.grade_count]) ?? 0;
  const defaultLabel = `Regenerate stale (${partCount} ${partCount === 1 ? 'part' : 'parts'} · ${gradeCount} ${gradeCount === 1 ? 'grade' : 'grades'})`;
  return {
    label: label ?? defaultLabel,
    message: message ?? '',
    partCount,
    gradeCount,
  };
}

function readStringArray(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) return undefined;
  return value.filter((item): item is string => typeof item === 'string' && item.length > 0);
}

function rawStalePartsList(
  answer: Record<string, unknown>,
  raw: Record<string, unknown> | null,
): unknown[] | null {
  if (raw && Array.isArray(raw.parts)) return raw.parts;
  if (Array.isArray(answer.staleParts)) return answer.staleParts;
  return null;
}

function parseStalePartRow(part: unknown): StalePart[] {
  if (!isRecord(part)) return [];
  const id = firstText([part.id, part.part]);
  if (!id) return [];
  return [{ id, title: text(part.title) ?? id, reason: text(part.reason) ?? '' }];
}

function attachRawStaleMeta(
  result: NonNullable<ExplainerState['stale']>,
  raw: Record<string, unknown> | null,
) {
  if (!raw) return;
  const total = number(raw.total);
  const unavailable = text(raw.unavailable);
  const trusted = readStringArray(raw.trusted);
  if (total !== null) result.total = total;
  if (unavailable) result.unavailable = unavailable;
  if (typeof raw.wholePage === 'boolean') result.wholePage = raw.wholePage;
  if (trusted) result.trusted = trusted;
}

function readStaleGradesList(
  answer: Record<string, unknown>,
  raw: Record<string, unknown> | null,
): string[] | undefined {
  if (raw) {
    const fromRaw = readStringArray(raw.staleGrades) ?? readStringArray(raw.stale_grades);
    if (fromRaw) return fromRaw;
  }
  return readStringArray(answer.staleGrades) ?? readStringArray(answer.stale_grades);
}

function readStaleRegeneration(
  answer: Record<string, unknown>,
  raw: Record<string, unknown> | null,
): StaleRegeneration | undefined {
  return (
    (raw ? readRegeneration(raw.regeneration) : undefined) ?? readRegeneration(answer.regeneration)
  );
}

function readStale(answer: Record<string, unknown>): ExplainerState['stale'] {
  const raw = isRecord(answer.stale) ? answer.stale : null;
  const list = rawStalePartsList(answer, raw);
  const staleGrades = readStaleGradesList(answer, raw);
  const regeneration = readStaleRegeneration(answer, raw);
  if (list === null && !staleGrades && !regeneration && !raw) return null;
  const result: NonNullable<ExplainerState['stale']> = {
    parts: (list ?? []).flatMap(parseStalePartRow),
  };
  attachRawStaleMeta(result, raw);
  if (staleGrades) result.staleGrades = staleGrades;
  if (regeneration) result.regeneration = regeneration;
  return result;
}

function rawExplanationRecord(answer: Record<string, unknown>): Record<string, unknown> | null {
  if (isRecord(answer.explanation)) return answer.explanation;
  if (isRecord(answer.page)) return answer.page;
  if (isRecord(answer.frame)) return answer.frame;
  return null;
}

function readExplanation(answer: Record<string, unknown>): SavedExplanation | null {
  const raw = rawExplanationRecord(answer);
  if (raw) {
    const revision = number(raw.revision);
    if (revision === null) return null;
    const binding = readBinding(raw.binding);
    const coverage = text(raw.coverage);
    return {
      revision,
      at: firstText([raw.at, raw.writtenAt]),
      entrypoint: text(raw.entrypoint),
      comparison: firstText([raw.comparison, binding?.reference]),
      ...(binding ? { binding } : {}),
      ...(coverage ? { coverage } : {}),
    };
  }
  const revision = number(answer.readyRevision);
  if (revision === null) return null;
  return {
    revision,
    at: text(answer.writtenAt),
    entrypoint: text(answer.entrypoint),
    comparison: text(answer.comparison),
  };
}

function readBuilding(answer: Record<string, unknown>): ExplainerState['building'] {
  if (isRecord(answer.building)) {
    const state = text(answer.building.state);
    if (state === 'building' || state === 'starting') {
      const step = text(answer.building.step);
      return { state, ...(step ? { step } : {}) };
    }
  }
  if (isRecord(answer.build) && text(answer.build.state) === 'running') {
    return { state: 'building' };
  }
  return null;
}

function readMoved(value: unknown): ExplainerMoved | null {
  if (!isRecord(value)) return null;
  const blocks = firstNumber([value.blocks, value.blocksChanged]) ?? 0;
  const result: ExplainerMoved = {
    from: number(value.from),
    to: number(value.to),
    blocks,
  };
  const commitFrom = text(value.commitFrom);
  const commitTo = text(value.commitTo);
  const publishedText = text(value.text);
  if (commitFrom) result.commitFrom = commitFrom;
  if (commitTo) result.commitTo = commitTo;
  if (publishedText) result.text = publishedText;
  return result;
}

const BASIS: ReadonlyArray<ClaimBasis> = [
  'recorded-fact',
  'mechanical-signal',
  'presenter-inference',
  'intended',
];

function readBasis(value: unknown): ClaimBasis {
  return (BASIS as readonly string[]).includes(value as string)
    ? (value as ClaimBasis)
    : 'presenter-inference';
}

function readClaimLink(value: unknown): ClaimLink | undefined {
  if (!isRecord(value)) return undefined;
  const block = text(value.block);
  if (!block) return undefined;
  const comparison = text(value.comparison);
  return {
    block,
    label: text(value.label) ?? block,
    ...(comparison ? { comparison } : {}),
  };
}

function parseClaimRow(claim: unknown): ExplanationClaim[] {
  if (!isRecord(claim)) return [];
  const id = firstText([claim.id, claim.claim_id, claim.claimId]);
  const part = text(claim.part);
  if (!id || !part) return [];
  const claimText = firstText([claim.text, claim.statement]);
  const link = readClaimLink(claim.link);
  return [
    {
      id,
      part,
      basis: readBasis(claim.basis ?? claim.classification),
      ...(claimText ? { text: claimText } : {}),
      ...(link ? { link } : {}),
    },
  ];
}

function readClaims(value: unknown): ExplanationClaim[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap(parseClaimRow);
}

function readParts(value: unknown): Array<{ id: string; title: string }> {
  if (!Array.isArray(value)) return [];
  return value.flatMap((part) => {
    if (!isRecord(part)) return [];
    const id = firstText([part.id, part.part]);
    if (!id) return [];
    return [{ id, title: text(part.title) ?? id }];
  });
}

function readLimit(value: unknown): ExplainerLimit | null {
  if (!isRecord(value)) return null;
  if (value.state === 'refused') {
    return {
      state: 'refused',
      reason: text(value.reason) ?? 'refused',
      detail: text(value.detail) ?? '',
    };
  }
  return null;
}

function readRetiredStatus(
  answer: Record<string, unknown>,
  nested: Record<string, unknown>,
): { retired: boolean; retiredAt: string | null } {
  const ansRetiredRec = isRecord(answer.retired) ? answer.retired : {};
  const nestedRetiredRec = isRecord(nested.retired) ? nested.retired : {};
  const retiredBool = Boolean(answer.retired || nested.retired);
  const retiredAt =
    firstText([answer.retiredAt, ansRetiredRec.at, nestedRetiredRec.at]) ??
    (retiredBool ? '' : null);
  return { retired: retiredAt !== null || retiredBool, retiredAt };
}

function readStateBinding(
  answer: Record<string, unknown>,
  nested: Record<string, unknown>,
): ExplainerBinding | null {
  const fallbackLabel = firstText([answer.label, nested.label]);
  return readBinding(answer.binding, fallbackLabel) ?? readBinding(nested.binding, fallbackLabel);
}

function readStateParts(answer: Record<string, unknown>): ExplainerState['parts'] {
  const partMap = isRecord(answer.partMap) ? answer.partMap : {};
  return readParts(answer.parts ?? partMap.parts);
}

function readStateClaims(answer: Record<string, unknown>): ExplainerState['claims'] {
  const claimMap = isRecord(answer.claimMap) ? answer.claimMap : {};
  return readClaims(answer.claims ?? claimMap.claims);
}

export function parseState(answer: Record<string, unknown>): ExplainerState {
  const nested = isRecord(answer.explainer) ? answer.explainer : {};
  const identity = readExplainerIdentity(answer);
  const { retired, retiredAt } = readRetiredStatus(answer, nested);
  const stale = readStale(answer);
  const categories = readCategories(answer);
  const grades = readGrades(answer, stale);
  const result: ExplainerState = {
    state: 'ok',
    binding: readStateBinding(answer, nested),
    seat: readSeat(answer.seat ?? nested.seat, identity),
    retired,
    retiredAt,
    explanation: readExplanation(answer),
    building: readBuilding(answer),
    build: parseExplainerBuild(answer.build ?? nested.build),
    stale,
    parts: readStateParts(answer),
    claims: readStateClaims(answer),
    moved: readMoved(answer.moved ?? answer.movedNotice),
    limit: readLimit(answer.limit ?? answer.refusal),
  };
  if (identity) result.explainer = identity;
  if (categories.length) result.categories = categories;
  if (grades.length) result.grades = grades;
  return result;
}

function readRevisionKind(row: Record<string, unknown>): 'global' | 'focused' {
  if (text(row.kind) === 'focused') return 'focused';
  const focus = text(row.focus)?.trim() ?? '';
  if (
    focus &&
    !focus.startsWith('the change set of ') &&
    focus !== 'explain this change set end to end'
  ) {
    return 'focused';
  }
  return 'global';
}

function attachOptionalRevisionFields(result: ExplainerRevision, row: Record<string, unknown>) {
  const focus = text(row.focus);
  const coverage = text(row.coverage);
  const bytes = number(row.bytes);
  if (focus) result.focus = focus;
  if (coverage) result.coverage = coverage;
  if (row.removed === true) result.removed = true;
  if (bytes !== null) result.bytes = bytes;
  if (Array.isArray(row.parts)) result.parts = readParts(row.parts);
  if (Array.isArray(row.claims)) result.claims = readClaims(row.claims);
  if (row.stale !== undefined) result.stale = readStale(row);
}

function parseRevisionRow(row: unknown): ExplainerRevision[] {
  if (!isRecord(row)) return [];
  const revNum = number(row.revision);
  const binding = readBinding(row.binding, text(row.label));
  if (revNum === null || !binding) return [];
  const result: ExplainerRevision = {
    revision: revNum,
    binding,
    at: firstText([row.at, row.writtenAt]) ?? '',
    kind: readRevisionKind(row),
    agreed: row.agreed === true,
    pinned: row.pinned === true,
  };
  attachOptionalRevisionFields(result, row);
  return [result];
}

export function parseRevisions(answer: Record<string, unknown>): ExplainerRevisions {
  const rows = Array.isArray(answer.revisions) ? answer.revisions : [];
  const revisions = rows.flatMap(parseRevisionRow);
  const total = isRecord(answer.total)
    ? {
        used: number(answer.total.used) ?? 0,
        limit: number(answer.total.limit) ?? 0,
      }
    : undefined;
  const detail = text(answer.detail);
  return {
    state: 'ok',
    revisions,
    pinned: number(answer.pinned),
    ...(total ? { total } : {}),
    ...(detail ? { detail } : {}),
  };
}

// ---- the calls ----

/** The badge's answer, parsed by the same readers `/state` is: no second spelling of a field. */
export function parseStaleBadge(answer: Record<string, unknown>): ExplainerStaleBadge {
  const stale = readStale(answer);
  const categories = readCategories(answer);
  const grades = readGrades(answer, stale);
  return {
    state: 'ok',
    stale,
    explanation: readExplanation(answer),
    moved: readMoved(answer.movedNotice),
    building: readBuilding(answer),
    retired: answer.retired === true,
    ...(categories.length ? { categories } : {}),
    ...(grades.length ? { grades } : {}),
  };
}

// Identical explainer reads in flight are one request (`sharedRead`): a StrictMode remount or a second
// mounted reader joins the request already asked for, and the request is cancelled on the wire once
// no mounted reader wants it. The answer object is shared, so readers treat it as immutable.

export function readExplainerState(
  target: ExplainerTarget,
  query?: ExplainerStateQuery,
  signal?: AbortSignal,
): Promise<ExplainerState> {
  const url = stateUrl(target, query);
  return sharedRead(`explainer:${url}`, async (shared) => {
    const base = stateUrl(target);
    const response = await fetch(url, { signal: shared });
    if (url !== base && response.status === 404) {
      const clone = response.clone();
      const peek: unknown = await clone.json().catch(() => null);
      if (isRecord(peek) && peek.reason === 'unmocked') {
        const fallback = await fetch(base, { signal: shared });
        return parseState(await bodyOf(fallback));
      }
    }
    return parseState(await bodyOf(response));
  }, signal);
}

/** The top bar's badge: `/stale`, read for the comparison on screen (F18). */
export function readExplainerStale(
  target: ExplainerTarget,
  query?: ExplainerStateQuery,
  signal?: AbortSignal,
): Promise<ExplainerStaleBadge> {
  const url = staleUrl(target, query);
  return sharedRead(`explainer:${url}`, async (shared) => {
    const response = await openingFetch(url, { signal: shared });
    return parseStaleBadge(await bodyOf(response));
  }, signal);
}

export function readExplainerRevisions(
  target: ExplainerTarget,
  signal?: AbortSignal,
): Promise<ExplainerRevisions> {
  const url = revisionsUrl(target);
  return sharedRead(`explainer:${url}`, async (shared) => {
    const response = await fetch(url, { signal: shared });
    return parseRevisions(await bodyOf(response));
  }, signal);
}

export async function readPlannedReview(target: ExplainerTarget): Promise<PlannedPage | null> {
  try {
    const response = await fetch(plannedUrl(target));
    if (!response.ok) return null;
    const raw: unknown = await response.json().catch(() => null);
    return readPlannedPage(raw);
  } catch {
    return null;
  }
}

/** The developer's Update press. Nothing else calls it. */
export async function updateExplainer(
  target: ExplainerTarget,
  comparison?: string,
): Promise<ExplainerUpdate> {
  const response = await fetch(leafUrl(target, '/update'), {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(comparison ? { comparison } : {}),
  });
  const answer = await bodyOf(response);
  const counts = isRecord(answer.counts)
    ? {
        ...(number(answer.counts.blocks) !== null
          ? { blocks: number(answer.counts.blocks) as number }
          : {}),
        ...(number(answer.counts.parts) !== null
          ? { parts: number(answer.counts.parts) as number }
          : {}),
        ...(number(answer.counts.grades) !== null
          ? { grades: number(answer.counts.grades) as number }
          : {}),
        ...(number(answer.counts.files) !== null
          ? { files: number(answer.counts.files) as number }
          : {}),
        ...(number(answer.counts.bytes) !== null
          ? { bytes: number(answer.counts.bytes) as number }
          : {}),
      }
    : undefined;
  return { attempt: text(answer.attempt), ...(counts ? { counts } : {}) };
}

/**
 * The developer's Cancel while a build runs (MIK-R211 v2 rules 4 and 5).
 *
 * It names the build's own request id, so the host ends that attempt and nothing else; the route
 * answers with the seat's receipt and no build record, which is why the caller re-reads `/state` to
 * show the ended build and its reason. A refusal is thrown as `ExplainerRefusal`, like every read.
 */
export async function cancelExplainerBuild(
  target: ExplainerTarget,
  requestId: string,
): Promise<ExplainerCancel> {
  const response = await fetch(cancelUrl(target), {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ requestId }),
  });
  const answer = await bodyOf(response);
  const cancel = isRecord(answer.cancel) ? answer.cancel : {};
  return {
    requestId: text(cancel.requestId) ?? requestId,
    seat: text(cancel.seat),
    turnStopped: cancel.turnStopped === true,
    requestUncertain: cancel.requestUncertain === true,
    publicationPrevented: cancel.publicationPrevented !== false,
  };
}

/**
 * The developer's selective regeneration press for a live explainer seat (MIK-R187 rule 8).
 */
export async function regenerateExplainerStale(
  target: ExplainerTarget,
  input: {
    requestId: string;
    message: string;
    stalePartIds: string[];
    staleGrades: string[];
    comparison?: string;
  },
): Promise<{ attempt: string | null }> {
  const response = await fetch(leafUrl(target, '/build'), {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      requestId: input.requestId,
      message: input.message,
      stalePartIds: input.stalePartIds,
      staleGrades: input.staleGrades,
      regeneration: true,
      ...(input.comparison ? { comparison: input.comparison } : {}),
    }),
  });
  const answer = await bodyOf(response);
  return { attempt: text(answer.attempt) };
}

/**
 * The developer's removal of old revisions (MIK-R103 v2 rule 8).
 *
 * The numbers are the selector's own list, which never contains the pinned revision: the host
 * refuses a pinned removal as well, so a mistake here cannot remove the agreed page.
 */
export async function removeExplainerRevisions(
  target: ExplainerTarget,
  revisions: number[],
): Promise<ExplainerRemoval> {
  const response = await fetch(leafUrl(target, '/remove-revisions'), {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ revisions }),
  });
  const answer = await bodyOf(response);
  return {
    state: 'ok',
    removed: Array.isArray(answer.removed)
      ? answer.removed.filter((one): one is number => typeof one === 'number')
      : revisions,
    ...(number(answer.remaining) !== null ? { remaining: number(answer.remaining) as number } : {}),
  };
}

export interface RevisionPageOptions {
  binding?: ExplainerBinding | null;
  compareSlot?: 'primary' | 'left' | 'right' | 'compare' | null;
  entrypoint?: string | null;
}

function buildSessionPayload(
  target: ExplainerTarget,
  revision: number,
  options?: RevisionPageOptions,
): Record<string, unknown> {
  const payload: Record<string, unknown> = {
    repository: target.repository,
    master: target.master,
    leaf: target.leaf,
    frame: EXPLAINER_FRAME_ID,
    revision,
  };
  if (options?.compareSlot) payload.compareSlot = options.compareSlot;
  if (options?.entrypoint) payload.entrypoint = options.entrypoint;
  const binding = options?.binding;
  if (binding) {
    if (binding.reference) payload.comparison = binding.reference;
    payload.comparisonNumber = binding.number;
    payload.comparisonOwner = binding.owner;
    payload.binding = binding.raw ?? {
      owner: binding.owner,
      number: binding.number,
      reference: binding.reference,
      label: binding.label,
    };
  }
  return payload;
}

/**
 * The channel answer as a `CanvasSession`, field by field. An answer that names no page is a
 * refusal of the page, not a session with holes: the frame would otherwise open a blank document.
 */
function readCanvasSession(body: Record<string, unknown>): CanvasSession {
  const session = text(body.session);
  const revision = number(body.revision);
  const comparison = text(body.comparison);
  const entrypoint = text(body.entrypoint);
  const sandbox = text(body.sandbox);
  const pageUrl = text(body.pageUrl);
  if (
    body.state !== 'ok' ||
    session === null ||
    revision === null ||
    comparison === null ||
    entrypoint === null ||
    typeof body.scripts !== 'boolean' ||
    sandbox === null ||
    pageUrl === null
  ) {
    throw new CanvasRefusal('unreadable-answer', 'the host answered a channel that is not a page');
  }
  return {
    state: 'ok',
    session,
    revision,
    comparison,
    entrypoint,
    scripts: body.scripts,
    sandbox,
    pageUrl,
  };
}

/**
 * One isolated-host channel for one retained revision (MIK-R104 v3; MIK-R103 v2).
 */
export async function openRevisionPage(
  target: ExplainerTarget,
  revision: number,
  options?: RevisionPageOptions,
): Promise<CanvasSession> {
  const payload = buildSessionPayload(target, revision, options);
  const response = await fetch('/api/canvas/sessions', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const parsed: unknown = await response.json().catch(() => null);
  const body = isRecord(parsed) ? parsed : null;
  if (!body) {
    throw new CanvasRefusal('no-answer', 'the host answered nothing for /api/canvas/sessions');
  }
  if (!response.ok || body.state === 'refused') {
    throw new CanvasRefusal(
      text(body.reason) ?? `http-${response.status}`,
      text(body.detail) ?? response.statusText,
    );
  }
  return readCanvasSession(body);
}

/** Close one revision's channel; a revoked channel is not an error the view reports. */
export function closeRevisionPage(session: string, why: string): void {
  void revokeSession(session, why).catch(() => undefined);
}
