// R16 (visible structured refusals) at the mounted surface: the real `ReviewSurface` over the real
// review client, for every state that is not a plain success.
//
// WHAT THIS EXERCISES. `ReviewSurface` is the real component and `intentReview` the real client;
// only `fetch` is stubbed, so each response travels the way the browser's does (status, body, the
// shared decode in `data/reviewTransport.ts`, the component tree). No assertion reads a prop this
// test itself passed: every case asserts what the rendered DOM contains.
//
// WHERE THE VALUES COME FROM. Every refusal body below is the measured output of the REAL route over
// REAL HTTP in this leaf's evidence run (`probe-l16-real-route.py`, recorded in
// `ar-coordination/temp/icr/evidence-l16-refusals.txt`): the production ports inside
// `cli.dashboard.create_app`, against a real never-initialized leaf enclosure. The recorded
// `body-normalized` line replaces the fixture's per-run uuid `repository_id` with
// `<repository_id>`; these fixtures are that normalized body, and the digests below are the run's
// `sha256-normalized` values. The task-context payload is this module's reduced fixture whose
// inventory rows, counts and wording are the six paths that same run measured.
//
// THE DEFECT THESE CASES CATCH. The surface used to catch the client's throw and print
// `the review read failed: 404 Not Found`: the route's typed refusal -- code, reason, offending
// input, the action that initializes the dataset -- was dropped, and a failed re-read erased the
// comparison already on screen. Every refusal case below fails against that surface, and so does the
// retained-generation case.

import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ReviewFailure, ReviewPayload, ReviewResult } from "../../data/review";
import { type ReviewRead, ReviewOutcomeRegion } from "./ReviewOutcome";
import { ReviewSurface } from "./ReviewSurface";

const REPO = "agents-remember";
const MASTER = "260921_complete-code-and-intent-review";
const LEAF = "260921-ICR-L16";
const SUBJECT = "3c513a59-5e5f-428a-b5e2-7301fcb280f7";

// The real route's 404 for a never-initialized task's subject review (measured body-normalized,
// sha256-normalized 834c79f474b8a219d5cee66bd9a8e28af5eb619e7aab53b744ac1c4ba4bee736).
const DATASET_ABSENT_DETAIL = "the resolved baseline dataset is absent, so there is nothing to compare";
const DATASET_ABSENT_NEXT =
  "author the candidate's knowledge in the leaf's disposable knowledge root, and place the dataset " +
  "it forks from in the baseline half if this leaf has one; the surface substitutes no other dataset";
const DATASET_ABSENT: ReviewResult = {
  state: "refused",
  operation: "read_knowledge_review",
  repository_id: "<repository_id>",
  refusal: {
    code: "candidate_dataset_absent",
    detail: DATASET_ABSENT_DETAIL,
    next_action: DATASET_ABSENT_NEXT,
    offending_input: "knowledge-candidate.sqlite",
  },
};

// The real route's 400 for a selector it does not admit (sha256
// 8a45fcd38844428ee76b25ebd3992737b50b148240d11d4eea2272326765e9d7), its 503 for a process composed
// without the adapter (sha256 c55ee098da2014aabb8784ae65ca289f0111dbdd18aa70a504553fca1635a690) and
// its 400 for a refused authority (sha256
// 3f8a79a5ef046e3f5e4d6f9fc75d217089d7e0ff29a1cb3058df260d0c005f7c); none of the three carries a
// repository id, so their raw and normalized digests are the same.
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
const UNWIRED = {
  status: "unavailable",
  detail:
    "no review adapter is wired into this process, so the Intent Reviewer cannot resolve a " +
    "candidate; the surface is not served rather than served empty",
  nextAction: "start the dashboard through its composition root, which supplies the review adapter",
};
const BAD_PATH = {
  status: "bad-path",
  detail: "repository 'not-admitted' is outside the configured workspace authority",
  nextAction:
    "name a repository the configured workspace authority admits, then reopen the review; these " +
    "routes read no other repository in its place",
};

const INVENTORY_DETAIL =
  "the two requested code trees differ at 6 path(s); every one is listed with the change status Git " +
  "reported for it, whether or not its content can be rendered";

