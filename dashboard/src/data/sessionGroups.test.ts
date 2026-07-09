import { describe, expect, it } from "vitest";

import type { EnclosureNode, TaskDocNode } from "../types/projection";
import { groupSessions } from "./sessionGroups";
import type { OpenSession } from "./sessions";

// The G1 command-tree derivation (L14): membership is claim + spawn-role provenance, the deck
// exists only when an orchestration task exists (D3), landed work rolls into one archive, and a
// flat run derives zero groups so the sidebar renders exactly as before.

function doc(over: Partial<TaskDocNode> & Pick<TaskDocNode, "title" | "docPath">): TaskDocNode {
  return {
    id: "doc",
    repository: "agents-remember",
    status: "inProgress",
    kind: "subTask",
    stepsDone: 0,
    stepsTotal: 0,
    steps: [],
    objective: "",
    requirements: [],
    codeExamples: [],
    decisions: [],
    openQuestions: [],
    references: [],
    subTasks: [],
    sections: [],
    ...over,
  };
}

function enclosure(
  over: Partial<EnclosureNode> & Pick<EnclosureNode, "enclosure" | "leafId" | "taskRoot">,
): EnclosureNode {
  return {
    enclosureId: over.enclosure,
    taskId: "T",
    taskName: "t",
    repoName: "agents-remember",
    lifecycleId: "",
    worktreeGroup: "/worktrees/g",
    humanReviewStatus: "pending-review",
    closeoutStatus: "not-started",
    integrationStatus: "not-started",
    cleanup: "pending",
    codeWorktreeExists: true,
    memoryWorktreeExists: true,
    actions: [],
    ...over,
  };
}

function session(over: Partial<OpenSession> & Pick<OpenSession, "id">): OpenSession {
  return { label: over.id, status: "running", ...over };
}

const SPRINT = doc({
  id: "SPRINT-02",
  kind: "master",
  title: "SPRINT 02 · management-repo rollout",
  docPath: "/tasks/agents-remember/sprint-02/task.json",
  orchestrates: ["260706_management-repo"],
  createdAt: "2026-07-01T09:00:00+00:00",
});
const COMMANDED_MASTER = doc({
  id: "260706",
  kind: "master",
  title: "260706 management-repo",
  docPath: "/tasks/agents-remember/260706_management-repo/task.json",
  createdAt: "2026-07-02T09:00:00+00:00",
});
const FREE_MASTER = doc({
  id: "260707",
  kind: "master",
  title: "260707 settings page",
  docPath: "/tasks/agents-remember/260707_settings-page/task.json",
  createdAt: "2026-07-03T09:00:00+00:00",
});
const LIVE_ENCLOSURE = enclosure({
  enclosure: "/contracts/l1",
  leafId: "260706-l1",
  taskRoot: "/tasks/agents-remember/260706_management-repo",
});
const FREE_ENCLOSURE = enclosure({
  enclosure: "/contracts/s0",
  leafId: "260707-l0",
  taskRoot: "/tasks/agents-remember/260707_settings-page",
});

