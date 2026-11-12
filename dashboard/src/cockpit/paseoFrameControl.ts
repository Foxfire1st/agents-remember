import {
  agentProblemText,
  agentSilentText,
  agentTimeoutText,
  DASHBOARD_ABILITIES,
  parentProblemText,
  parsePluginMessage,
  paseoFrameUrl,
  pluginNoticeText,
  pluginOpensAgents,
  SHOWING_ABILITY,
  type AvailablePaseoFrame,
  type PaseoAgentTarget,
  type PaseoFrameShowing,
  type PaseoOpenRequest,
  type PaseoPluginMessage,
  type PluginAbilities,
} from "./paseoFrameModel";
import { clearFrameTray, frameTrayReady, receiveFrameTray, setFrameChat, type FrameTrayLink } from "./frameTrayChannel";

/**
 * How long the embedded application has to report ready after a load, how long a request to
 * show an agent may stay unanswered before the application is asked whether it is there, and
 * how long that ping may stay unanswered.
 */
export const PASEO_CONTROL_TIMEOUT_MS = 10_000;

/**
 * How long a not-ready selection waits for a superseding one before it counts as the latest.
 * A later-turn supersede arrives with the previous turn's window still pending and takes over
 * synchronously; a lone selection loads when this window expires. Longer than successive
 * user-event turns, short enough to keep the fallback snappy (MIK-R122 recovery 3).
 */
export const PASEO_UNAVAILABLE_QUIET_MS = 150;

export type PaseoControlState = "waiting" | "ready" | "unavailable";

/** The frame element's source; a new generation is a new element, so the same URL loads again. */
export interface PaseoFrameView {
  src: string;
  generation: number;
}

/** What the controller needs from the pane that renders the frame. */
export interface PaseoFramePane {
  frameWindow(): Window | null;
  setControl(state: PaseoControlState): void;
  setFrame(update: (current: PaseoFrameView | null) => PaseoFrameView | null): void;
  setAgentProblem(text: string | null): void;
  /** What the frame shows and which request it has not answered yet. */
  setShowing(showing: PaseoFrameShowing): void;
  /** What to say about a plug-in that lacks something this dashboard uses; `null` for none. */
  setPluginNotice(text: string | null): void;
  page?: "chats" | "document";
}

/**
 * Steers the embedded Paseo application to the agent of the execution the launcher bar displays.
 *
 * While the AR plugin's control channel is ready the agent is shown by one message and the frame
 * is never reloaded. Messages are accepted only from the frame's own window and configured
 * origin, and posted only to that origin.
 *
 * The frame's document loads again in these cases only. The application answers nothing at
 * all: its ready report after a load, or its answer to a request, is missing for the deadline,
 * and the ping sent then stays unanswered for another deadline. That silence allows one load,
 * of the wanted agent's URL. Retry, and a host that was gone and is back, start the frame anew.
 * And a plug-in that says it cannot show an agent on request has every agent loaded by its URL.
 *
 * After every load the application's ready report is awaited in the same way, and a pong counts
 * as that report. A selection made meanwhile is held and goes out by message at the report; it
 * loads only when the ping after that load stays unanswered too. An answer that is only late is
 * waited for: the channel stays ready, and the next selection is a message.
 */
export class PaseoFrameControl {
  private available: AvailablePaseoFrame | null = null;
  private control: PaseoControlState = "waiting";
  // The agent of the displayed execution; the agent the frame answered for (shown, archived or
  // gone); the agent of a request the frame has not answered yet; and the agent the frame's
  // current URL names.
  private wanted: PaseoAgentTarget | null = null;
  private settled: string | null = null;
  private pending: string | null = null;
  private loaded: string | null = null;
  // Of the answers: the agent confirmed as shown, and the agent the frame refused.
  private shown: string | null = null;
  private refused: string | null = null;
  // Of the open request: past its deadline; the plug-in said the application was asked to
  // navigate; and a ping is out that the application has not answered.
  private late = false;
  private navigated = false;
  private onChat = false;
  private probing = false;
  // A load whose ready report is still awaited: no other load starts before that wait ends.
  private loading = false;
  private abilities: PluginAbilities = null;
  private deadline: number | null = null;
  private scope: string | null = null;
  private seenAgent: string | null = null;
  private selectedAgentIds = new Set<string>();
  // MIK-R122 failure/recovery 3, sealed F04: trailing-edge settle for selections made while
  // the channel is unavailable. Every display re-arms one quiet window; only its expiry
  // brings the latest selection forward, so a burst of any turn pattern loads exactly its
  // latest and never an intermediate. A lone selection loads when the window expires. The
  // ready report, Retry, host return, or a new deadline preempts the window.
  private settleTimer: number | null = null;
  // The tray's part of the channel (frameTrayChannel.ts): linked while a plug-in that picks is
  // ready, so the tray's wishes go out as `ar.tray` / `ar.tray-space` when they change.
  private trayLink: FrameTrayLink | null = null;
  // The chat the frame shows as its plug-in tells it, for the tray's default (MIK-R188 rule 7):
  // an agent, null for none it can name, undefined while it has told nothing since this load.
  private frameChat: string | null | undefined = undefined;

