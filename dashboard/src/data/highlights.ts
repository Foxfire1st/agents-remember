// Wire shape for server-computed tree-sitter UTF-16 syntax highlights (models/highlights.py).

export type HighlightKind =
  | "keyword"
  | "string"
  | "comment"
  | "number"
  | "function"
  | "type"
  | "operator"
  | "punctuation"
  | "tag"
  | "attribute"
  | "heading";

export interface SyntaxHighlights {
  version: number;
  digest: string;
  kinds: readonly string[];
  spans: readonly number[];
}
