import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { FileContent } from "../../data/files";
import type { DocumentContext } from "../../grammar/DocumentMarkdown";
import { DualPane } from "./DualPane";

afterEach(cleanup);

function code(overrides: Partial<FileContent>): FileContent {
  return {
    scope: "mainline",
    path: "pkg/pic.png",
    language: "binary",
    size: 1234,
    truncated: false,
    content: "",
    onboarding: { status: "missing" },
    ...overrides,
  };
}

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

describe("DualPane media and rendered documents", () => {
  it("draws a picture from its bytes route, with its type and size under it", () => {
    const { getByTestId } = render(
      <DualPane
        code={code({ mediaType: "image/png" })}
        sidecar={{ state: "empty" }}
        split={true}
        mediaUrl="/api/files/bytes?repo=R&scope=mainline&path=pkg%2Fpic.png"
      />,
    );
    const image = getByTestId("media-image") as HTMLImageElement;
    expect(image.getAttribute("src")).toBe(
      "/api/files/bytes?repo=R&scope=mainline&path=pkg%2Fpic.png",
    );
    expect(getByTestId("media-caption").textContent).toContain("PNG");
    expect(getByTestId("media-caption").textContent).toContain("1,234");
  });

  it("states a video above the bytes bound instead of requesting it", () => {
    const { getByTestId } = render(
      <DualPane
        code={code({ path: "media/demo.mp4", mediaType: "video/mp4", tooLarge: true })}
        sidecar={{ state: "empty" }}
        split={false}
        mediaUrl="/api/files/bytes?repo=R&path=media%2Fdemo.mp4"
      />,
    );
    expect(getByTestId("media-too-large").textContent).toContain("too large");
    expect(getByTestId("media-too-large").textContent).toContain("MP4");
  });

  it("keeps the truncation warning in rendered document mode", () => {
    const document: DocumentContext = {
      repo: "R",
      scope: "mainline",
      path: "README.md",
      openFile: () => undefined,
    };
    const view = render(
      <DualPane
        code={code({
          path: "README.md",
          language: "markdown",
          size: 3_000_000,
          truncated: true,
          content: "# Long\n",
        })}
        sidecar={{ state: "empty" }}
        split={false}
        document={document}
        rendered
      />,
    );
    expect(view.getByTestId("truncation-banner").textContent).toContain("first 2 MiB");
  });

  it("renders a repository document only in document mode; source mode stays source", () => {
    const document: DocumentContext = {
      repo: "R",
      scope: "mainline",
      path: "README.md",
      openFile: () => undefined,
    };
    const markdown = code({
      path: "README.md",
      language: "markdown",
      size: 30,
      content: "# Read me\n\n<script>alert(1)</script>\n",
    });
    const rendered = render(
      <DualPane code={markdown} sidecar={{ state: "empty" }} split={false} document={document} rendered />,
    );
    const heading = rendered.getByRole("heading", { name: "Read me" });
    expect(heading.id).toBe("read-me");
    expect(rendered.container.querySelector("script")).toBeNull();
    rendered.unmount();
    const source = render(
      <DualPane code={markdown} sidecar={{ state: "empty" }} split={false} document={document} />,
    );
    expect(source.queryByTestId("document-rendered")).toBeNull();
    expect(source.getByTestId("dual-pane")).not.toBeNull();
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
