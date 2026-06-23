// The Engine Room render model (slice 5e). The server composes the enclosure-centered
// process nodes (analytics.engineProcesses) with their fact-state honesty; the client model
// only joins each to its live lifecycle, lifts the shared workspace stack, and keeps a
// legacy fallback. No semantics are re-derived here.

import type { EngineStack } from "../../data/selectors";
import type {
  EngineProcessNode,
  GateNode,
  LifecycleProjection,
  ProviderNode,
} from "../../types/projection";

/** One enclosure process plus the live session (if any) driving it. */
export interface EngineProcessView {
  /**
   * Stable React key for the enclosure = the node's `worktreeGroup`. It survives the
   * fleeting→real id swap (start-progress id → contract-path id), so keying the list and the
   * future promotion morph by this makes a block-clears transition one continuous element (5f §8.3).
   */
  enclosureKey: string;
  node: EngineProcessNode;
  lifecycle?: LifecycleProjection;
  gate?: GateNode;
}

/** The Engine Room's render model: enclosure process pods + the shared workspace stack. */
export interface EngineRoomModel {
  /** Enclosure-centered process pods (server-composed, deterministic order). */
  processes: EngineProcessView[];
  /** The official/main shared provider stack (workspace-scoped engines). */
  workspaceEngines: ProviderNode[];
  /** Legacy per-worktree provider stacks, used only when no engineProcesses are present. */
  fallbackStacks: EngineStack[];
  /** True when the projection predates the engineProcesses surface (groupEngines fallback). */
  usesFallback: boolean;
}
