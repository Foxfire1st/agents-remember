// R16 (visible structured refusals) at the review client's transport boundary: the route's typed
// refusal is read out of a NON-2xx response, and everything that is not that answer is named.
//
// WHAT THIS EXERCISES. The real client functions (`intentReview`, `intentReviewEntries`,
// `reviewSourceContent`) and the real shared decode (`data/reviewTransport.ts`); only `fetch` is
// stubbed, so the URL, the response status and the body all travel the way the browser's do.
//
// WHERE THE BODIES COME FROM. Every refusal body below is the measured output of the REAL route over
// REAL HTTP in this leaf's evidence run -- `serving_collaborators(config)` ports inside
// `cli.dashboard.create_app`, driven against a real never-initialized leaf enclosure built by the
// shipped test support. The exact bytes are recorded in
// `ar-coordination/temp/icr/evidence-l16-refusals.txt` (`probe-l16-real-route.py`), as `body` (the
// run's own bytes) and `body-normalized`, which replaces the fixture's per-run uuid `repository_id`
// with `<repository_id>`. These fixtures are that normalized body verbatim, and the digests quoted
// below are the run's `sha256-normalized` values.
//
// THE DEFECT THESE CASES CATCH. `getJson` (the shared client for the other serving routes) reads
// only `body.status` and throws on any non-2xx, so every typed review refusal was unreachable: a
// reader saw "404 Not Found" where the route had published a code, a reason, the offending input and
// the action that initializes the dataset. The first two cases are that pair -- the same response,
// read through the two clients -- and the rest pin each distinct state the route can answer with.

import { afterEach, describe, expect, it, vi } from "vitest";

import { FilesApiError, getJson } from "./files";
import type { ReviewSelectorKind } from "./review";
import { intentReview, intentReviewEntries, reviewSourceContent } from "./review";
import { ReviewTransportError, reviewProblemFromCause } from "./reviewTransport";

const REPO = "agents-remember";
const MASTER = "260921_complete-code-and-intent-review";
const LEAF = "260921-ICR-L16";
const SUBJECT = "3c513a59-5e5f-428a-b5e2-7301fcb280f7";

// The real route's 404 for a subject review of a never-initialized task (measured body-normalized,
// sha256-normalized 834c79f474b8a219d5cee66bd9a8e28af5eb619e7aab53b744ac1c4ba4bee736).
const DATASET_ABSENT = {
  state: "refused",
  operation: "read_knowledge_review",
  repository_id: "<repository_id>",
  refusal: {
    code: "candidate_dataset_absent",
    detail: "the resolved baseline dataset is absent, so there is nothing to compare",
    next_action:
      "author the candidate's knowledge in the leaf's disposable knowledge root, and place the " +
      "dataset it forks from in the baseline half if this leaf has one; the surface substitutes no " +
      "other dataset",
    offending_input: "knowledge-candidate.sqlite",
  },
};

// The real route's 404 for the entry read of the same task (measured body-normalized,
// sha256-normalized fdbabc97219c6f0a7b531acbe1032622bbb21f3bbfc441f406f1ee34a2bf828e).
const ENTRIES_DATASET_ABSENT = {
  state: "refused",
  operation: "list_knowledge_review_entries",
  repository_id: "<repository_id>",
  master: MASTER,
  leaf_id: LEAF,
  entries: [],
  refusal: {
    code: "candidate_dataset_absent",
    detail:
      "the resolved baseline dataset is absent, so the pair has nothing to compare; the review " +
      "reads neither of its two halves out of the live coordination tree and substitutes no other " +
      "dataset",
    next_action:
      "author the candidate's knowledge in the leaf's disposable knowledge root, and place the " +
      "dataset it forks from in the baseline half if this leaf has one; the surface substitutes no " +
      "other dataset",
    offending_input: "knowledge-candidate.sqlite",
  },
};

// The real route's 400 for a selector it does not admit (measured body, sha256
// 8a45fcd38844428ee76b25ebd3992737b50b148240d11d4eea2272326765e9d7; no repository id is in this
// body, so its raw and normalized digests are the same).
const BAD_REQUEST = {
  status: "bad-request",
  detail:
    "the review selector names no admitted subject kind; the surface reviews one recorded invariant " +
    "or family identity, or no subject at all when both selector parameters are omitted",
  offendingInput: "latest",
  expected: "invariant, family, or no selector at all",
  nextAction:
    "name selectorKind=invariant|family together with the subject's record id, or omit both to " +
    "review the task's complete source change inventory",
};

