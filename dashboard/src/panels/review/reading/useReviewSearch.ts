import { useMemo } from 'react';
import { useReviewTrees, type ReviewTreesRead, type ReviewTreesResult } from '../../../data/reviewTrees';
import { matchingTreeRead } from '../codeFacts';

function problemText(problem: { code: string; detail: string }): string { return `${problem.code}: ${problem.detail}`; }

function searchProblemOf(needed: boolean, read: ReviewTreesRead | null, inventory: ReviewTreesResult['entry_inventory']): string | undefined {
  if (!needed) return undefined;
  if (read?.phase === 'unavailable') return problemText(read.problem);
  if (read?.phase === 'trees' && inventory?.state !== 'ready') return inventory?.reason ?? 'The complete location inventory could not be read.';
  return undefined;
}

export function useReviewSearch(repo: string, master: string, leaf: string, number: number | undefined, query: string | undefined, moveGen: number) {
  const searchNeeded = Boolean(query?.trim());
  const searchRead = useReviewTrees(repo, master, leaf, { comparison: number }, number !== undefined && searchNeeded, moveGen);
  const searchTrees = matchingTreeRead(searchRead, number);
  const searchInventory = searchTrees?.entry_inventory;
  const searchProblem = searchProblemOf(searchNeeded, searchRead, searchInventory);
  const searching = searchNeeded && searchInventory?.state !== 'ready' && searchProblem === undefined;
  const facts = useMemo(() => searchNeeded && searchInventory?.state === 'ready' ? { inventory: searchInventory.entries } : {}, [searchNeeded, searchInventory]);
  return { facts, searching, problem: searchProblem };
}
