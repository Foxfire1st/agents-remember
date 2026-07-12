import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import gsap from "gsap";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { EngineProcessNode, ProviderNode } from "../../types/projection";
import { EnclosureProcessMap } from "./EnclosureProcessMap";
import { ENGINE_ROOM_SCENARIOS, OFFICIAL_LEDGER } from "./fixtures";

const KNOWN_EDGE_KINDS = new Set(["worktree-add", "ledger-map", "cgc-seed", "grepai-clone", "sync", "integration"]);
const VALID_RUNTIME = new Set(["nominal", "configured", "indexing", "down", "missing", "unknown"]);
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

describe("EnclosureProcessMap — fleeting block enclosure (5f S2 / 05o)", () => {
  it("renders the big red fleeting-enclosure box (BLOCKED title + reason + recovery) for a pre-contract block", () => {
    const node = nodeFrom("engine-precontract-blocked");
    const { getByTestId } = render(<EnclosureProcessMap node={node} />);
    const box = getByTestId("fleeting-enclosure");
    expect(box.textContent).toContain("BLOCKED");
    expect(box.textContent).toContain("ledger mapping"); // the block reason (from node.summary)
    expect(box.textContent).toContain(node.nextAction ?? ""); // the recovery choice (reconciliation)
  });

  it("renders no fleeting-enclosure box for a contract-anchored enclosure, and shows the pod-stage canvas", () => {
    const { queryByTestId } = render(<EnclosureProcessMap node={nodeFrom("engine-bootstrap")} />);
    expect(queryByTestId("fleeting-enclosure")).toBeNull();
    expect(queryByTestId("process-map")).not.toBeNull();
    expect(queryByTestId("enclosure-canvas")).not.toBeNull();
  });
});

