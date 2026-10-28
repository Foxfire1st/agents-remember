// Rollup knowledge and loaded location rows are different facts.
import type { ReviewOverview, ReviewSubjectSummary } from '../../data/reviewTrees';
import { CURRENTNESS_RANK } from './changeSetRanks';
import type { LocationNode } from './changeSetTypes';

export function completeSummary(overview: ReviewOverview | undefined, id: string): ReviewSubjectSummary | undefined {
  if (overview?.state !== 'ready') return undefined;
  return overview.subjects.find((row) => row.invariant_key === id) ?? {
    invariant_key: id, changed: 0, unchanged: 0, not_comparable: 0,
    changed_span: 0, changed_paths: [], entry_currentness: 'not measured', proofs: {},
  };
}

export function entrySummary(id: string, entries: readonly LocationNode[]): ReviewSubjectSummary {
  const changed = entries.filter((entry) => entry.change === 'changed');
  const least = entries.reduce<ReviewSubjectSummary['entry_currentness']>((state, entry) =>
    CURRENTNESS_RANK[state] <= CURRENTNESS_RANK[entry.currentness] ? state : entry.currentness,
    'not measured');
  const proofs: ReviewSubjectSummary['proofs'] = {};
  for (const entry of entries) {
    if (entry.role === 'proof') proofs[entry.currentness] = (proofs[entry.currentness] ?? 0) + 1;
  }
  return {
    invariant_key: id, changed: changed.length,
    unchanged: entries.filter((entry) => entry.change === 'unchanged').length,
    not_comparable: entries.filter((entry) => entry.change === 'undetermined').length,
    changed_span: changed.reduce((total, entry) => total + entry.span, 0),
    changed_paths: [...new Set(changed.map((entry) => entry.path))].sort(),
    entry_currentness: least, proofs,
  };
}
