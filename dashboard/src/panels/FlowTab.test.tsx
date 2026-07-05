import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { FLOW_MODELS } from "./flowModels";
import { FlowTab } from "./FlowTab";

afterEach(cleanup);

describe("FlowTab canvas (unified l-01-agent-lifecycles)", () => {
  it("renders the router model by default — the unified skill's spine", () => {
    const { getByTestId, getByText } = render(<FlowTab />);
    expect(getByTestId("flow-tab").getAttribute("data-model")).toBe("router");
    expect(getByText(/one skill, one lifecycle per agent type/)).not.toBeNull();
    expect(getByTestId("flow-nav")).not.toBeNull();
    // The retired models are gone from the nav.
    expect(document.querySelector('[data-testid="flow-nav-build-job"]')).toBeNull();
    expect(document.querySelector('[data-testid="flow-nav-frame"]')).toBeNull();
  });

  it("switches models through the nav radiogroup", () => {
    const { getByTestId, getByText } = render(<FlowTab />);
    fireEvent.click(getByTestId("flow-nav-orchestrator"));
    expect(getByTestId("flow-tab").getAttribute("data-model")).toBe("orchestrator");
    expect(getByText(/the event loop, drawn on its biggest run/)).not.toBeNull();
    expect(getByTestId("flow-nav-orchestrator").getAttribute("aria-checked")).toBe("true");
    expect(getByTestId("flow-nav-router").getAttribute("aria-checked")).toBe("false");
    fireEvent.click(getByTestId("flow-nav-comms"));
    expect(getByTestId("flow-tab").getAttribute("data-model")).toBe("comms");
  });

  it("honors initialModel and falls back to the first model for unknown ids", () => {
    const { getByTestId, unmount } = render(<FlowTab initialModel="manager" />);
    expect(getByTestId("flow-tab").getAttribute("data-model")).toBe("manager");
    unmount();
    const { getByTestId: second } = render(<FlowTab initialModel="nope" />);
    expect(second("flow-tab").getAttribute("data-model")).toBe("router");
  });

  it("renders every registered model without crashing (nodes, gates, rundowns)", () => {
    for (const model of FLOW_MODELS) {
      const { getAllByTestId, getByTestId, queryAllByTestId, unmount } = render(
        <FlowTab initialModel={model.id} />,
      );
      expect(getByTestId("flow-tab").getAttribute("data-model")).toBe(model.id);
      const nodes = model.segments.filter((s) => s.kind === "node");
      const gates = nodes.filter((s) => s.kind === "node" && s.rides);
      if (gates.length > 0) expect(getAllByTestId("flow-gate").length).toBe(gates.length);
      if (nodes.length - gates.length > 0) {
        expect(getAllByTestId("flow-node").length).toBe(nodes.length - gates.length);
      }
      const rundowns = model.segments.filter((s) => s.kind === "rundown");
      expect(queryAllByTestId("flow-rundown").length).toBe(rundowns.length);
      unmount();
    }
  });

  it("encodes the router invariants: three conditions, the ladder, no chat builds", () => {
    const { getByText } = render(<FlowTab initialModel="router" />);
    expect(getByText(/the three conditions — in order, no fourth entry/)).not.toBeNull();
    expect(
      getByText(/task doc \(approved\) → branch \(intent\) → worktree \(only where something is built\)/),
    ).not.toBeNull();
    expect(getByText(/⟁ chat is never a build route — small code work takes the minimal w-02 artifact/)).not.toBeNull();
  });

  it("encodes the agreed orchestration invariants on the drawn models", () => {
    const { getByTestId, getByText } = render(<FlowTab initialModel="orchestrator" />);
    // Master-granular DAG + the branch-not-worktree intent + the delegated handover decision.
    expect(getByText(/reshape master boundaries — NEVER interleave dispatch/)).not.toBeNull();
    expect(getByText(/creates a BRANCH off main, nothing more/)).not.toBeNull();
    expect(
      getByText(/the ORCHESTRATOR decides by the packet-carried gateId \(own ambient identity/),
    ).not.toBeNull();
    // The escalation ladder lives on the comms drawing; the spirit test is ORCHESTRATOR-ONLY.
    fireEvent.click(getByTestId("flow-nav-comms"));
    expect(getByText(/escalation · worker → manager → orchestrator → developer/)).not.toBeNull();
    expect(getByText(/spirit test — ORCHESTRATOR-ONLY/)).not.toBeNull();
    // Managers escalate plan deltas instead of judging them, and reopen wrong deliverables.
    fireEvent.click(getByTestId("flow-nav-manager"));
    expect(getByText(/managers don't reshape plans \(no bird's-eye\)/)).not.toBeNull();
    expect(getByText(/task_reopen the SAME leaf, reshape — never a redo sibling/)).not.toBeNull();
    // The ruled seam channel: non-blocking raise, the gateId rides the packet.
    expect(
      getByText(/the returned gateId rides the packet via inbox \+ push · the ORCHESTRATOR decides the gate by that id/),
    ).not.toBeNull();
  });

  it("draws the worker as brief-started with no lifecycle machinery", () => {
    const { getByTestId, getByText } = render(<FlowTab initialModel="worker" />);
    expect(getByTestId("flow-tab").getAttribute("data-model")).toBe("worker");
    expect(getByText(/one leaf, one session, one report/)).not.toBeNull();
    expect(getByText(/NEVER git commit/)).not.toBeNull();
    expect(
      getByText(/the owning seat runs closeout → integrate → finalize/),
    ).not.toBeNull();
  });

  it("draws the designer as the hat the orchestrator pulls", () => {
    const { getByTestId, getByText } = render(<FlowTab initialModel="designer" />);
    expect(getByTestId("flow-tab").getAttribute("data-model")).toBe("designer");
    expect(getByText(/the hat the orchestrator pulls/)).not.toBeNull();
    expect(getByText(/ORCHESTRATOR adversarially reviews the design/)).not.toBeNull();
    expect(getByText(/ask — never fill silently/)).not.toBeNull();
  });

  it("draws the reviewer with verdicts as evidence and the ruled deciders", () => {
    const { getByTestId, getByText } = render(<FlowTab initialModel="reviewer" />);
    expect(getByTestId("flow-tab").getAttribute("data-model")).toBe("reviewer");
    expect(
      getByText(/verdicts are evidence, not decisions — requireReviewerVerdictAtSeams binds delegated seam decisions/),
    ).not.toBeNull();
    expect(getByText(/the ORCHESTRATOR at master-exit \(master-handover-approval\)/)).not.toBeNull();
    expect(getByText(/⟁ block\? → decomposable fix leaves/)).not.toBeNull();
  });
});
