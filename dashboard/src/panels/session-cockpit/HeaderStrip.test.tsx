// HeaderStrip anatomy + stage container: identity → controls →
// state → diagnostics; the ModelEffortControl slot; freshness honesty; the
// one-plain-pair rule (no provenance duplication); the focus-handoff note on the stage.
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { fromTerminalSessionInfo } from "../../data/sessions";
import { catalogRow, FLEET } from "../../test/fixtures/catalogRows";
import { HeaderStrip } from "./HeaderStrip";
import { SessionStage } from "./SessionStage";

const worker = fromTerminalSessionInfo(FLEET.find((row) => row.id === "worker-l4")!);

afterEach(cleanup);

describe("HeaderStrip (R10)", () => {
  it("renders the §1.2 anatomy in order: identity → controls → state → leaf → diagnostics", () => {
    const { getByTestId } = render(<HeaderStrip session={worker} cockpit={undefined} />);
    const strip = getByTestId("header-strip");
    const segments = [...strip.querySelectorAll("[data-header-segment]")].map((node) =>
      node.getAttribute("data-header-segment"),
    );
    expect(segments).toEqual(["identity", "controls", "state", "leaf", "diagnostics"]);
    expect(getByTestId("header-leaf").textContent).not.toContain("seat");
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

  it("keeps the neutral dot but omits the meaningless dash for an unclassified raw terminal", () => {
    const terminal = fromTerminalSessionInfo(catalogRow({ id: "raw", kind: "terminal" }));
    const { getByTestId, getByRole } = render(
      <HeaderStrip session={terminal} cockpit={undefined} />,
    );

    expect(getByTestId("header-state").textContent).toBe("");
    expect(getByTestId("header-dot").getAttribute("data-state")).toBe("unclassified");
    expect(getByRole("img", { name: "state unavailable" })).toBeTruthy();
  });

  it("freshness honesty (R15/RV-R3): the ws marker collapses when no pane reports a state, real state + quiet age when known, sweep bound in the tooltip", () => {
    const bare = render(<HeaderStrip session={worker} cockpit={undefined} />);
    // `ws —` on a seat with no pane is an em-dash placeholder; it collapses (is omitted)
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

  it("one plain pair (260723): the control carries model · effort; diagnostics never duplicate it", () => {
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
    const { getByTestId, queryByTestId } = render(
      <HeaderStrip session={readyClaude} cockpit={undefined} />,
    );
    // The pair reads plainly from the one control — no evidence badge, no tier word, no
    // duplicated provenance chip in the diagnostics cluster.
    expect(getByTestId("model-effort-trigger-model").textContent).toBe("claude-fable-5[1m]");
    expect(getByTestId("model-effort-trigger-effort").textContent).toBe("max");
    expect(queryByTestId("header-provenance-model")).toBeNull();
    expect(getByTestId("header-strip").querySelector("[data-evidence-tier]")).toBeNull();
    expect(getByTestId("header-diagnostics").textContent).not.toContain("model");
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
  it("reserves NO WorkingLine slot in the stage chrome (F-as: it lives between conversation and composer)", () => {
    const { getByTestId, queryByTestId } = render(
      <SessionStage focused={worker} cockpit={undefined} handoff={null}>
        <div data-testid="surface-child" />
      </SessionStage>,
    );
    // The turn theater docks where the eye waits for the next
    // message — SessionsView renders the slot between the conversation and the composer;
    // the stage's top chrome no longer owns one.
    expect(queryByTestId("stage-working-line-slot")).toBeNull();
    expect(getByTestId("surface-child")).not.toBeNull();
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
