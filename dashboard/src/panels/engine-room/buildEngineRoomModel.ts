import type {
  EngineProcessNode,
  LifecycleProjection,
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
 * inference: it joins each process to its live lifecycle. Provider state left the surface
 * (MIK-R112): no workspace stack, no legacy provider fallback. Takes flat arrays (like
 * `buildTopology`) so it stays React-free and unit-testable. No clock, no git, no safety inference.
 */
export function buildEngineRoomModel(
  engineProcesses: EngineProcessNode[],
  lifecycles: LifecycleProjection[],
): EngineRoomModel {
  const lifecycleById = new Map(lifecycles.map((lifecycle) => [lifecycle.id, lifecycle]));
  const processes = engineProcesses
    .map((node) => {
      const lifecycle = node.lifecycleId ? lifecycleById.get(node.lifecycleId) : undefined;
      return {
        enclosureKey: node.worktreeGroup,
        node,
        lifecycle,
      };
    })
    .sort((a, b) => {
      const phase = (PHASE_ORDER[a.node.phase] ?? 99) - (PHASE_ORDER[b.node.phase] ?? 99);
      if (phase !== 0) return phase;
      return (a.node.leafId || a.node.taskName).localeCompare(b.node.leafId || b.node.taskName);
    });

  return {
    processes,
  };
}
