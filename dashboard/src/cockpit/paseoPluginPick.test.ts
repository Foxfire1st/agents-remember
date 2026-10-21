// The tray's part of the AR plugin's control channel (MIK-R188 rules 3–5, evidence 2): the
// "⊕ add" offer on highlighted chat text and its pick, copy on highlight, the one notice for an
// unreadable chat page, the message box's rectangle and the tray's reserved height. Its source
// is client/pick.ts beside client/bridge.ts; the DOM below is the pinned Paseo 0.11.1 web build's
// (index-32cec38dd4de4493a1d5392537c1e464.js): the scroll root `agent-chat-scroll`, the rows'
// `data-history-row-id` and `data-message-id`, the message nodes' test ids and
// `message-input-root`; and each row's own record, the stream item React DOM 19.1.0 keeps in the
// row element's props, with the time the host's timeline gave it.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { installBridge, type BridgeClient } from "../../../mcp/src/agents_remember/package_data/paseo_plugin/client/bridge";
import {
  COMPOSER_GRACE_MS,
  PICK_OFFER_ID,
  TRAY_SPACE_STYLE_ID,
} from "../../../mcp/src/agents_remember/package_data/paseo_plugin/client/pick";
import { DAEMON, DASHBOARD, emptyHierarchyClient, newTab, pageLoad, type Tab } from "../test/paseoPluginPage";

const LEAF_URL = DAEMON + "/h/srv_test/workspace/wks_leaf";
const MANAGER = "agent-manager";
const OLDER = "The gate runs on a clean copy.";
const SAID = "The review page reads the change set from its changes.";
const ASKED = "Check where the tray docks.";
const SENDING = "And the File Viewer?";
/** A tool call's row id in the pinned build: `agent_tool_turn:<turnId>/<callId>`. */
const TOOL_ROW = "agent_tool_turn:turn_8/toolu_3";
/** The newest page of the host's timeline of the Manager: it names neither the older message nor the tool call. */
const TIMELINE = {
  entries: [
    { provider: "claude", item: { type: "user_message", text: ASKED, clientMessageId: "cm_41", messageId: "um_9" }, timestamp: "2026-10-19T12:04:00.000Z" },
    { provider: "claude", item: { type: "assistant_message", text: SAID, messageId: "msg_7f2" }, timestamp: "2026-10-19T12:05:00.000Z" },
  ],
  hasOlder: true,
  startCursor: { epoch: "ep_1", seq: 1800 },
};
/**
 * The pinned build's stream items of the drawn rows: a row of the history carries the timeline
 * entry's time and cursor (`hydrateStreamState`); a message the developer just sent has no
 * cursor until the host records it.
 */
const ITEMS: Record<string, Record<string, unknown>> = {
  msg_old: { kind: "assistant_message", id: "msg_old", messageId: "msg_old", text: OLDER, timestamp: new Date("2026-10-12T08:15:00.000Z"), timelineCursor: { epoch: "ep_1", seq: 3 } },
  cm_41: { kind: "user_message", id: "cm_41", clientMessageId: "cm_41", messageId: "um_9", text: ASKED, timestamp: new Date("2026-10-19T12:04:00.000Z"), timelineCursor: { epoch: "ep_1", seq: 1838 } },
  [TOOL_ROW]: {
    kind: "tool_call",
    id: TOOL_ROW,
    turnId: "turn_8",
    timestamp: new Date("2026-10-19T12:04:30.000Z"),
    timelineCursor: { epoch: "ep_1", seq: 1839 },
    payload: { source: "agent", data: { callId: "toolu_3", name: "Bash", status: "completed" } },
  },
  msg_7f2: { kind: "assistant_message", id: "msg_7f2", messageId: "msg_7f2", text: SAID, timestamp: new Date("2026-10-19T12:05:00.000Z"), timelineCursor: { epoch: "ep_1", seq: 1840 } },
  cm_new: { kind: "user_message", id: "cm_new", clientMessageId: "cm_new", text: SENDING, timestamp: new Date("2026-10-19T12:06:00.000Z") },
};
/** React DOM 19.1.0 keeps a host element's props under `__reactProps$` and a random suffix. */
const REACT_PROPS = "__reactProps$k3x9q2";

