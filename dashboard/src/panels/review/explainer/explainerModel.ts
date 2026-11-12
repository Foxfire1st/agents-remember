/**
 * What the Explanation view may say, and what it must never imply (MIK-R106 v2 rule 6).
 *
 * Pure functions only: the view's decisions are testable without a browser, and the distinctions the
 * packets insist on stay in one place instead of in JSX branches:
 *   * a saved page opens with no model, and a page is never generated on the reader's behalf;
 *   * the trusted stale list is the record -- the marks inside a frame are advisory (MIK-R104 v3
 *     rules 1 and 3);
 *   * the agreed revision is pinned, and the one control that removes revisions never offers it
 *     (MIK-R103 v2 rule 8; MIK-R189 rule 9);
 *   * only the task total offers that control: the 16 MiB per-revision ceiling refuses one revision,
 *     and removing others does not help there.
 */

import type {
  ExplainerBinding,
  ExplainerRevision,
  ExplainerState,
  RefusalInfo,
  StalePart,
  StaleRegeneration,
} from './explainerApi';

export type ExplanationPhase = 'reading' | 'unavailable' | 'none' | 'building' | 'saved';

/**
 * What the staleness model reads: a full `/state` answer, or the top bar's cheap `/stale` answer
 * (`ExplainerStaleBadge`), which carries exactly these fields and no seat -- besides the label's
 * two marks, retired and a build in progress.
 */
export type StaleFacts = Pick<ExplainerState, 'stale' | 'moved'> &
  Partial<
    Pick<
      ExplainerState,
      'categories' | 'grades' | 'explanation' | 'binding' | 'building' | 'retired'
    >
  >;

/** `#8` for a recorded comparison; `planned`, never `#0`, for a planned one (MIK-R103 v2 item 3). */
export function bindingLabel(binding: ExplainerBinding | null | undefined): string {
  if (!binding) return 'no binding';
  if (binding.owner === 'planned') return 'planned';
  return binding.label ?? `#${binding.number}`;
}

/** A revision's time as the view shows it: `14:36`, in the reader's own clock. */
export function clockOf(at: string | null | undefined): string {
  if (!at) return '';
  const date = new Date(at);
  if (Number.isNaN(date.getTime())) return '';
  return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
}

export function kindLabel(kind: ExplainerRevision['kind']): string {
  return kind === 'focused' ? 'focused' : 'global';
}

/** The agreement the architect wrote at the go, in either spelling the host may answer with. */
export function agreedRevision(
  rows: readonly ExplainerRevision[],
  pinned?: number | null,
): ExplainerRevision | null {
  return rows.find((row) => row.agreed) ?? rows.find((row) => row.revision === pinned) ?? null;
}

/** Whether one revision is the agreed, pinned one (MIK-R189 rule 9). */
export function isPinned(row: ExplainerRevision, pinned?: number | null): boolean {
  return row.agreed || row.pinned || (pinned !== undefined && pinned !== null && row.revision === pinned);
}

/**
 * The revisions the removal control may offer: the unpinned ones, and never the agreed one.
 *
 * A removed revision keeps its unavailable receipt and is not offered again.
 */
export function removableRevisions(
  rows: readonly ExplainerRevision[],
  pinned?: number | null,
): ExplainerRevision[] {
  return rows.filter((row) => !row.removed && !isPinned(row, pinned));
}

export function phaseOf(state: ExplainerState | null | undefined): ExplanationPhase {
  if (!state) return 'reading';
  if (state.state !== 'ok') return 'unavailable';
  if (state.building && state.building.state !== 'idle') return 'building';
  if (!state.explanation || state.explanation.revision === null) return 'none';
  return 'saved';
}

/** Whether explainer work exists for this leaf: a published page or a grade (MIK-R187 rule 9). */
export function hasWork(state: StaleFacts | null | undefined): boolean {
  if (!state) return false;
  if (typeof state.explanation?.revision === 'number') return true;
  return Boolean(state.grades?.length || state.categories?.length);
}

