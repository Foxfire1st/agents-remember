// The word-level intent diff (MIK-R35, adopting ICR-R35@v1): the pure half, no rendering.
//
// A changed authored text field reads as ONE prose passage with its removed and added words marked
// in place. This module decides what is marked; `IntentWordDiff.tsx` draws it.
//
// * Tokens (rule 2). Text is plain prose. A token is a run of non-whitespace (a word, with its
//   punctuation attached) or a run of whitespace, so every byte of the text is in exactly one token
//   and the tokens concatenate back to the text.
// * Truth (rule 8). The diff is presentation only. Its parts are the two texts' own tokens: the
//   `same` and `removed` parts concatenate to the before text byte for byte, and the `same` and
//   `added` parts to the after text. Nothing is normalized, trimmed or summarized.
// * Byte inequality (rule 1). Two texts differ when their strings differ. Texts that are equal once
//   all their whitespace is removed differ only in whitespace (rule 3), which is its own answer: that
//   includes a space inserted into or removed from a word ("a,b" -> "a, b"), review R1 F3.
// * Rewrite fallback (rule 4). The changed words of a passage are the words of its longer side that
//   are not unchanged (for two texts, `max(removed words, added words)`). When they are more than
//   REWRITE_RATIO of the longer side's words, the passage is a rewrite and is shown side by side.
// * List alignment (rule 1a). Conditions and exclusions are aligned by exact item text, never by
//   position across the whole list: see `alignLists`.

// The renderer's rewrite ratio: above it, a passage is shown side by side by default.
export const REWRITE_RATIO = 0.5;
// The largest token table one alignment builds (both sides after their common prefix and suffix are
// set aside). A longer middle is shown as one removed run and one added run: still the exact texts,
// only coarser, and the passage says so.
export const DIFF_CELL_LIMIT = 2_000_000;

export interface DiffPart {
  kind: 'same' | 'removed' | 'added';
  text: string;
}

export type TextDiff =
  | { kind: 'identical' }
  | { kind: 'whitespace_only' }
  | {
      kind: 'words';
      parts: DiffPart[];
      changedWords: number;
      longerWords: number;
      ratio: number;
      rewrite: boolean;
      // The middle was too long to align word by word (DIFF_CELL_LIMIT).
      coarse: boolean;
    };

const SPACE = /^\s+$/;

export function tokenize(text: string): string[] {
  return text.match(/\s+|\S+/g) ?? [];
}

export function isWhitespace(text: string): boolean {
  return SPACE.test(text);
}

const wordCount = (tokens: string[]) => tokens.filter((token) => !isWhitespace(token)).length;

type Op =
  | { kind: 'same'; i: number; j: number }
  | { kind: 'removed'; i: number }
  | { kind: 'added'; j: number };

// A longest common subsequence of two sequences, as the edit script that walks both in order. Where
// two scripts are equally long, a removal is taken before an addition, so a replaced run reads as its
// removed words followed by its added words.
function editScript(
  a: string[],
  b: string[],
  limit = DIFF_CELL_LIMIT,
): { ops: Op[]; coarse: boolean } {
  let start = 0;
  while (start < a.length && start < b.length && a[start] === b[start]) start++;
  let endA = a.length;
  let endB = b.length;
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) {
    endA--;
    endB--;
  }
  const ops: Op[] = [];
  for (let k = 0; k < start; k++) ops.push({ kind: 'same', i: k, j: k });
  const coarse = middleScript(a, b, [start, endA], [start, endB], ops, limit);
  for (let k = 0; k < a.length - endA; k++) ops.push({ kind: 'same', i: endA + k, j: endB + k });
  return { ops, coarse };
}

function middleScript(
  a: string[],
  b: string[],
  [fromA, toA]: [number, number],
  [fromB, toB]: [number, number],
  ops: Op[],
  limit: number,
): boolean {
  const n = toA - fromA;
  const m = toB - fromB;
  if ((n + 1) * (m + 1) > limit) {
    for (let i = fromA; i < toA; i++) ops.push({ kind: 'removed', i });
    for (let j = fromB; j < toB; j++) ops.push({ kind: 'added', j });
    return true;
  }
  const middleA = a.slice(fromA, toA);
  const middleB = b.slice(fromB, toB);
  const table = lcsTable(middleA, middleB);
  const width = m + 1;
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (middleA[i] === middleB[j]) ops.push({ kind: 'same', i: fromA + i++, j: fromB + j++ });
    else if (table[(i + 1) * width + j] >= table[i * width + j + 1])
      ops.push({ kind: 'removed', i: fromA + i++ });
    else ops.push({ kind: 'added', j: fromB + j++ });
  }
  while (i < n) ops.push({ kind: 'removed', i: fromA + i++ });
  while (j < m) ops.push({ kind: 'added', j: fromB + j++ });
  return false;
}

