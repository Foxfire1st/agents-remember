import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { FeatureCapability } from "../../../data/conversation/types";
import { CapabilityReason } from "./primitives";

// L5P R11 — the capability hint is progressive disclosure: a short honest cue stays in place (the
// one-word state), the full implementation-jargon reason moves behind hover (title). It is never a
// prominent always-visible paragraph above the conversation.
function cap(overrides: Partial<FeatureCapability>): FeatureCapability {
  return {
    state: "partial",
    reason: "live items cross in full; the evidence window is bounded and ephemeral threads have no native page",
    evidenceTier: "native-declaration",
    ...overrides,
  };
}

describe("CapabilityReason — R11 progressive disclosure cue", () => {
  it("renders nothing when the capability is supported", () => {
    const { container } = render(<CapabilityReason capability={cap({ state: "supported" })} />);
    expect(container.querySelector('[data-testid="capability-reason"]')).toBeNull();
  });

  it("shows the short state word, not the full reason paragraph, and puts the reason in the tooltip", () => {
    const { getByTestId } = render(<CapabilityReason capability={cap({})} />);
    const cue = getByTestId("capability-reason");
    // The visible cue is the one-word state — the jargon paragraph is NOT rendered inline.
    expect(cue.textContent).toBe("partial");
    expect(cue.textContent).not.toContain("evidence window is bounded");
    // The exact server reason is disclosed on hover.
    expect(cue.getAttribute("title")).toContain("evidence window is bounded");
    expect(cue.getAttribute("data-state")).toBe("partial");
  });

  it("prefixes the cue and the tooltip with a disambiguating label when given", () => {
    const { getByTestId } = render(
      <CapabilityReason
        capability={cap({ state: "unavailable", reason: "historical tool details are lossy" })}
        label="history"
      />,
    );
    const cue = getByTestId("capability-reason");
    expect(cue.textContent).toBe("history unavailable");
    expect(cue.getAttribute("title")).toBe("history: historical tool details are lossy");
  });
});
