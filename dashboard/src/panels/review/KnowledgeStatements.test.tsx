// R06 (one-sided knowledge statements) at the real renderer: the Intent Reviewer surface over the
// intent-review transport, for a knowledge addition, a removal, and the states that are not a known
// absence.
//
// WHAT THIS EXERCISES. `ReviewSurface` is the real component and `intentReview` is the real client:
// only `fetch` is stubbed, so the payload below travels the same route the browser's does (JSON in,
// `getJson` decode, component tree, `KnowledgeStatements`, and the shipped `DiffPane`/`FilePane`
// CodeMirror primitives, which render in jsdom -- see `src/test/setup.ts`). Nothing here renders a
// hand-rolled stand-in for the statement area, and no assertion reads a prop the test itself passed:
// every case asserts what the rendered DOM contains.
//
// WHERE THE VALUES COME FROM. The statements, the `absent`/`unresolved` details and the field rows
// below are the measured output of the real composition for the addition, the removal and the
// acceptance-reference transition, asserted on the server side by
// `mcp/tests/test_knowledge_review_one_sided_statements.py` (same statements, same details, same
// `acceptance_ref` and `provenance` rows). That test is the production-composition half of this
// requirement; this module is the renderer half. A change to either half's contract fails in one of
// the two. The `binary` detail is the exception and is labelled as such at the constant: `binary` is a
// state the review vocabulary declares, and no shipped composition emits it (measured: `"binary"`
// appears in `models/knowledge/review.py`'s `ReviewSideState` and in the source-inventory content
// classification, and in no statement-side builder). Its case is a declared-state case, not a witness
// of production output.
//
// THE DEFECT THESE CASES CATCH. The pane used to draw its diff only when *both* statements were
// `present`, while the side line returned null for the present side: an added or removed statement
// rendered as two muted lines and no statement text at all. Every `absent`/`present` case below fails
// against that implementation, because the statement text is only in the DOM when it is drawn.

import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  ReviewKnowledgePane,
  ReviewPayload,
  ReviewResult,
  ReviewSideContent,
} from "../../data/review";
import { ReviewSurface } from "./ReviewSurface";

const REPO = "agents-remember";
const MASTER = "260921_complete-code-and-intent-review";
const LEAF = "260921-ICR-L6";

// The authored words and identities the server-side cases measure, verbatim.
const ADDED_STATEMENT = "Every refused candidate write records the identity that refused it.";
const REMOVED_STATEMENT =
  "A withdrawn obligation is reported as withdrawn and never silently dropped.";
const SHARED_STATEMENT = "An accepted obligation carries the reference that accepted it.";
const SHARED_ACCEPTANCE_REF = "requirement:KS-R08@v1";
// The pane's own rendering of a structured field value: the marker plus compact JSON of the value
// each side holds (`review_statement_sides.structured_value_text`). The two sides differ, as the
// comparison says the field does -- a shared reason string would have made a changed field read as
// one unchanged value.
const STRUCTURED_BEFORE =
  '<recorded as a structured value, rendered as compact JSON: {"actor_ref":"agent:read-fixture",' +
  '"operation_id":"fec429aa-d988-432f-90d7-be0976a5ab13","origin_refs":["requirement:KS-R07@v1"]}>';
const STRUCTURED_AFTER =
  '<recorded as a structured value, rendered as compact JSON: {"actor_ref":"agent:read-fixture",' +
  '"operation_id":"905ef907-5470-431c-9c89-04c936597314","origin_refs":["requirement:KS-R07@v1"]}>';
const ABSENT_BEFORE = "the before snapshot selected no record for the reviewed subject";
const ABSENT_AFTER = "the after snapshot selected no record for the reviewed subject";
const UNRESOLVED_AFTER =
  "the after snapshot holds the record but published no statement for it; the operand is " +
  "unresolved rather than an empty statement";
// A DECLARED-STATE FIXTURE, not a measured composition value: the vocabulary declares `binary` and no
// shipped statement-side builder produces it today, so this detail is the case's own words rather than
// the server's. It is here because the renderer must treat an unsupported-content state distinctly
// from a known absence, and that obligation is about the declared contract.
const BINARY_AFTER = "the after snapshot's operand is not text and this pane does not render it";

function present(text: string): ReviewSideContent {
  return { state: "present", text, language: "text", detail: "recorded statement for this revision" };
}