/** The trusted list's own contents: the parts, and the true total while more are stale than listed. */
export function staleParts(state: StaleFacts | null | undefined): {
  parts: StalePart[];
  total: number;
  unavailable: string | null;
} {
  const stale = state?.stale;
  if (!stale) return { parts: [], total: 0, unavailable: null };
  const parts = stale.parts ?? [];
  return {
    parts,
    total: stale.total ?? parts.length,
    unavailable: stale.unavailable ?? null,
  };
}

/** The references of blocks whose grades are stale against the target comparison (MIK-R187 rule 8). */
export function staleGradesOf(state: StaleFacts | null | undefined): string[] {
  const fromStale = state?.stale?.staleGrades ?? [];
  if (fromStale.length > 0) return [...new Set(fromStale)];
  const fromGrades = (state?.grades ?? []).filter((g) => g.stale).map((g) => g.reference);
  return [...new Set(fromGrades)];
}

const plural = (n: number, word: string) => `${n} ${n === 1 ? word : `${word}s`}`;

function buildRegenerationMessage(
  revision: number,
  target: string,
  parts: readonly StalePart[],
  grades: readonly string[],
): string {
  const partLines = parts.length
    ? [
        `The ${plural(parts.length, 'stale part')}:`,
        ...parts.map((part) => `- ${part.title} (${part.id}): ${part.reason}`),
      ]
    : ['No part is stale: every claim basis still resolves against this comparison.'];
  const gradeLines = grades.length
    ? [`The ${plural(grades.length, 'stale grade')}:`, ...grades.map((ref) => `- ${ref}`)]
    : ['No grade is stale: every graded block still carries its fingerprint.'];
  return [
    `Regenerate revision ${revision} against ${target}.`,
    ...partLines,
    ...gradeLines,
    'Nothing regenerates by itself: this is the message your press sends, and only the stale parts and grades are redone.',
  ].join('\n');
}

function regenerationRevisionOf(state: StaleFacts, shown?: number | null): number {
  return shown ?? state.explanation?.revision ?? 1;
}

function regenerationTargetOf(state: StaleFacts): string {
  return state.binding?.reference ?? bindingLabel(state.binding);
}

function stalePartsAndCount(state: StaleFacts): { parts: StalePart[]; partCount: number } {
  const stale = state.stale;
  if (!stale) return { parts: [], partCount: 0 };
  return { parts: stale.parts, partCount: stale.total ?? stale.parts.length };
}

/**
 * The selective regeneration label and message for stale parts and grades (MIK-R187 rule 8).
 */
export function staleRegenerationOf(
  state: StaleFacts | null | undefined,
  shown?: number | null,
): StaleRegeneration | null {
  if (!state || state.stale?.unavailable) return null;
  if (state.stale?.regeneration) return state.stale.regeneration;
  const { parts, partCount } = stalePartsAndCount(state);
  const grades = staleGradesOf(state);
  const gradeCount = grades.length;
  if (partCount + gradeCount === 0) return null;
  const label = `Regenerate stale (${plural(partCount, 'part')} · ${plural(gradeCount, 'grade')})`;
  const message = buildRegenerationMessage(
    regenerationRevisionOf(state, shown),
    regenerationTargetOf(state),
    parts,
    grades,
  );
  return { label, message, partCount, gradeCount };
}

/** The task-total refusal's name, as MIK-R103 v2 rule 8 writes it. */
export const TASK_TOTAL_REFUSAL = 'frame-store-full';

/**
 * Whether a refusal offers the removal control: only the task total does.
 *
 * The 16 MiB per-revision ceiling refuses that one revision by name and removing others does not
 * help, so the control is not offered for it (MIK-R103 v2 rule 8).
 */
