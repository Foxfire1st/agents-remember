// The reading column's cards for a test: the change-set model's locations of some card-read entries
// (every subject outlined, the All view), each drawn by `ReviewBlockCard` as the column draws it.
import type { ReviewOutline } from '../../../data/review';
import type { ReviewTreeComparison, ReviewTreeEntry } from '../../../data/reviewTrees';
import { changeSetModel, type LocationNode } from '../changeSetOrder';
import { ReviewBlockCard } from '../ExpressionCards';

const PLACE = { repo: 'r', master: 'm', leaf: 'l', repository: 'r' };

function locationsOf(entries: readonly ReviewTreeEntry[], comparison: ReviewTreeComparison): LocationNode[] {
  const keys = [...new Set(entries.map((entry) => entry.invariant_key))];
  const outline: ReviewOutline = {
    state: 'outlined',
    detail: 'the entries of this case',
    families: [],
    invariants: keys.map((key) => ({
      selector_id: key,
      id: key,
      summary: '',
      change: 'changed',
      meaning: { state: 'none', labels: [] },
    })),
  };
  const model = changeSetModel(
    { outline, catalogue: [], code: new Map(), cards: { state: 'ready', entries, comparison } },
    'priority',
    'all',
    { selection: null },
  );
  return model.subjects.flatMap((node) => [...node.changed, ...node.unchanged, ...node.notComparable]);
}

export function BlockCards({
  entries,
  comparison,
  collapse = true,
  layout = 'split',
}: {
  entries: readonly ReviewTreeEntry[];
  comparison: ReviewTreeComparison;
  collapse?: boolean;
  layout?: 'split' | 'inline';
}) {
  return (
    <>
      {locationsOf(entries, comparison).map((node) => (
        <ReviewBlockCard
          key={node.key}
          node={node}
          place={PLACE}
          comparison={comparison}
          planning={new Map()}
          layout={layout}
          collapse={collapse}
          selected={false}
          onSelect={() => undefined}
        />
      ))}
    </>
  );
}
