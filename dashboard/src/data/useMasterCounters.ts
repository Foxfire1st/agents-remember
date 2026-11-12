import { useEffect, useMemo, useState } from 'react';

import { isAbortedRead } from './abortableRead';
import {
  loadMasterCounters,
  type MasterCounters,
  type MasterNetGeneration,
  type MasterNetPins,
} from './changeset';
import { type ReviewFailure, ReviewTransportError, reviewProblemFromCause } from './reviewTransport';

export interface MasterCounterTarget {
  repo: string;
  master: string;
  pins?: MasterNetPins;
}

export type MasterAttribution =
  | { phase: 'idle' | 'loading'; leaves: [] }
  | { phase: 'ready'; leaves: MasterCounters['leaves'] }
  | { phase: 'failed'; leaves: []; problem: ReviewFailure };

export function masterCountersMismatch(): ReviewTransportError {
  return new ReviewTransportError({
    token: 'unreadable',
    code: 'master-counter-binding-mismatch',
    detail: 'The master counter response does not match the requested series generation.',
  });
}

export function matchesMasterPins(
  generation: MasterNetGeneration | null | undefined,
  pins: MasterNetPins,
): boolean {
  return Object.entries(pins).every(([name, value]) => value === undefined || generation?.[name as keyof MasterNetPins] === value);
}

function sameGeneration(left: MasterNetGeneration, right: MasterNetGeneration | null | undefined): boolean {
  return right?.digest === left.digest && matchesMasterPins(right, left);
}

type NetRead = { key: string; data?: MasterCounters; problem?: ReviewFailure };

function useNetRead(target: MasterCounterTarget | null, key: string): NetRead | null {
  const [read, setRead] = useState<NetRead | null>(null);
  useEffect(() => {
    if (!target) return;
    const controller = new AbortController();
    void loadMasterCounters(target.repo, target.master, { includeLeaves: false, pins: target.pins }, controller.signal)
      .then((data) => {
        if (data.master !== target.master || !matchesMasterPins(data.generation, target.pins ?? {}))
          throw masterCountersMismatch();
        if (!controller.signal.aborted) setRead({ key, data });
      })
      .catch((cause: unknown) => {
        if (!controller.signal.aborted && !isAbortedRead(cause))
          setRead({ key, problem: reviewProblemFromCause(cause) });
      });
    return () => controller.abort();
  }, [target, key]);
  return read?.key === key ? read : null;
}

function useAttribution(
  target: MasterCounterTarget | null,
  net: MasterCounters | undefined,
  key: string,
): MasterAttribution {
  const [read, setRead] = useState<{ key: string; value: MasterAttribution } | null>(null);
  const generation = net?.generation;
  const attributionKey = JSON.stringify([key, generation]);
  useEffect(() => {
    if (!target || !generation) return;
    const controller = new AbortController();
    void loadMasterCounters(target.repo, target.master, { includeLeaves: true, pins: generation }, controller.signal)
      .then((data) => {
        if (data.master !== target.master || !sameGeneration(generation, data.generation))
          throw masterCountersMismatch();
        if (!controller.signal.aborted)
          setRead({ key: attributionKey, value: { phase: 'ready', leaves: data.leaves } });
      })
      .catch((cause: unknown) => {
        if (!controller.signal.aborted && !isAbortedRead(cause))
          setRead({ key: attributionKey, value: { phase: 'failed', leaves: [], problem: reviewProblemFromCause(cause) } });
      });
    return () => controller.abort();
  }, [target, generation, attributionKey]);
  if (!generation) return { phase: 'idle', leaves: [] };
  return read?.key === attributionKey ? read.value : { phase: 'loading', leaves: [] };
}

/** Net totals commit first; leaf attribution has its own later, pinned read and state. */
export function useMasterCounters(target: MasterCounterTarget | null, revalidation = 0) {
  const { repo, master, pins } = target ?? {};
  const { codeBase, codeTip, memoryBase, memoryTip } = pins ?? {};
  const address = useMemo(() => repo !== undefined && master !== undefined
    ? { repo, master, pins: { codeBase, codeTip, memoryBase, memoryTip } }
    : null, [repo, master, codeBase, codeTip, memoryBase, memoryTip]);
  const key = JSON.stringify([address, revalidation]);
  const net = useNetRead(address, key);
  const attribution = useAttribution(address, net?.data, key);
  return counterReadState(address, net, attribution);
}

function counterReadState(address: MasterCounterTarget | null, net: NetRead | null, attribution: MasterAttribution) {
  return {
    data: net?.data,
    problem: net?.problem ?? null,
    loading: address !== null && net === null,
    attribution,
  };
}
