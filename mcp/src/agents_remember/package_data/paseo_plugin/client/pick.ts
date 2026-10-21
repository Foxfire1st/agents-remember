import type { PluginPage } from "./page";

// The tray's part of the control channel (MIK-R188 rules 3, 4 and 5; the architect's Q-D ruling
// of 2026-10-19T20:20+02:00). Started by client/bridge.ts for a trusted parent; it acts only for
// a dashboard that said in `ar.hello` that it reads picks (`pick`), so no control is shown that
// cannot act (FAM-8GST1A).
//
//   dashboard -> app   { type: "ar.tray", copyOnHighlight, picks }   after ar.hello and on change
//                      { type: "ar.tray-space", height }              the tray's height; 0 releases it
//   app -> dashboard   { source: "ar-plugin", type: "pick", text, agentId, messageId, messageTime }
//                      { source: "ar-plugin", type: "pick-unavailable", reason }      once
//                      { source: "ar-plugin", type: "composer", top, height, frameHeight }
//                      { source: "ar-plugin", type: "composer-unavailable", reason }  once per loss
//
// Chat text: a highlight inside the chat copies it when the dashboard said so (`execCommand` in
// the `mouseup` handler) and offers a small non-modal "⊕ add" beside it. Only its click posts a
// pick. Nothing here calls `removeAllRanges()` or moves focus: the offer swallows its own
// `mousedown`, so the selection and the focus stay where they were.
//
// The message box: its rectangle inside the frame is posted on the dashboard's hello, when the
// box changes size (ResizeObserver) and when the frame is resized, so the dashboard draws the
// tray outside the frame, directly above the box. The tray's height is reserved above the box as
// padding at the end of the chat's scroll root, so the last message can always be scrolled
// above the tray.
//
// Pinned, unsupported Paseo 0.11.1 DOM adapter (the pinned server package's web build
// index-32cec38dd4de4493a1d5392537c1e464.js, read in its source on 2026-10-19, the rows' records
// on 2026-10-20; not yet measured in a running page, unlike the display sign in client/bridge.ts).
// UNSUPPORTED Paseo behaviour this file relies on:
//   - the chat's scroll root `<div data-testid="agent-chat-scroll" id="agent-chat-scroll-web-dom-
//     scroll">` (or `…-web-dom-virtualized`); matched by the `agent-chat-scroll-` id prefix and the
//     exact test id, as client/bridge.ts matches it;
//   - every row of the chat carrying `data-message-id` (`getStreamItemMessageId`): a user
//     message's stream id (`id ?? clientMessageId ?? messageId`), an assistant message's
//     `blockGroupId ?? id`, where that id is the timeline item's `messageId`, or
//     `<messageId>:segment:<n>`, or a generated id when the provider names none; a tool call's
//     `agent_tool_<callId>` or `agent_tool_turn:<turnId>/<callId>`; the message nodes
//     `data-testid="assistant-message"` and `"user-message"`;
//   - each row's own record. The build runs React DOM 19.1.0 (it refuses any other), which keeps
//     a host element's props under `__reactProps$<random>`. A row's props are
//     `{ "data-history-row-id": item.id, "data-message-id", style, children: <row item={item}> }`,
//     where the item is the chat's stream item. Its `timestamp` and `timelineCursor` are the host's
//     timeline entry's (`hydrateStreamState`: `new Date(entry.timestamp)`, `{ epoch, seq:
//     entry.seqEnd }`; a live item's come from its stream event). A message sent from this page
//     has no cursor until the host records it. React deletes the props when the row unmounts;
//   - the message box root `data-testid="message-input-root"` (React Native web `testID`);
//   - Paseo's own copy handler, which turns a copied chat selection into Markdown: the copy on
//     highlight runs it exactly as Ctrl+C does;
//   - the browser globals reached through page.ts: `document.getSelection()`,
//     `document.execCommand("copy")` (allowed in the frame by the dashboard's `clipboard-write`
//     delegation), `ResizeObserver`, timers, a `style` element in the head.
// The time of a chat text pick is the time its row's record holds (MIK-R188 rule 3, Provenance):
// read in place, with no request to the host, for a message however far back the history was
// scrolled and for a tool call's row alike. (The public SDK reads the timeline only in pages back
// from a cursor, `timeline.refetch`, never by message, so finding an old message there is not
// bounded.) A row the host has not recorded yet is refused in place by name: no pick goes without
// its time. In a tool call group (Paseo's "overview" detail level) the group's row is its first
// call's and holds its latest call's time.
// When the chat page cannot be read, chat text picks stop with one named notice; object and
// panel picks in the dashboard are not affected.

