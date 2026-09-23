// ICR-R12 at the mounted surface: the entry's historical target reaches the server and the surface
// states which record it is reading.
//
// WHAT THIS EXERCISES. `ReviewSurface` is the real component and `intentReview` the real client, so
// the query string asserted below is the one the browser builds, and the provenance line is read out
// of the rendered DOM rather than from a prop this test itself passed. Only `fetch` is stubbed.
//
// THE DEFECT THESE CASES CATCH. A closed leaf's review used to be offered only while the enclosure
// was live, and the surface had no way to name the record it wanted: a leaf whose worktree cleanup
// had removed the enclosure could not be reviewed at all. The historical target is what the entry
// now carries, and the provenance line is what tells a reader the panes are the recorded comparison
// rather than whatever the repository holds now.
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ReviewResult } from "../../data/review";
import { ReviewSurface } from "./ReviewSurface";

const REPO = "agents-remember";
const MASTER = "260921_complete-code-and-intent-review";
const LEAF = "260921-ICR-L12";
const SUBJECT = "3c513a59-5e5f-428a-b5e2-7301fcb280f7";

// The refusal a closed leaf with nothing recorded earns. The surface's job here is not to resolve it
// -- the server owns that -- but to show it rather than hiding the entry behind it.
const NOTHING_RECORDED: ReviewResult = {
  state: "refused",
  operation: "read_knowledge_review",
  repository_id: REPO,
  refusal: {
    code: "candidate_not_live",
    detail:
      "leaf 260921-ICR-L12 has no live worktree and records no comparison generation, so no " +
      "candidate can be resolved",
    next_action:
      "record the leaf's landed commit in its enclosure contract, or freeze its comparison while " +
      "the leaf's worktree is live; a review is never composed from the current branch tip or from " +
      "another leaf's records",
    offending_input: "code",
  },
};

function serving(body: unknown): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        ({
          ok: false,
          status: 404,
          statusText: "Not Found",
          json: async () => body,
        }) as unknown as Response,
    ),
  );
}

function requestUrl(fetchMock: ReturnType<typeof vi.fn>): string {
  return String(fetchMock.mock.calls[0][0]);
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ReviewSurface historical target (ICR-R12)", () => {
  it("asks for the leaf's recorded comparison and says so when the entry carries the record", async () => {
    const fetchFn = vi.fn(
      async () =>
        ({
          ok: false,
          status: 404,
          statusText: "Not Found",
          json: async () => NOTHING_RECORDED,
        }) as unknown as Response,
    );
    vi.stubGlobal("fetch", fetchFn);

    const view = render(
      <ReviewSurface
        repo={REPO}
        master={MASTER}
        leaf={LEAF}
        selectorKind="invariant"
        selectorId={SUBJECT}
        history="recorded"
        onBack={vi.fn()}
      />,
    );

    // The record the caller named is the one the client asks for: the live read is a different
    // question and the surface must not silently answer it instead.
    expect(requestUrl(fetchFn)).toContain("history=recorded");
    expect(requestUrl(fetchFn)).toContain(`selectorId=${SUBJECT}`);
    const provenance = await view.findByTestId("review-history");
    expect(provenance.textContent).toContain("recorded comparison");
    expect(view.getByTestId("review-surface").dataset.reviewHistory).toBe("recorded");
    // The refusal the recorded read earned still reaches the reader with its action, so a leaf that
    // recorded nothing is a stated state rather than a missing entry.
    const refusal = await view.findByTestId("review-refusal");
    expect(refusal.dataset.reviewCode).toBe("candidate_not_live");
    expect(refusal.textContent).toContain("records no comparison generation");
  });

  it("asks for the live candidate and claims no record when the entry names none", async () => {
    serving(NOTHING_RECORDED);
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;

    const view = render(
      <ReviewSurface
        repo={REPO}
        master={MASTER}
        leaf={LEAF}
        selectorKind="invariant"
        selectorId={SUBJECT}
        onBack={vi.fn()}
      />,
    );

    expect(requestUrl(fetchMock)).not.toContain("history=");
    await view.findByTestId("review-refusal");
    expect(view.queryByTestId("review-history")).toBeNull();
    expect(view.getByTestId("review-surface").dataset.reviewHistory).toBe("live");
  });
});
