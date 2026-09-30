// MIK-R35 (ICR-R35@v1): the word diff's decisions -- tokens, marks, whitespace-only, the rewrite
// ratio and the list alignment of rule 1a. The texts are the real INV-2TQGXFAX wording of the
// converted scratch leaf (gitTrees.invariant.captured.json), edited the way each case describes.
import { describe, expect, it } from 'vitest';

import {
  DIFF_CELL_LIMIT,
  REWRITE_RATIO,
  alignLists,
  describeWhitespace,
  sideText,
  textDiff,
  tokenize,
  visibleWhitespace,
  type DiffPart,
} from './wordDiff';

const STATEMENT =
  "An unchanged path that no realization recorded in the comparison's knowledge links is refused " +
  'with source_content_unresolved naming what each snapshot answered, and no content is read for it.';

function words(diff: ReturnType<typeof textDiff>): DiffPart[] {
  if (diff.kind !== 'words') throw new Error(`expected a word diff, got ${diff.kind}`);
  return diff.parts;
}

describe('tokens', () => {
  it('keeps every byte: words with their punctuation, and whitespace runs as they are', () => {
    expect(tokenize('must refuse, then  report.\nDone ')).toEqual([
      'must',
      ' ',
      'refuse,',
      ' ',
      'then',
      '  ',
      'report.',
      '\n',
      'Done',
      ' ',
    ]);
    expect(tokenize(STATEMENT).join('')).toBe(STATEMENT);
    expect(tokenize('')).toEqual([]);
  });
});

describe('a changed text field', () => {
  it('marks the replaced words in place within one sentence (the conforming example)', () => {
    const after = STATEMENT.replace('is refused', 'is rejected and reported');
    const diff = textDiff(STATEMENT, after);
    expect(words(diff)).toEqual([
      {
        kind: 'same',
        text: "An unchanged path that no realization recorded in the comparison's knowledge links is ",
      },
      { kind: 'removed', text: 'refused' },
      { kind: 'added', text: 'rejected and reported' },
      {
        kind: 'same',
        text: ' with source_content_unresolved naming what each snapshot answered, and no content is read for it.',
      },
    ]);
    expect(diff).toMatchObject({ changedWords: 3, longerWords: 30, rewrite: false });
  });

  it('reassembles both exact texts from its parts, whatever the change', () => {
    const cases: [string, string][] = [
      [STATEMENT, STATEMENT.replace('refused', 'refused, logged').replace(' and no', ' and\nno')],
      ['a  b c', 'a b d'],
      ['', 'A new applicability.'],
      ['Alpha beta gamma.', ''],
      [' leading and trailing ', 'leading and trailing'.toUpperCase()],
      ['one two three four', 'four three two one'],
      [STATEMENT, STATEMENT.split(' ').reverse().join(' ')],
    ];
    for (const [before, after] of cases) {
      const parts = words(textDiff(before, after));
      expect(sideText(parts, 'before')).toBe(before);
      expect(sideText(parts, 'after')).toBe(after);
    }
  });

  it('reads a rewritten phrase as one removed run and one added run', () => {
    expect(words(textDiff('It must refuse the read.', 'It will report every path.'))).toEqual([
      { kind: 'same', text: 'It ' },
      { kind: 'removed', text: 'must refuse the read.' },
      { kind: 'added', text: 'will report every path.' },
    ]);
  });

  it('keeps a whitespace change beside a word change as its own exact mark', () => {
    expect(words(textDiff('a  b c', 'a b d'))).toEqual([
      { kind: 'same', text: 'a' },
      { kind: 'removed', text: '  ' },
      { kind: 'added', text: ' ' },
      { kind: 'same', text: 'b ' },
      { kind: 'removed', text: 'c' },
      { kind: 'added', text: 'd' },
    ]);
    expect(describeWhitespace('  \n\t')).toBe('2 spaces, 1 line break, 1 tab');
    expect(visibleWhitespace('a b\tc\n')).toBe('a·b→c↵\n');
  });

  it('decides by bytes: identical, whitespace only, or words', () => {
    expect(textDiff(STATEMENT, STATEMENT)).toEqual({ kind: 'identical' });
    expect(textDiff(STATEMENT, `${STATEMENT} `)).toEqual({ kind: 'whitespace_only' });
    expect(textDiff('a b', 'a\nb')).toEqual({ kind: 'whitespace_only' });
    // A space inserted into or removed from a word is whitespace only too (review R1 F3).
    expect(textDiff('a,b', 'a, b')).toEqual({ kind: 'whitespace_only' });
    expect(textDiff('answered, and', 'answered,and')).toEqual({ kind: 'whitespace_only' });
    expect(textDiff('a b', 'a b.').kind).toBe('words');
  });
});