/** The ability the plug-in names, and the one the dashboard names when it reads picks. */
export const PICK_ABILITY = "pick";
/** How long a missing message box may stay missing after a chat was drawn before it is told. */
export const COMPOSER_GRACE_MS = 1_000;
export const PICK_OFFER_ID = "ar-pick-offer";
export const TRAY_SPACE_STYLE_ID = "ar-tray-space";

const CHAT_ROOT = '[id^="agent-chat-scroll-"], [data-testid="agent-chat-scroll"], [data-testid^="agent-chat-scroll-"]';
const MESSAGE_ROW = "[data-message-id]";
const MESSAGE_NODE = '[data-testid="assistant-message"], [data-testid="user-message"]';
const MESSAGE_BOX = '[data-testid="message-input-root"]';
const EDITABLE = 'input, textarea, [contenteditable=""], [contenteditable="true"], [contenteditable="plaintext-only"]';
/** Where React DOM keeps a host element's props: this prefix and the page's random suffix. */
const REACT_PROPS = "__reactProps$";

type Post = (payload: Record<string, unknown>) => void;

/** The tray's part of the channel, driven by the bridge's message handler. */
export interface TrayChannel {
  /** The dashboard said what it reads (`ar.hello`). */
  hello(dashboardAbilities: readonly string[]): void;
  /** `ar.tray`: whether to copy on highlight and whether picks are read now. */
  configure(message: Record<string, unknown>): void;
  /** `ar.tray-space`: the height to reserve above the message box; 0 releases it. */
  reserve(message: Record<string, unknown>): void;
  /** The bridge reported a drawn chat: from now on a missing message box is told. */
  chatShown(): void;
  stop(): void;
}

/** A node's element: itself, or the element a text node lies in. */
function elementOf(node: any): any {
  if (!node) return null;
  return node.nodeType === 1 ? node : (node.parentElement ?? null);
}

function closestOf(element: any, selector: string): any {
  return element && typeof element.closest === "function" ? element.closest(selector) : null;
}

function visibleBox(element: any): { top: number; height: number; width: number } | null {
  if (!element || typeof element.getBoundingClientRect !== "function") return null;
  const rect = element.getBoundingClientRect();
  if (typeof rect?.width !== "number" || typeof rect?.height !== "number") return null;
  if (rect.width <= 0 || rect.height <= 0) return null;
  return { top: Number(rect.top) || 0, height: rect.height, width: rect.width };
}

/** The highlighted text of the page right now with its range, or `null` for none. */
function currentSelection(page: PluginPage): { text: string; range: any; start: any } | null {
  const selection = typeof page.document.getSelection === "function" ? page.document.getSelection() : null;
  if (!selection || selection.isCollapsed || selection.rangeCount < 1) return null;
  const text = String(selection.toString()).trim();
  if (!text) return null;
  const range = selection.getRangeAt(0);
  const start = elementOf(range.startContainer);
  if (!start || closestOf(start, EDITABLE)) return null;
  return { text, range, start };
}

/**
 * Where a highlight starts, if it is chat text: its scroll root, its row and the row's message
 * id, each `null` when it starts outside them; and whether the root holds any row at all
 * (`rows`). `null` for text outside the chat.
 */
function chatPlace(start: any): { root: any; row: any; messageId: string | null; rows: boolean } | null {
  const root = closestOf(start, CHAT_ROOT);
  const row = closestOf(start, MESSAGE_ROW);
  const node = closestOf(start, MESSAGE_NODE);
  if (!root && !row && !node) return null;
  const id = row && typeof row.getAttribute === "function" ? row.getAttribute("data-message-id") : null;
  const rows = row !== null || (root !== null && typeof root.querySelector === "function" && root.querySelector(MESSAGE_ROW) !== null);
  return { root, row, messageId: typeof id === "string" && id ? id : null, rows };
}

/**
 * The time a chat row's own record holds (the adapter above): `{ time }` for an item the host's
 * timeline recorded, `{ time: null }` for one it has not recorded yet, and `null` when the row
 * holds no record of the item it draws.
 */