const absent = (detail: string): ReviewSideContent => ({ state: "absent", language: "text", detail });
const unresolved = (detail: string): ReviewSideContent => ({
  state: "unresolved",
  language: "text",
  detail,
});
const binary = (detail: string): ReviewSideContent => ({
  state: "binary",
  language: "binary",
  detail,
});

function knowledge(over: Partial<ReviewKnowledgePane> = {}): ReviewKnowledgePane {
  return {
    invariant_ids: [],
    family_ids: [],
    before_statement: present("unchanged"),
    after_statement: present("unchanged"),
    before_conditions: [],
    after_conditions: [],
    revision_groups: [],
    field_changes: [],
    authored_effects: [],
    signals: [],
    assessments: [],
    unresolved: [],
    selection_state: "subject_selected",
    ...over,
  };
}

function payload(over: Partial<ReviewPayload> = {}): ReviewPayload {
  return {
    surface_version: "knowledge-review-surface/1",
    candidate: { repository_id: REPO, master: MASTER, leaf_id: LEAF },
    comparison: {
      reference: "3f2a-comparison-reference",
      policy_version: "knowledge-diff/1",
      binding_digest: "3f2a-comparison-reference",
      selector_digest: "selector-digest",
      before_snapshot_digest: "before-snapshot-digest",
      after_snapshot_digest: "after-snapshot-digest",
      before_code_tree_id: "before-code-tree",
      after_code_tree_id: "after-code-tree",
      knowledge_compared: true,
    },
    knowledge: knowledge(),
    source: {
      inventory: {
        state: "measured",
        entries: [],
        listed_total: 0,
        detail: "the bound pair differs at no path",
        partial: false,
        command: "git diff --name-status before after",
        before_code_tree_id: "before-code-tree",
        after_code_tree_id: "after-code-tree",
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
      state: "current",
      statement: "the displayed comparison is the candidate's current comparison",
      moved: [],
    },
    submission: {
      state: "unavailable",
      reason: "no assessment submission is offered by this surface",
      next_action: "author an assessment through the ordinary authority",
      proposed_dispositions: [],
      none_is_approval: true,
    },
    limitations: [],
    ...over,
  };
}

// The surface reads one route and renders whatever came back: the stub is the transport, not a
// stand-in for a pane.
function serve(result: ReviewResult) {
  const fetchFn = vi.fn(
    async () => ({ ok: true, status: 200, json: async () => result }) as unknown as Response,
  );
  vi.stubGlobal("fetch", fetchFn);
  return fetchFn;
}

function reviewed(knowledgeOver: Partial<ReviewKnowledgePane>, payloadOver: Partial<ReviewPayload> = {}) {
  serve({
    state: "review",
    operation: "review_intent",
    repository_id: REPO,
    payload: payload({ ...payloadOver, knowledge: knowledge(knowledgeOver) }),
  });
  return render(
    <ReviewSurface repo={REPO} master={MASTER} leaf={LEAF} onBack={vi.fn()} />,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("the knowledge pane over a one-sided statement", () => {
  it("draws an added invariant's full after statement beside an absent-before label", async () => {
    const view = reviewed({
      invariant_ids: ["1620b0ad-97f0-4623-ae63-ea15e28157bc"],
      before_statement: absent(ABSENT_BEFORE),
      after_statement: present(ADDED_STATEMENT),
      after_conditions: ["the candidate write is admitted for this namespace."],
    });

    const before = await view.findByTestId("review-before-state");
    expect(before.dataset.sideState).toBe("absent");
    expect(before.textContent).toContain(ABSENT_BEFORE);
    const after = view.getByTestId("review-after-state");
    expect(after.dataset.sideState).toBe("present");
    // The statement itself is drawn by the shipped diff pane: the text is in the rendered DOM, not
    // merely in the payload the test sent.
    await waitFor(() =>
      expect(view.getByTestId("diff-pane").textContent).toContain(ADDED_STATEMENT),
    );
  });

  it("draws a removed invariant's full before statement beside an absent-after label", async () => {
    const view = reviewed({
      before_statement: present(REMOVED_STATEMENT),
      after_statement: absent(ABSENT_AFTER),
      before_conditions: ["the batch is admitted for this namespace."],
    });

    const after = await view.findByTestId("review-after-state");
    expect(after.dataset.sideState).toBe("absent");
    expect(after.textContent).toContain(ABSENT_AFTER);
    expect(view.getByTestId("review-before-state").dataset.sideState).toBe("present");
    await waitFor(() =>
      expect(view.getByTestId("diff-pane").textContent).toContain(REMOVED_STATEMENT),
    );
  });

  it("keeps both statements when both sides recorded one, and names no side", async () => {
    const view = reviewed({
      before_statement: present(SHARED_STATEMENT),
      after_statement: present(SHARED_STATEMENT),
    });

    await waitFor(() =>
      expect(view.getByTestId("diff-pane").textContent).toContain(SHARED_STATEMENT),
    );
    expect(view.queryByTestId("review-before-state")).toBeNull();
    expect(view.queryByTestId("review-after-state")).toBeNull();
  });

  it("keeps the available text and claims no diff when the other side is unreadable", async () => {
    const view = reviewed({
      before_statement: present(ADDED_STATEMENT),
      after_statement: unresolved(UNRESOLVED_AFTER),
    });

    const after = await view.findByTestId("review-after-state");
    expect(after.dataset.sideState).toBe("unresolved");
    expect(after.textContent).toContain(UNRESOLVED_AFTER);
    expect(view.getByTestId("review-no-diff-claimed").textContent).toContain("no diff is drawn");
    // The readable operand stays visible, and it is drawn as content: an empty-string operand would
    // claim the other side is a known-empty document.
    await waitFor(() =>
      expect(view.getByTestId("file-pane").textContent).toContain(ADDED_STATEMENT),
    );
    expect(view.queryByTestId("diff-pane")).toBeNull();
  });

  it.each([
    ["absent", absent(BINARY_AFTER)],
    ["unresolved", unresolved(UNRESOLVED_AFTER)],
    ["binary", binary(BINARY_AFTER)],
  ])("renders a %s side as that state and never as another one", async (state, side) => {
    const view = reviewed({ before_statement: side, after_statement: absent(ABSENT_AFTER) });

    const before = await view.findByTestId("review-before-state");
    expect(before.dataset.sideState).toBe(state);
    // Every state is carried through as its own token: none of the three collapses into another, and
    // in particular an unreadable side never reads as the known absence beside it.
    expect(view.getByTestId("review-after-state").dataset.sideState).toBe("absent");
    expect(view.queryByTestId("diff-pane")).toBeNull();
  });

  it("draws no diff and both named states when no subject was compared", async () => {
    const view = reviewed({
      before_statement: unresolved("no knowledge operand was compared"),
      after_statement: unresolved("no knowledge operand was compared"),
      selection_state: "task_context",
      selection_detail: "no invariant or family subject was selected for this review",
    });

    const before = await view.findByTestId("review-before-state");
    expect(before.dataset.sideState).toBe("unresolved");
    expect(view.getByTestId("review-after-state").dataset.sideState).toBe("unresolved");
    expect(view.queryByTestId("diff-pane")).toBeNull();
    expect(view.queryByTestId("file-pane")).toBeNull();
  });

  it("names an absent field value and a recorded empty one without printing either as blank", async () => {
    const view = reviewed({
      before_statement: present(SHARED_STATEMENT),
      after_statement: present(SHARED_STATEMENT),
      field_changes: [
        {
          item_id: "2c935ffe-a88b-45d4-b454-53a466db32b0",
          item_kind: "invariant",
          field: "acceptance_ref",
          after_value: SHARED_ACCEPTANCE_REF,
        },
        {
          item_id: "2c935ffe-a88b-45d4-b454-53a466db32b0",
          item_kind: "invariant",
          field: "lifecycle",
          before_value: "proposed",
          after_value: "accepted",
        },
        {
          item_id: "2c935ffe-a88b-45d4-b454-53a466db32b0",
          item_kind: "invariant",
          field: "provenance",
          before_value: STRUCTURED_BEFORE,
          after_value: STRUCTURED_AFTER,
        },
        {
          item_id: "2c935ffe-a88b-45d4-b454-53a466db32b0",
          item_kind: "invariant",
          field: "essential_conditions",
          before_value: "the batch is admitted for this namespace.",
          after_value: "",
        },
      ],
    });

    const rows = await view.findByTestId("review-field-changes");
    expect(rows.textContent).toContain(
      `acceptance_ref: (absent) → ${SHARED_ACCEPTANCE_REF}`,
    );
    expect(rows.textContent).toContain("lifecycle: proposed → accepted");
    expect(rows.textContent).toContain(`provenance: ${STRUCTURED_BEFORE} → ${STRUCTURED_AFTER}`);
    expect(STRUCTURED_BEFORE).not.toBe(STRUCTURED_AFTER);
    expect(rows.textContent).toContain(
      "essential_conditions: the batch is admitted for this namespace. → (recorded empty)",
    );
  });
});
