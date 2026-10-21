// The dashboard's side of the tray's part of the frame channel (MIK-R188 rules 3–5, evidence 1's
// channel part): a pick reaches the tray only from the frame's own origin and window, the tray's
// wishes go to a ready frame whose plug-in picks, and what a frame reported is kept per page
// until its next ready report.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  clearFrameTray,
  frameTrayStore,
  onPick,
  setTrayPreferences,
  setTraySpace,
  type FramePick,
} from "./frameTrayChannel";
import { PaseoFrameControl, type PaseoFramePane } from "./paseoFrameControl";
import { parsePluginMessage, type AvailablePaseoFrame } from "./paseoFrameModel";

const FRAME_ORIGIN = "http://127.0.0.1:6820";
const AVAILABLE: AvailablePaseoFrame = {
  available: true,
  frameBaseUrl: FRAME_ORIGIN,
  frameOrigin: FRAME_ORIGIN,
  serverId: "srv_test",
  projectsWorkspaceId: "wks_projects",
  projectsWorkspaceDetail: null,
  provisionCommand: "agents-remember paseo provision",
};
/** What the plug-in of this build says it can do. */
const PICKING = ["open-agent", "ping", "page", "shown-on-display", "navigated", "on-chat", "load-failed", "pick"];
const PICK = { type: "pick", text: "The review page reads the change set.", agentId: "agent-manager", messageId: "msg_7f2", messageTime: "2026-10-19T12:05:00.000Z" };

const stops: Array<() => void> = [];

/** A controller on a real frame window (the rail's chat, page "document"), with what it posts. */
function railFrame() {
  const iframe = document.createElement("iframe");
  document.body.append(iframe);
  const frameWindow = iframe.contentWindow!;
  const posts = vi.fn();
  frameWindow.postMessage = posts as unknown as Window["postMessage"];
  const pane: PaseoFramePane = {
    frameWindow: () => frameWindow,
    setControl: () => {},
    setFrame: () => {},
    setAgentProblem: () => {},
    setShowing: () => {},
    setPluginNotice: () => {},
    page: "document",
  };
  const control = new PaseoFrameControl(pane);
  control.start(AVAILABLE, false);
  stops.push(() => control.stop());
  const says = (data: Record<string, unknown>, from: { origin?: string; source?: MessageEventSource | null } = {}) =>
    control.receive(
      new MessageEvent("message", {
        data: { source: "ar-plugin", ...data },
        origin: from.origin ?? FRAME_ORIGIN,
        source: from.source === undefined ? frameWindow : from.source,
      }),
    );
  const posted = (type: string) => posts.mock.calls.filter(([message]) => message.type === type).map(([message, origin]) => [message, origin]);
  return { control, posts, says, posted, frameWindow };
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  for (const stop of stops.splice(0)) stop();
  setTrayPreferences({ copyOnHighlight: false });
  setTraySpace("document", 0);
  clearFrameTray("document");
  vi.useRealTimers();
  document.body.innerHTML = "";
});

