// ICR-R24@v3 at the mounted surface: the family tree, the unified central reading path, the complete
// source explorer, and the one family-roster walk control.
//
// WHAT THIS EXERCISES. `ReviewSurface` is the real component and `intentReview` the real client, so
// every request below is built by the shipped client and every response travels the way the
// browser's does (status, body, the shared decode in `data/reviewTransport.ts`, the component tree,
// the read cycle in `ReviewReadCycle.ts`). Only `fetch` is stubbed, and the bodies it is stubbed WITH
// are the real route's own: each `familyReview.*.captured.json` holds the bytes `serving/review.py`
// published over the real application owners and the real store for one real enclosure. Not all of
// them are from the same route revision:
//   * `complete` and `identical` were re-captured over HTTP by the producer, command and source tree
//     named in `familyReview.capture-provenance.json`, and carry each member source's structured
//     locator, resolved ranges and locator state;
//   * `truncated`, `continued`, `oneSided`, `walkFinal` and `emptyRoster` still hold their capture at
//     63b47629. That route listed only the membership rows on a roster page; the current route also
//     resolves the members that a page's content and claim items represent, so it cannot reproduce
//     the first four (`emptyRoster` carries no member source and was left as captured; see the
//     receipt's `not_recaptured` section). Their re-capture belongs with the change that moves the
//     cases reading them to the current route's roster states, and is not done here.
// No assertion below reads a prop this test itself passed, and
// no payload is assembled here: a case that reached into the component with a hand-built value would
// prove nothing about the wire contract, which is what these cases are about.
//
// WHAT THESE CASES CATCH. The surface as it stood was a stack of diagnostic paragraphs over three
// panes: a family's recorded guarantee, its roster of member revisions (unchanged siblings included)
// and the intent-to-expression reading path were not rendered at all, so a reviewer could not see the
// family a selected invariant belongs to or the guarantee its member statement is about. They also
// pin the two ways a repair could lie: rendering a family context the body does not carry as a
// measured "no family recorded", and offering a control that fetches the server's own refusal (a
// cursor-less request for the `family_members` collection, which is a SET of per-family walks rather
// than one walk with a first page).
//
// WHAT THEY DO NOT CLAIM. These are not browser evidence. The Playwright configs in this repository
// are Dagger-only (`dashboard/scripts/require-dagger-test-environment.mjs` refuses host execution), so
// the mounted-tree evidence here is evidence of the real client and the real component tree over real
// server bytes -- not of a live page fed by a running publication. The packet's mounted-browser
// structural and visual review, and R25's assembled acceptance, are separate obligations.

import { readFileSync } from "node:fs";
import path from "node:path";

import { cleanup, fireEvent, render, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { REVIEW_PAGED_COLLECTIONS, REVIEW_WALKABLE_COLLECTIONS } from "../../data/review";
import { RECORDS_PAGE_REFUSAL_RESPONSE } from "./recordsPageRefusal.captured";
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
const LEAF = "260921-ICR-L24";

// Two families the captured bodies record, with the exact authored guarantee text each selected
// revision carries. They are read from the capture below rather than typed, so a changed capture
// fails this module's own reading instead of silently re-pointing the assertions.
const COMPLETE = captured("familyReview.complete.captured.json");
const TRUNCATED = captured("familyReview.truncated.captured.json");
const CONTINUED = captured("familyReview.continued.captured.json");
const IDENTICAL = captured("familyReview.identical.captured.json");
const ONE_SIDED = captured("familyReview.oneSided.captured.json");
const EMPTY_ROSTER = captured("familyReview.emptyRoster.captured.json");
const WALK_FINAL = captured("familyReview.walkFinal.captured.json");

// Resolved from this file rather than from the process cwd, the same way `wireFixtureGuard` resolves
// the dashboard root: the suite is run from the dashboard root by convention, but a case that depended
// on that would fail for a reader running one file from elsewhere.
function captured(name: string): unknown {
  const here = path.dirname(new URL(import.meta.url).pathname);
  return JSON.parse(readFileSync(path.join(here, name), "utf8")) as unknown;
}

// The one identity a case needs out of a captured body whose subject was a FAMILY rather than an
// invariant. The bodies are `unknown` on purpose -- they carry fields this client's mirror does not
// declare -- so the value is narrowed at runtime instead of asserted with a type assertion, and a
// body that does not carry it fails loudly rather than mounting the surface under a wrong subject.
function firstFamilyId(body: unknown): string {
  if (typeof body !== "object" || body === null || !("payload" in body)) {
    throw new Error("the captured body carries no payload");
  }
  const { payload } = body;
  if (typeof payload !== "object" || payload === null || !("family_context" in payload)) {
    throw new Error("the captured body carries no family context");
  }
  const { family_context: context } = payload;
  if (typeof context !== "object" || context === null || !("entries" in context)) {
    throw new Error("the captured family context carries no entries");
  }
  const { entries } = context;
  if (!Array.isArray(entries) || entries.length === 0) {
    throw new Error("the captured family context composes no family");
  }
  const first: unknown = entries[0];
  if (typeof first !== "object" || first === null || !("family_id" in first)) {
    throw new Error("the composed family names no identity");
  }
  const { family_id: familyId } = first;
  if (typeof familyId !== "string") throw new Error("the family identity is not a string");
  return familyId;
}

// The route's own typed answer for one listed entry whose content was not opened: this module's cases
// about the explorer never assert on a file's bytes, only on the expansion state, and a real refusal
// body is the shortest honest answer that keeps the decode path identical.
const SOURCE_REFUSAL: unknown = {
  state: "refused",
  operation: "read_knowledge_review_source_content",
  repository_id: REPO,
  refusal: {
    code: "not-found",
    detail: "this case does not open entry content; the explorer's expansion state is what it reads",
    next_action: "open an entry against a served repository to read its content",
  },
};

interface Serving {
  urls: string[];
}

// One `fetch` for the whole surface, routed the way the browser routes: the review reads answer with
// the queue of real bodies in order, and the source-content read answers with the typed refusal above.
let initialFamily: string | undefined;
function serving(queue: unknown[]): Serving {
  try { initialFamily = firstFamilyId(queue[0]); } catch { initialFamily = undefined; }
  const urls: string[] = [];
  let index = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: unknown) => {
      const address = String(url);
      urls.push(address);
      const body = address.includes("/source-content")
        ? SOURCE_REFUSAL
        : (queue[Math.min(index++, queue.length - 1)] as unknown);
      return {
        ok: true,
        status: 200,
        statusText: "OK",
        json: async () => body,
      } as unknown as Response;
    }),
  );
  return { urls };
}