// table[i * (m + 1) + j] = the LCS length of a[i..] and b[j..], which is at most min(n, m).
function lcsTable(a: string[], b: string[]): Uint16Array | Uint32Array {
  const n = a.length;
  const m = b.length;
  const width = m + 1;
  const table =
    Math.min(n, m) < 0xffff ? new Uint16Array((n + 1) * width) : new Uint32Array((n + 1) * width);
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--)
      table[i * width + j] =
        a[i] === b[j]
          ? table[(i + 1) * width + j + 1] + 1
          : Math.max(table[(i + 1) * width + j], table[i * width + j + 1]);
  return table;
}

// Adjacent parts of one kind become one part.
function merged(parts: DiffPart[]): DiffPart[] {
  const out: DiffPart[] = [];
  for (const part of parts) {
    const last = out[out.length - 1];
    if (last?.kind === part.kind) last.text += part.text;
    else if (part.text) out.push({ ...part });
  }
  return out;
}

// A change region is a maximal run of removed and added parts; it reads as its removed text, then its
// added text. Whitespace kept between two changes joins the region on both sides, so a rewritten
// phrase reads as one removed phrase and one added phrase instead of alternating single words. Each
// side's own sequence is untouched, so both texts still reconstruct exactly.
function regions(parts: DiffPart[]): DiffPart[] {
  const out: DiffPart[] = [];
  let removed = '';
  let added = '';
  const flush = () => {
    if (removed) out.push({ kind: 'removed', text: removed });
    if (added) out.push({ kind: 'added', text: added });
    removed = '';
    added = '';
  };
  parts.forEach((part, index) => {
    const between = betweenChanges(parts, index);
    if (part.kind === 'removed' || between) removed += part.text;
    if (part.kind === 'added' || between) added += part.text;
    if (part.kind === 'same' && !between) {
      flush();
      out.push(part);
    }
  });
  flush();
  return out;
}

// Whitespace kept between two changes (merged parts alternate `same` with change runs).
function betweenChanges(parts: DiffPart[], index: number): boolean {
  const [prev, part, next] = [parts[index - 1], parts[index], parts[index + 1]];
  if (!prev || !next || part.kind !== 'same') return false;
  return prev.kind !== 'same' && next.kind !== 'same' && isWhitespace(part.text);
}

export function textDiff(before: string, after: string): TextDiff {
  if (before === after) return { kind: 'identical' };
  if (before.replace(/\s+/g, '') === after.replace(/\s+/g, '')) return { kind: 'whitespace_only' };
  const a = tokenize(before);
  const b = tokenize(after);
  const { ops, coarse } = editScript(a, b);
  const parts = regions(
    merged(
      ops.map((op) =>
        op.kind === 'added' ? { kind: 'added', text: b[op.j] } : { kind: op.kind, text: a[op.i] },
      ),
    ),
  );
  const common = ops.filter((op) => op.kind === 'same' && !isWhitespace(a[op.i])).length;
  const longerWords = Math.max(wordCount(a), wordCount(b));
  const changedWords = longerWords - common;
  const ratio = longerWords === 0 ? 1 : changedWords / longerWords;
  return {
    kind: 'words',
    parts,
    changedWords,
    longerWords,
    ratio,
    rewrite: ratio > REWRITE_RATIO,
    coarse,
  };
}

// One side of a diff, reassembled from its parts: the before text from `same` and `removed`, the
// after text from `same` and `added`.
export function sideText(parts: DiffPart[], side: 'before' | 'after'): string {
  const dropped = side === 'before' ? 'added' : 'removed';
  return parts
    .filter((part) => part.kind !== dropped)
    .map((part) => part.text)
    .join('');
}

// Whitespace made visible, for the whitespace-only disclosure and for a whitespace-only mark.
export function visibleWhitespace(text: string): string {
  return text.replace(/\s/g, (char) =>
    char === ' ' ? '·' : char === '\t' ? '→' : char === '\n' ? '↵\n' : char === '\r' ? '␍' : '␣',
  );
}