/** The row element's props as React draws the row: its attributes, and its row component with the stream item. */
function record(node: Element, item: Record<string, unknown>) {
  const props = { "data-history-row-id": node.getAttribute("data-history-row-id"), "data-message-id": node.getAttribute("data-message-id"), style: {} };
  Object.assign(node, { [REACT_PROPS]: { ...props, children: { props: { item, index: 0 } } } });
}

/** The chat as the pinned web build draws it, with Paseo's message box below it. */
function drawChat(options: { rowIds?: boolean; records?: boolean; messageBox?: boolean; tab?: string | null; gap?: boolean } = {}) {
  const row = (id: string) => (options.rowIds === false ? "" : ` data-message-id="${id}"`);
  document.body.innerHTML =
    (options.tab === null ? "" : `<button data-testid="workspace-tab-agent_${options.tab ?? MANAGER}" aria-selected="true"></button>`) +
    `<div data-testid="agent-chat-scroll" id="agent-chat-scroll-web-dom-scroll"><div>` +
    // Text in the scroll root outside every row (the older-history slot, a separator).
    (options.gap ? `<div data-testid="older-history-slot"><p id="gap">Load older messages</p></div>` : "") +
    // A message a week back: the developer scrolled the history far up.
    `<div data-history-row-id="msg_old"${row("msg_old")}><div data-testid="assistant-message"><p id="older">${OLDER}</p></div></div>` +
    `<div data-history-row-id="cm_41"${row("cm_41")}><div data-testid="user-message"><p id="asked">${ASKED}</p></div></div>` +
    `<div data-history-row-id="${TOOL_ROW}"${row(TOOL_ROW)}><div data-testid="tool-call-badge"><p id="tool">npm run typecheck</p></div></div>` +
    `<div data-history-row-id="msg_7f2"${row("msg_7f2")}><div data-testid="assistant-message"><p id="said">${SAID}</p></div></div>` +
    `<div data-history-row-id="cm_new"${row("cm_new")}><div data-testid="user-message"><p id="sending">${SENDING}</p></div></div>` +
    `</div></div>` +
    (options.messageBox === false ? "" : `<div data-testid="message-input-root"><textarea aria-label="Message"></textarea></div>`) +
    `<header><p id="title">Manager</p></header>`;
  if (options.records !== false) {
    for (const node of document.querySelectorAll("[data-history-row-id]")) record(node, ITEMS[node.getAttribute("data-history-row-id")!]);
  }
  const box = { top: 812, height: 64, width: 400 };
  for (const node of document.querySelectorAll("button, #agent-chat-scroll-web-dom-scroll")) {
    Object.assign(node, { checkVisibility: () => true, getBoundingClientRect: () => ({ top: 0, width: 400, height: 28 }) });
  }
  const input = document.querySelector('[data-testid="message-input-root"]');
  if (input) Object.assign(input, { getBoundingClientRect: () => ({ ...box }) });
  return box;
}

function highlight(id: string): Range {
  const selection = document.getSelection()!;
  selection.removeAllRanges();
  const range = document.createRange();
  range.selectNodeContents(document.getElementById(id)!.firstChild!);
  selection.addRange(range);
  return range;
}

const release = (target: Element) => target.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true }));

function seedLayout(tab: Tab, focusedTabId: string) {
  tab.local.put("workspace-layout-state", {
    state: {
      layoutByWorkspace: {
        "srv_test:wks_leaf": {
          root: { kind: "group", group: { id: "workspace-root", children: [{ kind: "pane", pane: { id: "main", tabIds: [focusedTabId], focusedTabId, tabs: [] } }] } },
          focusedPaneId: "main",
        },
      },
    },
    version: 2,
  });
}

const stops: Array<() => void> = [];

