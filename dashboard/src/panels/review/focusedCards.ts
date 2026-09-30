// Focused expression cards (MIK-R31): every code and test location of the selected family's members,
// grouped by the exact region it names, in the family order of MIK-R01.
//
// A card is one (path, range) on each side, never one path: two regions of one file keep two cards
// and two rationales, and several members' entries at the same path and range share one card that
// lists each member with its own rationale. Ranges come from the server's MIK-R08 definition 3
// resolution (data/reviewTrees.ts `ReviewTreeEntry`); nothing here resolves, searches or guesses a
// range. A side that did not resolve keeps its entry on a card of its own, because "the same range"
// is not a fact anyone established for it.
//
// Order (MIK-R01 rule 4): the seed invariant's entries first (the selected member, when one is
// selected), then each remaining member by ID; within a member, realizations before proofs, each by
// path and then ID. A card takes the position of its first entry.
import type { ReviewFamilyContextEntry } from '../../data/review';
import type { ReviewTreeEntry, ReviewTreeEntrySide } from '../../data/reviewTrees';

export type CardChange = ReviewTreeEntry['change'];

export interface ExpressionCard {
  key: string;
  kind: ReviewTreeEntry['kind'];
  path: string;
  change: CardChange;
  before: ReviewTreeEntrySide;
  after: ReviewTreeEntrySide;
  entries: ReviewTreeEntry[];
}

export interface CardCounts {
  cards: number;
  changed: number;
  unchanged: number;
  undetermined: number;
}

const KIND_RANK: Record<ReviewTreeEntry['kind'], number> = { realization: 0, proof: 1 };

function sidePath(entry: ReviewTreeEntry): string {
  return entry.after.recorded ? entry.after.path : entry.before.path;
}

// The region one side names: its path and its resolved lines, or its state alone.
function region(side: ReviewTreeEntrySide): string {
  if (side.state === 'resolved') return `${side.path}:${side.start_line}-${side.end_line}`;
  return `${side.path}:${side.state}`;
}

// Entries share a card only when both sides are established regions: resolved, or a file absent
// there. An unresolved or unavailable side is not a region, so its entry is keyed by its own ID.
function cardKey(entry: ReviewTreeEntry): string {
  const established = (side: ReviewTreeEntrySide) =>
    side.state === 'resolved' || side.state === 'absent';
  const own = established(entry.before) && established(entry.after) ? '' : `|${entry.id}`;
  return `${entry.kind}|${region(entry.before)}|${region(entry.after)}${own}`;
}

export function orderedEntries(entries: ReviewTreeEntry[], seed?: string): ReviewTreeEntry[] {
  return [...entries].sort(
    (left, right) =>
      Number(right.invariant_key === seed) - Number(left.invariant_key === seed) ||
      left.invariant.localeCompare(right.invariant) ||
      KIND_RANK[left.kind] - KIND_RANK[right.kind] ||
      sidePath(left).localeCompare(sidePath(right)) ||
      left.id.localeCompare(right.id),
  );
}

export function expressionCards(entries: ReviewTreeEntry[], seed?: string): ExpressionCard[] {
  const cards = new Map<string, ExpressionCard>();
  for (const entry of orderedEntries(entries, seed)) {
    const key = cardKey(entry);
    const card = cards.get(key);
    if (card) {
      card.entries.push(entry);
      continue;
    }
    cards.set(key, {
      key,
      kind: entry.kind,
      path: sidePath(entry),
      change: entry.change,
      before: entry.before,
      after: entry.after,
      entries: [entry],
    });
  }
  return [...cards.values()];
}

// An unchanged card is excluded from every changed count; an undetermined one is counted apart.
export function cardCounts(cards: ExpressionCard[]): CardCounts {
  return {
    cards: cards.length,
    changed: cards.filter((card) => card.change === 'changed').length,
    unchanged: cards.filter((card) => card.change === 'unchanged').length,
    undetermined: cards.filter((card) => card.change === 'undetermined').length,
  };
}

