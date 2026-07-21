// HeaderStrip anatomy + stage container (260715-FEUI-L2 S5, spec §1.2): identity → controls →
// state → diagnostics; the ModelEffortControl slot (L4 fills it); freshness honesty; provenance
// badges (R7); the reserved WorkingLine slot and the focus-handoff note on the stage.
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { fromTerminalSessionInfo } from "../../data/sessions";
import { catalogRow, FLEET } from "../../test/fixtures/catalogRows";
import { HeaderStrip } from "./HeaderStrip";
import { SessionStage } from "./SessionStage";

const worker = fromTerminalSessionInfo(FLEET.find((row) => row.id === "worker-l4")!);

afterEach(cleanup);

describe("HeaderStrip (R10)", () => {
  it("renders the §1.2 anatomy in order: identity → controls → state → (leaf/seat) → diagnostics", () => {
    const { getByTestId } = render(<HeaderStrip session={worker} cockpit={undefined} />);
    const strip = getByTestId("header-strip");
    const segments = [...strip.querySelectorAll("[data-header-segment]")].map((node) =>
      node.getAttribute("data-header-segment"),
    );
    expect(segments).toEqual(["identity", "controls", "state", "leaf-seat", "diagnostics"]);
  });

  it("mounts the ModelEffortControl into the reserved slot (L4)", () => {
    const { getByTestId } = render(<HeaderStrip session={worker} cockpit={undefined} />);
    const slot = getByTestId("header-control-slot");
    expect(slot.getAttribute("data-slot")).toBe("model-effort-control");
    expect(slot.querySelector('[data-testid="model-effort-control"]')).not.toBeNull();
    expect(slot.querySelector('[data-testid="model-effort-trigger"]')).not.toBeNull();
  });

  it("renders the state dot + word from the shared grammar", () => {
    const { getByTestId } = render(<HeaderStrip session={worker} cockpit={undefined} />);
    expect(getByTestId("header-state").textContent).toContain("working");
    expect(getByTestId("header-dot").getAttribute("data-state")).toBe("working");
  });

  it("freshness honesty (R15/RV-R3): the ws marker collapses when no pane reports a state, real state + quiet age when known, sweep bound in the tooltip", () => {
    const bare = render(<HeaderStrip session={worker} cockpit={undefined} />);
    // RV/R3 — `ws —` on a seat with no pane is an em-dash placeholder; it collapses (is omitted)
    // rather than rendering a bare dash on every seat. The sweep-bound tooltip still explains freshness.
    expect(bare.getByTestId("header-diagnostics").textContent).not.toContain("ws —");
    expect(bare.getByTestId("header-diagnostics").textContent).not.toContain("quiet");
    expect(bare.getByTestId("header-diagnostics").getAttribute("title")).toContain("10 s");
    bare.unmount();

    const now = Date.now();
    const live = render(
      <HeaderStrip
        session={worker}
        now={now}
        cockpit={{
          snapshotLoading: false,
          echoEvidence: {},
          pendingSets: {},
          setLedger: [],
          launchEvidence: { tier: "pending" },
          composer: { draft: "", draftRevision: 0 },
          surfaceTab: "terminal",
          turnClock: { workingSince: null },
          freshness: { ptyWs: "connected", lastOutputAt: now - 3000 },
          queue: [],
          submitHistory: [],
        }}
      />,
    );
    expect(live.getByTestId("header-diagnostics").textContent).toContain("ws ✓");
    expect(live.getByTestId("header-diagnostics").textContent).toContain("quiet 3s");
  });

  it("provenance badges (R7): the pair renders at the tier DERIVED from control state", () => {
    // A purpose-built row (review finding 7 — not FLEET's worker-l4, whose harness/key pairing
    // is an L2 fixture quirk): controlState 'ready' on the claude harness, where stream-json
    // emits no launch-effort echo, so the pair's honest ceiling is 'model-validated'.
    const readyClaude = fromTerminalSessionInfo(
      catalogRow({
        id: "ready-claude",
        harness: "claude",
        resolvedModel: "claude-fable-5[1m]",
        resolvedEffort: "max",
        controlState: "ready",
        spawnLevel: "leaf",
        spawnLevelSource: "default",
      }),
    );
    const { getByTestId } = render(<HeaderStrip session={readyClaude} cockpit={undefined} />);
    expect(getByTestId("header-provenance-model").textContent).toContain(
      "claude-fable-5[1m] · max",
    );
    expect(getByTestId("header-provenance-model").textContent).toContain("(model-validated)");
    const badge = getByTestId("header-provenance-model").querySelector("[data-evidence-tier]");
    expect(badge?.getAttribute("data-evidence-tier")).toBe("model-validated");
    expect(getByTestId("header-provenance-level").textContent).toContain("leaf (default)");
  });

  it("provenance badges (R7): a STARTING row renders the retained pair as requested/pending", () => {
    const starting = fromTerminalSessionInfo(
      catalogRow({
        id: "starting-1",
        harness: "claude",
        resolvedModel: "sonnet",
        resolvedEffort: "high",
        controlState: "starting",
      }),
    );
    const { getByTestId } = render(<HeaderStrip session={starting} cockpit={undefined} />);
    expect(getByTestId("header-provenance-model").textContent).toContain("(requested)");
    const badge = getByTestId("header-provenance-model").querySelector("[data-evidence-tier]");
    expect(badge?.getAttribute("data-evidence-tier")).toBe("pending");
  });

  it("renders no provenance chips for a hand-opened session — absent, never invented", () => {
    const bare = fromTerminalSessionInfo(catalogRow({ id: "hand" }));
    const { queryByTestId } = render(<HeaderStrip session={bare} cockpit={undefined} />);
    expect(queryByTestId("header-provenance-model")).toBeNull();
    expect(queryByTestId("header-provenance-level")).toBeNull();
  });
});

describe("SessionStage container (R10)", () => {
  it("reserves the WorkingLine slot directly under the header (rendered by L6)", () => {
    const { getByTestId } = render(
      <SessionStage focused={worker} cockpit={undefined} handoff={null}>
        <div data-testid="surface-child" />
      </SessionStage>,
    );
    const slot = getByTestId("stage-working-line-slot");
    expect(slot.getAttribute("data-slot")).toBe("working-line");
    const header = getByTestId("session-stage").querySelector("[data-stage-header]");
    // The slot follows the header immediately (before the surface child).
    expect(header && (header.compareDocumentPosition(slot) & 4)).toBe(4);
    expect(slot.compareDocumentPosition(getByTestId("surface-child")) & 4).toBe(4);
  });

  it("shows the focus-handoff note (F17) and the explained empty state (R9)", () => {
    const withNote = render(
      <SessionStage focused={worker} cockpit={undefined} handoff="worker-L4-serving landed — leaf integrated · focus handed off">
        <div />
      </SessionStage>,
    );
    expect(withNote.getByTestId("stage-handoff-note").textContent).toContain("focus handed off");
    withNote.unmount();

    const empty = render(
      <SessionStage focused={undefined} cockpit={undefined} handoff={null}>
        <div />
      </SessionStage>,
    );
    expect(empty.getByTestId("stage-empty-identity").textContent).toContain("no focused session");
  });
});
