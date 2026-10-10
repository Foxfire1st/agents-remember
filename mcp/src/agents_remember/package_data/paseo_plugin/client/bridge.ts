import { documentSidebar, isDocumentPage, setEmbedPageMode } from "./sidebar";
import { PLUGIN_ID } from "./look";
import { startHierarchy, type HierarchyClient } from "./hierarchy";
import { readSelectedChats, watchSelectedChats } from "./selection";
import type { PluginPage } from "./page";

// The control channel between the AR dashboard and the app it frames.
//
//   dashboard -> app   { type: "ar.open", agentId, workspaceId? } | { type: "ar.open", workspaceId }
//                      { type: "ar.ping" }
//   app -> dashboard   { source: "ar-plugin", type: "ready" | "shown" | "error" | "pong", ... }
//
// A document page names the agent's workspace in `ar.open` for the record; navigation itself
// carries the agent alone, because the supported `navigation.openAgent({agentId})` resolves the
// agent's workspace from the application's own session (pinned @getpaseo/server 0.11.0-beta.2:
// resolveNavigateToAgent reads sessions/agents before navigating to that workspace with the
// agent target). The bridge never invents a workspace hop of its own.
//
// `shown` is display evidence, never navigation receipt: an opened agent is reported only when
// the shared sign below is satisfied for it — the frame's route names its workspace as exact
// path segments, the stored tab layout under the exact key names it as the shown pane's
// focused tab, no visible selected agent tab names another agent, and an agent chat is
// visibly mounted with no loading/error pane — evaluated at once when the display already
// satisfies the request and re-evaluated on later observations. A screen that never appears
// is never reported, so the dashboard's deadline ends the request.
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
  /**
   * Display evidence for the one unanswered open. The shared sign below is satisfied only by
   * the request's own agent named in the frame's route and stored tab state with a visibly
   * mounted chat, so another agent's screen or an Opening screen never satisfies it — while
   * a display that already shows the requested agent confirms at once.
   */
  let pendingDisplay: { agentId: string; workspaceId: string | null; sourceAgentId?: string; generation: number } | null = null;
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
    confirmDisplay();
  };
  /** The published selection surface is intentionally left exactly as MIK-R95 fixed it: an
   * unknown compact selection stays unknown there. Display acknowledgement below reads its own
   * compact observation and never reinterprets the published set. */
  const firstVisible = (selector: string): unknown => {
    const nodes = page.document.querySelectorAll(selector);
    for (const node of nodes) {
      const element = node as { getBoundingClientRect?: unknown; checkVisibility?: unknown };
      if (typeof element.getBoundingClientRect !== "function") continue;
      const bounds = element.getBoundingClientRect() as { width?: unknown; height?: unknown };
      if (typeof bounds.width !== "number" || typeof bounds.height !== "number") continue;
      if (bounds.width <= 0 || bounds.height <= 0) continue;
      if (typeof element.checkVisibility === "function" && !element.checkVisibility()) continue;
      return node;
    }
    return null;
  };
  /**
   * A visibly mounted agent chat with no loading/error pane, in either layout. Identity comes
   * from the shared sign below; this probe only says a chat screen is mounted. The scroll
   * root is matched by its shared `agent-chat-scroll-` prefix on either id or test id: the
   * pinned bundle carries the native literal while the web runtime constructs its own
   * variant (observed live `agent-chat-scroll-web-dom-scroll`, 960x512 visible).
   */
  const agentChatVisible = () => {
    const roots = page.document.querySelectorAll("[id], [data-testid]");
    let mounted = false;
    for (const node of roots) {
      const element = node as { getAttribute?: unknown; getBoundingClientRect?: unknown; checkVisibility?: unknown };
      if (typeof element.getAttribute !== "function") continue;
      const id = element.getAttribute("id");
      const testid = element.getAttribute("data-testid");
      const name = (typeof id === "string" && id.startsWith("agent-chat-scroll-")) ? id
        : (typeof testid === "string" && testid.startsWith("agent-chat-scroll-")) ? testid : null;
      if (name === null) continue;
      if (typeof element.getBoundingClientRect !== "function") continue;
      const bounds = element.getBoundingClientRect() as { width?: unknown; height?: unknown };
      if (typeof bounds.width !== "number" || typeof bounds.height !== "number") continue;
      if (bounds.width <= 0 || bounds.height <= 0) continue;
      if (typeof element.checkVisibility === "function" && !element.checkVisibility()) continue;
      mounted = true;
      break;
    }
    if (!mounted) return false;
    return firstVisible('[id="agent-loading"], [data-testid="agent-loading"]') === null &&
      firstVisible('[id="agent-not-found"], [data-testid="agent-not-found"]') === null;
  };
  /**
   * No contradicting visible selection: every on-screen selected agent tab names the
   * request's agent. Cached workspace tabs remain selected with zero bounds and are
   * excluded by visibility. Where the compact trigger replaces the strip there is no
   * tab to contradict, and the stored focus below decides alone.
   */
  const noContradictingSelection = (agentId: string): boolean => {
    const tabs = page.document.querySelectorAll('[data-testid^="workspace-tab-agent_"][aria-selected="true"]');
    for (const node of tabs) {
      const element = node as { getAttribute?: unknown; getBoundingClientRect?: unknown; checkVisibility?: unknown };
      if (typeof element.getBoundingClientRect !== "function") continue;
      const bounds = element.getBoundingClientRect() as { width?: unknown; height?: unknown };
      if (typeof bounds.width !== "number" || typeof bounds.height !== "number") continue;
      if (bounds.width <= 0 || bounds.height <= 0) continue;
      if (typeof element.checkVisibility === "function" && !element.checkVisibility()) continue;
      if (typeof element.getAttribute !== "function") return false;
      const testid = element.getAttribute("data-testid");
      if (typeof testid !== "string" || testid !== `workspace-tab-agent_${agentId}`) return false;
    }
    return true;
  };
  /**
   * The stored layout may be written after the last DOM change of the display, so the wait
   * is re-observed on a timer as well as on DOM batches. The timer runs only while one
   * request waits and ends with it: it is armed when the wait is set, and clearing the wait
   * always stops it. (The page's storage-write notice is not used: client/load.ts already
   * owns its one listener.)
   */
  let displayTimer: unknown = null;
  const stopDisplayTimer = () => {
    if (displayTimer === null) return;
    try {
      page.window.clearInterval(displayTimer as number);
    } catch {
      // Observing must never break the bridge's teardown.
    }
    displayTimer = null;
  };
  const clearPending = () => { pendingDisplay = null; stopDisplayTimer(); };
  const armDisplayTimer = () => {
    stopDisplayTimer();
    try {
      displayTimer = page.window.setInterval(() => {
        if (!live) { stopDisplayTimer(); return; }
        confirmDisplay();
      }, 250);
    } catch {
      displayTimer = null;
    }
  };
  /**
   * The shared display sign, one for both layouts: the frame's route names the request's
   * workspace as exact path segments, the application's stored tab layout under the exact
   * key `<server>:<workspace>` names `agent_<agent id>` as the focused tab of its shown
   * (non-hidden focused) pane, no visible selected agent tab names another agent, and an
   * agent chat is visibly mounted with no loading or not-found pane. Same class as
   * client/selection.ts and client/look.ts: pinned, unsupported, read-only, fail-closed.
   * It only reads; it writes nothing into the application's storage and calls nothing of
   * the application.
   *
   * Pinned @getpaseo/server 0.11.0-beta.2, web build
   * index-a43718759ebeb27df202e8b9fc2fe58a.js: the agent route is
   * `/h/<server>/workspace/<workspace>` with the intent `open=agent:<id>`; the app persists
   * its tab store under the localStorage name `workspace-layout-state` as `{ state, version }`,
   * with `state.layoutByWorkspace[<serverId>:<workspaceId>]` holding `focusedPaneId` and a tree
   * of `{ kind: "group", group: { children } }` / `{ kind: "pane", pane }` nodes, each pane
   * with `id`, `tabIds`, `focusedTabId` and optional `hidden`. An agent's tab id is
   * `agent_<agent id>` (buildDeterministicWorkspaceTabId), the same identity the wide strip
   * shows as `workspace-tab-agent_<id>`. Comparisons are exact: route path segments, never a
   * search of the whole address (a workspace id in the query names nothing); the whole stored
   * key, never its ending (another server's key never matches). Re-read this adapter and
   * client/selection.ts on the new release before the pin moves (also on the 0.11.1 upgrade).
   *
   * NATIVE MEASUREMENT 2026-10-09 (sandbox ar-l121-witness, pinned bundle, real Chromium,
   * no live roles): the state lies in the page's localStorage under `workspace-layout-state`
   * (absent before the workspace is entered); a tab creation flips the new tab to focused and
   * a manual tab change flips `focusedTabId` to it, both stored within ~0.15 s and visible in
   * the strip (`workspace-tab-terminal_<terminalId>`, `aria-selected`); below the desktop
   * breakpoint the strip is replaced by `workspace-tab-switcher-trigger` ("Switch tabs (N
   * open)") with per-tab options naming the full stored tab ids and a check on the focused
   * one; closing a tab falls the focus back and renames the trigger count; shell exit removes
   * its tab with `focusedTabId` null on the empty pane; focus survives a reload. All focus
   * changes funnel through the one store reducer with a no-dangling-focus normalizer. Two
   * agents of one workspace were not opened (needs live roles): agent tabs were verified
   * statically (`agent_<id>`, same funnel), not clicked.
   *
   * LIVE-AGENT MEASUREMENT 2026-10-09 (FINAL sandbox ar-l121-witness, scripted Chromium,
   * stopped agents, real bridge context): opening a stopped agent's deep link settles with
   * the strip showing `workspace-tab-agent_<full agent uuid>` `aria-selected="true"`
   * 160x28 visible, the other agent's tab `aria-selected="false"`, ten visible
   * `assistant-message` nodes, one `agent-chat-scroll-web-dom-scroll` 960x512 visible, and
   * no `agent-loading` / `agent-not-found` node once settled (a loading skeleton appears
   * transiently mid-render). The bundle carries only the native scroll literal
   * (`agent-chat-scroll-native-virtualized`); the web runtime constructs the
   * `agent-chat-scroll-web-dom-scroll` variant, so the probe matches the shared
   * `agent-chat-scroll-` prefix on id or test id. The store key is exactly
   * `<server>:<workspace>` with the opened agent as the shown pane's focused tab.
   *
   * Compact identity reliance (bundle names unstable): the compact sheet's row select and the
   * wide strip's tab click call one layout-store focus function with (workspace key, tab id);
   * opening an agent ends in the same function; the trigger's shown tab derives from the stored
   * layout's focused pane. Wide manual switches flip focusedTabId within ~0.15 s; compact
   * close-driven flips flip it the same way; sheet-row selects and agent tabs share the funnel
   * without a clicked observation here. Where the compact trigger replaces the strip there is
   * no selected tab to contradict, and the stored focus decides alone.
   *
   * UNSUPPORTED application behaviour this relies on: the storage name, the `{state,version}`
   * envelope, the `<serverId>:<workspaceId>` key, the pane tree with `focusedPaneId` /
   * `focusedTabId`, the `agent_<id>` tab id, the `workspace-tab-agent_<id>` strip test id
   * with `aria-selected`, the `agent-chat-scroll-` scroll root, the loading/not-found
   * markers, and the `/h/<server>/workspace/<id>` route. Any of it absent, unparsable, of
   * another shape, without the workspace, or with another focused or selected tab: no
   * confirmation, never a fall back to "some chat is visible".
   */
  const LAYOUT_STATE_KEY = "workspace-layout-state";
  const panesOf = (node: unknown, into: Array<Record<string, unknown>>): void => {
    if (typeof node !== "object" || node === null) return;
    const record = node as Record<string, unknown>;
    if (record.kind === "pane") {
      if (typeof record.pane === "object" && record.pane !== null) into.push(record.pane as Record<string, unknown>);
      return;
    }
    if (record.kind === "group") {
      const group = record.group;
      if (typeof group === "object" && group !== null) {
        const children = (group as Record<string, unknown>).children;
        if (Array.isArray(children)) for (const child of children) panesOf(child, into);
      }
    }
  };
  const displaySatisfied = (workspaceId: string | null, agentId: string): boolean => {
    try {
      if (!workspaceId) return false;
      // Route path segments only: the query and hash name nothing. A workspace id that
      // appears only in `?next=` (or anywhere but the route) never satisfies.
      const pathname = page.location?.pathname;
      if (typeof pathname !== "string") return false;
      const segments = pathname.split("/");
      // ["", "h", "<server>", "workspace", "<workspace>", ...]
      if (segments.length < 5 || segments[1] !== "h" || segments[3] !== "workspace") return false;
      const server = segments[2];
      if (typeof server !== "string" || server === "" || segments[4] !== workspaceId) return false;
      const raw = page.localStorage.getItem(LAYOUT_STATE_KEY);
      if (typeof raw !== "string") return false;
      const parsed: unknown = JSON.parse(raw);
      if (typeof parsed !== "object" || parsed === null) return false;
      const state = (parsed as Record<string, unknown>).state;
      if (typeof state !== "object" || state === null) return false;
      const layouts = (state as Record<string, unknown>).layoutByWorkspace;
      if (typeof layouts !== "object" || layouts === null) return false;
      // The whole key, never its ending: another server's workspace never matches.
      const layout = (layouts as Record<string, unknown>)[`${server}:${workspaceId}`];
      if (typeof layout !== "object" || layout === null) return false;
      const focusedPaneId = (layout as Record<string, unknown>).focusedPaneId;
      if (typeof focusedPaneId !== "string") return false;
      const panes: Array<Record<string, unknown>> = [];
      panesOf((layout as Record<string, unknown>).root, panes);
      const shown = panes.find((pane) => pane.id === focusedPaneId);
      if (!shown || shown.hidden === true || typeof shown.focusedTabId !== "string") return false;
      if (shown.focusedTabId !== `agent_${agentId}`) return false;
      // The visible strip agrees: no on-screen selected agent tab may name another agent.
      // A render lag between the store write and the strip keeps the wait open; the timer
      // below re-observes it.
      if (!noContradictingSelection(agentId)) return false;
      return agentChatVisible();
    } catch {
      return false;
    }
  };
  /**
   * `shown` means the shared sign below is satisfied for this request: the frame's route
   * names the request's workspace as exact path segments, the stored layout under the exact
   * key names the request's agent as the shown pane's focused tab, no visible selected
   * agent tab names another agent, and an agent chat is visibly mounted with no loading
   * or not-found pane. A display that already satisfies the request confirms at once, on
   * any observation. A newer request, a stopped bridge, or a changed parent relation drops
   * the wait, and the dashboard's deadline ends it.
   */
  const confirmDisplay = () => {
    const pending = pendingDisplay;
    if (!pending || !live || pending.generation !== openGeneration) { clearPending(); return; }
    if (pending.sourceAgentId && (selectedCandidates.includes(pending.sourceAgentId) === false ||
        knownAgents.get(pending.sourceAgentId)?.parentAgentId !== pending.agentId)) {
      clearPending();
      return;
    }
    if (!displaySatisfied(pending.workspaceId, pending.agentId)) return;
    clearPending();
    post({ type: "shown", agentId: pending.agentId, workspaceId: pending.workspaceId });
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
    stopDisplayTimer();
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
      // The agent alone: the application resolves its workspace from its own session. A
      // dashboard workspace that disagrees with the host record never blocks the open; the
      // reported workspace below always names where the host keeps the agent.
      open({ agentId });
    } catch (error) {
      const message = errorText(error);
      const code = /not found/i.test(message) ? "agent-not-found" : "open-failed";
      failure(code, message);
      return;
    }
    // A display that already satisfies the request confirms at once; otherwise the wait
    // stays open for a later observation — a mutation batch, a re-published selection, or
    // the timer below for a later store write.
    pendingDisplay = { agentId, workspaceId: agent.workspaceId, ...(sourceAgentId ? { sourceAgentId } : {}), generation };
    armDisplayTimer();
    confirmDisplay();
  };

  const openWorkspace = async (workspaceId: string) => {
    const generation = ++openGeneration;
    stopDisplayTimer();
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
      stopDisplayTimer();
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
  // The observation driver: every DOM mutation batch after an open re-reads the selection
  // candidates synchronously and re-evaluates the shared sign, so confirmation never runs
  // on a set older than the observation. Scans run only while a request waits; teardown
  // disconnects.
  const stopObserving = (() => {
    try {
      const observer = new page.window.MutationObserver(() => {
        selectedCandidates = readSelectedChats(page);
        confirmDisplay();
      });
      observer.observe(page.document.documentElement, {
        subtree: true,
        childList: true,
        attributes: true,
        attributeFilter: ["aria-selected", "data-testid", "style", "class", "hidden", "aria-hidden"],
      });
      return () => observer.disconnect();
    } catch {
      return () => {};
    }
  })();

  return () => {
    live = false;
    stopDisplayTimer();
    stopSidebar?.();
    stopObserving();
    stopSelection();
    stopHierarchy();
    page.window.removeEventListener("message", onMessage);
  };
}