// What one member says on a card: the side that records the entry now (after, else before), with
// the role or facet and the rationale that side authored. `missing` is a realization whose authored
// rationale is absent -- rendered as an explicit gap, never replaced by text of the renderer's own.
export interface CardVoice {
  entry: ReviewTreeEntry;
  side: 'before' | 'after';
  role?: string;
  facet?: string;
  rationale?: string;
  missing: boolean;
  revised?: string;
}

export function cardVoice(entry: ReviewTreeEntry): CardVoice {
  const side = entry.after.recorded ? 'after' : 'before';
  const own = entry[side];
  const other = side === 'after' && entry.before.recorded ? entry.before : undefined;
  const revised =
    entry.kind === 'proof'
      ? other && other.facet !== own.facet
        ? other.facet
        : undefined
      : other && other.rationale !== own.rationale
        ? other.rationale
        : undefined;
  return {
    entry,
    side,
    role: own.role,
    facet: own.facet,
    rationale: own.rationale,
    missing: entry.kind === 'realization' && !own.rationale,
    revised,
  };
}

// The first line number of each side's excerpt, for a diff whose gutter keeps the file's numbering.
export function firstLines(card: ExpressionCard): { before: number; after: number } {
  return { before: card.before.start_line ?? 1, after: card.after.start_line ?? 1 };
}

export function rangeLabel(side: ReviewTreeEntrySide): string {
  if (side.state === 'resolved') {
    return side.start_line === side.end_line
      ? `L${side.start_line}`
      : `L${side.start_line}–${side.end_line}`;
  }
  if (side.state === 'absent') return 'no file';
  return side.state;
}

// The MIK-R03 states worth a mark on a card: every recorded side whose entry is not current.
export function notCurrent(
  card: ExpressionCard,
): { entry: string; side: 'before' | 'after'; state: string; reason?: string }[] {
  return card.entries.flatMap((entry) =>
    (['before', 'after'] as const).flatMap((side) => {
      const own = entry[side];
      return own.recorded && own.currentness && own.currentness !== 'current'
        ? [{ entry: entry.id, side, state: own.currentness, reason: own.currentness_reason }]
        : [];
    }),
  );
}

// The highlighting language of an excerpt, from its path's suffix (the ids data/review.ts's source
// expansion uses; anything else is plain text). It styles the text and decides nothing.
const SUFFIX_LANGUAGES: Record<string, string> = {
  py: 'python',
  ts: 'typescript',
  tsx: 'tsx',
  js: 'javascript',
  mjs: 'javascript',
  cjs: 'javascript',
  jsx: 'jsx',
  json: 'json',
  css: 'css',
  html: 'html',
  md: 'markdown',
};

export function languageOfPath(path: string): string {
  const suffix = /\.([A-Za-z0-9]+)$/.exec(path)?.[1]?.toLowerCase() ?? '';
  return SUFFIX_LANGUAGES[suffix] ?? 'text';
}

// How much of a family a card read covers, counted on one side so the two numbers are the same kind
// of thing: the side recording the most membership rows, its distinct loaded members against its
// own row count (never above it). `undefined` when the roster is complete (the cards cover every
// member). Review F2/R2-5: a bounded roster must never read as the whole family.
export interface CardScope {
  loaded: number;
  total: number;
}

export function cardScope(entry: ReviewFamilyContextEntry | undefined): CardScope | undefined {
  if (!entry) return undefined;
  const sides = [entry.before, entry.after].filter((side) => side.state === 'recorded');
  const complete = sides.every(
    (side) =>
      side.page?.complete !== false &&
      side.members.length === side.members_total &&
      side.members.every((member) => member.state === 'recorded'),
  );
  if (complete || sides.length === 0) return undefined;
  const widest = sides.reduce((most, side) =>
    side.members_total > most.members_total ? side : most,
  );
  const loaded = new Set(widest.members.map((member) => member.invariant_id ?? member.member_id));
  return { loaded: Math.min(loaded.size, widest.members_total), total: widest.members_total };
}