  constructor(private readonly pane: PaseoFramePane) {}

  /**
   * The frame route named a frame: load it and wait for the application's ready report. The
   * first load opens the Projects workspace. A reload after Retry names the displayed agent in
   * the URL, so the frame lands on it even when the control channel stays down. Either way the
   * agent is asked for by message once the channel is ready: only that answer tells whether the
   * agent still exists.
   *
   * A preload (MIK-R89 v2 rule 12: the review page's closed chat column) starts the frame before
   * its pane is shown. Its load names no agent, so preloading opens none; the pane's first showing
   * asks for its agent by message.
   */
  start(available: AvailablePaseoFrame, afterRetry: boolean, preload = false): void {
    this.available = available;
    this.settled = null;
    this.shown = null;
    this.refused = null;
    this.closeRequest();
    this.clearSettle();
    this.selectedAgentIds.clear();
    this.tellChat(undefined);
    this.setControl("waiting");
    const named = !preload && (afterRetry || this.pane.page === "document");
    this.load(named ? this.wanted : null);
  }

  /** No frame (any more): nothing is steered and no deadline runs. */
  stop(): void {
    this.linkTray(null);
    this.clearDeadline();
    this.clearSettle();
    this.available = null;
    this.pending = null;
    this.loading = false;
    this.selectedAgentIds.clear();
    this.tellChat(undefined);
  }

  /**
   * The launcher's selection and the agent of its execution. Within one selection only another
   * agent is a new request: the execution is briefly absent while launch options reload, and
   * that must not navigate the frame again.
   *
   * While the control channel is ready the switch goes out at once by message. While a load's
   * ready report is awaited the switch is held for that report. While the channel is
   * unavailable the switch re-arms the trailing settle window: only its expiry brings the
   * latest selection forward, so a burst of any turn pattern loads exactly its latest and
   * never an intermediate. The ready report, Retry, host return, or a new deadline preempts
   * the window.
   */
  display(scope: string, target: PaseoAgentTarget | null): void {
    if (this.scope !== scope) {
      this.scope = scope;
      this.seenAgent = null;
      this.wanted = null;
      this.pane.setAgentProblem(null);
    }
    if (!target || this.seenAgent === target.agentId) return;
    this.seenAgent = target.agentId;
    this.wanted = target;
    this.settled = null;
    this.shown = null;
    this.refused = null;
    this.loaded = null;
    this.pane.setAgentProblem(null);
    this.publish();
    if (this.control !== "unavailable" || this.loading) {
      this.sync();
      return;
    }
    this.clearSettle();
    this.settleTimer = window.setTimeout(() => this.onSettle(), PASEO_UNAVAILABLE_QUIET_MS);
  }

  /** The settle window expired with no superseding display: the latest selection loads now. */
  private onSettle(): void {
    this.settleTimer = null;
    this.sync();
  }

  private clearSettle(): void {
    if (this.settleTimer === null) return;
    window.clearTimeout(this.settleTimer);
    this.settleTimer = null;
  }

  /** A message event of the dashboard window; everything but the embedded application is ignored. */
  receive(event: MessageEvent): void {
    const frameWindow = this.pane.frameWindow();
    if (!this.available || !frameWindow) return;
    if (event.origin !== this.available.frameOrigin || event.source !== frameWindow) return;
    const message = parsePluginMessage(event.data);
    if (!message) return;
    this.onMessage(message);
  }

  private onMessage(message: PaseoPluginMessage): void {
    switch (message.type) {
      case "selection":
        this.selectedAgentIds = new Set(message.agentIds);
        this.followSelection(message.agentIds);
        break;
      case "showing":
        this.tellChat(message.agentId);
        break;
      case "navigation-error":
        if (this.selectedAgentIds.has(message.sourceAgentId))
          this.pane.setAgentProblem(parentProblemText(message.code, message.message));
        break;
      case "pick":
      case "pick-unavailable":
      case "composer":
      case "composer-unavailable":
        receiveFrameTray(this.pane.page ?? "chats", message);
        break;
      default:
        this.onAnswer(message);
    }
  }

