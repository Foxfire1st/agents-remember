import type { ReviewEntry, ReviewFamilyContextEntry, ReviewOutline } from '../../data/review';
import type { InvariantCurrentnessState, ReviewCodeChange, ReviewEntryMemory, ReviewEntryMemoryState, ReviewTreeComparison, ReviewTreeEntry, ReviewTreesResult, ReviewSubjectSummary } from '../../data/reviewTrees';
import type { BlockIdentity } from './blockIdentity';
import type { OutlineRow } from './changeTriage';
import type { ExplainerCategory, ExplainerGrade } from './explainer/explainerApi';
import type { CardChange, ExpressionCard } from './focusedCards';
import type { ReviewSelection, ReviewSort } from './reviewSelection';
import type { TreeView } from './triageViewPreference';

// ---- types (stable names: the reading column codes against them) ----

// Read 5, the cards of the shown subjects, merged over its chunks.
export type ChangeSetCards =
  | { state: 'reading' }
  | { state: 'unavailable'; reason: string }
  | {
      state: 'ready';
      entries: readonly ReviewTreeEntry[];
      comparison: ReviewTreeComparison;
      // The card read's memory values state (`entry_memory`); absent when the answer carries none.
      memory?: ReviewEntryMemoryState;
      // Per-area reads distinguish an unread invariant from a loaded invariant with no locations.
      // Absent on the complete comparison reads.
      coverage?: {
        loaded: readonly string[];
        pending: readonly string[];
        failures: Readonly<Record<string, string>>;
      };
    };

export interface ChangeSetInputs {
  // Read 3: the outline, and the catalogue for a comparison without one (MIK-R86 v1 rule 11).
  outline: ReviewOutline | undefined;
  catalogue: readonly ReviewEntry[];
  // Read 4's code facts by `invariant_key` (codeFacts.ts `codeFacts`): `null` when read 4 answered
  // without them, `undefined` when it was not read (the walked families' own facts then apply).
  code?: ReadonlyMap<string, ReviewCodeChange> | null;
  // Read 4's leaf-wide currentness of the comparison shown.
  currentness?: ReviewTreesResult['currentness'];
  // The complete metadata-only location inventory from read 4, bound to this comparison.
  inventory?: readonly ReviewTreeEntry[];
  overview?: ReviewTreesResult['overview'];
  comparison?: ReviewTreeComparison;
  inventoryProblem?: string;
  cards: ChangeSetCards;
  // The explainer's judgment categories and block grades (MIK-R187 rules 2–6).
  categories?: readonly ExplainerCategory[];
  grades?: readonly ExplainerGrade[];
}

export interface LocationGradeEntry {
  category: string;
  grade: string;
  reason: string;
  stale: boolean;
  staleReason?: string | null;
}

export interface LocationJudgment {
  chipLabel: string;
  activeCategory: string | null;
  activeGrade: string | null;
  scaleRank: number | null;
  stale: boolean;
  grades: LocationGradeEntry[];
  hoverText: string;
}

// What keeps a row shown in Changed beside its real change (MIK-R86 v1 rule 5).
export interface ChangeSetKeep {
  selection: ReviewSelection | null;
  // The walked tree (MIK-R39): walked families stay, a kept one with its `kept` tag.
  walked?: { entries: readonly ReviewFamilyContextEntry[]; kept: ReadonlyMap<string, string> };
}

export type LocationRole =
  | 'primary-authority'
  | 'enforcement'
  | 'propagation-persistence'
  | 'support'
  | 'presentation'
  | 'unclassified'
  | 'proof';

// A location's MIK-R03 currentness: the least current side that records it.
export type EntryCurrentness = 'stale' | 'unverifiable' | 'current' | 'not measured';
export type SubjectCurrentness = InvariantCurrentnessState | 'not measured';

