import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { FileDiff } from "../../data/changeset";
import { ChangeSetPane } from "./ChangeSetPane";

// Stub the CodeMirror primitives so the pane renders in jsdom without building a MergeView / editor —
// this test is about the markdown "rendered" mode, which uses react-markdown (jsdom-safe) instead.
vi.mock("./DiffPane", () => ({
  DiffPane: ({ after }: { after: string }) => <div data-testid="diff-pane">{after}</div>,
}));
vi.mock("../file-viewer/FilePane", () => ({
  FilePane: ({ content }: { content: string }) => <div data-testid="file-pane">{content}</div>,
}));

function mdDiff(over: Partial<FileDiff> = {}): FileDiff {
  return {
    scope: "wt-a",
    kind: "memory",
    path: "onboarding/dashboard/src/x.ts.md",
    language: "markdown",
    before: { content: "# Old\n" },
    after: { content: "## Heading\n\nReadable onboarding prose.\n" },
    ...over,
  } as FileDiff;
}

afterEach(() => {
  cleanup();
  window.localStorage.clear(); // the rendered/diff toggles persist per-column; isolate between tests
});

describe("ChangeSetPane markdown rendered view", () => {
  it("offers a 'rendered' toggle for markdown that draws the after-content as prose, not a raw diff", () => {
    const { getByTestId, queryByTestId } = render(
      <ChangeSetPane diff={mdDiff()} keyPrefix="t.main" />,
    );

    // Default: the diff (CodeMirror) view; the rendered prose is not shown yet.
    expect(getByTestId("diff-pane")).not.toBeNull();
    expect(queryByTestId("changeset-rendered")).toBeNull();

    // Flip to rendered: the markdown prose replaces the diff, formatted like the file reader's sidecar.
    fireEvent.click(getByTestId("changeset-rendered-toggle"));
    const rendered = getByTestId("changeset-rendered");
    expect(rendered.textContent).toContain("Heading");
    expect(rendered.textContent).toContain("Readable onboarding prose.");
    expect(queryByTestId("diff-pane")).toBeNull();
  });

  it("does not offer the rendered toggle for non-markdown files", () => {
    const codeDiff = mdDiff({
      kind: "code",
      path: "dashboard/src/x.ts",
      language: "typescript",
    });
    const { queryByTestId } = render(<ChangeSetPane diff={codeDiff} keyPrefix="t.code" />);
    expect(queryByTestId("changeset-rendered-toggle")).toBeNull();
    expect(queryByTestId("diff-pane")).not.toBeNull();
  });
});
