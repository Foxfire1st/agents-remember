import { useEffect } from 'react';
import { loadChangeSet, peekChangeSet, type TaskChangeset, type MasterChangeset, type MasterNetPins, type LeafMode, type FileDiff } from '../../data/changeset';
import { type ReviewFailure, reviewProblemFromCause } from '../../data/review';
import { matchesMasterPins, masterCountersMismatch } from '../../data/useMasterCounters';

export function useChangesetLoad(
  repo: string,
  scope: string | undefined,
  master: string | undefined,
  leaf: string | undefined,
  mode: LeafMode | undefined,
  generation: MasterNetPins | undefined,
  setData: (data: TaskChangeset | MasterChangeset | null) => void,
  setError: (error: ReviewFailure | null) => void,
  setActive: (
    active: { kind: 'code' | 'memory'; path: string; hasSidecar?: boolean } | null,
  ) => void,
  setDiff: (diff: FileDiff | null) => void,
  setPartner: (diff: FileDiff | null) => void,
  comparison?: number,
  enabled = true,
): void {
  useEffect(() => {
    if (!enabled) { setData(null); return; }
    const controller = new AbortController();
    let live = true;
    setData(peekChangeSet({ repo, scope, master, leaf, mode, generation, comparison, includeLeaves: master && !leaf ? false : undefined }) ?? null);
    setError(null);
    setActive(null);
    setDiff(null);
    setPartner(null);
    const req = loadChangeSet({ repo, scope, master, leaf, mode, generation, comparison,
      includeLeaves: master && !leaf ? false : undefined }, controller.signal);
    void req.then(
      (d) => {
        if (!live) return;
        if (master && !leaf && generation && (!('generation' in d) || !matchesMasterPins(d.generation, generation))) {
          setData(null);
          setError(reviewProblemFromCause(masterCountersMismatch()));
          return;
        }
        setData(d);
      },
      // The refusal is an ANSWER and is carried as one (the same doctrine as the change-set bar's
      // own counter read, L32/D01): the route publishes its code, its reason, the input it refused
      // and the next action, and all of it is kept so this pane can NAME what it cannot show rather
      // than printing a bare status. A cause that is not this route's refusal is named as the
      // failure it is rather than guessed into one.
      (cause: unknown) => live && setError(reviewProblemFromCause(cause)),
    );
    return () => {
      live = false;
      controller.abort();
    };
  }, [
    enabled,
    repo,
    scope,
    master,
    leaf,
    mode,
    generation,
    comparison,
    setData,
    setError,
    setActive,
    setDiff,
    setPartner,
  ]);
}

