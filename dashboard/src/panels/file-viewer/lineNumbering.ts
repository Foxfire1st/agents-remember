// Line numbers that keep the file's own numbering when the document is an excerpt starting at line
// `first` (1 = the plain gutter, exactly as before). The reviewer's expression cards show one region
// of a file, and its gutter must read the file's lines, not the excerpt's.
import type { Extension } from '@codemirror/state';
import { lineNumbers } from '@codemirror/view';

export function numberedFrom(first = 1): Extension {
  return first === 1
    ? lineNumbers()
    : lineNumbers({ formatNumber: (line) => String(line + first - 1) });
}
