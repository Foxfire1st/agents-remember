// The block identity (MIK-R89 v2 rule 13). One name for one block of the review page, used by the
// tray's ⊕ (MIK-R188 rule 2), the selection in the URL (rule 9) and `j`/`k` (rule 11).
//
// A review block is one card-read entry. It is named by its entry id, the side, the path, the range
// mapped into the shown tree, and the comparison's four tree ids. The side is the after side, or the
// before side when the after side does not record the entry. The range is the card read's own
// `start_line`/`end_line` for that side: the server resolved the entry's locator in that side's code
// tree of the shown comparison, so nothing here maps or guesses a range. A side that did not resolve
// has no range (`null`).
//
// A hunk of code without an invariant is named by its path and its span through the file's diff
// between the comparison's two code trees (the lane read's zero-context hunks).
import type { ReviewLaneSpan } from '../../data/reviewLane';
import type { ReviewTreeComparison, ReviewTreeEntry } from '../../data/reviewTrees';

export interface ComparisonTrees {
  codeBase: string;
  codeCandidate: string;
  memoryBase: string;
  memoryCandidate: string;
}

export interface BlockIdentity {
  kind: 'entry';
  entry: string;
  side: 'after' | 'before';
  path: string;
  range: { start: number; end: number } | null;
  trees: ComparisonTrees;
}

export interface HunkIdentity {
  kind: 'hunk';
  path: string;
  before: ReviewLaneSpan;
  after: ReviewLaneSpan;
  trees: Pick<ComparisonTrees, 'codeBase' | 'codeCandidate'>;
}

export type ReviewBlockIdentity = BlockIdentity | HunkIdentity;

export function comparisonTrees(comparison: ReviewTreeComparison): ComparisonTrees {
  return {
    codeBase: comparison.code_base.tree,
    codeCandidate: comparison.code_candidate.tree,
    memoryBase: comparison.memory_base.tree,
    memoryCandidate: comparison.memory_candidate.tree,
  };
}

// The side a block names: after, or before when the after side does not record the entry. An after
// side whose memory tree could not be read (`recorded` undefined) is not known to record it, so the
// before side names the block only when it does record it; otherwise the after side stays.
export function blockSide(entry: ReviewTreeEntry): 'after' | 'before' {
  if (entry.after.recorded === false && entry.before.recorded) return 'before';
  if (entry.after.recorded === undefined && entry.before.recorded) return 'before';
  return 'after';
}

export function blockIdentityOf(entry: ReviewTreeEntry, comparison: ReviewTreeComparison): BlockIdentity {
  const side = blockSide(entry);
  const named = entry[side];
  const range =
    named.state === 'resolved' && named.start_line !== undefined && named.end_line !== undefined
      ? { start: named.start_line, end: named.end_line }
      : null;
  return { kind: 'entry', entry: entry.id, side, path: named.path, range, trees: comparisonTrees(comparison) };
}

export function hunkIdentityOf(
  path: string,
  hunk: { before: ReviewLaneSpan; after: ReviewLaneSpan },
  comparison: ReviewTreeComparison,
): HunkIdentity {
  return {
    kind: 'hunk',
    path,
    before: { start: hunk.before.start, count: hunk.before.count },
    after: { start: hunk.after.start, count: hunk.after.count },
    trees: { codeBase: comparison.code_base.tree, codeCandidate: comparison.code_candidate.tree },
  };
}

// A stable string for maps, React keys and equality. Two identities are the same block exactly when
// their keys are equal.
export function blockKey(identity: ReviewBlockIdentity): string {
  if (identity.kind === 'hunk') {
    const { before, after, trees } = identity;
    return [
      'h',
      identity.path,
      `${before.start},${before.count}`,
      `${after.start},${after.count}`,
      trees.codeBase,
      trees.codeCandidate,
    ].join('\u0000');
  }
  const { trees, range } = identity;
  return [
    'b',
    identity.entry,
    identity.side,
    identity.path,
    range ? `${range.start}-${range.end}` : '-',
    trees.codeBase,
    trees.codeCandidate,
    trees.memoryBase,
    trees.memoryCandidate,
  ].join('\u0000');
}

export function sameBlock(left: ReviewBlockIdentity, right: ReviewBlockIdentity): boolean {
  return blockKey(left) === blockKey(right);
}

