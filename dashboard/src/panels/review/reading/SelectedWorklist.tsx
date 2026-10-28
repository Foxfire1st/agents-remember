import { useReviewTrees } from '../../../data/reviewTrees';
import { LeafWorklistView } from '../LeafKnowledgeChanges';
import type { OpenReviewChanges } from '../changeSetNavigation';
import type { PageProps } from './pageParts';

// The leaf worklist view (MIK-R86 rule 21), in the column in place of the change set when chosen.
export function WorklistView({ props, open }: { props: PageProps; open: OpenReviewChanges }) {
  const { changeSet } = props.page;
  const { repo, master, leaf } = props.reads;
  const complete = useReviewTrees(repo, master, leaf, { comparison: changeSet.number }, changeSet.number !== undefined);
  return (
    <LeafWorklistView
      read={changeSet.number === undefined ? { phase: 'not-converted' } : complete}
      reviewComparison={changeSet.number}
      onOpenChanges={() => open('all', undefined, 'memory')}
      onOpenPath={(path) => open('all', path)}
    />
  );
}

