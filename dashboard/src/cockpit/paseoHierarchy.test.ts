import { afterEach, describe, expect, it, vi } from "vitest";
import { installBridge, type BridgeClient } from "../../../mcp/src/agents_remember/package_data/paseo_plugin/client/bridge";
import { startClientPart } from "../../../mcp/src/agents_remember/package_data/paseo_plugin/client/start";
import type { NativeAgent, NativeProject } from "../../../mcp/src/agents_remember/package_data/paseo_plugin/client/hierarchy";
import type { PaseoHierarchySnapshot } from "./paseoFrameModel";
import { DASHBOARD, EMBED, newTab, pageLoad } from "../test/paseoPluginPage";

const stops: Array<() => void> = [];
afterEach(() => { for (const stop of stops.splice(0)) stop(); document.body.innerHTML = ""; });
const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };
const page = <T,>(entries: T[], nextCursor: string | null = null) => ({ entries, pageInfo: { hasMore: nextCursor !== null, nextCursor } });
const nativeState = { provider: "pi", status: "idle" as const, pendingPermissions: [] };

function stream<T>(initial: T) {
  type Observer = { snapshot(value: T): void; update(message: { type: string; payload: unknown }): void };
  let listener: Observer | null = null;
  let current = initial;
  return {
    subscription: {
      subscribe(observer: Observer) { listener = observer; observer.snapshot(current); return () => { listener = null; }; },
      release: vi.fn(async () => {}),
    },
    update: (type: string, payload: unknown) => listener?.update({ type, payload }),
    snapshot: (value: T) => { current = value; listener?.snapshot(value); },
  };
}

function host() {
  const root = { ...nativeState, id: "parent", title: "Architect", workspaceId: "projects", labels: { "ar.role": "architect" } };
  const child = { ...nativeState, id: "child", title: "Worker", workspaceId: "task", labels: {
    "paseo.parent-agent-id": "parent", "ar.role": "worker", "ar.request-id": "request-1",
    "ar.sprint-ref": "app/sprint/task.json", "ar.master-ref": "app/master/task.json", "ar.task-ref": "app/master/01_leaf.json",
    "ar.qualification-fixture": "true", "unrelated-private-value": "never exported",
  } };
  const initialAgents = page([{ agent: root }, { agent: child }]);
  const initialWorkspaces = page([{ id: "projects", projectId: "p0", name: "Projects" }, { id: "task", projectId: "p1", name: "Task" }]);
  const agentStream = stream<Awaited<ReturnType<BridgeClient["paseo"]["agents"]["list"]>>>(initialAgents), workspaceStream = stream<Awaited<ReturnType<BridgeClient["paseo"]["workspaces"]["list"]>>>(initialWorkspaces);
  let projectListener!: Parameters<BridgeClient["paseo"]["projects"]["subscribe"]>[0];
  const projectStop = vi.fn();
  const references = new Map<string, Partial<NativeAgent> & { workspaceId: string }>([["parent", root], ["archived", { id: "archived", workspaceId: "projects", archivedAt: "2026-10-03" }]]);
  const pills = new Map<string, Parameters<BridgeClient["addComposerPill"]>[0]>();
  const removed: string[] = [];
  const opened: Parameters<BridgeClient["openScreen"]>[0][] = [];
  const client: BridgeClient = {
    openScreen: (input) => opened.push(input),
    addComposerPill: vi.fn((input) => {
      const state = { ...input };
      pills.set(input.agentId, state);
      return { update(patch: Partial<typeof input.button>) { state.button = { ...state.button, ...patch }; }, remove() { removed.push(input.agentId); pills.delete(input.agentId); } };
    }),
    paseo: {
      projects: {
        list: vi.fn(async () => ({ projects: [{ projectId: "p0", projectDisplayName: "Projects" }, { projectId: "p1", projectDisplayName: "Master" }] })),
        subscribe(handler) { projectListener = handler; return projectStop; },
      },
      agents: {
        list: vi.fn(async (options: Parameters<BridgeClient["paseo"]["agents"]["list"]>[0]) => {
          if (options.page.limit > 200) throw new Error("Paseo SDK page.limit maximum is 200");
          return { ...initialAgents, subscription: agentStream.subscription };
        }),
        ref(id) {
          const handle = { archivedAt: null as unknown, workspaceId: null as string | null, refresh: async () => {
            const found = references.get(id);
            if (!found) return null;
            handle.archivedAt = found.archivedAt ?? null; handle.workspaceId = found.workspaceId;
            return { agent: found };
          } };
          return handle;
        },
      },
      workspaces: {
        list: vi.fn(async (options: Parameters<BridgeClient["paseo"]["workspaces"]["list"]>[0]) => {
          if (options.page.limit > 200) throw new Error("Paseo SDK page.limit maximum is 200");
          return { ...initialWorkspaces, subscription: workspaceStream.subscription };
        }),
        ref: (id) => ({ refresh: async () => ({ id }) }),
      },
    },
  };
  return { client, root, child, initialAgents, initialWorkspaces, agentStream, workspaceStream, pills, removed, opened, projectStop, project: (change: Parameters<typeof projectListener>[0]) => projectListener(change) };
}