// ---- URL form (the `sel=` value of the review hash, MIK-R89 v2 rule 9) ----

export type ReviewSubjectRef = { kind: 'family' | 'invariant'; id: string };

export type EncodedSelection =
  | { kind: 'block'; block: ReviewBlockIdentity }
  | { kind: 'subject'; subject: ReviewSubjectRef };

function toBase64Url(text: string): string {
  const bytes = new TextEncoder().encode(text);
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function fromBase64Url(value: string): string | null {
  try {
    const padded = value.replace(/-/g, '+').replace(/_/g, '/');
    const binary = atob(padded + '='.repeat((4 - (padded.length % 4)) % 4));
    const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
    return new TextDecoder('utf-8', { fatal: true }).decode(bytes);
  } catch {
    return null;
  }
}

export function formatSelection(selection: EncodedSelection): string {
  if (selection.kind === 'subject') return `s:${selection.subject.kind}:${encodeURIComponent(selection.subject.id)}`;
  const block = selection.block;
  if (block.kind === 'hunk') {
    const payload = [block.path, block.before.start, block.before.count, block.after.start, block.after.count,
      block.trees.codeBase, block.trees.codeCandidate];
    return `h:${toBase64Url(JSON.stringify(payload))}`;
  }
  const payload = [block.entry, block.side, block.path, block.range?.start ?? null, block.range?.end ?? null,
    block.trees.codeBase, block.trees.codeCandidate, block.trees.memoryBase, block.trees.memoryCandidate];
  return `b:${toBase64Url(JSON.stringify(payload))}`;
}

const isText = (value: unknown): value is string => typeof value === 'string' && value !== '';
const isLine = (value: unknown): value is number => Number.isInteger(value) && (value as number) >= 0;

// A block's range: both ends null (none recorded), or two lines in order. `undefined` = malformed.
function parseRange(start: unknown, end: unknown): BlockIdentity['range'] | undefined {
  if (start === null && end === null) return null;
  if (!isLine(start) || !isLine(end) || end < start) return undefined;
  return { start, end };
}

function parseBlock(payload: unknown): BlockIdentity | null {
  if (!Array.isArray(payload) || payload.length !== 9) return null;
  const [entry, side, path, start, end, codeBase, codeCandidate, memoryBase, memoryCandidate] = payload;
  if (!isText(entry) || (side !== 'after' && side !== 'before') || !isText(path)) return null;
  if (![codeBase, codeCandidate, memoryBase, memoryCandidate].every(isText)) return null;
  const range = parseRange(start, end);
  if (range === undefined) return null;
  return {
    kind: 'entry', entry, side, path, range,
    trees: { codeBase, codeCandidate, memoryBase, memoryCandidate } as ComparisonTrees,
  };
}

function parseHunk(payload: unknown): HunkIdentity | null {
  if (!Array.isArray(payload) || payload.length !== 7) return null;
  const [path, beforeStart, beforeCount, afterStart, afterCount, codeBase, codeCandidate] = payload;
  if (!isText(path) || ![beforeStart, beforeCount, afterStart, afterCount].every(isLine)) return null;
  if (!isText(codeBase) || !isText(codeCandidate)) return null;
  return {
    kind: 'hunk', path,
    before: { start: beforeStart as number, count: beforeCount as number },
    after: { start: afterStart as number, count: afterCount as number },
    trees: { codeBase, codeCandidate },
  };
}

// The selection a `sel=` value names, or null when the value names none (malformed, or an unknown
// form). Whether it lies inside the shown comparison is the caller's check against its trees.
export function parseSelection(value: string | null | undefined): EncodedSelection | null {
  if (!value) return null;
  const colon = value.indexOf(':');
  if (colon < 1) return null;
  const form = value.slice(0, colon);
  const rest = value.slice(colon + 1);
  if (form === 's') return parseSubject(rest);
  if (form !== 'b' && form !== 'h') return null;
  const payload = parseJson(fromBase64Url(rest));
  const block = form === 'b' ? parseBlock(payload) : parseHunk(payload);
  return block ? { kind: 'block', block } : null;
}

// `<kind>:<id>` of an `s:` value; null when the kind is unknown or the id is empty or malformed.
function parseSubject(rest: string): EncodedSelection | null {
  const second = rest.indexOf(':');
  const kind = rest.slice(0, second);
  if (second < 1 || (kind !== 'family' && kind !== 'invariant')) return null;
  try {
    const id = decodeURIComponent(rest.slice(second + 1));
    return id ? { kind: 'subject', subject: { kind, id } } : null;
  } catch {
    return null;
  }
}

// The JSON a `b:`/`h:` payload decodes to; null (which no parser admits) when it is not JSON.
function parseJson(json: string | null): unknown {
  if (json === null) return null;
  try {
    return JSON.parse(json) as unknown;
  } catch {
    return null;
  }
}

// Whether an identity belongs to the comparison whose trees are given (Failure and Recovery: "The URL
// names a selection outside the comparison").
export function inComparison(identity: ReviewBlockIdentity, trees: ComparisonTrees): boolean {
  if (identity.trees.codeBase !== trees.codeBase || identity.trees.codeCandidate !== trees.codeCandidate) return false;
  if (identity.kind === 'hunk') return true;
  return identity.trees.memoryBase === trees.memoryBase && identity.trees.memoryCandidate === trees.memoryCandidate;
}

function fieldMatches(value: unknown, expected: string): boolean {
  return typeof value !== 'string' || value === expected;
}

function treesMatchJson(trees: ComparisonTrees, record: Record<string, unknown>): boolean {
  const pairs: Array<[unknown, string]> = [
    [record.codeBase, trees.codeBase],
    [record.codeCandidate, trees.codeCandidate],
    [record.memoryBase, trees.memoryBase],
    [record.memoryCandidate, trees.memoryCandidate],
  ];
  return pairs.every(([actual, expected]) => fieldMatches(actual, expected));
}

function matchesEntryJsonRecord(
  identity: BlockIdentity,
  entryIds: readonly string[],
  record: Record<string, unknown>,
): boolean {
  if (typeof record.entry !== 'string' || !entryIds.includes(record.entry)) return false;
  if (!fieldMatches(record.path, identity.path)) return false;
  return treesMatchJson(identity.trees, record);
}

function matchesJsonReference(
  identity: ReviewBlockIdentity,
  entryIds: readonly string[],
  raw: string,
): boolean {
  if (!raw.startsWith('{')) return false;
  const parsed = parseJson(raw);
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) return false;
  const record = parsed as Record<string, unknown>;
  if (identity.kind === 'entry') return matchesEntryJsonRecord(identity, entryIds, record);
  return typeof record.path === 'string' && record.path === identity.path;
}

