import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { EngineProcessNode, ProviderNode } from "../../types/projection";
import { EnclosureProcessMap } from "./EnclosureProcessMap";
import { ENGINE_ROOM_SCENARIOS, OFFICIAL_LEDGER } from "./fixtures";

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

describe("EnclosureCanvas — ledger coupler (5h coupler fix)", () => {
  it("labels the coupler with its code⇄memory commit pair, not the contract", () => {
    const { container } = render(<EnclosureProcessMap node={nodeFrom("engine-bootstrap")} />);
    const text = container.querySelector('[data-testid="warp-coupler"]')?.textContent ?? "";
    expect(text).toContain("⇄");
    expect(text).not.toContain("contract");
  });

  it("renders the chain-link glyph + the warp-core surge bands when bound", () => {
    const { container } = render(<EnclosureProcessMap node={nodeFrom("engine-bootstrap")} />);
    expect(container.querySelectorAll('[data-testid="warp-link"]').length).toBeGreaterThan(0);
    // two surge bands (up + down) per bound coupler
    expect(container.querySelectorAll('[data-testid="warp-surge"]').length).toBeGreaterThanOrEqual(2);
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

describe("EnclosureCanvas — conduit wiring polish (5h cleanup)", () => {
  it("tips a conduit only on a running flow (action) — a complete/nominal connection carries no arrowhead", () => {
    const { container } = render(<EnclosureProcessMap node={nodeFrom("engine-setup-running")} />);
    const running = container.querySelector('[data-kind="grepai-clone"][data-state="running"] path');
    const complete = container.querySelector('[data-kind="cgc-seed"] path');
    expect(running?.getAttribute("marker-end")).toBe("url(#er-chev)");
    expect(complete?.getAttribute("marker-end")).toBeNull();
  });

  it("seats the chevron reference at its visual tip so the arrowhead lands on the line end, not past it", () => {
    const { container } = render(<EnclosureProcessMap node={nodeFrom("engine-bootstrap")} />);
    // refX < the chevron apex (8.5) overshoots the endpoint into the target engine — see the marker comment.
    expect(container.querySelector("marker#er-chev")?.getAttribute("refX")).toBe("9.6");
  });

  it("wires each provider conduit from the box-edge midpoint into the engine's inner corner", () => {
    const { container } = render(<EnclosureProcessMap node={nodeFrom("engine-bootstrap")} />);
    // code box right-edge midpoint (900,281) → CGC inner corner (1057,198); memory midpoint (900,403) → GrepAI (1057,452)
    expect(container.querySelector('[data-kind="cgc-seed"] path')?.getAttribute("d")).toBe("M900 281 L 1057 198");
    expect(container.querySelector('[data-kind="grepai-clone"] path')?.getAttribute("d")).toBe("M900 403 L 1057 452");
  });

  it("keeps the sync lane on the worktree-add centreline (one centred line, not an offset double)", () => {
    const { container } = render(<EnclosureProcessMap node={nodeFrom("engine-sync-needed")} />);
    const sync = container.querySelector('[data-kind="sync"] path')?.getAttribute("d");
    const add = container.querySelector('[data-kind="worktree-add"] path')?.getAttribute("d");
    expect(sync).toBe("M480 281 L 698 281");
    expect(add).toBe("M480 281 L 698 281"); // collinear: same y on both ends, so they read as one centred line
  });

  it("fans six engine petals with mirrored flanks (symmetric across the gauge centre)", () => {
    const { container } = render(<EnclosureProcessMap node={nodeFrom("engine-bootstrap")} />);
    const gauge = container.querySelector('[data-testid="engine-gauge"]');
    const lines = [...(gauge?.querySelectorAll("line") ?? [])];
    const mid = (l: Element) => (Number(l.getAttribute("y1")) + Number(l.getAttribute("y2"))) / 2;
    // petals are the only gauge lines whose x sits outside the gauge body [0, ENGINE.w=54]
    const left = lines.filter((l) => Number(l.getAttribute("x1")) < 0).map(mid).sort((a, b) => a - b);
    const right = lines.filter((l) => Number(l.getAttribute("x1")) > 54).map(mid).sort((a, b) => a - b);
    expect(left).toEqual([24, 48, 72]);
    expect(right).toEqual([24, 48, 72]); // each flank's petal midpoints mirror the other
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

describe("EnclosureCanvas — ledger popover (5h, worktree coupler)", () => {
  it("opens the lookup table on the worktree coupler, highlights this row, and starts collapsed at 8", async () => {
    render(<EnclosureProcessMap node={nodeFrom("engine-bootstrap")} />);
    expect(screen.queryByTestId("ledger-popover")).toBeNull(); // closed until the button is clicked
    fireEvent.click(screen.getByTestId("warp-coupler-ledger"));
    const popover = await screen.findByTestId("ledger-popover");
    // default view is the newest 8 (the served window is 25 of 40 total)
    expect(popover.querySelectorAll("tbody tr").length).toBe(8);
    expect(popover.querySelector('[data-current="true"]')?.textContent).toContain("08e9221a");
    // collapsed → a "show 17 more" control (25 served − 8 shown), and NOT yet the file footer
    expect(screen.getByTestId("ledger-show-more").textContent).toContain("17 more");
    expect(popover.textContent).not.toContain("more in memory.md");
  });

  it("extends in place to the full served window (≤25) and then points at the file for the rest", async () => {
    render(<EnclosureProcessMap node={nodeFrom("engine-bootstrap")} />);
    fireEvent.click(screen.getByTestId("warp-coupler-ledger"));
    await screen.findByTestId("ledger-popover");
    fireEvent.click(screen.getByTestId("ledger-show-more")); // expand
    const popover = screen.getByTestId("ledger-popover");
    expect(popover.querySelectorAll("tbody tr").length).toBe(25); // the served cap
    expect(screen.queryByTestId("ledger-show-more")).toBeNull(); // fully expanded → no expand control
    expect(popover.textContent).toContain("+15 more in memory.md"); // 40 total − 25 served
  });

  it("gives a coupler no ledger trigger when it carries no rows (no officialLedger supplied)", () => {
    render(<EnclosureProcessMap node={nodeFrom("engine-bootstrap")} workspaceEngines={WORKSPACE_ENGINES} />);
    expect(screen.queryByTestId("warp-coupler-ledger")).not.toBeNull(); // worktree coupler has rows
    expect(screen.queryByTestId("warp-coupler-official-ledger")).toBeNull(); // official one was given none
  });

  it("opens the popover on the official coupler too, highlighting the official source row", async () => {
    render(
      <EnclosureProcessMap
        node={nodeFrom("engine-bootstrap")}
        workspaceEngines={WORKSPACE_ENGINES}
        officialLedger={OFFICIAL_LEDGER}
      />,
    );
    fireEvent.click(screen.getByTestId("warp-coupler-official-ledger"));
    const popover = await screen.findByTestId("ledger-popover");
    // the official coupler highlights the official source commit (08e9221a)
    expect(popover.querySelector('[data-current="true"]')?.textContent).toContain("08e9221a");
    expect(screen.getByTestId("ledger-show-more").textContent).toContain("17 more");
  });
});
