// What the Chats pane knows about the embedded Paseo application: the frame route's answer, the
// host fields of an execution, the frame URLs built from them, and the messages the AR plugin
// sends up from inside the frame. Pure functions only; PaseoChatFrame.tsx owns the state.

import type { RoleExecutionHost } from "./roleLaunchModel";

/** The agent of an execution, as its public execution names it (`execution.kind: "paseo-agent"`). */
export interface PaseoAgentTarget {
  agentId: string;
  workspaceId?: string;
}

export type PaseoFrameUnavailableReason =
  "not-configured" | "origin-not-listed" | "unreachable" | "backend";

/** The one sentence the pane has for each reason the host or its frame cannot be had. */
export const UNAVAILABLE_HEADLINE: Record<PaseoFrameUnavailableReason, string> = {
  "not-configured": "no Paseo runtime configured",
  unreachable: "Paseo daemon unreachable",
  "origin-not-listed": "embedded chat is not configured for this address",
  backend: "the dashboard backend did not answer",
};

export interface AvailablePaseoFrame {
  available: true;
  frameBaseUrl: string;
  frameOrigin: string;
  serverId: string;
  projectsWorkspaceId: string | null;
  /** Why the route named no Projects workspace; `null` when it named one. */
  projectsWorkspaceDetail: string | null;
  /** The command that provisions the host and its plug-in, as the backend names it. */
  provisionCommand: string;
}

export type PaseoFrameDescriptor =
  AvailablePaseoFrame | { available: false; reason: PaseoFrameUnavailableReason; detail: string };

/** What a plug-in said it can do; `null` for one that says nothing, as every older one. */
export type PluginAbilities = string[] | null;

export type PaseoPluginMessage =
  | { type: "ready"; capabilities: PluginAbilities }
  | { type: "pong"; capabilities: PluginAbilities }
  | { type: "navigated"; agentId: string }
  | { type: "on-chat"; agentId: string }
  | { type: "load-failed"; agentId: string }
  | { type: "shown"; agentId?: string; workspaceId?: string }
  | { type: "error"; code: string; agentId?: string; message?: string }
  | { type: "selection"; agentIds: string[] }
  | {
      type: "navigation-error";
      context: "parent";
      sourceAgentId: string;
      targetAgentId: string | null;
      code: string;
      message?: string;
    };

const PASEO_PLUGIN_SOURCE = "ar-plugin";

/** What this dashboard uses of the plug-in in the frame. */
const PLUGIN_ABILITIES_USED = ["open-agent", "ping", "page", "shown-on-display", "navigated", "on-chat", "load-failed"];
/** What this dashboard can do for a plug-in: it is told so once the plug-in said what it can. */
export const DASHBOARD_ABILITIES = ["late-answer", "navigated", "on-chat", "load-failed"];
/**
 * Said of a plug-in that answers when the agent's screen is opened, not when its conversation
 * is drawn: the dashboard's waiting line then ends at that answer.
 */
const EARLY_ANSWER = " With this plug-in the waiting line ends before the conversation is drawn.";
/** Named where the backend is a build that does not name the command itself. */
const PROVISION_COMMAND_UNNAMED =
  "agents-remember paseo provision --config <the active harness settings file>";

const UNAVAILABLE_REASONS: readonly PaseoFrameUnavailableReason[] = [
  "not-configured",
  "origin-not-listed",
  "unreachable",
];
const BACKEND_UNAVAILABLE: PaseoFrameDescriptor = {
  available: false,
  reason: "backend",
  detail: "The dashboard backend did not answer the frame request.",
};

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function text(value: unknown): string | undefined {
  return typeof value === "string" && value ? value : undefined;
}

/**
 * The agent the frame should show for a public execution, or `null` when it names none. A
 * `rejected` launch keeps its minted agent id although no host agent exists, so it names none.
 */
export function paseoAgentTarget(execution: unknown): PaseoAgentTarget | null {
  const receipt = record(execution);
  // Read as untrusted JSON, under the field names the execution's host type declares.
  const host: Partial<Record<keyof RoleExecutionHost, unknown>> | null = record(receipt?.execution);
  const agentId = text(host?.agentId);
  if (!receipt || host?.kind !== "paseo-agent" || !agentId) return null;
  if (typeof receipt.status === "string" && receipt.status.toLowerCase() === "rejected")
    return null;
  const workspaceId = text(host.workspaceId);
  return workspaceId ? { agentId, workspaceId } : { agentId };
}

/** The origin of an http(s) URL, or `null` for anything a frame must not be pointed at. */
function webOrigin(url: string): string | null {
  try {
    const parsed = new URL(url);
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.origin : null;
  } catch {
    return null;
  }
}

