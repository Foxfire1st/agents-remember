import { groupEngines } from "../../data/selectors";
import type {
  EngineProcessNode,
  LifecycleProjection,
  ProviderNode,
} from "../../types/projection";
import type { EngineRoomModel } from "./engineRoomTypes";

const PHASE_ORDER: Record<string, number> = {
  "provider-setup": 0,
  "worktree-started": 1,
  "sync-needed": 2,
  "closeout-pending": 3,
  "commit-approval-pending": 4,
  "integration-blocked": 5,
  "integration-pending": 6,
  "carryover-pending": 7,
  "cleanup-pending": 8,
  completed: 9,
  abandoned: 10,
};

/**
 * Pure: the resolved collections -> the Engine Room render model (slice 5e).
 *
 * The server already composes the enclosure-centered process nodes (`analytics.engineProcesses`)
 * with their observed/derived/planned/missing fact-state honesty, so this client seam does no
 * inference: it joins each process to its live lifecycle, lifts the shared workspace
 * (official/main) stack, and falls back to the legacy `groupEngines` view for projections that
 * carry worktree providers but no process surface. Takes flat arrays (like `buildTopology`) so it
 * stays React-free and unit-testable. No clock, no git, no safety inference.
 */
export function buildEngineRoomModel(
  engineProcesses: EngineProcessNode[],
  providers: ProviderNode[],
  lifecycles: LifecycleProjection[],
): EngineRoomModel {
  const stacks = groupEngines(providers);
  const workspaceEngines = stacks.find((stack) => stack.scope === "workspace")?.engines ?? [];
  const worktreeStacks = stacks.filter((stack) => stack.scope === "worktree");

  const lifecycleById = new Map(lifecycles.map((lifecycle) => [lifecycle.id, lifecycle]));
  const processes = engineProcesses
    .map((node) => {
      const lifecycle = node.lifecycleId ? lifecycleById.get(node.lifecycleId) : undefined;
      return {
        enclosureKey: node.worktreeGroup,
        node,
        lifecycle,
        gate: lifecycle?.gate,
      };
    })
    .sort((a, b) => {
      const phase = (PHASE_ORDER[a.node.phase] ?? 99) - (PHASE_ORDER[b.node.phase] ?? 99);
      if (phase !== 0) return phase;
      return (a.node.leafId || a.node.taskName).localeCompare(b.node.leafId || b.node.taskName);
    });

  const usesFallback = engineProcesses.length === 0 && worktreeStacks.length > 0;
  return {
    processes,
    workspaceEngines,
    fallbackStacks: usesFallback ? worktreeStacks : [],
    usesFallback,
  };
}
