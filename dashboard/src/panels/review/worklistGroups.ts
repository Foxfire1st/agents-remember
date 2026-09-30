// The leaf's MIK-R08 worklist as the reviewer reads it (MIK-R25 rule 3, MIK-R11 rule 7, MIK-R10).
//
// Grouping only: every item is shown as the worklist recorded it, with the history rows about the
// subject it answers to, and nothing here decides whether a row answers an item.
//
//   * knowledge items (invariant and family kinds) carry MIK-R11's `planning` mark;
//   * `planned_untouched` items are the declared effects no row delivered;
//   * `unexplained_hunk` / `unexplained_file` items (MIK-R10) can be numerous (160 on one real leaf),
//     so they are grouped by file and then by coverage state. The unexplained-changes lane
//     (MIK-R32) owns their disposition; this structure only lists them.
//   * every other kind (onboarding traces, route conditions, ...) is listed by kind.
import type {
  ReviewWorklistChange,
  ReviewWorklistHistoryRow,
  ReviewWorklistItem,
  ReviewWorklistView,
} from '../../data/reviewTrees';

export const UNEXPLAINED_KINDS = new Set(['unexplained_hunk', 'unexplained_file']);
const PLANNED_UNTOUCHED = 'planned_untouched';

export interface WorklistEntry {
  item: ReviewWorklistItem;
  rows: ReviewWorklistHistoryRow[];
}

export interface UnexplainedGroup {
  path: string;
  coverage: { state: string; entries: WorklistEntry[] }[];
  count: number;
}

export interface WorklistGroups {
  knowledge: WorklistEntry[];
  plannedUntouched: WorklistEntry[];
  unexplained: UnexplainedGroup[];
  other: { kind: string; entries: WorklistEntry[] }[];
}

// The subjects a row about this item may carry: its own, and the one its facts name for its row.
export function rowSubjects(item: ReviewWorklistItem): string[] {
  const row = item.facts.row;
  return typeof row === 'string' && row !== item.subject ? [item.subject, row] : [item.subject];
}

function entryOf(item: ReviewWorklistItem, rows: ReviewWorklistHistoryRow[]): WorklistEntry {
  const subjects = new Set(rowSubjects(item));
  return { item, rows: rows.filter((row) => subjects.has(row.subject)) };
}

function coverageState(item: ReviewWorklistItem): string {
  const coverage = item.facts.coverage;
  if (coverage && typeof coverage === 'object' && 'state' in coverage)
    return String((coverage as { state: unknown }).state);
  return 'coverage not recorded';
}

function unexplainedGroups(entries: WorklistEntry[]): UnexplainedGroup[] {
  const byPath = new Map<string, Map<string, WorklistEntry[]>>();
  for (const entry of entries) {
    const path = typeof entry.item.facts.path === 'string' ? entry.item.facts.path : '(no path)';
    const states = byPath.get(path) ?? new Map<string, WorklistEntry[]>();
    const state = coverageState(entry.item);
    states.set(state, [...(states.get(state) ?? []), entry]);
    byPath.set(path, states);
  }
  return [...byPath.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([path, states]) => ({
      path,
      coverage: [...states.entries()]
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([state, grouped]) => ({ state, entries: grouped })),
      count: [...states.values()].reduce((total, grouped) => total + grouped.length, 0),
    }));
}

export function worklistGroups(worklist: ReviewWorklistView): WorklistGroups {
  const entries = worklist.items.map((item) => entryOf(item, worklist.history_rows));
  const knowledge = entries.filter((entry) => entry.item.planning !== undefined);
  const plannedUntouched = entries.filter((entry) => entry.item.kind === PLANNED_UNTOUCHED);
  const unexplained = entries.filter((entry) => UNEXPLAINED_KINDS.has(entry.item.kind));
  const rest = entries.filter(
    (entry) =>
      entry.item.planning === undefined &&
      entry.item.kind !== PLANNED_UNTOUCHED &&
      !UNEXPLAINED_KINDS.has(entry.item.kind),
  );
  const kinds = [...new Set(rest.map((entry) => entry.item.kind))].sort();
  return {
    knowledge,
    plannedUntouched,
    unexplained: unexplainedGroups(unexplained),
    other: kinds.map((kind) => ({
      kind,
      entries: rest.filter((entry) => entry.item.kind === kind),
    })),
  };
}

// MIK-R11's mark per invariant or family subject, for the cards: "touched · planned".
export function planningMarks(worklist: ReviewWorklistView | undefined): Map<string, string> {
  const marks = new Map<string, string>();
  for (const item of worklist?.items ?? []) {
    if (item.planning === undefined) continue;
    marks.set(item.subject, `${item.kind.replace(/_/g, ' ')} · ${item.planning}`);
  }
  return marks;
}

// Per changed file, how many of its hunks the gate linked to recorded knowledge.
export function hunkLinkage(change: ReviewWorklistChange): {
  hunks: number;
  linked: number;
  fileLinked?: boolean;
} {
  const hunks = change.hunks ?? [];
  return {
    hunks: hunks.length,
    linked: hunks.filter((hunk) => hunk.linked).length,
    fileLinked: change.file_level?.linked,
  };
}