  private onAnswer(message: PaseoPluginMessage): void {
    switch (message.type) {
      case "ready":
        this.onReady(message.capabilities);
        break;
      case "pong":
        this.onPong(message.capabilities);
        break;
      case "navigated":
        this.onNavigated(message.agentId);
        break;
      case "on-chat":
        this.onOnChat(message.agentId);
        break;
      case "shown":
      case "load-failed":
        this.onShown(message.agentId, message.type === "shown");
        break;
      case "error":
        this.onError(message);
    }
  }

  /**
   * Also after the plugin restarted inside the page: an unanswered request is sent again. A
   * plug-in that says what it can do is told what this dashboard can do; one that says it
   * cannot show an agent on request leaves the channel unavailable, and agents load by URL.
   */
  private onReady(capabilities: PluginAbilities): void {
    this.loading = false;
    this.clearDeadline();
    this.clearSettle();
    this.closeRequest();
    this.abilities = capabilities;
    this.pane.setPluginNotice(pluginNoticeText(capabilities, this.available!.provisionCommand));
    if (!pluginOpensAgents(capabilities)) {
      this.linkTray(null);
      this.setControl("unavailable");
      this.sync();
      return;
    }
    this.setControl("ready");
    if (capabilities) this.post({ type: "ar.hello", capabilities: DASHBOARD_ABILITIES });
    if (this.pane.page === "document") this.post({ type: "ar.page", page: "document" });
    this.linkTray(capabilities);
    this.sync();
  }

  /**
   * After a ready report the plug-in's earlier tray reports no longer hold; a plug-in that picks
   * is told the tray's wishes now and whenever they change while the channel is ready. `null`
   * (no frame, or no plug-in that picks) unlinks.
   */
  private linkTray(capabilities: PluginAbilities): void {
    const page = this.pane.page ?? "chats";
    this.trayLink?.stop();
    this.trayLink = null;
    if (capabilities === null) {
      clearFrameTray(page);
      return;
    }
    this.trayLink = frameTrayReady(page, capabilities, (message) => {
      if (this.control === "ready") this.post(message);
    });
  }

  /**
   * The answer to a ping: the application is there, so a late answer is waited for and asked
   * after again one deadline later. It never sends the open request a second time. From an
   * application that was not counted as ready, a pong is its ready report.
   */
  private onPong(capabilities: PluginAbilities): void {
    if (this.control !== "ready") {
      this.onReady(capabilities);
      return;
    }
    if (!this.probing) return;
    this.probing = false;
    this.arm(() => this.probe());
  }

  private onNavigated(agentId: string): void {
    if (agentId !== this.pending) return;
    this.navigated = true;
    this.publish();
  }

  /** The application is on the asked chat: its header and its input are that agent's. */
  private onOnChat(agentId: string): void {
    if (agentId !== this.pending) return;
    this.onChat = true;
    this.publish();
  }

  /**
   * The answer that ends a request: the agent's chat is drawn, or the application shows on the
   * agent's screen its own pane for a host that could not load it. Either way the application
   * is on the agent, nothing more is waited for, and the frame is in reach; a failure is the
   * application's to say and to retry, the dashboard names none of its own.
   */
  private onShown(agentId: string | undefined, drawn: boolean): void {
    if (!agentId || agentId !== this.pending) return;
    this.clearDeadline();
    this.settled = agentId;
    this.shown = agentId;
    this.closeRequest();
    this.pane.setAgentProblem(null);
    this.tellChat(drawn ? agentId : null);
  }

  /**
   * Paseo's own list can move the frame to another chat, and the tray's default is the chat the
   * frame shows (MIK-R188 rule 7). A plug-in that names `showing` tells that chat in both of
   * Paseo's layouts, and its report alone is followed. For an older one the visible selected
   * chats (MIK-R95) stand in: one chat is that chat; several (split panes) keep the chat told
   * before only if it is one of them; none tells nothing, as its compact layout publishes none
   * while it shows a chat. Either way the frame is not steered: the host's own navigation stands,
   * and the launcher's selection is not changed.
   */
  private followSelection(agentIds: string[]): void {
    if (this.abilities?.includes(SHOWING_ABILITY)) return;
    if (agentIds.length === 1) this.tellChat(agentIds[0]);
    else if (agentIds.length > 1) this.tellChat(this.frameChat && agentIds.includes(this.frameChat) ? this.frameChat : null);
  }

  private tellChat(agentId: string | null | undefined): void {
    this.frameChat = agentId;
    setFrameChat(this.pane.page ?? "chats", agentId);
  }

  private onError(message: Extract<PaseoPluginMessage, { type: "error" }>): void {
    if (!this.pending || (message.agentId && message.agentId !== this.pending)) return;
    this.clearDeadline();
    this.settled = this.pending;
    this.refused = this.pending;
    this.closeRequest();
    this.pane.setAgentProblem(agentProblemText(message.code, message.message));
  }

