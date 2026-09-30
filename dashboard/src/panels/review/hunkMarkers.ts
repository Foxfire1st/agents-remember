// Per-hunk intent markers (MIK-R34): what each hunk of a changed text file names, and where its mark
// sits in a drawn diff.
//
// Nothing here classifies. Every hunk, class, link and family occurrence is the per-file response of
// the one classification owner (MIK-R32, data/reviewLane.ts); these functions only group what it
// names and place each hunk's mark on the side line numbers it names. The mark's line never comes from
// the renderer's own change regions, from context lines, or from any name or text in the file, so two
// owner hunks drawn in one displayed region keep two marks.
import type {
  LaneMembershipState,
  LaneSideName,
  ReviewFileClassification,
  ReviewLaneHunk,
  ReviewLaneLink,
  ReviewLaneSpan,
} from '../../data/reviewLane';

export type MarkTone = 'linked' | 'unexplained' | 'attribution_unknown';

// The tree position activating an occurrence selects: the invariant, at its member row of the family
// when one is recorded; without a family, the invariant's own position. The membership state travels
// with it, so an unknown membership is shown as `Attribution unknown` with its reason at the target --
// never as the `No recorded family` a confirmed absence reads.
export interface MarkTarget {
  invariant: string;
  invariantKey: string;
  familyKey?: string;
  memberRevisionKey?: string;
  family?: string;
  state: LaneMembershipState;
  // Why the membership is not established (`membership_unknown` only).
  reason?: string;
}

// One recorded family occurrence of an intersecting revision, on the side(s) that record it.
export interface MarkOccurrence {
  key: string;
  family?: string;
  familyRevision?: number;
  state: LaneMembershipState;
  revision?: number;
  sides: LaneSideName[];
  target: MarkTarget;
}

// One intersecting invariant, through realization entries or through proof entries (a test): the two
// are listed apart and never counted together.
export interface MarkEntry {
  key: string;
  kind: 'realization' | 'proof';
  invariant: string;
  invariantKey: string;
  facets: string[];
  entryIds: string[];
  revisions: number[];
  occurrences: MarkOccurrence[];
}

export interface MarkUnknown {
  side: LaneSideName;
  detail: string;
}

export interface HunkMark {
  key: string;
  before: ReviewLaneSpan;
  after: ReviewLaneSpan;
  tone: MarkTone;
  // What the gutter shows: the one entry's short label, or a compact count of several.
  label: string;
  // What a phone-width gutter shows instead (the full label stays the mark's accessible name): the
  // count of intents, `t` for tests, `?` for unknown lines; `!` unexplained; `?` attribution unknown.
  compact: string;
  realizations: MarkEntry[];
  proofs: MarkEntry[];
  // Changed lines whose attribution is unknown, per side: the owner's reasons, and on a linked hunk
  // the lines it changes on a side whose knowledge could not be read.
  unknown: MarkUnknown[];
}

// A file either carries one file-level mark (confirmed unregistered, or neither side's knowledge
// readable) or one mark per owner hunk.
export type FileMarks =
  | { kind: 'file'; tone: 'unexplained' | 'attribution_unknown'; detail: string }
  | { kind: 'hunks'; hunks: HunkMark[] };

export function hunkKey(hunk: { before: ReviewLaneSpan; after: ReviewLaneSpan }): string {
  return `${hunk.before.start}:${hunk.before.count}:${hunk.after.start}:${hunk.after.count}`;
}

export function fileMarks(file: ReviewFileClassification): FileMarks {
  if (file.bucket === 'unexplained')
    return { kind: 'file', tone: 'unexplained', detail: file.reason };
  const unread = file.sides.filter((side) => side.knowledge === 'unavailable');
  if (unread.length === file.sides.length)
    return {
      kind: 'file',
      tone: 'attribution_unknown',
      detail: unread.map((side) => `${side.side}: ${side.detail ?? 'unavailable'}`).join('; '),
    };
  return { kind: 'hunks', hunks: file.hunks.map((hunk) => hunkMark(file, hunk)) };
}

export function hunkMark(file: ReviewFileClassification, hunk: ReviewLaneHunk): HunkMark {
  const realizations = markEntries(
    file,
    hunk.links.filter((link) => link.kind !== 'proof'),
  );
  const proofs = markEntries(
    file,
    hunk.links.filter((link) => link.kind === 'proof'),
  );
  const unknown = [
    ...hunk.unknown.map((one) => ({ side: one.side, detail: one.detail })),
    ...unreadChangedSides(file, hunk),
  ];
  const mark = {
    key: hunkKey(hunk),
    before: hunk.before,
    after: hunk.after,
    tone: hunk.classification,
    realizations,
    proofs,
    unknown,
  };
  return { ...mark, label: markLabel(mark), compact: compactLabel(mark) };
}