function availableFrame(answer: Record<string, unknown>): PaseoFrameDescriptor {
  const frameBaseUrl = text(answer.frameBaseUrl);
  const serverId = text(answer.serverId);
  const frameOrigin = frameBaseUrl ? webOrigin(frameBaseUrl) : null;
  if (!frameBaseUrl || !serverId || !frameOrigin) return BACKEND_UNAVAILABLE;
  return {
    available: true,
    frameBaseUrl: frameBaseUrl.replace(/\/+$/, ""),
    frameOrigin,
    serverId,
    projectsWorkspaceId: text(answer.projectsWorkspaceId) ?? null,
    projectsWorkspaceDetail: text(answer.projectsWorkspaceId)
      ? null
      : (text(answer.projectsWorkspaceDetail) ?? null),
    provisionCommand: text(answer.provisionCommand) ?? PROVISION_COMMAND_UNNAMED,
  };
}

/** The frame route's answer; anything it cannot read is the backend failing to answer. */
export function parseFrameDescriptor(value: unknown): PaseoFrameDescriptor {
  const answer = record(value);
  if (!answer) return BACKEND_UNAVAILABLE;
  if (answer.available === true) return availableFrame(answer);
  const reason = UNAVAILABLE_REASONS.find((candidate) => candidate === answer.reason);
  return reason
    ? { available: false, reason, detail: text(answer.detail) ?? "" }
    : BACKEND_UNAVAILABLE;
}

/**
 * The URL the frame loads: the agent's tab in its workspace, the agent's own route when its
 * workspace is not known, else the Projects workspace, else the application's start page.
 *
 * These are routes of Paseo's web application (`/h/<serverId>/workspace/<id>?open=agent:<id>`
 * and `/h/<serverId>/agent/<id>`), not a documented interface: a Paseo release can change them.
 * Showing an agent by message does not use them.
 */
export function paseoFrameUrl(
  descriptor: AvailablePaseoFrame,
  target: PaseoAgentTarget | null,
): string {
  const host = descriptor.frameBaseUrl + "/h/" + encodeURIComponent(descriptor.serverId);
  if (target?.workspaceId) {
    return (
      host +
      "/workspace/" +
      encodeURIComponent(target.workspaceId) +
      "?open=agent:" +
      encodeURIComponent(target.agentId)
    );
  }
  if (target) return host + "/agent/" + encodeURIComponent(target.agentId);
  if (descriptor.projectsWorkspaceId)
    return host + "/workspace/" + encodeURIComponent(descriptor.projectsWorkspaceId);
  return descriptor.frameBaseUrl + "/";
}

/** The optional fields of a message, present only when they are non-empty text. */
function named(message: Record<string, unknown>, ...keys: string[]): Record<string, string> {
  const present: Record<string, string> = {};
  for (const key of keys) {
    const value = text(message[key]);
    if (value) present[key] = value;
  }
  return present;
}

function nullableText(value: unknown): value is string | null {
  return value === null || (typeof value === "string" && value.length > 0);
}

/** A message of the AR plugin's control channel, or `null` for anything else. */
export function parsePluginMessage(data: unknown): PaseoPluginMessage | null {
  const message = record(data);
  if (!message || message.source !== PASEO_PLUGIN_SOURCE) return null;
  switch (message.type) {
    case "ready":
      return { type: "ready", capabilities: abilities(message.capabilities) };
    case "pong":
      return { type: "pong", capabilities: abilities(message.capabilities) };
    case "shown":
      return { type: "shown", ...named(message, "agentId", "workspaceId") };
    case "error":
      return {
        type: "error",
        code: text(message.code) ?? "open-failed",
        ...named(message, "agentId", "message"),
      };
    default:
      return parseNavigationMessage(message);
  }
}

/** The list a plug-in sent of what it can do; anything else is a plug-in that says nothing. */
function abilities(value: unknown): PluginAbilities {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : null;
}

/** Whether the plug-in can show an agent on request; one that says nothing could, since ever. */
export function pluginOpensAgents(capabilities: PluginAbilities): boolean {
  return capabilities === null || capabilities.includes("open-agent");
}

/**
 * What the frame's notice says about a plug-in that lacks something this dashboard uses, with
 * the command that provisions the current one; `null` for a plug-in that lacks nothing.
 */
export function pluginNoticeText(capabilities: PluginAbilities, command: string): string | null {
  const remedy = " To provision the current plug-in run: " + command;
  if (capabilities === null)
    return "The chat plug-in on the host is older than this dashboard: it does not say what it can do. Chats still switch." + EARLY_ANSWER + remedy;
  const lacking = PLUGIN_ABILITIES_USED.filter((ability) => !capabilities.includes(ability));
  if (lacking.length === 0) return null;
  if (lacking.includes("open-agent"))
    return "The chat plug-in on the host cannot show a chat on request, so each chat is loaded by its address." + remedy;
  const early = lacking.includes("shown-on-display") ? EARLY_ANSWER : "";
  return "The chat plug-in on the host is older than this dashboard: it lacks " + lacking.join(", ") + ". Chats still switch." + early + remedy;
}

