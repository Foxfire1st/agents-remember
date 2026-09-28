// R03 (actual source-content inspection) at the real renderer: the Intent Reviewer surface opening a
// listed inventory entry, over the intent-review transport and the source-content route.
//
// WHAT THIS EXERCISES. `ReviewSurface` is the real component and `reviewSourceContent` the real
// client: only `fetch` is stubbed, so both requests travel the same way the browser's do (URL, JSON
// decode, component tree, and the shipped `DiffPane`/`FilePane` CodeMirror primitives, which render
// in jsdom). Nothing here renders a stand-in for the expansion, and no assertion reads a prop this
// test itself passed: every case asserts what the rendered DOM contains, and the generation case
// asserts the exact request the surface made.
//
// WHERE THE VALUES COME FROM. The side states, the object identities and the texts below are the
// shape the production route returns, asserted against real Git objects by
// `mcp/tests/test_knowledge_review_source_content.py`; this module is the renderer half of the same
// requirement, and a change to either half's contract fails in one of the two modules.
//
// THE DEFECT THESE CASES CATCH. The Source pane printed a path and a command and had no
// source-content interaction at all: an "expansion labelled full diff" showed no bytes. Every case
// below fails against that pane, because the file's own text -- or the explicit state that says why
// there is none -- is only in the DOM when the entry is really opened.

import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  ReviewChangedFile,
  ReviewPayload,
  ReviewResult,
  ReviewSourceContentResult,
  ReviewSourceExpansion,
  ReviewSourceSide,
  ReviewUnrepresentablePath,
} from "../../data/review";
import { ReviewSurface } from "./ReviewSurface";

const REPO = "agents-remember";
const MASTER = "260921_complete-code-and-intent-review";
const LEAF = "260921-ICR-L3";

const BEFORE_TREE = "1f2e3d4c5b6a79887766554433221100ffeeddcc";
const AFTER_TREE = "00112233445566778899aabbccddeeff00112233";

// The exact texts a real read carries: an added file's whole candidate body, a modified file's two
// bodies, and a symlink's recorded target.
const ADDED_TEXT = "# unmapped\nthis file carries no recorded realization\n";
const MODIFIED_BEFORE = "# batch\none transaction\n";
const MODIFIED_AFTER = "# batch\napplied by a scheduled sweep\n";
const SYMLINK_TARGET = "batch.py";
const SUBMODULE_COMMIT = "76236df88ef6c8d6e030c6ae1e4620efecbb5fc0";

// A CodeMirror editor renders its gutter and its own line elements, so the text is in the DOM with
// its whitespace reshaped by the view. Comparing with whitespace removed asserts the text itself
// rather than the editor's line layout.
const dense = (text: string | null | undefined) => (text ?? "").replace(/\s+/g, "");

function present(text: string, objectId: string, byteLength: number): ReviewSourceSide {
  return {
    state: "present",
    text,
    detail: `the complete content of this endpoint's ${byteLength}-byte object`,
    object_id: objectId,
    byte_length: byteLength,
    truncated: false,
  };
}

const absent = (detail: string): ReviewSourceSide => ({
  state: "absent",
  detail,
  truncated: false,
});

const binary = (objectId: string, size: number): ReviewSourceSide => ({
  state: "binary",
  detail: `this endpoint holds ${size} byte(s) whose first 8000 contain a NUL byte, so the content is binary`,
  object_id: objectId,
  byte_length: size,
  truncated: false,
});

const symlink = (target: string, objectId: string): ReviewSourceSide => ({
  state: "symlink",
  text: target,
  detail: "this endpoint's entry is a symlink, so the text shown is the link target recorded in the tree",
  object_id: objectId,
  byte_length: target.length,
  truncated: false,
});

const submodule = (commit: string): ReviewSourceSide => ({
  state: "submodule",
  detail: `this endpoint's entry is a submodule pointer to the recorded commit ${commit}`,
  object_id: commit,
  truncated: false,
});

function entry(path: string, over: Partial<ReviewChangedFile> = {}): ReviewChangedFile {
  return { path, status: "modified", content: "text", mode_change: false, ...over };
}

