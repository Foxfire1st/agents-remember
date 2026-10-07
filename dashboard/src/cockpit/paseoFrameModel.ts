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

export interface AvailablePaseoFrame {
  available: true;
  frameBaseUrl: string;
  frameOrigin: string;
  serverId: string;
  projectsWorkspaceId: string | null;
  /** Why the route named no Projects workspace; `null` when it named one. */
  projectsWorkspaceDetail: string | null;
}

export type PaseoFrameDescriptor =
  AvailablePaseoFrame | { available: false; reason: PaseoFrameUnavailableReason; detail: string };

export type PaseoPluginMessage =
  | { type: "ready" }
  | { type: "pong" }
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
      return { type: "ready" };
    case "pong":
      return { type: "pong" };
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

function parseNavigationMessage(message: Record<string, unknown>): PaseoPluginMessage | null {
  switch (message.type) {
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
