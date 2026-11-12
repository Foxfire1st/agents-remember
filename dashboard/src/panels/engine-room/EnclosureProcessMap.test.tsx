import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import gsap from "gsap";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { EngineProcessNode } from "../../types/projection";
import { EnclosureProcessMap } from "./EnclosureProcessMap";
import { ENGINE_ROOM_SCENARIOS, OFFICIAL_LEDGER } from "./fixtures";

const KNOWN_EDGE_KINDS = new Set(["worktree-add", "ledger-map", "sync", "integration"]);

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
    render(<EnclosureProcessMap node={nodeFrom("engine-bootstrap")} />);
    expect(spy).not.toHaveBeenCalled();
    spy.mockRestore();
  });

  it("wires the GSAP context when effects are on (proves the gate, not a vacuous assert)", () => {
    document.documentElement.removeAttribute("data-effects");
    const spy = vi.spyOn(gsap, "context");
    render(<EnclosureProcessMap node={nodeFrom("engine-bootstrap")} />);
    expect(spy).toHaveBeenCalled();
    spy.mockRestore();
  });

  it("isolates repeating transforms from the text-heavy structural SVG", () => {
    document.documentElement.removeAttribute("data-effects");
    const { getByTestId } = render(
      <EnclosureProcessMap
        node={nodeFrom("engine-bootstrap")}
      />,
    );
    const structural = getByTestId("enclosure-canvas");
    const effects = getByTestId("engine-fx-overlay");

    expect(structural.querySelector("[data-fx='surge']")).toBeNull();
    expect(structural.querySelector("[data-fx='breath']")).toBeNull();
    expect(effects.querySelectorAll("[data-fx='surge']").length).toBeGreaterThanOrEqual(2);
    // No reindex pulse remains anywhere: the engine-anchored reroute retired with the provider
    // engines, so neither SVG carries the marker.
    expect(structural.querySelector("[data-fx='reindex']")).toBeNull();
    expect(effects.querySelector("[data-fx='reindex']")).toBeNull();
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
      landing: base.landing.map((ref) => ({
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

  it("omits the remote strip when a plain enclosure carries an empty landing arc", () => {
    const { queryByTestId } = render(<EnclosureProcessMap node={nodeFrom("engine-bootstrap")} />);
    expect(queryByTestId("remote-strip")).toBeNull();
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

  it("renders no engine gauges — the warp coupler is the only stage gauge, bound iff external memory", () => {
    const node = nodeFrom("engine-bootstrap");
    const external = hasExternalMemory(node);
    const { container } = render(<EnclosureProcessMap node={node} />);
    // No engine gauges anywhere on the stage.
    expect(container.querySelectorAll('[data-testid="engine-gauge"]').length).toBe(0);
    expect(container.querySelector('[data-testid="warp-coupler"]')?.getAttribute("data-bound")).toBe(String(external));
  });

  it("renders no official-line engines or wiring — the official line keeps only its coupler", () => {
    const node = nodeFrom("engine-bootstrap");
    const external = hasExternalMemory(node);
    const { container } = render(<EnclosureProcessMap node={node} />);
    // No left-world engines, no engine wires. The official (ledger) coupler is retained
    // task state and renders iff external memory.
    expect(container.querySelectorAll('[data-testid="engine-gauge"]').length).toBe(0);
    expect(container.querySelectorAll('[data-testid="official-wire"]').length).toBe(0);
    expect(container.querySelector('[data-testid="warp-coupler-official"]') !== null).toBe(external);
    expect(container.querySelector('[data-testid="warp-coupler"]') !== null).toBe(external);
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

  it("renders the worktree branch nodes (code & memory when external), never engine gauges", () => {
    const node = nodeFrom("engine-bootstrap");
    const external = hasExternalMemory(node);
    const { container } = render(<EnclosureProcessMap node={node} />);
    expect(container.querySelectorAll('[data-testid="branch-node"]').length).toBe(external ? 4 : 2);
    expect(container.querySelectorAll('[data-testid="engine-gauge"]').length).toBe(0);
  });
});

describe("EnclosureCanvas — conduit wiring polish (5h cleanup)", () => {
  it("tips a conduit only on a running flow (action) — a complete/nominal connection carries no arrowhead", () => {
    const node = {
      ...nodeFrom("engine-bootstrap"),
      edges: [
        { id: "add", fromNode: "code-source", toNode: "code-worktree", kind: "worktree-add", state: "running", label: "add code worktree" },
        { id: "map", fromNode: "memory-source", toNode: "memory-worktree", kind: "ledger-map", state: "complete", label: "ledger-map" },
      ],
    } satisfies EngineProcessNode;
    const { container } = render(<EnclosureProcessMap node={node} />);
    const running = container.querySelector('[data-kind="worktree-add"][data-state="running"] path');
    const complete = container.querySelector('[data-kind="ledger-map"] path');
    expect(running?.getAttribute("marker-end")).toBe("url(#er-chev)");
    expect(complete?.getAttribute("marker-end")).toBeNull();
  });

  it("seats the chevron reference at its visual tip so the arrowhead lands on the line end, not past it", () => {
    const { container } = render(<EnclosureProcessMap node={nodeFrom("engine-bootstrap")} />);
    // refX < the chevron apex (8.5) overshoots the endpoint into the target engine — see the marker comment.
    expect(container.querySelector("marker#er-chev")?.getAttribute("refX")).toBe("9.6");
  });

  it("renders no seed/clone conduits — the stage carries only the enclosure lanes", () => {
    const { container } = render(<EnclosureProcessMap node={nodeFrom("engine-bootstrap")} />);
    // The seed/clone edge kinds are gone from the projection AND carry no stage geometry, so no
    // conduit renders for them.
    expect(container.querySelector('[data-kind="cgc-seed"]')).toBeNull();
    expect(container.querySelector('[data-kind="grepai-clone"]')).toBeNull();
    for (const edge of nodeFrom("engine-bootstrap").edges) {
      expect(["worktree-add", "ledger-map", "sync", "integration", "integration-mem"]).toContain(edge.kind);
    }
  });

  it("keeps the sync lane on the worktree-add centreline (one centred line, not an offset double)", () => {
    const { container } = render(<EnclosureProcessMap node={nodeFrom("engine-sync-needed")} />);
    const sync = container.querySelector('[data-kind="sync"] path')?.getAttribute("d");
    const add = container.querySelector('[data-kind="worktree-add"] path')?.getAttribute("d");
    expect(sync).toBe("M455 281 L 735 281"); // main right edge → worktree left edge (COL_MAIN_CX+90 → COL_WT_CX-100)
    expect(add).toBe("M455 281 L 735 281"); // collinear: same y on both ends, so they read as one centred line
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
    render(<EnclosureProcessMap node={nodeFrom("engine-bootstrap")} />);
    expect(screen.queryByTestId("warp-coupler-ledger")).not.toBeNull(); // worktree coupler has rows
    expect(screen.queryByTestId("warp-coupler-official-ledger")).toBeNull(); // official one was given none
  });

  it("opens the popover on the official coupler too, highlighting the official source row", async () => {
    render(
      <EnclosureProcessMap
        node={nodeFrom("engine-bootstrap")}

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
    // the newest row carries the real commit message + the recorded wall-clock (06-18 18:19), not a hash alone
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

describe("EnclosureCanvas — provider traces are gone (MIK-R112 rule 2)", () => {
  const PROVIDER_TEXT = /cgc|grepai|codegraphcontext|provider/i;
  const REMOVED_NODE_FIELDS = [
    "providers",
    "setupState",
    "currentPhase",
    "completedPhases",
    "failedPhases",
    "heartbeatAgeSeconds",
    "seedFallback",
    "retryArgs",
  ];

  it("carries no provider state on any fixture node", () => {
    for (const scenario of ENGINE_ROOM_SCENARIOS) {
      for (const node of scenario.processes) {
        for (const field of REMOVED_NODE_FIELDS) {
          expect(node, `${scenario.name} still carries ${field}`).not.toHaveProperty(field);
        }
        for (const edge of node.edges) {
          expect(
            ["worktree-add", "ledger-map", "sync", "integration", "integration-mem"],
            `${scenario.name} carries the retired ${edge.kind} lane`,
          ).toContain(edge.kind);
        }
      }
    }
  });

  it("renders no gauge, provider block, dropout halo, fault/reindex marker, seed/clone lane or provider text", () => {
    document.documentElement.removeAttribute("data-effects"); // effects ON — a transient marker would render
    for (const scenario of ENGINE_ROOM_SCENARIOS) {
      const { container, unmount } = render(<EnclosureProcessMap node={scenario.processes[0]} />);
      expect(container.querySelector('[data-testid="engine-gauge"]'), scenario.name).toBeNull();
      expect(container.querySelector('[data-testid="provider-block"]'), scenario.name).toBeNull();
      expect(container.querySelector('[data-testid="engine-dropout"]'), scenario.name).toBeNull();
      expect(container.querySelector("[data-fx='fault']"), scenario.name).toBeNull();
      expect(container.querySelector("[data-fx='reindex']"), scenario.name).toBeNull();
      expect(container.querySelector('[data-kind="cgc-seed"]'), scenario.name).toBeNull();
      expect(container.querySelector('[data-kind="grepai-clone"]'), scenario.name).toBeNull();
      expect(container.textContent ?? "", scenario.name).not.toMatch(PROVIDER_TEXT);
      unmount();
    }
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
    // line so the red conduit no longer bisects them (legibility fix).
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

// ── Engine Room CPU fix: off-screen pause + transform-driven fx ─────────────────────
// The cockpit keeps the Engine Room mounted (display:none) across tab switches, so the animation
// substrate must sleep while the canvas doesn't intersect. jsdom has no IntersectionObserver — a
// controllable mock drives the hide/show flips (same pattern as useElementVisible.test.tsx).
const observed = new Map<Element, IntersectionObserverCallback>();
class MockIntersectionObserver {
  private readonly callback: IntersectionObserverCallback;
  readonly root = null;
  readonly rootMargin = "0px";
  readonly thresholds = [0];
  constructor(callback: IntersectionObserverCallback) {
    this.callback = callback;
  }
  observe = (element: Element) => observed.set(element, this.callback);
  unobserve = (element: Element) => void observed.delete(element);
  disconnect = () => observed.clear();
  takeRecords = (): IntersectionObserverEntry[] => [];
}

function installMockIO() {
  observed.clear();
  (globalThis as { IntersectionObserver?: unknown }).IntersectionObserver = MockIntersectionObserver;
}

function uninstallMockIO() {
  observed.clear();
  delete (globalThis as { IntersectionObserver?: unknown }).IntersectionObserver;
}

function fireIntersection(element: Element, isIntersecting: boolean) {
  act(() => {
    observed.get(element)?.(
      [{ isIntersecting, target: element } as IntersectionObserverEntry],
      {} as IntersectionObserver,
    );
  });
}

describe("EnclosureCanvas — off-screen GSAP pause (260721 C1)", () => {
  beforeEach(() => {
    document.documentElement.removeAttribute("data-effects"); // effects ON — the context builds
    installMockIO();
  });
  afterEach(() => {
    uninstallMockIO();
  });

  it("pauses every context tween while hidden and resumes on re-show — without rebuilding the context", () => {
    const contextSpy = vi.spyOn(gsap, "context");
    const { getByTestId } = render(
      <EnclosureProcessMap node={nodeFrom("engine-bootstrap")} />,
    );
    expect(contextSpy).toHaveBeenCalledTimes(1);
    const ctx = contextSpy.mock.results[0].value as gsap.Context;
    const tweens = ctx.getTweens() as gsap.core.Tween[];
    expect(tweens.length).toBeGreaterThan(0);
    expect(tweens.every((tween) => !tween.paused())).toBe(true); // running while on-screen

    fireIntersection(getByTestId("enclosure-canvas"), false); // the tab switch hides the layer
    expect(tweens.every((tween) => tween.paused())).toBe(true);

    fireIntersection(getByTestId("enclosure-canvas"), true); // back on-screen → mid-beat resume
    expect(tweens.every((tween) => !tween.paused())).toBe(true);
    // the pause never reverted/rebuilt the choreography: still the one original context
    expect(contextSpy).toHaveBeenCalledTimes(1);
    contextSpy.mockRestore();
  });

  it("keeps a context rebuilt while hidden paused (a projection beat landing under display:none)", () => {
    const contextSpy = vi.spyOn(gsap, "context");
    const view = render(
      <EnclosureProcessMap node={nodeFrom("engine-bootstrap")} />,
    );
    fireIntersection(view.getByTestId("enclosure-canvas"), false);
    // the signature changes while the tab is hidden → the choreography reverts + rebuilds; the new
    // context must inherit the pause in the same commit (the pause effect trails the build effect).
    view.rerender(
      <EnclosureProcessMap node={nodeFrom("engine-sync-needed")} />,
    );
    expect(contextSpy).toHaveBeenCalledTimes(2);
    const rebuilt = contextSpy.mock.results[1].value as gsap.Context;
    const tweens = rebuilt.getTweens() as gsap.core.Tween[];
    expect(tweens.length).toBeGreaterThan(0);
    expect(tweens.every((tween) => tween.paused())).toBe(true);

    fireIntersection(view.getByTestId("enclosure-canvas"), true);
    expect(tweens.every((tween) => !tween.paused())).toBe(true);
    contextSpy.mockRestore();
  });
});

describe("EnclosureProcessMap — backdrop video follows layer visibility (260721 C1)", () => {
  beforeEach(() => {
    document.documentElement.removeAttribute("data-effects"); // effects ON — the backdrop mounts
    installMockIO();
  });
  afterEach(() => {
    uninstallMockIO();
  });

  it("pauses the looping blueprint video while hidden and plays it again on re-show", () => {
    const playSpy = vi.spyOn(HTMLMediaElement.prototype, "play");
    const pauseSpy = vi.spyOn(HTMLMediaElement.prototype, "pause");
    const { getByTestId } = render(<EnclosureProcessMap node={nodeFrom("engine-bootstrap")} />);
    const video = getByTestId("backdrop").querySelector("video");
    expect(video).not.toBeNull();
    expect(playSpy).toHaveBeenCalled(); // visible on mount → playing
    expect(pauseSpy).not.toHaveBeenCalled();

    fireIntersection(getByTestId("process-map"), false);
    expect(pauseSpy).toHaveBeenCalledTimes(1);

    fireIntersection(getByTestId("process-map"), true);
    expect(playSpy.mock.calls.length).toBeGreaterThanOrEqual(2); // resumed
    expect(pauseSpy).toHaveBeenCalledTimes(1);
    playSpy.mockRestore();
    pauseSpy.mockRestore();
  });
});

describe("EnclosureCanvas — transform-driven fx geometry (260721 C1)", () => {
  it("renders the warp-surge bands at FULL geometry so scaleY-from-the-link replaces the y1/y2 attr tween", () => {
    const { container } = render(<EnclosureProcessMap node={nodeFrom("engine-bootstrap")} />);
    const up = container.querySelector('[data-testid="warp-surge"][data-dir="up"]');
    const down = container.querySelector('[data-testid="warp-surge"][data-dir="down"]');
    // cy = 342; the old tween's targets were y1/y2 = 342∓26 / 342∓4 — now the rendered geometry,
    // with GSAP scaling from (x, 342) instead of writing attributes every frame.
    expect([up?.getAttribute("y1"), up?.getAttribute("y2")]).toEqual(["316", "338"]);
    expect([down?.getAttribute("y1"), down?.getAttribute("y2")]).toEqual(["368", "346"]);
  });
});