function matchesEncodedBlock(
  key: string,
  drawnKeys: readonly string[],
  reference: string,
): boolean {
  if (!reference.startsWith('b:') && !reference.startsWith('h:')) return false;
  const decoded = parseSelection(reference);
  if (decoded?.kind !== 'block') return false;
  const decodedKey = blockKey(decoded.block);
  return decodedKey === key || drawnKeys.includes(decodedKey);
}

function matchesPrefixedSubject(
  identity: ReviewBlockIdentity,
  entryIds: readonly string[],
  reference: string,
): boolean {
  if (reference.startsWith('card:')) {
    const entry = reference.slice('card:'.length).split('@')[0]?.split(':')[0] ?? '';
    return Boolean(entry) && entryIds.includes(entry);
  }
  if (reference.startsWith('hunk:') && identity.kind === 'hunk') {
    const pathPart = reference.slice('hunk:'.length).split('@')[0] ?? '';
    return pathPart === identity.path;
  }
  return false;
}

/**
 * Whether a stored grade or claim reference names this block (MIK-R89 v2 rule 13; MIK-R187 rule 3).
 */
export function matchesBlockReference(
  identity: ReviewBlockIdentity,
  drawnKeys: readonly string[],
  entryIds: readonly string[],
  reference: string,
): boolean {
  if (!reference) return false;
  const key = blockKey(identity);
  if (reference === key || drawnKeys.includes(reference) || entryIds.includes(reference)) return true;
  if (reference === formatSelection({ kind: 'block', block: identity })) return true;
  if (matchesEncodedBlock(key, drawnKeys, reference)) return true;
  if (matchesPrefixedSubject(identity, entryIds, reference)) return true;
  return matchesJsonReference(identity, entryIds, reference);
}