// A task-context answer from the same run: no subject was compared, the knowledge halves are absent,
// and the SOURCE inventory is measured in full -- which is what makes source inspection available
// when only intent is not (the measured `staleness: not_compared` and
// `source_inspection_available: true` of that run).
function taskContextPayload(): ReviewPayload {
  return {
    surface_version: "knowledge-review-surface/1",
    // The measured body's own repository id is the per-run fixture uuid; the reduced fixture keeps
    // the same placeholder the recorded `body-normalized` line carries.
    candidate: { repository_id: "<repository_id>", master: MASTER, leaf_id: LEAF, task_ref: MASTER },
    knowledge: {
      invariant_ids: [],
      family_ids: [],
      before_statement: {
        state: "unresolved",
        language: "text",
        detail:
          "no subject was selected and the resolved baseline dataset is absent " +
          "(knowledge-candidate.sqlite), so no knowledge operand was compared; the Source pane " +
          "carries the complete source change inventory of the bound pair, which does not depend on " +
          "knowledge availability",
      },
      after_statement: {
        state: "unresolved",
        language: "text",
        detail:
          "no subject was selected and the resolved baseline dataset is absent " +
          "(knowledge-candidate.sqlite), so no knowledge operand was compared; the Source pane " +
          "carries the complete source change inventory of the bound pair, which does not depend on " +
          "knowledge availability",
      },
      before_conditions: [],
      after_conditions: [],
      revision_groups: [],
      field_changes: [],
      authored_effects: [],
      signals: [],
      assessments: [],
      unresolved: [],
      selection_state: "task_context",
      selection_detail:
        "no subject was selected and the resolved baseline dataset is absent " +
        "(knowledge-candidate.sqlite), so no knowledge operand was compared; the Source pane " +
        "carries the complete source change inventory of the bound pair, which does not depend on " +
        "knowledge availability",
    },
    source: {
      inventory: {
        state: "measured",
        entries: [
          { path: ".gitignore", status: "added", content: "text", mode_change: false },
          { path: "src/batch.py", status: "modified", content: "text", mode_change: false },
          { path: "src/retry_interval.py", status: "added", content: "text", mode_change: false },
          { path: "src/staged_addition.py", status: "added", content: "text", mode_change: false },
          { path: "src/synchronization.py", status: "deleted", content: "text", mode_change: false },
          { path: "src/unmapped.py", status: "added", content: "text", mode_change: false },
        ],
        listed_total: 6,
        detail: INVENTORY_DETAIL,
        partial: false,
        command: "git diff --raw -z --no-renames <before> <after>",
        before_code_tree_id: "88f5c3c763d96791f193cf49a438065f48e05530",
        after_code_tree_id: "4bca9de2059271a1c9ba46e5196402631bf1d334",
        unrepresentable_paths: [],
      },
      locations: [],
      remaining: [],
      unattributed_changed_paths: [],
      attributed_changed_paths: [],
      unresolved: [],
    },
    evidence: {
      evidence_state: "none_recorded",
      assessment_state: "unassessed",
      evidence_links: [],
      observations: [],
      assessments: [],
      source_inspection_available: true,
      unresolved: [],
    },
    staleness: {
      state: "not_compared",
      statement:
        "no knowledge subject was selected for this review, so no comparison binding exists to be " +
        "current or stale; the Source pane carries the complete inventory of the bound source pair",
      moved: [],
    },
    submission: {
      state: "unavailable",
      reason:
        "this increment mounts no serving route that publishes an assessment, so the surface " +
        "displays only and does not grow a private write path to compensate",
      next_action:
        "publish an assessment through the existing curator authority's publication action, which " +
        "supplies the author, the role and the authority provenance",
      proposed_dispositions: ["concern_found", "no_concern_found", "unresolved"],
      none_is_approval: true,
    },
    limitations: ["limitation:no_knowledge_subject_selected"],
  };
}

const reviewed = (payload: ReviewPayload): ReviewResult => ({
  state: "review",
  operation: "read_knowledge_review",
  repository_id: REPO,
  payload,
});

