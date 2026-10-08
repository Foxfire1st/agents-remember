import { documentSidebar, isDocumentPage, setEmbedPageMode } from "./sidebar";
import { PLUGIN_ID } from "./look";
import { startHierarchy, type HierarchyClient } from "./hierarchy";
import { watchSelectedChats } from "./selection";
import type { PluginPage } from "./page";

// The control channel between the AR dashboard and the app it frames.
//
//   dashboard -> app   { type: "ar.open", agentId } | { type: "ar.open", workspaceId }
//                      { type: "ar.ping" }
//   app -> dashboard   { source: "ar-plugin", type: "ready" | "shown" | "error" | "pong", ... }
//
// Each side names the other's origin when it posts and checks it when it receives; neither uses
// a wildcard, and no message carries a secret. Navigation itself goes through supported plugin
// interfaces (a contributed screen and its `navigation` prop, and the public SDK for the checks).
//
// UNSUPPORTED Paseo behaviour this file relies on: `message` events and `parent.postMessage`
// being available to plugin code; `location.ancestorOrigins` and `document.referrer` naming the
// page that frames the app; the text "not found" in the SDK's error for a missing agent; and
// Paseo's web UI letting itself be framed at all (it sends no frame-ancestors restriction).

export const OPEN_SCREEN_ID = "open";

export interface EmbedEntry {
  dashboardOrigin: string;
  frameBaseUrl: string;
}

/** What the bridge uses of the plugin's client context (a `PluginClientContext` satisfies it). */
export interface BridgeClient extends HierarchyClient {
  paseo: HierarchyClient["paseo"] & {
    agents: HierarchyClient["paseo"]["agents"] & {
      ref(agentId: string): {
        refresh(): Promise<unknown>;
        readonly archivedAt: unknown;
        readonly workspaceId: string | null;
      };
    };
    workspaces: HierarchyClient["paseo"]["workspaces"] & { ref(workspaceId: string): { refresh(): Promise<unknown> } };
  };
  openScreen(input: { screenId: string; params?: Record<string, string> }): void;
}

function originOf(value: string): string | null {
  try {
    return new URL(value).origin;
  } catch {
    return null;
  }
}

/**
 * The origin of the page that frames this one: `null` at top level (a standalone tab),
 * `undefined` when the page is framed and nothing says by whom.
 *
 * `carriedParent` is the origin the page verified before this plugin reloaded it. It is used
 * only when the page is framed and the browser names no parent (no `ancestorOrigins`, and a
 * referrer that after the reload is the page's own origin). It is a candidate like the others:
 * the caller still checks it against the embed list on every load.
 */
export function framingOrigin(
  page: PluginPage,
  carriedParent: string | null,
): string | null | undefined {
  let framed = true;
  try {
    framed = page.window.parent !== page.window;
  } catch {
    // A parent that cannot even be compared is still a parent.
  }
  if (!framed) return null;
  const ancestors = page.location.ancestorOrigins;
  if (ancestors && ancestors.length > 0) return ancestors[0];
  const referrer = page.document.referrer ? originOf(page.document.referrer) : null;
  if (referrer && referrer !== page.location.origin) return referrer;
  return carriedParent ?? undefined;
}

/** Whether the embed list pairs this parent origin with the origin this page is served from. */
export function isListedParent(
  embed: readonly EmbedEntry[],
  parentOrigin: string,
  ownOrigin: string,
): boolean {
  return embed.some(
    (entry) =>
      originOf(entry.dashboardOrigin) === parentOrigin && originOf(entry.frameBaseUrl) === ownOrigin,
  );
}

/** Only the listed parent itself: no other origin, and no other window of that origin. */
export function isParentMessage(
  event: { origin: string; source: unknown },
  parentOrigin: string,
  parentWindow: unknown,
): boolean {
  return event.origin === parentOrigin && event.source === parentWindow;
}

function errorText(error: unknown): string {
  return String((error as { message?: unknown } | null)?.message ?? error);
}

