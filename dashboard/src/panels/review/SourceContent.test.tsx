// R03 source inspection after MIK-R86: inventory files open in the existing ChangeSetViewer.
// Only fetch is stubbed: the real viewer/client and CodeMirror panes draw the returned bytes.
// The six metadata cases use the real SourceContent/client retained by the cards' Full file:
// FileDiff has no object-state/currentness/truncation/path-bound fields. Those live facts are
// preserved here, not invented on the viewer or discarded with the removed inventory controls.
// Synthetic replies exercise rendering and request binding, not server/Git-object admission.

import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { dashboardStore } from "../../data/store";

import type {
  ReviewChangedFile,
  ReviewSourceInventory,
  ReviewSourceContentResult,
  ReviewSourceExpansion,
  ReviewSourceSide,
  ReviewUnrepresentablePath,
} from "../../data/review";
import type { TaskChangeset, FileDiff } from "../../data/changeset";
import { ChangeSetViewer } from "../changeset/ChangeSetViewer";
import { SourceContent } from "./SourceContent";

const REPO = "agents-remember";
const MASTER = "260921_complete-code-and-intent-review";
const LEAF = "260921-ICR-L3";
const COMPARISON = 43;

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
  detail:
    "this endpoint's entry is a symlink, so the text shown is the link target recorded in the tree",
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

function inventory(
  entries: ReviewChangedFile[],
  over: Partial<ReviewSourceInventory> = {},
): ReviewSourceInventory {
  return {
    state: "measured",
    entries,
    listed_total: entries.length,
    detail: `the two requested code trees differ at ${entries.length} path(s)`,
    partial: false,
    command: `git diff --raw -z --no-renames ${BEFORE_TREE} ${AFTER_TREE}`,
    before_code_tree_id: BEFORE_TREE,
    after_code_tree_id: AFTER_TREE,
    unrepresentable_paths: [],
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

// The comparison route carries FileDiff, not ReviewSourceExpansion. No renderer is mocked.
function reviewed(
  entries: ReviewChangedFile[],
  expansions: Record<string, ReviewSourceContentResult>,
  inventoryOver: Partial<ReviewSourceInventory> = {},
) {
  const listed = inventory(entries, inventoryOver);
  const changeset: TaskChangeset = {
    scope: LEAF,
    comparison: COMPARISON,
    trees: { codeBase: BEFORE_TREE, codeTip: AFTER_TREE, memoryBase: "mm11", memoryTip: "mm22" },
    code: entries.map((item) => ({
      path: item.path,
      status: item.status === "added" ? "A" : "M",
      insertions: item.content === "binary" ? null : 1,
      deletions: item.content === "binary" ? null : 0,
    })),
    memory: [],
    counters: {
      code: { files: entries.length, insertions: entries.length, deletions: 0 },
      memory: { files: 0, insertions: 0, deletions: 0 },
    },
  };
  const fetchFn = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://localhost");
    if (url.pathname === "/api/changeset/task") {
      if (listed.state === "unavailable") {
        return new Response(JSON.stringify(({ status: "not-found", path: listed.detail })), { status: 404 });
      }
      return new Response(JSON.stringify(changeset), { status: 200 });
    }
    if (url.pathname !== "/api/changeset/file-diff") throw new Error(`unexpected read ${url}`);
    const path = url.searchParams.get("path") ?? "";
    const result = expansions[path];
    if (!result) throw new Error(`no stubbed file diff for ${path}`);
    if (result.state === "refused") {
      return new Response(JSON.stringify(({ status: "not-found", path: result.refusal?.detail })), { status: 404 });
    }
    const opened = result.expansion!;
    const diff: FileDiff = {
      scope: LEAF,
      kind: "code",
      path,
      language: opened.language,
      before: opened.before.text === undefined ? null : { content: opened.before.text },
      after: opened.after.text === undefined ? null : { content: opened.after.text },
    };
    return new Response(JSON.stringify(diff), { status: 200 });
  });
  vi.stubGlobal("fetch", fetchFn);
  const view = render(
    <ChangeSetViewer
      repo={REPO}
      master={MASTER}
      leaf={LEAF}
      comparison={COMPARISON}
      reviewContext={{
        page: { comparison: COMPARISON, filter: "all" },
        inventory: listed,
        lane: null,
      }}
      onBack={vi.fn()}
    />,
  );
  return { view, fetchFn };
}

