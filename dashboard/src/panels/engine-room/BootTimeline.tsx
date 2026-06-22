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
  // Contract anchor LEADS the boot sequence (dev directive): the task contract is the precondition the
  // worktree is created from, so it heads the list — before the code / memory worktrees materialise and the
  // providers seed — rather than sitting in the middle.
  const steps: Step[] = [
    { label: "Contract anchor", state: "complete" },
    { label: "Code worktree", state: materialized(node.codeWorktree.exists) },
  ];
  if (node.memoryMode === "external") {
    steps.push({
      label: "Ledger-map · memory worktree",
      state: materialized(node.memoryWorktree?.exists),
    });
  } else {
    steps.push({ label: `Memory (${node.memoryMode})`, state: "skipped" });
  }
  steps.push({ label: "CGC seed", state: edge("cgc-seed") });
  if (node.memoryMode === "external") {
    steps.push({ label: "GrepAI clone", state: edge("grepai-clone") });
  }
  steps.push({ label: "Watchers · steady state", state: steadyState(node) });
  return steps;
}

// 5k F2/F4 — during the landing/teardown arc the right panel shows a DISPOSE sequence, not the boot checklist
// reverting items to "pending" (which read backwards).
const DISPOSE_PHASES = new Set([
  "closeout-pending",
  "integration-pending",
  "integration-blocked", // t14c — a terminal integration conflict is part of the LANDING/tear-down arc, not boot
  "carryover-pending",
  "cleanup-pending",
  "abandoned",
]);

// The single ACTIVE dispose step (the frontier), read from the SAME landing[] ref progression the canvas
// flows use (origin-feat pushed → pr merged → origin-mem-main pushed) — so the panel's "running" row and the
// canvas's cyan flow always agree. Index into the step list below. carryoverDoneAt isn't on the TS projection
// yet (a deferred 5k-render item), so the carryover step reads the origin-mem-main ref.
function disposeFrontier(node: EngineProcessNode): number {
  const ref = (k: string) => node.landing?.find((r) => r.kind === k);
  const resolved = (k: string) => {
    const r = ref(k);
    return r ? r.factState !== "planned" && r.state !== "planned" : false;
  };
  if (node.phase === "abandoned") return 6; // retire
  if (node.phase === "cleanup-pending") return 5; // cleanup · de-materialise
  if (ref("origin-mem-main")?.state === "pushed") return 4; // carryover (active)
  if (ref("pr")?.state === "merged") return 3; // pull
  if (resolved("origin-feat")) return 2; // PR (open)
  if (node.phase === "integration-pending") return 1; // push
  return 0; // closeout
}

function teardownSteps(node: EngineProcessNode): Step[] {
  const f = disposeFrontier(node);
  // t14c — a terminal integration conflict: closeout landed, then the replay onto the feat/fix source HIT a
  // conflict (all-or-nothing). The integrate/push step reads BLOCKED and the arc stops there (no auto-recovery).
  const conflict = node.phase === "integration-blocked";
  // t18 — abandon BYPASSES the whole landing: no closeout / push / PR / pull / carryover ever runs (the
  // diagnostics say "abandoned without integration — no landing"), so those read SKIPPED, not "done"; only the
  // dispose tail (cleanup · de-materialise → retire) actually happened.
  const abandoned = node.phase === "abandoned";
  const at = (i: number): StepState =>
    conflict
      ? i === 0
        ? "complete"
        : i === 1
          ? "blocked"
          : "pending"
      : abandoned
        ? i <= 4
          ? "skipped"
          : "complete"
        : f > i
          ? "complete"
          : f === i
            ? "running"
            : "pending";
  return [
    { label: "Closeout · code → ledger", state: at(0) },
    { label: "Push → origin/feat", state: at(1) },
    { label: "PR · merge", state: at(2) },
    { label: "Pull → main", state: at(3) },
    { label: "Carryover → mem-main", state: at(4) },
    { label: "Cleanup · de-materialise", state: at(5) },
    { label: "Retire branches", state: at(6) },
  ];
}

// A LIVE, fully-booted worktree (not booting, not disposing) — e.g. t12b live-sync, where origin/mem-main
// moved while the worktree keeps working. The boot checklist is all done; calling it a "Boot sequence" reads
// wrong for a running worktree, so it gets a STEADY-STATE label over the same completed checklist.
const STEADY_PHASES = new Set(["sync-needed", "running", "nominal", "completed", "live"]);

export function BootTimeline({ node }: { node: EngineProcessNode }) {
  const disposing = DISPOSE_PHASES.has(node.phase);
  const steady = !disposing && STEADY_PHASES.has(node.phase);
  const steps = disposing ? teardownSteps(node) : bootSteps(node);
  const mode = disposing ? "teardown" : steady ? "steady" : "boot";
  const label = disposing ? "Tear-down sequence" : steady ? "Steady state" : "Boot sequence";
  return (
    <div className={timeline} data-testid="boot-timeline" data-mode={mode}>
      <span className={sectionLabel}>{label}</span>
      <ol className={css({ display: "grid", gap: "0.2rem", margin: "0", padding: "0", listStyle: "none" })}>
        {steps.map((step) => (
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