// A task-context answer that measured nothing: a measured inventory of zero paths, no comparison,
// no evidence and no assessment. It is a known-empty RESULT, not a missing one.
function emptyPayload(): ReviewPayload {
  const payload = taskContextPayload();
  return {
    ...payload,
    source: {
      ...payload.source,
      inventory: {
        ...payload.source.inventory,
        entries: [],
        listed_total: 0,
        detail: "the bound pair differs at no path",
      },
    },
  };
}

function response(status: number, body: unknown, statusText = "") {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText,
    json: async () => body,
  } as unknown as Response;
}

function serving(status: number, body: unknown, statusText = "") {
  const fetchFn = vi.fn(async () => response(status, body, statusText));
  vi.stubGlobal("fetch", fetchFn);
  return fetchFn;
}

const mount = () =>
  render(<ReviewSurface repo={REPO} master={MASTER} leaf={LEAF} onBack={vi.fn()} />);
const mountSubject = () =>
  render(
    <ReviewSurface
      repo={REPO}
      master={MASTER}
      leaf={LEAF}
      selectorKind="invariant"
      selectorId={SUBJECT}
      onBack={vi.fn()}
    />,
  );

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("the review surface's read states", () => {
  it("shows the read as in flight before any answer arrives", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise<Response>(() => {})),
    );

    const view = mount();

    const loading = view.getByTestId("review-loading");
    expect(loading.dataset.reviewState).toBe("loading");
    expect(view.queryByTestId("review-refusal")).toBeNull();
    expect(view.queryByTestId("review-known-empty")).toBeNull();
  });

  it("shows a never-initialized refusal with its reason, offending input and next action", async () => {
    serving(404, DATASET_ABSENT, "Not Found");

    const view = mountSubject();

    const block = await view.findByTestId("review-refusal");
    expect(block.dataset.reviewState).toBe("not-initialized");
    expect(block.dataset.reviewCode).toBe("candidate_dataset_absent");
    expect(block.textContent).toContain(DATASET_ABSENT_DETAIL);
    expect(view.getByTestId("review-offending-input").textContent).toContain(
      "knowledge-candidate.sqlite",
    );
    expect(view.getByTestId("review-next-action").textContent).toContain(DATASET_ABSENT_NEXT);
    // The pre-existing rendering it replaces: a generic message carrying only the HTTP status.
    expect(view.queryByTestId("review-error")).toBeNull();
    expect(view.queryByTestId("review-surface")?.textContent).not.toContain("404");
  });

  it("keeps source inspection reachable when only intent is unavailable, on request", async () => {
    const fetchFn = vi
      .fn()
      .mockResolvedValueOnce(response(404, DATASET_ABSENT, "Not Found"))
      .mockResolvedValueOnce(response(200, reviewed(taskContextPayload())));
    vi.stubGlobal("fetch", fetchFn);

    const view = mountSubject();
    fireEvent.click(await view.findByTestId("review-source-instead"));

    // The second read is the SAME task context with no subject: the client names no dataset, and the
    // refusal is still on screen beside the inventory it explains.
    await waitFor(() => expect(fetchFn).toHaveBeenCalledTimes(2));
    expect(String(fetchFn.mock.calls[1][0])).not.toContain("selectorKind");
    const note = await view.findByTestId("review-source-instead-note");
    expect(note.textContent).toContain("candidate_dataset_absent");
    expect(note.textContent).toContain(DATASET_ABSENT_NEXT);
    // The owner named an offending input, so it stays on screen after the reader changes the
    // question: the refusal's whole actionable content survives the click.
    expect(note.textContent).toContain("offending input: knowledge-candidate.sqlite");
    const inventory = view.getByTestId("review-inventory");
    expect(inventory.dataset.inventoryState).toBe("measured");
    expect(inventory.textContent).toContain("6 listed path(s)");
    const rows = view.getAllByTestId("review-inventory-entry").map((row) => row.textContent ?? "");
    expect(rows.some((row) => row.includes("src/retry_interval.py") && row.includes("added"))).toBe(true);
    expect(rows.some((row) => row.includes("src/synchronization.py") && row.includes("deleted"))).toBe(
      true,
    );
  });

  it("says known empty for a measured empty answer and never for a failure", async () => {
    serving(200, reviewed(emptyPayload()));

    const view = mount();

    const note = await view.findByTestId("review-known-empty");
    expect(note.dataset.reviewState).toBe("known-empty");
    expect(view.getByTestId("review-inventory").textContent).toContain("0 listed path(s)");
    expect(view.queryByTestId("review-refusal")).toBeNull();
    expect(view.queryByTestId("review-failure")).toBeNull();
  });

  it("keeps the unavailable-adapter, validation and authority states apart", async () => {
    serving(503, UNWIRED, "Service Unavailable");
    const unwired = mount();
    const unavailable = await unwired.findByTestId("review-failure");
    expect(unavailable.dataset.reviewState).toBe("unavailable-history");
    expect(unavailable.dataset.reviewCode).toBe("unavailable");
    expect(unavailable.textContent).toContain(UNWIRED.nextAction);
    // A composed-without-adapter process is not retried into existence by the browser: the route
    // published the action that fixes it, and no retry control is offered beside a typed refusal.
    expect(unwired.queryByTestId("review-retry")).toBeNull();
    cleanup();

    serving(400, BAD_REQUEST, "Bad Request");
    const invalid = mountSubject();
    const validation = await invalid.findByTestId("review-failure");
    expect(validation.dataset.reviewState).toBe("validation");
    expect(validation.dataset.reviewCode).toBe("bad-request");
    expect(validation.textContent).toContain(BAD_REQUEST.detail);
    expect(invalid.getByTestId("review-offending-input").textContent).toContain("latest");
    expect(invalid.getByTestId("review-next-action").textContent).toContain(BAD_REQUEST.nextAction);
    cleanup();

    serving(400, BAD_PATH, "Bad Request");
    const refused = mountSubject();
    const authority = await refused.findByTestId("review-failure");
    expect(authority.dataset.reviewState).toBe("authority");
    expect(authority.dataset.reviewCode).toBe("bad-path");
    expect(refused.getByTestId("review-next-action").textContent).toContain(BAD_PATH.nextAction);
    expect(refused.queryByTestId("review-refusal")).toBeNull();
  });

  it("offers an explicit retry for a network failure, and the retry renders the answer", async () => {
    const fetchFn = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("fetch failed"))
      .mockResolvedValueOnce(response(200, reviewed(taskContextPayload())));
    vi.stubGlobal("fetch", fetchFn);

    const view = mountSubject();
    const failure = await view.findByTestId("review-failure");
    expect(failure.dataset.reviewState).toBe("network");
    expect(failure.textContent).toContain("could not reach the server");

    fireEvent.click(view.getByTestId("review-retry"));

    await waitFor(() => expect(fetchFn).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(view.queryByTestId("review-failure")).toBeNull());
    expect(view.getByTestId("review-inventory").textContent).toContain("6 listed path(s)");
    expect(view.queryByTestId("review-known-empty")).toBeNull();
  });

  it("keeps a subject-level refusal distinct, under its own code", async () => {
    serving(
      400,
      {
        state: "refused",
        operation: "read_knowledge_review",
        repository_id: REPO,
        refusal: {
          code: "comparison_refused",
          detail: "the review-matrix view refused the candidate: the snapshot is unreadable",
          next_action: "repair the candidate dataset, then reopen the review",
        },
      },
      "Bad Request",
    );

    const view = mountSubject();

    const block = await view.findByTestId("review-refusal");
    expect(block.dataset.reviewState).toBe("domain-refused");
    expect(block.dataset.reviewCode).toBe("comparison_refused");
    expect(view.getByTestId("review-next-action").textContent).toContain(
      "repair the candidate dataset, then reopen the review",
    );
  });

  it("names an answer whose state it does not admit, instead of rendering it as a review", async () => {
    serving(200, { state: "something-else", operation: "read_knowledge_review" });

    const view = mountSubject();

    const failure = await view.findByTestId("review-failure");
    expect(failure.dataset.reviewState).toBe("unreadable");
    expect(failure.textContent).toContain('state "something-else"');
    // The surface claims no empty review for an answer it could not read.
    expect(view.queryByTestId("review-known-empty")).toBeNull();
    expect(view.queryByTestId("review-inventory")).toBeNull();
  });

  it("never renders a previous target's comparison under a new target's header", async () => {
    // F4: the retained generation belongs to the question it was read for. Target A succeeds, then
    // target B fails -- and nothing read for A may appear under B's header.
    const first = reviewed(taskContextPayload());
    const fetchFn = vi
      .fn()
      .mockResolvedValueOnce(response(200, first))
      .mockRejectedValueOnce(new TypeError("fetch failed"));
    vi.stubGlobal("fetch", fetchFn);

    const view = mountSubject();
    await waitFor(() =>
      expect(view.getByTestId("review-inventory").textContent).toContain("6 listed path(s)"),
    );

    view.rerender(
      <ReviewSurface
        repo={REPO}
        master={MASTER}
        leaf={LEAF}
        selectorKind="invariant"
        selectorId="a-different-subject"
        onBack={vi.fn()}
      />,
    );

    const failure = await view.findByTestId("review-failure");
    expect(failure.dataset.reviewState).toBe("network");
    expect(view.getByTestId("review-subject").textContent).toContain("a-different-subject");
    // The header above names B; A's comparison, A's inventory and any retention note are gone.
    expect(view.queryByTestId("review-inventory")).toBeNull();
    expect(view.queryByTestId("review-retained-generation")).toBeNull();
    expect(view.queryByTestId("review-known-empty")).toBeNull();
    expect(view.queryByTestId("review-surface")?.getAttribute("data-comparison")).toBeNull();
  });
});