function rowTime(row: any): { time: string | null } | null {
  const key = Object.keys(row).find((name) => name.startsWith(REACT_PROPS));
  const item = key ? row[key]?.children?.props?.item : null;
  if (!item || typeof item !== "object" || item.id !== row.getAttribute("data-history-row-id")) return null;
  const cursor = item.timelineCursor;
  const recorded = typeof cursor?.epoch === "string" && Number.isInteger(cursor?.seq);
  const at = typeof item.timestamp?.getTime === "function" ? Number(item.timestamp.getTime()) : Number.NaN;
  return { time: recorded && Number.isFinite(at) ? new Date(at).toISOString() : null };
}

/** The "⊕ add" control beside a selection; non-modal, it never takes focus or the selection. */
function offerControl(page: PluginPage, range: any, onAdd: () => void): any {
  const button = page.document.createElement("button");
  button.id = PICK_OFFER_ID;
  button.type = "button";
  button.textContent = "⊕ add";
  button.setAttribute("aria-label", "Add the highlighted text to the AR tray");
  button.style.cssText = [
    "position:fixed", "z-index:2147483647", "margin:0", "padding:2px 8px", "border-radius:999px",
    "border:1px solid #ffb330", "background:#1a222b", "color:#ece4cf", "font:12px/16px system-ui,sans-serif",
    "cursor:pointer", "box-shadow:0 2px 6px rgba(0,0,0,.4)",
  ].join(";");
  button.addEventListener("mousedown", (event: any) => {
    // The click keeps the selection and the focus where they are.
    event.preventDefault();
    event.stopPropagation();
  });
  button.addEventListener("click", (event: any) => {
    event.preventDefault();
    event.stopPropagation();
    onAdd();
  });
  placeOffer(page, button, range);
  return button;
}

/** The offer tells in its own place that a click took nothing: the message has no time yet. */
function refuseOffer(page: PluginPage, button: any, range: any): void {
  button.textContent = "not added: no message time";
  button.setAttribute("aria-label", "Not added to the AR tray: the host has no time for this message yet");
  button.setAttribute("aria-disabled", "true");
  placeOffer(page, button, range);
}

/** Below the end of the selection, or above it where the frame ends. */
function placeOffer(page: PluginPage, button: any, range: any): void {
  const rects = typeof range?.getClientRects === "function" ? Array.from(range.getClientRects() as ArrayLike<any>) : [];
  const last: any = rects.length > 0 ? rects[rects.length - 1] : (typeof range?.getBoundingClientRect === "function" ? range.getBoundingClientRect() : null);
  const width = Number(page.window.innerWidth) || 0;
  const height = Number(page.window.innerHeight) || 0;
  // The control's own width once it is drawn; the refusal is wider than "⊕ add".
  const own = Math.max(72, (Number(typeof button.getBoundingClientRect === "function" ? button.getBoundingClientRect()?.width : 0) || 0) + 8);
  let left = (Number(last?.right) || 0) + 6;
  let top = (Number(last?.bottom) || 0) + 4;
  if (width > 0 && left > width - own) left = Math.max(4, width - own);
  if (height > 0 && top > height - 30) top = Math.max(4, (Number(last?.top) || 0) - 26);
  button.style.left = `${Math.round(left)}px`;
  button.style.top = `${Math.round(top)}px`;
}

