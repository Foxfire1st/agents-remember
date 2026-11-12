import { PANEL_STATE_KEY } from "./look";
import type { PluginPage } from "./page";

const STATE = "__arDocumentSidebar";
const MODE = "__arEmbedPageMode";
const STYLE = "ar-document-sidebar";
const MENU = '[data-testid="menu-button"]';

/** The rail's in-memory collapse must never become another page's stored choice. */
export function preserveSidebarChoice(page: PluginPage, key: string, value: string): string {
  if (page.state[MODE] !== "document" || key !== PANEL_STATE_KEY) return value;
  try {
    const stored = JSON.parse(page.localStorage.getItem(key) ?? "null");
    const next = JSON.parse(value);
    if (!next?.state?.desktop) return value;
    const choice = stored?.state?.desktop?.agentListOpen;
    if (typeof choice === "boolean") next.state.desktop.agentListOpen = choice;
    else delete next.state.desktop.agentListOpen;
    return JSON.stringify(next);
  } catch { return value; }
}

/** Only invoked after the parent has been verified. No shared sidebar state is written. */
export function documentSidebar(page: PluginPage): () => void {
  page.state[STATE] = true;
  setEmbedPageMode(page, "document");
  let style = page.document.getElementById(STYLE);
  if (!style) {
    style = page.document.createElement("style");
    style.id = STYLE;
    style.textContent = MENU + " { display: none !important; }";
    page.document.head.appendChild(style);
  }
  let collapsing = false;
  const collapse = () => {
    const toggle = page.document.querySelector(MENU);
    if (collapsing || toggle?.getAttribute("aria-expanded") !== "true") return;
    collapsing = true;
    try { toggle.click(); } finally { collapsing = false; }
  };
  const observer = new page.window.MutationObserver(collapse);
  observer.observe(page.document.documentElement, { subtree: true, childList: true, attributes: true, attributeFilter: ["aria-expanded"] });
  collapse();
  return () => { observer.disconnect(); style.remove(); delete page.state[STATE]; };
}

export function setEmbedPageMode(page: PluginPage, mode: "document" | "chats"): void {
  page.state[MODE] = mode;
}

export function clearEmbedPageMode(page: PluginPage): void {
  delete page.state[MODE];
}

export function isDocumentPage(page: PluginPage): boolean {
  return page.state[MODE] === "document" || new URL(page.location.href).searchParams.get("arPage") === "document";
}
