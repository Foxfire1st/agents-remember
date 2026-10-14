// Preservation proof for L150-EVIDENCE-04: the extracted seeded-document and
// stale-override repair paths keep the exact HEAD callback payloads, order and
// scheduling — sprint/master/task pruning, agent/model/effort pruning, the
// loaded-options deferral, first-invalid-tier priority, and quiescence for valid
// selections. Callbacks are observed for their exact payloads, not labels.
import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { taskDocumentRefForDoc } from "../../data/taskIdentity";
import type { TaskDocumentRef } from "../../types/projection";
import { chatLeaf, chatMaster, chatSeries, chatSprint } from "../../test/fixtures/documentChat";
import type { RoleAgentChoice, RoleLaunchSelection } from "../roleLaunchModel";
import {
  repairedOverride,
  repairedSeededSelection,
  usePruneSeededSelection,
  usePruneStaleOverride,
} from "./useLauncherSelectionRepair";

const DOCS = [chatSprint, chatMaster, chatLeaf];

function refOf(doc: { repository: string; docPath: string }): TaskDocumentRef {
  const ref = taskDocumentRefForDoc(doc);
  if (!ref) throw new Error("fixture doc has no ref");
  return ref;
}

const SPRINT_REF = refOf(chatSprint);
const MASTER_REF = refOf(chatMaster);
const LEAF_REF = refOf(chatLeaf);

function managerSelection(over: Partial<RoleLaunchSelection> = {}): RoleLaunchSelection {
  return {
    role: "manager",
    sprintDocumentRef: { ...SPRINT_REF },
    masterDocumentRef: { ...MASTER_REF },
    ...over,
  };
}

const AGENTS: RoleAgentChoice[] = [
  {
    id: "codex",
    label: "Codex",
    models: [{ id: "gpt-a", label: "GPT A", efforts: [{ id: "default", label: "Normal" }] }],
  },
  { id: "other", label: "Other", models: [] },
];

describe("repairedSeededSelection payloads and order", () => {
  it("keeps a selection naming live documents (quiescence)", () => {
    expect(
      repairedSeededSelection(DOCS, chatSeries, { ...managerSelection(), taskDocumentRef: { ...LEAF_REF } }),
    ).toBeUndefined();
    expect(repairedSeededSelection(DOCS, chatSeries, managerSelection())).toBeUndefined();
  });

  it("prunes a vanished sprint back to the role alone", () => {
    expect(
      repairedSeededSelection(
        DOCS,
        chatSeries,
        managerSelection({ sprintDocumentRef: { repository: "repo", path: "gone/task.json" } }),
      ),
    ).toEqual({ role: "manager" });
  });

  it("prunes a vanished master back to its sprint", () => {
    expect(
      repairedSeededSelection(
        DOCS,
        chatSeries,
        managerSelection({ masterDocumentRef: { repository: "repo", path: "gone/task.json" } }),
      ),
    ).toEqual({ role: "manager", sprintDocumentRef: SPRINT_REF });
  });

  it("prunes a vanished task back to its master", () => {
    expect(
      repairedSeededSelection(
        DOCS,
        chatSeries,
        { ...managerSelection(), taskDocumentRef: { repository: "repo", path: "gone/leaf.json" } },
      ),
    ).toEqual({ role: "manager", sprintDocumentRef: SPRINT_REF, masterDocumentRef: MASTER_REF });
  });

  it("repairs the first invalid tier when several levels are stale", () => {
    expect(
      repairedSeededSelection(
        DOCS,
        chatSeries,
        managerSelection({
          sprintDocumentRef: { repository: "repo", path: "gone/task.json" },
          masterDocumentRef: { repository: "repo", path: "gone/master.json" },
        }),
      ),
    ).toEqual({ role: "manager" });
  });
});

