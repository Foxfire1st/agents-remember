// HeaderStrip anatomy + stage container (260715-FEUI-L2 S5, spec §1.2): identity → controls →
// state → diagnostics; the EMPTY ModelEffortControl slot; freshness honesty; provenance badges
// (R7); the reserved WorkingLine slot and the focus-handoff note on the stage.
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

  it("ships the ModelEffortControl slot EMPTY (L4 fills it)", () => {
    const { getByTestId } = render(<HeaderStrip session={worker} cockpit={undefined} />);
    const slot = getByTestId("header-control-slot");
    expect(slot.getAttribute("data-slot")).toBe("model-effort-control");
    expect(slot.childElementCount).toBe(0);
    expect(slot.textContent).toBe("");
  });

  it("renders the state dot + word from the shared grammar", () => {
    const { getByTestId } = render(<HeaderStrip session={worker} cockpit={undefined} />);
    expect(getByTestId("header-state").textContent).toContain("working");
    expect(getByTestId("header-dot").getAttribute("data-state")).toBe("working");
  });

  it("freshness honesty (R15): 'ws —' with no pane, real state + quiet age when known, sweep bound in the tooltip", () => {
    const bare = render(<HeaderStrip session={worker} cockpit={undefined} />);
    expect(bare.getByTestId("header-diagnostics").textContent).toContain("ws —");
    expect(bare.getByTestId("header-diagnostics").textContent).not.toContain("quiet");
    expect(bare.getByTestId("header-diagnostics").getAttribute("title")).toContain("10 s");
    bare.unmount();

    const now = Date.now();
    const live = render(
      <HeaderStrip
        session={worker}
        now={now}
        cockpit={{
          pendingSets: {},
          setLedger: [],
          launchEvidence: { tier: "pending" },
          composer: { draft: "", draftRevision: 0 },
          surfaceTab: "terminal",
          turnClock: { workingSince: null },
          freshness: { ptyWs: "connected", lastOutputAt: now - 3000 },
          queue: [],
        }}
      />,
    );
    expect(live.getByTestId("header-diagnostics").textContent).toContain("ws ✓");
    expect(live.getByTestId("header-diagnostics").textContent).toContain("quiet 3s");
  });

  it("provenance badges (R7): requested model/effort at the honest tier + spawn level/source", () => {
    const { getByTestId } = render(<HeaderStrip session={worker} cockpit={undefined} />);
    expect(getByTestId("header-provenance-model").textContent).toContain("gpt-5.6-sol · xhigh");
    expect(getByTestId("header-provenance-model").textContent).toContain("(requested)"); // tier 'pending'
    expect(getByTestId("header-provenance-level").textContent).toContain("leaf (default)");
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