export function removalOffered(refusal: RefusalInfo | null | undefined): boolean {
  return refusal?.reason === TASK_TOTAL_REFUSAL;
}

/** The refusal as one line: its name first, then the host's detail. */
export function refusalLine(refusal: RefusalInfo): string {
  return refusal.detail ? `${refusal.reason}: ${refusal.detail}` : refusal.reason;
}

/** The newer comparison's label for the badge: `#8 → #9`, or the commit pair for a planned one. */
export function movedLine(moved: NonNullable<ExplainerState['moved']>): string {
  const from = moved.from === null ? 'no number' : `#${moved.from}`;
  const to = moved.to === null ? 'a new state' : `#${moved.to}`;
  if (moved.commitFrom && moved.commitTo) {
    return `master moved: ${moved.commitFrom.slice(0, 8)} → ${moved.commitTo.slice(0, 8)}`;
  }
  return `Task moved: ${from} → ${to} · ${moved.blocks} ${moved.blocks === 1 ? 'block' : 'blocks'} changed`;
}

/**
 * What the top bar's badge says when it is opened (F18): staleness is reported once, in the top
 * line, and the badge names what is stale and why instead of the page drawing a second copy.
 *
 * Every field comes from the host's own answer -- `stale.parts`/`stale.total` for the parts, the
 * grades for their count, `moved` for the newer comparison -- so the badge invents nothing.
 */
export interface StaleBrief {
  /** The parts the host named stale, each with the reason its basis changed. */
  parts: StalePart[];
  /** The true count: the host may name more stale parts than it lists. */
  total: number;
  /** Why staleness could not be computed; the badge then claims nothing about a part. */
  unavailable: string | null;
  /** How many block grades rested on an earlier comparison. */
  gradeCount: number;
  /** The newer comparison the leaf moved to, in the page's own words; null when it has not moved. */
  newerComparison: string | null;
  /** The selective regeneration the badge's refresh control sends (MIK-R187 rule 8). */
  regeneration: StaleRegeneration | null;
  /** Whether there is anything at all to report: nothing is drawn when there is not. */
  present: boolean;
}

/** The newer comparison's own line, published as the watcher wrote it when it carries text. */
export function newerComparisonOf(state: StaleFacts | null | undefined): string | null {
  const moved = state?.moved;
  if (!moved) return null;
  return moved.text ?? movedLine(moved);
}

/**
 * The badge's whole content, from one answer.
 *
 * `revisionExists` is the caller's fact -- a retained revision, or one named by the state -- because
 * a host that cannot compute staleness says so with the same sentence before any revision exists
 * ("the leaf has no retained revision"), and a fresh leaf has nothing to report. With a revision in
 * hand the host's sentence is the record, and the badge carries it (MIK-R104 v3 rule 3).
 */
export function staleBriefOf(
  state: StaleFacts | null | undefined,
  shown?: number | null,
  revisionExists = false,
): StaleBrief {
  const { parts, total, unavailable } = staleParts(state);
  const gradeCount = staleGradesOf(state).length;
  const newerComparison = newerComparisonOf(state);
  const regeneration = staleRegenerationOf(state, shown);
  return {
    parts,
    total,
    unavailable,
    gradeCount,
    newerComparison,
    regeneration,
    present:
      total > 0 ||
      parts.length > 0 ||
      gradeCount > 0 ||
      newerComparison !== null ||
      (revisionExists && unavailable !== null),
  };
}

/** The first-request items a cold regeneration asks for: the stale parts, then the stale grades. */
export function coldRegenerateItems(
  state: StaleFacts | null | undefined,
): Array<{ id: string; title: string; reason: string }> {
  const { parts } = staleParts(state);
  const partItems = parts.map((p) => ({ id: p.id, title: p.title, reason: p.reason }));
  const seen = new Set(partItems.map((p) => p.id));
  const gradeItems = staleGradesOf(state)
    .filter((ref) => !seen.has(ref))
    .map((ref) => ({ id: ref, title: `Grade · ${ref}`, reason: 'stale grade' }));
  return [...partItems, ...gradeItems];
}

