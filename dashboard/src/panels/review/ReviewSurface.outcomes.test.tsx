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

import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ReviewFailure, ReviewPayload, ReviewResult } from "../../data/review";
import { type ReviewRead, ReviewOutcomeRegion } from "./ReviewOutcome";
import { ReviewSurface } from "./ReviewSurface";

// Comparison-focused cases isolate the catalogue. The normal catalogue-to-review journey is
// exercised through both real readers in ReviewSurface.navigation.test.tsx.
vi.mock("../../data/useReviewCatalogue", () => ({
  useReviewCatalogue: () => ({
    loading: false, entries: [], empty: true, stale: false, facts: "test", refresh: () => undefined,
  }),
}));

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
    expect(inventory.textContent).toContain("6 changed files");
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
    expect(view.getByTestId("review-inventory").textContent).toContain("0 changed files");
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
    expect(view.getByTestId("review-inventory").textContent).toContain("6 changed files");
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
      expect(view.getByTestId("review-inventory").textContent).toContain("6 changed files"),
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
    // The header above names B; A's comparison, A's reading and any retention note are gone. The
    // failure is stated in the reading area, labelled with B, while the task's own shell -- its
    // complete source change inventory and navigation, which belong to no subject -- stays usable
    // (a failed selection does not destroy available navigation).
    expect(failure.closest('[data-testid="review-reading-problem"]')?.getAttribute("data-problem-subject")).toBe(
      "invariant:a-different-subject",
    );
    expect(view.getByTestId("review-inventory").textContent).toContain("6 changed files");
    expect(view.queryByTestId("review-center")).toBeNull();
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

// ── ICR-R17: the refresh control, the carried binding identity, and the read race ───────────────
//
// WHAT THIS EXERCISES. `ReviewSurface` is the real component and `intentReview` the real client, so
// the query string asserted below is the one the browser builds (the same posture as the cases
// above); only `fetch` is stubbed.
//
// THE DEFECTS THESE CASES CATCH. (1) A reader looking at a comparison had no way to ask for it again,
// and nothing carried the identity they were looking at -- so a candidate that moved while the panel
// stayed open was never compared against what was on screen. (2) A response that answered an EARLIER
// selectable target could still write the read state after the target had changed: a slow subject A
// answer landing after subject B was selected replaced B's comparison with A's. The second case below
// fails against the surface this leaf started from.

// One subject review, with the comparison binding the surface reads its identity from. The panes are
// the reduced fixture above; the binding is this case's own input, because a generation identity is
// what the refresh carries and the assertion is about that identity.
function subjectPayload(binding: string, stale = false): ReviewPayload {
  const payload = taskContextPayload();
  return {
    ...payload,
    comparison: {
      reference: binding,
      policy_version: "knowledge-diff/v1",
      binding_digest: binding,
      selector_digest: "f".repeat(64),
      before_snapshot_digest: "a".repeat(64),
      after_snapshot_digest: "b".repeat(64),
      before_code_tree_id: payload.source.inventory.before_code_tree_id,
      after_code_tree_id: payload.source.inventory.after_code_tree_id,
      knowledge_compared: true,
    },
    staleness: stale
      ? {
          state: "stale",
          statement: "Candidate changed — open a new comparison",
          previous_comparison_ref: "1".repeat(64),
          moved: ["comparison-binding"],
        }
      : { state: "current", statement: "the comparison is current", moved: [] },
  };
}

function servedJson(url: string): unknown {
  if (url.includes("previousBindingDigest")) return reviewed(subjectPayload("2".repeat(64), true));
  return reviewed(subjectPayload("1".repeat(64)));
}