  /**
   * Bring the frame to the wanted agent: by message while the control channel is ready, by
   * loading the agent's URL when it is unavailable (once: a frame whose URL already names the
   * agent is left alone; bursts coalesce through the settle window before this runs). While
   * the application is still expected to report ready after a load nothing happens; the
   * report, or the silence after its ping, calls this again.
   */
  private sync(): void {
    const { available, wanted } = this;
    if (!available || !wanted || this.settled === wanted.agentId) return;
    if (this.control === "ready") {
      this.ask(wanted);
    } else if (this.control === "unavailable" && !this.loading && this.loaded !== wanted.agentId) {
      this.load(wanted);
    }
  }

  /** Ask the application by message for the wanted agent; its answer has the deadline. */
  private ask(wanted: PaseoAgentTarget): void {
    if (!this.pane.frameWindow()) return;
    this.pending = wanted.agentId;
    this.late = false;
    this.navigated = false;
    this.onChat = false;
    this.probing = false;
    this.arm(() => this.probe());
    this.post({ type: "ar.open", agentId: wanted.agentId, ...(this.pane.page === "document" && wanted.workspaceId ? { workspaceId: wanted.workspaceId } : {}) });
    this.publish();
  }

  private post(message: Record<string, unknown>): void {
    if (this.available) this.pane.frameWindow()?.postMessage(message, this.available.frameOrigin);
  }

  private load(wanted: PaseoAgentTarget | null): void {
    const { available } = this;
    if (!available) return;
    this.loaded = wanted?.agentId ?? null;
    this.pane.setFrame((current) => ({
      src: paseoFrameUrl(available, wanted) + (this.pane.page === "document" ? (paseoFrameUrl(available, wanted).includes("?") ? "&" : "?") + "arPage=document" : ""),
      generation: (current?.generation ?? 0) + 1,
    }));
    this.publish();
    this.awaitReady();
  }

  /**
   * The selected target when the frame never became ready: wanted but never settled. Null
   * when nothing is selected or the selection already settled — then the silence names nothing.
   */
  private unansweredTarget(): PaseoAgentTarget | null {
    return this.wanted && this.settled !== this.wanted.agentId ? this.wanted : null;
  }

  /**
   * After a load the application has the deadline to report ready. Then it is asked by a ping
   * whether it is there, and its pong is taken as the ready report. Only when that ping stays
   * unanswered for another deadline is the control channel unavailable.
   */
  private awaitReady(): void {
    this.loading = true;
    this.arm(() => {
      this.post({ type: "ar.ping" });
      this.arm(() => {
        this.loading = false;
        // The silence names the selected target truthfully, saying whether the fallback below
        // loads its page or the frame already names it. It wins over any coalescing burst: it
        // clears it first, so the one fallback stays bounded.
        const target = this.unansweredTarget();
        if (target) this.pane.setAgentProblem(agentTimeoutText(target.agentId, this.loaded !== target.agentId));
        this.fallBack();
      });
    });
  }

  /**
   * A request is unanswered for the deadline: the answer is late. Whether the application is
   * there at all is asked by a ping with the same deadline; only its silence loads the frame.
   */
  private probe(): void {
    if (!this.pending || !this.pane.frameWindow()) return;
    this.late = true;
    this.probing = true;
    this.publish();
    this.post({ type: "ar.ping" });
    this.arm(() => {
      const agentId = this.pending;
      if (agentId) this.pane.setAgentProblem(agentSilentText(agentId, this.loaded !== agentId));
      this.fallBack();
    });
  }

  private fallBack(): void {
    this.clearSettle();
    this.closeRequest();
    this.setControl("unavailable");
    this.sync();
  }

  private closeRequest(): void {
    this.pending = null;
    this.late = false;
    this.navigated = false;
    this.onChat = false;
    this.probing = false;
    this.publish();
  }

  private openRequest(): PaseoOpenRequest | null {
    if (!this.pending) return null;
    const told = this.abilities?.includes("navigated") === true;
    return {
      agentId: this.pending,
      late: this.late,
      waitsFor: !told ? "chat" : this.navigated || this.onChat ? "application" : "host",
      onChat: this.onChat,
    };
  }

  private publish(): void {
    this.pane.setShowing({
      shown: this.shown,
      refused: this.refused,
      landed: this.loaded,
      request: this.openRequest(),
    });
  }

  private arm(onDeadline: () => void): void {
    this.clearDeadline();
    this.deadline = window.setTimeout(() => {
      this.deadline = null;
      onDeadline();
    }, PASEO_CONTROL_TIMEOUT_MS);
  }

  private clearDeadline(): void {
    if (this.deadline === null) return;
    window.clearTimeout(this.deadline);
    this.deadline = null;
  }

  private setControl(next: PaseoControlState): void {
    this.control = next;
    this.pane.setControl(next);
  }
}
