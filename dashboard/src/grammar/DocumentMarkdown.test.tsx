// The document rendering policy: GitHub's sanitized HTML, repository links and media, and no
// raw HTML in any ordinary dashboard Markdown.
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { DocumentContext } from "./DocumentMarkdown";
import { Markdown } from "./Markdown";

const context: DocumentContext = {
  repo: "R",
  scope: "mainline",
  path: "docs/guide.md",
  openFile: vi.fn(),
};

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      const path = new URL(url, "http://localhost").searchParams.get("path");
      return {
        ok: true,
        status: 200,
        json: async () => ({ exists: !(path ?? "").endsWith("gone.md"), path, kind: "file" }),
      } as unknown as Response;
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.mocked(context.openFile).mockClear();
});

describe("document HTML policy", () => {
  it("keeps GitHub's elements and drops script, handlers and style", () => {
    const { container, getByText } = render(
      <Markdown document={context}>
        {"## Guide\n\n<b>bold</b>\n\n<script>alert(1)</script>\n\n<img src=\"pic.png\" onerror=\"alert(1)\" />\n\n<iframe src=\"https://example.com\"></iframe>\n\n<div style=\"color:red\" class=\"shell__body\">styled</div>\n\n<a href=\"javascript:alert(1)\">bad</a>\n"}
      </Markdown>,
    );
    expect(getByText("bold").tagName).toBe("B");
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("iframe")).toBeNull();
    expect(container.querySelector("div[style]")).toBeNull();
    expect(container.querySelector("div.shell__body")).toBeNull();
    expect(container.querySelector('a[href^="javascript:"]')).toBeNull();
    const image = container.querySelector("img");
    expect(image?.getAttribute("onerror")).toBeNull();
    expect(image?.hasAttribute("style")).toBe(false);
  });

  it("prefixes a raw id and gives a heading the id it is linked by", () => {
    const { container, getByRole } = render(
      <Markdown document={context}>
        {"<h3 id=\"legacy\">Old</h3>\n\n## New heading\n"}
      </Markdown>,
    );
    expect(container.querySelector("#user-content-legacy")?.textContent).toBe("Old");
    expect(getByRole("heading", { name: "New heading" }).id).toBe("new-heading");
  });

  it("removes every permitted class from a cleaned document", () => {
    const { container } = render(
      <Markdown document={context}>
        {"<h2 class=\"sr-only shell__body\">Title</h2>\n\n<ul class=\"contains-task-list\"><li class=\"task-list-item\">task</li></ul>\n\n```ts\nconst x = 1;\n```\n"}
      </Markdown>,
    );
    const root = container.querySelector("[data-document-root]");
    expect(root?.querySelectorAll("[class]")).toHaveLength(0);
  });

  it("does not render raw HTML in ordinary dashboard Markdown", () => {
    const { container } = render(<Markdown>{"<script>alert(1)</script>\n\n<b>bold</b>"}</Markdown>);
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
  });
});

