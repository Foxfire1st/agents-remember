// The review page's cards (MIK-R31; MIK-R89 v2 W1): one card-read entry is one card, drawn by
// ExpressionCards.tsx `ReviewBlockCard` from the change-set model's location (changeSetOrder.ts).
// Ranges come from the server's MIK-R08 definition 3 resolution (data/reviewTrees.ts
// `ReviewTreeEntry`); nothing here resolves, searches or guesses a range. These are the card's
// readers: who speaks on it, its first lines, its range label, its sides that are not current and
// the highlighting language of its path.
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

// The text an excerpt draws: the newline that ends its range's last line starts no line of its own,
// so a card labelled L1697–1817 draws exactly those 121 rows and no empty row 1818 (R89 evidence 8).
export function drawnText(excerpt: string): string {
  return excerpt.endsWith('\n') ? excerpt.slice(0, -1) : excerpt;
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