// F3 (fix round): the two statements about a payload that measured nothing are mutually exclusive.
// Driven at the region -- the shipped component the surface renders its outcome through -- because
// the case they must not both appear in (a failed read over a RETAINED known-empty generation)
// needs a same-target re-read, which the shipped props cannot produce (Cockpit remounts the takeover
// per target); the region is where the rule lives, so the region is where it is pinned.
describe("the outcome region's two statements about a measured-empty payload", () => {
  const failure: ReviewFailure = {
    token: "network",
    code: "network",
    detail: "the review read could not reach the server: fetch failed",
  };

  const region = (read: ReviewRead, shown: ReviewPayload | null, lastCoherent: ReviewPayload | null) =>
    render(
      <ReviewOutcomeRegion
        read={read}
        instead={null}
        shown={shown}
        lastCoherent={lastCoherent}
        onRetry={vi.fn()}
      />,
    );

  const statements = (view: ReturnType<typeof region>) => ({
    knownEmpty: view.queryByTestId("review-known-empty")?.textContent ?? null,
    retained: view.queryByTestId("review-retained-generation")?.textContent ?? null,
  });

  it("states a retained known-empty once, as the measured result it is, and never denies it", () => {
    const empty = emptyPayload();
    const view = region({ phase: "failed", problem: failure }, empty, empty);

    const printed = statements(view);
    expect(printed.knownEmpty).toContain("known empty");
    expect(printed.retained).toBeNull();
    expect(view.queryByTestId("review-failure")).not.toBeNull();
    cleanup();
  });

  it("labels a retained real comparison and denies no emptiness for it", () => {
    const real = taskContextPayload();
    const view = region({ phase: "failed", problem: failure }, real, real);

    const printed = statements(view);
    expect(printed.retained).toContain("no empty review is claimed for it");
    expect(printed.knownEmpty).toBeNull();
    cleanup();
  });

  it("says nothing of either kind for a written review, and only known-empty for an empty answer", () => {
    const real = taskContextPayload();
    const written = region({ phase: "reviewed", payload: real }, real, null);
    expect(statements(written)).toEqual({ knownEmpty: null, retained: null });
    cleanup();

    const empty = emptyPayload();
    const answered = region({ phase: "reviewed", payload: empty }, empty, null);
    expect(statements(answered).knownEmpty).toContain("known empty");
    expect(statements(answered).retained).toBeNull();
    cleanup();
  });
});