describe("document links", () => {
  it("opens a repository file that exists, with its anchor", async () => {
    const { findByTestId } = render(
      <Markdown document={context}>{"[the guide](guide.md#part)"}</Markdown>,
    );
    const link = await findByTestId("document-file-link");
    expect(link.getAttribute("title")).toBe("open docs/guide.md");
    fireEvent.click(link);
    expect(context.openFile).toHaveBeenCalledWith("docs/guide.md", "part");
  });

  it("opens a code file link in the same viewer", async () => {
    const { findByTestId } = render(
      <Markdown document={context}>{"[the code](pkg/mod.py)"}</Markdown>,
    );
    const link = await findByTestId("document-file-link");
    fireEvent.click(link);
    expect(context.openFile).toHaveBeenCalledWith("docs/pkg/mod.py", undefined);
  });

  it("opens a picture link in the same viewer", async () => {
    const { findByTestId } = render(
      <Markdown document={context}>{"[the picture](assets/pic.png)"}</Markdown>,
    );
    const link = await findByTestId("document-file-link");
    fireEvent.click(link);
    expect(context.openFile).toHaveBeenCalledWith("docs/assets/pic.png", undefined);
  });

  it("states a path the repository does not hold instead of linking it", async () => {
    const { findByTestId, queryByTestId } = render(
      <Markdown document={context}>{"[gone](gone.md)"}</Markdown>,
    );
    expect((await findByTestId("document-link-missing")).textContent).toContain("no such file");
    expect(queryByTestId("document-file-link")).toBeNull();
  });

  it("opens an outside https address in a new tab", () => {
    const { getByRole } = render(
      <Markdown document={context}>{"[outside](https://example.com/x)"}</Markdown>,
    );
    const link = getByRole("link", { name: "outside" });
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toContain("noopener");
  });

  it("decodes an encoded repository path and fragment before opening", async () => {
    const { findByTestId } = render(
      <Markdown document={context}>{"[space](my%20file.md#part%20one)"}</Markdown>,
    );
    const link = await findByTestId("document-file-link");
    expect(link.getAttribute("title")).toBe("open docs/my file.md");
    fireEvent.click(link);
    expect(context.openFile).toHaveBeenCalledWith("docs/my file.md", "part one");
  });

  it("refuses a malformed escape and an encoded traversal", () => {
    const { queryByTestId, getByText } = render(
      <Markdown document={context}>
        {"[bad](bad%ZZ.md) and [out](%2e%2e/%2e%2e/secret.md)"}
      </Markdown>,
    );
    expect(queryByTestId("document-file-link")).toBeNull();
    expect(getByText(/bad \(no such file\)/)).not.toBeNull();
  });

  it("scopes an anchor click to its own rendered document", () => {
    const seen: string[] = [];
    const spy = vi
      .spyOn(HTMLElement.prototype, "scrollIntoView")
      .mockImplementation(function (this: HTMLElement) {
        seen.push(this.textContent ?? "");
      });
    const { container } = render(
      <>
        <Markdown document={{ ...context, path: "docs/other.md" }}>{"## Part\n"}</Markdown>
        <Markdown document={context}>{"## Part\n\n[jump](#part)"}</Markdown>
      </>,
    );
    fireEvent.click(container.querySelector('[data-testid="document-anchor"]') as HTMLElement);
    expect(seen).toEqual(["Part"]);
    spy.mockRestore();
  });

  it("scrolls to a heading in the document", () => {
    const { getByTestId } = render(
      <Markdown document={context}>{"## Part\n\n[jump](#part)"}</Markdown>,
    );
    expect(() => fireEvent.click(getByTestId("document-anchor"))).not.toThrow();
  });
});

describe("no rendered Markdown moves the dashboard away", () => {
  it("keeps a relative link from moving the dashboard's own tab", () => {
    const { container } = render(<Markdown>{"[other](other/file.md)"}</Markdown>);
    const link = container.querySelector('[data-testid="link-not-followed"]') as HTMLAnchorElement;
    expect(link).not.toBeNull();
    expect(link.getAttribute("href")).toBe("other/file.md");
    // The click is cancelled: the dashboard does not navigate to an address it does not serve.
    const event = new MouseEvent("click", { bubbles: true, cancelable: true });
    link.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
  });

  it("opens an outside address in a new tab and keeps an anchor in the page", () => {
    const { getByRole, container } = render(
      <Markdown>{"[outside](https://example.com/x) · [section](#proof)"}</Markdown>,
    );
    const outside = getByRole("link", { name: "outside" });
    expect(outside.getAttribute("target")).toBe("_blank");
    expect(container.querySelector('a[href="#proof"]')).not.toBeNull();
  });
});

