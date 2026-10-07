import type { PluginPage } from "./page";

// Pinned, unsupported Paseo 0.11.0-beta.2 DOM adapter. The public plugin SDK has no native
// tab-selection event. Agent tab identities are workspace-tab-agent_<opaque agent id> and
// aria-selected is per pane, so every visible selected chat belongs to this set. Cached
// workspace tabs remain selected with zero bounds; they are excluded. These are DOM candidates;
// the trusted bridge intersects them with its SDK catalog before publishing selection.
// No focus/color inference.
const AGENT_TAB_PREFIX = "workspace-tab-agent_";
const SELECTED_TABS = `[data-testid^="${AGENT_TAB_PREFIX}"][aria-selected="true"]`;

// The hierarchy subscribes before the bridge starts this page's live publisher. Fan out the
// same value after the bridge receives it; do not retain an earlier selection or read it twice.
const selectedChatListeners = new Set<(agentIds: string[]) => void>();

export function onSelectedChats(changed: (agentIds: string[]) => void): () => void {
  selectedChatListeners.add(changed);
  return () => { selectedChatListeners.delete(changed); };
}

export function watchSelectedChats(page: PluginPage, changed: (agentIds: string[]) => void): () => void {
  let last = "";
  const publish = () => {
    const ids = new Set<string>();
    for (const tab of page.document.querySelectorAll(SELECTED_TABS)) {
      const bounds = tab.getBoundingClientRect();
      if (bounds.width <= 0 || bounds.height <= 0 || !tab.checkVisibility()) continue;
      const id = tab.getAttribute("data-testid").slice(AGENT_TAB_PREFIX.length);
      if (id) ids.add(id);
    }
    const agentIds = [...ids].sort();
    const value = JSON.stringify(agentIds);
    if (value === last) return;
    last = value;
    changed(agentIds);
    for (const listener of selectedChatListeners) listener(agentIds);
  };
  const observer = new page.window.MutationObserver(publish);
  observer.observe(page.document.documentElement, {
    subtree: true,
    childList: true,
    attributes: true,
    attributeFilter: ["aria-selected", "data-testid", "style", "class", "hidden", "aria-hidden"],
  });
  page.window.addEventListener("resize", publish);
  publish();
  return () => {
    observer.disconnect();
    page.window.removeEventListener("resize", publish);
  };
}
