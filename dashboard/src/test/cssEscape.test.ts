import { describe, expect, it } from "vitest";

describe("CSS.escape test setup stand-in (CSSOM identifier serialization)", () => {
  it("escapes a leading digit as a hex code point followed by a space", () => {
    expect(CSS.escape("0")).toBe("\\30 ");
    expect(CSS.escape("1abc")).toBe("\\31 abc");
    expect(CSS.escape("9-tail")).toBe("\\39 -tail");
  });

  it("escapes a hyphen followed by a digit per CSSOM rules", () => {
    expect(CSS.escape("-0")).toBe("-\\30 ");
    expect(CSS.escape("-1foo")).toBe("-\\31 foo");
    expect(CSS.escape("-9")).toBe("-\\39 ");
  });

  it("escapes a standalone hyphen and leaves double-hyphen custom properties intact", () => {
    expect(CSS.escape("-")).toBe("\\-");
    expect(CSS.escape("--custom-prop")).toBe("--custom-prop");
    expect(CSS.escape("-foo")).toBe("-foo");
  });

  it("handles NULL, ASCII control characters, punctuation, and plain identifiers", () => {
    expect(CSS.escape("")).toBe("");
    expect(CSS.escape("\0")).toBe("\uFFFD");
    expect(CSS.escape("\x01a\x7f")).toBe("\\1 a\\7f ");
    expect(CSS.escape("a:b.c[0]")).toBe("a\\:b\\.c\\[0\\]");
    expect(CSS.escape("plain_ident-2")).toBe("plain_ident-2");
  });

  it("produces valid querySelector attribute selectors for leading-digit and hyphen-digit values", () => {
    const root = document.createElement("div");
    for (const key of ["0", "1item", "-2item", "-", "a:b.c"]) {
      const el = document.createElement("span");
      el.setAttribute("data-key", key);
      root.appendChild(el);
      expect(root.querySelector(`[data-key=${CSS.escape(key)}]`)).toBe(el);
      expect(root.querySelector(`[data-key="${CSS.escape(key)}"]`)).toBe(el);
    }
  });
});
