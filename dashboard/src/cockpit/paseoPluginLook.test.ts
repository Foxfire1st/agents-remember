// The look part of the AR plugin's client code for the Paseo runtime (PNT-R05): what is stored
// for the frame, what is remembered for a standalone tab, and who wrote the app's stores last.
// The source lives with the plugin (mcp/src/agents_remember/package_data/paseo_plugin/client/
// look.ts) and imports nothing from Paseo; this file is where the repository's gates run it.
import { readFileSync } from "node:fs";
import path from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  APP_SETTINGS_KEY,
  AR_FONT_STACK,
  EMBED_LOOK,
  PANEL_STATE_KEY,
  STANDALONE_LOOK_KEY,
  applyEmbedLook,
  recordAppWrite,
  restoreStandaloneLook,
} from "../../../mcp/src/agents_remember/package_data/paseo_plugin/client/look";
import {
  CYCLED_IN_FRAME,
  DEFAULT_LOOK,
  HAND_PICKED,
  MemoryStorage,
  OWN_LOOK,
  memoryOf,
  newTab,
  panelState,
  paseoWrites,
  recordedSidebar,
  recordedWrite,
  settingsOf,
  sidebarOf,
  type Tab,
} from "../test/paseoPluginPage";

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  document.head.innerHTML = "";
  document.body.innerHTML = "";
  localStorage.clear();
});

