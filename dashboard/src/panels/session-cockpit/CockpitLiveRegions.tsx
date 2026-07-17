import { css } from "../../../styled-system/css";
import { useAnnouncer } from "../../data/announcer";

// The cockpit's TWO live regions (260715-FEUI-L4 R8, design §9.5):
//   polite    — SetResult arrivals/promotions for the focused session (data/setClient announces);
//   assertive — ANY session entering failed or awaiting-input (data/announcer watcher).
// Both regions are permanent DOM nodes (AT needs the region to exist BEFORE the text changes);
// the InteractionBar's own alert covers the focused pending question — the announcer's skip rule
// keeps the two from double-announcing.

const srOnly = css({
  position: "absolute",
  width: "1px",
  height: "1px",
  overflow: "hidden",
  clipPath: "inset(50%)",
});

export function CockpitLiveRegions() {
  const polite = useAnnouncer((state) => state.polite);
  const assertive = useAnnouncer((state) => state.assertive);
  return (
    <>
      <div
        className={srOnly}
        role="status"
        aria-live="polite"
        data-testid="cockpit-live-polite"
        data-announce-seq={polite.seq}
      >
        {polite.text}
      </div>
      <div
        className={srOnly}
        role="alert"
        aria-live="assertive"
        data-testid="cockpit-live-assertive"
        data-announce-seq={assertive.seq}
      >
        {assertive.text}
      </div>
    </>
  );
}