describe("the tray's part of the frame channel", () => {
  it("hands a pick from the frame's own origin and window to the tray, and drops every other", () => {
    const frame = railFrame();
    frame.says({ type: "ready", capabilities: PICKING });
    const picks: FramePick[] = [];
    const stop = onPick((pick) => picks.push(pick));

    frame.says(PICK, { origin: "http://evil.test" });
    frame.says(PICK, { origin: FRAME_ORIGIN + "0" });
    frame.says(PICK, { source: window });
    frame.says(PICK, { source: null });
    frame.says({ ...PICK, agentId: "" });
    frame.says({ ...PICK, text: "  " });
    // A chat text pick goes with its message's time (MIK-R188 rule 3): none, or one that is no
    // time, is no pick of the plug-in.
    frame.says({ ...PICK, messageTime: null });
    frame.says({ ...PICK, messageTime: "time unknown" });
    expect(picks).toEqual([]);

    frame.says(PICK);
    frame.says({ ...PICK, messageId: null });
    expect(picks).toEqual([
      { page: "document", text: PICK.text, agentId: "agent-manager", messageId: "msg_7f2", messageTime: "2026-10-19T12:05:00.000Z" },
      { page: "document", text: PICK.text, agentId: "agent-manager", messageId: null, messageTime: "2026-10-19T12:05:00.000Z" },
    ]);
    stop();
    frame.says(PICK);
    expect(picks).toHaveLength(2);
  });

  it("tells a ready frame whose plug-in picks what the tray wants, and again on every change", () => {
    const frame = railFrame();
    // Not ready yet: nothing goes out.
    setTrayPreferences({ copyOnHighlight: true });
    expect(frame.posted("ar.tray")).toEqual([]);

    frame.says({ type: "ready", capabilities: PICKING });
    expect(frame.posted("ar.hello")).toEqual([[{ type: "ar.hello", capabilities: ["late-answer", "navigated", "on-chat", "load-failed", "pick", "showing"] }, FRAME_ORIGIN]]);
    // The hello goes first, so the plug-in knows the dashboard reads picks before it is told more.
    expect(frame.posts.mock.calls.map(([message]) => message.type)).toEqual(["ar.hello", "ar.page", "ar.tray"]);
    expect(frame.posted("ar.tray")).toEqual([[{ type: "ar.tray", copyOnHighlight: true, picks: false }, FRAME_ORIGIN]]);
    expect(frame.posted("ar.tray-space")).toEqual([]);

    // The tray listens: picks are read now. The same wish twice goes out once.
    const stop = onPick(() => {});
    setTrayPreferences({ copyOnHighlight: true });
    setTraySpace("document", 132.4);
    setTraySpace("document", 132);
    // Another frame's space is not this frame's.
    setTraySpace("chats", 90);
    setTraySpace("document", 0);
    expect(frame.posted("ar.tray").map(([message]) => message)).toEqual([
      { type: "ar.tray", copyOnHighlight: true, picks: false },
      { type: "ar.tray", copyOnHighlight: true, picks: true },
    ]);
    expect(frame.posted("ar.tray-space").map(([message]) => message)).toEqual([
      { type: "ar.tray-space", height: 132 },
      { type: "ar.tray-space", height: 0 },
    ]);
    stop();
    setTraySpace("chats", 0);

    // A plug-in that does not pick is sent nothing of the tray, and a stopped frame nothing at all.
    const older = railFrame();
    older.says({ type: "ready", capabilities: PICKING.filter((ability) => ability !== "pick") });
    setTrayPreferences({ copyOnHighlight: false });
    expect(older.posted("ar.tray")).toEqual([]);
    const sent = frame.posts.mock.calls.length;
    frame.control.stop();
    setTraySpace("document", 64);
    expect(frame.posts.mock.calls).toHaveLength(sent);
  });

  it("keeps the message box and the pick notice per frame page until the frame's next ready report", () => {
    const frame = railFrame();
    frame.says({ type: "ready", capabilities: PICKING });
    frame.says({ type: "composer", top: 812, height: 64, frameHeight: 900 });
    frame.says({ type: "pick-unavailable", reason: "message-id-unreadable" });
    expect(frameTrayStore.getState().composer).toEqual({ document: { kind: "measured", top: 812, height: 64, frameHeight: 900 } });
    expect(frameTrayStore.getState().pickNotice).toEqual({ document: "message-id-unreadable" });

    frame.says({ type: "composer", top: 812, height: -4, frameHeight: 900 });
    frame.says({ type: "composer", top: "812", height: 64, frameHeight: 900 });
    frame.says({ type: "composer-unavailable", reason: "message-box-not-found" }, { origin: "http://evil.test" });
    expect(frameTrayStore.getState().composer.document).toEqual({ kind: "measured", top: 812, height: 64, frameHeight: 900 });
    frame.says({ type: "composer-unavailable", reason: "message-box-not-found" });
    expect(frameTrayStore.getState().composer.document).toEqual({ kind: "unavailable", reason: "message-box-not-found" });

    // A new ready report (a reloaded page): what the old page said no longer holds.
    frame.says({ type: "ready", capabilities: PICKING });
    expect(frameTrayStore.getState().composer).toEqual({});
    expect(frameTrayStore.getState().pickNotice).toEqual({});
    expect(parsePluginMessage({ source: "ar-plugin", type: "pick-unavailable" })).toBeNull();
  });

  it("takes down a page's pick notice when a pick from that page arrives", () => {
    const frame = railFrame();
    frame.says({ type: "ready", capabilities: PICKING });
    const picks: FramePick[] = [];
    const stop = onPick((pick) => picks.push(pick));
    frame.says({ type: "pick-unavailable", reason: "message-id-unreadable" });
    expect(frameTrayStore.getState().pickNotice).toEqual({ document: "message-id-unreadable" });
    // A pick from another origin proves nothing about this page.
    frame.says(PICK, { origin: "http://evil.test" });
    expect(frameTrayStore.getState().pickNotice).toEqual({ document: "message-id-unreadable" });

    frame.says(PICK);
    expect(picks).toHaveLength(1);
    expect(frameTrayStore.getState().pickNotice).toEqual({});
    stop();
  });

  it("keeps the chat the frame shows as its plug-in tells it, and never steers the frame for it", () => {
    const frame = railFrame();
    const chat = () => frameTrayStore.getState().chat.document;
    frame.says({ type: "ready", capabilities: PICKING });
    expect(chat()).toBeUndefined();
    frame.control.display("leaf", { agentId: "agent-manager" });
    frame.says({ type: "shown", agentId: "agent-manager" });
    expect(chat()).toBe("agent-manager");

    // No visible selected chat tells nothing: a compact layout publishes none while it shows one.
    frame.says({ type: "selection", agentIds: [] });
    expect(chat()).toBe("agent-manager");
    // Split panes keep the chat told before only while it is one of them.
    frame.says({ type: "selection", agentIds: ["agent-manager", "agent-worker"] });
    expect(chat()).toBe("agent-manager");
    frame.says({ type: "selection", agentIds: ["agent-worker"] });
    expect(chat()).toBe("agent-worker");
    frame.says({ type: "selection", agentIds: ["agent-manager", "agent-other"] });
    expect(chat()).toBeNull();
    // The application's own pane for an agent the host could not load shows no chat.
    frame.control.display("leaf", { agentId: "agent-gone" });
    frame.says({ type: "load-failed", agentId: "agent-gone" });
    expect(chat()).toBeNull();
    expect(frame.posted("ar.open").map(([message]) => message)).toEqual([
      { type: "ar.open", agentId: "agent-manager" },
      { type: "ar.open", agentId: "agent-gone" },
    ]);

    // A new load, and no frame, forget it.
    frame.says({ type: "selection", agentIds: ["agent-worker"] });
    frame.control.start(AVAILABLE, false);
    expect(chat()).toBeUndefined();
    frame.says({ type: "selection", agentIds: ["agent-worker"] });
    frame.control.stop();
    expect(chat()).toBeUndefined();
  });

  it("follows only the shown chat a plug-in that names `showing` tells, in either of Paseo's layouts", () => {
    const frame = railFrame();
    const chat = () => frameTrayStore.getState().chat.document;
    frame.says({ type: "ready", capabilities: [...PICKING, "showing"] });
    frame.says({ type: "showing", agentId: "agent-manager" });
    expect(chat()).toBe("agent-manager");
    // Its selection stands in for nothing: the plug-in's own report decides.
    frame.says({ type: "selection", agentIds: ["agent-worker"] });
    expect(chat()).toBe("agent-manager");
    frame.says({ type: "showing", agentId: "agent-worker" }, { origin: "http://evil.test" });
    frame.says({ type: "showing", agentId: "" });
    frame.says({ type: "showing" });
    expect(chat()).toBe("agent-manager");
    frame.says({ type: "showing", agentId: null });
    expect(chat()).toBeNull();
    expect(frame.posted("ar.open")).toEqual([]);
  });
});
