import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { EngineProcessNode, ProviderNode } from "../../types/projection";
import { EnclosureProcessMap } from "./EnclosureProcessMap";
import { ENGINE_ROOM_SCENARIOS } from "./fixtures";

const KNOWN_EDGE_KINDS = new Set(["worktree-add", "ledger-map", "cgc-seed", "grepai-clone", "sync", "integration"]);
const VALID_RUNTIME = new Set(["nominal", "configured", "indexing", "down", "unknown"]);
// The shared official line's two workspace engines (CGC code + GrepAI memory), as the model lifts them.
const WORKSPACE_ENGINES: ProviderNode[] = [
  { id: "cgc", state: "ready", watcherUp: true, indexingState: "indexed", scope: "workspace", role: "code" },
  { id: "grepai", state: "ready", watcherUp: true, indexingState: "indexed", scope: "workspace", role: "memory" },
];

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

describe("EnclosureCanvas — landing arc (5h H2)", () => {
  it("plays the closeout train (5 beats) on closeout-pending", () => {
    const { getByTestId } = render(<EnclosureProcessMap node={nodeFrom("engine-landing-closeout")} />);
    expect(getByTestId("closeout-train").querySelectorAll("rect").length).toBe(5);
  });

  it("draws the integration conduit straight for ff-only and bent for replay", () => {
    const ff = render(<EnclosureProcessMap node={nodeFrom("engine-landing-ffonly")} />);
    expect(ff.container.querySelector('[data-kind="integration"]')?.getAttribute("data-strategy")).toBeNull();
    cleanup();
    const replay = render(<EnclosureProcessMap node={nodeFrom("engine-landing-merged")} />);
    expect(replay.container.querySelector('[data-kind="integration"]')?.getAttribute("data-strategy")).toBe("replay");
  });

  it("advances the official source line to its landing tip when a strategy is recorded", () => {
    const { getByTestId } = render(<EnclosureProcessMap node={nodeFrom("engine-landing-ffonly")} />);
    expect(getByTestId("lane-landing-source").textContent).toContain("origin/");
  });

  it("shows neither the closeout train nor the landing-source flag for a plain enclosure", () => {
    const { queryByTestId } = render(<EnclosureProcessMap node={nodeFrom("engine-bootstrap")} />);
    expect(queryByTestId("closeout-train")).toBeNull();
    expect(queryByTestId("lane-landing-source")).toBeNull();
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

  it("renders the official-line (workspace) engines on the left when provided, plus their wiring (5g decals)", () => {
    const node = nodeFrom("engine-bootstrap");
    const external = hasExternalMemory(node);
    const right = external ? 2 : 1;
    const { container } = render(<EnclosureProcessMap node={node} workspaceEngines={WORKSPACE_ENGINES} />);
    // the two official-line engines (left world) sit on top of the worktree engines (right world)…
    expect(container.querySelectorAll('[data-testid="engine-gauge"]').length).toBe(right + 2);
    // …fed by their provider→branch wires, with the official code↔memory coupler when memory is external.
    expect(container.querySelectorAll('[data-testid="official-wire"]').length).toBeGreaterThan(0);
    expect(container.querySelector('[data-testid="warp-coupler-official"]') !== null).toBe(external);
  });

  it("omits the official-line engines + wiring when no workspace engines are supplied (default empty)", () => {
    const node = nodeFrom("engine-bootstrap");
    const external = hasExternalMemory(node);
    const { container } = render(<EnclosureProcessMap node={node} />);
    // no left-world engines or wires without workspace engines (right-world gauges unchanged)
    expect(container.querySelectorAll('[data-testid="engine-gauge"]').length).toBe(external ? 2 : 1);
    expect(container.querySelectorAll('[data-testid="official-wire"]').length).toBe(0);
  });

  it("draws the canopy HUD frame (bevel rim + corner brackets + edge ticks)", () => {
    const { container } = render(<EnclosureProcessMap node={nodeFrom("engine-bootstrap")} />);
    expect(container.querySelector('[data-testid="canopy-frame"]')).not.toBeNull();
  });

  it("annotates the worktree landing lane (ledger ▸ maps merge) only for an external-memory enclosure", () => {
    const node = nodeFrom("engine-bootstrap");
    const { container } = render(<EnclosureProcessMap node={node} />);
    expect(container.querySelector('[data-testid="lane-ledger"]') !== null).toBe(hasExternalMemory(node));
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

describe("EnclosureCanvas — live + teardown (5g G5)", () => {
  it("t14c — a terminal integration conflict draws a STOP and suppresses recovery chips", () => {
    const { queryByTestId } = render(<EnclosureProcessMap node={nodeFrom("engine-integration-conflict")} />);
    expect(queryByTestId("terminal-stop")).not.toBeNull();
    // terminal = human-only resolution: no recovery chips, and no thin Gate on the integration lane.
    expect(queryByTestId("recovery-chips")).toBeNull();
    expect(queryByTestId("gate")).toBeNull();
    // it still raises the alarm-parity attention badge.
    expect(queryByTestId("attention")).not.toBeNull();
  });

  it("t12b — a sync-blocked lane shows a steady gate + worktree_sync recovery (not a terminal STOP)", () => {
    const { queryByTestId, getByTestId } = render(<EnclosureProcessMap node={nodeFrom("engine-sync-needed")} />);
    expect(queryByTestId("gate")).not.toBeNull();
    expect(queryByTestId("terminal-stop")).toBeNull();
    expect(getByTestId("recovery-chips").textContent).toContain("worktree_sync");
  });

  it("t18 — an abandoned enclosure dissolves to a dim record (no recovery chips, no attention)", () => {
    const { getByTestId, queryByTestId } = render(<EnclosureProcessMap node={nodeFrom("engine-abandoned")} />);
    expect(getByTestId("process-map").getAttribute("data-abandoned")).toBe("true");
    expect(getByTestId("abandon-record").textContent).toContain("Abandoned");
    expect(queryByTestId("recovery-chips")).toBeNull();
    expect(queryByTestId("attention")).toBeNull();
  });
});

describe("EnclosureProcessMap — atmospheric backdrop (5g G6)", () => {
  it("mounts no backdrop under data-effects=off (determinism / reduced-motion)", () => {
    const { queryByTestId } = render(<EnclosureProcessMap node={nodeFrom("engine-bootstrap")} />);
    expect(queryByTestId("backdrop")).toBeNull();
  });

  it("mounts the faint boomerang backdrop (aria-hidden, a <video>) when effects are on", () => {
    document.documentElement.removeAttribute("data-effects"); // effects on for this case
    const { getByTestId } = render(<EnclosureProcessMap node={nodeFrom("engine-bootstrap")} />);
    const bd = getByTestId("backdrop");
    expect(bd.getAttribute("aria-hidden")).toBe("true");
    expect(bd.querySelector("video")).not.toBeNull();
  });
});
