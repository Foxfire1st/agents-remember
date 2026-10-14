import { describe, expect, it } from "vitest";

import type { TaskDocNode } from "../types/projection";
import { analytics, taskDoc } from "../test/fixtures/wire";
import { masterFolderForSelection } from "./taskIdentity";

// Minimal docs: only the fields the selection reads (kind/docPath/id/repository/lifecycleId) are
// stated; the served builder fills the rest, so every override is still checked against the mirror
// instead of asserted past it.
function doc(partial: Partial<TaskDocNode>): TaskDocNode {
  return taskDoc({ repository: "repo", ...partial });
}

describe("masterFolderForSelection", () => {
  it("resolves the master folder of a selected task doc", () => {
    const docs = [doc({ kind: "subTask", id: "L5", docPath: "/tasks/repo/ops/05.json", lifecycleId: "LC-5" })];
    const folder = masterFolderForSelection(
      "taskdoc:/tasks/repo/ops/05.json",
      {},
      analytics({ taskDocuments: docs }),
    );
    expect(folder).toBe("ops");
  });
});
