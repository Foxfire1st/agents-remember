import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { DualPane } from "./DualPane";

afterEach(cleanup);

describe("DualPane empty-state backdrop", () => {
  it("fills the pane with the siege-tank boomerang backdrop when nothing is selected", () => {
    const { getByText, getByTestId, queryByTestId } = render(
      <DualPane code={null} sidecar={{ state: "empty" }} split={true} />,
    );
    expect(getByText(/Select a code file/i)).not.toBeNull();
    // The backdrop replaces the per-side "select a file" placeholders, using the new siege-tank clip.
    const video = getByTestId("empty-backdrop").querySelector("video");
    expect(video?.getAttribute("src")).toBe("/assets/sc2-siege-tank-boomerang.mp4");
    // The siege-tank loop reads darker, so it gets a brighter wash than the shared 0.14 default.
    expect((video as HTMLElement | null)?.style.opacity).toBe("0.18");
    expect(queryByTestId("pane-placeholder")).toBeNull();
  });
});

describe("DualPane partnerless overview rendering", () => {
  it("renders an overview's markdown full-pane in single mode (no code partner to split with)", () => {
    const { getByTestId, queryByTestId } = render(
      <DualPane
        code={null}
        sidecar={{ state: "markdown", body: "# Route Overview\n\nReadable prose." }}
        split={false}
      />,
    );
    const pane = getByTestId("sidecar-pane");
    expect(pane.textContent).toContain("Route Overview");
    expect(pane.textContent).toContain("Readable prose.");
    // The overview owns the whole pane — no "select a code file" placeholder.
    expect(queryByTestId("pane-placeholder")).toBeNull();
  });

  it("renders the overview markdown full-pane in split mode too (not an empty code split)", () => {
    const { getByTestId, queryByTestId } = render(
      <DualPane code={null} sidecar={{ state: "markdown", body: "# Overview" }} split={true} />,
    );
    expect(getByTestId("sidecar-pane").textContent).toContain("Overview");
    expect(queryByTestId("pane-placeholder")).toBeNull();
  });

  it("falls back to the no-code-partner placeholder when the overview body is unavailable", () => {
    const { getAllByTestId } = render(
      <DualPane code={null} sidecar={{ state: "overview" }} split={true} />,
    );
    const texts = getAllByTestId("pane-placeholder").map((node) => node.textContent ?? "");
    expect(texts.some((text) => text.includes("Overview"))).toBe(true);
  });
});