/** The highlight offer and its pick (rules 3 and 4). */
function startPickOffer(page: PluginPage, post: Post, visibleAgent: () => string | null) {
  let reads = false;
  let copyOnHighlight = false;
  let picks = false;
  let unreadable: string | null = null;
  let offer: any = null;
  let shownFor: { range: any } | null = null;
  const isOffer = (target: any) => offer !== null && (target === offer || (typeof offer.contains === "function" && offer.contains(target)));
  const hide = () => {
    offer?.remove();
    offer = null;
    shownFor = null;
  };
  const unavailable = (reason: string) => {
    if (unreadable !== null) return;
    unreadable = reason;
    post({ type: "pick-unavailable", reason });
  };
  /**
   * The click reads the row's time again, so a message the host recorded since the highlight
   * picks; a row React has unmounted since keeps the time read at the highlight. Without a time
   * the pick is refused in place and nothing is posted.
   */
  const add = (pick: { text: string; agentId: string; messageId: string }, row: any, highlighted: string | null) => {
    const time = (rowTime(row) ?? { time: highlighted }).time;
    if (time === null) {
      if (offer && shownFor) refuseOffer(page, offer, shownFor.range);
      return;
    }
    hide();
    post({ type: "pick", text: pick.text, agentId: pick.agentId, messageId: pick.messageId, messageTime: time });
  };
  /**
   * The one notice goes only for the adapter's missing structure: no scroll root, a root
   * without a single message row, or a row without its record. A highlight in a gap between
   * rows, or one whose agent the frame does not name alone (split panes), gets no offer and no
   * notice: chat picks still work.
   */
  const offerFor = (selection: { text: string; range: any }, place: { root: any; row: any; messageId: string | null; rows: boolean }) => {
    let agentId: string | null = null;
    try {
      agentId = visibleAgent();
    } catch {
      agentId = null;
    }
    const messageId = place.messageId;
    if (!place.root) return unavailable("chat-root-unreadable");
    if (!place.rows) return unavailable("message-id-unreadable");
    if (!messageId || !agentId) return;
    const highlighted = rowTime(place.row);
    if (highlighted === null) return unavailable("message-time-unreadable");
    const pick = { text: selection.text, agentId, messageId };
    offer = offerControl(page, selection.range, () => add(pick, place.row, highlighted.time));
    shownFor = { range: selection.range };
    page.document.body.appendChild(offer);
  };
  const onMouseUp = (event: any) => {
    if (!reads || isOffer(event?.target)) return;
    hide();
    const selection = currentSelection(page);
    const place = selection ? chatPlace(selection.start) : null;
    if (!selection || !place) return;
    if (copyOnHighlight) {
      try {
        page.document.execCommand("copy");
      } catch {
        // A refused copy leaves the clipboard as it was; the offer still works.
      }
    }
    if (picks) offerFor(selection, place);
  };
  const onMouseDown = (event: any) => {
    if (offer && !isOffer(event?.target)) hide();
  };
  const onSelectionChange = () => {
    if (!offer) return;
    const selection = typeof page.document.getSelection === "function" ? page.document.getSelection() : null;
    if (!selection || selection.isCollapsed) hide();
  };
  const onScroll = () => {
    if (!offer || !shownFor) return;
    if (shownFor.range?.collapsed) hide();
    else placeOffer(page, offer, shownFor.range);
  };
  const listeners: Array<[string, (event: any) => void]> = [
    ["mouseup", onMouseUp], ["mousedown", onMouseDown], ["selectionchange", onSelectionChange], ["scroll", onScroll],
  ];
  for (const [type, listener] of listeners) page.document.addEventListener(type, listener, true);
  return {
    /** A dashboard that says hello again (a new ready) is told again what it was told. */
    hello(abilities: readonly string[]) {
      reads = abilities.includes(PICK_ABILITY);
      if (!reads) hide();
      else if (unreadable !== null) post({ type: "pick-unavailable", reason: unreadable });
    },
    configure(message: Record<string, unknown>) {
      copyOnHighlight = message.copyOnHighlight === true;
      picks = message.picks === true;
      if (!picks) hide();
    },
    stop() {
      hide();
      for (const [type, listener] of listeners) page.document.removeEventListener(type, listener, true);
    },
  };
}