describe("document media", () => {
  it("draws a repository picture from the bytes route", () => {
    const { container } = render(
      <Markdown document={context}>{"![logo](logo.png)"}</Markdown>,
    );
    const image = container.querySelector("img");
    expect(image?.getAttribute("src")).toBe(
      "/api/files/bytes?repo=R&scope=mainline&path=docs%2Flogo.png",
    );
    expect(image?.getAttribute("loading")).toBe("lazy");
  });

  it("draws an outside https picture without a referrer and refuses the rest", () => {
    const { container } = render(
      <Markdown document={context}>
        {"![a](https://example.com/a.png)\n\n![b](http://example.com/b.png)"}
      </Markdown>,
    );
    const images = container.querySelectorAll("img");
    expect(images).toHaveLength(1);
    expect(images[0].getAttribute("referrerpolicy")).toBe("no-referrer");
    expect(container.querySelector('[data-testid="document-image-refused"]')?.textContent).toBe("b");
  });

  it("resolves every srcset candidate through the same media policy", () => {
    const { container } = render(
      <Markdown document={context}>
        {"![logo](logo.png)\n\n<img src=\"logo.png\" srcSet=\"http://example.com/bad.png 1x, logo.png 2x\" />\n"}
      </Markdown>,
    );
    const images = container.querySelectorAll("img");
    expect(images).toHaveLength(2);
    expect(images[1].getAttribute("srcset")).toBe(
      "/api/files/bytes?repo=R&scope=mainline&path=docs%2Flogo.png 2x",
    );
    expect(images[1].getAttribute("srcset")).not.toContain("example.com");
  });

  it("does not split a comma inside a data URL into an HTTPS candidate", () => {
    const { container } = render(
      <Markdown document={context}>
        {"<img src=\"logo.png\" srcSet=\"data:image/png,https://forbidden.example/x.png 1x, logo.png 2x\" />"}
      </Markdown>,
    );
    const image = container.querySelector("img");
    expect(image?.getAttribute("srcset")).toBe(
      "/api/files/bytes?repo=R&scope=mainline&path=docs%2Flogo.png 2x",
    );
    expect(image?.getAttribute("srcset")).not.toContain("forbidden.example");
    expect(image?.getAttribute("srcset")).not.toContain("data:image/png");
  });

  it("keeps a nested source's address and type", () => {
    const { container } = render(
      <Markdown document={context}>
        {"<video controls><source src=\"clip.mp4\" type=\"video/mp4\"><source srcSet=\"http://example.com/bad.mp4 1x, clip.webm 2x\"></video>"}
      </Markdown>,
    );
    const sources = container.querySelectorAll("video source");
    expect(sources[0].getAttribute("src")).toBe(
      "/api/files/bytes?repo=R&scope=mainline&path=docs%2Fclip.mp4",
    );
    expect(sources[0].getAttribute("type")).toBe("video/mp4");
    expect(sources[1].getAttribute("srcset")).toBe(
      "/api/files/bytes?repo=R&scope=mainline&path=docs%2Fclip.webm 2x",
    );
    expect(sources[1].getAttribute("srcset")).not.toContain("example.com");
  });

  it("draws a repository video and refuses a video it cannot fetch", () => {
    const { container } = render(
      <Markdown document={context}>
        {"<video src=\"clip.mp4\"></video>\n\n<video src=\"http://example.com/clip.mp4\"></video>"}
      </Markdown>,
    );
    const source = container.querySelector("video source");
    expect(source?.getAttribute("src")).toBe(
      "/api/files/bytes?repo=R&scope=mainline&path=docs%2Fclip.mp4",
    );
    expect(container.querySelector('[data-testid="document-video-refused"]')).not.toBeNull();
  });

  it("waits for a link's resolution before drawing it as a link", async () => {
    const { queryByTestId, findByTestId } = render(
      <Markdown document={context}>{"[the guide](guide.md)"}</Markdown>,
    );
    expect(queryByTestId("document-file-link")).toBeNull();
    await waitFor(async () => expect(await findByTestId("document-file-link")).not.toBeNull());
  });
});