/** Two revisions to show side by side: the one on screen and the latest other one (MIK-R106 v2 rule 6). */
export function defaultComparePair(
  rows: readonly ExplainerRevision[],
  shown: number | null,
): { left: number; right: number } | null {
  const retained = rows.filter((row) => !row.removed);
  if (retained.length < 2) return null;
  const right = shown ?? retained[retained.length - 1].revision;
  const left = [...retained].reverse().find((row) => row.revision !== right)?.revision;
  if (left === undefined) return null;
  return { left, right };
}

/** Global explanations precede focused ones; within each kind, rows keep their order (MIK-R106 v2 rule 6). */
export function orderRevisions(rows: readonly ExplainerRevision[]): ExplainerRevision[] {
  const globalRows = rows.filter((row) => row.kind !== 'focused');
  const focusedRows = rows.filter((row) => row.kind === 'focused');
  return [...globalRows, ...focusedRows];
}

/**
 * The revision shown by default: the latest retained global revision when one exists, else the
 * latest retained focused revision or the state's own explanation revision (MIK-R106 v2 rule 6).
 */
export function defaultShownRevision(
  rows: readonly ExplainerRevision[],
  state: ExplainerState | null | undefined,
): number | null {
  const retained = rows.filter((row) => !row.removed);
  const stateRev = state?.explanation?.revision ?? null;
  const globalRows = retained.filter((row) => row.kind !== 'focused');
  if (globalRows.length > 0) {
    if (stateRev !== null && globalRows.some((row) => row.revision === stateRev)) {
      return stateRev;
    }
    return Math.max(...globalRows.map((row) => row.revision));
  }
  if (retained.length > 0) {
    if (stateRev !== null && retained.some((row) => row.revision === stateRev)) {
      return stateRev;
    }
    return Math.max(...retained.map((row) => row.revision));
  }
  return stateRev;
}

/**
 * The built page's standfirst: who wrote it, when, at which revision and binding (MIK-R106 v2 rule 6).
 *
 * Uses the selected revision row's own binding and timestamp when present, rather than the
 * environment's current binding.
 */
function rowKindSuffix(row: ExplainerRevision | undefined): string {
  if (!row) return '';
  const focus = row.focus ? ` (${row.focus})` : '';
  return ` · ${kindLabel(row.kind)}${focus}`;
}

/** The shown revision's own interaction coverage, from its row or the saved explanation (MIK-R211 v5). */
function standfirstCoverage(
  row: ExplainerRevision | undefined,
  state: ExplainerState | null | undefined,
  shown: number,
): string {
  const saved = state?.explanation?.revision === shown ? state.explanation.coverage : undefined;
  const coverage = row ? row.coverage : saved;
  return coverage ? ` · interactions checked: ${coverage}` : '';
}

function standfirstAt(
  row: ExplainerRevision | undefined,
  state: ExplainerState | null | undefined,
): string {
  return clockOf(row ? row.at : (state?.explanation?.at ?? null));
}

function standfirstBinding(
  row: ExplainerRevision | undefined,
  state: ExplainerState | null | undefined,
) {
  if (row) return row.binding;
  return state?.explanation?.binding ?? state?.binding ?? null;
}

export function standfirstText(
  state: ExplainerState | null | undefined,
  shown: number,
  rows: readonly ExplainerRevision[] = [],
): string {
  const row = rows.find((entry) => entry.revision === shown);
  const at = standfirstAt(row, state);
  const bound = bindingLabel(standfirstBinding(row, state));
  const retired = state?.retired ? ' · explainer retired' : '';
  const coverage = standfirstCoverage(row, state, shown);
  return ` · ${at} · revision ${shown}${coverage} · bound to ${bound}${rowKindSuffix(row)}${retired}`;
}