/** A trusted frame page on the leaf workspace with the bridge installed, and what it posts. */
function framedChat(tab: Tab = newTab()) {
  const load = pageLoad(tab, { href: LEAF_URL });
  const resizeListeners = new Set<() => void>();
  const resizeObservers: Array<{ callback: () => void; targets: unknown[] }> = [];
  class FakeResizeObserver {
    targets: unknown[] = [];
    constructor(readonly callback: () => void) {
      resizeObservers.push(this);
    }
    observe(target: unknown) {
      this.targets.push(target);
    }
    disconnect() {
      this.targets = [];
    }
  }
  const execCommand = vi.fn(() => true);
  Object.assign(load.page.document, { body: document.body, getSelection: () => document.getSelection(), execCommand });
  const window_ = load.page.window as Record<string, unknown>;
  const addMessage = window_.addEventListener as (type: string, listener: () => void) => void;
  const removeMessage = window_.removeEventListener as (type: string, listener: () => void) => void;
  Object.assign(window_, {
    addEventListener: (type: string, listener: () => void) => (type === "resize" ? resizeListeners.add(listener) : addMessage(type, listener)),
    removeEventListener: (type: string, listener: () => void) => (type === "resize" ? resizeListeners.delete(listener) : removeMessage(type, listener)),
    // The page's timers are the test's fake clock (`vi.useFakeTimers` in beforeEach): its grace
    // passes only when a case advances that clock.
    setTimeout: window.setTimeout.bind(window),
    clearTimeout: window.clearTimeout.bind(window),
    ResizeObserver: FakeResizeObserver,
    innerWidth: 420,
    innerHeight: 900,
  });
  // The SDK's agent handle offers the host's timeline; a pick never pages through it.
  const refetch = vi.fn(async () => TIMELINE);
  const handle = { archivedAt: null, workspaceId: "wks_leaf", refresh: async () => ({}), timeline: { refetch } };
  const catalog = emptyHierarchyClient();
  const client: BridgeClient = {
    ...catalog,
    openScreen: () => {},
    paseo: {
      ...catalog.paseo,
      agents: {
        ...catalog.paseo.agents,
        ref: () => handle,
      },
      workspaces: { ...catalog.paseo.workspaces, ref: () => ({ refresh: async () => ({}) }) },
    },
  };
  stops.push(installBridge(client, load.page, DASHBOARD));
  const sent = (type?: string) =>
    load.posted.map((entry) => entry.data).filter((data) => (type ? data.type === type : !["hierarchy", "selection", "ready"].includes(String(data.type))));
  /** The dashboard that reads picks says hello, then what the tray wants. */
  const trayDashboard = (tray: { copyOnHighlight: boolean; picks: boolean }) => {
    load.receive({ type: "ar.hello", capabilities: ["late-answer", "navigated", "on-chat", "load-failed", "pick"] });
    load.receive({ type: "ar.tray", ...tray });
  };
  return {
    ...load,
    execCommand,
    refetch,
    sent,
    trayDashboard,
    resize: () => resizeListeners.forEach((listener) => listener()),
    resizeObservers,
    window: window_,
  };
}

const flush = () => vi.advanceTimersByTimeAsync(0);
const offer = () => document.getElementById(PICK_OFFER_ID);

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  for (const stop of stops.splice(0)) stop();
  vi.restoreAllMocks();
  vi.useRealTimers();
  document.getSelection()?.removeAllRanges();
  document.head.innerHTML = "";
  document.body.innerHTML = "";
  localStorage.clear();
});