describe("document source-set parsing and media admission", () => {
  const logo = "/api/files/bytes?repo=R&scope=mainline&path=docs%2Flogo.png";
  const local = "/api/files/bytes?repo=R&scope=mainline&path=docs%2Flocal.png";
  const third = "/api/files/bytes?repo=R&scope=mainline&path=docs%2Fthird.png";
  const cases = [
    { name: "a descriptorless candidate followed by a density candidate", value: "logo.png, local.png 2x", expected: `${logo}, ${local} 2x` },
    { name: "several candidates without spaces after descriptor commas", value: "logo.png 1x,local.png 2x,third.png 3x", expected: `${logo} 1x, ${local} 2x, ${third} 3x` },
    { name: "leading and repeated trailing separators", value: ",, logo.png,,, local.png 2x", expected: `${logo}, ${local} 2x` },
    { name: "a forbidden HTTP candidate between allowed candidates", value: "logo.png 1x,http://forbidden.example/x.png 2x,local.png 3x", expected: `${logo} 1x, ${local} 3x` },
    { name: "a descriptorless forbidden candidate between allowed candidates", value: "logo.png, data:image/png;base64,YQ==, local.png 2x", expected: `${logo}, ${local} 2x` },
    { name: "a data URL whose comma precedes an HTTPS spelling", value: "logo.png 1x, data:image/png,https://forbidden.example/x.png 2x, local.png 3x", expected: `${logo} 1x, ${local} 3x` },
    { name: "a forbidden script candidate between allowed candidates", value: "logo.png 1x,javascript:forbidden.png 2x,local.png 3x", expected: `${logo} 1x, ${local} 3x` },
    { name: "a forbidden protocol-relative candidate between allowed candidates", value: "logo.png 1x,//forbidden.example/x.png 2x,local.png 3x", expected: `${logo} 1x, ${local} 3x` },
    { name: "a parenthesized descriptor without leaking its comma suffix", value: "logo.png 1x, bad.png (ignored,https://forbidden.example/x.png), local.png 2x", expected: `${logo} 1x, ${local} 2x` },
    { name: "an invalid descriptor between allowed candidates", value: "logo.png 1x,bad.png 1w 2x,local.png 2x", expected: `${logo} 1x, ${local} 2x` },
    { name: "a comma inside a repository URL", value: "logo,wide.png 1x, local.png 2x", expected: `/api/files/bytes?repo=R&scope=mainline&path=docs%2Flogo%2Cwide.png 1x, ${local} 2x` },
    { name: "an outside HTTPS candidate", value: "logo.png, https://allowed.example/local.png 2x", expected: `${logo}, https://allowed.example/local.png 2x` },
    { name: "width and height descriptors", value: "logo.png 300w 200h, local.png 600w", expected: `${logo} 300w 200h, ${local} 600w` },
    { name: "zero and exponent density descriptors", value: "logo.png 0x, local.png 1e+2x", expected: `${logo} 0x, ${local} 1e+2x` },
  ];

  for (const tag of ["img", "source"] as const) {
    it.each(cases)(`${tag} keeps $name`, ({ value, expected }) => {
      const html = tag === "img"
        ? `<img src="fallback.png" srcset="${value}">`
        : `<picture><source srcset="${value}"><img src="fallback.png"></picture>`;
      const { container } = render(<Markdown document={context}>{html}</Markdown>);
      expect(container.querySelector(tag)?.getAttribute("srcset")).toBe(expected);
      expect(container.querySelector("img")?.getAttribute("referrerpolicy")).toBe("no-referrer");
    });
  }

  it.each(["img", "source"] as const)("omits %s source sets with no admitted candidate", (tag) => {
    const value = "http://forbidden.example/x.png 1x,data:image/png;base64,YQ== 2x";
    const html = tag === "img"
      ? `<img src="fallback.png" srcset="${value}">`
      : `<picture><source srcset="${value}"><img src="fallback.png"></picture>`;
    const { container } = render(<Markdown document={context}>{html}</Markdown>);
    expect(container.querySelector(tag)?.hasAttribute("srcset")).toBe(false);
  });
});

describe("document fetch-bearing attributes", () => {
  const attributes = [
    { name: "img.src", html: (url: string) => `<img src="${url}">`, selector: "img", attribute: "src" },
    { name: "video.src", html: (url: string) => `<video src="${url}"></video>`, selector: "video source", attribute: "src" },
    { name: "video.poster", html: (url: string) => `<video poster="${url}"></video>`, selector: "video", attribute: "poster" },
    { name: "source.src", html: (url: string) => `<video><source src="${url}"></video>`, selector: "source", attribute: "src" },
  ];

  it.each(attributes)("resolves $name against the repository", ({ html, selector, attribute }) => {
    const { container } = render(<Markdown document={context}>{html("logo.png")}</Markdown>);
    expect(container.querySelector(selector)?.getAttribute(attribute)).toBe(
      "/api/files/bytes?repo=R&scope=mainline&path=docs%2Flogo.png",
    );
  });

  it.each(attributes)("keeps outside HTTPS in $name", ({ html, selector, attribute }) => {
    const url = "https://allowed.example/logo.png";
    const { container } = render(<Markdown document={context}>{html(url)}</Markdown>);
    expect(container.querySelector(selector)?.getAttribute(attribute)).toBe(url);
  });

  for (const url of ["http://forbidden.example/logo.png", "data:image/png,https://forbidden.example/logo.png", "javascript:forbidden.png", "//forbidden.example/logo.png"]) {
    it.each(attributes)(`refuses ${url} in $name`, ({ html, selector, attribute }) => {
      const { container } = render(<Markdown document={context}>{html(url)}</Markdown>);
      expect(container.querySelector(selector)?.getAttribute(attribute) ?? null).toBeNull();
    });
  }
});