function mount(history?: "recorded", familyId?: string) {
  return render(
    <ReviewSurface
      repo={REPO}
      master={MASTER}
      leaf={LEAF}
      selectorKind={familyId ?? initialFamily ? "family" : undefined}
      selectorId={familyId ?? initialFamily}
      history={history}
      onBack={vi.fn()}
    />,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ReviewSurface family-centered workspace (ICR-R24@v3)", () => {
  it("renders the recorded families, their authored guarantees and the full member statements", async () => {
    serving([COMPLETE]);
    const view = mount();

    const tree = await view.findByTestId("review-family-tree");
    // The context's own state is the server's, and the tree does not upgrade a partial composition to
    // a complete one.
    expect(tree.dataset.familyState).toBe("partial");
    const families = view.getAllByTestId("review-family");
    expect(families).toHaveLength(2);

    // Each family's guarantee is the family revision's own authored text, printed whole. The first
    // family authored a new guarantee between the snapshots; the second selected the same revision on
    // both sides. Both texts are on screen, and they are different facts.
    const guarantees = view
      .getAllByTestId("review-family-guarantee")
      .map((node) => node.textContent ?? "");
    expect(guarantees.join("\n")).toContain("The retry budget and the anchor identity rule hold together.");
    expect(guarantees.join("\n")).toContain(
      "The retry budget and the batch obligation hold together under the revised member.",
    );

    // The complete member statements, unchanged siblings included: the revision recorded on BOTH
    // snapshots is rendered once, with both sides named, and the before-only and after-only
    // memberships are rendered beside it rather than dropped.
    const memberNodes = view.getAllByTestId("review-family-member");
    expect(memberNodes.length).toBeGreaterThanOrEqual(5);
    const text = memberNodes.map((node) => node.textContent ?? "").join("\n");
    expect(text).toContain("recorded on both snapshots");
    expect(text).toContain("recorded on the before snapshot only");
    expect(text).toContain("recorded on the after snapshot only");
    expect(view.getAllByTestId("review-family-member-open").length).toBe(memberNodes.length);
    // A statement the store records is rendered as prose, not summarised into a count. The same
    // member revision is recorded under two families, so its one statement appears beneath each --
    // one canonical revision referenced twice, never two revisions.
    expect(view.getAllByText(/Retries share one budget across integration/).length).toBeGreaterThan(1);
    // A member recorded under more than one family revision states that recorded sharing.
    expect(view.getAllByTestId("review-family-member-shared").length).toBeGreaterThan(0);
  });

  it("opens a family review whose guarantee comparison is the shape the two recorded revisions support", async () => {
    serving([COMPLETE]);
    const view = mount();
    const openers = await view.findAllByTestId("review-family-open");

    // One of these two families selected the SAME family revision on both snapshots -- the only shape
    // in which "the guarantee is unchanged" is a true sentence -- and the other selected two DIFFERENT
    // revisions with different authored text, which must be drawn as the real before/after diff. The
    // case reads which is which from the rendered blocks rather than from a capture's row order, so a
    // re-captured body cannot silently re-point it at the wrong family.
    const shapes: string[] = [];
    for (const familyId of openers.map(node => node.dataset.family)) {
      fireEvent.click(view.getAllByTestId("review-family-open").find(node => node.dataset.family === familyId)!);
      await waitFor(() => expect(view.getByTestId("review-center-family").dataset.family).toBe(familyId));
      if (view.queryByTestId("review-center-guarantee-unchanged") !== null) {
        expect(
          view.getByTestId("review-center-guarantee-unchanged").textContent,
        ).toContain("Guarantee unchanged · same recorded revision");
        expect(view.queryByTestId("review-center-guarantee-changed")).toBeNull();
        shapes.push("unchanged");
        continue;
      }
      const changed = await view.findByTestId("review-center-guarantee-changed");
      expect(changed.textContent).toContain(
        "The retry budget and the anchor identity rule hold together.",
      );
      expect(changed.textContent).toContain(
        "The retry budget and the batch obligation hold together under the revised member.",
      );
      shapes.push("changed");
    }
    expect(shapes.sort()).toEqual(["changed", "unchanged"]);
    expect(view.getByTestId("review-center").dataset.selectionKind).toBe("family");
  });

  it("keeps the family context when a member is selected and states the five facts separately", async () => {
    serving([COMPLETE]);
    const view = mount();
    const memberOpeners = await view.findAllByTestId("review-family-member-open");

    fireEvent.click(memberOpeners[0]);
    await view.findByTestId("review-center-member");

    // The member's review retains its family: the guarantee the statement is about is on screen above
    // it, not behind a tab or an inspector.
    expect(view.getByTestId("review-center-member-family").textContent).toContain("Member review");
    expect(
      view.queryByTestId("review-center-guarantee-changed") ??
        view.queryByTestId("review-center-guarantee-unchanged") ??
        view.queryByTestId("review-center-guarantee-one-sided"),
    ).not.toBeNull();

    // Statement, membership/realization, source attribution and authored judgment are four more
    // separate facts beside the guarantee, each stated from its own owner's value.
    const facts = view.getByTestId("review-center-facts");
    const byFact = (name: string) =>
      facts.querySelector(`[data-fact="${name}"]`)?.textContent ?? "";
    expect(byFact("guarantee")).toContain("guarantee:");
    expect(byFact("statement")).toContain("member statement:");
    expect(byFact("membership")).toContain("recorded realization claim(s)");
    expect(byFact("source")).toContain("location record(s) name this member's retained revisions");
    expect(byFact("assessment")).toContain("No member, membership or guarantee change creates one");
    expect(view.getByTestId("review-center-evidence")).toBeTruthy();
  });

  it("keeps the complete source explorer independent of the family selection", async () => {
    serving([COMPLETE]);
    const view = mount();
    const before = await view.findByTestId("review-source-explorer");
    const listedBefore = view.getByTestId("review-inventory").textContent ?? "";
    expect(view.getByTestId("review-population-scope").textContent).toContain(
      "Complete source population, including unattributed changes",
    );
    expect(before.dataset.inventoryState).toBe("measured");

    fireEvent.click((await view.findAllByTestId("review-family-open"))[0]);
    await view.findByTestId("review-center-family");

    // The selection is an attribution lens: the explorer's own population sentence is unchanged by it.
    expect(view.getByTestId("review-inventory").textContent).toBe(listedBefore);
    expect(view.getByTestId("review-source-explorer")).toBeTruthy();
  });

  it("sends the family's published cursor and refuses a response from another walk", async () => {
    const { urls } = serving([TRUNCATED, CONTINUED]);
    const view = mount();

    // The collection picker offers only the collections a cursor-less request can be answered for.
    // `family_members` is a member of the server's union and of this client's mirror, and is
    // deliberately absent here: naming it with no cursor earns the server's own
    // `comparison_page_unreadable` refusal, so a picker option for it would be a control that fetches
    // a refusal for a question the reader did not ask.
    expect(REVIEW_PAGED_COLLECTIONS).toContain("family_members");
    expect(REVIEW_WALKABLE_COLLECTIONS).not.toContain("family_members");
    const picker = await view.findByTestId("review-page-collection");
    const offered = within(picker)
      .getAllByTestId("review-page-option")
      .map((option) => (option as HTMLOptionElement).value);
    expect(offered).toEqual([...REVIEW_WALKABLE_COLLECTIONS]);
    expect(offered).not.toContain("family_members");

    // The truncated roster states both of the read owner's own measures, with the population each one
    // measured -- the item walk and the membership rows are different counts.
    const roster = view.getAllByTestId("review-family-roster")[0];
    expect(roster.dataset.rosterComplete).toBe("false");
    expect(roster.textContent).toContain("item(s) of this family revision's recorded selection");
    expect(roster.textContent).toContain("membership row(s); loaded context contains");

    const nexts = await view.findAllByTestId("review-family-roster-next");
    expect(nexts.length).toBeGreaterThan(0);
    const published = nexts[0].dataset.continuation ?? "";
    expect(published).not.toBe("");
    fireEvent.click(nexts[0]);

    await waitFor(() => expect(urls.length).toBeGreaterThan(1));
    const continuationRequest = urls[1];
    expect(continuationRequest).toContain("pageOf=family_members");
    // The value sent is the value the family context's own roster page published, unchanged.
    expect(decodeURIComponent(continuationRequest)).toContain(published);

    // This older capture continues the after-side cursor and carries a different primary revision
    // selection. The clicked before cursor cannot admit it as a successful replacement.
    const failure = await view.findByTestId("review-failure");
    expect(failure.dataset.reviewCode).toBe("comparison_page_unreadable");
    expect(view.getByTestId("review-retained-generation")).toBeTruthy();
    expect(view.queryByTestId("review-roster-walk")).toBeNull();
  });

  it("renders a body that carries no family context as that fact, never as a measured zero", async () => {
    // `recordsPageRefusal.captured.ts` is a real route body recorded before the family field existed.
    // A decoder has to say what such a body is; what it must never do is render the absent field as
    // "the recorded scope was read and holds no family", which is what `no_family_recorded` asserts.
    serving([RECORDS_PAGE_REFUSAL_RESPONSE]);
    const view = mount();

    const tree = await view.findByTestId("review-family-tree");
    expect(tree.dataset.familyState).toBe("absent");
    const context = view.getByTestId("review-family-context");
    expect(context.dataset.contextState).toBe("absent");
    expect(context.textContent).toContain("No family context was supplied");
    expect(context.textContent).toContain("Attribution is unknown");
    expect(view.queryByTestId("review-family-list")).toBeNull();
    // The scope header states the same fact, and the complete source explorer is unaffected by it.
    expect(view.getByTestId("review-scope-families").textContent).toContain(
      "Family context was not supplied",
    );
    expect(view.getByTestId("review-source-explorer")).toBeTruthy();
  });

  it("keeps an expanded entry expanded across a diff-layout switch", async () => {
    serving([COMPLETE]);
    const view = mount();
    await view.findByTestId("review-source-explorer");

    const opener = view.getAllByTestId("review-inventory-open")[0];
    fireEvent.click(opener);
    await waitFor(() => expect(opener.getAttribute("aria-expanded")).toBe("true"));
    const path = opener.dataset.path ?? "";
    expect(path).not.toBe("");
    expect(view.getByTestId("review-display-state").textContent).toContain(`expanded: ${path}`);

    // The layout preference belongs to the workspace, not to the explorer or the entry: switching it
    // cannot collapse what the reader had open.
    fireEvent.change(view.getByTestId("review-center-diff-layout"), { target: { value: "inline" } });
    await waitFor(() =>
      expect(view.getByTestId("review-workspace").dataset.diffLayout).toBe("inline"),
    );
    expect(opener.getAttribute("aria-expanded")).toBe("true");
    expect(view.getByTestId("review-display-state").textContent).toContain(`expanded: ${path}`);

    // And the full-file preference is one value shared by the explorer's bar and the centre column.
    fireEvent.click(view.getByTestId("review-center-full-file"));
    await waitFor(() =>
      expect(view.getByTestId("review-workspace").dataset.fullFile).toBe("true"),
    );
    expect(view.getByTestId("review-center-full-file").getAttribute("aria-pressed")).toBe("true");
  });

  it("marks the current selection and traverses the tree by keyboard", async () => {
    serving([COMPLETE]);
    const view = mount();
    const nodes = await view.findAllByTestId("review-family-open");

    fireEvent.click(nodes[0]);
    await waitFor(() => expect(view.getByTestId("review-center")).toBeTruthy());
    const currentNode = view
      .getAllByTestId("review-family-open")
      .find((node) => node.getAttribute("aria-current") === "true");
    expect(currentNode).toBe(view.getAllByTestId("review-family-open")[0]);

    // Arrow traversal inside the one roving-focus group, and the current selection is exposed on the
    // tree itself rather than only implied by what the centre column happens to show.
    const tree = view.getByTestId("review-family-list");
    const memberOpeners = view.getAllByTestId("review-family-member-open");
    const target = memberOpeners[0];
    target.focus();
    fireEvent.keyDown(target, { key: "ArrowDown" });
    expect(document.activeElement).toBe(memberOpeners[1]);
    fireEvent.keyDown(memberOpeners[1], { key: "ArrowUp" });
    expect(document.activeElement).toBe(target);
    expect(tree).toBeTruthy();
  });

  it("reports the filter scope without restating the comparison's totals", async () => {
    serving([COMPLETE]);
    const view = mount();
    await view.findByTestId("review-family-tree");

    const scope = view.getByTestId("review-family-filter-scope");
    expect(scope.textContent).toContain("2 families");
    fireEvent.change(view.getByTestId("review-family-filter"), {
      target: { value: "retry-budget-family" },
    });
    await waitFor(() =>
      expect(view.getByTestId("review-family-filter-scope").textContent).toContain(
        "Matching families retain all siblings",
      ),
    );
    expect(view.getAllByTestId("review-family")).toHaveLength(1);
    // The matched family keeps its members: a search retains the matching family's context.
    expect(within(view.getAllByTestId("review-family")[0]).getAllByTestId("review-family-member").length)
      .toBeGreaterThan(0);

    // A filter that matches nothing says so and states that the composed contexts are unchanged.
    fireEvent.change(view.getByTestId("review-family-filter"), {
      target: { value: "no-such-family-anywhere" },
    });
    await waitFor(() =>
      expect(view.getByTestId("review-family-none-shown").textContent).toContain(
        "unchanged by this filter",
      ),
    );
  });
  // ── fix round 1: the two blocked sentences, and the two unpinned distinctions ────────────────────

  it("says a bounded roster carried none of the measured rows, never that the read measured zero", async () => {
    serving([TRUNCATED]);
    const view = mount();
    await view.findByTestId("review-family-tree");

    // Every family in this capture has a roster page that is a position in a walk: each side MEASURED
    // 2 recorded membership rows and carried none of them. The sentence this block prints must be the
    // page-scoped fact, because the clause it replaced ("the read measured zero memberships for the
    // selected family revision") was false about the store while the owner's own line two paragraphs
    // above said it records 2 and carried 0.
    const empties = view.getAllByTestId("review-family-empty-roster");
    expect(empties).toHaveLength(2);
    const said = empties.map((node) => node.textContent ?? "").join("\n");
    expect(said).toContain("this page carried no member row");
    expect(said).toContain("0 of the 2 recorded membership row(s) it measured");
    expect(said).toContain("The continuation beside each bounded roster reaches the rows this page did not carry.");
    expect(said).not.toContain("measured zero");

    // The owner's own two measures are still printed above it, so the two clauses cannot disagree.
    const roster = view.getAllByTestId("review-family-roster")[0];
    expect(roster.textContent).toContain("records 2 membership row(s); loaded context contains 0 of them");
  });

  it("still says the measured zero when the read really measured zero memberships", async () => {
    // The other direction of the same sentence: this body records a family revision whose roster the
    // read took whole and measured zero membership rows for. Only here may "measured zero" be said.
    serving([EMPTY_ROSTER]);
    const view = mount(undefined, firstFamilyId(EMPTY_ROSTER));
    const empty = await view.findByTestId("review-family-empty-roster");
    expect(empty.textContent).toContain("the read measured zero memberships for the selected family revision");
    expect(empty.textContent).toContain("0 recorded membership row(s)");
    expect(empty.textContent).not.toContain("this page carried no member row");
  });

  it("heads a bounded member context partial and counts the owner's rows, not this page's", async () => {
    const { urls } = serving([TRUNCATED, CONTINUED]);
    const view = mount();
    await view.findByTestId("review-family-tree");
    fireEvent.click((await view.findAllByTestId("review-family-open"))[0]);
    await view.findByTestId("review-center-family");

    // The centre is the packet's primary reading path, so it may not present a bounded page as the
    // member context's whole: the heading, the counts and the per-side lines all state the bound.
    expect(view.getByTestId("review-center-member-heading").textContent).toContain(
      "Recorded member context (partial)",
    );
    const counts = view.getByTestId("review-center-member-counts").textContent ?? "";
    expect(counts).toContain("4 recorded membership row(s) measured by the read across 2 recorded side(s) (before 2 + after 2)");
    expect(counts).toContain("loaded context contains 0 member row(s) of them");
    expect(counts).toContain("the continuations beside the bounded rosters reach the rest");
    expect(view.getByTestId("review-center-member-distinct").textContent).toContain(
      "among the loaded membership contexts",
    );
    // The per-side owner lines and the walk control are the tree's own components, mounted here too.
    for (const line of view.getAllByTestId("review-center-roster")) {
      expect(line.textContent).toContain("membership row(s); loaded context contains");
    }
    // The centre's own control must WORK, not merely exist: it is clicked here and the request it
    // issues is asserted, because a control mounted without its handler renders identically and
    // fetches nothing (fix round 2, V6).
    const control = view.getAllByTestId("review-center-roster-next")[0];
    const published = control.dataset.continuation ?? "";
    expect(published).not.toBe("");
    expect(urls.length).toBe(1);
    fireEvent.click(control);
    await waitFor(() => expect(urls.length).toBeGreaterThan(1));
    expect(urls[1]).toContain("pageOf=family_members");
    expect(decodeURIComponent(urls[1])).toContain(published);
  });

  it("distinguishes two distinct revisions with identical text from one unchanged revision", async () => {
    serving([IDENTICAL]);
    const view = mount();
    const openers = await view.findAllByTestId("review-family-open");

    // Family 2 authored a successor revision carrying the parent's own text: two DISTINCT revisions,
    // identical text. That is not "the guarantee is unchanged" -- a revision was authored between
    // them -- and the block must say so with both revision identities.
    fireEvent.click(openers[1]);
    const identical = await view.findByTestId("review-center-guarantee-identical-text");
    expect(identical.textContent).toContain("two recorded revisions");
    expect(identical.textContent).toContain("Guarantee wording unchanged");
    expect(view.queryByTestId("review-center-guarantee-unchanged")).toBeNull();
    expect(identical.textContent).not.toContain("so the guarantee is unchanged");

    // Family 1 selected the SAME revision on both snapshots, which is the only shape allowed to say
    // the guarantee is unchanged.
    fireEvent.click(view.getAllByTestId("review-family-open")[0]);
    const unchanged = await view.findByTestId("review-center-guarantee-unchanged");
    expect(unchanged.textContent).toContain("Guarantee unchanged · same recorded revision");
    expect(view.queryByTestId("review-center-guarantee-identical-text")).toBeNull();
  });

  it("states a member whose content the page did not carry as that, not as a one-sided statement", async () => {
    serving([ONE_SIDED]);
    const view = mount();
    await view.findByTestId("review-family-tree");

    // This walk carried one membership row whose revision content it did not carry, on the only side
    // that records it. Selecting it must say the content was not on the page; the one-sided wrapper
    // beside it would instead present a comparison that was never made.
    const notCarried = view.getByTestId("review-family-member-state");
    const row = notCarried.closest("li");
    expect(row).not.toBeNull();
    fireEvent.click(within(row as HTMLElement).getByTestId("review-family-member-open"));

    const block = await view.findByTestId("review-center-member-not-on-page");
    expect(block.textContent).toContain("did not carry the revision content");
    expect(view.queryByTestId("review-center-member-one-sided")).toBeNull();
    expect(view.queryByTestId("review-center-member-unchanged")).toBeNull();
    expect(view.queryByTestId("review-center-member-changed")).toBeNull();
  });
  it("does not turn an uncarried roster operand into an absent statement", async () => {
    const { urls } = serving([CONTINUED]);
    const view = mount();
    const tree = await view.findByTestId("review-family-tree");
    const row = within(tree).getAllByTestId("review-family-member").find(node => node.dataset.sides === "before+after")!;
    const revision = within(row).getByTestId("review-family-member-open").dataset.revision!;
    fireEvent.click(within(row).getByTestId("review-family-member-open"));
    await waitFor(() => expect(urls.some(url => url.includes("selectorKind=invariant"))).toBe(true));
    await view.findByTestId("review-center-member-ambiguous");
    expect(view.queryByTestId("review-center-member-one-sided")).toBeNull();
    expect(view.getByTestId("review-family-tree").querySelector(`[data-revision="${revision}"]`)).not.toBeNull();
    const control = view.getAllByTestId("review-center-roster-next")[0];
    const published = control.dataset.continuation!;
    fireEvent.click(control);
    await waitFor(() => expect(urls.some(url => url.includes("pageOf=family_members"))).toBe(true));
    expect(decodeURIComponent(urls.at(-1)!)).toContain(published);
  });

  it("retains a bounded before-only membership without claiming that the invariant was removed", async () => {
    const { urls } = serving([CONTINUED]);
    const view = mount();
    const tree = await view.findByTestId("review-family-tree");
    const row = within(tree).getAllByTestId("review-family-member").find(node => node.dataset.sides === "before")!;
    const revision = within(row).getByTestId("review-family-member-open").dataset.revision!;
    fireEvent.click(within(row).getByTestId("review-family-member-open"));
    await waitFor(() => expect(urls.some(url => url.includes("selectorKind=invariant"))).toBe(true));
    await view.findByTestId("review-center-member");
    expect(view.queryByTestId("review-center-member-one-sided")).toBeNull();
    expect(view.getByTestId("review-family-tree").querySelector(`[data-revision="${revision}"]`)?.closest("li")?.dataset.sides).toBe("before");
    expect(view.getAllByTestId("review-center-roster-next").length).toBeGreaterThan(0);
  });

  it("prints one empty-roster sentence in both columns, not two that happen to agree", async () => {
    serving([TRUNCATED]);
    const view = mount();
    await view.findByTestId("review-family-tree");

    // One implementation, mounted twice: the centre's line is the tree's own string for the same
    // family. A second sentence in the centre that merely happens to read the same would drift.
    const treeSaid = Object.fromEntries(
      view
        .getAllByTestId("review-family")
        .map((node) => [
          node.dataset.family ?? "",
          within(node).getByTestId("review-family-empty-roster").textContent ?? "",
        ]),
    );
    const openers = view.getAllByTestId("review-family-open");
    for (const [index, opener] of openers.entries()) {
      fireEvent.click(opener);
      await view.findByTestId("review-center-family");
      const family = view.getByTestId("review-center-family").dataset.family ?? "";
      const centreSaid = view.getByTestId("review-center-family-empty").textContent ?? "";
      expect(centreSaid).toBe(treeSaid[family]);
      expect(index).toBeLessThan(openers.length);
    }
  });
  it("states the page that completes a multi-page walk as the walk's last page, not as the whole roster", async () => {
    serving([WALK_FINAL]);
    const view = mount();
    await view.findByTestId("review-family-tree");

    // This body is the final page of a four-page roster walk: it completes the walk and carried 11 of
    // the revision's 72 recorded membership rows. Before the walk's completion guard was corrected the
    // route answered this page's request with HTTP 500, so nothing here was renderable at all.
    const lines = view.getAllByTestId("review-family-roster");
    const lastPage = lines.filter((node) =>
      (node.textContent ?? "").includes("completes the walk"),
    );
    expect(lastPage).toHaveLength(1);
    expect(lastPage[0].textContent).toContain(
      "loaded context can retain earlier pages",
    );
    expect(lastPage[0].textContent).toContain("loaded context contains 11 of them");
    expect(lastPage[0].textContent).not.toContain("the page is the whole selection");

    // The rosters the read really did take in one page still say so, and never claim to be a step in
    // a walk they are the whole of.
    const whole = lines.filter((node) =>
      (node.textContent ?? "").includes("the page is the whole selection"),
    );
    expect(whole.length).toBeGreaterThan(0);
    for (const line of whole) expect(line.textContent).not.toContain("completes the walk");

    // And the surface read the real route's own sentence for that side rather than inventing one.
    expect(view.getByTestId("review-family-tree").textContent).toContain("completes the read walk");
  });
  it("keeps the reader's workspace state across two page requests through the centre's own control", async () => {
    const { urls } = serving([TRUNCATED, CONTINUED, CONTINUED]);
    const view = mount();
    await view.findByTestId("review-family-tree");

    // The reader sets all four pieces of local state: a selected family, a filter, an inline diff
    // layout and "full file". A page read unmounts the workspace subtree -- the payload is
    // null while the answer is in flight -- so state owned below that switch would be discarded on
    // every page, which is what the round-4 verification measured (fix round 5, V10).
    fireEvent.click((await view.findAllByTestId("review-family-open"))[0]);
    await view.findByTestId("review-center-family");
    fireEvent.change(view.getByTestId("review-family-filter"), { target: { value: "anchor" } });
    fireEvent.change(view.getByTestId("review-center-diff-layout"), { target: { value: "inline" } });
    fireEvent.click(view.getByTestId("review-center-full-file"));
    await waitFor(() => expect(view.getByTestId("review-workspace").dataset.fullFile).toBe("true"));
    expect(view.getByTestId("review-workspace").dataset.diffLayout).toBe("inline");

    const state = () => ({
      selectionKind: view.getByTestId("review-center").dataset.selectionKind,
      centreControls: view.getAllByTestId("review-center-roster-next").length,
      filter: (view.getByTestId("review-family-filter") as HTMLInputElement).value,
      layout: view.getByTestId("review-workspace").dataset.diffLayout,
      fullFile: view.getByTestId("review-workspace").dataset.fullFile,
    });
    expect(state()).toEqual({
      selectionKind: "family",
      centreControls: 2,
      filter: "anchor",
      layout: "inline",
      fullFile: "true",
    });

    // TWO page requests, each issued by the CENTRE's own continuation control -- the control that was
    // single-use while the state was owned below the pane switch.
    for (const step of [1, 2]) {
      const control = view.getAllByTestId("review-center-roster-next")[0];
      const published = control.dataset.continuation ?? "";
      expect(published).not.toBe("");
      fireEvent.click(control);
      await waitFor(() => expect(urls.length).toBe(step + 1));
      expect(decodeURIComponent(urls[step])).toContain(published);
      expect(urls[step]).toContain("pageOf=family_members");
      // Every value survives, and the control is there to be used again.
      await waitFor(() => expect(state().selectionKind).toBe("family"));
      expect(state()).toEqual({
        selectionKind: "family",
        centreControls: 2,
        filter: "anchor",
        layout: "inline",
        fullFile: "true",
      });
    }
    expect(urls).toHaveLength(3);
  });

  // B3 (the accepted design's finding P2-3). The narrow-screen route to the review must be composed
  // ABOVE the family tree, because the tree's own height is the reason the affordance exists: the
  // finding records the rail at 2,298px pushing the review to y=2,821px, and its fix puts the
  // affordance "near the top" at y≈307 "while keeping the full family/sibling tree intact". Composed
  // immediately before the centre column instead, the control lands below the whole tree — measured on
  // the mounted product at y=1183 in a 900px viewport, i.e. reachable only after the scroll it exists
  // to avoid. This case pins the composition order, not a pixel: a jsdom render has no layout, and the
  // pixels are the mounted capture's job.
  it("renders the family's changed expression excerpts, deduplicated, over the captured family", async () => {
    // A4's third half: "Whole-family selection presents the full guarantee, all members including the
    // unchanged sibling, and deduplicated changed expression excerpts" (RENDER-CHECKLIST.md:46). The
    // body is the real captured one and the expectation is computed from that body here rather than
    // typed, so a re-captured body moves the expectation with it. What it pins is that the rendered
    // count is the DISTINCT set of excerpts, never the number of rows that named them.
    const expected = familyExpressionArithmetic(WALK_FINAL);
    expect(expected.rows).toBeGreaterThan(expected.distinct);
    expect(expected.divergent.length).toBeGreaterThan(0);

    serving([WALK_FINAL]);
    const view = mount();
    const openers = await view.findAllByTestId("review-family-open");
    const opener = openers.find((node) => node.dataset.family === expected.familyId);
    expect(opener, `the tree did not render the family ${expected.familyId} the body records`).toBeTruthy();
    fireEvent.click(opener as HTMLElement);
    await view.findByTestId("review-center-family");

    // The collection is present in the family view at all -- the defect this case exists for was that
    // the centre went family state -> guarantee -> roster -> source explorer with no expressions.
    const section = view.getByTestId("review-center-family-expressions");
    const rows = within(section).getAllByTestId("review-center-family-expression");
    expect(rows).toHaveLength(expected.distinct);
    expect(rows.length).not.toBe(expected.rows);

    // Every row's collapse count is the body's own group size, and the counts add up to the row total
    // the verdict states -- so the rendered number is the deduplicated set and not a member-row count.
    const collapsedPerRow = rows.map((row) => Number(row.dataset.collapsedRows)).sort((left, right) => left - right);
    expect(collapsedPerRow).toEqual([...expected.groupSizes].sort((left, right) => left - right));
    expect(collapsedPerRow.reduce((total, value) => total + value, 0)).toBe(expected.rows);
    expect(Math.max(...collapsedPerRow)).toBeGreaterThan(1);

    // A row whose two sides read one address differently names BOTH readings, which is the fact a
    // first-wins reading of the member rows would have hidden (the captured body records exactly this
    // for `src/batch.py`: resolved before, mismatched after). The expectation is the body's own
    // per-side reading, so the assertion is about the divergence, not about the word "before".
    expect(expected.divergent.length).toBeGreaterThan(0);
    for (const divergent of expected.divergent) {
      const row = rows.find((candidate) => candidate.dataset.path === divergent.path);
      expect(row, `no rendered excerpt names ${divergent.path}`).toBeTruthy();
      const resolution = row?.querySelector("[data-testid=review-center-family-expression-resolution]")?.textContent ?? "";
      const sides = (row?.dataset.sides ?? "").split(",").filter(Boolean);
      expect(sides.sort()).toEqual([...divergent.sides].sort());
      for (const reading of divergent.readings) {
        expect(resolution, `${divergent.path} does not print the ${reading.side} reading`).toContain(
          `${reading.side} ${reading.resolutions.join(" and ")}`,
        );
      }
    }

    // EVERY rendered row names exactly the sides the body resolves that excerpt on -- the page's own
    // sentence about both sides, checked row by row against the body rather than read from the page.
    for (const row of rows) {
      const group = expected.groups.find((candidate) => candidate.key === row.dataset.dedupKey);
      expect(group, `the rendered row ${row.dataset.dedupKey} is not an excerpt of the captured body`).toBeTruthy();
      expect((row.dataset.sides ?? "").split(",").filter(Boolean).sort()).toEqual([...(group?.sides ?? [])].sort());
    }

    // The verdict states both counts, so a reader can see the collapse without adding rows up.
    const verdict = view.getByTestId("review-center-family-expressions-verdict").textContent ?? "";
    expect(verdict).toContain(`${expected.rows} changed expression row(s)`);
    expect(verdict).toContain(`collapse to ${expected.distinct} distinct excerpt(s)`);
    expect(verdict).toContain("not the comparison's own measured change set");
    // The member roster A4's first half requires is untouched by the collection.
    expect(view.getByTestId("review-center-member-counts").textContent).toContain(
      "recorded membership row(s) measured by the read",
    );
    expect(view.getAllByTestId("review-center-open-member").length).toBeGreaterThan(1);
  });

  // The arithmetic this case's expectation is read from, computed from the captured body alone. A
  // changed expression is a realization claim whose recorded address did not resolve to the recorded
  // bytes ON THE SIDE THAT CARRIED IT; the excerpt's identity is its address together with the RECORDED
  // identity the claim names. The observed identity is deliberately NOT part of the identity: it is what
  // one side's read found at the address, so a divergent address has one observed value per side, and
  // folding it into the key splits that address into two excerpts that can never be paired -- the defect
  // this case exists for. Written here rather than imported from the component so the assertion is
  // checked against the BODY and not against the implementation it tests, and every step narrows at
  // runtime, the way `firstFamilyId` does, because a captured body is `unknown` on purpose.
  function familyExpressionArithmetic(body: unknown): {
    familyId: string;
    rows: number;
    distinct: number;
    groupSizes: number[];
    groups: { key: string; path: string; sides: string[] }[];
    divergent: { path: string; sides: string[]; readings: { side: string; resolutions: string[] }[] }[];
  } {
    const record = (value: unknown, what: string): Record<string, unknown> => {
      if (typeof value !== "object" || value === null) throw new Error(`${what} is not an object`);
      return value as Record<string, unknown>;
    };
    const payload = record(record(body, "the captured body").payload, "the captured payload");
    const context = record(payload.family_context, "the captured family context");
    if (!Array.isArray(context.entries)) throw new Error("the captured family context carries no entries");
    const resolved = new Set(["exact_recorded_blob"]);
    const unmeasured = new Set(["recorded_object_unavailable", "not_requested"]);
    let best: {
      familyId: string;
      rows: number;
      distinct: number;
      groupSizes: number[];
      groups: { key: string; path: string; sides: string[] }[];
      divergent: { path: string; sides: string[]; readings: { side: string; resolutions: string[] }[] }[];
    } | null = null;
    for (const rawEntry of context.entries) {
      const entry = record(rawEntry, "a family entry");
      const familyId = entry.family_id;
      if (typeof familyId !== "string") throw new Error("a family entry carries no identity");
      const groups = new Map<string, number>();
      // Every side whose read resolved an address, with the resolutions it gave -- the per-side fact.
      const readings = new Map<string, Map<string, Set<string>>>();
      let rows = 0;
      for (const side of ["before", "after"]) {
        const sideRecord = record(entry[side], `the ${side} side`);
        if (!Array.isArray(sideRecord.members)) throw new Error(`the ${side} side carries no members`);
        for (const rawMember of sideRecord.members) {
          const member = record(rawMember, "a member");
          if (!Array.isArray(member.sources)) continue;
          for (const rawSource of member.sources) {
            const source = record(rawSource, "a realization claim");
            const resolution = typeof source.resolution === "string" ? source.resolution : "";
            if (typeof source.path !== "string" || resolution === "") continue;
            const key = `${source.path}\u0000${String(source.recorded_source_identity ?? "")}`;
            if (!readings.has(key)) readings.set(key, new Map());
            const perSide = readings.get(key);
            if (!perSide?.has(side)) perSide?.set(side, new Set());
            perSide?.get(side)?.add(resolution);
            if (resolved.has(resolution) || unmeasured.has(resolution)) continue;
            rows += 1;
            groups.set(key, (groups.get(key) ?? 0) + 1);
          }
        }
      }
      if (rows === 0) continue;
      // An excerpt of this family, with the sides the body resolves it on -- what the page's own
      // sentence about both sides must be true of, row by row.
      const groupList = [...groups.keys()].map((key) => ({
        key,
        path: key.split("\u0000")[0],
        sides: [...(readings.get(key)?.keys() ?? [])].sort(),
      }));
      // A DIVERGENT address is one whose sides did not read it the same way. This is the predicate that
      // matters: "seen on more than one side" is not divergence, because a side can carry an address
      // and resolve it identically, and the shipped case must bite on the reading that differs.
      const divergent = [...groups.keys()]
        .filter((key) => {
          const distinct = new Set([...(readings.get(key)?.values() ?? [])].map((set) => [...set].sort().join("+")));
          return distinct.size > 1;
        })
        .map((key) => ({
          path: key.split("\u0000")[0],
          sides: [...(readings.get(key)?.keys() ?? [])].sort(),
          readings: [...(readings.get(key)?.entries() ?? [])]
            .map(([side, set]) => ({ side, resolutions: [...set].sort() }))
            .sort((left, right) => left.side.localeCompare(right.side)),
        }));
      const candidate = {
        familyId,
        rows,
        distinct: groups.size,
        groupSizes: [...groups.values()],
        groups: groupList,
        divergent,
      };
      if (best === null || candidate.rows > best.rows) best = candidate;
    }
    if (best === null) throw new Error("the captured body records no changed expression in any family");
    return best;
  }

  it("composes the narrow jump route above the family tree, with the tree intact", async () => {
    serving([COMPLETE]);
    const view = mount();

    const jump = await view.findByTestId("review-jump-to-selection");
    const tree = view.getByTestId("review-family-tree");
    // DOCUMENT_POSITION_FOLLOWING: the tree comes after the jump control in document order.
    expect(jump.compareDocumentPosition(tree) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(tree.compareDocumentPosition(jump) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();

    // "While keeping the full family/sibling tree intact": the tree is not trimmed, hidden or moved.
    expect(view.getAllByTestId("review-family")).toHaveLength(2);
    expect(view.getAllByTestId("review-family-member-open").length).toBeGreaterThan(0);

    // The route still does what it is for: it focuses this column, which is the whole of its effect.
    const centre = view.getByTestId("review-center-column");
    fireEvent.click(jump);
    expect(document.activeElement).toBe(centre);
  });
});
