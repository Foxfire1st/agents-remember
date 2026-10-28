// Whether the mounted review page is the one on screen (D575). A bottom tab left with a review open
// keeps the review mounted and hidden, so returning shows the reader's page as it was left -- its
// selection, revealed changes, kept answers and scroll -- with no request. While it is hidden, the
// keys and gestures it listens for on the whole document belong to the tab on screen.
import { createContext, useContext } from 'react';

export const ReviewShownContext = createContext(true);

export function useReviewShown(): boolean {
  return useContext(ReviewShownContext);
}