describe("the look stored for the frame and the look remembered for a standalone tab", () => {
  it("uses the dashboard's own font stack", () => {
    const tokens = readFileSync(path.join(process.cwd(), "src/styles/tokens.css"), "utf8");
    expect(/--font-mono:\s*([^;]+);/.exec(tokens)?.[1]).toBe(AR_FONT_STACK);
    expect(EMBED_LOOK).toEqual({
      theme: "plugin",
      pluginThemeId: "ar-plugin/theme/agents-remember",
      uiFontFamily: AR_FONT_STACK,
      monoFontFamily: AR_FONT_STACK,
    });
  });

  it("remembers the user's look and open or closed sidebar before storing the AR look", () => {
    for (const open of [false, true]) {
      const tab = newTab(OWN_LOOK, open);
      expect(applyEmbedLook(tab.local)).toEqual(["theme", "pluginThemeId", "uiFontFamily", "monoFontFamily"]);
      expect(settingsOf(tab)).toEqual({ ...EMBED_LOOK, language: "system" });
      // Storing a theme does not toggle the native sidebar; only its native button does that.
      expect(sidebarOf(tab)).toBe(open);
      const remembered = {
        appSettings: OWN_LOOK,
        agentListOpen: open,
        stored: "frame",
        seen: EMBED_LOOK,
        at: Date.now(),
        sidebar: { stored: "user", seen: open },
      };
      expect(memoryOf(tab)).toEqual(remembered);
      expect(applyEmbedLook(tab.local)).toEqual([]);
      expect(memoryOf(tab)).toEqual(remembered);
      // The native initial collapse does not overwrite the standalone choice.
      recordedSidebar(tab, false, false);
      expect(restoreStandaloneLook(tab.local).changed).toBe(true);
      expect(sidebarOf(tab)).toBe(open);
      expect(settingsOf(tab)).toEqual({ ...OWN_LOOK, language: "system" });
    }
  });

  it("never overwrites the user's look before it is kept", () => {
    const tab = newTab(OWN_LOOK);
    const put = tab.local.setItem.bind(tab.local);
    tab.local.setItem = (key, value) => {
      if (key === STANDALONE_LOOK_KEY) throw new Error("the storage is full");
      put(key, value);
    };
    expect(applyEmbedLook(tab.local)).toEqual([]);
    expect(settingsOf(tab)).toEqual({ ...OWN_LOOK, language: "system" });
    expect(sidebarOf(tab)).toBe(true);
  });

  it("gives a standalone tab the user's look back, keys the user never had included", () => {
    const tab = newTab({ theme: "dark" });
    applyEmbedLook(tab.local);

    expect(restoreStandaloneLook(tab.local)).toMatchObject({ changed: true, usersLook: false });
    expect(settingsOf(tab)).toEqual({ theme: "dark", language: "system" });
    expect(sidebarOf(tab)).toBe(true);
    expect(memoryOf(tab)).toEqual({
      appSettings: { theme: "dark" },
      agentListOpen: true,
      stored: "user",
      seen: { theme: "dark" },
      at: Date.now(),
      sidebar: { stored: "user", seen: true },
    });
    // Nothing left to put back: the page that loads now runs the user's look.
    expect(restoreStandaloneLook(tab.local)).toMatchObject({ changed: false, usersLook: true });
    // A tab that never shared storage with a frame has nothing remembered and is left alone.
    const untouched = newTab(OWN_LOOK);
    expect(restoreStandaloneLook(untouched.local)).toMatchObject({ changed: false, usersLook: true });
    expect(memoryOf(untouched)).toBeNull();
    expect(settingsOf(untouched)).toEqual({ ...OWN_LOOK, language: "system" });
  });

  it("puts the default sidebar back when the app had stored none before the frame closed it", () => {
    // A first visit: the frame's look is stored before the app has written any sidebar state.
    const tab = newTab(DEFAULT_LOOK);
    tab.local.removeItem(PANEL_STATE_KEY);
    applyEmbedLook(tab.local);
    expect(memoryOf(tab)?.agentListOpen).toBeNull();
    // The frame then closes the sidebar through the app's toggle, and the app stores that.
    recordedSidebar(tab, false, false);
    expect(memoryOf(tab)).toMatchObject({ agentListOpen: null, sidebar: { stored: "frame", seen: false } });

    expect(restoreStandaloneLook(tab.local).changed).toBe(true);
    expect(sidebarOf(tab)).toBe(true);
    expect(settingsOf(tab)).toEqual({ ...DEFAULT_LOOK, language: "system" });
  });

  it("records a write of the app inside the frame as the frame's and never remembers it", () => {
    const everyKeyChanged = { theme: "light", pluginThemeId: EMBED_LOOK.pluginThemeId, uiFontFamily: "Courier New", monoFontFamily: "Courier New" };
    // The last one holds nothing of the AR look any more: only the record says the frame wrote it.
    for (const written of [CYCLED_IN_FRAME, { ...EMBED_LOOK, uiFontFamily: '"Courier New", monospace' }, everyKeyChanged]) {
      // A standalone tab took its look back (the mark says "user"); then the user changes a look
      // setting INSIDE the frame, whose page writes its whole AR look with that one change.
      const tab = newTab(OWN_LOOK);
      applyEmbedLook(tab.local);
      restoreStandaloneLook(tab.local);
      recordedWrite(tab, false, written);
      expect(memoryOf(tab)).toMatchObject({ appSettings: OWN_LOOK, stored: "frame" });

      // Frame load first: the AR look is stored again, the user's look is still remembered.
      const frameFirst: Tab = { local: Object.assign(new MemoryStorage(), { items: new Map(tab.local.items) }), session: tab.session };
      expect(applyEmbedLook(frameFirst.local).length).toBeGreaterThan(0);
      expect(memoryOf(frameFirst)).toMatchObject({ appSettings: OWN_LOOK, stored: "frame" });
      expect(restoreStandaloneLook(frameFirst.local).changed).toBe(true);
      expect(settingsOf(frameFirst)).toEqual({ ...OWN_LOOK, language: "system" });

      // Standalone load first: the whole look goes back to the user's, nothing of the AR look stays.
      expect(restoreStandaloneLook(tab.local)).toMatchObject({ changed: true, usersLook: false });
      expect(settingsOf(tab)).toEqual({ ...OWN_LOOK, language: "system" });
      expect(applyEmbedLook(tab.local)).toHaveLength(4);
      expect(memoryOf(tab)).toMatchObject({ appSettings: OWN_LOOK, stored: "frame" });
    }
  });

  it("records a write of the app in a standalone page as the user's, and remembers it", () => {
    const tab = newTab(DEFAULT_LOOK);
    applyEmbedLook(tab.local);
    // A standalone tab loaded before the frame runs the user's look; the user types a font and
    // Paseo writes that page's whole look over the AR look in the storage.
    const usersNewLook = { ...DEFAULT_LOOK, uiFontFamily: "Verdana" };
    recordedWrite(tab, true, usersNewLook);
    expect(memoryOf(tab)).toMatchObject({ appSettings: usersNewLook, stored: "user", seen: usersNewLook });

    // The tab's next load leaves both the user's settings and untouched sidebar alone.
    expect(restoreStandaloneLook(tab.local)).toMatchObject({ changed: false, usersLook: true });
    expect(settingsOf(tab)).toEqual({ ...usersNewLook, language: "system" });
    expect(sidebarOf(tab)).toBe(true);
    expect(restoreStandaloneLook(tab.local)).toMatchObject({ changed: false, usersLook: true });

    // The other order: the frame loads first and remembers the look that write left.
    const other = newTab(DEFAULT_LOOK);
    applyEmbedLook(other.local);
    recordedWrite(other, true, usersNewLook);
    expect(applyEmbedLook(other.local)).toHaveLength(4);
    expect(memoryOf(other)).toMatchObject({ appSettings: usersNewLook, agentListOpen: true, stored: "frame" });
    expect(settingsOf(other)).toEqual({ ...EMBED_LOOK, language: "system" });
  });

  it("keeps the Agents Remember theme a user chose by hand in a standalone tab", () => {
    const tab = newTab({ ...DEFAULT_LOOK, uiFontFamily: "Georgia" });
    applyEmbedLook(tab.local);
    // Mark "user", and a store that is partly the AR look: a standalone page wrote it.
    recordedWrite(tab, true, HAND_PICKED);

    // At a standalone load nothing of it is taken away.
    expect(restoreStandaloneLook(tab.local).usersLook).toBe(true);
    expect(settingsOf(tab)).toEqual({ ...HAND_PICKED, language: "system" });
    // The record decides, not what the store or the memory holds: with the memory set to
    // something else by hand, the proven store is still left alone and remembered.
    tab.local.put(STANDALONE_LOOK_KEY, { ...memoryOf(tab), appSettings: OWN_LOOK });
    expect(restoreStandaloneLook(tab.local).usersLook).toBe(true);
    expect(settingsOf(tab)).toEqual({ ...HAND_PICKED, language: "system" });
    expect(memoryOf(tab)).toMatchObject({ appSettings: HAND_PICKED, stored: "user" });
    // At a frame load it is remembered as the user's look, although it holds the AR theme.
    expect(applyEmbedLook(tab.local)).toEqual(["uiFontFamily", "monoFontFamily"]);
    expect(memoryOf(tab)).toMatchObject({ appSettings: HAND_PICKED, stored: "frame" });
    // And the standalone tab gets it back, with the user's font.
    expect(restoreStandaloneLook(tab.local)).toMatchObject({ changed: true, usersLook: false });
    expect(settingsOf(tab)).toEqual({ ...HAND_PICKED, language: "system" });

    // A font typed there afterwards survives, also once the user has switched to another theme
    // (the app keeps the plugin theme's id then).
    applyEmbedLook(tab.local);
    for (const next of [{ ...HAND_PICKED, uiFontFamily: "Verdana" }, { ...HAND_PICKED, theme: "dark", uiFontFamily: "Tahoma" }]) {
      recordedWrite(tab, true, next);
      expect(restoreStandaloneLook(tab.local).usersLook).toBe(true);
      expect(settingsOf(tab)).toEqual({ ...next, language: "system" });
      applyEmbedLook(tab.local);
      expect(memoryOf(tab)?.appSettings).toEqual(next);
    }
  });

  it("goes by content when the record does not hold: a store written while no plugin observed it", () => {
    // Mark "user" and a partly-AR store nobody recorded: a frame page wrote it (it kept AR fonts).
    const frameWrote = newTab(OWN_LOOK);
    applyEmbedLook(frameWrote.local);
    restoreStandaloneLook(frameWrote.local);
    paseoWrites(frameWrote, CYCLED_IN_FRAME);
    expect(memoryOf(frameWrote)?.stored).toBe("user");
    // At a standalone load it is put back to the user's look ...
    const standaloneFirst: Tab = { local: Object.assign(new MemoryStorage(), { items: new Map(frameWrote.local.items) }), session: frameWrote.session };
    expect(restoreStandaloneLook(standaloneFirst.local)).toMatchObject({ changed: true, usersLook: false });
    expect(settingsOf(standaloneFirst)).toEqual({ ...OWN_LOOK, language: "system" });
    // ... and at a frame load it is not remembered.
    expect(applyEmbedLook(frameWrote.local)).toEqual(["theme"]);
    expect(memoryOf(frameWrote)).toMatchObject({ appSettings: OWN_LOOK, stored: "frame" });

    // One AR value is enough to make a store the frame's: either font alone, or the AR theme alone.
    for (const partly of [
      { ...OWN_LOOK, monoFontFamily: AR_FONT_STACK },
      { ...OWN_LOOK, uiFontFamily: AR_FONT_STACK },
      { ...OWN_LOOK, theme: "plugin", pluginThemeId: EMBED_LOOK.pluginThemeId },
    ]) {
      const tab = newTab(OWN_LOOK);
      applyEmbedLook(tab.local);
      restoreStandaloneLook(tab.local);
      paseoWrites(tab, partly);
      expect(restoreStandaloneLook(tab.local)).toMatchObject({ changed: true, usersLook: false });
      expect(settingsOf(tab)).toEqual({ ...OWN_LOOK, language: "system" });
    }

    // Mark "frame" and a store with nothing of the AR look: a standalone page wrote it. The theme
    // id alone does not make it the AR look: the app keeps the id when another theme is chosen.
    // Nor does the plugin theme being selected, when the id is another plugin's theme.
    for (const usersNewLook of [
      { ...DEFAULT_LOOK, theme: "light", uiFontFamily: "Verdana" },
      { theme: "dark", pluginThemeId: EMBED_LOOK.pluginThemeId, uiFontFamily: "Tahoma", monoFontFamily: "" },
      { theme: "plugin", pluginThemeId: "other-plugin/theme/solar", uiFontFamily: "Tahoma", monoFontFamily: "" },
    ]) {
      const userWrote = newTab(DEFAULT_LOOK, false);
      applyEmbedLook(userWrote.local);
      paseoWrites(userWrote, usersNewLook);
      const frameFirst: Tab = { local: Object.assign(new MemoryStorage(), { items: new Map(userWrote.local.items) }), session: userWrote.session };
      expect(restoreStandaloneLook(userWrote.local)).toMatchObject({ changed: false, usersLook: true });
      expect(settingsOf(userWrote)).toEqual({ ...usersNewLook, language: "system" });
      expect(memoryOf(userWrote)).toMatchObject({ appSettings: usersNewLook, stored: "user" });
      expect(applyEmbedLook(frameFirst.local).length).toBeGreaterThan(0);
      expect(memoryOf(frameFirst)).toMatchObject({ appSettings: usersNewLook, stored: "frame" });
    }
  });

  it("goes by content for the record of an earlier plugin version, which has no proof in it", () => {
    const legacy = (stored: string | undefined, look: Record<string, unknown>, sidebarOpen: boolean) => {
      const tab = newTab(look, sidebarOpen);
      tab.local.put(STANDALONE_LOOK_KEY, { appSettings: OWN_LOOK, agentListOpen: true, ...(stored ? { stored } : {}) });
      return tab;
    };
    // Mark "frame", settings a standalone page wrote: adopted, and the sidebar the frame closed is put back.
    const adopted = legacy("frame", { ...DEFAULT_LOOK, uiFontFamily: "Verdana" }, false);
    expect(restoreStandaloneLook(adopted.local)).toMatchObject({ changed: true, usersLook: true });
    expect(settingsOf(adopted)).toEqual({ ...DEFAULT_LOOK, uiFontFamily: "Verdana", language: "system" });
    expect(sidebarOf(adopted)).toBe(true);
    expect(memoryOf(adopted)).toMatchObject({ appSettings: { ...DEFAULT_LOOK, uiFontFamily: "Verdana" }, stored: "user" });
    // Mark "user" (or none at all), settings partly the AR look: restored, the sidebar left alone.
    for (const mark of ["user", undefined]) {
      const restored = legacy(mark, CYCLED_IN_FRAME, false);
      expect(restoreStandaloneLook(restored.local)).toMatchObject({ changed: true, usersLook: false });
      expect(settingsOf(restored)).toEqual({ ...OWN_LOOK, language: "system" });
      expect(sidebarOf(restored)).toBe(false);
      // The same record at a frame load: the partly-AR store is not remembered.
      const framed = legacy(mark, CYCLED_IN_FRAME, false);
      expect(applyEmbedLook(framed.local)).toEqual(["theme"]);
      expect(memoryOf(framed)).toMatchObject({ appSettings: OWN_LOOK, agentListOpen: false, stored: "frame" });
    }
  });

  it("restores again when a frame that stayed open stored the AR look after the tab took its own back", () => {
    const tab = newTab(OWN_LOOK);
    applyEmbedLook(tab.local);
    restoreStandaloneLook(tab.local);
    recordedWrite(tab, false, EMBED_LOOK);

    expect(restoreStandaloneLook(tab.local).changed).toBe(true);
    expect(settingsOf(tab)).toEqual({ ...OWN_LOOK, language: "system" });
    // And the frame's next load knows the storage is its own again without forgetting the user's look.
    recordedWrite(tab, false, EMBED_LOOK);
    expect(applyEmbedLook(tab.local)).toEqual([]);
    expect(memoryOf(tab)).toMatchObject({ appSettings: OWN_LOOK, agentListOpen: true, stored: "frame" });
  });

  it("records who wrote the sidebar's state: the user's is kept, the frame's is put back", () => {
    const tab = newTab(OWN_LOOK);
    applyEmbedLook(tab.local);
    // The native toggle opens the frame's sidebar: its state is recorded, not remembered.
    recordedSidebar(tab, false, true);
    expect(memoryOf(tab)).toMatchObject({ agentListOpen: true, sidebar: { stored: "frame", seen: true } });
    recordedSidebar(tab, false, false);
    expect(restoreStandaloneLook(tab.local).changed).toBe(true);
    expect(sidebarOf(tab)).toBe(true);

    // The user closes the sidebar inside the frame, then chooses closed in the standalone tab.
    applyEmbedLook(tab.local);
    recordedSidebar(tab, false, false);
    expect(memoryOf(tab)).toMatchObject({ agentListOpen: true, sidebar: { stored: "frame", seen: false } });
    recordedSidebar(tab, true, false);
    expect(memoryOf(tab)).toMatchObject({ agentListOpen: false, sidebar: { stored: "user", seen: false } });
    // That tab's next load takes the look back and leaves the sidebar closed, as the user left it.
    restoreStandaloneLook(tab.local);
    expect(sidebarOf(tab)).toBe(false);
    // And the frame's next load remembers "closed" as the user's sidebar.
    applyEmbedLook(tab.local);
    expect(memoryOf(tab)?.agentListOpen).toBe(false);

    // A sidebar state nobody recorded goes with the settings' mark. Mark "user": a page of the
    // user's look wrote it (the user reopened the sidebar while the plugin was off). It is left
    // alone and remembered as the user's.
    restoreStandaloneLook(tab.local);
    expect(memoryOf(tab)).toMatchObject({ stored: "user", agentListOpen: false, sidebar: { stored: "user", seen: false } });
    tab.local.put(PANEL_STATE_KEY, panelState(true));
    expect(restoreStandaloneLook(tab.local).changed).toBe(false);
    expect(sidebarOf(tab)).toBe(true);
    expect(memoryOf(tab)).toMatchObject({ agentListOpen: true, sidebar: { stored: "user", seen: true } });
    applyEmbedLook(tab.local);
    expect(memoryOf(tab)).toMatchObject({ agentListOpen: true, sidebar: { stored: "user", seen: true } });

    // Mark "frame": the frame's is put back.
    tab.local.put(PANEL_STATE_KEY, panelState(true));
    tab.local.put(STANDALONE_LOOK_KEY, { ...memoryOf(tab), agentListOpen: false, sidebar: { stored: "user", seen: false } });
    expect(restoreStandaloneLook(tab.local).changed).toBe(true);
    expect(sidebarOf(tab)).toBe(false);
  });

  it("keeps the time of the last write of the stored settings", () => {
    const tab = newTab(OWN_LOOK);
    const t0 = Date.now();
    applyEmbedLook(tab.local);
    expect(memoryOf(tab)?.at).toBe(t0);
    // A frame load that finds its own look writes nothing and keeps the time.
    vi.advanceTimersByTime(1000);
    applyEmbedLook(tab.local);
    expect(memoryOf(tab)?.at).toBe(t0);
    // Putting the user's look back is a write; the answer names the time before it.
    expect(restoreStandaloneLook(tab.local)).toEqual({ changed: true, usersLook: false, at: t0 });
    expect(memoryOf(tab)?.at).toBe(t0 + 1000);
    // Nothing to put back: no write, the time stays and is reported.
    vi.advanceTimersByTime(1000);
    expect(restoreStandaloneLook(tab.local)).toEqual({ changed: false, usersLook: true, at: t0 + 1000 });
    expect(memoryOf(tab)?.at).toBe(t0 + 1000);
    // A recorded settings write of the app is a write, from either kind of page; a panel write is not.
    recordedWrite(tab, true, { ...OWN_LOOK, theme: "light" });
    expect(memoryOf(tab)?.at).toBe(t0 + 2000);
    vi.advanceTimersByTime(1000);
    recordedSidebar(tab, true, false);
    expect(memoryOf(tab)?.at).toBe(t0 + 2000);
    recordedWrite(tab, false, CYCLED_IN_FRAME);
    expect(memoryOf(tab)?.at).toBe(t0 + 3000);
    // A record without the time (an earlier version's) says so.
    tab.local.put(STANDALONE_LOOK_KEY, { appSettings: OWN_LOOK, agentListOpen: true, stored: "user", seen: OWN_LOOK });
    paseoWrites(tab, OWN_LOOK);
    expect(restoreStandaloneLook(tab.local)).toEqual({ changed: false, usersLook: true, at: null });
    expect(restoreStandaloneLook(newTab(OWN_LOOK).local)).toEqual({ changed: false, usersLook: true, at: null });
  });

  it("does not say a page runs the user's look when the record could not be written", () => {
    const tab = newTab(OWN_LOOK);
    applyEmbedLook(tab.local);
    const put = tab.local.setItem.bind(tab.local);
    tab.local.setItem = (key, value) => {
      if (key === STANDALONE_LOOK_KEY) throw new Error("the storage is full");
      put(key, value);
    };
    expect(restoreStandaloneLook(tab.local)).toEqual({ changed: false, usersLook: false, at: null });
  });

  it("records nothing for other keys, unreadable values, or a storage no frame ever shared", () => {
    const fresh = newTab(OWN_LOOK);
    recordedWrite(fresh, true, { ...OWN_LOOK, theme: "light" });
    recordedWrite(fresh, false, CYCLED_IN_FRAME);
    expect(memoryOf(fresh)).toBeNull();

    const tab = newTab(OWN_LOOK);
    applyEmbedLook(tab.local);
    const before = memoryOf(tab);
    recordAppWrite(tab.local, true, "sidebar-view", JSON.stringify({ state: {} }));
    recordAppWrite(tab.local, true, APP_SETTINGS_KEY, "{not json");
    expect(memoryOf(tab)).toEqual(before);
    // A panel state without the sidebar flag still records who wrote it.
    recordAppWrite(tab.local, false, PANEL_STATE_KEY, JSON.stringify({ state: {} }));
    expect(memoryOf(tab)).toMatchObject({ agentListOpen: true, sidebar: { stored: "frame", seen: null } });
  });
});
