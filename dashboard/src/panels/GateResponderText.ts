import type { GateNode } from "../types/projection";

export type GateResponseStatus =
  | "idle"
  | "recording"
  | "sending"
  | "delivered"
  | "inbox"
  | "unconfirmed"
  | "inbox-error"
  | "decision-error"
  | "stale-gate"
  | "no-open-gate";

function pretty(value: Record<string, unknown> | undefined): string {
  if (!value || Object.keys(value).length === 0) return "{}";
  return JSON.stringify(value, null, 2);
}

export function humanKey(key: string): string {
  const spaced = key.replace(/([a-z0-9])([A-Z])/g, "$1 $2").replace(/[-_]+/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1).toLowerCase();
}

function formatValue(value: unknown): string {
  if (typeof value === "string") return value.trim() || "empty";
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) {
    if (value.length === 0) return "none";
    return value.map((entry) => formatValue(entry)).join(", ");
  }
  if (value && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    if (entries.length === 0) return "empty object";
    return entries.map(([key, nested]) => `${humanKey(key)}: ${formatValue(nested)}`).join("; ");
  }
  return "not provided";
}

function pickString(source: Record<string, unknown> | undefined, keys: string[]): string | null {
  for (const key of keys) {
    const value = source?.[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return null;
}

export function askQuestion(ask: Record<string, unknown> | undefined): string | null {
  return pickString(ask, ["question", "prompt", "request", "message"]);
}

function gateKindLabel(kind: string): string {
  return humanKey(kind);
}

const SUMMARY_KEYS = new Set([
  "question",
  "prompt",
  "request",
  "message",
  "summary",
  "objective",
  "changedPaths",
  "files",
  "paths",
  "commands",
  "commitMessage",
  "requiredDecision",
  "required_decision",
  "options",
]);

function contextLines(source: Record<string, unknown> | undefined): string[] {
  if (!source) return [];
  const lines: string[] = [];
  for (const key of [
    "objective",
    "summary",
    "changedPaths",
    "files",
    "paths",
    "commands",
    "commitMessage",
    "options",
  ]) {
    if (source[key] !== undefined) lines.push(`${humanKey(key)}: ${formatValue(source[key])}`);
  }
  const extras = Object.entries(source).filter(
    ([key, value]) => !SUMMARY_KEYS.has(key) && value !== undefined && value !== null,
  );
  for (const [key, value] of extras) lines.push(`${humanKey(key)}: ${formatValue(value)}`);
  return lines;
}

function gateHeaderLines(gateNode: GateNode | undefined, lines: string[]): void {
  if (!gateNode) {
    lines.push("Gate: Agent question");
    return;
  }
  lines.push(`Gate: ${gateKindLabel(gateNode.kind)}`);
  lines.push(`State: ${humanKey(gateNode.state)}`);
  if (gateNode.decisions.length > 0) {
    lines.push(`Decision options: ${gateNode.decisions.join(", ")}`);
  }
}

export function requestText(
  gateNode: GateNode | undefined,
  ask: Record<string, unknown> | undefined,
): string {
  const lines: string[] = [];
  const packet = gateNode?.packet;
  const subject =
    pickString(packet, ["question", "prompt", "request", "message", "summary"]) ??
    askQuestion(ask);

  gateHeaderLines(gateNode, lines);
  if (subject) lines.push("", "Request:", subject);

  const packetContext = contextLines(packet);
  if (packetContext.length > 0) lines.push("", "Context:", ...packetContext);
  const askContext = contextLines(ask).filter((line) => !packetContext.includes(line));
  if (askContext.length > 0) lines.push("", "Ask context:", ...askContext);
  if (!subject && packetContext.length === 0 && askContext.length === 0) {
    lines.push("", "No additional request details were supplied.");
  }
  return lines.join("\n");
}

export function diagnosticText(
  gateNode: GateNode | undefined,
  ask: Record<string, unknown> | undefined,
): string {
  const blocks: string[] = [];
  if (gateNode) blocks.push(["Gate packet:", pretty(gateNode.packet)].join("\n"));
  if (ask) blocks.push(["Ask payload:", pretty(ask)].join("\n"));
  return blocks.join("\n\n");
}

export function packageResponse(
  lifecycleId: string,
  gateNode: GateNode | undefined,
  ask: Record<string, unknown> | undefined,
  response: string,
): string {
  return [
    `Dashboard response for lifecycle ${lifecycleId}`,
    gateNode ? `Gate: ${gateNode.kind} (${gateNode.state})` : "Gate: ask",
    "",
    response.trim(),
    "",
    "--- request summary ---",
    requestText(gateNode, ask),
  ].join("\n");
}

type StatusCopy = (label: string | undefined, recorded: boolean) => string;

const STATUS_COPY: Record<GateResponseStatus, StatusCopy> = {
  idle: () => "",
  recording: () => "Recording decision...",
  sending: (_label, recorded) => (recorded ? "Notifying agent..." : "Sending..."),
  delivered: (label, recorded) => {
    const sent = label ? `sent to ${label}` : "sent";
    return recorded ? `Decision recorded; ${sent}.` : label ? `Sent to ${label}.` : "Sent.";
  },
  inbox: (_label, recorded) =>
    recorded ? "Decision recorded; queued agent notice." : "Queued in external inbox.",
  unconfirmed: (_label, recorded) =>
    recorded
      ? "Decision recorded; couldn't confirm agent notice. Retry?"
      : "Couldn't confirm delivery. Retry?",
  "inbox-error": (_label, recorded) =>
    recorded
      ? "Decision recorded; couldn't queue agent notice. Retry?"
      : "Couldn't queue external inbox response. Retry?",
  "stale-gate": () => "This gate was replaced by a newer request.",
  "no-open-gate": () => "No open gate exists for this task.",
  "decision-error": () => "Couldn't record the decision. Retry?",
};

export function statusText(
  status: GateResponseStatus,
  label: string | undefined,
  recordedDecision: boolean,
): string {
  return STATUS_COPY[status]?.(label, recordedDecision) ?? "";
}

export function isWorktreeGateKind(kind: string): boolean {
  return /closeout|push|integration|cleanup/.test(kind);
}
