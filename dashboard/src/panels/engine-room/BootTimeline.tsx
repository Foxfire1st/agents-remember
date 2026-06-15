import { css } from "../../../styled-system/css";
import type { EngineProcessNode } from "../../types/projection";
import { sectionLabel, timeline, timelineMark, timelineStep } from "./engineRoomStyles";

type StepState =
  | "complete"
  | "current"
  | "running"
  | "failed"
  | "blocked"
  | "pending"
  | "skipped";

interface Step {
  label: string;
  state: StepState;
}

// Edge state -> a boot-timeline step state (the timeline reads the same facts as the map).
const EDGE_TO_STEP: Record<string, StepState> = {
  complete: "complete",
  nominal: "complete",
  running: "running",
  stale: "failed",
  failed: "failed",
  blocked: "blocked",
  skipped: "skipped",
  planned: "pending",
  unknown: "pending",
};

const STATE_LABEL: Record<StepState, string> = {
  complete: "done",
  current: "current",
  running: "running",
  failed: "failed",
  blocked: "blocked",
  pending: "pending",
  skipped: "skipped",
};

function materialized(exists: boolean | undefined): StepState {
  return exists === true ? "complete" : "pending";
}

function steadyState(node: EngineProcessNode): StepState {
  if (node.health === "failed") return "failed";
  if (node.health === "blocked") return "blocked";
  if (node.health === "running") return "running";
  if (node.health === "complete" || node.health === "nominal") {
    return node.providers.length > 0 ? "complete" : "pending";
  }
  return "pending";
}

function bootSteps(node: EngineProcessNode): Step[] {
  const edge = (kind: string): StepState => {
    const found = node.edges.find((candidate) => candidate.kind === kind);
    return found ? (EDGE_TO_STEP[found.state] ?? "pending") : "pending";
  };
  const steps: Step[] = [{ label: "Code worktree", state: materialized(node.codeWorktree.exists) }];
  if (node.memoryMode === "external") {
    steps.push({
      label: "Ledger-map · memory worktree",
      state: materialized(node.memoryWorktree?.exists),
    });
  } else {
    steps.push({ label: `Memory (${node.memoryMode})`, state: "skipped" });
  }
  steps.push({ label: "Contract anchor", state: "complete" });
  steps.push({ label: "CGC seed", state: edge("cgc-seed") });
  if (node.memoryMode === "external") {
    steps.push({ label: "GrepAI clone", state: edge("grepai-clone") });
  }
  steps.push({ label: "Watchers · steady state", state: steadyState(node) });
  return steps;
}

export function BootTimeline({ node }: { node: EngineProcessNode }) {
  return (
    <div className={timeline} data-testid="boot-timeline">
      <span className={sectionLabel}>Boot sequence</span>
      <ol className={css({ display: "grid", gap: "0.2rem", margin: "0", padding: "0", listStyle: "none" })}>
        {bootSteps(node).map((step) => (
          <li key={step.label} className={timelineStep({ state: step.state })} data-state={step.state}>
            <span className={timelineMark({ state: step.state })} aria-hidden="true" />
            <span>{step.label}</span>
            <span className={css({ marginLeft: "auto", color: "muted", fontSize: "0.62rem" })}>
              {STATE_LABEL[step.state]}
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}