/** The message box's rectangle for the tray's dock above it (rule 5, the Q-D ruling). */
function startComposerWatch(page: PluginPage, post: Post) {
  let started = false;
  let element: any = null;
  let resizes: any = null;
  let mutations: any = null;
  let last = "";
  let chatDrawn = false;
  // The reason the box was told missing; `null` while it is read or not told.
  let missing: string | null = null;
  let missingTimer: unknown = null;
  const clearMissingTimer = () => {
    if (missingTimer === null) return;
    try {
      page.window.clearTimeout(missingTimer);
    } catch {
      // Nothing to clear.
    }
    missingTimer = null;
  };
  const detach = () => {
    resizes?.disconnect();
    resizes = null;
    element = null;
  };
  const tellMissing = (reason: string) => {
    if (missing !== null) return;
    missing = reason;
    last = "";
    post({ type: "composer-unavailable", reason });
  };
  const measure = () => {
    const box = element?.isConnected === false ? null : visibleBox(element);
    if (!box) {
      detach();
      look();
      return;
    }
    clearMissingTimer();
    missing = null;
    const frameHeight = Math.round(Number(page.window.innerHeight) || 0);
    const message = { type: "composer", top: Math.round(box.top), height: Math.round(box.height), frameHeight };
    const key = JSON.stringify(message);
    if (key === last) return;
    last = key;
    post(message);
  };
  const attach = (): boolean => {
    const found = [...page.document.querySelectorAll(MESSAGE_BOX)].find((node) => visibleBox(node) !== null);
    if (!found) return false;
    element = found;
    resizes = new page.window.ResizeObserver(() => measure());
    resizes.observe(found);
    measure();
    return true;
  };
  const armMissing = () => {
    if (missing !== null || missingTimer !== null || !chatDrawn) return;
    missingTimer = page.window.setTimeout(() => {
      missingTimer = null;
      if (element === null && !attach()) tellMissing("message-box-not-found");
    }, COMPOSER_GRACE_MS);
  };
  /** Find the box again when it is gone; tell its absence only after a drawn chat and the grace. */
  function look(): void {
    if (element !== null && element.isConnected !== false) return;
    detach();
    if (!attach()) armMissing();
  }
  const onResize = () => measure();
  return {
    /** On every hello: watch from the first one on, and tell a new hello what is known now. */
    start() {
      if (started) {
        last = "";
        if (missing !== null) post({ type: "composer-unavailable", reason: missing });
        else measure();
        return;
      }
      started = true;
      if (typeof page.window.ResizeObserver !== "function") {
        tellMissing("resize-observer-unavailable");
        return;
      }
      mutations = new page.window.MutationObserver(() => {
        if (element === null || element.isConnected === false) look();
      });
      mutations.observe(page.document.documentElement, { subtree: true, childList: true });
      page.window.addEventListener("resize", onResize);
      look();
    },
    chatShown() {
      chatDrawn = true;
      if (started && element === null) armMissing();
    },
    stop() {
      clearMissingTimer();
      mutations?.disconnect();
      mutations = null;
      detach();
      if (started) page.window.removeEventListener("resize", onResize);
      started = false;
      missing = null;
      last = "";
    },
  };
}

/** Reserve `height` pixels at the end of the chat's scroll root; 0 removes the reservation. */
function reserveSpace(page: PluginPage, requested: unknown): void {
  const frameHeight = Number(page.window.innerHeight) || Number.POSITIVE_INFINITY;
  const height = typeof requested === "number" && Number.isFinite(requested) && requested > 0
    ? Math.round(Math.min(requested, frameHeight)) : 0;
  // A chat scrolled to its end stays at its end, so the last message is not left under the tray.
  const atEnd = [...page.document.querySelectorAll(CHAT_ROOT)].filter((root: any) =>
    typeof root.scrollHeight === "number" && root.scrollHeight - root.scrollTop - root.clientHeight <= 2);
  const existing = page.document.getElementById(TRAY_SPACE_STYLE_ID);
  if (height === 0) {
    existing?.remove();
  } else {
    const style = existing ?? page.document.createElement("style");
    style.id = TRAY_SPACE_STYLE_ID;
    style.textContent = `${CHAT_ROOT} { padding-bottom: ${height}px !important; box-sizing: border-box !important; }`;
    if (!existing) page.document.head.appendChild(style);
  }
  for (const root of atEnd) {
    try {
      (root as any).scrollTop = (root as any).scrollHeight;
    } catch {
      // A root that refuses the scroll keeps its place.
    }
  }
}

/**
 * Start the tray's part of the channel. Nothing is posted and nothing is shown until the
 * dashboard says it reads picks. `visibleAgent` names the agent of the chat the frame shows.
 */
export function startTrayChannel(
  page: PluginPage,
  post: Post,
  visibleAgent: () => string | null,
): TrayChannel {
  const offer = startPickOffer(page, post, visibleAgent);
  const composer = startComposerWatch(page, post);
  let reads = false;
  let reserved = false;
  return {
    hello(dashboardAbilities) {
      reads = dashboardAbilities.includes(PICK_ABILITY);
      offer.hello(dashboardAbilities);
      if (reads) composer.start();
      else composer.stop();
    },
    configure(message) {
      offer.configure(message);
    },
    reserve(message) {
      if (!reads) return;
      reserveSpace(page, message.height);
      reserved = typeof message.height === "number" && message.height > 0;
    },
    chatShown() {
      composer.chatShown();
    },
    stop() {
      offer.stop();
      composer.stop();
      if (reserved) reserveSpace(page, 0);
    },
  };
}
