// ICR-R24@v3 at the mounted surface: the family tree, the unified central reading path, the complete
// source explorer, and the one family-roster walk control.
//
// WHAT THIS EXERCISES. `ReviewSurface` is the real component and `intentReview` the real client, so
// every request below is built by the shipped client and every response travels the way the
// browser's does (status, body, the shared decode in `data/reviewTransport.ts`, the component tree,
// the read cycle in `ReviewReadCycle.ts`). Only `fetch` is stubbed, and the bodies it is stubbed WITH
// are the real route's own: `familyReview.*.captured.json` holds the bytes `serving/review.py`
// published over the real application owners and the real store for one real enclosure, recorded by
// `temp/icr/probe-l24-family-body.py`. No assertion below reads a prop this test itself passed, and
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
function serving(queue: unknown[]): Serving {
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
      selectorKind={familyId === undefined ? "invariant" : "family"}
      selectorId={familyId ?? "473ba88c-c793-4ba7-981f-79d20a681029"}
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
    for (const opener of openers) {
      fireEvent.click(opener);
      await view.findByTestId("review-center-family");
      if (view.queryByTestId("review-center-guarantee-unchanged") !== null) {
        expect(
          view.getByTestId("review-center-guarantee-unchanged").textContent,
        ).toContain("selected the same family revision");
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
    expect(view.getByTestId("review-center-member-family").textContent).toContain("in family");
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
    expect(byFact("source")).toContain("location record(s) name this member revision");
    expect(byFact("assessment")).toContain("No member, membership or guarantee change creates one");
    expect(view.getByTestId("review-center-evidence")).toBeTruthy();
  });

  it("keeps the complete source explorer independent of the family selection", async () => {
    serving([COMPLETE]);
    const view = mount();
    const before = await view.findByTestId("review-source-explorer");
    const listedBefore = view.getByTestId("review-inventory").textContent ?? "";
    expect(view.getByTestId("review-population-scope").textContent).toContain(
      "it never removes one from this list",
    );
    expect(before.dataset.inventoryState).toBe("measured");

    fireEvent.click((await view.findAllByTestId("review-family-open"))[0]);
    await view.findByTestId("review-center-family");

    // The selection is an attribution lens: the explorer's own population sentence is unchanged by it.
    expect(view.getByTestId("review-inventory").textContent).toBe(listedBefore);
    expect(view.getByTestId("review-source-explorer")).toBeTruthy();
  });

  it("walks a family roster only from the cursor that family's own page published", async () => {
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
    expect(roster.textContent).toContain("membership row(s) and this page carried");

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

    // And the continued response renders as the page it is: the workspace names which family
    // revision's walk the cursor continues, from the page's own scope.
    const walk = await view.findByTestId("review-roster-walk");
    expect(walk.textContent).toContain("one family revision's roster walk");
    expect(walk.textContent).toContain("side=after");
    const bounds = view.getByTestId("review-page-bounds");
    expect(bounds.textContent).toContain("family_members");
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
    expect(context.textContent).toContain("this body carries no family context");
    expect(context.textContent).toContain("not a measured zero");
    expect(view.queryByTestId("review-family-list")).toBeNull();
    // The scope header states the same fact, and the complete source explorer is unaffected by it.
    expect(view.getByTestId("review-scope-families").textContent).toContain(
      "no family reading may be made from it",
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
    fireEvent.change(view.getByTestId("review-diff-layout"), { target: { value: "inline" } });
    await waitFor(() =>
      expect(view.getByTestId("review-workspace").dataset.diffLayout).toBe("inline"),
    );
    expect(opener.getAttribute("aria-expanded")).toBe("true");
    expect(view.getByTestId("review-display-state").textContent).toContain(`expanded: ${path}`);

    // And the full-file preference is one value shared by the explorer's bar and the centre column.
    fireEvent.click(view.getByTestId("review-center-full-file"));
    await waitFor(() =>
      expect(view.getByTestId("review-workspace").dataset.fullFile).toBe("false"),
    );
    expect(view.getByTestId("review-full-file").getAttribute("aria-pressed")).toBe("false");
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
    expect(scope.textContent).toContain("2 family context(s) and");
    fireEvent.change(view.getByTestId("review-family-filter"), {
      target: { value: "retry-budget-family" },
    });
    await waitFor(() =>
      expect(view.getByTestId("review-family-filter-scope").textContent).toContain(
        "This is a filter on this display only",
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
    expect(roster.textContent).toContain("records 2 membership row(s) and this page carried 0 of them");
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
    expect(counts).toContain("this page carried 0 member row(s) of them");
    expect(counts).toContain("the continuations beside the bounded rosters reach the rest");
    expect(view.getByTestId("review-center-member-distinct").textContent).toContain(
      "among the membership rows this page carried",
    );
    // The per-side owner lines and the walk control are the tree's own components, mounted here too.
    for (const line of view.getAllByTestId("review-center-roster")) {
      expect(line.textContent).toContain("membership row(s) and this page carried");
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
    expect(identical.textContent).toContain("different family revisions");
    expect(identical.textContent).toContain("A revision was authored between them; the text is what did not move.");
    expect(view.queryByTestId("review-center-guarantee-unchanged")).toBeNull();
    expect(identical.textContent).not.toContain("so the guarantee is unchanged");

    // Family 1 selected the SAME revision on both snapshots, which is the only shape allowed to say
    // the guarantee is unchanged.
    fireEvent.click(openers[0]);
    const unchanged = await view.findByTestId("review-center-guarantee-unchanged");
    expect(unchanged.textContent).toContain("selected the same family revision");
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
    const row = notCarried.parentElement;
    expect(row).not.toBeNull();
    fireEvent.click(within(row as HTMLElement).getByTestId("review-family-member-open"));

    const block = await view.findByTestId("review-center-member-not-on-page");
    expect(block.textContent).toContain("did not carry the revision content");
    expect(view.queryByTestId("review-center-member-one-sided")).toBeNull();
    expect(view.queryByTestId("review-center-member-unchanged")).toBeNull();
    expect(view.queryByTestId("review-center-member-changed")).toBeNull();
  });
  it("says a listed-but-uncarried row about the page, never that the snapshot records no row", async () => {
    const { urls } = serving([CONTINUED]);
    const view = mount();
    await view.findByTestId("review-family-tree");

    // Shape B: BOTH sides list the same member revision, and one side's row is `content_not_on_page` --
    // the membership row IS recorded and only its content fell outside this page. Which row that is, is
    // read from the DOM (a row recorded on both snapshots), and the centre is asked in turn, so the case
    // cannot be re-pointed by a re-captured body.
    const rows = view
      .getAllByTestId("review-family-member")
      .filter((node) => node.dataset.sides === "before+after");
    expect(rows.length).toBeGreaterThan(0);
    const said: string[] = [];
    for (const row of rows) {
      fireEvent.click(within(row).getByTestId("review-family-member-open"));
      await waitFor(() => expect(view.getByTestId("review-center")).toBeTruthy());
      const note = view.queryByTestId("review-center-member-one-sided-note");
      if (note === null) continue;
      said.push(note.textContent ?? "");
      if (!(note.textContent ?? "").includes("listed this revision's membership row")) continue;

      // The sentence is about the PAGE, and it names the side whose content IS on this page.
      expect(note.textContent).toContain("but did not carry its revision content");
      expect(note.textContent).toContain("side's content is below");
      expect(note.textContent).not.toContain("records no member row");
      expect(note.textContent).not.toContain("one-sided recorded statement");
      // Shape B is not the `not_on_page` shape: one side's content is on this page and is shown.
      expect(view.queryByTestId("review-center-member-not-on-page")).toBeNull();
      expect(view.getByTestId("review-center-member-one-sided")).toBeTruthy();
      expect(urls.length).toBe(1);

      // And the centre's own continuation control reaches the rest of that bounded walk, from the
      // cursor this page published -- the journey the false sentence used to sit at the end of.
      const control = view.getAllByTestId("review-center-roster-next")[0];
      const published = control.dataset.continuation ?? "";
      expect(published).not.toBe("");
      fireEvent.click(control);
      await waitFor(() => expect(urls.length).toBeGreaterThan(1));
      expect(urls[1]).toContain("pageOf=family_members");
      expect(decodeURIComponent(urls[1])).toContain(published);
      return;
    }
    throw new Error(
      `no both-sides member row produced the listed-but-uncarried sentence; the centre said: ${JSON.stringify(said)}`,
    );
  });

  it("says a bounded page's missing row about the page, never that the snapshot records none", async () => {
    serving([CONTINUED]);
    const view = mount();
    await view.findByTestId("review-family-tree");

    // A row listed on ONE side only, whose content that side DID carry, while the other side's roster
    // is a position in a bounded walk. The other snapshot may well record such a row; this page simply
    // did not reach one, and only the bounded sentence is true here.
    const rows = view
      .getAllByTestId("review-family-member")
      .filter((node) => node.dataset.sides !== "before+after");
    expect(rows.length).toBeGreaterThan(0);
    const said: string[] = [];
    for (const row of rows) {
      fireEvent.click(within(row).getByTestId("review-family-member-open"));
      await waitFor(() => expect(view.getByTestId("review-center")).toBeTruthy());
      const note = view.queryByTestId("review-center-member-one-sided-note");
      if (note === null) continue;
      said.push(note.textContent ?? "");
      if (!(note.textContent ?? "").includes("is a position in a bounded walk")) continue;
      expect(note.textContent).toContain("this page did not carry a");
      expect(note.textContent).toContain("the continuation beside it reaches the rows this page did not carry");
      expect(note.textContent).not.toContain("records no member row");
      return;
    }
    throw new Error(`no bounded missing-row sentence was rendered; the centre said: ${JSON.stringify(said)}`);
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
      "the pages before it carried the rows this one did not",
    );
    expect(lastPage[0].textContent).toContain("this page carried 11 of them");
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
    // layout and "changed regions only". A page read unmounts the workspace subtree -- the payload is
    // null while the answer is in flight -- so state owned below that switch would be discarded on
    // every page, which is what the round-4 verification measured (fix round 5, V10).
    fireEvent.click((await view.findAllByTestId("review-family-open"))[0]);
    await view.findByTestId("review-center-family");
    fireEvent.change(view.getByTestId("review-family-filter"), { target: { value: "anchor" } });
    fireEvent.change(view.getByTestId("review-diff-layout"), { target: { value: "inline" } });
    fireEvent.click(view.getByTestId("review-center-full-file"));
    await waitFor(() => expect(view.getByTestId("review-workspace").dataset.fullFile).toBe("false"));
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
      fullFile: "false",
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
        fullFile: "false",
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
