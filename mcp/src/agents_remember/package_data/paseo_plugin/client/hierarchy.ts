// Public Paseo SDK directories and per-agent composer pills share this one embed lifetime.
// The structural types are the injected subset of @getpaseo/client and @getpaseo/plugin/client;
// this browser-independent code is exercised by the dashboard's existing TypeScript test gate.
export interface NativeProject {
  projectId: string;
  projectDisplayName: string;
  projectCustomName?: string | null;
}
export interface NativeWorkspace {
  id: string;
  projectId: string;
  name: string;
  title?: string | null;
}
export interface NativeAgent {
  id: string;
  provider: string;
  status: "initializing" | "idle" | "running" | "error" | "closed";
  pendingPermissions: readonly unknown[];
  requiresAttention?: boolean;
  attentionReason?: "finished" | "error" | "permission" | null;
  providerUnavailable?: boolean;
  title?: string | null;
  workspaceId?: string | null;
  archivedAt?: string | null;
  labels?: Record<string, string>;
}
interface Subscription<T> {
  subscribe(observer: { snapshot(value: T): void; update(message: any): void; error?(error: unknown): void }): () => void;
  release(): Promise<void>;
}
interface Directory<T> {
  entries: T[];
  pageInfo: { hasMore: boolean; nextCursor: string | null };
  subscription?: Subscription<Directory<T>>;
}
interface ParentButton {
  title: string;
  icon: string;
  label: string;
  behavior: { kind: "action"; onPress(): void | Promise<void> };
}
interface Registration {
  update(button: Partial<ParentButton>): void;
  remove(): void;
}
export interface HierarchyClient {
  paseo: {
    projects: {
      list(): Promise<{ projects: NativeProject[] }>;
      subscribe(handler: (update: { kind: "upsert"; project: NativeProject } | { kind: "remove"; projectId: string }) => void): () => void;
    };
    workspaces: { list(options: { page: { limit: number; cursor?: string }; subscribe?: {} }): Promise<Directory<NativeWorkspace>> };
    agents: { list(options: { page: { limit: number; cursor?: string }; subscribe?: {}; filter: { includeArchived: true } }): Promise<Directory<{ agent: NativeAgent }>> };
  };
  addComposerPill(input: { id: string; workspaceId: string; agentId: string; button: ParentButton }): Registration;
}

const LABEL_KEYS = ["ar.role", "ar.request-id", "ar.sprint-ref", "ar.master-ref", "ar.task-ref", "ar.qualification-fixture", "paseo.parent-agent-id"];
const PAGE_SIZE = 200;

async function allEntries<T>(first: Directory<T>, next: (cursor: string) => Promise<Directory<T>>): Promise<T[]> {
  const entries = [...first.entries];
  const seen = new Set<string>();
  let page = first;
  while (page.pageInfo.hasMore) {
    const cursor = page.pageInfo.nextCursor;
    if (!cursor || seen.has(cursor)) throw new Error("Paseo returned an invalid directory continuation");
    seen.add(cursor);
    page = await next(cursor);
    entries.push(...page.entries);
  }
  return entries;
}

