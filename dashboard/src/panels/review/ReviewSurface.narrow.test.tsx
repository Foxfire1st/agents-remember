// ICR-L25 round 3 — B7's narrow-width shape, re-pinned for MIK-R89 v2 rule 1's layout B.
//
// WHAT THIS EXERCISES. The real `ReviewSurface` over the real review client, mounted through the real
// fetch boundary, so the DOM asserted here is the product's.
//
// WHY THESE ASSERTIONS AND NOT A WIDTH. jsdom has no layout engine: it cannot measure a 565px pane in
// a 294px column, and a case that read `getBoundingClientRect()` here would read zeros and pass
// vacuously. What it CAN hold is the declarations the layout depends on:
//
//   * layout B: the root does not scroll (`overflow: hidden`, `height: 100%`, `min-height: 0`); the
//     reading column is the page scroller (`overflow-y: auto`, `min-height: 0`) and never scrolls
//     sideways. At 40rem and narrower the root lets the page around it scroll and the column stops
//     being a scroller, so a narrow reader can still scroll a long guarantee into view (the B7
//     defect this file was written for: `MAIN` is `overflow: hidden`). That page is the review's
//     `main` (reviewPage.tsx), `overflow-y: auto` there, so the wheel and touch move it too.
//   * every column may shrink (`min-width: 0`) and long identities wrap (`overflow-wrap: anywhere`
//     on the root, inherited, never reset). Measured cause of B7: an `auto` grid item with
//     `min-width: auto` held one 64-character comparison reference at 565px in a 294px column.
//
// The measured evidence for the original fix is the round-3 headless probe of ICR-L25; this file is
// the regression pin that keeps the declarations from being dropped, and it is labelled as a pin
// rather than as a measurement.
import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ReviewMain } from "../../cockpit/reviewPage";
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
    judgment: { state: "none", detail: "This leaf recorded no judgment about this record", rows: [], proofs: [] },
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

// `inMain` mounts the surface as the cockpit does, inside the review page's `main` (reviewPage.tsx).
async function mount(inMain = false): Promise<HTMLElement> {
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
    inMain ? (
      <ReviewMain
        target={{ repo: REPO, master: MASTER, leaf: LEAF }}
        chat={{ open: false, toggle: () => undefined }}
        onBack={() => undefined}
      />
    ) : (
      <ReviewSurface repo={REPO} master={MASTER} leaf={LEAF} onBack={() => undefined} />
    ),
  );
  await waitFor(() => expect(view.getByTestId("review-surface")).toBeTruthy());
  await waitFor(() => expect(view.getByTestId("review-details")).toBeTruthy());
  return view.getByTestId("review-surface");
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const classes = (node: Element | null | undefined): string[] => [...(node?.classList ?? [])];

// The overflow an element's classes declare on one axis. In Panda's sheet the 40rem condition's rules
// follow the base ones, and a longhand follows its shorthand, so each wins in that order.
const NARROW = "[@media_(max-width:_40rem)]:";
function declared(element: Element, axis: "x" | "y", narrow: boolean): string {
  const names = classes(element);
  for (const level of narrow ? [NARROW, ""] : [""]) {
    for (const prefix of [`${level}ov-${axis}_`, `${level}ov_`]) {
      const found = names.find((name) => name.startsWith(prefix));
      if (found) return found.slice(prefix.length);
    }
  }
  return "visible";
}

// The used overflow (CSS Overflow 3): beside a scrolling axis, `visible` is `auto` and `clip` is
// `hidden`, and the element is then a scroll container. `clip` alone makes none.
function overflowOf(element: Element, narrow: boolean): { x: string; y: string; scrolls: boolean } {
  const x = declared(element, "x", narrow);
  const y = declared(element, "y", narrow);
  const scrolling = (value: string) => value !== "visible" && value !== "clip";
  if (!scrolling(x) && !scrolling(y)) return { x, y, scrolls: false };
  const used = (value: string) => (value === "visible" ? "auto" : value === "clip" ? "hidden" : value);
  return { x: used(x), y: used(y), scrolls: true };
}

// The scroll containers from `start` up to the document, nearest first.
function scrollers(start: Element, narrow: boolean): Element[] {
  const found: Element[] = [];
  for (let node = start.parentElement; node; node = node.parentElement)
    if (overflowOf(node, narrow).scrolls) found.push(node);
  return found;
}

