import type { CloseoutQueueNode } from "../types/projection";
import { taskDoc } from "../test/fixtures/wire";

const MASTER_A = { repository: "agents-remember", path: "master-a/task.json" };
const ATOMIC_F = { repository: "agents-remember", path: "atomic-f/task.json" };

// The seeded sprint for the /dev/sprint-graph mounted-UI evidence surface (L12-R7): a segmented
// organizational master with an early and a late segment around an atomic lump master, edges with
// recorded reasons, and per-node frontier states -- the zero-edge and segmented-master scenarios
// are covered by the component tests; this fixture drives the screenshot-able sprint page.
export const SPRINT_GRAPH_TASK_DOC = taskDoc({
  id: "SPRINT-1",
  kind: "master",
  title: "Dependency-aware sprint execution",
  docPath: "/tasks/agents-remember/sprint-graph/task.json",
  orchestrates: ["master-a", "atomic-f"],
  executionGraphView: {
    nodes: [
      {
        nodeId: "agents-remember/master-a/task.json#seg1",
        kind: "segment",
        masterRef: MASTER_A,
        masterTitle: "Org Master One",
        leafIds: ["OM1-L1", "OM1-L2"],
        leafTitles: ["Shared framework lands first", "Control bridge and contract"],
        waveIndex: 1,
        frontierState: "in-flight",
        executionNature: "organizational",
        predecessors: [],
      },
      {
        nodeId: "agents-remember/atomic-f/task.json",
        kind: "lump",
        masterRef: ATOMIC_F,
        masterTitle: "Atomic F",
        leafIds: [],
        leafTitles: [],
        waveIndex: 2,
        frontierState: "waiting",
        executionNature: "atomic",
        predecessors: [
          {
            predecessorRef: MASTER_A,
            predecessorTitle: "Org Master One",
            reason: "the shared framework must land first",
          },
        ],
      },
      {
        nodeId: "agents-remember/master-a/task.json#seg2",
        kind: "segment",
        masterRef: MASTER_A,
        masterTitle: "Org Master One",
        leafIds: ["OM1-L3"],
        leafTitles: ["Late wave segment"],
        waveIndex: 3,
        frontierState: "ready",
        executionNature: "organizational",
        predecessors: [
          {
            predecessorRef: ATOMIC_F,
            predecessorTitle: "Atomic F",
            reason: "the atomic block gates the late segment",
          },
        ],
      },
    ],
  },
});

export const SPRINT_GRAPH_QUEUE: CloseoutQueueNode = {
  sprintRef: { repository: "agents-remember", path: "sprint-graph/task.json" },
  revision: 2,
  serviceCondition: "valid-built",
  sourceClassification: "active",
  sourceFingerprint: "ab".repeat(32),
  sourceProblems: [],
  members: [
    {
      generationId: "cd".repeat(32),
      taskDocumentRef: { repository: "agents-remember", path: "atomic-f/task.json" },
      owningMaster: ATOMIC_F,
      classification: "waiting",
      priority: "high",
      order: 0,
      reasons: ["atomic-series-lane-owned-by: agents-remember/sprint-graph/atomic-f/task.json"],
    },
  ],
};
