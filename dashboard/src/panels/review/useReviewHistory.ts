// Which record a review surface reads, and how the page leaves that frame (ICR-R12, MIK-R195 F06).
//
// A target may name the record it is addressed to: a closed leaf's entry carries `historical`, and
// the surface then asks for that leaf's recorded comparison. The delivered link needs the opposite
// move -- the page's own default *is* the pair the leaf delivered -- so the flag is state the page
// can clear, not a prop fixed for the mount. A new target on the same mounted surface re-seeds it.
import { useCallback, useEffect, useRef, useState } from 'react';

import type { ReviewHistory } from '../../data/review';

export interface ReviewHistoryFrame {
  history: ReviewHistory | undefined;
  leaveHistory: () => void;
}

export function useReviewHistory(initial: ReviewHistory | undefined): ReviewHistoryFrame {
  const [history, setHistory] = useState<ReviewHistory | undefined>(initial);
  const seeded = useRef(initial);
  useEffect(() => {
    if (seeded.current === initial) return;
    seeded.current = initial;
    setHistory(initial);
  }, [initial]);
  const leaveHistory = useCallback(() => setHistory(undefined), []);
  return { history, leaveHistory };
}