function connect(fixture: ReturnType<typeof host>) {
  const load = pageLoad(newTab());
  stops.push(installBridge(fixture.client, load.page, DASHBOARD));
  return load;
}
const hierarchy = (load: ReturnType<typeof pageLoad>) => load.posted.filter((p) => p.data.type === "hierarchy").at(-1)?.data as unknown as PaseoHierarchySnapshot;

function visibleChats(ids: string[], selected: string[]) {
  document.body.innerHTML = ids.map((id) => `<button data-testid="workspace-tab-agent_${id}" aria-selected="${selected.includes(id)}"></button>`).join("");
  for (const node of document.querySelectorAll("button")) Object.assign(node, { checkVisibility: () => true, getBoundingClientRect: () => ({ width: 160, height: 28 }) });
}

describe("trusted host hierarchy and agent-specific parent actions", () => {
  it("includes every SDK page, canonical labels and actual host membership, then applies live metadata changes", async () => {
    const fixture = host();
    const first = { ...fixture.initialAgents, pageInfo: { hasMore: true, nextCursor: "page-2" }, subscription: fixture.agentStream.subscription };
    fixture.agentStream.snapshot(first);
    vi.mocked(fixture.client.paseo.agents.list).mockImplementation(async (options) => {
      if (options.page.limit > 200) throw new Error("Paseo SDK page.limit maximum is 200");
      return options.page.cursor ? page([{ agent: { ...nativeState, id: "sibling", workspaceId: "task", labels: {} } }]) : first;
    });
    const load = connect(fixture);
    await flush();
    expect(hierarchy(load).agents.map((a) => a.agentId)).toEqual(["child", "parent", "sibling"]);
    expect(hierarchy(load).agents[0]).toMatchObject({
      provider: "pi", status: "idle", pendingPermissionCount: 0,
      requiresAttention: false, attentionReason: null, providerUnavailable: false,
    });
    expect(hierarchy(load).agents[0].labels).toEqual({
      "ar.role": "worker", "ar.request-id": "request-1", "ar.sprint-ref": "app/sprint/task.json",
      "ar.master-ref": "app/master/task.json", "ar.task-ref": "app/master/01_leaf.json",
      "ar.qualification-fixture": "true", "paseo.parent-agent-id": "parent",
    });
    expect(hierarchy(load).workspaces[1]).toMatchObject({ workspaceId: "task", projectId: "p1" });
    fixture.project({ kind: "upsert", project: { projectId: "p1", projectDisplayName: "Master", projectCustomName: "Renamed" } });
    fixture.agentStream.update("agent_update", { kind: "upsert", agent: { ...fixture.child, title: "Updated worker" } });
    expect(hierarchy(load).projects[1].name).toBe("Renamed");
    expect(hierarchy(load).agents[0].name).toBe("Updated worker");
    fixture.agentStream.update("agent_update", { kind: "upsert", agent: {
      ...fixture.child, provider: "hermes", status: "running", pendingPermissions: [{ id: "permission-1" }],
      requiresAttention: true, attentionReason: "permission", providerUnavailable: true,
    } });
    expect(hierarchy(load).agents[0]).toMatchObject({
      provider: "hermes", status: "running", pendingPermissionCount: 1,
      requiresAttention: true, attentionReason: "permission", providerUnavailable: true,
    });
    fixture.agentStream.update("agent_update", { kind: "upsert", agent: fixture.child });
    expect(hierarchy(load).agents[0]).toMatchObject({
      provider: "pi", status: "idle", pendingPermissionCount: 0,
      requiresAttention: false, attentionReason: null, providerUnavailable: false,
    });
    expect(load.posted.every((p) => p.targetOrigin === DASHBOARD && p.data.source === "ar-plugin")).toBe(true);
  });

  it("checks current visible caller and parent relation, including deferred navigation, unavailable parents and root transitions", async () => {
    visibleChats(["child"], ["child"]);
    const fixture = host(), load = connect(fixture);
    await flush();
    expect(fixture.pills.has("parent")).toBe(false);
    await fixture.pills.get("child")!.button.behavior.onPress();
    expect(fixture.opened.at(-1)!.params!.agentId).toBe("parent");
    for (const [parentId, code] of [["archived", "agent-archived"], ["missing", "agent-not-found"]]) {
      fixture.agentStream.update("agent_update", { kind: "upsert", agent: { ...fixture.child, labels: { ...fixture.child.labels, "paseo.parent-agent-id": parentId } } });
      await fixture.pills.get("child")!.button.behavior.onPress();
      expect(load.posted.at(-1)?.data).toMatchObject({ type: "navigation-error", context: "parent", sourceAgentId: "child", targetAgentId: parentId, code });
      expect(fixture.opened).toHaveLength(1);
    }
    fixture.agentStream.update("agent_update", { kind: "upsert", agent: { ...fixture.child, labels: { "ar.role": "architect" } } });
    expect(fixture.pills.has("child")).toBe(false);

    fixture.agentStream.update("agent_update", { kind: "upsert", agent: fixture.child });
    fixture.agentStream.update("agent_update", { kind: "upsert", agent: { ...fixture.child, id: "child2" } });
    visibleChats(["child", "child2"], ["child"]);
    await flush();
    const ref = fixture.client.paseo.agents.ref;
    let completeParent: (value: unknown) => void = () => {};
    fixture.client.paseo.agents.ref = (id) => id === "parent" ? {
      archivedAt: null, workspaceId: "projects", refresh: () => new Promise((resolve) => { completeParent = resolve; }),
    } : ref(id);
    const before = fixture.opened.length;
    const pending = fixture.pills.get("child")!.button.behavior.onPress();
    document.querySelector('[data-testid="workspace-tab-agent_child"]')!.setAttribute("aria-selected", "false");
    document.querySelector('[data-testid="workspace-tab-agent_child2"]')!.setAttribute("aria-selected", "true");
    await flush();
    const afterSwitch = load.posted.length;
    completeParent({ agent: fixture.root });
    await pending;
    expect(fixture.opened).toHaveLength(before);
    expect(load.posted).toHaveLength(afterSwitch);

    // In split panes both selected children remain visible: the original caller is still valid.
    document.querySelector('[data-testid="workspace-tab-agent_child"]')!.setAttribute("aria-selected", "true");
    await flush();
    const current = fixture.pills.get("child")!.button.behavior.onPress();
    completeParent({ agent: fixture.root });
    await current;
    expect(fixture.opened).toHaveLength(before + 1);
    expect(fixture.opened.at(-1)!.params!.agentId).toBe("parent");

    const changedParent = fixture.pills.get("child")!.button.behavior.onPress();
    fixture.agentStream.update("agent_update", { kind: "upsert", agent: { ...fixture.child, labels: { ...fixture.child.labels, "paseo.parent-agent-id": "archived" } } });
    const afterRelationChange = load.posted.length;
    completeParent({ agent: fixture.root });
    await changedParent;
    expect(fixture.opened).toHaveLength(before + 1);
    expect(load.posted).toHaveLength(afterRelationChange);
  });

  it("replaces directory snapshots on reconnect and releases subscriptions and buttons on teardown", async () => {
    const fixture = host();
    fixture.agentStream.snapshot(page([{ agent: { ...fixture.root, title: "Latest snapshot" } }, { agent: fixture.child }]));
    const load = connect(fixture);
    await flush();
    expect(hierarchy(load).agents.find((a) => a.agentId === "parent")!.name).toBe("Latest snapshot");

    const pendingLoads: Array<(data: { projects: NativeProject[] }) => void> = [];
    vi.mocked(fixture.client.paseo.projects.list).mockImplementation(() => new Promise((resolve) => pendingLoads.push(resolve)));
    fixture.agentStream.snapshot(page([{ agent: fixture.root }, { agent: fixture.child }]));
    fixture.agentStream.update("agent_update", { kind: "upsert", agent: { ...fixture.child, title: "Old queued child" } });
    fixture.workspaceStream.update("workspace_update", { kind: "upsert", workspace: { id: "task", projectId: "p1", name: "Updated task" } });
    fixture.project({ kind: "upsert", project: { projectId: "p1", projectDisplayName: "Master", projectCustomName: "Updated master" } });
    fixture.agentStream.snapshot(page([{ agent: fixture.root }]));
    fixture.agentStream.update("agent_update", { kind: "upsert", agent: { ...fixture.child, id: "newer", title: "Newer queued child" } });
    const projectData = { projects: [{ projectId: "p0", projectDisplayName: "Projects" }, { projectId: "p1", projectDisplayName: "Master" }] };
    pendingLoads[1](projectData);
    await flush();
    expect(hierarchy(load).agents.map((a) => a.agentId)).toEqual(["newer", "parent"]);
    expect(fixture.pills.has("child")).toBe(false);
    expect(fixture.pills.has("newer")).toBe(true);
    expect(hierarchy(load).workspaces.find((w) => w.workspaceId === "task")!.name).toBe("Updated task");
    expect(hierarchy(load).projects.find((p) => p.projectId === "p1")!.name).toBe("Updated master");
    pendingLoads[0](projectData);
    await flush();
    expect(hierarchy(load).agents.map((a) => a.agentId)).toEqual(["newer", "parent"]);

    vi.mocked(fixture.client.paseo.projects.list).mockResolvedValue(projectData);
    fixture.agentStream.snapshot(page([{ agent: fixture.root }]));
    fixture.workspaceStream.snapshot(page([{ id: "projects", projectId: "p0", name: "Projects" }]));
    await flush();
    expect(hierarchy(load).agents.map((a) => a.agentId)).toEqual(["parent"]);
    expect(hierarchy(load).workspaces.map((w) => w.workspaceId)).toEqual(["projects"]);
    const stop = stops.pop()!; stop();
    expect(fixture.projectStop).toHaveBeenCalledOnce();
    expect(fixture.agentStream.subscription.release).toHaveBeenCalledOnce();
    expect(fixture.workspaceStream.subscription.release).toHaveBeenCalledOnce();
    expect(fixture.pills.size).toBe(0);
    const count = load.posted.length;
    fixture.agentStream.update("agent_update", { kind: "upsert", agent: fixture.child });
    await flush();
    expect(load.posted).toHaveLength(count);
  });

  it("starts no SDK catalog, parent buttons or selection publisher outside a listed embed", async () => {
    for (const framedBy of [null, "http://evil.test"]) {
      const fixture = host(), load = pageLoad(newTab(), { framedBy });
      stops.push(startClientPart(load.page, fixture.client, async () => EMBED));
      await flush();
      expect(fixture.client.paseo.projects.list).not.toHaveBeenCalled();
      expect(fixture.client.paseo.agents.list).not.toHaveBeenCalled();
      expect(fixture.client.addComposerPill).not.toHaveBeenCalled();
      expect(load.posted).toEqual([]);
    }
  });

  it("validates visible selections against current SDK IDs, recovering after catalog load and clearing removed or reconnected IDs", async () => {
    document.body.innerHTML = '<button data-testid="workspace-tab-agent_hidden" aria-selected="true"></button><button data-testid="workspace-tab-agent_not-in-sdk-catalog" aria-selected="true"></button><button data-testid="workspace-tab-agent_a" aria-selected="true"></button><button data-testid="workspace-tab-agent_b" aria-selected="true"></button><button data-testid="workspace-tab-agent_c" aria-selected="false"></button>';
    for (const node of document.querySelectorAll("button")) {
      Object.assign(node, { checkVisibility: () => node.dataset.testid !== "workspace-tab-agent_hidden", getBoundingClientRect: () => ({ width: node.dataset.testid === "workspace-tab-agent_hidden" ? 0 : 160, height: 28 }) });
    }
    const fixture = host();
    fixture.agentStream.snapshot(page(["a", "b", "c"].map((id) => ({ agent: { ...nativeState, id, workspaceId: "task", labels: {} } }))));
    const load = connect(fixture);
    const selected = () => load.posted.filter((p) => p.data.type === "selection").at(-1)?.data.agentIds;
    // The DOM already selects valid-looking IDs, but none is trusted until the SDK resolves.
    expect(selected()).toEqual([]);
    await flush();
    expect(selected()).toEqual(["a", "b"]);
    expect(load.posted.filter((p) => p.data.type === "selection").every((p) => !(p.data.agentIds as string[]).includes("not-in-sdk-catalog"))).toBe(true);
    document.querySelector('[data-testid="workspace-tab-agent_a"]')!.setAttribute("aria-selected", "false");
    document.querySelector('[data-testid="workspace-tab-agent_c"]')!.setAttribute("aria-selected", "true");
    await flush();
    expect(selected()).toEqual(["b", "c"]);
    // No DOM mutation: SDK removal and reconnect still recompute the retained candidate set.
    fixture.agentStream.update("agent_update", { kind: "remove", agentId: "b" });
    expect(selected()).toEqual(["c"]);
    fixture.agentStream.snapshot(page([{ agent: { ...nativeState, id: "a", workspaceId: "task", labels: {} } }]));
    await flush();
    expect(selected()).toEqual([]);
    const stop = stops.pop()!; stop();
    const count = load.posted.length;
    document.querySelector('[data-testid="workspace-tab-agent_c"]')!.setAttribute("aria-selected", "false");
    await flush();
    expect(load.posted).toHaveLength(count);
  });
});
