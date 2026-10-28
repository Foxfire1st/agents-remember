// The producer's line measurement (`line_changes`) of the captured entries the count tests draw. W10
// counts a location only by that measurement, never from its excerpts (changeSetOrder.ts `lineCounts`),
// and the card captures predate the field. The values are each changed entry's line diff over its
// captured before and after ranges, none of which is truncated, so they are what the producer measures.
import { vi } from 'vitest';

import type { ReviewTreeEntry, ReviewTreesResult } from '../../data/reviewTrees';

type LineChanges = NonNullable<ReviewTreeEntry['line_changes']>;

export const MEASURED_LINE_CHANGES: Readonly<Record<string, LineChanges>> = {
  // gitTrees.cards.captured.json
  'RLZ-CXH58B4W': { added: 1, removed: 1 },
  // l89.l181-cards.captured.json (L181 #8)
  'PRF-GQ2EVM': { added: 1, removed: 1 },
  'PRF-VEZBDV': { added: 1, removed: 1 },
  'PRF-ZXQ4R7': { added: 1, removed: 1 },
  'PRF-6MFPDJ': { added: 59, removed: 0 },
  'RLZ-1V0Y9T': { added: 10, removed: 10 },
  'RLZ-78JFRR': { added: 25, removed: 0 },
  'RLZ-C1JYFW': { added: 1, removed: 0 },
  'RLZ-C1KP4R': { added: 6, removed: 2 },
  'RLZ-D21Q7D': { added: 3, removed: 1 },
  'RLZ-EC5NRM': { added: 95, removed: 20 },
  'RLZ-H3YF9Z': { added: 7, removed: 0 },
  'RLZ-ZA95M4': { added: 95, removed: 20 },
  'RLZ-ZT2RRK': { added: 95, removed: 20 },
  'RLZ-1HAN2T': { added: 18, removed: 4 },
  'RLZ-M930WX': { added: 35, removed: 0 },
};

// The entries with the producer's measurement on every entry it is recorded for here.
export function measured<T extends ReviewTreeEntry>(entries: readonly T[]): T[] {
  return entries.map((entry) => {
    const counted = MEASURED_LINE_CHANGES[entry.id];
    return counted ? { ...entry, line_changes: { ...counted } } : entry;
  });
}

function measuredAnswer(body: ReviewTreesResult): ReviewTreesResult {
  const inventory = body.entry_inventory;
  return {
    ...body,
    ...(body.entries ? { entries: measured(body.entries) } : {}),
    ...(inventory?.state === 'ready' ? { entry_inventory: { ...inventory, entries: measured(inventory.entries) } } : {}),
  };
}

// The tree route's answers as the producer now sends them: with `line_changes` (call after the
// test's own `fetch` stub, e.g. `servePage`).
export function serveMeasuredTrees() {
  const served = globalThis.fetch;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string | URL, init?: RequestInit) => {
      const response = await served(address, init);
      if (new URL(String(address), 'http://localhost').pathname !== '/api/review/trees') return response;
      const body = (await response.json()) as ReviewTreesResult;
      return new Response(JSON.stringify(measuredAnswer(body)), {
        status: response.status,
        headers: { 'content-type': 'application/json' },
      });
    }),
  );
}
