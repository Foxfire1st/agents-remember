import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { EvidenceTier } from "../data/sessionCockpitStore";
import { EVIDENCE_GLYPHS, EvidenceBadge, type EvidenceBadgeSize } from "./EvidenceBadge";

afterEach(cleanup);

// The EvidenceBadge grammar primitive (260715-FEUI-L3 R7): five DISTINCT glyphs so tiers never
// collapse at ambient sizes, and the tier WORD always present in the ACCESSIBLE name — at every
// size — so truncation/size can never strip the evidence word.

const TIERS: EvidenceTier[] = ["pending", "readback", "model-validated", "defaults", "refused"];
const SIZES: EvidenceBadgeSize[] = ["row", "sm"];

describe("EvidenceBadge (R7)", () => {
  it("ships five DISTINCT glyphs — no two tiers share a mark", () => {
    const glyphs = TIERS.map((tier) => EVIDENCE_GLYPHS[tier]);
    expect(new Set(glyphs).size).toBe(TIERS.length);
  });

  it("keeps the leaf-doc glyph anatomy: OK echo / diamond validated / dot defaults", () => {
    expect(EVIDENCE_GLYPHS.readback).toBe("✓");
    expect(EVIDENCE_GLYPHS["model-validated"]).toBe("◇");
    expect(EVIDENCE_GLYPHS.defaults).toBe("·");
    expect(EVIDENCE_GLYPHS.refused).toBe("✕");
    expect(EVIDENCE_GLYPHS.pending).toBe("…"); // provenance-in-flight, never a verification mark
  });

  it("the tier WORD is present in the accessible name at EVERY size", () => {
    for (const tier of TIERS) {
      for (const size of SIZES) {
        const { container, unmount } = render(<EvidenceBadge tier={tier} size={size} />);
        const badge = container.querySelector("[data-evidence-tier]");
        expect(badge?.getAttribute("data-evidence-size")).toBe(size);
        const accessibleName = badge?.getAttribute("aria-label") ?? "";
        expect(accessibleName).toContain(tier);
        unmount();
      }
    }
  });

  it("the glyph itself is aria-hidden — the word, not the mark, is the accessible content", () => {
    const { container } = render(<EvidenceBadge tier="refused" />);
    const glyph = container.querySelector("[aria-hidden='true']");
    expect(glyph?.textContent).toBe("✕");
    expect(container.querySelector("[role='img']")).not.toBeNull();
  });

  it("showWord renders the tier word visibly (banner usage)", () => {
    const { container } = render(<EvidenceBadge tier="refused" showWord />);
    expect(container.textContent).toContain("refused");
  });

  it("colours refusal as alarm and readback as mint (token classes)", () => {
    const refused = render(<EvidenceBadge tier="refused" />);
    expect(refused.container.querySelector("span")?.className).toContain("c_alarm");
    refused.unmount();
    const readback = render(<EvidenceBadge tier="readback" />);
    expect(readback.container.querySelector("span")?.className).toContain("c_mint");
  });
});
