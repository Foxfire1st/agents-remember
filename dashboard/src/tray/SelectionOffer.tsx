// The small non-modal "⊕ add" beside a highlight in a dashboard panel (MIK-R188 rules 3 and 4). It is
// a plain fixed button, not a popover or dialog: it takes no focus, its `mousedown` is prevented so
// the click keeps the selection and the focus, and only its click adds the passage to the tray.
import { createPortal } from 'react-dom';

import * as look from './trayStyles';
import { useSelectionOffer } from './useSelectionOffer';

const OFFER_WIDTH = 64;

export function SelectionOffer() {
  const { offer, take } = useSelectionOffer();
  if (!offer) return null;
  const left = Math.max(4, Math.min(offer.left, window.innerWidth - OFFER_WIDTH - 4));
  const top = Math.max(4, Math.min(offer.top, window.innerHeight - 28));
  return createPortal(
    <button
      type="button"
      className={look.offer}
      style={{ top, left }}
      data-selection-offer=""
      data-testid="selection-offer"
      aria-label="Add the highlighted text to the AR tray"
      onMouseDown={(event) => event.preventDefault()}
      onClick={take}
    >
      ⊕ add
    </button>,
    document.body,
  );
}