function payload(entries: ReviewChangedFile[], over: Partial<ReviewPayload> = {}): ReviewPayload {
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
      before_code_tree_id: BEFORE_TREE,
      after_code_tree_id: AFTER_TREE,
      knowledge_compared: true,
    },
    knowledge: {
      invariant_ids: [],
      family_ids: [],
      before_statement: { state: "unresolved", language: "text", detail: "no operand compared" },
      after_statement: { state: "unresolved", language: "text", detail: "no operand compared" },
      before_conditions: [],
      after_conditions: [],
      revision_groups: [],
      field_changes: [],
      authored_effects: [],
      signals: [],
      assessments: [],
      unresolved: [],
      selection_state: "task_context",
      selection_detail: "no subject selected",
    },
    source: {
      inventory: {
        state: "measured",
        entries,
        listed_total: entries.length,
        detail: `the two requested code trees differ at ${entries.length} path(s)`,
        partial: false,
        command: `git diff --raw -z --no-renames ${BEFORE_TREE} ${AFTER_TREE}`,
        before_code_tree_id: BEFORE_TREE,
        after_code_tree_id: AFTER_TREE,
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

function expansion(over: Partial<ReviewSourceExpansion> = {}): ReviewSourceExpansion {
  return {
    path: "src/unmapped.py",
    status: "added",
    mode_change: false,
    language: "python",
    before: absent("this endpoint holds no entry at this path"),
    after: present(ADDED_TEXT, "aa11", 52),
    before_code_tree_id: BEFORE_TREE,
    after_code_tree_id: AFTER_TREE,
    currentness: "current",
    currentness_detail: "the requested candidate tree is the tree this leaf's review binds now",
    path_bound: "requested_generation",
    path_bound_detail:
      "the requested generation's own change set is the measurement that lists this path",
    admission: "changed",
    admission_detail: "a measured change set lists this path as changed",
    reference: "review:source-content-of-one-inventory-entry-at-the-bound-tree-pair",
    command: `before: git -C /repo ls-tree -l ${BEFORE_TREE} -- src/unmapped.py\nafter: git -C /wt ls-tree -l ${AFTER_TREE} -- src/unmapped.py ; git -C /wt cat-file blob aa11`,
    ...over,
  };
}

const content = (over: Partial<ReviewSourceExpansion> = {}): ReviewSourceContentResult => ({
  state: "content",
  operation: "read_review_source_content",
  repository_id: REPO,
  expansion: expansion(over),
});

const refused = (detail: string): ReviewSourceContentResult => ({
  state: "refused",
  operation: "read_review_source_content",
  repository_id: REPO,
  refusal: {
    code: "source_content_unresolved",
    detail,
    next_action: "expand a path the inventory listed for this generation",
    offending_input: "src/anchors.py",
  },
});

// The surface reads two routes; the stub is the transport, not a stand-in for a pane. The expansion
// answer is selected by the path the surface asked about, and the request URL is returned so a case
// can assert the generation the surface sent.
function serve(
  review: ReviewResult,
  expansions: Record<string, ReviewSourceContentResult>,
): ReturnType<typeof vi.fn> {
  const fetchFn = vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    if (url.includes("/api/review/intent/source-content")) {
      const params = new URL(url, "http://localhost").searchParams;
      const path = params.get("path") ?? "";
      const answer = expansions[path];
      if (!answer) throw new Error(`no stubbed expansion for ${path}`);
      return { ok: true, status: 200, json: async () => answer } as unknown as Response;
    }
    return { ok: true, status: 200, json: async () => review } as unknown as Response;
  });
  vi.stubGlobal("fetch", fetchFn);
  return fetchFn;
}

function reviewed(
  entries: ReviewChangedFile[],
  expansions: Record<string, ReviewSourceContentResult>,
  payloadOver: Partial<ReviewPayload> = {},
) {
  const fetchFn = serve(
    {
      state: "review",
      operation: "review_intent",
      repository_id: REPO,
      payload: payload(entries, payloadOver),
    },
    expansions,
  );
  const view = render(<ReviewSurface repo={REPO} master={MASTER} leaf={LEAF} onBack={vi.fn()} />);
  return { view, fetchFn };
}