describe("chat text picks inside the frame", () => {
  it("offers ⊕ add on highlighted chat text and posts its pick with agent, message and the row's own time, keeping selection and focus", async () => {
    drawChat();
    const frame = framedChat();
    frame.trayDashboard({ copyOnHighlight: false, picks: true });
    const range = highlight("said");
    const emptied = vi.spyOn(Selection.prototype, "removeAllRanges");
    const focused = document.activeElement;

    release(document.getElementById("said")!);
    // Highlighting alone adds nothing: it offers.
    expect(frame.sent("pick")).toEqual([]);
    const control = offer()!;
    expect(control.textContent).toBe("⊕ add");
    expect(control.getAttribute("aria-label")).toBe("Add the highlighted text to the AR tray");
    expect(getComputedStyle(control).position).toBe("fixed");
    // Pressing it neither takes the focus nor the selection; its own mouseup changes nothing.
    const press = new MouseEvent("mousedown", { bubbles: true, cancelable: true });
    control.dispatchEvent(press);
    expect(press.defaultPrevented).toBe(true);
    release(control);
    expect(offer()).toBe(control);

    control.click();
    await flush();
    expect(frame.sent("pick")).toEqual([
      { source: "ar-plugin", type: "pick", text: SAID, agentId: MANAGER, messageId: "msg_7f2", messageTime: "2026-10-19T12:05:00.000Z" },
    ]);
    // The time is the one Paseo's chat holds for the row: no page of the timeline is read.
    expect(frame.refetch).not.toHaveBeenCalled();
    expect(frame.posted.every((entry) => entry.targetOrigin === DASHBOARD)).toBe(true);
    expect(offer()).toBeNull();
    // The highlight is still there, so Ctrl+C still copies it; nothing emptied it or moved focus.
    expect(document.getSelection()!.toString()).toBe(SAID);
    expect(document.getSelection()!.getRangeAt(0)).toBe(range);
    expect(emptied).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(focused);
    expect(frame.execCommand).not.toHaveBeenCalled();
  });

  it("names the agent from the stored focus in the compact layout; a user message, a tool call and a message far back each go with their own time", async () => {
    drawChat({ tab: null });
    const tab = newTab();
    seedLayout(tab, `agent_${MANAGER}`);
    const frame = framedChat(tab);
    frame.trayDashboard({ copyOnHighlight: false, picks: true });

    // A user message's row is its client message id; a tool call's row is its call; the older
    // message lies before every page the timeline's newest answer covers.
    for (const id of ["asked", "tool", "older"]) {
      highlight(id);
      release(document.getElementById(id)!);
      offer()!.click();
      await flush();
    }

    expect(frame.sent("pick")).toEqual([
      { source: "ar-plugin", type: "pick", text: ASKED, agentId: MANAGER, messageId: "cm_41", messageTime: "2026-10-19T12:04:00.000Z" },
      { source: "ar-plugin", type: "pick", text: "npm run typecheck", agentId: MANAGER, messageId: TOOL_ROW, messageTime: "2026-10-19T12:04:30.000Z" },
      { source: "ar-plugin", type: "pick", text: OLDER, agentId: MANAGER, messageId: "msg_old", messageTime: "2026-10-12T08:15:00.000Z" },
    ]);
    expect(frame.refetch).not.toHaveBeenCalled();
  });

  it("refuses by name, in place, text of a message the host has not recorded yet: no pick goes without its time", async () => {
    drawChat();
    const frame = framedChat();
    frame.trayDashboard({ copyOnHighlight: false, picks: true });

    // The message he just sent has no time in the host's timeline yet.
    highlight("sending");
    release(document.getElementById("sending")!);
    const control = offer()!;
    expect(control.textContent).toBe("⊕ add");
    control.click();
    await flush();
    expect(frame.sent("pick")).toEqual([]);
    expect(offer()).toBe(control);
    expect(control.textContent).toBe("not added: no message time");
    expect(control.getAttribute("aria-label")).toBe("Not added to the AR tray: the host has no time for this message yet");
    // A second click adds nothing either, and the highlight stays.
    control.click();
    await flush();
    expect(frame.sent("pick")).toEqual([]);
    expect(document.getSelection()!.toString()).toBe(SENDING);
    expect(frame.sent("pick-unavailable")).toEqual([]);

    // The host records it and the chat draws the row again: the same text picks with that time.
    record(document.querySelector('[data-history-row-id="cm_new"]')!, { ...ITEMS.cm_new, timelineCursor: { epoch: "ep_1", seq: 1841 } });
    highlight("sending");
    release(document.getElementById("sending")!);
    offer()!.click();
    await flush();
    expect(frame.sent("pick")).toEqual([
      { source: "ar-plugin", type: "pick", text: SENDING, agentId: MANAGER, messageId: "cm_new", messageTime: "2026-10-19T12:06:00.000Z" },
    ]);
  });

  it("gives one named notice and no offer when a Paseo update leaves the rows' own records unreadable", async () => {
    // The rows carry their ids, but no row element holds the record this build keeps.
    drawChat({ records: false, messageBox: false });
    const frame = framedChat();
    frame.trayDashboard({ copyOnHighlight: false, picks: true });
    for (const id of ["said", "asked"]) {
      highlight(id);
      release(document.getElementById(id)!);
    }
    await flush();
    expect(offer()).toBeNull();
    expect(frame.sent()).toEqual([{ source: "ar-plugin", type: "pick-unavailable", reason: "message-time-unreadable" }]);
    expect(frame.refetch).not.toHaveBeenCalled();
  });

  it("copies on highlight only when the dashboard said so, and shows nothing to a dashboard that does not read picks", async () => {
    drawChat({ messageBox: false });
    const frame = framedChat();
    // A dashboard that never said `pick` is sent nothing and shown nothing, whatever it asks.
    frame.receive({ type: "ar.hello", capabilities: ["late-answer", "navigated"] });
    frame.receive({ type: "ar.tray", copyOnHighlight: true, picks: true });
    highlight("said");
    release(document.getElementById("said")!);
    expect(frame.execCommand).not.toHaveBeenCalled();
    expect(offer()).toBeNull();

    frame.trayDashboard({ copyOnHighlight: false, picks: true });
    release(document.getElementById("said")!);
    expect(frame.execCommand).not.toHaveBeenCalled();
    expect(offer()).not.toBeNull();

    // Copy on highlight runs in the mouseup handler itself, before anything awaits.
    frame.receive({ type: "ar.tray", copyOnHighlight: true, picks: false });
    expect(offer()).toBeNull();
    release(document.getElementById("said")!);
    expect(frame.execCommand).toHaveBeenCalledTimes(1);
    expect(frame.execCommand).toHaveBeenCalledWith("copy");
    expect(offer()).toBeNull();

    // Text outside the chat is neither copied nor offered.
    frame.receive({ type: "ar.tray", copyOnHighlight: true, picks: true });
    highlight("title");
    release(document.getElementById("title")!);
    expect(frame.execCommand).toHaveBeenCalledTimes(1);
    expect(offer()).toBeNull();
    await flush();
    expect(frame.sent()).toEqual([]);
  });

  it("gives one named notice when a Paseo update leaves the chat page unreadable, and tells a new hello again", async () => {
    // The rows no longer carry the message id this build relies on.
    drawChat({ rowIds: false, messageBox: false });
    const frame = framedChat();
    frame.trayDashboard({ copyOnHighlight: false, picks: true });
    for (const id of ["said", "asked"]) {
      highlight(id);
      release(document.getElementById(id)!);
    }
    await flush();
    expect(offer()).toBeNull();
    expect(frame.sent()).toEqual([{ source: "ar-plugin", type: "pick-unavailable", reason: "message-id-unreadable" }]);

    // The dashboard forgets the frame's reports at a new ready; its next hello hears it again.
    frame.trayDashboard({ copyOnHighlight: false, picks: true });
    expect(frame.sent("pick-unavailable")).toHaveLength(2);
  });

  it("gives no offer and no notice for a highlight outside every row or with no single agent, and later highlights still pick", async () => {
    drawChat({ gap: true, messageBox: false });
    const frame = framedChat();
    frame.trayDashboard({ copyOnHighlight: false, picks: true });
    highlight("gap");
    release(document.getElementById("gap")!);
    expect(offer()).toBeNull();

    // Split panes: two visible selected agent tabs and no stored focus name no single agent.
    const worker = document.createElement("button");
    worker.setAttribute("data-testid", "workspace-tab-agent_agent-worker");
    worker.setAttribute("aria-selected", "true");
    Object.assign(worker, { checkVisibility: () => true, getBoundingClientRect: () => ({ top: 0, width: 160, height: 28 }) });
    document.body.prepend(worker);
    highlight("said");
    release(document.getElementById("said")!);
    expect(offer()).toBeNull();
    await flush();
    expect(frame.sent()).toEqual([]);

    worker.remove();
    highlight("said");
    release(document.getElementById("said")!);
    offer()!.click();
    await flush();
    expect(frame.sent("pick")).toHaveLength(1);
    expect(frame.sent("pick-unavailable")).toEqual([]);
  });

  it("names the missing structure in its one notice, and a highlight in a gap before it uses nothing up", async () => {
    drawChat({ gap: true, messageBox: false });
    const frame = framedChat();
    frame.trayDashboard({ copyOnHighlight: false, picks: true });
    highlight("gap");
    release(document.getElementById("gap")!);

    // A Paseo update renames the scroll root: the rows and message nodes stand without it.
    const root = document.getElementById("agent-chat-scroll-web-dom-scroll")!;
    root.removeAttribute("id");
    root.removeAttribute("data-testid");
    for (const id of ["said", "asked"]) {
      highlight(id);
      release(document.getElementById(id)!);
    }
    await flush();
    expect(offer()).toBeNull();
    expect(frame.sent()).toEqual([{ source: "ar-plugin", type: "pick-unavailable", reason: "chat-root-unreadable" }]);
  });
});

