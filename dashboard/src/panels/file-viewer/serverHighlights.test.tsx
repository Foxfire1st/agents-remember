import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { SyntaxHighlights } from "../../data/highlights";
import type { FileDiff } from "../../data/changeset";
import { DiffPane } from "../changeset/DiffPane";
import { ChangeSetPane } from "../changeset/ChangeSetPane";
import { FilePane } from "./FilePane";
import { buildHighlightDecorations } from "./serverHighlights";

afterEach(() => {
  cleanup();
});

const PY_BEFORE = 'def old_fn():\n    return "a"\n';
const PY_BEFORE_HL: SyntaxHighlights = {
  version: 1,
  digest: "before-digest-01",
  kinds: ["keyword", "function", "string"],
  spans: [0, 3, 0, 4, 10, 1, 18, 24, 0, 25, 28, 2],
};

const PY_AFTER = 'def new_fn():\n    return 42\n';
const PY_AFTER_HL: SyntaxHighlights = {
  version: 1,
  digest: "after-digest-02",
  kinds: ["keyword", "function", "number"],
  spans: [0, 3, 0, 4, 10, 1, 18, 24, 0, 25, 27, 2],
};

describe("serverHighlights and CodeMirror 6 viewers", () => {
  it("renders server-computed Decoration.mark token classes in FilePane and layers with citationHighlight", () => {
    const view = render(
      <FilePane
        content={PY_BEFORE}
        language="python"
        highlights={PY_BEFORE_HL}
        highlightedLines={[2, 2]}
      />,
    );
    const keywords = [...view.container.querySelectorAll(".cm-ar-tok-keyword")].map((el) => el.textContent);
    const funcs = [...view.container.querySelectorAll(".cm-ar-tok-function")].map((el) => el.textContent);
    const strings = [...view.container.querySelectorAll(".cm-ar-tok-string")].map((el) => el.textContent);
    expect(keywords).toEqual(["def", "return"]);
    expect(funcs).toEqual(["old_fn"]);
    expect(strings).toEqual(['"a"']);
    expect(view.container.querySelector(".cm-citedLine")).not.toBeNull();
  });

  it("handles null, malformed, out-of-range, and drawnText trailing-newline clamped spans safely", () => {
    expect(buildHighlightDecorations(null, 10).size).toBe(0);
    expect(buildHighlightDecorations(undefined, 10).size).toBe(0);
    const malformed: SyntaxHighlights = {
      version: 1,
      digest: "bad",
      kinds: ["keyword", "unknownKind"],
      spans: [-5, 3, 0, 2, 10, 0, 10, 999, 0, 1, 2, 1, Number.NaN, 4, 0],
    };
    const deco = buildHighlightDecorations(malformed, 12);
    expect(deco.size).toBeGreaterThan(0);

    // Stripped trailing newline (drawnText) clamps trailing spans cleanly without throwing.
    const trimmed = PY_BEFORE.slice(0, -1);
    const view = render(<FilePane content={trimmed} language="python" highlights={PY_BEFORE_HL} />);
    expect(view.container.querySelectorAll(".cm-ar-tok-string")).toHaveLength(1);
  });

  it("applies independent before/after highlight compartments in split DiffPane and preserves DOM node on unchanged digest", () => {
    const view = render(
      <DiffPane
        before={PY_BEFORE}
        after={PY_AFTER}
        beforeHighlights={PY_BEFORE_HL}
        afterHighlights={PY_AFTER_HL}
        language="python"
        mode="split"
        collapse={false}
      />,
    );
    const mergeNode = view.container.querySelector(".cm-mergeView");
    expect(mergeNode).not.toBeNull();
    const leftEditor = view.container.querySelector(".cm-merge-a")!;
    const rightEditor = view.container.querySelector(".cm-merge-b")!;
    expect(leftEditor.querySelector(".cm-ar-tok-string")?.textContent).toBe('"a"');
    expect(leftEditor.querySelector(".cm-ar-tok-number")).toBeNull();
    expect(rightEditor.querySelector(".cm-ar-tok-number")?.textContent).toBe("42");
    expect(rightEditor.querySelector(".cm-ar-tok-string")).toBeNull();

    // Re-render with cloned equal-digest highlight objects (simulating unchanged 2.5s poll):
    // MergeView DOM node MUST stay identical (INV-QK8XNM).
    view.rerender(
      <DiffPane
        before={PY_BEFORE}
        after={PY_AFTER}
        beforeHighlights={{ ...PY_BEFORE_HL, spans: [...PY_BEFORE_HL.spans] }}
        afterHighlights={{ ...PY_AFTER_HL, spans: [...PY_AFTER_HL.spans] }}
        language="python"
        mode="split"
        collapse={false}
      />,
    );
    expect(view.container.querySelector(".cm-mergeView")).toBe(mergeNode);

    // Reconfigure compartment in place when digest changes (e.g. clearing highlights): still no remount.
    view.rerender(
      <DiffPane
        before={PY_BEFORE}
        after={PY_AFTER}
        beforeHighlights={null}
        afterHighlights={null}
        language="python"
        mode="split"
        collapse={false}
      />,
    );
    expect(view.container.querySelector(".cm-mergeView")).toBe(mergeNode);
    expect(view.container.querySelectorAll(".cm-ar-tok-keyword")).toHaveLength(0);
  });

  it("applies afterHighlights in inline DiffPane without breaking deleted chunks", () => {
    const view = render(
      <DiffPane
        before={PY_BEFORE}
        after={PY_AFTER}
        beforeHighlights={PY_BEFORE_HL}
        afterHighlights={PY_AFTER_HL}
        language="python"
        mode="inline"
        collapse={false}
      />,
    );
    expect(view.container.querySelector(".cm-ar-tok-number")?.textContent).toBe("42");
    expect(view.container.querySelector(".cm-deletedChunk")).not.toBeNull();
  });

  it("respects ChangeSetPane plain (highlight off) mode by suppressing token highlights", () => {
    const diff: FileDiff = {
      scope: "leaf",
      kind: "code",
      path: "app.py",
      language: "python",
      before: { content: PY_BEFORE, highlights: PY_BEFORE_HL },
      after: { content: PY_AFTER, highlights: PY_AFTER_HL },
    };
    const view = render(<ChangeSetPane diff={diff} keyPrefix="test-hl-toggle" />);
    expect(view.container.querySelectorAll(".cm-ar-tok-keyword").length).toBeGreaterThan(0);

    fireEvent.click(view.getByRole("button", { name: "change-set" }));
    fireEvent.click(view.getByRole("button", { name: "highlight" }));
    expect(view.container.querySelector("[data-testid='file-pane']")).not.toBeNull();
    expect(view.container.querySelectorAll(".cm-ar-tok-keyword")).toHaveLength(0);
  });
});
