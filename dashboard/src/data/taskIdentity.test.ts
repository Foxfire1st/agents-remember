import { describe, expect, it } from "vitest";

import type { TaskDocNode } from "../types/projection";
import { analytics, taskDoc } from "../test/fixtures/wire";
import { buildTaskTree, findMasterPath, masterFolderForSelection } from "./taskIdentity";

// Minimal docs: only the fields the tree builder reads (kind/docPath/id/repository/title/lifecycleId/
// masterLifecycleId) are stated; the served builder fills the rest, so every override is still
// checked against the mirror instead of asserted past it.
function doc(partial: Partial<TaskDocNode>): TaskDocNode {
  return taskDoc({ repository: "repo", ...partial });
}

describe("buildTaskTree", () => {
  it("nests a master that is itself a sub-task of another master, with leaves under each", () => {
    const docs = [
      doc({ kind: "master", id: "ops", title: "Operations", lifecycleId: "LC-ops", docPath: "/tasks/repo/ops/00.json" }),
      doc({ kind: "subTask", id: "L5", title: "Sidebar chat", docPath: "/tasks/repo/ops/05.json" }),
      // A NESTED master: its parent is the ops master (masterLifecycleId), its own folder is `engine`.
      doc({ kind: "master", id: "eng", title: "Engine Room", lifecycleId: "LC-eng", masterLifecycleId: "LC-ops", docPath: "/tasks/repo/engine/00.json" }),
      doc({ kind: "subTask", id: "E1", title: "Canvas motion", docPath: "/tasks/repo/engine/01.json" }),
    ];

    const tree = buildTaskTree(docs);

    // One root master (Operations); Engine Room is nested inside it, not a second root.
    expect(tree).toHaveLength(1);
    const ops = tree[0];
    expect(ops.master).toBe("ops");
    const opsLeaf = ops.children.find((node) => node.leafKey);
    expect(opsLeaf?.leafKey).toBe("repo/ops/L5");
    const engine = ops.children.find((node) => node.master === "engine");
    expect(engine).toBeDefined();
    expect(engine?.children[0]?.leafKey).toBe("repo/engine/E1");
  });

  it("puts a leaf with no master in its folder at the top level (orphan)", () => {
    const tree = buildTaskTree([
      doc({ kind: "subTask", id: "L5", title: "Sidebar chat", docPath: "/tasks/repo/ops/05.json" }),
    ]);
    expect(tree).toHaveLength(1);
    expect(tree[0].leafKey).toBe("repo/ops/L5");
  });
});

describe("findMasterPath", () => {
  it("returns the chain of masters down to a nested master", () => {
    const tree = buildTaskTree([
      doc({ kind: "master", id: "ops", title: "Operations", lifecycleId: "LC-ops", docPath: "/tasks/repo/ops/00.json" }),
      doc({ kind: "master", id: "eng", title: "Engine Room", lifecycleId: "LC-eng", masterLifecycleId: "LC-ops", docPath: "/tasks/repo/engine/00.json" }),
    ]);
    const path = findMasterPath(tree, "engine");
    expect(path.map((node) => node.master)).toEqual(["ops", "engine"]);
  });
});

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
