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
//   * the candidate published another comparison while the panel stayed open (`superseded`): the panes
//     below are the candidate's CURRENT comparison, replaced whole, and the identity the reader was
//     reading is named beside the refresh control as the previous input that moved -- never presented
//     as the generation on screen.
//
// A FAILED REFRESH KEEPS THE OLD GENERATION. The control is a re-read, so a read that fails leaves
// the last coherent comparison exactly where it was, still labelled, with its error stated beside it
// by the outcome region -- the notice below therefore describes the *displayed* comparison and never
// claims a generation the surface is not showing.
//
// WHAT IT IS NOT. No polling loop lives here: nothing re-reads on a timer, and the control is the
// reader's own action. It also chooses no dataset -- the previous identity it carries is only ever
// compared, never substituted for the comparison the server resolves. And the identity belongs to the
// question it was displayed under: it is sent only by a read that replaces exactly that question
// (see `ReviewReadCycle`), so the notice can never announce a candidate publication for a question
// the reader had merely switched to.

import { useRevalidateIntentEntry } from '../../data/intentEntryRevalidation';
import type { ReviewPayload } from '../../data/review';
import type { ReviewRead } from './ReviewOutcome';

// Whether the comparison on screen is still the one the candidate publishes. `undefined` is the
// state of a surface that has not been re-read since it was opened: nothing has been compared
// against the displayed identity yet, and claiming either state would assert a measurement nobody
// made. `superseded` carries the comparison that is there now, so the sentence can name both sides.
export type ReviewGenerationState = 'current' | 'superseded';

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
  const revalidateEntry = useRevalidateIntentEntry();
  return (
    <span style={{ display: 'inline-flex', gap: '0.5rem', alignItems: 'center' }}>
      <button
        type="button"
        onClick={() => {
          onRefresh();
          // The task entry's counts describe this comparison too: re-validate them with it.
          revalidateEntry();
        }}
        data-testid="review-refresh"
        data-review-busy={busy ? 'true' : 'false'}
        aria-busy={busy}
        title="re-read this comparison against the candidate as it is now"
      >
        ⟳ refresh
      </button>
      {generation === null ? null : (
        <span
          style={{ color: 'var(--muted)' }}
          data-testid="review-generation-notice"
          data-generation-state={generation.state}
          data-previous-binding={generation.previousBindingDigest}
          data-current-binding={generation.currentBindingDigest ?? ''}
        >
          {generation.state === 'superseded' ? 'Comparison updated' : 'Comparison current'}
          <details>
            <summary>Generation details</summary>
            {generation.state === 'superseded'
              ? `the candidate's comparison moved (was ${generation.previousBindingDigest}, is now ${generation.currentBindingDigest}) — the panes below have been replaced whole with the current one`
              : `the comparison below is still the candidate's current one (${generation.previousBindingDigest})`}
          </details>
        </span>
      )}
    </span>
  );
}

// Which generation the notice describes, from the identity that was carried, the payload the server
// answered with, and the phase of the read that carried it. `null` means no claim may be made;
// `current` means the identity the reader was looking at is the one the answer rendered;
// `superseded` means it moved and both identities are known.
//
// THE READ THAT CARRIED THE IDENTITY MUST HAVE ANSWERED (L17-F2). The phase is consulted before either
// digest is, because both this component's header and the packet's own rule say the same thing: the
// refresh exists to answer "has the candidate moved?", and a read that never reached the server has
// answered nothing. A `refused` or `failed` refresh therefore renders NO generation claim -- the last
// coherent comparison stays on screen, labelled, with the outcome region's failure beside it, and the
// surface does not tell the reader "still current" about a question nobody measured. (The payload it
// would have compared is the RETAINED one, whose identity the reader was already looking at, so the
// only thing the comparison could produce there is the answer the reader already had.)
//
// The two digests are read from the payload the surface actually renders, never from the props or
// from the request: a notice that described a generation the panes are not showing would be a false
// statement about the store, which is worse than showing no notice at all.
export function generationOf(
  read: ReviewRead,
  carried: string | null,
  shown: ReviewPayload | null,
): ReviewGeneration | null {
  if (read.phase !== 'reviewed') return null;
  if (carried === null || shown === null) return null;
  const current = shown.comparison?.binding_digest;
  if (current === undefined) return null;
  return {
    state: current === carried ? 'current' : 'superseded',
    previousBindingDigest: carried,
    currentBindingDigest: current,
  };
}
