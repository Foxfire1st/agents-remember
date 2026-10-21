// The dashboard's side of the tray's part of the frame channel (MIK-R188 rules 3–5; the
// architect's Q-D ruling of 2026-10-19T20:20+02:00). The plug-in in each Paseo frame reports chat
// text picks and the rectangle of Paseo's message box; the tray (dashboard/src/tray/) is drawn by
// the dashboard outside the frame, over it, directly above that box, and the frame reserves the
// tray's height above the box. This module is the one place both sides meet: the frame
// controller (paseoFrameControl.ts) writes what its plug-in reports and posts what the tray asks;
// the tray reads and writes here and never touches a frame. Vanilla zustand; nothing persisted
// (the tray keeps its own preferences).
//
// API for the tray. `page` is the frame's page: "document" for the rail's chat column (the
// review page's column included), "chats" for the Chats view.
//   frameTrayStore                      the store; getState() holds:
//     composer[page]                    ComposerEdge, or undefined while the frame reported none
//                                       (no plug-in that picks is ready there yet)
//     pickNotice[page]                  why chat text picks are off there, or undefined; the
//                                       next pick from that page takes it down
//     copyOnHighlight                   posted to every frame in `ar.tray`
//     traySpace[page]                   the height the frame on `page` reserves above its box
//     chat[page]                        the agent whose chat the frame shows as its plug-in tells
//                                       it, null for none it can name, undefined while it has
//                                       told nothing since the frame loaded (rule 7's default)
//   setTrayPreferences({ copyOnHighlight })
//   setTraySpace(page, height)          0 releases it
//   onPick(listener) -> unsubscribe     chat text picks; frames are told `picks: true` exactly
//                                       while at least one listener is registered
//   pickUnavailableText(reason)         the one named notice for pickNotice
//   composerUnavailableText(reason)     the one named notice for an unavailable composer; the
//                                       tray then docks below the frame
//   useComposerEdge(page), usePickNotice(page), useFrameChat(page)   React hooks over the store
// The composer rectangle is in the frame's own viewport (CSS pixels from the frame's top edge);
// `frameHeight` is the frame's height as the plug-in measured it, so a rectangle measured for
// another frame height can be told apart.
//
// For paseoFrameControl.ts only: frameTrayReady(page, abilities, post), receiveFrameTray(page,
// message), clearFrameTray(page), setFrameChat(page, agentId). A frame that is ready with a
// plug-in that names `pick` gets `ar.tray` at once and again on every change, and
// `ar.tray-space` whenever its height changes.
import { useStore } from "zustand";
import { createStore } from "zustand/vanilla";

import { PICK_ABILITY, type PaseoTrayMessage, type PluginAbilities } from "./paseoFrameModel";

export type FramePage = "chats" | "document";

export type ComposerEdge =
  | { kind: "measured"; top: number; height: number; frameHeight: number }
  | { kind: "unavailable"; reason: string };

/** A chat text pick as the frame reported it, with the page of the frame it came from. */
export interface FramePick {
  page: FramePage;
  text: string;
  agentId: string;
  messageId: string | null;
  /** The message's time from the host's timeline (ISO 8601). */
  messageTime: string;
}

export interface FrameTrayState {
  composer: Partial<Record<FramePage, ComposerEdge>>;
  pickNotice: Partial<Record<FramePage, string>>;
  copyOnHighlight: boolean;
  traySpace: Partial<Record<FramePage, number>>;
  /** How many pick listeners are registered; frames read picks while it is above 0. */
  pickListeners: number;
  chat: Partial<Record<FramePage, string | null>>;
}

export const frameTrayStore = createStore<FrameTrayState>(() => ({
  composer: {},
  pickNotice: {},
  copyOnHighlight: false,
  traySpace: {},
  pickListeners: 0,
  chat: {},
}));

const listeners = new Set<(pick: FramePick) => void>();

export function setTrayPreferences(preferences: { copyOnHighlight: boolean }): void {
  frameTrayStore.setState({ copyOnHighlight: preferences.copyOnHighlight });
}

export function setTraySpace(page: FramePage, height: number): void {
  const next = Number.isFinite(height) && height > 0 ? Math.round(height) : 0;
  const { traySpace } = frameTrayStore.getState();
  if ((traySpace[page] ?? 0) === next) return;
  frameTrayStore.setState({ traySpace: { ...traySpace, [page]: next } });
}