export interface LocationNode {
  // blockKey(identity): one block, one key (URL selection, j/k, ⊕, DOM ids, React keys).
  key: string;
  identity: BlockIdentity;
  entry: ReviewTreeEntry;
  // The card that draws the entry, for the existing card renderer: the entry alone (W1), or every
  // entry of its shared region, the drawing location's first.
  card: ExpressionCard;
  // The keys of the locations this one draws as one row and one card: its own, then those of the
  // entries at its region; empty when the first location of its region draws it.
  draws: string[];
  // The subject invariant's selector id (`invariant_key`).
  subject: string;
  change: CardChange;
  role: LocationRole;
  // The short role word of the row ("enforce"; mockup app.js:24).
  roleLabel: string;
  // The identity side's path, range label ("L12–40", or its state) and span in lines (0 without a
  // range).
  path: string;
  range: string;
  span: number;
  symbol?: string;
  // W10: "+a −r" from a line diff of the two excerpts (a side with no file has no lines), else the
  // range.
  lines: string;
  currentness: EntryCurrentness;
  memory?: ReviewEntryMemory;
  // The tree row's one reason (MIK-R86 v2 rule 3).
  reason: string;
  // The explainer's judgment on this block, when graded (MIK-R187 rules 5 and 6).
  judgment?: LocationJudgment;
  // Present on the first ungraded changed location of a subject under a judgment sort (MIK-R187 rule 6).
  ungradedGroupCount?: number;
}

export interface SubjectNode {
  // The occurrence: `<family selector id or ->/<invariant selector id>`.
  key: string;
  // The outline row: kind 'invariant', id, selectorId, title, badge, meaning, familyId.
  row: OutlineRow;
  // The invariant's first place in display order: the reading column renders it there, and only
  // there do its locations, groups and blocks appear.
  first: boolean;
  // The recorded effect labels (MIK-R43) when its meaning has steps.
  effects: string[];
  cards: ChangeSetCards['state'] | 'unread';
  // Counts and locations may be known from the inventory before code details are fetched.
  locationsKnown: boolean;
  // Complete summary knowledge is independent of whether location rows have loaded.
  counts?: ReviewSubjectSummary;
  changed: LocationNode[];
  unchanged: LocationNode[];
  // The not-comparable group: entries whose change is `undetermined`.
  notComparable: LocationNode[];
  currentness: SubjectCurrentness;
  reason: string;
  // Under a judgment sort, the ungraded changed locations and their heading label `Ungraded (n)`.
  ungradedChanged?: LocationNode[];
  ungradedLabel?: string;
}

export interface FamilyHeading {
  row: OutlineRow;
  reason: string;
  // The subject the family was last read for, when the walk keeps it (MIK-R39).
  kept?: string;
}

export interface SubjectGroup {
  key: string;
  // Undefined: "Without a family".
  family?: FamilyHeading;
  subjects: SubjectNode[];
}

export interface SortOption {
  sort: ReviewSort;
  label: string;
  hint: string;
  // A memory sort may be requested before its values are loaded; the applied sort stays Priority.
  available: boolean;
  reason?: string;
}

export interface ChangeSetCounts {
  // Outline invariants whose own change is added, removed or changed; undefined without an outline.
  invariantsChanged: number | undefined;
  // W11: the distinct effects of the changed invariants, in Priority order.
  effects: string[];
  // Over the shown subjects, from read 5; undefined until it answered.
  changedLocations: number | undefined;
  changedFiles: number | undefined;
  // Unchanged plus not comparable locations of the shown subjects.
  folded: number | undefined;
  subjects: number;
}

export interface ChangeSetModel {
  // The sort applied: a memory sort falls back to Priority while its values are unavailable.
  sort: ReviewSort;
  // The stored choice.
  requested: ReviewSort;
  // The view applied: All without an outline (MIK-R86 v1 rule 11).
  view: TreeView;
  outlined: boolean;
  cards: ChangeSetCards['state'];
  locations: ChangeSetCards['state'];
  groups: SubjectGroup[];
  subjects: SubjectNode[];
  blocks: LocationNode[];
  shownInvariantIds: string[];
  sorts: SortOption[];
  counts: ChangeSetCounts;
  // Changed view of an outline in which nothing changed: "This leaf changed no recorded meaning."
  noChange: boolean;
}

// A subject's two groups: its unchanged locations and its not comparable (`undetermined`) ones.
export type FoldedGroup = 'unchanged' | 'undetermined';

export interface FilteredSubject {
  node: SubjectNode;
  // The location rows the tree draws under the subject row: one per drawn location (`draws`).
  locations: LocationNode[];
  // The group rows drawn (only where a count is above 0): both, and while filtering only a group
  // holding a location the filter matches.
  groups: FoldedGroup[];
}

export interface FilteredGroup {
  group: SubjectGroup;
  subjects: FilteredSubject[];
}

export interface TreeFilterResult {
  query: string;
  groups: FilteredGroup[];
  // A query is set and nothing matches: "Nothing matches “q”."
  empty: boolean;
}

