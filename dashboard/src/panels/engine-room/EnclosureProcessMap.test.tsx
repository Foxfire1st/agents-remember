import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { EngineProcessNode } from "../../types/projection";
import { EnclosureProcessMap } from "./EnclosureProcessMap";
import { ENGINE_ROOM_SCENARIOS } from "./fixtures";

function nodeFrom(name: string): EngineProcessNode {
  const scenario = ENGINE_ROOM_SCENARIOS.find((entry) => entry.name === name);
  const node = scenario?.processes[0];
  if (!node) throw new Error(`no fixture node for ${name}`);
  return node;
}

// Determinism: freeze motion so the fleeting/structural assertions are stable (no GSAP/Motion tween).
beforeEach(() => {
  document.documentElement.dataset.effects = "off";
});
afterEach(() => {
  document.documentElement.removeAttribute("data-effects");
  cleanup();
});

describe("EnclosureProcessMap fleeting rendering (5f S2)", () => {
  it("renders a fleeting banner (block reason + recovery) for a pre-contract blocked-start node", () => {
    const node = nodeFrom("engine-precontract-blocked");
    const { getByTestId } = render(<EnclosureProcessMap node={node} />);
    const banner = getByTestId("fleeting-banner");
    expect(banner.textContent).toContain("contract not yet written");
    expect(banner.textContent).toContain(node.summary);
    expect(banner.textContent).toContain(node.nextAction ?? "");
  });

  it("does not render a fleeting banner for a normal (contract-anchored) enclosure", () => {
    const { queryByTestId } = render(<EnclosureProcessMap node={nodeFrom("engine-bootstrap")} />);
    expect(queryByTestId("fleeting-banner")).toBeNull();
    expect(queryByTestId("process-map")).not.toBeNull();
  });

  it("renders the SVG conduits for both lanes", () => {
    const { container } = render(<EnclosureProcessMap node={nodeFrom("engine-bootstrap")} />);
    expect(container.querySelectorAll('[data-testid="code-lane"] svg line').length).toBe(2);
    expect(container.querySelectorAll('[data-testid="memory-lane"] svg line').length).toBe(2);
  });

  it("renders a power-up flow packet only on a running (seeding/cloning) conduit (5f S4, T8/T9)", () => {
    // engine-setup-running carries a running grepai-clone edge → one travelling packet.
    const { container } = render(<EnclosureProcessMap node={nodeFrom("engine-setup-running")} />);
    expect(container.querySelectorAll('[data-testid="conduit-flow"]').length).toBeGreaterThanOrEqual(1);
  });

  it("renders no flow packet when every conduit is settled", () => {
    const { container } = render(<EnclosureProcessMap node={nodeFrom("engine-bootstrap")} />);
    expect(container.querySelectorAll('[data-testid="conduit-flow"]').length).toBe(0);
  });
});