/** Listen to chat text picks from every frame. Returns the function that stops listening. */
export function onPick(listener: (pick: FramePick) => void): () => void {
  if (!listeners.has(listener)) {
    listeners.add(listener);
    frameTrayStore.setState({ pickListeners: listeners.size });
  }
  return () => {
    if (!listeners.delete(listener)) return;
    frameTrayStore.setState({ pickListeners: listeners.size });
  };
}

/** The one notice for a frame whose chat text cannot be picked. */
export function pickUnavailableText(reason: string): string {
  return `Chat text picks are off in this chat: its page could not be read (${reason}). Object and panel picks still work.`;
}

/** The one notice for a frame whose message box cannot be read: the tray docks below the frame. */
export function composerUnavailableText(reason: string): string {
  return `The tray sits below the chat: the chat's message box could not be read (${reason}).`;
}

export const useComposerEdge = (page: FramePage): ComposerEdge | undefined =>
  useStore(frameTrayStore, (state) => state.composer[page]);

export const usePickNotice = (page: FramePage): string | undefined =>
  useStore(frameTrayStore, (state) => state.pickNotice[page]);

export const useFrameChat = (page: FramePage): string | null | undefined =>
  useStore(frameTrayStore, (state) => state.chat[page]);

function withoutPage<T>(byPage: Partial<Record<FramePage, T>>, page: FramePage): Partial<Record<FramePage, T>> {
  const next = { ...byPage };
  delete next[page];
  return next;
}

/** What a frame's plug-in reported no longer holds: a new ready report, or no frame. */
export function clearFrameTray(page: FramePage): void {
  const { composer, pickNotice } = frameTrayStore.getState();
  if (composer[page] === undefined && pickNotice[page] === undefined) return;
  frameTrayStore.setState({ composer: withoutPage(composer, page), pickNotice: withoutPage(pickNotice, page) });
}

/** The chat the frame on `page` shows, as the controller read it from the plug-in; undefined forgets it. */
export function setFrameChat(page: FramePage, agentId: string | null | undefined): void {
  const { chat } = frameTrayStore.getState();
  if (agentId === undefined ? !(page in chat) : chat[page] === agentId) return;
  frameTrayStore.setState({ chat: agentId === undefined ? withoutPage(chat, page) : { ...chat, [page]: agentId } });
}

/** A tray message of the frame on `page`, after the controller checked its origin and window. */
export function receiveFrameTray(page: FramePage, message: PaseoTrayMessage): void {
  const state = frameTrayStore.getState();
  switch (message.type) {
    case "pick":
      // A pick that arrived proves this page's chat text can be picked.
      if (state.pickNotice[page] !== undefined) frameTrayStore.setState({ pickNotice: withoutPage(state.pickNotice, page) });
      for (const listener of [...listeners]) {
        listener({ page, text: message.text, agentId: message.agentId, messageId: message.messageId, messageTime: message.messageTime });
      }
      break;
    case "pick-unavailable":
      frameTrayStore.setState({ pickNotice: { ...state.pickNotice, [page]: message.reason } });
      break;
    case "composer":
      frameTrayStore.setState({
        composer: { ...state.composer, [page]: { kind: "measured", top: message.top, height: message.height, frameHeight: message.frameHeight } },
      });
      break;
    case "composer-unavailable":
      frameTrayStore.setState({ composer: { ...state.composer, [page]: { kind: "unavailable", reason: message.reason } } });
  }
}

/** The link of one ready frame: it posts the tray's wishes until it is stopped. */
export interface FrameTrayLink {
  stop(): void;
}

/**
 * A frame on `page` reported ready: what its plug-in reported before no longer holds. A plug-in
 * that names `pick` is told the tray's wishes now and on every change; any other is sent nothing
 * (`null`).
 */
export function frameTrayReady(
  page: FramePage,
  abilities: PluginAbilities,
  post: (message: Record<string, unknown>) => void,
): FrameTrayLink | null {
  clearFrameTray(page);
  if (!abilities?.includes(PICK_ABILITY)) return null;
  let tray = "";
  let space = 0;
  const tell = (state: FrameTrayState) => {
    const wish = { type: "ar.tray", copyOnHighlight: state.copyOnHighlight, picks: state.pickListeners > 0 };
    const key = JSON.stringify(wish);
    if (key !== tray) {
      tray = key;
      post(wish);
    }
    const height = state.traySpace[page] ?? 0;
    if (height !== space) {
      space = height;
      post({ type: "ar.tray-space", height });
    }
  };
  tell(frameTrayStore.getState());
  return { stop: frameTrayStore.subscribe(tell) };
}
