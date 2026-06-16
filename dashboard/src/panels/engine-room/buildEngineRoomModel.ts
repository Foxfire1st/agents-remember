import { groupEngines } from "../../data/selectors";
import type {
  EngineProcessNode,
  LifecycleProjection,
  ProviderNode,
} from "../../types/projection";
import type { EngineRoomModel } from "./engineRoomTypes";

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
  const processes = engineProcesses.map((node) => ({
    enclosureKey: node.worktreeGroup,
    node,
    lifecycle: node.lifecycleId ? lifecycleById.get(node.lifecycleId) : undefined,
  }));

  const usesFallback = engineProcesses.length === 0 && worktreeStacks.length > 0;
  return {
    processes,
    workspaceEngines,
    fallbackStacks: usesFallback ? worktreeStacks : [],
    usesFallback,
  };
}