// SourceContent is the actual cards' Full file component (R86 rules17–18). It requests its own
// structured reply; this helper does not synthesize UI or mount the removed SourceExplorer.
function fullFile(
  entries: ReviewChangedFile[],
  expansions: Record<string, ReviewSourceContentResult>,
) {
  const fetchFn = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://localhost");
    if (url.pathname !== "/api/review/intent/source-content")
      throw new Error(`unexpected read ${url}`);
    const path = url.searchParams.get("path") ?? "";
    const result = expansions[path];
    if (!result) throw new Error(`no stubbed full file for ${path}`);
    return new Response(JSON.stringify(result), { status: 200 });
  });
  vi.stubGlobal("fetch", fetchFn);
  const view = render(
    <SourceContent
      repo={REPO}
      master={MASTER}
      leaf={LEAF}
      entry={entries[0]}
      beforeCodeTreeId={BEFORE_TREE}
      afterCodeTreeId={AFTER_TREE}
    />,
  );
  return { view, fetchFn };
}

async function open(view: ReturnType<typeof render>, path: string) {
  const label = await view.findByText(path);
  const button = label.closest("button");
  if (!button) throw new Error(`no openable entry for ${path}`);
  fireEvent.click(button);
}

afterEach(() => {
  cleanup();
  dashboardStore.getState().reset();
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("source inspection through the change-set page and retained card full files", () => {
  it("draws an added file's entire candidate text beside the named absent side", async () => {
    const { view } = reviewed([entry("src/unmapped.py", { status: "added" })], {
      "src/unmapped.py": content(),
    });

    await open(view, "src/unmapped.py");

    const diff = await view.findByTestId("diff-pane");
    await waitFor(() => {
      const sides = diff.querySelectorAll(".cm-content");
      expect(sides).toHaveLength(2);
      expect(dense(sides[0].textContent)).toBe("");
      expect(dense(sides[1].textContent)).toBe(dense(ADDED_TEXT));
    });
    view.unmount();
    const retained = fullFile([entry("src/unmapped.py", { status: "added" })], {
      "src/unmapped.py": content(),
    }).view;
    const before = await retained.findByTestId("review-source-before-state");
    expect(before.dataset.sideState).toBe("absent");
    expect(before.textContent).toContain("holds no entry at this path");
    expect(retained.getByTestId("review-source-after-state").dataset.sideState).toBe("present");
    expect(retained.getByTestId("review-source-no-diff-claimed").textContent).toContain(
      "no diff is drawn",
    );
  });

  it("draws a modified file as the two-sided diff of its two bound texts", async () => {
    const { view } = reviewed([entry("src/batch.py")], {
      "src/batch.py": content({
        path: "src/batch.py",
        status: "modified",
        before: present(MODIFIED_BEFORE, "bb22", 24),
        after: present(MODIFIED_AFTER, "cc33", 39),
      }),
    });

    await open(view, "src/batch.py");

    await waitFor(() => {
      const drawn = dense(view.getByTestId("diff-pane").textContent);
      expect(drawn).toContain(dense(MODIFIED_BEFORE));
      expect(drawn).toContain(dense(MODIFIED_AFTER));
    });
    expect(view.container.textContent).toContain(
      `${BEFORE_TREE.slice(0, 8)} → ${AFTER_TREE.slice(0, 8)}`,
    );
  });

  it("states a binary side's identity and size and draws no content for it", async () => {
    const { view } = fullFile([entry("src/asset.bin", { content: "binary", status: "added" })], {
      "src/asset.bin": content({
        path: "src/asset.bin",
        status: "added",
        language: "text",
        after: binary("dd44", 4096),
      }),
    });

    await view.findByTestId("review-source-expansion");

    const after = await view.findByTestId("review-source-after-state");
    expect(after.dataset.sideState).toBe("binary");
    expect(after.textContent).toContain("dd44");
    expect(after.textContent).toContain("4096 byte(s)");
    expect(view.queryByTestId("diff-pane")).toBeNull();
    expect(view.queryByTestId("review-source-after-content")).toBeNull();
  });

  it("carries a symlink's target as content and never claims a document edit", async () => {
    const { view } = fullFile(
      [entry("src/link_to_batch.py", { content: "symlink", status: "added" })],
      {
        "src/link_to_batch.py": content({
          path: "src/link_to_batch.py",
          status: "added",
          after: symlink(SYMLINK_TARGET, "ee55"),
        }),
      },
    );

    await view.findByTestId("review-source-expansion");

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
    const { view } = fullFile([entry("vendor/lib", { content: "submodule", status: "added" })], {
      "vendor/lib": content({
        path: "vendor/lib",
        status: "added",
        after: submodule(SUBMODULE_COMMIT),
      }),
    });

    await view.findByTestId("review-source-expansion");

    const after = await view.findByTestId("review-source-after-state");
    expect(after.dataset.sideState).toBe("submodule");
    expect(after.textContent).toContain(SUBMODULE_COMMIT);
    expect(view.queryByTestId("review-source-after-content")).toBeNull();
    expect(view.queryByTestId("diff-pane")).toBeNull();
  });

  it("labels a superseded generation while still showing the listed generation's text", async () => {
    const { view } = fullFile([entry("src/unmapped.py", { status: "added" })], {
      "src/unmapped.py": content({
        currentness: "superseded",
        currentness_detail: `the leaf's candidate tree has moved since this generation was listed (requested ${AFTER_TREE}, bound now 99887766554433221100aabbccddeeff00112233)`,
      }),
    });

    await view.findByTestId("review-source-expansion");

    const line = await view.findByText(
      (_, element) =>
        element?.tagName === "P" && Boolean(element.textContent?.startsWith("superseded:")),
    );
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
    const { view } = fullFile([entry("src/oversized.py", { status: "added" })], {
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
    });

    await view.findByTestId("review-source-expansion");

    const note = await view.findByTestId("review-source-truncated");
    expect(note.textContent).toContain("bounded expansion");
    expect(view.getByTestId("review-source-after-state").dataset.sideTruncated).toBe("true");
    expect(view.getByTestId("review-source-after-state").textContent).toContain("2099134 byte(s)");
  });

  it("renders a refused entry read as its typed refusal and no content", async () => {
    const { view } = reviewed([entry("src/anchors.py", { status: "unknown" })], {
      "src/anchors.py": refused(
        "'src/anchors.py' is not one of the 9 changed path(s) this surface measured between the requested trees",
      ),
    });

    await open(view, "src/anchors.py");

    const block = await view.findByTestId("changeset-refusal");
    expect(block.textContent).toContain("not-found (404)");
    expect(view.getByTestId("pane-placeholder").textContent).toContain(
      "is not one of the 9 changed path(s)",
    );
    expect(view.queryByTestId("diff-pane")).toBeNull();
    view.unmount();
    const retained = fullFile([entry("src/anchors.py", { status: "unknown" })], {
      "src/anchors.py": refused(
        "'src/anchors.py' is not one of the 9 changed path(s) this surface measured between the requested trees",
      ),
    }).view;
    const refusal = await retained.findByTestId("review-source-refusal");
    expect(refusal.textContent).toContain("source_content_unresolved");
    expect(refusal.textContent).toContain("is not one of the 9 changed path(s)");
    expect(refusal.textContent).toContain("next:");
    expect(retained.queryByTestId("diff-pane")).toBeNull();
    expect(retained.queryByTestId("review-source-after-content")).toBeNull();
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
      String(call[0]).includes("/api/changeset/file-diff"),
    );
    expect(expansionCall).toBeDefined();
    const asked = new URL(String(expansionCall?.[0]), "http://localhost").searchParams;
    expect(asked.get("comparison")).toBe(String(COMPARISON));
    expect(asked.get("mode")).toBeNull();
    expect(asked.get("kind")).toBe("code");
    expect(fetchFn.mock.calls.every((call) => !String(call[0]).includes("/api/review/"))).toBe(
      true,
    );
    expect(asked.get("path")).toBe("src/batch.py");
    expect(asked.get("repo")).toBe(REPO);
    expect(asked.get("master")).toBe(MASTER);
    expect(asked.get("leaf")).toBe(LEAF);
    view.unmount();
    const card = fullFile([entry("src/batch.py")], {
      "src/batch.py": content({
        path: "src/batch.py",
        before: present(MODIFIED_BEFORE, "bb22", 24),
        after: present(MODIFIED_AFTER, "cc33", 39),
      }),
    });
    await card.view.findByTestId("diff-pane");
    const cardQuery = new URL(String(card.fetchFn.mock.calls[0][0]), "http://localhost")
      .searchParams;
    expect(cardQuery.get("beforeCodeTreeId")).toBe(BEFORE_TREE);
    expect(cardQuery.get("afterCodeTreeId")).toBe(AFTER_TREE);
    expect(cardQuery.get("path")).toBe("src/batch.py");
    expect(cardQuery.get("repo")).toBe(REPO);
    expect(cardQuery.get("master")).toBe(MASTER);
    expect(cardQuery.get("leaf")).toBe(LEAF);
  });

  it("states which measured change set admitted the path when the requested one could not be measured", async () => {
    const { view } = fullFile([entry("src/batch.py")], {
      "src/batch.py": content({
        path: "src/batch.py",
        status: "unknown",
        before: present(MODIFIED_BEFORE, "bb22", 24),
        after: {
          state: "unavailable",
          detail:
            "the entry at this path could not be looked up in tree 0000…, so this side's content is unknown rather than absent",
          truncated: false,
        },
        currentness: "superseded",
        currentness_detail: "the leaf's candidate tree has moved since this generation was listed",
        path_bound: "leaf_change_set",
        path_bound_detail:
          "the requested generation could not be measured (the two requested code trees could not be compared), so the change set this leaf's review publishes -- its recorded baseline against the candidate tree it binds now, 13 changed path(s) -- is the measurement that lists this path",
      }),
    });

    await view.findByTestId("review-source-expansion");

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
    const { view, fetchFn } = reviewed(
      [entry("src/batch.py")],
      {
        "src/batch.py": content({ path: "src/batch.py", status: "modified" }),
      },
      { partial: true, unrepresentable_paths: [byteRow] },
    );
    const row = await view.findByText(
      (_, element) =>
        element?.tagName === "P" && Boolean(element.textContent?.includes(byteRow.path_bytes)),
    );
    expect(row.textContent).toContain(byteRow.path_bytes);
    expect(row.textContent).toContain(`cannot open: ${byteRow.detail}`);
    expect(row.querySelector("button")).toBeNull();
    expect((await view.findByText("src/batch.py")).closest("button")).not.toBeNull();
    await view.findByTestId("diff-pane");
    expect(
      fetchFn.mock.calls
        .filter((call) => String(call[0]).includes("/file-diff"))
        .every(
          (call) =>
            new URL(String(call[0]), "http://localhost").searchParams.get("path") ===
            "src/batch.py",
        ),
    ).toBe(true);
  });

  it("offers no expansion for an inventory that named no code trees", async () => {
    const { view, fetchFn } = reviewed(
      [],
      {},
      {
        state: "unavailable",
        detail: "the two requested code trees could not be compared",
        before_code_tree_id: undefined,
        after_code_tree_id: undefined,
      },
    );
    const refusal = await view.findByTestId("changeset-refusal");
    expect(refusal.textContent).toContain("not-found (404)");
    expect(view.getByTestId("pane-placeholder").textContent).toContain("could not be compared");
    expect(view.queryByTestId("changeset-empty")).toBeNull();
    expect(view.queryByText("src/batch.py")).toBeNull();
    expect(fetchFn.mock.calls.some((call) => String(call[0]).includes("/file-diff"))).toBe(false);
  });
});