/** A request to show an agent that the frame has not answered yet. */
export interface PaseoOpenRequest {
  agentId: string;
  /** Past the deadline, while the application still answers a ping. */
  late: boolean;
  /** What the answer waits for, as far as the plug-in tells: `chat` where it does not. */
  waitsFor: "host" | "application" | "chat";
  /**
   * The application is on this agent's chat: its header and its input are that agent's, while
   * the conversation may still be loading. Only a plug-in that tells so sets it.
   */
  onChat: boolean;
}

/** What the frame shows, as far as its answers and loads tell. */
export interface PaseoFrameShowing {
  /**
   * The agent whose screen the frame confirmed: its chat is drawn, or the application shows
   * there its own pane for a host that could not load the agent.
   */
  shown: string | null;
  /** The agent the frame said it cannot show. */
  refused: string | null;
  /** The agent whose address the frame's document was loaded on: no earlier chat is in it. */
  landed: string | null;
  request: PaseoOpenRequest | null;
}

const WAITED_FOR: Record<PaseoOpenRequest["waitsFor"], string> = {
  host: "the host to answer for this chat",
  application: "the application to draw this chat",
  chat: "this chat to be shown",
};

/** The one line of the pane while a chat is asked for and not shown yet. */
export function requestWaitText(request: PaseoOpenRequest | null): string {
  const what = WAITED_FOR[request?.waitsFor ?? "chat"];
  return request?.late
    ? "Still waiting for " + what + ": it is slow, not gone. Retry loads the chat anew."
    : "Waiting for " + what + "…";
}

function parseNavigationMessage(message: Record<string, unknown>): PaseoPluginMessage | null {
  switch (message.type) {
    case "navigated":
    case "on-chat":
    case "load-failed":
      return text(message.agentId) ? { type: message.type, agentId: text(message.agentId)! } : null;
    case "selection":
      return selectionMessage(message.agentIds);
    case "navigation-error":
      return parentErrorMessage(message);
    default:
      return null;
  }
}

function selectionMessage(agentIds: unknown): PaseoPluginMessage | null {
  if (!Array.isArray(agentIds) || !agentIds.every((id) => typeof id === "string" && id.length > 0))
    return null;
  return { type: "selection", agentIds: [...new Set(agentIds)] };
}

function parentErrorMessage(message: Record<string, unknown>): PaseoPluginMessage | null {
  const sourceAgentId = text(message.sourceAgentId);
  const code = text(message.code);
  if (
    message.context !== "parent" ||
    !sourceAgentId ||
    !nullableText(message.targetAgentId) ||
    !code
  )
    return null;
  return {
    type: "navigation-error",
    context: "parent",
    sourceAgentId,
    targetAgentId: message.targetAgentId,
    code,
    ...named(message, "message"),
  };
}

/** What the pane says when the frame never shows the agent it was asked for. When the frame
 * already names the agent's page no second load runs, and the sentence says exactly that. */
export function agentTimeoutText(agentId: string, fallbackLoaded: boolean): string {
  return fallbackLoaded
    ? `The embedded chat did not show this execution's agent "${agentId}" in time, so it loaded the agent's page instead. Retry asks the application again.`
    : `The embedded chat did not show this execution's agent "${agentId}" in time; the frame already shows the agent's page. Retry asks the application again.`;
}

/** What the pane says after the one load that follows a ping the application did not answer. */
export function agentSilentText(agentId: string, fallbackLoaded: boolean): string {
  const start = `The embedded chat answered nothing while it was asked for this execution's agent "${agentId}"`;
  return fallbackLoaded
    ? start + ", so the frame loaded the agent's page. The next change of the selection is a switch again."
    : start + "; the frame already shows the agent's page. Retry loads it anew.";
}

/** What the pane says when the frame cannot show the agent it was asked for. */
export function agentProblemText(code: string, detail?: string): string {
  if (code === "agent-archived") {
    return "This execution's agent is archived; the chat below stays where it was.";
  }
  if (code === "agent-not-found") {
    return "This execution's agent is gone (the host has no such agent); the chat below stays where it was.";
  }
  return "The embedded chat could not show this execution's agent" + (detail ? ": " + detail : ".");
}

export function parentProblemText(code: string, detail?: string): string {
  if (code === "agent-archived")
    return "The parent chat is archived; the child chat stays where it is.";
  if (code === "agent-not-found")
    return "The parent chat is missing; the child chat stays where it is.";
  return "The parent chat could not be opened" + (detail ? ": " + detail : ".");
}