export function startHierarchy(
  client: HierarchyClient,
  post: (message: Record<string, unknown>) => void,
  openParent: (sourceAgentId: string, parentAgentId: string) => Promise<void>,
): () => void {
  let live = true;
  let ready = false;
  let generation = 0;
  let last = "";
  const projects = new Map<string, NativeProject>();
  const workspaces = new Map<string, NativeWorkspace>();
  const agents = new Map<string, NativeAgent>();
  const pills = new Map<string, { workspaceId: string; parentId: string; registration: Registration }>();
  const subscriptions: Subscription<any>[] = [];
  const listeners: Array<() => void> = [];
  let queued: Array<{ kind: "project.update" | "agent_update" | "workspace_update"; apply: () => void }> = [];
  let agentSnapshot: Directory<{ agent: NativeAgent }> | undefined;
  let workspaceSnapshot: Directory<NativeWorkspace> | undefined;

  const publish = () => {
    if (!live || !ready) return;
    for (const [id, pill] of pills) {
      const agent = agents.get(id);
      if (!agent || agent.archivedAt || agent.workspaceId !== pill.workspaceId || !agent.labels?.["paseo.parent-agent-id"]?.trim()) {
        pill.registration.remove();
        pills.delete(id);
      }
    }
    for (const agent of agents.values()) {
      const parentId = agent.labels?.["paseo.parent-agent-id"]?.trim();
      if (!parentId || !agent.workspaceId || agent.archivedAt) continue;
      const current = pills.get(agent.id);
      if (current?.parentId === parentId) continue;
      const button: ParentButton = {
        title: "Back to parent", label: "Back to parent", icon: "ArrowUpLeft",
        behavior: { kind: "action", onPress: () => live ? openParent(agent.id, parentId) : undefined },
      };
      if (current) {
        current.registration.update(button);
        current.parentId = parentId;
      } else {
        pills.set(agent.id, { workspaceId: agent.workspaceId, parentId, registration: client.addComposerPill({ id: "parent", agentId: agent.id, workspaceId: agent.workspaceId, button }) });
      }
    }
    const snapshot = {
      type: "hierarchy",
      projects: [...projects.values()].map((p) => ({ projectId: p.projectId, name: p.projectCustomName ?? p.projectDisplayName })).sort((a, b) => a.projectId.localeCompare(b.projectId)),
      workspaces: [...workspaces.values()].map((w) => ({ workspaceId: w.id, projectId: w.projectId, name: w.title ?? w.name })).sort((a, b) => a.workspaceId.localeCompare(b.workspaceId)),
      agents: [...agents.values()].map((a) => ({
        agentId: a.id, name: a.title ?? a.id, workspaceId: a.workspaceId ?? null,
        provider: a.provider, status: a.status, pendingPermissionCount: a.pendingPermissions.length,
        requiresAttention: a.requiresAttention === true, attentionReason: a.attentionReason ?? null,
        providerUnavailable: a.providerUnavailable === true,
        parentAgentId: a.labels?.["paseo.parent-agent-id"]?.trim() || null, archivedAt: a.archivedAt ?? null,
        labels: Object.fromEntries(LABEL_KEYS.filter((key) => typeof a.labels?.[key] === "string").map((key) => [key, a.labels![key]])),
      })).sort((a, b) => a.agentId.localeCompare(b.agentId)),
    };
    const value = JSON.stringify(snapshot);
    if (value !== last) { last = value; post(snapshot); }
  };
  const change = (kind: "project.update" | "agent_update" | "workspace_update", apply: () => void) => {
    if (!live) return;
    if (!ready) queued.push({ kind, apply });
    else { apply(); publish(); }
  };
  listeners.push(client.paseo.projects.subscribe((update) => change("project.update", () => {
    if (update.kind === "upsert") projects.set(update.project.projectId, update.project);
    else projects.delete(update.projectId);
  })));

  const observe = (kind: "agent_update" | "workspace_update", data: Directory<any>) => {
    const subscription = data.subscription;
    if (!subscription) throw new Error("Paseo returned no directory subscription");
    if (!live) { void subscription.release(); return data; }
    subscriptions.push(subscription);
    let initial = true;
    listeners.push(subscription.subscribe({
      snapshot: (snapshot: any) => {
        queued = queued.filter((update) => update.kind !== kind);
        if (kind === "agent_update") agentSnapshot = snapshot;
        else workspaceSnapshot = snapshot;
        if (initial) { initial = false; return; }
        if (live) void load();
      },
      update: (message: any) => {
        if (message.type !== kind) return;
        change(kind, () => {
          const update = message.payload;
          if (kind === "agent_update") {
            if (update.kind === "upsert") agents.set(update.agent.id, update.agent);
            else agents.delete(update.agentId);
          } else {
            if (update.kind === "upsert") workspaces.set(update.workspace.id, update.workspace);
            else workspaces.delete(update.workspaceId);
          }
        });
      },
      error: (error) => { if (live) post({ type: "hierarchy-error", message: String(error) }); },
    }));
    return kind === "agent_update" ? agentSnapshot! : workspaceSnapshot!;
  };
  const load = async () => {
    const epoch = ++generation;
    ready = false;
    try {
      const [projectData, agentData, workspaceData] = await Promise.all([
        client.paseo.projects.list(),
        agentSnapshot ?? client.paseo.agents.list({ page: { limit: PAGE_SIZE }, filter: { includeArchived: true }, subscribe: {} }).then((data) => { agentSnapshot = data; return observe("agent_update", data); }),
        workspaceSnapshot ?? client.paseo.workspaces.list({ page: { limit: PAGE_SIZE }, subscribe: {} }).then((data) => { workspaceSnapshot = data; return observe("workspace_update", data); }),
      ]);
      if (!live || epoch !== generation) return;
      const [allAgents, allWorkspaces] = await Promise.all([
        allEntries(agentData, (cursor) => client.paseo.agents.list({ page: { limit: PAGE_SIZE, cursor }, filter: { includeArchived: true } })),
        allEntries(workspaceData, (cursor) => client.paseo.workspaces.list({ page: { limit: PAGE_SIZE, cursor } })),
      ]);
      if (!live || epoch !== generation) return;
      projects.clear(); workspaces.clear(); agents.clear();
      for (const project of projectData.projects) projects.set(project.projectId, project);
      for (const workspace of allWorkspaces) workspaces.set(workspace.id, workspace);
      for (const entry of allAgents) agents.set(entry.agent.id, entry.agent);
      for (const update of queued.splice(0)) update.apply();
      ready = true;
      publish();
    } catch (error) {
      if (live && epoch === generation) post({ type: "hierarchy-error", message: String(error) });
    }
  };
  void load();
  return () => {
    live = false;
    for (const stop of listeners) stop();
    for (const subscription of subscriptions) void subscription.release();
    for (const pill of pills.values()) pill.registration.remove();
    pills.clear();
  };
}
