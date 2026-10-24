// The Engine Room render model (slice 5e). The server composes the enclosure-centered
// process nodes (analytics.engineProcesses) with their fact-state honesty; the client model
// presents those nodes directly. No semantics are re-derived here.

import type { EngineProcessNode } from "../../types/projection";

/** One server-composed enclosure process. */
export interface EngineProcessView {
  /**
   * Stable React key for the enclosure = the node's `worktreeGroup`. It survives the
   * fleeting→real id swap (start-progress id → contract-path id), so keying the list and the
   * future promotion morph by this makes a block-clears transition one continuous element (5f §8.3).
   */
  enclosureKey: string;
  node: EngineProcessNode;
}

/** The Engine Room's render model: enclosure process pods. */
export interface EngineRoomModel {
  /** Enclosure-centered process pods (server-composed, deterministic order). */
  processes: EngineProcessView[];
}
