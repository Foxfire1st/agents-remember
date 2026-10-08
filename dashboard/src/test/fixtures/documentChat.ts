import type { SeriesNode } from "../../types/projection";
import { taskDoc } from "./wire";

export const chatSprint = taskDoc({ repository: "repo", docPath: "/tasks/repo/sprint/task.json", kind: "master", orchestrates: ["master"] });
export const chatMaster = taskDoc({ repository: "repo", docPath: "/tasks/repo/master/task.json", kind: "master", orchestrates: [] });
export const chatLeaf = taskDoc({ repository: "repo", docPath: "/tasks/repo/master/leaf.json", kind: "subTask", orchestrates: [] });
export const chatSeries: SeriesNode[] = [{
  repository: "repo", docPath: "/tasks/repo/master/task.json", createdAt: "2026-01-01", seriesId: "master", title: "Master", status: "inProgress",
  decisions: [], discardedCount: 0, discardedSubTasks: [], doneCount: 0, objective: "", sections: [], seriesTokenTotal: 0, totalCount: 1,
  subTasks: [{ file: "leaf.json", number: "1", name: "Leaf", scope: "", status: "inProgress" }],
}];