describe("the review surface's refresh control and read race (ICR-R17)", () => {
  it("re-reads the same question carrying the binding identity on screen, and names what moved", async () => {
    const fetchFn = vi.fn(async (url: string) => response(200, servedJson(url)));
    vi.stubGlobal("fetch", fetchFn);

    const view = mountSubject();
    await waitFor(() =>
      expect(view.getByTestId("review-surface").dataset.comparison).toBe("1".repeat(64)),
    );
    const first = String(fetchFn.mock.calls[0][0]);
    expect(first).not.toContain("previousBindingDigest");
    expect(view.queryByTestId("review-generation-notice")).toBeNull();

    fireEvent.click(view.getByTestId("review-refresh"));

    await waitFor(() => expect(fetchFn.mock.calls).toHaveLength(2));
    const second = String(fetchFn.mock.calls[1][0]);
    // The whole question is repeated -- the same task context, the same subject, the same record --
    // and the identity the reader was looking at travels as the comparison this read replaces.
    for (const part of [`repo=${REPO}`, `master=${MASTER}`, `leaf=${LEAF}`, "selectorId=" + SUBJECT]) {
      expect(second).toContain(part);
    }
    expect(second).toContain(`previousBindingDigest=${"1".repeat(64)}`);

    const notice = await view.findByTestId("review-generation-notice");
    expect(notice.dataset.generationState).toBe("superseded");
    expect(notice.dataset.previousBinding).toBe("1".repeat(64));
    expect(notice.dataset.currentBinding).toBe("2".repeat(64));
    // The panes below were replaced whole with the candidate's current comparison, and the previous
    // identity is named rather than presented as the generation on screen.
    expect(view.getByTestId("review-surface").dataset.comparison).toBe("2".repeat(64));
    // FIX ROUND 2 (L17-R2-F2): the sentence must describe the panes it sits beside. It used to claim
    // "the panes below are the comparison you were reading (Y)", which the DOM contradicts: a `stale`
    // answer publishes the CURRENT comparison and merely NAMES the carried identity as the previous
    // input. The claim and the attribute are checked against each other here, so the sentence cannot
    // drift from what is rendered again.
    expect(notice.textContent).toContain(`was ${"1".repeat(64)}, is now ${"2".repeat(64)}`);
    expect(notice.textContent).not.toContain("the panes below are the comparison you were reading");
    expect(notice.textContent).toContain("current");
    expect(notice.dataset.currentBinding).toBe(
      view.getByTestId("review-surface").dataset.comparison,
    );
    // The server's own staleness statement is rendered beside it, so the reader sees both the
    // compared identities and the rule that disabled submission against the moved one.
    expect(view.getByTestId("review-stale").textContent).toContain("previous input");
  });

  it("keeps the labelled old comparison and its error when the refresh fails", async () => {
    const fetchFn = vi
      .fn()
      .mockResolvedValueOnce(response(200, reviewed(subjectPayload("1".repeat(64)))))
      .mockRejectedValueOnce(new TypeError("fetch failed"));
    vi.stubGlobal("fetch", fetchFn);

    const view = mountSubject();
    await waitFor(() =>
      expect(view.getByTestId("review-surface").dataset.comparison).toBe("1".repeat(64)),
    );

    fireEvent.click(view.getByTestId("review-refresh"));

    const failure = await view.findByTestId("review-failure");
    expect(failure.dataset.reviewState).toBe("network");
    // The generation the reader was reading is still on screen, still labelled with its own identity
    // -- a failed refresh is not an empty panel, and it is not the new generation either.
    expect(view.getByTestId("review-surface").dataset.comparison).toBe("1".repeat(64));
    // FIX ROUND 1 (L17-F2): this case USED TO assert `generationState === "current"` here, which
    // pinned the defect: the refresh exists to answer "has the candidate moved?", and a read that
    // never reached the server has answered nothing. Claiming "still current" beside the failure
    // block asserts a measurement nobody made -- the rule this module's own header states. The
    // identity the reader carried is not rendered as a claim at all until a read answers for it.
    expect(view.queryByTestId("review-generation-notice")).toBeNull();
    // The control is offered again, so a reader whose network flapped can ask once more.
    expect(view.getByTestId("review-refresh")).toBeDefined();
  });

  it("never renders a slow earlier subject's answer over the subject selected now", async () => {
    // THE BOUNDARY EXAMPLE: "a slow response for subject A cannot replace newly selected B". A's
    // answer is held open until after B has been selected AND answered.
    let releaseSlow: (() => void) | undefined;
    const fetchFn = vi.fn(async (url: string) => {
      if (url.includes("subject-slow")) {
        return await new Promise<Response>((resolve) => {
          releaseSlow = () => resolve(response(200, reviewed(subjectPayload("a".repeat(64)))));
        });
      }
      return response(200, reviewed(subjectPayload("b".repeat(64))));
    });
    vi.stubGlobal("fetch", fetchFn);

    const view = render(
      <ReviewSurface
        repo={REPO}
        master={MASTER}
        leaf={LEAF}
        selectorKind="invariant"
        selectorId="subject-slow"
        onBack={vi.fn()}
      />,
    );
    expect(view.getByTestId("review-loading")).toBeDefined();

    view.rerender(
      <ReviewSurface
        repo={REPO}
        master={MASTER}
        leaf={LEAF}
        selectorKind="invariant"
        selectorId="subject-newer"
        onBack={vi.fn()}
      />,
    );

    await waitFor(() =>
      expect(view.getByTestId("review-surface").dataset.comparison).toBe("b".repeat(64)),
    );
    expect(view.getByTestId("review-subject").textContent).toContain("subject-newer");

    // The superseded answer arrives last. It must not win: neither its comparison nor its header.
    await act(async () => {
      releaseSlow?.();
      await Promise.resolve();
    });

    expect(view.getByTestId("review-surface").dataset.comparison).toBe("b".repeat(64));
    expect(view.getByTestId("review-subject").textContent).toContain("subject-newer");
    expect(view.getByTestId("review-subject").textContent).not.toContain("subject-slow");
  });

  it("renders no generation claim when the subject change and the refresh land in one flush (L17-R2-F1)", async () => {
    // THE DEFECT THIS CATCHES. The notice's own derivation checked the read number but not the
    // question key. When the subject change and the reader's refresh reach the SAME React flush, the
    // refresh captures subject A's still-retained identity, and the single effect run that follows
    // asks for B and consumes exactly that read number -- so `carriedHere` held, and the surface
    // announced "the candidate published a new comparison (B) — the panes below are the comparison
    // you were reading (A)", about two identities that belong to two different subjects. The REQUEST
    // carried nothing (the key check in `startRead` held) and the server answered `current`: the
    // client invented the publication. The key is now checked at BOTH sites.
    const seen: string[] = [];
    const fetchFn = vi.fn(async (url: string) => {
      seen.push(url);
      if (url.includes("previousBindingDigest")) {
        return response(200, reviewed(subjectPayload("f".repeat(64), true)));
      }
      return response(200, reviewed(subjectPayload(url.includes("subject-b") ? "b".repeat(64) : "a".repeat(64))));
    });
    vi.stubGlobal("fetch", fetchFn);

    const view = render(
      <ReviewSurface
        repo={REPO}
        master={MASTER}
        leaf={LEAF}
        selectorKind="invariant"
        selectorId="subject-a"
        onBack={vi.fn()}
      />,
    );
    await waitFor(() =>
      expect(view.getByTestId("review-surface").dataset.comparison).toBe("a".repeat(64)),
    );

    // ONE flush: the subject changes and the reader clicks refresh before the effect that would clear
    // the retained payload has run.
    await act(async () => {
      view.rerender(
        <ReviewSurface
          repo={REPO}
          master={MASTER}
          leaf={LEAF}
          selectorKind="invariant"
          selectorId="subject-b"
          onBack={vi.fn()}
        />,
      );
      fireEvent.click(view.getByTestId("review-refresh"));
    });

    await waitFor(() =>
      expect(view.getByTestId("review-surface").dataset.comparison).toBe("b".repeat(64)),
    );
    // Exactly two reads, and the B read carried nothing -- the server never made this claim, so the
    // surface must not render one.
    expect(seen.filter((url) => url.startsWith("/api/review/intent"))).toHaveLength(2);
    expect(String(seen[1])).toContain("selectorId=subject-b");
    expect(String(seen[1])).not.toContain("previousBindingDigest");
    await act(async () => {
      await Promise.resolve();
    });
    expect(view.queryByTestId("review-generation-notice")).toBeNull();
    expect(view.getByTestId("review-subject").textContent).toContain("subject-b");
  });

  it("carries the identity into a read that replaces it, and into no other question (L17-F1)", async () => {
    // THE DEFECT THIS CATCHES. `carried` used to be sticky: once a refresh set it, EVERY later read
    // sent it -- a different subject, a different leaf, a recorded read -- so selecting another
    // subject produced a false "the candidate published a new comparison" notice, the server's
    // `stale` answer beside it, and submission disabled for a subject the reader merely selected.
    // The identity is the previous input of ONE question, so it travels only while the question on
    // screen is the one it was displayed under.
    const seen: string[] = [];
    const fetchFn = vi.fn(async (url: string) => {
      seen.push(url);
      if (url.includes("previousBindingDigest")) {
        return response(200, reviewed(subjectPayload("2".repeat(64), true)));
      }
      return response(200, reviewed(subjectPayload("1".repeat(64))));
    });
    vi.stubGlobal("fetch", fetchFn);

    const view = render(
      <ReviewSurface
        repo={REPO}
        master={MASTER}
        leaf={LEAF}
        selectorKind="invariant"
        selectorId="subject-a"
        onBack={vi.fn()}
      />,
    );
    await waitFor(() =>
      expect(view.getByTestId("review-surface").dataset.comparison).toBe("1".repeat(64)),
    );

    // (c) The refresh of the displayed subject DOES carry its identity -- that is the whole point.
    fireEvent.click(view.getByTestId("review-refresh"));
    await waitFor(() => expect(fetchFn.mock.calls).toHaveLength(2));
    expect(String(seen[1])).toContain(`selectorId=subject-a`);
    expect(String(seen[1])).toContain(`previousBindingDigest=${"1".repeat(64)}`);
    await view.findByTestId("review-generation-notice");

    // (a) A DIFFERENT subject is a different question: its read carries nothing, and no generation
    // claim survives the change.
    view.rerender(
      <ReviewSurface
        repo={REPO}
        master={MASTER}
        leaf={LEAF}
        selectorKind="invariant"
        selectorId="subject-b"
        onBack={vi.fn()}
      />,
    );
    await waitFor(() => expect(fetchFn.mock.calls).toHaveLength(3));
    const third = String(seen[2]);
    expect(third).toContain("selectorId=subject-b");
    expect(third).not.toContain("previousBindingDigest");
    await waitFor(() =>
      expect(view.getByTestId("review-surface").dataset.comparison).toBe("1".repeat(64)),
    );
    expect(view.queryByTestId("review-generation-notice")).toBeNull();
    expect(view.queryByTestId("review-stale")).toBeNull();
  });

  it("never carries a live identity into a recorded read, nor a recorded one into a live read (L17-F1)", async () => {
    // The same rule at the record dimension: `history="recorded"` is a read of the leaf's durable
    // generation, and a live comparison identity is not its previous input. A frozen link labelled
    // stale against a live identity is exactly the false statement this guards.
    const seen: string[] = [];
    const fetchFn = vi.fn(async (url: string) => {
      seen.push(url);
      if (url.includes("previousBindingDigest")) {
        return response(200, reviewed(subjectPayload("2".repeat(64), true)));
      }
      return response(200, reviewed(subjectPayload("1".repeat(64))));
    });
    vi.stubGlobal("fetch", fetchFn);

    const view = render(
      <ReviewSurface
        repo={REPO}
        master={MASTER}
        leaf={LEAF}
        selectorKind="invariant"
        selectorId="subject-a"
        onBack={vi.fn()}
      />,
    );
    await waitFor(() =>
      expect(view.getByTestId("review-surface").dataset.comparison).toBe("1".repeat(64)),
    );
    fireEvent.click(view.getByTestId("review-refresh"));
    await waitFor(() => expect(fetchFn.mock.calls).toHaveLength(2));
    expect(String(seen[1])).toContain(`previousBindingDigest=${"1".repeat(64)}`);

    // The same subject read from the leaf's own record instead: a different question, nothing carried.
    view.rerender(
      <ReviewSurface
        repo={REPO}
        master={MASTER}
        leaf={LEAF}
        selectorKind="invariant"
        selectorId="subject-a"
        history="recorded"
        onBack={vi.fn()}
      />,
    );
    await waitFor(() => expect(fetchFn.mock.calls).toHaveLength(3));
    const recorded = String(seen[2]);
    expect(recorded).toContain("history=recorded");
    expect(recorded).not.toContain("previousBindingDigest");
    expect(view.queryByTestId("review-stale")).toBeNull();

    // And back again: the recorded read answered, so the live read that follows is a new question
    // too -- nothing recorded is dragged into it.
    view.rerender(
      <ReviewSurface
        repo={REPO}
        master={MASTER}
        leaf={LEAF}
        selectorKind="invariant"
        selectorId="subject-a"
        onBack={vi.fn()}
      />,
    );
    await waitFor(() => expect(fetchFn.mock.calls).toHaveLength(4));
    const live = String(seen[3]);
    expect(live).not.toContain("history=");
    expect(live).not.toContain("previousBindingDigest");
    expect(view.queryByTestId("review-generation-notice")).toBeNull();
  });

  it("renders the boundary's own sentence when it could not compare the declared identities (L23)", async () => {
    // ICR-R23@v1: the raw-Git identity boundary reports `not-measured` when it could not take its
    // comparison at all -- a checkout that left its declared branch, a recorded object that is gone,
    // a generation that could not be read. The mounted line is the only place the reader meets this
    // surface's own sentence, so it must not read as an ordinary current review: the boundary's
    // sentence is rendered, and no previous input is named, because nothing was observed to move.
    const payload = subjectPayload("1".repeat(64));
    payload.staleness = {
      state: "not-measured",
      statement:
        "this boundary did not compare every declared identity of the reviewed generation: " +
        "code-work-branch was not compared: the code worktree is on super, not on the declared " +
        "work branch ar/icr-r01-l1",
      moved: [],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => response(200, reviewed(payload))),
    );

    const view = mountSubject();

    const unmeasured = await view.findByTestId("review-staleness-unmeasured");
    expect(unmeasured.textContent).toContain("did not compare every declared identity");
    expect(unmeasured.textContent).toContain("not on the declared work branch");
    // Nothing claims a previous input, and nothing claims the comparison is current.
    expect(unmeasured.textContent).not.toContain("previous input");
    expect(unmeasured.textContent).not.toContain("current comparison");
    expect(view.queryByTestId("review-stale")).toBeNull();
  });
});