async function open(view: ReturnType<typeof render>, path: string) {
  const buttons = await view.findAllByTestId("review-inventory-open");
  const button = buttons.find((candidate) => candidate.dataset.path === path);
  if (!button) throw new Error(`no openable entry for ${path}`);
  fireEvent.click(button);
  return button;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("the source pane opening a listed entry", () => {
  it("draws an added file's entire candidate text beside the named absent side", async () => {
    const { view } = reviewed(
      [entry("src/unmapped.py", { status: "added" })],
      { "src/unmapped.py": content() },
    );

    await open(view, "src/unmapped.py");

    const before = await view.findByTestId("review-source-before-state");
    expect(before.dataset.sideState).toBe("absent");
    expect(before.textContent).toContain("holds no entry at this path");
    expect(view.getByTestId("review-source-after-state").dataset.sideState).toBe("present");
    // The file's own bytes are in the DOM, drawn as content: an "expansion" that printed a path or a
    // command instead would fail here.
    await waitFor(() =>
      expect(dense(view.getByTestId("review-source-after-content").textContent)).toContain(
        dense(ADDED_TEXT),
      ),
    );
    expect(view.getByTestId("review-source-no-diff-claimed").textContent).toContain(
      "no diff is drawn",
    );
  });

  it("draws a modified file as the two-sided diff of its two bound texts", async () => {
    const { view } = reviewed(
      [entry("src/batch.py")],
      {
        "src/batch.py": content({
          path: "src/batch.py",
          status: "modified",
          before: present(MODIFIED_BEFORE, "bb22", 24),
          after: present(MODIFIED_AFTER, "cc33", 39),
        }),
      },
    );

    await open(view, "src/batch.py");

    await waitFor(() => {
      const drawn = dense(view.getByTestId("diff-pane").textContent);
      expect(drawn).toContain(dense(MODIFIED_BEFORE));
      expect(drawn).toContain(dense(MODIFIED_AFTER));
    });
    expect(view.getByTestId("review-source-generation").textContent).toContain(
      `${BEFORE_TREE} → ${AFTER_TREE}`,
    );
  });

  it("states a binary side's identity and size and draws no content for it", async () => {
    const { view } = reviewed(
      [entry("src/asset.bin", { content: "binary", status: "added" })],
      {
        "src/asset.bin": content({
          path: "src/asset.bin",
          status: "added",
          language: "text",
          after: binary("dd44", 4096),
        }),
      },
    );

    await open(view, "src/asset.bin");

    const after = await view.findByTestId("review-source-after-state");
    expect(after.dataset.sideState).toBe("binary");
    expect(after.textContent).toContain("dd44");
    expect(after.textContent).toContain("4096 byte(s)");
    expect(view.queryByTestId("diff-pane")).toBeNull();
    expect(view.queryByTestId("review-source-after-content")).toBeNull();
  });

  it("carries a symlink's target as content and never claims a document edit", async () => {
    const { view } = reviewed(
      [entry("src/link_to_batch.py", { content: "symlink", status: "added" })],
      {
        "src/link_to_batch.py": content({
          path: "src/link_to_batch.py",
          status: "added",
          after: symlink(SYMLINK_TARGET, "ee55"),
        }),
      },
    );

    await open(view, "src/link_to_batch.py");

    const after = await view.findByTestId("review-source-after-state");
    expect(after.dataset.sideState).toBe("symlink");
    expect(after.textContent).toContain("link target");
    await waitFor(() =>
      expect(dense(view.getByTestId("review-source-after-content").textContent)).toContain(
        dense(SYMLINK_TARGET),
      ),
    );
    expect(view.queryByTestId("diff-pane")).toBeNull();
  });

  it("reports a submodule pointer by its recorded commit and draws nothing for it", async () => {
    const { view } = reviewed(
      [entry("vendor/lib", { content: "submodule", status: "added" })],
      {
        "vendor/lib": content({
          path: "vendor/lib",
          status: "added",
          after: submodule(SUBMODULE_COMMIT),
        }),
      },
    );

    await open(view, "vendor/lib");

    const after = await view.findByTestId("review-source-after-state");
    expect(after.dataset.sideState).toBe("submodule");
    expect(after.textContent).toContain(SUBMODULE_COMMIT);
    expect(view.queryByTestId("review-source-after-content")).toBeNull();
    expect(view.queryByTestId("diff-pane")).toBeNull();
  });

  it("labels a superseded generation while still showing the listed generation's text", async () => {
    const { view } = reviewed(
      [entry("src/unmapped.py", { status: "added" })],
      {
        "src/unmapped.py": content({
          currentness: "superseded",
          currentness_detail: `the leaf's candidate tree has moved since this generation was listed (requested ${AFTER_TREE}, bound now 99887766554433221100aabbccddeeff00112233)`,
        }),
      },
    );

    await open(view, "src/unmapped.py");

    const line = await view.findByTestId("review-source-currentness");
    expect(view.getByTestId("review-source-expansion").dataset.currentness).toBe("superseded");
    expect(line.textContent).toContain("has moved since this generation was listed");
    // The listed bytes stay on screen: the newer generation is named, never substituted.
    await waitFor(() =>
      expect(dense(view.getByTestId("review-source-after-content").textContent)).toContain(
        dense(ADDED_TEXT),
      ),
    );
  });

  it("states a bounded expansion as a prefix of the object", async () => {
    const { view } = reviewed(
      [entry("src/oversized.py", { status: "added" })],
      {
        "src/oversized.py": content({
          path: "src/oversized.py",
          status: "added",
          after: {
            state: "present",
            text: "# a line of the oversized file\n",
            detail:
              "this endpoint's object is 2099134 byte(s) and the text above is the first 2097152 byte(s) of it",
            object_id: "ff66",
            byte_length: 2099134,
            truncated: true,
          },
        }),
      },
    );

    await open(view, "src/oversized.py");

    const note = await view.findByTestId("review-source-truncated");
    expect(note.textContent).toContain("bounded expansion");
    expect(view.getByTestId("review-source-after-state").dataset.sideTruncated).toBe("true");
    expect(view.getByTestId("review-source-after-state").textContent).toContain("2099134 byte(s)");
  });

  it("renders a refused entry read as its typed refusal and no content", async () => {
    const { view } = reviewed(
      [entry("src/anchors.py", { status: "unknown" })],
      {
        "src/anchors.py": refused(
          "'src/anchors.py' is not one of the 9 changed path(s) this surface measured between the requested trees",
        ),
      },
    );

    await open(view, "src/anchors.py");

    const block = await view.findByTestId("review-source-refusal");
    expect(block.textContent).toContain("source_content_unresolved");
    expect(block.textContent).toContain("is not one of the 9 changed path(s)");
    expect(block.textContent).toContain("next:");
    expect(view.queryByTestId("diff-pane")).toBeNull();
    expect(view.queryByTestId("review-source-after-content")).toBeNull();
  });

  it("sends the generation and path the listing published, not a re-resolved one", async () => {
    const { view, fetchFn } = reviewed(
      [entry("src/batch.py"), entry("src/unmapped.py", { status: "added" })],
      {
        "src/batch.py": content({
          path: "src/batch.py",
          before: present(MODIFIED_BEFORE, "bb22", 24),
          after: present(MODIFIED_AFTER, "cc33", 39),
        }),
        "src/unmapped.py": content(),
      },
    );

    await open(view, "src/batch.py");
    // Let the expansion settle before reading the request log, so the assertion is about the
    // request the surface made and not about a state update that outlives the case.
    await waitFor(() => expect(view.getByTestId("diff-pane")).toBeTruthy());

    const expansionCall = fetchFn.mock.calls.find((call) =>
      String(call[0]).includes("/api/review/intent/source-content"),
    );
    expect(expansionCall).toBeDefined();
    const asked = new URL(String(expansionCall?.[0]), "http://localhost").searchParams;
    expect(asked.get("beforeCodeTreeId")).toBe(BEFORE_TREE);
    expect(asked.get("afterCodeTreeId")).toBe(AFTER_TREE);
    expect(asked.get("path")).toBe("src/batch.py");
    expect(asked.get("repo")).toBe(REPO);
    expect(asked.get("master")).toBe(MASTER);
    expect(asked.get("leaf")).toBe(LEAF);
  });

  it("states which measured change set admitted the path when the requested one could not be measured", async () => {
    const { view } = reviewed(
      [entry("src/batch.py")],
      {
        "src/batch.py": content({
          path: "src/batch.py",
          status: "unknown",
          before: present(MODIFIED_BEFORE, "bb22", 24),
          after: {
            state: "unavailable",
            detail: "the entry at this path could not be looked up in tree 0000…, so this side's content is unknown rather than absent",
            truncated: false,
          },
          currentness: "superseded",
          currentness_detail: "the leaf's candidate tree has moved since this generation was listed",
          path_bound: "leaf_change_set",
          path_bound_detail:
            "the requested generation could not be measured (the two requested code trees could not be compared), so the change set this leaf's review publishes -- its recorded baseline against the candidate tree it binds now, 13 changed path(s) -- is the measurement that lists this path",
        }),
      },
    );

    await open(view, "src/batch.py");

    const bound = await view.findByTestId("review-source-path-bound");
    expect(bound.textContent).toContain("could not be measured");
    expect(bound.textContent).toContain("13 changed path(s)");
    // The readable side is still drawn, and the unreadable one is named rather than left blank.
    expect(view.getByTestId("review-source-before-state").dataset.sideState).toBe("present");
    expect(view.getByTestId("review-source-after-state").dataset.sideState).toBe("unavailable");
  });

  it("lists a byte-form row without implying it can be opened", async () => {
    const byteRow: ReviewUnrepresentablePath = {
      path_bytes: "b'src/caf\\xe9-latin1.py'",
      status: "modified",
      mode_change: false,
      detail:
        "this changed path's name is not valid text under this code, so the bytes Git reported are carried here instead of a name",
    };
    const { view } = reviewed([entry("src/batch.py")], {}, {
      source: {
        inventory: {
          state: "measured",
          entries: [entry("src/batch.py")],
          listed_total: 1,
          detail: "the two requested code trees differ at 1 path(s)",
          partial: true,
          command: `git diff --raw -z --no-renames ${BEFORE_TREE} ${AFTER_TREE}`,
          before_code_tree_id: BEFORE_TREE,
          after_code_tree_id: AFTER_TREE,
          unrepresentable_paths: [byteRow],
        },
        locations: [],
        remaining: [],
        unattributed_changed_paths: [],
        attributed_changed_paths: [],
        unresolved: [],
      },
    });

    const row = await view.findByTestId("review-inventory-byte-path");
    expect(row.dataset.status).toBe("modified");
    expect(row.textContent).toContain(byteRow.path_bytes);
    expect(view.getByTestId("review-byte-path-not-addressable").textContent).toContain(
      "not openable through this surface",
    );
    // Exactly one openable row: the text entry. The byte-form row has no control that would promise
    // content this vocabulary cannot address.
    expect(view.getAllByTestId("review-inventory-open")).toHaveLength(1);
  });

  it("offers no expansion for an inventory that named no code trees", async () => {
    const { view } = reviewed(
      [entry("src/batch.py")],
      {},
      {
        source: {
          inventory: {
            state: "unavailable",
            entries: [],
            listed_total: 0,
            detail: "the two requested code trees could not be compared",
            partial: false,
            command: "git diff --raw -z --no-renames <no baseline tree requested> <no candidate tree requested>",
            unrepresentable_paths: [],
          },
          locations: [],
          remaining: [],
          unattributed_changed_paths: [],
          attributed_changed_paths: [],
          unresolved: [],
        },
      },
    );

    await view.findByTestId("review-inventory");
    expect(view.queryAllByTestId("review-inventory-open")).toHaveLength(0);
  });
});