// What a whitespace-only change is, in words, for assistive technology.
export function describeWhitespace(text: string): string {
  const counts = new Map<string, number>();
  for (const char of text) {
    const name =
      char === ' '
        ? 'space'
        : char === '\t'
          ? 'tab'
          : char === '\n'
            ? 'line break'
            : char === '\r'
              ? 'carriage return'
              : 'other whitespace character';
    counts.set(name, (counts.get(name) ?? 0) + 1);
  }
  return [...counts].map(([name, count]) => `${count} ${name}${count === 1 ? '' : 's'}`).join(', ');
}

// Rule 1a: ordered lists (conditions, exclusions) aligned by exact item text. Positions are 0-based.
export type ListRow =
  | { kind: 'unchanged'; text: string; before: number; after: number }
  | { kind: 'moved'; text: string; before: number; after: number }
  | { kind: 'changed'; beforeText: string; afterText: string; before: number; after: number }
  | { kind: 'removed'; text: string; before: number }
  | { kind: 'added'; text: string; after: number };

// 1. The longest common subsequence of items by exact text: those items are unchanged.
// 2. An item outside it whose exact text also occurs outside it on the other side is `moved`; equal
//    texts pair one to one, in order. Moved items leave the gaps before any gap is paired.
// 3. In each gap between two aligned items, the remaining before and after items pair in order (and
//    are word-diffed) only when the gap holds as many on each side; otherwise they are removed and
//    added items. Nothing is ever paired by its position in the whole list.
export function alignLists(before: string[], after: string[]): ListRow[] {
  // Lists are aligned exactly whatever their length: a coarse script would pair by position.
  const anchors = editScript(before, after, Infinity).ops.filter(
    (op): op is Extract<Op, { kind: 'same' }> => op.kind === 'same',
  );
  const inA = new Set(anchors.map((op) => op.i));
  const inB = new Set(anchors.map((op) => op.j));
  const moved = movedPairs(before, after, inA, inB);
  const movedFrom = new Map([...moved].map(([j, i]) => [i, j]));
  const rows: ListRow[] = [];
  let prevI = -1;
  let prevJ = -1;
  for (const anchor of [...anchors, { kind: 'same' as const, i: before.length, j: after.length }]) {
    const gapA = range(prevI + 1, anchor.i).filter((i) => !movedFrom.has(i));
    const gapB = range(prevJ + 1, anchor.j);
    rows.push(...gapRows(before, after, gapA, gapB, moved));
    if (anchor.i < before.length)
      rows.push({ kind: 'unchanged', text: before[anchor.i], before: anchor.i, after: anchor.j });
    prevI = anchor.i;
    prevJ = anchor.j;
  }
  return rows;
}

// after index -> before index, for every moved item.
function movedPairs(
  before: string[],
  after: string[],
  inA: Set<number>,
  inB: Set<number>,
): Map<number, number> {
  const waiting = new Map<string, number[]>();
  before.forEach((text, i) => {
    if (!inA.has(i)) waiting.set(text, [...(waiting.get(text) ?? []), i]);
  });
  const moved = new Map<number, number>();
  after.forEach((text, j) => {
    const queue = waiting.get(text);
    if (inB.has(j) || !queue?.length) return;
    moved.set(j, queue.shift()!);
  });
  return moved;
}

function gapRows(
  before: string[],
  after: string[],
  gapA: number[],
  gapB: number[],
  moved: Map<number, number>,
): ListRow[] {
  const plain = gapB.filter((j) => !moved.has(j));
  const paired = plain.length > 0 && plain.length === gapA.length;
  const rows: ListRow[] = paired
    ? []
    : gapA.map((i) => ({ kind: 'removed' as const, text: before[i], before: i }));
  let next = 0;
  for (const j of gapB) {
    const from = moved.get(j);
    if (from !== undefined) rows.push({ kind: 'moved', text: after[j], before: from, after: j });
    else if (paired) {
      const i = gapA[next++];
      rows.push({
        kind: 'changed',
        beforeText: before[i],
        afterText: after[j],
        before: i,
        after: j,
      });
    } else rows.push({ kind: 'added', text: after[j], after: j });
  }
  return rows;
}

function range(from: number, to: number): number[] {
  return Array.from({ length: Math.max(0, to - from) }, (_, k) => from + k);
}
