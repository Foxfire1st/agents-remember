import type { PluginClientContext } from "@getpaseo/plugin/client";
import { PLUGIN_ID, setWorkspaceHeaderHidden } from "./look";

// The control channel between the AR dashboard and the app it frames.
//
//   dashboard -> app   { type: "ar.open", agentId } | { type: "ar.open", workspaceId }
//                      { type: "ar.ping" }
//   app -> dashboard   { source: "ar-plugin", type: "ready" | "shown" | "error" | "pong", ... }
//
// Each side names the other's origin when it posts and checks it when it receives; neither uses
// a wildcard, and no message carries a secret. Navigation itself goes through supported plugin
// interfaces (a contributed screen and its `navigation` prop). UNSUPPORTED here: listening for
// `message` events and reading `location.ancestorOrigins`, which Paseo documents as unavailable
// to plugin code because they do not exist on iOS and Android.

export const OPEN_SCREEN_ID = "open";

export interface EmbedEntry {
  dashboardOrigin: string;
  frameBaseUrl: string;
}

// The plugin tsconfig deliberately has no DOM lib; reach the browser globals untyped.
const web = globalThis as any;

function originOf(value: string): string | null {
  try {
    return new web.URL(value).origin;
  } catch {
    return null;
  }
}

/**
 * The origin of the page that frames this one: `null` at top level (a standalone tab),
 * `undefined` when the page is framed and the browser does not say by whom.
 */
export function framingOrigin(): string | null | undefined {
  let framed = true;
  try {
    framed = web.window.parent !== web.window;
  } catch {
    // A parent that cannot even be compared is still a parent.
  }
  if (!framed) return null;
  const ancestors = web.location.ancestorOrigins;
  if (ancestors && ancestors.length > 0) return ancestors[0];
  // Browsers without ancestorOrigins: the referrer names the parent only until this frame
  // navigates itself, after which it names this page's own origin and proves nothing.
  const referrer = web.document.referrer ? originOf(web.document.referrer) : null;
  return referrer && referrer !== web.location.origin ? referrer : undefined;
}

/** Whether the embed list pairs this parent origin with the origin this page is served from. */
export function isListedParent(embed: readonly EmbedEntry[], parentOrigin: string): boolean {
  return embed.some(
    (entry) =>
      originOf(entry.dashboardOrigin) === parentOrigin &&
      originOf(entry.frameBaseUrl) === web.location.origin,
  );
}

/** Start answering the listed parent. Returns the function that stops it. */
export function installBridge(client: PluginClientContext, parentOrigin: string): () => void {
  const post = (payload: Record<string, unknown>) => {
    web.window.parent.postMessage({ source: PLUGIN_ID, ...payload }, parentOrigin);
  };

  const openAgent = async (agentId: string) => {
    const agent = client.paseo.agents.ref(agentId);
    try {
      await agent.refresh();
    } catch (error) {
      const message = String((error as Error)?.message ?? error);
      const code = /not found/i.test(message) ? "agent-not-found" : "open-failed";
      post({ type: "error", code, agentId, message });
      return;
    }
    if (agent.archivedAt) {
      post({ type: "error", code: "agent-archived", agentId, message: "the agent is archived" });
      return;
    }
    // The stamp makes every request a new set of screen params, so asking for the agent that
    // was shown last navigates again after the user moved elsewhere in the app.
    client.openScreen({ screenId: OPEN_SCREEN_ID, params: { agentId, at: String(Date.now()) } });
    post({ type: "shown", agentId, workspaceId: agent.workspaceId });
  };

  const onMessage = (event: any) => {
    // Only the listed parent itself: no other origin, and no other window of that origin.
    if (event.origin !== parentOrigin || event.source !== web.window.parent) return;
    const message = event.data;
    if (!message || typeof message !== "object") return;
    if (message.type === "ar.ping") {
      post({ type: "pong" });
    } else if (message.type === "ar.open" && typeof message.agentId === "string") {
      void openAgent(message.agentId);
    } else if (message.type === "ar.open" && typeof message.workspaceId === "string") {
      const workspaceId: string = message.workspaceId;
      client.openScreen({ screenId: OPEN_SCREEN_ID, params: { workspaceId, at: String(Date.now()) } });
      post({ type: "shown", workspaceId });
    } else if (message.type === "ar.open") {
      post({ type: "error", code: "open-failed", message: "ar.open names no agent or workspace" });
    }
  };

  web.window.addEventListener("message", onMessage);
  setWorkspaceHeaderHidden(true);
  post({ type: "ready" });

  return () => {
    web.window.removeEventListener("message", onMessage);
    setWorkspaceHeaderHidden(false);
  };
}
