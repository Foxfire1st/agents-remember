import { PLUGIN_ID, setWorkspaceHeaderHidden } from "./look";
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
export interface BridgeClient {
  paseo: {
    agents: {
      ref(agentId: string): {
        refresh(): Promise<unknown>;
        readonly archivedAt: unknown;
        readonly workspaceId: string | null;
      };
    };
    workspaces: { ref(workspaceId: string): { refresh(): Promise<unknown> } };
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
  const post = (payload: Record<string, unknown>) => {
    page.window.parent.postMessage({ source: PLUGIN_ID, ...payload }, parentOrigin);
  };
  // The stamp makes every request a new set of screen params, so asking for what was shown last
  // navigates again after the user moved elsewhere in the app.
  const open = (params: Record<string, string>) => {
    client.openScreen({ screenId: OPEN_SCREEN_ID, params: { ...params, at: String(Date.now()) } });
  };

  const openAgent = async (agentId: string) => {
    const agent = client.paseo.agents.ref(agentId);
    try {
      await agent.refresh();
    } catch (error) {
      const message = errorText(error);
      const code = /not found/i.test(message) ? "agent-not-found" : "open-failed";
      post({ type: "error", code, agentId, message });
      return;
    }
    if (agent.archivedAt) {
      post({ type: "error", code: "agent-archived", agentId, message: "the agent is archived" });
      return;
    }
    open({ agentId });
    post({ type: "shown", agentId, workspaceId: agent.workspaceId });
  };

  const openWorkspace = async (workspaceId: string) => {
    let workspace: unknown = null;
    let failure = "the runtime has no such workspace";
    try {
      workspace = await client.paseo.workspaces.ref(workspaceId).refresh();
    } catch (error) {
      failure = errorText(error);
    }
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
    if (message.type === "ar.ping") {
      post({ type: "pong" });
    } else if (message.type === "ar.open" && typeof message.agentId === "string") {
      void openAgent(message.agentId);
    } else if (message.type === "ar.open" && typeof message.workspaceId === "string") {
      void openWorkspace(message.workspaceId);
    } else if (message.type === "ar.open") {
      post({ type: "error", code: "open-failed", message: "ar.open names no agent or workspace" });
    }
  };

  page.window.addEventListener("message", onMessage);
  setWorkspaceHeaderHidden(page.document, true);
  post({ type: "ready" });

  return () => {
    page.window.removeEventListener("message", onMessage);
    setWorkspaceHeaderHidden(page.document, false);
  };
}
