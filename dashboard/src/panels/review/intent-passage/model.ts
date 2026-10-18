import { alignLists, textDiff, type ListRow, type TextDiff } from '../wordDiff';

export type PreparedListRow = ListRow & { diff?: TextDiff };
export type PreparedPassage =
  { kind: 'text'; diff: TextDiff } | { kind: 'list'; rows: PreparedListRow[] };

// Layout choice and drawing share the same alignment and passage diffs.
export function preparePassage(
  before: string | string[] | null | undefined,
  after: string | string[] | null | undefined,
  projected = false,
): PreparedPassage | undefined {
  if (typeof before === 'string' && typeof after === 'string')
    return { kind: 'text', diff: textDiff(before, after) };
  if (Array.isArray(before) && Array.isArray(after) && !projected)
    return {
      kind: 'list',
      rows: alignLists(before, after).map((row) =>
        row.kind === 'changed' ? { ...row, diff: textDiff(row.beforeText, row.afterText) } : row,
      ),
    };
  return undefined;
}

export function passageHasWords(passage: PreparedPassage | undefined): boolean {
  return passage?.kind === 'text'
    ? passage.diff.kind === 'words'
    : (passage?.rows.some((row) => row.diff?.kind === 'words') ?? false);
}

export const COARSE_NOTE =
  'The changed region is too long to align word by word; it is marked as one removed and one added run.';
export const UNPAIRED_NOTE =
  'These items could not be paired one to one; they are shown as removed and added.';