describe('the rewrite ratio', () => {
  it('is the named renderer constant 0.5, compared with changed words over the longer side', () => {
    expect(REWRITE_RATIO).toBe(0.5);
    // 2 of 4 words changed: exactly the ratio, so not above it.
    expect(textDiff('one two three four', 'one two five six')).toMatchObject({
      changedWords: 2,
      longerWords: 4,
      ratio: 0.5,
      rewrite: false,
    });
    // 3 of 4 changed.
    expect(textDiff('one two three four', 'one six seven eight')).toMatchObject({
      ratio: 0.75,
      rewrite: true,
    });
    // An insertion counts the inserted words against the longer (after) side.
    expect(textDiff('one two', 'one two three four five')).toMatchObject({
      changedWords: 3,
      longerWords: 5,
      rewrite: true,
    });
  });

  it('marks a middle too long to align as one removed and one added run, still exact', () => {
    const before = `Start ${Array.from({ length: 800 }, (_, k) => `w${k}`).join(' ')} end`;
    const after = `Start ${Array.from({ length: 800 }, (_, k) => `x${k}`).join(' ')} end`;
    expect(1600 * 1600).toBeGreaterThan(DIFF_CELL_LIMIT);
    const diff = textDiff(before, after);
    expect(diff).toMatchObject({ kind: 'words', coarse: true, rewrite: true });
    const parts = words(diff);
    expect(parts.map((part) => part.kind)).toEqual(['same', 'removed', 'added', 'same']);
    expect(sideText(parts, 'before')).toBe(before);
    expect(sideText(parts, 'after')).toBe(after);
  });
});

describe('list alignment (rule 1a)', () => {
  const CONDITIONS = [
    'Every bound snapshot was read and none records a realization at the exact path.',
    'The refusal is source_content_unresolved and names what each snapshot answered.',
  ];
  const [first, second] = CONDITIONS;

  it('names an inserted and a removed condition, keeping the aligned ones unchanged', () => {
    expect(alignLists(CONDITIONS, [first, 'A new condition.', second])).toEqual([
      { kind: 'unchanged', text: first, before: 0, after: 0 },
      { kind: 'added', text: 'A new condition.', after: 1 },
      { kind: 'unchanged', text: second, before: 1, after: 2 },
    ]);
    expect(alignLists([first, 'Dropped.', second], CONDITIONS)).toEqual([
      { kind: 'unchanged', text: first, before: 0, after: 0 },
      { kind: 'removed', text: 'Dropped.', before: 1 },
      { kind: 'unchanged', text: second, before: 2, after: 1 },
    ]);
  });

  it('pairs a gap with equal counts for a word diff, and never pairs unequal gaps', () => {
    const edited = second.replace('names', 'lists');
    expect(alignLists(CONDITIONS, [first, edited])).toEqual([
      { kind: 'unchanged', text: first, before: 0, after: 0 },
      { kind: 'changed', beforeText: second, afterText: edited, before: 1, after: 1 },
    ]);
    expect(alignLists(['A', 'B', 'C'], ['A', 'X', 'Y', 'C']).map((row) => row.kind)).toEqual([
      'unchanged',
      'removed',
      'added',
      'added',
      'unchanged',
    ]);
  });

  it('shows a reordered item as moved, never pairing by position across the list', () => {
    expect(alignLists(['A', 'B', 'C'], ['C', 'A', 'B'])).toEqual([
      { kind: 'moved', text: 'C', before: 2, after: 0 },
      { kind: 'unchanged', text: 'A', before: 0, after: 1 },
      { kind: 'unchanged', text: 'B', before: 1, after: 2 },
    ]);
    // Positional pairing would diff A against X, B against A and C against B.
    expect(alignLists(['A', 'B', 'C'], ['X', 'A', 'B']).map((row) => row.kind)).toEqual([
      'added',
      'unchanged',
      'unchanged',
      'removed',
    ]);
  });

  it('takes moved items out of the gaps before pairing, and pairs equal texts one to one', () => {
    expect(alignLists(['A', 'M', 'B', 'C', 'D'], ['A', 'X', 'C', 'D', 'M'])).toEqual([
      { kind: 'unchanged', text: 'A', before: 0, after: 0 },
      { kind: 'changed', beforeText: 'B', afterText: 'X', before: 2, after: 1 },
      { kind: 'unchanged', text: 'C', before: 3, after: 2 },
      { kind: 'unchanged', text: 'D', before: 4, after: 3 },
      { kind: 'moved', text: 'M', before: 1, after: 4 },
    ]);
    const twice = alignLists(['M', 'M', 'A', 'B', 'C'], ['A', 'B', 'C', 'M', 'M']);
    expect(twice.filter((row) => row.kind === 'moved')).toEqual([
      { kind: 'moved', text: 'M', before: 0, after: 3 },
      { kind: 'moved', text: 'M', before: 1, after: 4 },
    ]);
  });
});
