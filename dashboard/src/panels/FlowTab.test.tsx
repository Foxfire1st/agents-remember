import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { FLOW_MODELS } from "./flowModels";
import { FlowTab } from "./FlowTab";

afterEach(cleanup);

describe("FlowTab canvas (orchestration L0)", () => {
  it("renders the original build-job model by default", () => {
    const { getByTestId, getByText } = render(<FlowTab />);
    expect(getByTestId("flow-tab").getAttribute("data-model")).toBe("build-job");
    expect(getByText(/build job — the hint chain/)).not.toBeNull();
    // The task-26 content survives: the prose rundown card + a gate node riding a notification.
    expect(getByTestId("flow-nav")).not.toBeNull();
    expect(getByText(/worktree_start --dry-run/)).not.toBeNull();
  });

  it("switches models through the nav radiogroup", () => {
    const { getByTestId, getByText } = render(<FlowTab />);
    fireEvent.click(getByTestId("flow-nav-orchestrator"));
    expect(getByTestId("flow-tab").getAttribute("data-model")).toBe("orchestrator");
    expect(getByText(/portfolio → super integration branch/)).not.toBeNull();
    expect(getByTestId("flow-nav-orchestrator").getAttribute("aria-checked")).toBe("true");
    expect(getByTestId("flow-nav-build-job").getAttribute("aria-checked")).toBe("false");
    fireEvent.click(getByTestId("flow-nav-comms"));
    expect(getByTestId("flow-tab").getAttribute("data-model")).toBe("comms");
  });

  it("honors initialModel and falls back to the first model for unknown ids", () => {
    const { getByTestId, unmount } = render(<FlowTab initialModel="manager" />);
    expect(getByTestId("flow-tab").getAttribute("data-model")).toBe("manager");
    unmount();
    const { getByTestId: second } = render(<FlowTab initialModel="nope" />);
    expect(second("flow-tab").getAttribute("data-model")).toBe("build-job");
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

  it("encodes the agreed orchestration invariants on the drawn models", () => {
    const { getByTestId, getByText, getAllByText } = render(<FlowTab initialModel="orchestrator" />);
    // Master-granular DAG invariant + the two seams live on the orchestrator drawing.
    expect(getByText(/reshape master boundaries — NEVER interleave dispatch/)).not.toBeNull();
    expect(getAllByText(/adversarial review seam/).length).toBe(2);
    // The escalation ladder lives on the comms drawing; the spirit test is ORCHESTRATOR-ONLY.
    fireEvent.click(getByTestId("flow-nav-comms"));
    expect(getByText(/escalation · worker → manager → orchestrator → developer/)).not.toBeNull();
    expect(getByText(/spirit test — ORCHESTRATOR-ONLY/)).not.toBeNull();
    // Managers escalate plan deltas instead of judging them (no spirit test below the bird's-eye).
    fireEvent.click(getByTestId("flow-nav-manager"));
    expect(getByText(/managers don't reshape plans \(no bird's-eye\)/)).not.toBeNull();
  });

  it("draws the designer as its own job with the orchestrator as its adversarial reviewer", () => {
    const { getByTestId, getByText } = render(<FlowTab initialModel="designer" />);
    expect(getByTestId("flow-tab").getAttribute("data-model")).toBe("designer");
    expect(getByText(/co-think a master task with the developer/)).not.toBeNull();
    expect(getByText(/ORCHESTRATOR adversarially reviews the design/)).not.toBeNull();
    expect(getByText(/ask — never fill silently/)).not.toBeNull();
  });

  it("draws the frame as the thin consistent runtime housing every job", () => {
    const { getByTestId, getByText } = render(<FlowTab initialModel="frame" />);
    expect(getByTestId("flow-tab").getAttribute("data-model")).toBe("frame");
    expect(getByText(/context → job → wrap-up \(the consistent runtime\)/)).not.toBeNull();
    expect(getByText(/the JOB's own flow takes over — see its drawing; the frame stays thin/)).not.toBeNull();
    // The build-job drawing names itself as the Wollmilchsau the frame decomposes.
    fireEvent.click(getByTestId("flow-nav-build-job"));
    expect(getByText(/Eierlegende Wollmilchsau/)).not.toBeNull();
  });

  it("draws the reviewer with verdicts as evidence, never decisions", () => {
    const { getByTestId, getByText } = render(<FlowTab initialModel="reviewer" />);
    expect(getByTestId("flow-tab").getAttribute("data-model")).toBe("reviewer");
    expect(getByText(/verdicts are evidence, not decisions — a policy may REQUIRE a verdict/)).not.toBeNull();
    expect(getByText(/onboarding-vs-code — changed files' sidecars updated in the same pass/)).not.toBeNull();
    expect(getByText(/⟁ block\? → decomposable fix leaves/)).not.toBeNull();
  });
});
