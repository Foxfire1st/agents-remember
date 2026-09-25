// ICR-L25 round 3 — B7's narrow-width shape, pinned on the reviewer's OWN root.
//
// WHAT THIS EXERCISES. The real `ReviewSurface` over the real review client, mounted through the real
// fetch boundary, so the DOM asserted here is the product's.
//
// WHY THESE ASSERTIONS AND NOT A WIDTH. jsdom has no layout engine: it cannot measure a 565px pane in
// a 294px column, and a case that read `getBoundingClientRect()` here would read zeros and pass
// vacuously. What it CAN hold is the pair of declarations the layout depends on, and those are the
// whole repair:
//
//   * the surface root supplies the reviewer's own vertical scrollport (`min-height: 0` +
//     `overflow-y: auto`). The cockpit's `MAIN` is deliberately `overflow: hidden`, so before this a
//     narrow reader could not scroll a long guarantee into view at all — measured on the mounted
//     product at 320px: `userScrollableCount: 0`, three wheel trials moving nothing.
//   * every complete-payload pane is a grid item that may shrink (`min-width: 0`) and whose long
//     identities wrap (`overflow-wrap: anywhere`). Measured cause: the pane is an `auto` grid track
//     item with `min-width: auto`, and the inherited `break-word` does not lower min-content, so one
//     64-character comparison reference (539px) held the pane at 565px inside a 294px column.
//
// The measured evidence for the fix is the round-3 headless probe against the served bundle
// (`round3/before/…` vs `round3/after/…` in this leaf's enclosure); this case is the regression pin
// that keeps the declarations from being dropped again, and it is labelled as a pin rather than as a
// measurement.
import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ReviewPayload, ReviewResult } from "../../data/review";
import { ReviewSurface } from "./ReviewSurface";

const REPO = "agents-remember";
const MASTER = "260921_complete-code-and-intent-review";
const LEAF = "260921-ICR-L25";

// A comparison whose reference is ONE unbreakable 64-character token — the measured 539px culprit —
// so the payload is the shape that produced the defect, not a smaller one.
function payload(): ReviewPayload {
  return {
    surface_version: "knowledge-review-surface/1",
    candidate: { repository_id: REPO, master: MASTER, leaf_id: LEAF, task_ref: MASTER },
    comparison: {
      reference: "1678adab6416222068ba391db5b8b48d7c38b5b8c859adeac36fad3d0a2a",
      policy_version: "recorded-two-snapshot-union/v1",
      binding_digest: "d".repeat(64),
      selector_digest: "e".repeat(64),
      before_snapshot_digest: "a".repeat(64),
      after_snapshot_digest: "b".repeat(64),
      before_code_tree_id: "1".repeat(40),
      after_code_tree_id: "2".repeat(40),
      knowledge_compared: true,
    },
    knowledge: {
      invariant_ids: ["i-1"],
      family_ids: [],
      before_statement: { state: "present", language: "text", text: "before text", detail: "" },
      after_statement: { state: "present", language: "text", text: "after text", detail: "" },
      before_conditions: [],
      after_conditions: [],
      revision_groups: [],
      field_changes: [],
      authored_effects: [],
      signals: [],
      assessments: [],
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
      assessment_state: "unassessed",
      evidence_links: [],
      observations: [],
      assessments: [],
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

const reviewed: ReviewResult = {
  state: "review",
  operation: "read_knowledge_review",
  repository_id: REPO,
  payload: payload(),
};

async function mount(): Promise<HTMLElement> {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        ({
          ok: true,
          status: 200,
          statusText: "OK",
          json: async () => reviewed,
        }) as unknown as Response,
    ),
  );
  const view = render(
    <ReviewSurface repo={REPO} master={MASTER} leaf={LEAF} onBack={() => undefined} />,
  );
  await waitFor(() => expect(view.getByTestId("review-surface")).toBeTruthy());
  await waitFor(() => expect(view.getByTestId("review-details")).toBeTruthy());
  return view.getByTestId("review-surface");
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const isZeroLength = (value: string): boolean => /^0(px)?$/.test(value);

describe("ReviewSurface narrow-width shape (B7, round 3)", () => {
  it("gives the reviewer its own vertical scrollport on its own root", async () => {
    const surface = await mount();
    // The reviewer's own root — `review-surface`, the Intent Reviewer surface mounted by Cockpit —
    // not the inner `review-workspace`. The overflow this pins was inside this root.
    expect(surface.dataset.testid).toBe("review-surface");
    expect(surface.style.overflowY).toBe("auto");
    // React writes a numeric `0` as `"0"` and jsdom keeps it, so the assertion is on the length
    // being zero rather than on one serialisation of it.
    expect(isZeroLength(surface.style.minHeight)).toBe(true);
    expect(surface.style.height).toBe("100%");
  });

  it("lets every complete-payload pane shrink and wrap its long identities", async () => {
    const surface = await mount();
    const panes = [...surface.querySelectorAll<HTMLElement>("[data-pane]")];
    // Non-empty input, asserted before anything is compared: a query that silently found no panes
    // would make every assertion below vacuous.
    expect(panes.length).toBeGreaterThan(0);
    for (const paneEl of panes) {
      expect(isZeroLength(paneEl.style.minWidth)).toBe(true);
      expect(paneEl.style.overflowWrap).toBe("anywhere");
    }
  });

  it("constrains the disclosure's grid track and wraps the header row", async () => {
    const surface = await mount();
    const details = surface.querySelector<HTMLElement>("[data-testid='review-details']");
    // The grid is the disclosure's second child: the first is its `<summary>`.
    const grid = details?.children[1] as HTMLElement | undefined;
    expect(grid?.tagName).toBe("DIV");
    expect((grid as HTMLElement).style.gridTemplateColumns).toBe("minmax(0, 1fr)");
    const subject = surface.querySelector<HTMLElement>("[data-testid='review-subject']");
    expect(subject).not.toBeNull();
    const header = (subject as HTMLElement).parentElement as HTMLElement;
    expect(header.style.flexWrap).toBe("wrap");
    expect((subject as HTMLElement).style.overflowWrap).toBe("anywhere");
  });
});