describe("repairedOverride payloads", () => {
  const MODELS = AGENTS[0].models;
  const EFFORTS = MODELS[0].efforts ?? [];

  it("keeps an offered override (quiescence)", () => {
    const selection = {
      ...managerSelection(),
      agentOverride: { agentId: "codex", modelId: "gpt-a", effortId: "default" },
    };
    expect(repairedOverride(selection, AGENTS, MODELS, EFFORTS)).toBeUndefined();
  });

  it("prunes an unoffered agent back to no override over the document scope", () => {
    const selection = { ...managerSelection(), agentOverride: { agentId: "gone", modelId: "gpt-a" } };
    expect(repairedOverride(selection, AGENTS, MODELS, EFFORTS)).toEqual({
      role: "manager",
      sprintDocumentRef: SPRINT_REF,
      masterDocumentRef: MASTER_REF,
      agentOverride: undefined,
    });
  });

  it("prunes an unoffered model back to the agent alone", () => {
    const selection = { ...managerSelection(), agentOverride: { agentId: "codex", modelId: "gone" } };
    expect(repairedOverride(selection, AGENTS, MODELS, EFFORTS)).toEqual({
      role: "manager",
      sprintDocumentRef: SPRINT_REF,
      masterDocumentRef: MASTER_REF,
      agentOverride: { agentId: "codex" },
    });
  });

  it("prunes an unoffered effort back to the agent and model", () => {
    const selection = {
      ...managerSelection(),
      agentOverride: { agentId: "codex", modelId: "gpt-a", effortId: "gone" },
    };
    expect(repairedOverride(selection, AGENTS, MODELS, EFFORTS)).toEqual({
      role: "manager",
      sprintDocumentRef: SPRINT_REF,
      masterDocumentRef: MASTER_REF,
      agentOverride: { agentId: "codex", modelId: "gpt-a" },
    });
  });

  it("repairs the first invalid override tier", () => {
    const selection = {
      ...managerSelection(),
      agentOverride: { agentId: "gone", modelId: "gone" },
    };
    expect(repairedOverride(selection, AGENTS, MODELS, EFFORTS)).toEqual({
      role: "manager",
      sprintDocumentRef: SPRINT_REF,
      masterDocumentRef: MASTER_REF,
      agentOverride: undefined,
    });
  });
});

describe("repair scheduling: deferral and single callback", () => {
  const MODELS = AGENTS[0].models;
  const EFFORTS = MODELS[0].efforts ?? [];

  it("defers the stale-override repair until the options are ready", () => {
    const onSelectionChange = vi.fn();
    const selection = { ...managerSelection(), agentOverride: { agentId: "gone" } };
    const { rerender } = renderHook(
      (props: { ready: boolean }) =>
        usePruneStaleOverride({
          optionsReady: props.ready,
          agents: AGENTS,
          models: MODELS,
          efforts: EFFORTS,
          selection,
          onSelectionChange,
        }),
      { initialProps: { ready: false } },
    );
    expect(onSelectionChange).not.toHaveBeenCalled();
    rerender({ ready: true });
    expect(onSelectionChange).toHaveBeenCalledTimes(1);
    expect(onSelectionChange).toHaveBeenCalledWith({
      role: "manager",
      sprintDocumentRef: SPRINT_REF,
      masterDocumentRef: MASTER_REF,
      agentOverride: undefined,
    });
  });

  it("leaves a valid selection alone once the options are ready", () => {
    const onSelectionChange = vi.fn();
    const selection = {
      ...managerSelection(),
      agentOverride: { agentId: "codex", modelId: "gpt-a", effortId: "default" },
    };
    const { rerender } = renderHook(
      (props: { ready: boolean }) =>
        usePruneStaleOverride({
          optionsReady: props.ready,
          agents: AGENTS,
          models: MODELS,
          efforts: EFFORTS,
          selection,
          onSelectionChange,
        }),
      { initialProps: { ready: false } },
    );
    rerender({ ready: true });
    rerender({ ready: true });
    expect(onSelectionChange).not.toHaveBeenCalled();
  });

  it("repairs a stale seeded selection as soon as the documents arrive", () => {
    const onSelectionChange = vi.fn();
    renderHook(() =>
      usePruneSeededSelection({
        taskDocuments: DOCS,
        series: chatSeries,
        role: "manager",
        sprintDocumentRef: { repository: "repo", path: "gone/task.json" },
        masterDocumentRef: undefined,
        taskDocumentRef: undefined,
        onSelectionChange,
      }));
    expect(onSelectionChange).toHaveBeenCalledTimes(1);
    expect(onSelectionChange).toHaveBeenCalledWith({ role: "manager" });
  });

  it("leaves a live seeded selection alone", () => {
    const onSelectionChange = vi.fn();
    renderHook(() =>
      usePruneSeededSelection({
        taskDocuments: DOCS,
        series: chatSeries,
        role: "manager",
        sprintDocumentRef: { ...SPRINT_REF },
        masterDocumentRef: { ...MASTER_REF },
        taskDocumentRef: undefined,
        onSelectionChange,
      }));
    expect(onSelectionChange).not.toHaveBeenCalled();
  });
});
