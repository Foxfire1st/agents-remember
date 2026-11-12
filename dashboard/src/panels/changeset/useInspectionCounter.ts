import { peekMasterCounters, type MasterChangeset, type MasterCounters, type MasterNetPins, type TaskChangeset } from '../../data/changeset';
import type { ReviewFailure } from '../../data/review';
import { matchesMasterPins, useMasterCounters } from '../../data/useMasterCounters';

interface Target { repo: string; master?: string; leaf?: string; generation?: MasterNetPins }

export function useInspectionCounter(target: Target, data: TaskChangeset | MasterChangeset | null, error: ReviewFailure | null) {
  const isSeries = Boolean(target.master) && !target.leaf;
  const read = useMasterCounters(isSeries ? { repo: target.repo, master: target.master!, pins: target.generation } : null);
  return inspectionCounterState(target, isSeries, read, data, error);
}

function measuredCounters(data: MasterCounters | undefined) {
  return data?.generation ? data.counters : undefined;
}

function knownPinnedTotals(target: Target) {
  if (!target.master || !target.generation) return undefined;
  const pins = target.generation;
  if ([pins.codeBase, pins.codeTip, pins.memoryBase, pins.memoryTip].some((pin) => pin === undefined))
    return undefined;
  const known = peekMasterCounters(target.repo, target.master);
  return known?.master === target.master && matchesMasterPins(known.generation, pins)
    ? measuredCounters(known) : undefined;
}

function inspectionCounterState(
  target: Target,
  isSeries: boolean,
  read: ReturnType<typeof useMasterCounters>,
  data: TaskChangeset | MasterChangeset | null,
  error: ReviewFailure | null,
) {
  return {
    seriesMeta: read.data ?? null,
    leaves: read.attribution.leaves,
    boundGeneration: read.data?.generation ?? target.generation,
    enabled: !isSeries || read.data !== undefined,
    // The button already measured this exact pair. Display its totals while the
    // pinned read validates currentness; the snapshot never enables inspection
    // or supplies the generation/currentness metadata above.
    counters: isSeries ? (read.loading ? knownPinnedTotals(target) : measuredCounters(read.data)) : data?.counters,
    error: read.problem ?? error,
    attribution: isSeries ? read.attribution : null,
  };
}
