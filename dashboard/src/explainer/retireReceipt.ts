import { useEffect, useState } from 'react';

import type { ExplainerRetireAnswer, ExplainerState } from './client';

/** A retirement receipt belongs to its generation, never to the seat that replaces it. */
export function useRetireReceipt(state: ExplainerState | null) {
  const [receipt, setReceipt] = useState<ExplainerRetireAnswer | null>(null);
  const superseded = receipt !== null && state !== null && state.generation > receipt.receipt.generation;
  useEffect(() => {
    if (superseded) setReceipt(null);
  }, [superseded]);
  // A Retire reply can arrive before its state readback. Keep its receipt for that readback,
  // but never put a "No process remains" notice above a state that still reports a live seat.
  const shown = superseded || state?.seat === 'live' ? null : receipt;
  return [shown, setReceipt] as const;
}
