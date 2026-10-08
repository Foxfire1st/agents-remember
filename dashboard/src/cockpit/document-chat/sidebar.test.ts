import { afterEach, describe, expect, it } from "vitest";
import { installBridge, type BridgeClient } from "../../../../mcp/src/agents_remember/package_data/paseo_plugin/client/bridge";
import { watchAppWrites } from "../../../../mcp/src/agents_remember/package_data/paseo_plugin/client/load";
import { PANEL_STATE_KEY, EMBED_LOOK } from "../../../../mcp/src/agents_remember/package_data/paseo_plugin/client/look";
import { documentSidebar, setEmbedPageMode } from "../../../../mcp/src/agents_remember/package_data/paseo_plugin/client/sidebar";
import { DASHBOARD, emptyHierarchyClient, newTab, pageLoad, panelState, sidebarOf } from "../../test/paseoPluginPage";

afterEach(() => { document.head.innerHTML = ""; document.body.innerHTML = ""; });

describe("document-only sidebar isolation", () => {
  it("collapses again on native changes, hides the control and never stores the rail's closed choice", async () => {
    const tab = newTab(EMBED_LOOK, true);
    const load = pageLoad(tab);
    const stopWrites = watchAppWrites(load.page);
    const button = document.createElement("button");
    button.dataset.testid = "menu-button";
    button.setAttribute("aria-expanded", "true");
    let clicks = 0;
    button.onclick = () => { clicks++; button.setAttribute("aria-expanded", "false"); load.appWrites(PANEL_STATE_KEY, panelState(false)); };
    document.body.appendChild(button);
    const stop = documentSidebar(load.page);
    expect(clicks).toBe(1);
    expect(getComputedStyle(button).display).toBe("none");
    expect(sidebarOf(tab)).toBe(true);
    button.setAttribute("aria-expanded", "true");
    await new Promise<void>((resolve) => queueMicrotask(resolve));
    expect(clicks).toBe(2);
    expect(sidebarOf(tab)).toBe(true);
    stop();
    // A plugin reevaluation in this rail retains its page mode while observers are replaced.
    load.appWrites(PANEL_STATE_KEY, panelState(false));
    expect(sidebarOf(tab)).toBe(true);
    setEmbedPageMode(load.page, "chats");
    stopWrites();
    expect(getComputedStyle(button).display).not.toBe("none");
    load.appWrites(PANEL_STATE_KEY, panelState(false));
    expect(sidebarOf(tab)).toBe(false);
  });

  it("accepts page mode only from the listed parent and leaves Chats controls and storage alone", () => {
    const load = pageLoad(newTab(EMBED_LOOK));
    const base = emptyHierarchyClient();
    const directory = base.paseo;
    const client: BridgeClient = { ...base, paseo: { ...directory,
      agents: { ...directory.agents, ref: () => ({ refresh: async () => null, archivedAt: null, workspaceId: null }) },
      workspaces: { ...directory.workspaces, ref: () => ({ refresh: async () => null }) },
    }, openScreen: () => {} };
    const stop = installBridge(client, load.page, DASHBOARD);
    load.receive({ type: "ar.page", page: "document" }, { origin: "https://other.example" });
    expect(document.getElementById("ar-document-sidebar")).toBeNull();
    load.receive({ type: "ar.page", page: "document" });
    expect(document.getElementById("ar-document-sidebar")).not.toBeNull();
    load.receive({ type: "ar.page", page: "chats" });
    expect(document.getElementById("ar-document-sidebar")).toBeNull();
    stop();
  });
});