/** Start answering the listed parent. Returns the function that stops it. */
export function installBridge(
  client: BridgeClient,
  page: PluginPage,
  parentOrigin: string,
): () => void {
  let live = true;
  let openGeneration = 0;
  let stopSidebar: (() => void) | undefined;
  const pageMode = (mode: "document" | "chats") => {
    setEmbedPageMode(page, mode);
    if (mode === "document" && !stopSidebar) stopSidebar = documentSidebar(page);
    else if (mode === "chats" && stopSidebar) { stopSidebar(); stopSidebar = undefined; }
  };
  if (isDocumentPage(page)) pageMode("document");
  let knownAgents = new Map<string, { parentAgentId: string | null }>();
  let selectedCandidates: string[] = [];
  let lastSelection = "";
  const send = (payload: Record<string, unknown>) => {
    if (!live) return;
    page.window.parent.postMessage({ source: PLUGIN_ID, ...payload }, parentOrigin);
  };
  const publishSelection = () => {
    const agentIds = selectedCandidates.filter((id) => knownAgents.has(id));
    const value = JSON.stringify(agentIds);
    if (value === lastSelection) return;
    lastSelection = value;
    send({ type: "selection", agentIds });
  };
  const post = (payload: Record<string, unknown>) => {
    send(payload);
    if (payload.type === "hierarchy") {
      knownAgents = new Map((payload.agents as Array<{ agentId: string; parentAgentId: string | null }>).map((agent) => [agent.agentId, agent]));
      publishSelection();
    }
  };
  // The stamp makes every request a new set of screen params, so asking for what was shown last
  // navigates again after the user moved elsewhere in the app.
  const open = (params: Record<string, string>) => {
    client.openScreen({ screenId: OPEN_SCREEN_ID, params: { ...params, at: String(Date.now()) } });
  };

  const openAgent = async (agentId: string, sourceAgentId?: string) => {
    const generation = ++openGeneration;
    const currentParent = () => live && generation === openGeneration && (!sourceAgentId || (selectedCandidates.includes(sourceAgentId) && knownAgents.get(sourceAgentId)?.parentAgentId === agentId));
    const failure = (code: string, message: string) => {
      if (!live || !currentParent()) return;
      post(sourceAgentId
        ? { type: "navigation-error", context: "parent", sourceAgentId, targetAgentId: agentId, code, message }
        : { type: "error", code, agentId, message });
    };
    const agent = client.paseo.agents.ref(agentId);
    try {
      const refreshed = await agent.refresh();
      if (!live || !currentParent()) return;
      if (!refreshed) { failure("agent-not-found", "the runtime has no such agent"); return; }
      if (agent.archivedAt) { failure("agent-archived", "the agent is archived"); return; }
      open({ agentId });
    } catch (error) {
      const message = errorText(error);
      const code = /not found/i.test(message) ? "agent-not-found" : "open-failed";
      failure(code, message);
      return;
    }
    post({ type: "shown", agentId, workspaceId: agent.workspaceId });
  };

  const openWorkspace = async (workspaceId: string) => {
    const generation = ++openGeneration;
    let workspace: unknown = null;
    let failure = "the runtime has no such workspace";
    try {
      workspace = await client.paseo.workspaces.ref(workspaceId).refresh();
    } catch (error) {
      failure = errorText(error);
    }
    if (!live || generation !== openGeneration) return;
    if (!workspace) {
      post({ type: "error", code: "workspace-not-found", workspaceId, message: failure });
      return;
    }
    open({ workspaceId });
    post({ type: "shown", workspaceId });
  };

  const onMessage = (event: { origin: string; source: unknown; data: any }) => {
    if (!isParentMessage(event, parentOrigin, page.window.parent)) return;
    const message = event.data;
    if (!message || typeof message !== "object") return;
    if (message.type === "ar.page" && (message.page === "document" || message.page === "chats")) {
      pageMode(message.page);
    } else if (message.type === "ar.ping") {
      post({ type: "pong" });
    } else if (message.type === "ar.open" && typeof message.agentId === "string") {
      void openAgent(message.agentId);
    } else if (message.type === "ar.open" && typeof message.workspaceId === "string") {
      void openWorkspace(message.workspaceId);
    } else if (message.type === "ar.open") {
      openGeneration++;
      post({ type: "error", code: "open-failed", message: "ar.open names no agent or workspace" });
    }
  };

  page.window.addEventListener("message", onMessage);
  post({ type: "ready" });
  const stopHierarchy = startHierarchy(client, post, (sourceAgentId, parentId) => openAgent(parentId, sourceAgentId));
  const stopSelection = watchSelectedChats(page, (agentIds) => {
    selectedCandidates = agentIds;
    publishSelection();
  });

  return () => {
    live = false;
    stopSidebar?.();
    stopSelection();
    stopHierarchy();
    page.window.removeEventListener("message", onMessage);
  };
}
