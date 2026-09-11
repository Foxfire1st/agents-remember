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
    expect(getByText(/backend event loop, drawn on its biggest run/)).not.toBeNull();
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
    expect(getByText(/unresolvable role or incomplete hosted identity → FAIL CLOSED/)).not.toBeNull();
    expect(
      getByText(/task doc \(approved\) → branch \(intent\) → worktree \(only where something is built\)/),
    ).not.toBeNull();
    expect(getByText(/⟁ chat is never a build route — small code work takes the minimal w-02 artifact/)).not.toBeNull();
    expect(getByText(/otherwise: free chat → create\/resolve sprint \+ first leaf → sprint-bound architect/)).not.toBeNull();
  });

  it("draws the architect as the sprint-bound owner and decision relay", () => {
    const { getByTestId, getByText } = render(<FlowTab initialModel="architect" />);
    expect(getByTestId("flow-tab").getAttribute("data-model")).toBe("architect");
    expect(getByText(/sprint-local owner, drawing board, decision relay/)).not.toBeNull();
    expect(getByText(/backend decision-item → present ONE item, record the durable ruling/)).not.toBeNull();
    expect(getByText(/roles expand into task-document-owned chats/)).not.toBeNull();
  });

  it("encodes the agreed orchestration invariants on the drawn models", () => {
    const { getByTestId, getByText } = render(<FlowTab initialModel="orchestrator" />);
    // Master-granular DAG + the branch-not-worktree intent + the delegated handover decision.
    expect(getByText(/reshape master boundaries — NEVER interleave dispatch/)).not.toBeNull();
    expect(getByText(/creates a BRANCH off main, nothing more/)).not.toBeNull();
    expect(
      getByText(/the BACKEND ORCHESTRATOR resolves one gate by canonical master document \+ kind/),
    ).not.toBeNull();
    // The escalation ladder lives on the comms drawing; the spirit test is limited to bird's-eye seats.
    fireEvent.click(getByTestId("flow-nav-comms"));
    expect(getByText(/escalation · worker → manager → orchestrator → architect → developer/)).not.toBeNull();
    expect(getByText(/spirit test — BIRD'S-EYE SEAT ONLY: backend orchestrator or architect/)).not.toBeNull();
    // Managers escalate plan deltas instead of judging them, and reopen wrong deliverables.
    fireEvent.click(getByTestId("flow-nav-manager"));
    expect(getByText(/managers don't reshape plans \(no bird's-eye\)/)).not.toBeNull();
    expect(getByText(/task_reopen the SAME leaf, reshape — never a redo sibling/)).not.toBeNull();
    // The ruled seam channel: non-blocking raise and structural master-document decision.
    expect(
      getByText(/the ambient master seat supplies the structural address/),
    ).not.toBeNull();
    expect(
      getByText(/the BACKEND ORCHESTRATOR decides by canonical master document \+ gate kind/),
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

  it("draws the strategist as the optional spawn-first sprint planner", () => {
    const { getByTestId, getByText } = render(<FlowTab initialModel="strategist" />);
    expect(getByTestId("flow-tab").getAttribute("data-model")).toBe("strategist");
    expect(getByText(/the sprint planner, spawn-first/)).not.toBeNull();
    expect(getByText(/the optional portfolio pass — approved explicitly, never auto-run/)).not.toBeNull();
    expect(getByText(/when approved, even a single master gets the full method/)).not.toBeNull();
    expect(
      getByText(/4 doctrine edges, cited — an uncited edge is refutable by default · 5 blast-radius register \(low\/med\/high — feeds the loop-tier scoring\)/),
    ).not.toBeNull();
    expect(
      getByText(/⟁ a leaf naming neither existing surfaces nor a parent anchor → finding: unplannable as scoped — never a silent guess/),
    ).not.toBeNull();
    expect(
      getByText(/reader-not-mutator: the strategist drafts; the BACKEND ORCHESTRATOR adopts it into durable task form \(decision-log entry\)/),
    ).not.toBeNull();
  });

  it("encodes the three-party-loop invariants across the drawn models", () => {
    const { getByTestId, getByText } = render(<FlowTab initialModel="manager" />);
    expect(
      getByText(/score each leaf's loop tier at dispatch — direct · builder-verified · full loop/),
    ).not.toBeNull();
    expect(
      getByText(/⟁ full-loop leaves: HARD cap 3 full rounds \(delta-verifies don't count\) · a non-shrinking round escalates NOW with the round history/),
    ).not.toBeNull();
    fireEvent.click(getByTestId("flow-nav-comms"));
    expect(
      getByText(/⟁ quo-vadis — a high-blast-radius TRUTH \(answered wrong = big rewrites later\) goes to the architect relay IMMEDIATELY; presentation-grade \(2px vs 3px\) never does/),
    ).not.toBeNull();
    fireEvent.click(getByTestId("flow-nav-reviewer"));
    expect(
      getByText(/criteria catalogs bound per review type \(criteria\/: code-seam · doctrine · onboarding-memory · report-verification · plan-review\)/),
    ).not.toBeNull();
    expect(
      getByText(/loop-seat reuse: delta-verifies resume the SAME reviewer \(they close rounds, never open them\); fresh reviewer only for a full round/),
    ).not.toBeNull();
    fireEvent.click(getByTestId("flow-nav-worker"));
    expect(
      getByText(/loop position: the BUILDER seat — fix rounds resume the SAME session; round-2\+ reports APPEND \(loop history stays legible\)/),
    ).not.toBeNull();
    fireEvent.click(getByTestId("flow-nav-orchestrator"));
    expect(
      getByText(/optional STRATEGIST pass — architect proposes it; if approved, its orchestration task becomes sprint plan \+ scope/),
    ).not.toBeNull();
    expect(
      getByText(/visible-behavior-first in a REVIEWABLE ENVIRONMENT \(the dashboard\) with demo notes \(what changed visibly\), code second/),
    ).not.toBeNull();
  });

  it("draws the designer as an optional sprint role", () => {
    const { getByTestId, getByText } = render(<FlowTab initialModel="designer" />);
    expect(getByTestId("flow-tab").getAttribute("data-model")).toBe("designer");
    expect(getByText(/optional sprint design seat/)).not.toBeNull();
    expect(getByText(/BACKEND ORCHESTRATOR adversarially reviews the design/)).not.toBeNull();
    expect(getByText(/ask — never fill silently/)).not.toBeNull();
  });

  it("draws the reviewer with verdicts as evidence and the ruled deciders", () => {
    const { getByTestId, getByText } = render(<FlowTab initialModel="reviewer" />);
    expect(getByTestId("flow-tab").getAttribute("data-model")).toBe("reviewer");
    expect(
      getByText(/verdicts are evidence, not decisions — requireReviewerVerdictAtSeams binds delegated seam decisions/),
    ).not.toBeNull();
    expect(getByText(/the BACKEND ORCHESTRATOR at master-exit \(master-handover-approval\)/)).not.toBeNull();
    expect(getByText(/leaf route\/full-loop → leaf document · parent = manager/)).not.toBeNull();
    expect(getByText(/portfolio plan → sprint document · parent = architect/)).not.toBeNull();
    expect(getByText(/super-exit → sprint document · parent = orchestrator/)).not.toBeNull();
    expect(getByText(/⟁ block\? → decomposable fix leaves/)).not.toBeNull();
  });
});