// A linked hunk that also changes lines on a side whose knowledge could not be read: those lines are
// of unknown attribution (the owner links the hunk through the readable side and names the unread
// side's availability on the file; it states no reason on a linked hunk).
function unreadChangedSides(file: ReviewFileClassification, hunk: ReviewLaneHunk): MarkUnknown[] {
  if (hunk.classification !== 'linked') return [];
  return file.sides
    .filter((side) => side.knowledge === 'unavailable' && hunk[side.side].count > 0)
    .map((side) => ({
      side: side.side,
      detail: `the ${side.side} knowledge is unavailable: ${side.detail ?? 'not read'}`,
    }));
}

// The links of one kind, one entry per invariant, in the owner's order.
function markEntries(file: ReviewFileClassification, links: ReviewLaneLink[]): MarkEntry[] {
  const entries = new Map<string, MarkEntry>();
  for (const link of links) {
    const key = `${link.kind}:${link.invariant_key}`;
    const entry = entries.get(key) ?? {
      key,
      kind: link.kind,
      invariant: link.invariant,
      invariantKey: link.invariant_key,
      facets: [],
      entryIds: [],
      revisions: [],
      occurrences: [],
    };
    entries.set(key, entry);
    addOnce(entry.entryIds, link.id);
    if (link.facet) addOnce(entry.facets, link.facet);
    if (link.invariant_revision !== undefined) addOnce(entry.revisions, link.invariant_revision);
    for (const family of link.families) addOccurrence(entry, link, family, file);
  }
  return [...entries.values()];
}

function addOnce<T>(list: T[], value: T): void {
  if (!list.includes(value)) list.push(value);
}

type Occurrence = ReviewLaneLink['families'][number];

function addOccurrence(
  entry: MarkEntry,
  link: ReviewLaneLink,
  family: Occurrence,
  file: ReviewFileClassification,
): void {
  const key = `${family.state}:${family.family_key ?? ''}:${link.invariant_revision_key ?? ''}`;
  const known = entry.occurrences.find((one) => one.key === key);
  if (known) {
    addOnce(known.sides, link.side);
    return;
  }
  entry.occurrences.push({
    key,
    family: family.family,
    familyRevision: family.family_revision,
    state: family.state,
    revision: link.invariant_revision,
    sides: [link.side],
    target: {
      invariant: link.invariant,
      invariantKey: link.invariant_key,
      familyKey: family.family_key,
      memberRevisionKey: family.family_key ? link.invariant_revision_key : undefined,
      family: family.family,
      state: family.state,
      reason: unknownReason(file, link, family),
    },
  });
}

// Why an occurrence's membership is not established, from the owner's own facts. A named family is
// unknown only for a before-side occurrence whose after side could not be read whole (the owner
// compares the side's family record with the other side's); a family-less one, because not every
// family record of its own side was read.
//
// The rule's owner is MIK-L32's server: `_membership` and `_occurrences` in
// `application/review_unexplained_lane.py` decide these states. The response carries no reason per
// occurrence, so this sentence restates that rule; it must change when that mapping does.
function unknownReason(
  file: ReviewFileClassification,
  link: ReviewLaneLink,
  family: Occurrence,
): string | undefined {
  if (family.state !== 'membership_unknown') return undefined;
  const side = family.family ? (link.side === 'before' ? 'after' : 'before') : link.side;
  const read = file.sides.find((one) => one.side === side);
  const why =
    read?.knowledge === 'unavailable'
      ? `the ${side} knowledge could not be read (${read.detail ?? 'unavailable'})`
      : `not every family record of the ${side} knowledge could be read`;
  const what = family.family
    ? `whether ${family.family} still lists ${link.invariant} there`
    : `whether a family lists ${link.invariant} on that side`;
  return `${why}, so ${what} is not established`;
}

const plural = (count: number, noun: string) => `${count} ${noun}${count === 1 ? '' : 's'}`;

const TONE_LABELS: Record<Exclude<MarkTone, 'linked'>, string> = {
  unexplained: 'unexplained',
  attribution_unknown: 'attribution unknown',
};

