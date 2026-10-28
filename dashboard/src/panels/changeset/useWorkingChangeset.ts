import { useEffect } from 'react';
import { leafChangeset, leafFileDiff, type TaskChangeset, type MasterChangeset, type FileDiff } from '../../data/changeset';
import { type ReviewFailure, reviewProblemFromCause } from '../../data/review';
import type { ChangeSetTarget } from './ChangeSetViewer';

export function useWorkingChangesetPoll(
  target: Pick<ChangeSetTarget, 'mode' | 'leaf' | 'repo' | 'master' | 'comparison'>,
  active: { kind: 'code' | 'memory'; path: string; hasSidecar?: boolean } | null,
  setters: {
    setData: (data: TaskChangeset | MasterChangeset | null) => void;
    setDiff: (diff: FileDiff | null) => void;
    setError: (error: ReviewFailure | null) => void;
    setDiffError: (error: ReviewFailure | null) => void;
  },
): void {
  const { mode, leaf, repo, master, comparison } = target;
  const { setData, setDiff, setError, setDiffError } = setters;
  useEffect(() => {
    if (mode !== 'working' || !leaf || comparison !== undefined) return;
    let live = true;
    const m = master ?? '';
    let timer: number | undefined;
    const refresh = async () => {
      const listRequest = leafChangeset(repo, m, leaf, 'working');
      const diffRequest = active
        ? leafFileDiff(repo, m, leaf, active.kind, active.path, 'working')
        : Promise.resolve(null);
      const [listResult, diffResult] = await Promise.allSettled([listRequest, diffRequest]);
      if (!live) return;
      applyWorkingRead(listResult, setData, setError);
      applyWorkingRead<FileDiff | null>(diffResult, setDiff, setDiffError);
      timer = window.setTimeout(refresh, 2500);
    };
    timer = window.setTimeout(refresh, 2500);
    return () => {
      live = false;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [mode, leaf, repo, master, comparison, active, setData, setDiff, setError, setDiffError]);
}

function applyWorkingRead<T>(
  result: PromiseSettledResult<T>,
  setValue: (value: T | null) => void,
  setProblem: (problem: ReviewFailure | null) => void,
): void {
  if (result.status === 'fulfilled') {
    setValue(result.value);
    setProblem(null);
  } else {
    setValue(null);
    setProblem(reviewProblemFromCause(result.reason));
  }
}

// WHAT COULD NOT BE SHOWN, NAMED (R33.3). A refused read says which read it was (the owner's own code,
// kept in its own element so the status line still reads exactly as it always did), the reason in the
// owner's words, the input it refused and the next action it published -- never an empty pane that
// leaves the reader guessing whether there was nothing to show or nothing was read.
