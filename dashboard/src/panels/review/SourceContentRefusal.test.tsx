// R16 (visible structured refusals) at the Source pane's expansion read, over R03's own pane.
//
// WHAT THIS EXERCISES. `SourceContent` is the real component and `reviewSourceContent` the real
// client function; only `fetch` is stubbed. R03 owns this pane and its typed-refusal rendering, which
// is untouched (`review-source-refusal`); this module pins the OTHER answer shape the expansion route
// can give -- a transport-level refusal or a socket that never answered -- which used to be printed
// as the thrown message alone (`the entry's content could not be read: 503 unavailable`) and now goes
// through the shared `ReviewProblemBlock` the surface uses (ICR-R16).
//
// WHERE THE BODIES COME FROM. The 503 body is the measured output of the REAL route over REAL HTTP in
// this leaf's evidence run (`probe-l16-real-route.py`; recorded as `unwired-adapter` in
// `ar-coordination/temp/icr/evidence-l16-refusals.txt`, sha256
// c55ee098da2014aabb8784ae65ca289f0111dbdd18aa70a504553fca1635a690).

import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ReviewChangedFile } from "../../data/review";
import { SourceContent } from "./SourceContent";

const ENTRY: ReviewChangedFile = {
  path: "src/batch.py",
  status: "modified",
  content: "text",
  mode_change: false,
};
const UNWIRED = {
  status: "unavailable",
  detail:
    "no review adapter is wired into this process, so an inventory entry's source content cannot " +
    "be opened; the surface is not served rather than served as an empty file",
  nextAction: "start the dashboard through its composition root, which supplies the review adapter",
};

const mount = () =>
  render(
    <SourceContent
      repo="agents-remember"
      master="260921_complete-code-and-intent-review"
      leaf="260921-ICR-L16"
      entry={ENTRY}
      beforeCodeTreeId="88f5c3c763d96791f193cf49a438065f48e05530"
      afterCodeTreeId="4bca9de2059271a1c9ba46e5196402631bf1d334"
    />,
  );

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("the expansion read's transport-level failure", () => {
  it("carries the code, reason, offending input and next action of an unwired adapter", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          ({
            ok: false,
            status: 503,
            statusText: "Service Unavailable",
            json: async () => UNWIRED,
          }) as unknown as Response,
      ),
    );

    const view = mount();

    const block = await view.findByTestId("review-failure");
    expect(block.dataset.reviewState).toBe("unavailable-history");
    expect(block.dataset.reviewCode).toBe("unavailable");
    expect(block.textContent).toContain("this entry's content could not be opened");
    expect(block.textContent).toContain(UNWIRED.detail);
    expect(block.textContent).toContain(UNWIRED.nextAction);
    // The pre-fix rendering: the thrown message alone, with none of the server's own words.
    expect(view.queryByTestId("review-source-error")).toBeNull();
  });

  it("names an unadmitted query as a validation failure rather than as the thrown message", async () => {
    const badRequest = {
      status: "bad-request",
      detail:
        "an inventory entry's content is opened for one exact generation: the path and both bound " +
        "code tree ids are required together, and a missing one is refused rather than resolved to " +
        "a generation the caller did not name",
      offendingInput: "src/batch.py",
      expected: "path, beforeCodeTreeId and afterCodeTreeId",
      nextAction:
        "expand an entry through the inventory the review returned, whose own path and code tree " +
        "ids are the address of the content",
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          ({
            ok: false,
            status: 400,
            statusText: "Bad Request",
            json: async () => badRequest,
          }) as unknown as Response,
      ),
    );

    const view = mount();

    const block = await view.findByTestId("review-failure");
    expect(block.dataset.reviewState).toBe("validation");
    expect(view.getByTestId("review-offending-input").textContent).toContain("src/batch.py");
    expect(view.getByTestId("review-next-action").textContent).toContain(badRequest.nextAction);
    expect(view.queryByTestId("review-retry")).toBeNull();
  });

  it("offers a retry for a socket that never answered, and the retry re-reads", async () => {
    const fetchFn = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("fetch failed"))
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        statusText: "OK",
        json: async () => ({
          state: "refused",
          operation: "read_review_source_content",
          repository_id: "agents-remember",
          refusal: {
            code: "source_content_unresolved",
            detail: "the requested path is not one of the measured changed paths",
            next_action: "expand a path the inventory listed for this generation",
          },
        }),
      } as unknown as Response);
    vi.stubGlobal("fetch", fetchFn);

    const view = mount();

    const failure = await view.findByTestId("review-failure");
    expect(failure.dataset.reviewState).toBe("network");
    fireEvent.click(view.getByTestId("review-retry"));

    await waitFor(() => expect(fetchFn).toHaveBeenCalledTimes(2));
    // The retry renders whatever the read then answers -- here R03's own typed refusal, unchanged.
    await waitFor(() =>
      expect(view.getByTestId("review-source-refusal").textContent).toContain(
        "source_content_unresolved",
      ),
    );
    expect(view.queryByTestId("review-failure")).toBeNull();
  });
});
