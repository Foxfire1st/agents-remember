import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { EngineProcessNode } from "../../types/projection";
import { EnclosureProcessMap } from "./EnclosureProcessMap";
import { ENGINE_ROOM_SCENARIOS } from "./fixtures";

const KNOWN_EDGE_KINDS = new Set(["worktree-add", "ledger-map", "cgc-seed", "grepai-clone", "sync"]);
const VALID_RUNTIME = new Set(["nominal", "configured", "indexing", "down", "unknown"]);

function nodeFrom(name: string): EngineProcessNode {
  const scenario = ENGINE_ROOM_SCENARIOS.find((entry) => entry.name === name);
  const node = scenario?.processes[0];
  if (!node) throw new Error(`no fixture node for ${name}`);
  return node;
}

function hasExternalMemory(node: EngineProcessNode): boolean {
  return node.memoryMode === "external" && !!node.memoryWorktree;
}

// Determinism: freeze motion so the structural assertions are stable (no GSAP/Motion tween).
beforeEach(() => {
  document.documentElement.dataset.effects = "off";
});
afterEach(() => {
  document.documentElement.removeAttribute("data-effects");
  cleanup();
});

describe("EnclosureProcessMap — fleeting promote-in-place (5f S2)", () => {
  it("renders a fleeting banner (block reason + recovery) for a pre-contract blocked-start node", () => {
    const node = nodeFrom("engine-precontract-blocked");
    const { getByTestId } = render(<EnclosureProcessMap node={node} />);
    const banner = getByTestId("fleeting-banner");
    expect(banner.textContent).toContain("contract not yet written");
    expect(banner.textContent).toContain(node.summary);
    expect(banner.textContent).toContain(node.nextAction ?? "");
  });

  it("renders no fleeting banner for a contract-anchored enclosure, and shows the pod-stage canvas", () => {
    const { queryByTestId } = render(<EnclosureProcessMap node={nodeFrom("engine-bootstrap")} />);
    expect(queryByTestId("fleeting-banner")).toBeNull();
    expect(queryByTestId("process-map")).not.toBeNull();
    expect(queryByTestId("enclosure-canvas")).not.toBeNull();
  });
});

describe("EnclosureCanvas — static bird's-eye (5g G1)", () => {
  it("renders one flow conduit per known model edge", () => {
    const node = nodeFrom("engine-bootstrap");
    const expected = node.edges.filter((edge) => KNOWN_EDGE_KINDS.has(edge.kind)).length;
    const { container } = render(<EnclosureProcessMap node={node} />);
    expect(container.querySelectorAll('[data-testid="conduit"]').length).toBe(expected);
  });

  it("renders the podracer engine gauges + warp coupler, bound iff external memory", () => {
    const node = nodeFrom("engine-bootstrap");
    const external = hasExternalMemory(node);
    const { container } = render(<EnclosureProcessMap node={node} />);
    expect(container.querySelectorAll('[data-testid="engine-gauge"]').length).toBe(external ? 2 : 1);
    expect(container.querySelector('[data-testid="warp-coupler"]')?.getAttribute("data-bound")).toBe(String(external));
  });

  it("renders the official + worktree branch nodes (code & memory when external)", () => {
    const node = nodeFrom("engine-bootstrap");
    const external = hasExternalMemory(node);
    const { container } = render(<EnclosureProcessMap node={node} />);
    expect(container.querySelectorAll('[data-testid="branch-node"]').length).toBe(external ? 4 : 2);
  });

  it("drives every gauge's runtime state from the model (state lives in the projection, not the class)", () => {
    const { container } = render(<EnclosureProcessMap node={nodeFrom("engine-bootstrap")} />);
    const gauges = [...container.querySelectorAll('[data-testid="engine-gauge"]')];
    expect(gauges.length).toBeGreaterThan(0);
    for (const gauge of gauges) {
      expect(VALID_RUNTIME.has(gauge.getAttribute("data-runtime") ?? "")).toBe(true);
    }
  });
});
