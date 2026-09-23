// The review surface's explicit refresh control and the notice that answers it (ICR-R17@v1).
//
// WHY THIS EXISTS AS A SEPARATE MODULE. `ReviewSurface.tsx` is over the repository's file-size rail,
// and this is one responsibility of its own: the reader's request that the displayed comparison be
// re-read against the candidate as it is now, plus the one sentence that says what the answer means.
// The surface keeps the load cycle and passes this component the two values it needs, so no read
// logic lives here and no rendering decision lives in the surface's read path.
//
// WHAT A REFRESH IS. It is a read of the SAME question -- same task context, same subject, same page
// position -- that additionally carries the identity of the comparison the reader is looking at. The
// server compares that identity against the comparison it renders, so the answer is one of exactly
// two facts and never a blend of them:
//
//   * the candidate's comparison is still the one on screen (`current`), and the panes below are the
//     answer to the question that was asked -- the whole payload was replaced, not patched;
//   * the candidate published another comparison while the panel stayed open (`superseded`), and the
//     previous identity is named on screen beside the refresh control rather than being silently
//     presented as the generation the reader is looking at.
//
// A FAILED REFRESH KEEPS THE OLD GENERATION. The control is a re-read, so a read that fails leaves
// the last coherent comparison exactly where it was, still labelled, with its error stated beside it
// by the outcome region -- the notice below therefore describes the *displayed* comparison and never
// claims a generation the surface is not showing.
//
// WHAT IT IS NOT. No polling loop lives here: nothing re-reads on a timer, and the control is the
// reader's own action. It also chooses no dataset -- the previous identity it carries is only ever
// compared, never substituted for the comparison the server resolves.

import type { ReviewPayload } from "../../data/review";

// Whether the comparison on screen is still the one the candidate publishes. `undefined` is the
// state of a surface that has not been re-read since it was opened: nothing has been compared
// against the displayed identity yet, and claiming either state would assert a measurement nobody
// made. `superseded` carries the comparison that is there now, so the sentence can name both sides.
export type ReviewGenerationState = "current" | "superseded";

export interface ReviewGeneration {
  state: ReviewGenerationState;
  previousBindingDigest: string;
  currentBindingDigest?: string;
}

export function ReviewRefresh({
  onRefresh,
  busy,
  generation,
}: {
  onRefresh: () => void;
  busy: boolean;
  generation: ReviewGeneration | null;
}) {
  return (
    <span style={{ display: "inline-flex", gap: "0.5rem", alignItems: "center" }}>
      <button
        type="button"
        onClick={onRefresh}
        data-testid="review-refresh"
        data-review-busy={busy ? "true" : "false"}
        aria-busy={busy}
        title="re-read this comparison against the candidate as it is now"
      >
        ⟳ refresh
      </button>
      {generation === null ? null : (
        <span
          style={{ color: "muted" }}
          data-testid="review-generation-notice"
          data-generation-state={generation.state}
          data-previous-binding={generation.previousBindingDigest}
          data-current-binding={generation.currentBindingDigest ?? ""}
        >
          {generation.state === "superseded"
            ? `the candidate published a new comparison (${generation.currentBindingDigest}) — the panes below are the comparison you were reading (${generation.previousBindingDigest}), replaced whole on the next read`
            : `the comparison below is still the candidate's current one (${generation.previousBindingDigest})`}
        </span>
      )}
    </span>
  );
}

// Which generation the notice describes, from the identity that was carried and the payload the
// server answered with. `null` means no re-read has compared anything yet; `current` means the
// identity the reader was looking at is the one the answer rendered; `superseded` means it moved and
// both identities are known.
//
// The two digests are read from the payload the surface actually renders, never from the props or
// from the request: a notice that described a generation the panes are not showing would be a false
// statement about the store, which is worse than showing no notice at all.
export function generationOf(
  carried: string | null,
  shown: ReviewPayload | null,
): ReviewGeneration | null {
  if (carried === null || shown === null) return null;
  const current = shown.comparison?.binding_digest;
  if (current === undefined) return null;
  return {
    state: current === carried ? "current" : "superseded",
    previousBindingDigest: carried,
    currentBindingDigest: current,
  };
}
