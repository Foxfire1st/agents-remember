import {
  agentProblemText,
  parsePluginMessage,
  paseoFrameUrl,
  type AvailablePaseoFrame,
  type PaseoAgentTarget,
  type PaseoPluginMessage,
} from "./paseoFrameModel";

/** How long the embedded application has to report ready, or to answer a request to show an agent. */
export const PASEO_CONTROL_TIMEOUT_MS = 10_000;

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
}

/**
 * Steers the embedded Paseo application to the agent of the execution the launcher bar displays.
 *
 * While the AR plugin's control channel is ready the agent is shown by one message and the frame
 * is never reloaded. Messages are accepted only from the frame's own window and configured
 * origin, and posted only to that origin. When the application does not report ready, or does
 * not answer a request, within the deadline the channel counts as unavailable: the frame stays
 * and an execution is then shown by loading its agent's URL.
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
  private deadline: number | null = null;
  private scope: string | null = null;
  private seenAgent: string | null = null;

  constructor(private readonly pane: PaseoFramePane) {}

  /**
   * The frame route named a frame: load it and wait for the application's ready report. The
   * first load opens the Projects workspace. A reload after Retry names the displayed agent in
   * the URL, so the frame lands on it even when the control channel stays down. Either way the
   * agent is asked for by message once the channel is ready: only that answer tells whether the
   * agent still exists.
   */
  start(available: AvailablePaseoFrame, afterRetry: boolean): void {
    this.available = available;
    this.pending = null;
    this.settled = null;
    this.setControl("waiting");
    this.load(afterRetry ? this.wanted : null);
    this.arm();
  }

  /** No frame (any more): nothing is steered and no deadline runs. */
  stop(): void {
    this.clearDeadline();
    this.available = null;
    this.pending = null;
  }

  /**
   * The launcher's selection and the agent of its execution. Within one selection only another
   * agent is a new request: the execution is briefly absent while launch options reload, and
   * that must not navigate the frame again.
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
    this.loaded = null;
    this.pane.setAgentProblem(null);
    this.sync();
  }

  /** A message event of the dashboard window; everything but the embedded application is ignored. */
  receive(event: MessageEvent): void {
    const frameWindow = this.pane.frameWindow();
    if (!this.available || !frameWindow) return;
    if (event.origin !== this.available.frameOrigin || event.source !== frameWindow) return;
    const message = parsePluginMessage(event.data);
    if (!message) return;
    if (message.type === "ready" || message.type === "pong") this.onReady();
    else if (message.type === "shown") this.onShown(message.agentId);
    else this.onError(message);
  }

  /** Also after the plugin restarted inside the page: an unanswered request is sent again. */
  private onReady(): void {
    this.clearDeadline();
    this.pending = null;
    this.setControl("ready");
    this.sync();
  }

  private onShown(agentId: string | undefined): void {
    if (!agentId || agentId !== this.pending) return;
    this.clearDeadline();
    this.pending = null;
    this.settled = agentId;
    this.pane.setAgentProblem(null);
  }

  private onError(message: Extract<PaseoPluginMessage, { type: "error" }>): void {
    if (!this.pending || (message.agentId && message.agentId !== this.pending)) return;
    this.clearDeadline();
    this.settled = this.pending;
    this.pending = null;
    this.pane.setAgentProblem(agentProblemText(message.code, message.message));
  }

  /**
   * Bring the frame to the wanted agent: by message while the control channel is ready, by
   * loading the agent's URL when it is unavailable (once: a frame whose URL already names the
   * agent is left alone). While the application is still expected to report ready nothing
   * happens; the report, or its deadline, calls this again.
   */
  private sync(): void {
    const { available, wanted } = this;
    if (!available || !wanted || this.settled === wanted.agentId) return;
    if (this.control === "ready") {
      const frameWindow = this.pane.frameWindow();
      if (!frameWindow) return;
      this.pending = wanted.agentId;
      this.arm();
      frameWindow.postMessage({ type: "ar.open", agentId: wanted.agentId }, available.frameOrigin);
    } else if (this.control === "unavailable" && this.loaded !== wanted.agentId) {
      this.load(wanted);
    }
  }

  private load(wanted: PaseoAgentTarget | null): void {
    const { available } = this;
    if (!available) return;
    this.loaded = wanted?.agentId ?? null;
    this.pane.setFrame((current) => ({
      src: paseoFrameUrl(available, wanted),
      generation: (current?.generation ?? 0) + 1,
    }));
  }

  /** Without a ready report, or an answer to a request, in time the control channel is unavailable. */
  private arm(): void {
    this.clearDeadline();
    this.deadline = window.setTimeout(() => {
      this.deadline = null;
      this.pending = null;
      this.setControl("unavailable");
      this.sync();
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
