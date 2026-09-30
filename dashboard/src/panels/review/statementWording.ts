// "Wording unchanged" (MIK-R31 rule 3; the ICR master decision of 2026-09-28T13:40, as written).
//
// Two revisions' wording is unchanged only when EVERY authored text field is identical: the
// statement, the applicability, each condition and each exclusion -- or, for a family, the
// guarantee. Then the text is shown once, with `revision <before> → <after>` as compact metadata and
// the record IDs in details. A revision that changes only a condition is NOT "wording unchanged":
// its changed fields are named and shown. A field that could not be compared (not carried on this
// page) never counts as identical, so an unknown is never promoted to "unchanged".

export type WordingField = 'statement' | 'applicability' | 'conditions' | 'exclusions';

export interface AuthoredWording {
  statement?: string;
  // `undefined` = this field was not carried, which is different from an empty value.
  applicability?: string | null;
  conditions?: string[];
  exclusions?: string[];
}

export type WordingComparison =
  | { kind: 'same_revision' }
  | { kind: 'wording_unchanged' }
  | { kind: 'changed'; fields: WordingField[] }
  | { kind: 'not_comparable'; fields: WordingField[] };

const FIELDS: WordingField[] = ['statement', 'applicability', 'conditions', 'exclusions'];

function same(left: unknown, right: unknown): boolean | undefined {
  if (left === undefined || right === undefined) return undefined;
  return JSON.stringify(left) === JSON.stringify(right);
}

export function wordingComparison(
  before: AuthoredWording,
  after: AuthoredWording,
  sameRevision: boolean,
): WordingComparison {
  if (sameRevision) return { kind: 'same_revision' };
  const verdicts = FIELDS.map((field) => [field, same(before[field], after[field])] as const);
  const changed = verdicts.filter(([, verdict]) => verdict === false).map(([field]) => field);
  if (changed.length) return { kind: 'changed', fields: changed };
  const unknown = verdicts.filter(([, verdict]) => verdict === undefined).map(([field]) => field);
  if (unknown.length) return { kind: 'not_comparable', fields: unknown };
  return { kind: 'wording_unchanged' };
}

export function revisionMeta(before: string, after: string): string {
  return `revision ${before} → ${after}`;
}
