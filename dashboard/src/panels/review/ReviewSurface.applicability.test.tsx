// R26 (subject and comparison isolation) at the mounted surface: the real `ReviewSurface` over the
// real review client, for the labels that say why a record may be displayed beside a subject.
//
// WHAT THIS EXERCISES. `ReviewSurface` is the real component and `intentReview` the real client; only
// `fetch` is stubbed, so each response travels the way the browser's does (status, body, the shared
// decode in `data/reviewTransport.ts`, the component tree). No assertion reads a prop this test
// itself passed: every case asserts what the rendered DOM contains.
//
// WHERE THE VALUES COME FROM. The label, context and count shapes below are the measured payload of
// this leaf's evidence run (`probe-l26-journey.py`, recorded in
// `ar-coordination/temp/icr/evidence-l26-subject-isolation.txt`): four assessments supplied, one
// direct, one labelled context, one unresolved and one not displayed, over a real enclosure whose
// records were produced by their owning operations.
//
// THE DEFECT THESE CASES CATCH. The surface used to render every supplied assessment in the selected
// subject's pane, so a sibling invariant's finding appeared to assess a subject it was never about --
// and a reader had no way to tell the two apart. The context case below fails against that surface
// (the sibling's finding is in the DOM), and the last case fails against a repair that required the
// new fields, because a payload published before this requirement must still render.

import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ReviewPayload, ReviewResult } from "../../data/review";
import { ReviewSurface } from "./ReviewSurface";

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

const historicalLabel = {
  records: "assessments",
  record_id: "ICR-L26-AS-OLD",
  state: "historical" as const,
  subject_kind: "invariant",
  subject_id: SUBJECT,
  subject_revision_ids: [REVISION],
  references: [`code-tree:candidate:${OLD_TREE}`],
  detail: `its own recorded input code-tree:candidate is ${OLD_TREE}, not the candidate tree the displayed comparison bound`,
};

function payload(applicability: boolean): ReviewPayload {
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
      authored_effects: [],
      signals: [],
      assessments: [
        {
          ...(applicability ? { applicability: directLabel } : {}),
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
          ...(applicability ? { applicability: historicalLabel } : {}),
          assessment_id: "ICR-L26-AS-OLD",
          disposition: "concern_found",
          finding: "a previous generation's record",
          rationale: "recorded against an earlier candidate",
          author_ref: "architect@task.json",
          role_ref: "architect",
          examined_inputs: ["code-tree:candidate"],
          binding_state: "stale",
          evidence_refs: [],
        },
      ],
      ...(applicability
        ? {
            context: [
              {
                records: "assessments",
                record_id: "ICR-L26-AS-SIBLING",
                // The label names the record's KIND, never the sibling's disposition (ICR-R26).
                label: "assessment/invariant-revision",
                subject_kind: "invariant",
                subject_id: SIBLING,
                subject_revision_ids: [REVISION],
                relationship: "realization 0cca9e84 at src/batch.py",
                author_ref: "architect@task.json",
                role_ref: "architect",
                references: [`subject:invariant-revision:${SIBLING}`],
                detail:
                  "this assessment's recorded subject is another invariant the selected subject's "
                  + "recorded relationships reach; it is displayed as labelled context",
              },
            ],
            applicability: [
              {
                records: "assessments",
                supplied: 4,
                direct: 1,
                historical: 1,
                context: 1,
                candidate: 0,
                unresolved: 0,
                unrelated: 1,
                detail:
                  "1 supplied record(s) name a subject this comparison records and the selected "
                  + "subject's recorded relationships do not reach",
              },
            ],
          }
        : {}),
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
      assessments: applicability
        ? [
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
          ]
        : [],
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

describe("the review surface's record applicability", () => {
  it("mounts each record's own treatment and the true subject of labelled context", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => response(reviewed(payload(true)))),
    );

    const view = mountSubject();

    await waitFor(() =>
      expect(view.container.querySelectorAll('[data-testid="review-assessment"]').length).toBe(3),
    );
    const direct = view.container.querySelector(
      '[data-testid="review-assessment"] [data-applicability="direct"]',
    );
    expect(direct?.textContent).toContain(`applicability: direct (invariant ${SUBJECT})`);
    // The same label travels on the evidence pane's copy of the record, which is why one selection
    // cannot be displayed with two different treatments in two panes.
    expect(
      view.container.querySelectorAll('[data-applicability="direct"]').length,
    ).toBeGreaterThan(1);

    const context = view.getByTestId("review-context");
    expect(context.textContent).toContain(`of invariant ${SIBLING}`);
    expect(context.textContent).toContain("realization 0cca9e84 at src/batch.py");
    // The sibling's finding is nowhere in the pane: its judgment belongs to its own review, and
    // displaying it beside the selected subject is the contamination this requirement prevents.
    expect(view.queryByText(SIBLING_FINDING)).toBeNull();
  });

  it("states the six-way counts beside the collections it filtered", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => response(reviewed(payload(true)))),
    );

    const view = mountSubject();

    const counts = await view.findByTestId("review-applicability");
    expect(counts.textContent).toContain("supplied assessments: 4");
    expect(counts.textContent).toContain("direct 1");
    expect(counts.textContent).toContain("historical 1");
    expect(counts.textContent).toContain("context 1");
    expect(counts.textContent).toContain("not displayed 1");
  });

  it("labels a previous generation's record as historical rather than as the current result", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => response(reviewed(payload(true)))),
    );

    const view = mountSubject();

    await waitFor(() =>
      expect(view.container.querySelector('[data-applicability="historical"]')).not.toBeNull(),
    );
    const historical = view.container.querySelector('[data-applicability="historical"]');
    expect(historical?.textContent).toContain(`historical (invariant ${SUBJECT})`);
    expect(historical?.textContent).toContain(OLD_TREE);
  });

  it("still renders a payload published before the labels existed", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => response(reviewed(payload(false)))),
    );

    const view = mountSubject();

    await waitFor(() => expect(view.getAllByTestId("review-assessment").length).toBe(2));
    expect(view.queryByTestId("review-context")).toBeNull();
    expect(view.queryByTestId("review-applicability")).toBeNull();
  });
});