// The real route's 503 when the process was composed without the review adapter (measured body,
// sha256 c55ee098da2014aabb8784ae65ca289f0111dbdd18aa70a504553fca1635a690; no repository id in
// this body either).
const UNWIRED = {
  status: "unavailable",
  detail:
    "no review adapter is wired into this process, so the Intent Reviewer cannot resolve a " +
    "candidate; the surface is not served rather than served empty",
  nextAction: "start the dashboard through its composition root, which supplies the review adapter",
};

// The real route's 400 for a port that refused the caller's authority (measured body, sha256
// 3f8a79a5ef046e3f5e4d6f9fc75d217089d7e0ff29a1cb3058df260d0c005f7c; no repository id in this body
// either).
const BAD_PATH = {
  status: "bad-path",
  detail: "repository 'not-admitted' is outside the configured workspace authority",
  nextAction:
    "name a repository the configured workspace authority admits, then reopen the review; these " +
    "routes read no other repository in its place",
};

// The real expansion route's 400 for a path the measured change set does not contain (measured
// body-normalized, sha256-normalized
// 2a8ee681c4654d6331198dbaad454a5d13dffff5b9ff6861d78794f06731da26).
const SOURCE_CONTENT_REFUSED = {
  state: "refused",
  operation: "read_review_source_content",
  repository_id: "<repository_id>",
  refusal: {
    code: "source_content_unresolved",
    detail:
      "the requested path 'src/not-in-this-change-set.py' is not one of the 6 changed path(s) this " +
      "surface measured between the requested trees, so no content was read for it; an entry is " +
      "expanded from the inventory's own measurement and this route reads no path outside it",
    next_action:
      "expand a path the inventory listed for this generation, or reopen the review if the " +
      "generation has moved",
    offending_input: "src/not-in-this-change-set.py",
  },
};

const intentUrl = `${"/api/review/intent"}?repo=${REPO}&master=${MASTER}&leaf=${LEAF}&selectorKind=invariant&selectorId=${SUBJECT}`;

function serving(status: number, body: unknown, statusText = "") {
  const fetchFn = vi.fn(
    async () =>
      ({ ok: status >= 200 && status < 300, status, statusText, json: async () => body }) as unknown as Response,
  );
  vi.stubGlobal("fetch", fetchFn);
  return fetchFn;
}

const failureOf = async (call: () => Promise<unknown>) => {
  try {
    await call();
  } catch (cause) {
    return reviewProblemFromCause(cause);
  }
  throw new Error("the read resolved; a failure was expected");
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the review route's typed answer, whatever the status", () => {
  it("resolves a 404 typed refusal through the review client, refusal intact", async () => {
    const fetchFn = serving(404, DATASET_ABSENT, "Not Found");

    const result = await intentReview(REPO, MASTER, LEAF, "invariant", SUBJECT);

    expect(fetchFn).toHaveBeenCalledWith(intentUrl);
    expect(result.state).toBe("refused");
    expect(result.refusal?.code).toBe("candidate_dataset_absent");
    expect(result.refusal?.detail).toBe(DATASET_ABSENT.refusal.detail);
    expect(result.refusal?.next_action).toBe(DATASET_ABSENT.refusal.next_action);
    expect(result.refusal?.offending_input).toBe("knowledge-candidate.sqlite");
  });

  it("drops the same body through getJson: the shared client's semantics are unchanged", async () => {
    // The negative control for the case above, on the identical response: this is what the review
    // surface used to receive, and it is why the refusal was invisible.
    serving(404, DATASET_ABSENT, "Not Found");

    await expect(getJson(intentUrl)).rejects.toBeInstanceOf(FilesApiError);
    const caught = await getJson(intentUrl).then(
      () => {
        throw new Error("getJson resolved; a throw was expected for this response");
      },
      (cause: unknown) => cause as FilesApiError,
    );
    expect(caught.code).toBe("Not Found");
    expect(caught.message).toBe("404 Not Found");
    expect((caught as { refusal?: unknown }).refusal).toBeUndefined();
  });

  it("resolves the entry read's and the expansion read's typed refusals the same way", async () => {
    serving(404, ENTRIES_DATASET_ABSENT, "Not Found");
    const entries = await intentReviewEntries(REPO, MASTER, LEAF);
    expect(entries.state).toBe("refused");
    expect(entries.entries).toEqual([]);
    expect(entries.refusal?.code).toBe("candidate_dataset_absent");

    serving(400, SOURCE_CONTENT_REFUSED, "Bad Request");
    const opened = await reviewSourceContent(
      REPO,
      MASTER,
      LEAF,
      "src/not-in-this-change-set.py",
      "before",
      "after",
    );
    expect(opened.state).toBe("refused");
    expect(opened.refusal?.code).toBe("source_content_unresolved");
    expect(opened.refusal?.offending_input).toBe("src/not-in-this-change-set.py");
    expect(opened.refusal?.next_action).toBe(SOURCE_CONTENT_REFUSED.refusal.next_action);
  });

  it("still resolves an ordinary 200 typed answer", async () => {
    serving(200, { state: "entries", operation: "list_knowledge_review_entries", entries: [] });
    const entries = await intentReviewEntries(REPO, MASTER, LEAF);
    expect(entries.state).toBe("entries");
    expect(entries.entries).toEqual([]);
  });
});