describe("the message box and the tray's space", () => {
  it("reports the message box on hello, when it grows and when the frame is resized", async () => {
    const box = drawChat();
    const frame = framedChat();
    expect(frame.sent("composer")).toEqual([]);

    frame.trayDashboard({ copyOnHighlight: false, picks: true });
    expect(frame.sent("composer")).toEqual([{ source: "ar-plugin", type: "composer", top: 812, height: 64, frameHeight: 900 }]);

    // A longer draft grows the box upwards.
    Object.assign(box, { top: 780, height: 96 });
    const observer = frame.resizeObservers.find((entry) => entry.targets.length > 0)!;
    observer.callback();
    observer.callback();
    // The frame gets shorter: the box moves up with its bottom edge.
    Object.assign(box, { top: 580 });
    frame.window.innerHeight = 700;
    frame.resize();
    await flush();
    expect(frame.sent("composer")).toEqual([
      { source: "ar-plugin", type: "composer", top: 812, height: 64, frameHeight: 900 },
      { source: "ar-plugin", type: "composer", top: 780, height: 96, frameHeight: 900 },
      { source: "ar-plugin", type: "composer", top: 580, height: 96, frameHeight: 700 },
    ]);
  });

  it("reserves the tray's height above the message box and releases it at 0", () => {
    drawChat();
    const frame = framedChat();
    // Only a dashboard that reads picks reserves anything.
    frame.receive({ type: "ar.tray-space", height: 120 });
    expect(document.getElementById(TRAY_SPACE_STYLE_ID)).toBeNull();

    frame.trayDashboard({ copyOnHighlight: false, picks: true });
    frame.receive({ type: "ar.tray-space", height: 120 });
    const root = document.getElementById("agent-chat-scroll-web-dom-scroll")!;
    expect(getComputedStyle(root).paddingBottom).toBe("120px");
    frame.receive({ type: "ar.tray-space", height: 148 });
    expect(getComputedStyle(root).paddingBottom).toBe("148px");
    expect(document.querySelectorAll(`#${TRAY_SPACE_STYLE_ID}`)).toHaveLength(1);

    frame.receive({ type: "ar.tray-space", height: 0 });
    expect(document.getElementById(TRAY_SPACE_STYLE_ID)).toBeNull();
    expect(getComputedStyle(root).paddingBottom).not.toBe("148px");
  });

  it("tells once that the message box cannot be read after a chat was drawn, and reports it when it appears", async () => {
    drawChat({ messageBox: false, tab: "live" });
    const tab = newTab();
    seedLayout(tab, "agent_live");
    const frame = framedChat(tab);
    frame.trayDashboard({ copyOnHighlight: false, picks: true });
    frame.receive({ type: "ar.open", agentId: "live" });
    await flush();
    expect(frame.sent("shown")).toHaveLength(1);
    // A missing box during an agent switch is not a failure: it has the grace to appear.
    await vi.advanceTimersByTimeAsync(COMPOSER_GRACE_MS - 1);
    expect(frame.sent("composer-unavailable")).toEqual([]);
    await vi.advanceTimersByTimeAsync(1);
    document.body.append(document.createElement("div"));
    await vi.advanceTimersByTimeAsync(COMPOSER_GRACE_MS * 3);
    expect(frame.sent("composer-unavailable")).toEqual([{ source: "ar-plugin", type: "composer-unavailable", reason: "message-box-not-found" }]);

    const input = document.createElement("div");
    input.setAttribute("data-testid", "message-input-root");
    Object.assign(input, { getBoundingClientRect: () => ({ top: 820, height: 56, width: 400 }) });
    document.body.append(input);
    await flush();
    expect(frame.sent("composer")).toEqual([{ source: "ar-plugin", type: "composer", top: 820, height: 56, frameHeight: 900 }]);
  });
});
