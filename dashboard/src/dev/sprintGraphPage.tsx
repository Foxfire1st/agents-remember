import { useEffect } from "react";

import { dashboardStore } from "../data/store";
import { DetailPanel } from "../panels/detail-panel/DetailPanel";
import { analytics, projection } from "../test/fixtures/wire";
import { SPRINT_GRAPH_QUEUE, SPRINT_GRAPH_TASK_DOC } from "./sprintGraphFixture";

// A deterministic mounted-UI surface for the sprint execution graph (L12-R7 evidence): the real
// sprint page (the Operations DetailPanel) opened against seeded sprint-graph data, so a reviewer
// can screenshot the mounted wave-grid view and its closeout queue at one URL.
const SPRINT_GRAPH_PROJECTION = projection({
  analytics: analytics({ taskDocuments: [SPRINT_GRAPH_TASK_DOC] }),
  closeoutQueues: [SPRINT_GRAPH_QUEUE],
});

export function SprintGraphPage() {
  useEffect(() => {
    dashboardStore.getState().applySnapshot(SPRINT_GRAPH_PROJECTION);
  }, []);
  return <DetailPanel selectedId="taskdoc:/tasks/agents-remember/sprint-graph/task.json" />;
}