describe("EnclosureCanvas — GSAP gate (05f §8.4 — no ticker under effects=off)", () => {
  it("builds no GSAP context under data-effects=off (deterministic — no live ticker)", () => {
    const spy = vi.spyOn(gsap, "context");
    // the canvas wires useEngineTimeline unconditionally; under effects=off the gate must skip it so the
    // Playwright/vitest snapshots stay deterministic (the rendered end-state stands, no GSAP tween/ticker).
    render(<EnclosureProcessMap node={nodeFrom("engine-bootstrap")} workspaceEngines={WORKSPACE_ENGINES} />);
    expect(spy).not.toHaveBeenCalled();
    spy.mockRestore();
  });

  it("wires the GSAP context when effects are on (proves the gate, not a vacuous assert)", () => {
    document.documentElement.removeAttribute("data-effects");
    const spy = vi.spyOn(gsap, "context");
    render(<EnclosureProcessMap node={nodeFrom("engine-bootstrap")} workspaceEngines={WORKSPACE_ENGINES} />);
    expect(spy).toHaveBeenCalled();
    spy.mockRestore();
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

  it("shows no closeout train and no landing dock for a plain (non-landing) enclosure", () => {
    const { queryByTestId } = render(<EnclosureProcessMap node={nodeFrom("engine-bootstrap")} />);
    expect(queryByTestId("closeout-train")).toBeNull();
    expect(queryByTestId("remote-strip")).toBeNull();
  });

  it("renders without crashing when the projection node omits the landing arc (pre-5h/persisted data)", () => {
    // A projection produced before the slice-5h `landing` field omits it entirely (not []); the
    // canvas must degrade to no landing dock, never throw on `node.landing.find`.
    const node = { ...nodeFrom("engine-landing-ffonly") };
    delete (node as { landing?: unknown }).landing;
    const { getByTestId, queryByTestId } = render(<EnclosureProcessMap node={node} />);
    expect(getByTestId("enclosure-canvas")).not.toBeNull();
    expect(queryByTestId("remote-strip")).toBeNull();
  });
});

describe("EnclosureCanvas — remote/PR strip (5h H3)", () => {
  const memChip = (container: HTMLElement) =>
    [...container.querySelectorAll('[data-testid="remote-chip"]')].find(
      (chip) => chip.getAttribute("data-kind") === "origin-mem-main",
    );

  it("renders the remote strip with origin/main and a PR badge once a landing arc exists", () => {
    const { getByTestId } = render(<EnclosureProcessMap node={nodeFrom("engine-landing-ffonly")} />);
    expect(getByTestId("remote-strip").textContent).toContain("origin/main");
    expect(getByTestId("pr-badge").textContent).toContain("PR #128");
  });

  it("keeps origin/mem-main planned while the PR is open, done once merged — the D3→D4 order", () => {
    const ff = render(<EnclosureProcessMap node={nodeFrom("engine-landing-ffonly")} />);
    expect(memChip(ff.container)?.getAttribute("data-tone")).toBe("planned");
    cleanup();
    const merged = render(<EnclosureProcessMap node={nodeFrom("engine-landing-merged")} />);
    expect(memChip(merged.container)?.getAttribute("data-tone")).toBe("done");
  });

  it("flips the PR badge from open to merged on the projection state", () => {
    const ff = render(<EnclosureProcessMap node={nodeFrom("engine-landing-ffonly")} />);
    expect(ff.getByTestId("pr-badge").getAttribute("data-state")).toBe("open");
    cleanup();
    const merged = render(<EnclosureProcessMap node={nodeFrom("engine-landing-merged")} />);
    expect(merged.getByTestId("pr-badge").getAttribute("data-state")).toBe("merged");
  });

  it("renders stale landing facts as stale and stops live landing-flow packets", () => {
    const base = nodeFrom("engine-landing-ffonly");
    const node: EngineProcessNode = {
      ...base,
      landing: (base.landing ?? []).map((ref) => ({
        ...ref,
        factState: "stale",
        observedAt: "2026-07-12T14:00:00+00:00",
        lastAttemptAt: "2026-07-12T14:01:00+00:00",
        staleSeconds: 60,
      })),
    };
    const { container, getByTestId, queryByTestId } = render(<EnclosureProcessMap node={node} />);
    const feat = container.querySelector('[data-testid="remote-chip"][data-kind="origin-feat"]');
    expect(feat?.getAttribute("data-tone")).toBe("stale");
    expect(feat?.textContent).toContain("stale");
    expect(feat?.querySelector("title")?.textContent).toContain("60s old");
    expect(getByTestId("pr-badge").getAttribute("data-state")).toBe("stale");
    expect(getByTestId("pr-badge").textContent).toContain("stale");
    expect(queryByTestId("landing-packet")).toBeNull();
  });

  it("omits the remote strip for a plain enclosure and never throws when landing is absent", () => {
    const { queryByTestId } = render(<EnclosureProcessMap node={nodeFrom("engine-bootstrap")} />);
    expect(queryByTestId("remote-strip")).toBeNull();
    cleanup();
    const node = { ...nodeFrom("engine-landing-ffonly") };
    delete (node as { landing?: unknown }).landing;
    const second = render(<EnclosureProcessMap node={node} />);
    expect(second.getByTestId("enclosure-canvas")).not.toBeNull();
    expect(second.queryByTestId("remote-strip")).toBeNull();
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

  it("draws the provider clone arrows from the official engine across to the worktree engine (cloned-from, not re-indexed)", () => {
    const { container } = render(<EnclosureProcessMap node={nodeFrom("engine-bootstrap")} />);
    // 5f §7.2: the seed/clone copies the index ACROSS from the official-line engine — CGC bows over the
    // top (official 135,150 → worktree 1057,150), GrepAI under the bottom — NOT worktree-node → engine.
    expect(container.querySelector('[data-kind="cgc-seed"] path')?.getAttribute("d")).toBe("M135 150 C 345 34, 847 34, 1057 150");
    expect(container.querySelector('[data-kind="grepai-clone"] path')?.getAttribute("d")).toBe("M135 500 C 345 604, 847 604, 1057 500");
  });

  it("keeps the sync lane on the worktree-add centreline (one centred line, not an offset double)", () => {
    const { container } = render(<EnclosureProcessMap node={nodeFrom("engine-sync-needed")} />);
    const sync = container.querySelector('[data-kind="sync"] path')?.getAttribute("d");
    const add = container.querySelector('[data-kind="worktree-add"] path')?.getAttribute("d");
    expect(sync).toBe("M455 281 L 735 281"); // main right edge → worktree left edge (COL_MAIN_CX+90 → COL_WT_CX-100)
    expect(add).toBe("M455 281 L 735 281"); // collinear: same y on both ends, so they read as one centred line
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

describe("EnclosureCanvas — T3B failure primitives (05o: scan ring + ghosted lane)", () => {
  it("ghosts the gated memory (ledger-map) lane while the code lane stays solid (steady gate, not a fault)", () => {
    const { container, queryByTestId } = render(<EnclosureProcessMap node={nodeFrom("engine-boot-memory-blocked")} />);
    // the held memory lane ghosts; the code intake lane does not.
    const ledger = container.querySelector('[data-testid="conduit"][data-kind="ledger-map"]');
    const codeAdd = container.querySelector('[data-testid="conduit"][data-kind="worktree-add"]');
    expect(ledger?.getAttribute("data-ghosted")).toBe("true");
    expect(codeAdd?.getAttribute("data-ghosted")).toBeNull();
    // it is a recoverable BLOCK: a steady gate + attention + reconciliation chip, never the terminal STOP.
    expect(queryByTestId("gate")).not.toBeNull();
    expect(queryByTestId("terminal-stop")).toBeNull();
    expect(queryByTestId("attention")).not.toBeNull();
    expect(screen.getByTestId("recovery-chips").textContent).toContain("reconciliation");
  });

  it("sweeps the cyan scan ring while the memory lane is being verified — only when effects are on", () => {
    // under effects=off the transient ring is absent (no frozen ring noise), like the flow packet.
    const off = render(<EnclosureProcessMap node={nodeFrom("engine-boot-memory-verify")} />);
    expect(off.queryByTestId("scan-ring")).toBeNull();
    cleanup();
    // effects on → the ring renders on the lane under check (ledger-map running, memory not yet on disk).
    document.documentElement.removeAttribute("data-effects");
    const on = render(<EnclosureProcessMap node={nodeFrom("engine-boot-memory-verify")} />);
    const ring = on.queryByTestId("scan-ring");
    expect(ring).not.toBeNull();
    expect(ring?.getAttribute("data-fx")).toBe("scan");
  });
});

describe("EnclosureCanvas — T1B failure primitives (05o: pruned base + code-lane scan)", () => {
  it("prunes the stale base node and raises a fleeting block with BOTH recovery choices", () => {
    const { container, getByTestId } = render(<EnclosureProcessMap node={nodeFrom("engine-boot-stale-blocked")} />);
    // the main code (base) node reads pruned/dormant (local main behind upstream)
    expect(container.querySelectorAll('[data-pruned="true"]').length).toBeGreaterThan(0);
    // a stale-base block is a FLEETING born-blocked enclosure: the big red box surfaces BOTH choices
    const box = getByTestId("fleeting-enclosure");
    expect(box.textContent).toContain("fast-forward");
    expect(box.textContent).toContain("proceed-stale");
  });

  it("sweeps the scan ring on the CODE/base lane (cy=281) during the preflight — only when effects are on", () => {
    const off = render(<EnclosureProcessMap node={nodeFrom("engine-boot-stale-verify")} />);
    expect(off.queryByTestId("scan-ring")).toBeNull();
    cleanup();
    document.documentElement.removeAttribute("data-effects");
    const on = render(<EnclosureProcessMap node={nodeFrom("engine-boot-stale-verify")} />);
    const ring = on.queryByTestId("scan-ring");
    expect(ring).not.toBeNull();
    expect(ring?.getAttribute("cy")).toBe("281"); // the code/base lane (the T3B memory verify is y=403)
  });
});

describe("EnclosureCanvas — cleanup teardown (5h H4)", () => {
  it("de-materialises a landed enclosure: success record + historical chip, no full-dim (not abandon)", () => {
    const { getByTestId, queryByTestId } = render(<EnclosureProcessMap node={nodeFrom("engine-cleanup-pending")} />);
    expect(getByTestId("process-map").getAttribute("data-teardown")).toBe("cleanup");
    // a landed cleanup de-materialises only the WORKTREE side (main stays bright) — it does NOT wrap in
    // the full-dim dissolve shell (that is reserved for abandon, which dissolves the whole enclosure).
    expect(queryByTestId("dissolve")).toBeNull();
    expect(getByTestId("cleanup-record").textContent).toContain("Landed");
    expect(getByTestId("lane-historical").textContent).toContain("historical");
    // success ≠ abandon: no abandon record, data-abandoned unset.
    expect(queryByTestId("abandon-record")).toBeNull();
    expect(getByTestId("process-map").getAttribute("data-abandoned")).toBeNull();
  });

  it("names the origin-main tip it rejoined (the back-into-main seam)", () => {
    const { getByTestId } = render(<EnclosureProcessMap node={nodeFrom("engine-cleanup-pending")} />);
    expect(getByTestId("lane-back-into-main").textContent).toContain("origin/main");
  });

  it("keeps abandon distinct — data-teardown=abandon, abandon record, no cleanup record", () => {
    const { getByTestId, queryByTestId } = render(<EnclosureProcessMap node={nodeFrom("engine-abandoned")} />);
    expect(getByTestId("process-map").getAttribute("data-teardown")).toBe("abandon");
    expect(queryByTestId("cleanup-record")).toBeNull();
    expect(getByTestId("abandon-record").textContent).toContain("Abandoned");
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

describe("EnclosureCanvas — ledger popover columns (5h Tier 2)", () => {
  it("renders the 6-column row with commit message + compact date on both sides", async () => {
    render(<EnclosureProcessMap node={nodeFrom("engine-bootstrap")} />);
    fireEvent.click(screen.getByTestId("warp-coupler-ledger"));
    const popover = await screen.findByTestId("ledger-popover");
    const current = popover.querySelector('[data-current="true"]');
    expect(current).not.toBeNull();
    // the newest row carries the real 5h message + the recorded wall-clock (06-18 18:19), not a hash alone
    expect(current?.textContent).toContain("dashboard(5h): ledger popover on both warp couplers");
    expect(current?.textContent).toContain("06-18 18:19");
    // 7 cells: code date | code msg | code hash | ⇄ seam | memory hash | memory msg | memory date
    expect(current?.querySelectorAll("td").length).toBe(7);
  });

  it("falls back to the hash with empty message/date when a row has no probed metadata (honest)", async () => {
    const node = {
      ...nodeFrom("engine-bootstrap"),
      ledgerRows: [{ codeCommit: "abc12345", memoryCommit: "def67890" }],
      ledgerRowCount: 1,
    };
    render(<EnclosureProcessMap node={node} />);
    fireEvent.click(screen.getByTestId("warp-coupler-ledger"));
    const popover = await screen.findByTestId("ledger-popover");
    const row = popover.querySelector("tbody tr");
    const cells = row?.querySelectorAll("td");
    expect(cells?.length).toBe(7);
    expect(row?.textContent).toContain("abc12345"); // the code hash still shows
    expect(row?.textContent).toContain("def67890"); // the memory hash still shows
    // message + date cells stay empty (never faked): date(0) · msg(1) · msg(5) · date(6)
    expect(cells?.[0].textContent).toBe("");
    expect(cells?.[1].textContent).toBe("");
    expect(cells?.[5].textContent).toBe("");
    expect(cells?.[6].textContent).toBe("");
  });
});

describe("EnclosureCanvas — T9B/T9C refused-conduit flash (shared red/amber primitive)", () => {
  it("renders the RED refused-conduit flash on the failed grepai-clone seed lane (and the engine fault co-fires)", () => {
    document.documentElement.removeAttribute("data-effects"); // effects ON — the one-shot flash renders
    const { getAllByTestId, getByTestId } = render(
      <EnclosureProcessMap node={nodeFrom("engine-boot-seed-fault")} workspaceEngines={WORKSPACE_ENGINES} />,
    );
    const refused = getAllByTestId("refused-conduit");
    expect(refused.length).toBe(1); // only the failed grepai-clone lane flashes
    expect(refused[0].getAttribute("data-polarity")).toBe("red"); // failed edge.state → red fault polarity
    expect(refused[0].getAttribute("data-fx")).toBe("refuse");
    expect(refused[0].getAttribute("data-kind")).toBe("grepai-clone");
    // the GrepAI (memory) engine is down → it raises the red fault flicker (data-fx='fault'), CGC unaffected.
    const faulting = getByTestId("enclosure-canvas").querySelectorAll("[data-fx='fault']");
    expect(faulting.length).toBe(1);
  });

  it("renders NO refused-conduit flash on a clean seeding frame (the flash is fault/reroute-only)", () => {
    document.documentElement.removeAttribute("data-effects");
    const { queryByTestId } = render(
      <EnclosureProcessMap node={nodeFrom("engine-boot-4-seeding")} workspaceEngines={WORKSPACE_ENGINES} />,
    );
    expect(queryByTestId("refused-conduit")).toBeNull();
  });

  it("flashes the cgc-seed conduit AMBER (refused) and reindexes CGC in place — a soft reroute, no gate/STOP", () => {
    document.documentElement.removeAttribute("data-effects"); // effects ON so the transient flash renders
    const { container, queryByTestId } = render(<EnclosureProcessMap node={nodeFrom("engine-cgc-seed-refused")} />);
    const seed = container.querySelector('[data-testid="conduit"][data-kind="cgc-seed"]');
    expect(seed?.getAttribute("data-state")).toBe("refused");
    expect(seed?.getAttribute("data-refused-polarity")).toBe("amber");
    const flash = container.querySelector('[data-fx="refuse"]');
    expect(flash).not.toBeNull();
    expect(flash?.getAttribute("data-polarity")).toBe("amber");
    // a SOFT reroute — no steady gate, no terminal STOP, no attention
    expect(queryByTestId("gate")).toBeNull();
    expect(queryByTestId("terminal-stop")).toBeNull();
    expect(queryByTestId("attention")).toBeNull();
    // the CGC engine reindexes in place (amber center-out pulse), driven by node.seedFallback
    expect(container.querySelector('[data-fx="reindex"]')).not.toBeNull();
  });
});

describe("EnclosureCanvas — T7B provider-plan block (05o: scan-at-engine + gate beside the provider engine, engines unlit)", () => {
  it("renders missing provider slots as visible missing gauges instead of dropping the engine", () => {
    const node = {
      ...nodeFrom("engine-bootstrap"),
      providers: [
        {
          id: "missing-code@grp",
          role: "code",
          runtimeState: "missing",
          factState: "missing",
        },
        {
          id: "missing-memory@grp",
          role: "memory",
          runtimeState: "missing",
          factState: "missing",
        },
      ],
    } satisfies EngineProcessNode;
    const { container } = render(<EnclosureProcessMap node={node} />);
    const gauges = [...container.querySelectorAll('[data-testid="engine-gauge"]')];
    expect(gauges.filter((gauge) => gauge.getAttribute("data-runtime") === "missing")).toHaveLength(2);
  });

  it("renders the provider gate BESIDE the engine (NOT on the code node, NOT the big red fleeting box) and keeps the engines unlit", () => {
    const { container, queryByTestId } = render(<EnclosureProcessMap node={nodeFrom("engine-boot-provider-blocked")} />);
    // a T7B block does NOT fall into the fleeting box (that is T1B/stale-base) — it bars the provider runtime
    expect(queryByTestId("fleeting-enclosure")).toBeNull();
    expect(queryByTestId("provider-block")).not.toBeNull();
    // the engines stayed unlit — the dropout halo marks the held engine slots
    expect(queryByTestId("engine-dropout")).not.toBeNull();
    // a steady gate + the alarm-parity attention badge
    const gate = queryByTestId("gate");
    expect(gate).not.toBeNull();
    // 05o dev fix: the alarm bar is a VERTICAL bar attached to the LEFT side of the worktree provider engine
    // (x≈1045, the engine is at 1057), NOT a horizontal bar across it and NOT on the code worktree node (which
    // would put the bar at x≈771). Guards the geometry regression — x beside the engine + taller than wide.
    const gx = Number(gate?.getAttribute("x"));
    expect(gx).toBeGreaterThan(960);
    expect(Number(gate?.getAttribute("height"))).toBeGreaterThan(Number(gate?.getAttribute("width")));
    expect(queryByTestId("attention")).not.toBeNull();
    // the recovery choices lead with retry-setup / disabled-memory (RecoveryChips caps at the first 3, so the
    // 4th 'abandon' action is not rendered on canvas — retry + disabled are the visible provider-plan choices)
    const chips = container.querySelector('[data-testid="recovery-chips"]')?.textContent ?? "";
    expect(chips).toMatch(/retry/i);
    expect(chips).toMatch(/disabled/i);
  });

  it("sweeps the cyan scan ring AT the worktree engine (cx=1084) during the provider-plan verify — effects on", () => {
    const off = render(<EnclosureProcessMap node={nodeFrom("engine-boot-provider-verify")} />);
    expect(off.queryByTestId("scan-ring")).toBeNull();
    cleanup();
    document.documentElement.removeAttribute("data-effects");
    const on = render(<EnclosureProcessMap node={nodeFrom("engine-boot-provider-verify")} />);
    const ring = on.queryByTestId("scan-ring");
    expect(ring).not.toBeNull();
    expect(ring?.getAttribute("data-fx")).toBe("scan");
    expect(ring?.getAttribute("cx")).toBe("1084"); // ENGINE.cgc.x(1057) + ENGINE.w/2(27) — the worktree CGC engine
    expect(ring?.getAttribute("cy")).toBe("150");
  });
});

describe("EnclosureCanvas — live memory-sync block (T12B)", () => {
  it("shows the soft “moved” badge when origin/mem-main advanced (notification, before the gate)", () => {
    const { getByTestId } = render(<EnclosureProcessMap node={nodeFrom("engine-sync-moved")} />);
    expect(getByTestId("moved-badge").textContent).toContain("moved");
  });

  it("gates + ghosts the memory lane only while the code lane stays solid, and offers merge/skip recovery", () => {
    const { getByTestId, container } = render(<EnclosureProcessMap node={nodeFrom("engine-sync-memory-blocked")} />);
    expect(getByTestId("node-block")).not.toBeNull();
    const ledger = [...container.querySelectorAll('[data-testid="conduit"]')].find(
      (c) => c.getAttribute("data-kind") === "ledger-map",
    );
    const codeLane = [...container.querySelectorAll('[data-testid="conduit"]')].find(
      (c) => c.getAttribute("data-kind") === "worktree-add",
    );
    expect(ledger?.getAttribute("data-ghosted")).toBe("true");
    expect(codeLane?.getAttribute("data-ghosted")).toBeNull();
    expect(getByTestId("recovery-chips").textContent).toMatch(/merge/i);
  });

  it("clears the gate + moved badge on recover (the memory worktree fast-forwarded)", () => {
    const { queryByTestId } = render(<EnclosureProcessMap node={nodeFrom("engine-sync-recovered")} />);
    expect(queryByTestId("node-block")).toBeNull();
    expect(queryByTestId("moved-badge")).toBeNull();
  });
});

describe("EnclosureCanvas — integration conflict (T14C · terminal)", () => {
  it("renders the steady terminal STOP (not the recoverable Gate) and NO recovery chips for the conflict", () => {
    const { getByTestId, queryByTestId } = render(<EnclosureProcessMap node={nodeFrom("engine-integration-conflict")} />);
    const stop = getByTestId("terminal-stop");
    expect(stop.getAttribute("data-kind")).toBe("integration");
    // the conflict words ride a banner (the ⛔ glyph carries the STOP semantics); the words are off the lane
    // line so the red conduit no longer bisects them (05o legibility fix).
    expect(stop.textContent).toMatch(/conflict/i);
    expect(stop.textContent).toContain("⛔");
    expect(queryByTestId("gate")).toBeNull(); // terminal STOP, never the recoverable Gate (no gate testid)
    expect(queryByTestId("recovery-chips")).toBeNull(); // human-only — no recovery chips
    expect(getByTestId("attention")).not.toBeNull();
  });

  it("renders the RED refused-conduit flash on the integration return-lane(s) for the transient CONFLICT beat", () => {
    const { getAllByTestId, queryByTestId } = render(<EnclosureProcessMap node={nodeFrom("engine-integration-conflict-flash")} />);
    const flashes = getAllByTestId("refused-conduit");
    expect(flashes.length).toBeGreaterThanOrEqual(1);
    for (const f of flashes) {
      expect(f.getAttribute("data-fx")).toBe("refuse");
      expect(f.getAttribute("data-polarity")).toBe("red");
      expect(["integration", "integration-mem"]).toContain(f.getAttribute("data-kind"));
    }
    expect(queryByTestId("terminal-stop")).toBeNull(); // the STOP only appears once the edge flips to blocked (C4)
  });
});

describe("EnclosureProcessMap — continuous-identity abandon (T18)", () => {
  it("the boot-demo abandon dissolves to a dim record, same as engine-abandoned", () => {
    const { getByTestId, queryByTestId } = render(<EnclosureProcessMap node={nodeFrom("engine-boot-abandoned")} />);
    expect(getByTestId("dissolve")).not.toBeNull();
    expect(getByTestId("process-map").getAttribute("data-abandoned")).toBe("true");
    expect(getByTestId("process-map").getAttribute("data-teardown")).toBe("abandon");
    expect(getByTestId("abandon-record").textContent).toContain("Abandoned");
    // abandon is a decision, not a block/land: no recovery chips, no attention, no landing dock.
    expect(queryByTestId("recovery-chips")).toBeNull();
    expect(queryByTestId("attention")).toBeNull();
    expect(queryByTestId("remote-strip")).toBeNull();
    expect(queryByTestId("cleanup-record")).toBeNull();
    expect(getByTestId("lane-historical").textContent).toContain("historical");
  });
});