describe("ReviewSurface narrow-width shape (B7, round 3; layout B)", () => {
  it("keeps the root still and makes the reading column the page scroller", async () => {
    const surface = await mount();
    expect(surface.dataset.testid).toBe("review-surface");
    expect(classes(surface)).toEqual(expect.arrayContaining(["h_100%", "min-h_0", "ov_hidden"]));
    // At 40rem and narrower the page around the stacked columns scrolls.
    expect(classes(surface)).toEqual(
      expect.arrayContaining(["[@media_(max-width:_40rem)]:h_auto", "[@media_(max-width:_40rem)]:ov_visible"]),
    );
    const scroller = surface.querySelector<HTMLElement>("[data-review-scroller]")!;
    expect(scroller.dataset.testid).toBe("review-center-column");
    expect(classes(scroller)).toEqual(expect.arrayContaining(["ov-y_auto", "ov-x_hidden", "min-h_0"]));
    expect(classes(scroller)).toContain("[@media_(max-width:_40rem)]:ov-y_visible");
    // The root sets no inline overflow of its own any more.
    expect(surface.style.overflowY).toBe("");
  });

  it("lets every column shrink and wrap its long identities", async () => {
    const surface = await mount();
    expect(classes(surface)).toContain("ov-wrap_anywhere");
    const columns = [
      surface.querySelector('[data-testid="review-workspace"]'),
      surface.querySelector('[data-testid="review-tree-column"]'),
      surface.querySelector('[data-review-scroller]'),
    ];
    for (const column of columns) {
      expect(column).not.toBeNull();
      expect(classes(column)).toContain("min-w_0");
      // Each inherits the root's anywhere wrap; none resets it.
      expect(classes(column).some((name) => name.startsWith("ov-wrap_"))).toBe(false);
      expect((column as HTMLElement).style.overflowWrap).toBe("");
    }
    expect(surface.querySelector('[data-testid="review-scope-comparison"]')?.textContent)
      .toContain(reviewed.payload!.comparison!.reference);
  });

  it("stacks the grid at 40rem, wraps the top line there and keeps identities in the disclosure", async () => {
    const surface = await mount();
    const details = surface.querySelector<HTMLElement>('[data-testid="review-details"]');
    expect(details?.dataset.disclosure).toBe("provenance");
    expect(details?.querySelector('[data-testid="review-selection"]')?.textContent)
      .toContain(reviewed.payload!.comparison!.reference);
    const grid = surface.querySelector<HTMLElement>('[data-testid="review-workspace"]')!;
    expect(classes(grid)).toEqual(
      expect.arrayContaining(["grid-tc_var(--review-tree-w)_minmax(0,_1fr)", "[@media_(max-width:_40rem)]:grid-tc_minmax(0,_1fr)"]),
    );
    const header = surface.querySelector('[data-testid="review-top-line"]');
    expect(classes(header)).toEqual(expect.arrayContaining(["min-w_0", "flex-wrap_wrap"]));
  });

  // MIK-R89 v2 rules 3 and 11. The tree draws its own controls (treeStyles.ts): the review's shared
  // control look leaves the tree's column out, as it leaves out the tree's rows, so its control line
  // keeps the sizes it is fitted with. The column clips without being a scroller, so a focus or a
  // menu can never scroll the tree sideways.
  it("leaves the tree's controls their own look and the tree no sideways scroll", async () => {
    const surface = await mount();
    const column = surface.querySelector('[data-testid="review-tree-column"]')!;
    expect(column.id).toBe("review-tree");
    expect(overflowOf(column, false)).toEqual({ x: "clip", y: "clip", scrolls: false });
    expect(overflowOf(column, true).scrolls).toBe(false);
    const shared = classes(surface).filter((name) => name.startsWith("[&_button:not("));
    expect(shared.length).toBeGreaterThan(0);
    // The button, the select and the input parts of the selector each leave the tree's column out.
    for (const name of shared) expect(name.split(":not(#review-tree_*)")).toHaveLength(4);
  });

  // MIK-R89 v2 rules 1 and 11, evidence 15. A wheel or a touch moves the nearest scroll container
  // that can scroll its way; an `overflow: hidden` one moves only by script. So the stacked page
  // needs one `auto` scroller, and nothing between it and the column may scroll on its own.
  it("makes the review's main the stacked page's one scroller, under the column's sticky head", async () => {
    const surface = await mount(true);
    const main = surface.closest("main")!;
    const head = surface.querySelector<HTMLElement>("[data-reading-head]")!;
    const column = surface.querySelector<HTMLElement>("[data-review-scroller]")!;
    expect(main.dataset.view).toBe("intent-review");
    // At 40rem and narrower: the head's first scroll container is the main, which the reader moves
    // up and down and never sideways. The column clips sideways without becoming a scroller, so the
    // head sticks to the top of the screen while the column passes under it.
    expect(scrollers(head, true)[0]).toBe(main);
    expect(overflowOf(main, true)).toEqual({ x: "hidden", y: "auto", scrolls: true });
    expect(overflowOf(column, true)).toEqual({ x: "clip", y: "visible", scrolls: false });
    for (let node: HTMLElement | null = head; node && node !== main; node = node.parentElement)
      expect([node.style.overflow, node.style.overflowX, node.style.overflowY].join("")).toBe("");
    // Above 40rem nothing moves: the column is the page scroller and the main stays still.
    expect(scrollers(head, false)[0]).toBe(column);
    expect(overflowOf(column, false)).toEqual({ x: "hidden", y: "auto", scrolls: true });
    expect(overflowOf(main, false)).toEqual({ x: "hidden", y: "hidden", scrolls: true });
  });
});