// "INV-9BH2BNCT", "INV-2TQGXFAX · test", or a compact count: "2 intents", "1 intent · 1 test".
function markLabel(mark: Omit<HunkMark, 'label' | 'compact'>): string {
  if (mark.tone !== 'linked') return TONE_LABELS[mark.tone];
  const [one, ...more] = [
    ...mark.realizations.map((entry) => entry.invariant),
    ...mark.proofs.map((entry) => `${entry.invariant} · test`),
    ...mark.unknown.map(() => 'unknown'),
  ];
  if (one !== undefined && !more.length) return one;
  return [
    mark.realizations.length ? plural(mark.realizations.length, 'intent') : null,
    mark.proofs.length ? plural(mark.proofs.length, 'test') : null,
    mark.unknown.length ? 'unknown' : null,
  ]
    .filter((part): part is string => part !== null)
    .join(' · ');
}

// What an occurrence's membership state reads as, after its family's name.
const MEMBERSHIP_WORDS: Record<LaneMembershipState, string> = {
  member: 'member',
  before_only: 'before-only',
  removed_or_reassigned: 'removed or reassigned in after',
  membership_unknown: 'membership unknown',
  confirmed_no_family: 'no recorded family',
};

const TONE_COMPACT: Record<Exclude<MarkTone, 'linked'>, string> = {
  unexplained: '!',
  attribution_unknown: '?',
};

// "2", "1+1t", "1t", "1?": the compact count of a phone-width gutter.
function compactLabel(mark: Omit<HunkMark, 'label' | 'compact'>): string {
  if (mark.tone !== 'linked') return TONE_COMPACT[mark.tone];
  const counts = [
    mark.realizations.length ? String(mark.realizations.length) : null,
    mark.proofs.length ? `${mark.proofs.length}t` : null,
  ].filter((part): part is string => part !== null);
  return `${counts.join('+')}${mark.unknown.length ? '?' : ''}`;
}

// "FAM-XZ5BR65G r3 · member", "No recorded family", "Attribution unknown", ...
export function occurrenceLabel(occurrence: MarkOccurrence): string {
  if (occurrence.state === 'confirmed_no_family') return 'No recorded family';
  if (!occurrence.family) return 'Attribution unknown';
  const revision = occurrence.familyRevision === undefined ? '' : ` r${occurrence.familyRevision}`;
  return `${occurrence.family}${revision} · ${MEMBERSHIP_WORDS[occurrence.state]}`;
}

export function sidesLabel(sides: LaneSideName[]): string {
  return sides.length > 1 ? 'before and after' : sides[0];
}

// -- placement --------------------------------------------------------------------------------------

// The file lines one side of a pane draws (inclusive, in the file's own numbering); `null` when the
// pane does not draw that side at all.
export interface SideWindow {
  first: number;
  last: number;
}

export interface PaneWindow {
  before: SideWindow | null;
  after: SideWindow | null;
}

// `split` draws both sides in their own editors; `inline` draws the after text with the removed lines
// shown between its lines, so every mark sits on an after line.
export type PaneLayout = 'split' | 'inline';

export interface MarkAnchor {
  side: LaneSideName;
  line: number;
}

function changedRun(span: ReviewLaneSpan): [number, number] | null {
  return span.count > 0 ? [span.start, span.start + span.count - 1] : null;
}

// The first changed line of `span` the window draws, if it draws any.
function firstDrawn(span: ReviewLaneSpan, window: SideWindow | null): number | null {
  const run = changedRun(span);
  if (window === null || run === null) return null;
  if (run[1] < window.first || run[0] > window.last) return null;
  return Math.max(run[0], window.first);
}

// Where a hunk's mark sits in a pane, or `null` when the pane draws none of its changed lines. The
// mark goes on the first drawn changed after line; a hunk that changes no drawn after line is marked
// on its first drawn removed line -- in a side-by-side diff on the before side, inline on the after
// line just below the removed lines (the owner names the line they follow).
export function markAnchor(
  hunk: { before: ReviewLaneSpan; after: ReviewLaneSpan },
  layout: PaneLayout,
  window: PaneWindow,
): MarkAnchor | null {
  const after = firstDrawn(hunk.after, window.after);
  if (after !== null) return { side: 'after', line: after };
  const before = firstDrawn(hunk.before, window.before);
  if (before === null) return null;
  if (layout === 'split' || window.after === null) return { side: 'before', line: before };
  const { first, last } = window.after;
  const below = Math.min(Math.max(hunk.after.start + 1, first), Math.max(first, last));
  return { side: 'after', line: below };
}

// The whole text of a side, as a window.
export function wholeSide(lines: number): SideWindow {
  return { first: 1, last: lines };
}