describe("groupSessions (L14 G1 command tree)", () => {
  it("groups command-role provenance onto the deck together with the orchestration-claiming chat", () => {
    const grouped = groupSessions({
      sessions: [
        session({ id: "architect", leafKey: "agents-remember/sprint-02/SPRINT-02" }),
        session({ id: "orch", spawnRole: "orchestrator" }),
        session({ id: "strat", spawnRole: "strategist" }),
        session({ id: "mgr", spawnRole: "manager", leafKey: "agents-remember/260706_management-repo/260706" }),
        session({ id: "worker", spawnRole: "worker", leafKey: "agents-remember/260706_management-repo/260706-L1" }),
      ],
      taskDocuments: [SPRINT, COMMANDED_MASTER],
      enclosures: [LIVE_ENCLOSURE],
    });

    const deck = grouped.groups.find((group) => group.kind === "command");
    expect(deck).toBeTruthy();
    expect(deck?.label).toBe("SPRINT 02 · management-repo rollout · command deck");
    expect(deck?.tier).toBe("orchestration");
    // The developer-facing architect chat (the orchestration task's own leaf claim) sits on the
    // deck with the spawned backend command seats; the worker does NOT (role provenance is the gate).
    expect(deck?.sessions.map((member) => member.id)).toEqual(["architect", "orch", "strat", "mgr"]);
    expect(deck?.countLabel).toBe("4 chats · 4 live");

    const master = grouped.groups.find((group) => group.key === "master:260706_management-repo");
    expect(master?.sessions.map((member) => member.id)).toEqual(["worker"]);
    expect(master?.tier).toBe("management"); // commanded by the sprint → purple badge
    expect(master?.nested).toBe(true); // + one indent step
  });

  it("groups by leaf claim under the master, unmarked and un-nested when no orchestration commands it", () => {
    const grouped = groupSessions({
      sessions: [
        session({ id: "builder", spawnRole: "worker", leafKey: "agents-remember/260707_settings-page/260707-L0" }),
      ],
      taskDocuments: [FREE_MASTER],
      enclosures: [FREE_ENCLOSURE],
    });
    expect(grouped.groups).toHaveLength(1);
    const master = grouped.groups[0];
    expect(master.key).toBe("master:260707_settings-page");
    expect(master.label).toBe("260707 settings page");
    expect(master.tier).toBeUndefined();
    expect(master.nested).toBe(false);
    expect(grouped.ungrouped).toEqual([]);
  });

  it("keeps command-role sessions out of the deck when NO orchestration task exists (D3 flat run)", () => {
    const grouped = groupSessions({
      sessions: [
        session({ id: "mgr", spawnRole: "manager" }),
        session({ id: "plain" }),
      ],
      taskDocuments: [FREE_MASTER],
      enclosures: [],
    });
    expect(grouped.groups).toEqual([]); // no deck, no master claims → zero groups
    expect(grouped.ungrouped.map((member) => member.id)).toEqual(["mgr", "plain"]);
  });

  it("rolls only landed rows into the archive group", () => {
    const landedEnclosure = enclosure({
      enclosure: "/contracts/landed",
      leafId: "260706-l0",
      taskRoot: "/tasks/agents-remember/260706_management-repo",
      codeWorktreeExists: false,
      memoryWorktreeExists: false,
    });
    const grouped = groupSessions({
      sessions: [
        session({ id: "landed-chat", leafKey: "agents-remember/260706_management-repo/260706-L0", status: "landed" }),
        session({ id: "legacy-exited", leafKey: "agents-remember/260799_unknown/260799-L9", status: "exited" }),
        session({ id: "active-absent", leafKey: "agents-remember/260799_unknown/260799-L10" }),
      ],
      taskDocuments: [COMMANDED_MASTER],
      enclosures: [landedEnclosure],
    });
    expect(grouped.groups).toHaveLength(1);
    const archive = grouped.groups[0];
    expect(archive.kind).toBe("landed");
    expect(archive.label).toBe("landed archive");
    expect(archive.tier).toBeUndefined(); // unmarked
    expect(archive.defaultCollapsed).toBe(true);
    expect(archive.sessions.map((member) => member.id)).toEqual(["landed-chat"]);
    expect(archive.countLabel).toBe("1 chat · archived");
    expect(grouped.ungrouped.map((member) => member.id)).toEqual(["legacy-exited", "active-absent"]);
  });

  it("matches enclosure leaf ids case-insensitively (doc ids are uppercase, enclosure ids slugified)", () => {
    const grouped = groupSessions({
      sessions: [session({ id: "w", leafKey: "agents-remember/260706_management-repo/260706-L1" })],
      taskDocuments: [COMMANDED_MASTER],
      enclosures: [LIVE_ENCLOSURE], // leafId "260706-l1"
    });
    expect(grouped.groups[0]?.key).toBe("master:260706_management-repo");
  });

  it("reads at a glance at 30-chat scale: deck + per-master groups + one archive", () => {
    const sessions: OpenSession[] = [
      session({ id: "architect", spawnRole: "architect" }),
      session({ id: "strategist", spawnRole: "strategist", status: "exited" }),
      session({ id: "m1", spawnRole: "manager" }),
      session({ id: "m2", spawnRole: "manager", status: "exited" }),
    ];
    for (let i = 0; i < 6; i += 1) {
      sessions.push(
        session({
          id: `a${i}`,
          spawnRole: i % 2 ? "reviewer" : "worker",
          leafKey: "agents-remember/260706_management-repo/260706-L1",
          status: i < 2 ? "running" : "exited",
        }),
      );
    }
    for (let i = 0; i < 7; i += 1) {
      sessions.push(
        session({
          id: `b${i}`,
          spawnRole: "worker",
          leafKey: "agents-remember/260707_settings-page/260707-L0",
          status: i < 1 ? "running" : "exited",
        }),
      );
    }
    for (let i = 0; i < 13; i += 1) {
      sessions.push(
        session({ id: `old${i}`, leafKey: `agents-remember/260650_landed/260650-L${i}`, status: "landed" }),
      );
    }
    expect(sessions).toHaveLength(30);

    const grouped = groupSessions({
      sessions,
      taskDocuments: [
        SPRINT,
        COMMANDED_MASTER,
        FREE_MASTER,
      ],
      enclosures: [LIVE_ENCLOSURE, FREE_ENCLOSURE],
    });

    expect(grouped.groups.map((group) => group.key)).toEqual([
      "command",
      "master:260706_management-repo",
      "master:260707_settings-page",
      "landed",
    ]);
    const [deck, masterA, masterB, archive] = grouped.groups;
    expect(deck.sessions).toHaveLength(4);
    expect(deck.countLabel).toBe("4 chats · 2 live");
    expect(masterA.sessions).toHaveLength(6);
    expect(masterA.countLabel).toBe("6 chats · 2 live");
    expect(masterB.sessions).toHaveLength(7);
    expect(masterB.countLabel).toBe("7 chats · 1 live");
    expect(archive.sessions).toHaveLength(13);
    expect(archive.countLabel).toBe("13 chats · archived");
    expect(grouped.ungrouped).toEqual([]);
  });
});
