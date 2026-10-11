// R86 rule 19 / Architect 2026-10-08: pane 3 still carries the selected subject's direct
// assessment and must not borrow a sibling's judgment. This synthetic labelled fixture exercises
// that retained DOM behaviour through the real ReviewSurface/client, with only fetch/catalogue
// replaced. Dataset context/count/history and pre-revision-selection probes retire here; no
// production payload, range or channel compatibility is added.
import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ReviewPayload, ReviewResult } from "../../data/review";
import { ReviewSurface } from "./ReviewSurface";

// Comparison-focused cases isolate the catalogue. The normal catalogue-to-review journey is
// exercised through both real readers in ReviewSurface.navigation.test.tsx.
vi.mock("../../data/useReviewCatalogue", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../data/useReviewCatalogue")>();
  return {
    ...actual,
  useReviewCatalogue: () => ({
    loading: false, entries: [], empty: true, stale: false, facts: "test", refresh: () => undefined,
  }),
  };
});

const REPO = "agents-remember";
const MASTER = "260921_complete-code-and-intent-review";
const LEAF = "260921-ICR-L26";
const SUBJECT = "3c513a59-5e5f-428a-b5e2-7301fcb280f7";
const SIBLING = "8ad1d0b4-3f9e-4a1d-9c0f-6d2f4c1b7e55";
const REVISION = "4a2c1f77-9c33-4f0a-8f1d-2b6e5a7c9d10";
const OLD_TREE = "1".repeat(40);
const NEW_TREE = "2".repeat(40);
const SIBLING_FINDING = "the sibling records a concern about its own subject";

const directLabel = {
  records: "assessments",
  record_id: "ICR-L26-AS-DIRECT",
  state: "direct" as const,
  subject_kind: "invariant",
  subject_id: SUBJECT,
  subject_revision_ids: [REVISION],
  references: [`subject:invariant-revision:${SUBJECT}`, `code-tree:candidate:${NEW_TREE}`],
  detail: "this assessment's recorded subject is the selected invariant and a revision it retains",
};

function payload(): ReviewPayload {
  return {
    surface_version: "knowledge-review-surface/1",
    candidate: { repository_id: REPO, master: MASTER, leaf_id: LEAF, task_ref: MASTER },
    comparison: {
      reference: "c".repeat(64),
      policy_version: "recorded-two-snapshot-union/v1",
      binding_digest: "d".repeat(64),
      selector_digest: "e".repeat(64),
      before_snapshot_digest: "a".repeat(64),
      after_snapshot_digest: "b".repeat(64),
      before_code_tree_id: OLD_TREE,
      after_code_tree_id: NEW_TREE,
      knowledge_compared: true,
    },
    knowledge: {
      invariant_ids: [SUBJECT],
      family_ids: [],
      before_statement: { state: "present", language: "text", text: "before", detail: "" },
      after_statement: { state: "present", language: "text", text: "after", detail: "" },
      before_conditions: [],
      after_conditions: [],
      revision_groups: [],
      field_changes: [],
      signals: [],
      assessments: [
        {
          applicability: directLabel,
          assessment_id: "ICR-L26-AS-DIRECT",
          disposition: "concern_found",
          finding: "the selected subject's own record",
          rationale: "recorded against the selected subject",
          author_ref: "architect@task.json",
          role_ref: "architect",
          examined_inputs: ["code-tree:candidate"],
          binding_state: "stale",
          evidence_refs: ["task:notes/reports/icr-l26-evidence.md"],
        },
        {
          assessment_id: "ICR-L26-AS-SIBLING",
          applicability: { ...directLabel, record_id: "ICR-L26-AS-SIBLING", subject_id: SIBLING },
          disposition: "concern_found",
          finding: SIBLING_FINDING,
          rationale: "recorded against the sibling subject",
          author_ref: "architect@task.json",
          role_ref: "architect",
          examined_inputs: ["code-tree:candidate"],
          binding_state: "stale",
          evidence_refs: [],
        },
      ],
      revision_selection: {
        record_kind: "invariant", record_id: SUBJECT, state: "compared",
        before_revision_id: REVISION, after_revision_id: REVISION,
        before_heads: [REVISION], after_heads: [REVISION],
        before_retained: [REVISION], after_retained: [REVISION],
        statement: "Selected subject's direct assessment fixture.",
      },
      unresolved: [],
      selection_state: "subject_selected",
    },
    source: {
      inventory: {
        state: "measured",
        entries: [],
        listed_total: 0,
        detail: "the bound pair differs at no path",
        partial: false,
        command: "git diff --raw -z",
        unrepresentable_paths: [],
      },
      locations: [],
      remaining: [{ name: "locations_remaining", value: 0 }],
      unattributed_changed_paths: [],
      attributed_changed_paths: [],
      unresolved: [],
    },
    evidence: {
      evidence_state: "none_recorded",
      assessment_state: "assessed",
      evidence_links: [],
      observations: [],
      assessments: [
            {
              applicability: directLabel,
              assessment_id: "ICR-L26-AS-DIRECT",
              disposition: "concern_found",
              finding: "the selected subject's own record",
              rationale: "recorded against the selected subject",
              author_ref: "architect@task.json",
              role_ref: "architect",
              examined_inputs: ["code-tree:candidate"],
              binding_state: "stale",
              evidence_refs: [],
            },
          ],
      source_inspection_available: true,
      unresolved: [],
    },
    staleness: { state: "current", statement: "the displayed comparison is current", moved: [] },
    submission: {
      state: "unavailable",
      reason: "this increment ships the surface display-only",
      next_action: "publish through the curator authority",
      proposed_dispositions: [],
      none_is_approval: true,
    },
    page: null,
    limitations: [],
  };
}

const reviewed = (body: ReviewPayload): ReviewResult => ({
  state: "review",
  operation: "read_knowledge_review",
  repository_id: REPO,
  payload: body,
});

function response(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    statusText: "",
    json: async () => body,
  } as unknown as Response;
}

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

describe("the review surface's retained direct assessments", () => {
  it("mounts a direct record's own treatment without showing a sibling's judgment", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => response(reviewed(payload()))));
    const view = mountSubject();
    const center = await view.findByTestId("review-center-member");
    await waitFor(() =>
      expect(center.querySelectorAll('[data-testid="review-center-assessment"]')).toHaveLength(1),
    );
    const direct = center.querySelector('[data-testid="review-center-assessment"]')!;
    expect(direct.textContent).toContain("the selected subject's own record");
    expect(direct.textContent).toContain("concern_found");
    expect(direct.textContent).toContain("recorded against the selected subject");
    expect(direct.getAttribute("data-binding")).toBe("stale");
    const treatment = direct.querySelector('[data-applicability="direct"]');
    expect(treatment?.textContent).toContain(`direct · invariant ${SUBJECT}`);
    expect(view.queryByText(SIBLING_FINDING)).toBeNull();
    expect(view.container.textContent).not.toContain(SIBLING_FINDING);
  });
});