describe("what is not this route's typed answer", () => {
  it("carries a transport-level refusal body's reason, offending input and next action", async () => {
    serving(400, BAD_REQUEST, "Bad Request");

    // The measured answer is the route's response to a selector KIND the surface does not declare
    // ("latest"), which is exactly the input the server refuses; the client's own type admits only
    // the two kinds this surface reviews, so the cast names the test's intent rather than widening it.
    const failure = await failureOf(() =>
      intentReview(REPO, MASTER, LEAF, "latest" as ReviewSelectorKind, "x"),
    );

    expect(failure.token).toBe("validation");
    expect(failure.code).toBe("bad-request");
    expect(failure.httpStatus).toBe(400);
    expect(failure.detail).toBe(BAD_REQUEST.detail);
    expect(failure.offendingInput).toBe("latest");
    expect(failure.expected).toBe(BAD_REQUEST.expected);
    expect(failure.nextAction).toBe(BAD_REQUEST.nextAction);
  });

  it("names an unwired adapter as unavailable history, with the action that wires it", async () => {
    serving(503, UNWIRED, "Service Unavailable");

    const failure = await failureOf(() => intentReview(REPO, MASTER, LEAF, "invariant", SUBJECT));

    expect(failure.token).toBe("unavailable-history");
    expect(failure.code).toBe("unavailable");
    expect(failure.httpStatus).toBe(503);
    expect(failure.detail).toBe(UNWIRED.detail);
    expect(failure.nextAction).toBe(UNWIRED.nextAction);
  });

  it("names a refused authority as its own state, not as a network or review failure", async () => {
    serving(400, BAD_PATH, "Bad Request");

    const failure = await failureOf(() => intentReview(REPO, MASTER, LEAF, "invariant", SUBJECT));

    expect(failure.token).toBe("authority");
    expect(failure.code).toBe("bad-path");
    expect(failure.detail).toBe(BAD_PATH.detail);
    expect(failure.nextAction).toBe(BAD_PATH.nextAction);
  });

  it("names an HTTP response this route did not produce as unreadable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          ({ ok: false, status: 502, statusText: "Bad Gateway", json: async () => "<html>proxy</html>" }) as unknown as Response,
      ),
    );

    const failure = await failureOf(() => intentReview(REPO, MASTER, LEAF));

    expect(failure.token).toBe("unreadable");
    expect(failure.httpStatus).toBe(502);
    expect(failure.code).toBe("502 Bad Gateway");
    expect(failure.detail).toContain("with no refusal body");
  });

  it("names a socket that never answered as a network failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("fetch failed");
      }),
    );

    const failure = await failureOf(() => intentReview(REPO, MASTER, LEAF));

    expect(failure.token).toBe("network");
    expect(failure.code).toBe("network");
    expect(failure.httpStatus).toBeUndefined();
    expect(failure.detail).toContain("fetch failed");
    // Nothing in this path invents a next action: only the server publishes one.
    expect(failure.nextAction).toBeUndefined();
  });

  it("throws the review transport error, which stays a FilesApiError for existing catchers", async () => {
    serving(503, UNWIRED, "Service Unavailable");

    const caught = await intentReview(REPO, MASTER, LEAF).catch((cause: unknown) => cause);
    expect(caught).toBeInstanceOf(ReviewTransportError);
    expect(caught).toBeInstanceOf(FilesApiError);
    expect((caught as ReviewTransportError).failure.token).toBe("unavailable-history");
    expect((caught as Error).message).toBe("503 unavailable");
  });
});
