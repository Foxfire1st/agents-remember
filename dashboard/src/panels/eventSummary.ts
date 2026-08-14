import {
  findLifecycleEnclosure,
  groupEnclosuresByLifecycle,
  taskDocumentLabel,
  taskDocsForLifecycle,
  taskLabel,
} from "../data/taskIdentity";
import type { Actor, ObserverEvent, Trust } from "../types/event";
import type {
  Analytics,
  EnclosureNode,
  LifecycleProjection,
  Phase,
  TaskDocNode,
} from "../types/projection";

export type EventVisibility = "normal" | "quiet" | "hidden";

export interface EventSummary {
  label: string;
  context?: string;
  meta: string[];
  title?: string;
  diagnosticKind: string;
  visibility: EventVisibility;
}

export interface EventSummaryContext {
  analytics: Analytics | null;
  enclosures: Record<string, EnclosureNode>;
  enclosuresByLifecycle: Map<string, EnclosureNode>;
  lifecycles: Record<string, LifecycleProjection>;
  taskDocsByLifecycle: Map<string, TaskDocNode[]>;
}

interface LifecycleContext {
  label?: string;
  lifecycleId?: string;
  repoId?: string;
  enclosure?: string;
}

const ACTOR_LABELS: Record<Actor, string> = {
  developer: "developer",
  model: "agent",
  system: "system",
};

const TRUST_LABELS: Record<Trust, string> = {
  approved: "approved",
  declared: "declared",
  inferred: "inferred",
  observed: "observed",
};

const PHASE_LABELS: Record<Phase, string> = {
  build: "Build",
  close: "Close",
  decide: "Decide",
  "reframe-research": "Research",
  request: "Request",
  "trust-checkpoint": "Trust checkpoint",
};

const TOOL_LABELS: Record<string, string> = {
  cgc_callers: "Checked callers",
  cgc_callees: "Checked callees",
  cgc_complexity: "Checked code complexity",
  cgc_dependencies: "Checked dependencies",
  cgc_symbol_search: "Found code symbol",
  context_packet: "Checked workspace context",
  drift_check: "Checked onboarding drift",
  gate_create: "Opened approval request",
  gate_response_wait: "Waited for approval",
  gate_wait: "Waited for approval",
  grepai_search: "Searched onboarding",
  lifecycle_gate: "Asked for approval",
  lifecycle_phase: "Updated phase",
  lifecycle_resume: "Resumed lifecycle",
  lifecycle_start: "Started lifecycle",
  memory_quality_check: "Checked memory quality",
  read_ar_files: "Read source with onboarding",
  route_index_refresh: "Checked route indexes",
  task_doc: "Updated task document",
  worktree_attach: "Attached to task",
  worktree_cleanup: "Cleaned up task worktree",
  worktree_integrate: "Integrated task branch",
  worktree_start: "Started task worktree",
  worktree_status: "Checked worktree status",
};

const TIME_FORMATTER = new Intl.DateTimeFormat(undefined, {
  hour: "2-digit",
  hour12: false,
  minute: "2-digit",
  second: "2-digit",
});

export function buildEventSummaryContext(
  lifecycles: Record<string, LifecycleProjection>,
  enclosures: Record<string, EnclosureNode>,
  analytics: Analytics | null,
): EventSummaryContext {
  return {
    analytics,
    enclosures,
    enclosuresByLifecycle: groupEnclosuresByLifecycle(Object.values(enclosures)),
    lifecycles,
    taskDocsByLifecycle: groupTaskDocsByLifecycle(analytics?.taskDocuments ?? []),
  };
}

const SUMMARIZERS: Record<
  string,
  (event: ObserverEvent, context: EventSummaryContext) => EventSummary
> = {
  "read.packet": summarizeReadPacket,
  "tool.completed": summarizeToolCompleted,
  "lifecycle.phase-changed": summarizePhaseChanged,
  "lifecycle.started": (event, context) => summarizeLifecycleEvent(event, context, "Started lifecycle"),
  "lifecycle.resumed": (event, context) => summarizeLifecycleEvent(event, context, "Resumed lifecycle"),
  "lifecycle.paused": (event, context) => summarizeLifecycleEvent(event, context, "Paused lifecycle"),
  "lifecycle.promoted": (event, context) => summarizeLifecycleEvent(event, context, "Saved task lifecycle"),
  "lifecycle.ended": summarizeEnded,
  "lifecycle.blocked": summarizeBlocked,
  "lifecycle.heartbeat": summarizeHeartbeat,
};

function summarizeDefault(event: ObserverEvent, context: EventSummaryContext): EventSummary {
  return event.kind.startsWith("gate.")
    ? summarizeGateEvent(event, context)
    : summarizeUnknown(event, context);
}

export function summarizeEvent(
  event: ObserverEvent,
  context: EventSummaryContext,
): EventSummary {
  const summarizer = SUMMARIZERS[event.kind] ?? summarizeDefault;
  return summarizer(event, context);
}

export function eventSummaryContextReady(
  event: ObserverEvent,
  context: EventSummaryContext,
): boolean {
  if (event.lifecycleId) {
    if (context.lifecycles[event.lifecycleId]) return true;
    if (event.enclosure && context.enclosures[event.enclosure]) return true;
    return (context.taskDocsByLifecycle.get(event.lifecycleId) ?? []).length > 0;
  }
  if (event.enclosure) {
    return Boolean(context.enclosures[event.enclosure]);
  }
  return true;
}

export function formatEventTime(ts: string | undefined): string {
  if (!ts) return "-";
  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) return "-";
  return TIME_FORMATTER.format(date);
}

export function actorLabel(actor: Actor): string {
  return ACTOR_LABELS[actor] ?? actor;
}

export function trustLabel(trust: Trust): string {
  return TRUST_LABELS[trust] ?? trust;
}

function summarizeReadPacket(
  event: ObserverEvent,
  context: EventSummaryContext,
): EventSummary {
  const data = event.data ?? {};
  const files = Array.isArray(data.files) ? (data.files as unknown[]) : [];
  const paths = files
    .map((file) => (isRecord(file) && typeof file.path === "string" ? file.path : ""))
    .filter((path) => path.length > 0);
  const repo = typeof data.repoId === "string" ? data.repoId : undefined;
  const firstName = paths.length > 0 ? (paths[0].split("/").pop() ?? paths[0]) : "(no file)";
  const label = paths.length > 1 ? `Read: ${firstName} +${paths.length - 1} more` : `Read: ${firstName}`;
  return {
    context: lifecycleContext(event, context).label ?? repo,
    diagnosticKind: event.kind,
    label,
    meta: compact([repo]),
    title: paths.join("\n"),
    visibility: "normal",
  };
}

function summarizeToolCompleted(
  event: ObserverEvent,
  context: EventSummaryContext,
): EventSummary {
  const data = event.data ?? {};
  const tool = typeof data.tool === "string" ? data.tool : undefined;
  const ok = typeof data.ok === "boolean" ? data.ok : undefined;
  const tokens = typeof data.tokens === "number" ? data.tokens : undefined;
  const label = `${toolLabel(tool)}${ok === false ? " failed" : ""}`;
  return {
    context: lifecycleContext(event, context).label,
    diagnosticKind: event.kind,
    label,
    meta: compact([ok === undefined ? undefined : ok ? "ok" : "failed", tokenLabel(tokens)]),
    title: rawDiagnosticTitle(event),
    visibility: "normal",
  };
}

function summarizePhaseChanged(
  event: ObserverEvent,
  context: EventSummaryContext,
): EventSummary {
  const phase = typeof event.data?.phase === "string" ? event.data.phase : undefined;
  const label = isPhase(phase) ? `Moved to ${PHASE_LABELS[phase]}` : "Updated phase";
  return {
    context: lifecycleContext(event, context).label,
    diagnosticKind: event.kind,
    label,
    meta: compact([phase && !isPhase(phase) ? phase : undefined]),
    title: rawDiagnosticTitle(event),
    visibility: "normal",
  };
}

function summarizeLifecycleEvent(
  event: ObserverEvent,
  context: EventSummaryContext,
  label: string,
): EventSummary {
  const data = event.data ?? {};
  return {
    context: lifecycleContext(event, context).label,
    diagnosticKind: event.kind,
    label,
    meta: compact([stringField(data, "cause"), stringField(data, "scope")]),
    title: rawDiagnosticTitle(event),
    visibility: "normal",
  };
}

function summarizeEnded(event: ObserverEvent, context: EventSummaryContext): EventSummary {
  const outcome = stringField(event.data ?? {}, "outcome");
  return {
    context: lifecycleContext(event, context).label,
    diagnosticKind: event.kind,
    label: outcome === "completed" ? "Completed lifecycle" : outcome === "abandoned" ? "Abandoned lifecycle" : "Ended lifecycle",
    meta: compact([outcome]),
    title: rawDiagnosticTitle(event),
    visibility: "normal",
  };
}

function summarizeBlocked(event: ObserverEvent, context: EventSummaryContext): EventSummary {
  const prompt = askPrompt(event.data?.ask) ?? stringField(event.data ?? {}, "prompt");
  const askKind = askKindLabel(event.data?.ask);
  return {
    context: lifecycleContext(event, context).label,
    diagnosticKind: event.kind,
    label: prompt ? `Waiting: ${prompt}` : "Waiting for developer input",
    meta: compact([askKind]),
    title: rawDiagnosticTitle(event),
    visibility: "normal",
  };
}

function summarizeGateEvent(event: ObserverEvent, context: EventSummaryContext): EventSummary {
  const prompt = askPrompt(event.data?.ask);
  return {
    context: lifecycleContext(event, context).label,
    diagnosticKind: event.kind,
    label: prompt ? `Gate: ${prompt}` : humanizeIdentifier(event.kind),
    meta: compact([stringField(event.data ?? {}, "state"), askKindLabel(event.data?.ask)]),
    title: rawDiagnosticTitle(event),
    visibility: "normal",
  };
}

function summarizeHeartbeat(
  event: ObserverEvent,
  context: EventSummaryContext,
): EventSummary {
  const data = event.data ?? {};
  return {
    context: lifecycleContext(event, context).label,
    diagnosticKind: event.kind,
    label: "Heartbeat",
    meta: compact([stringField(data, "state"), stringField(data, "phase")]),
    title: rawDiagnosticTitle(event),
    visibility: "hidden",
  };
}

function summarizeUnknown(event: ObserverEvent, context: EventSummaryContext): EventSummary {
  return {
    context: lifecycleContext(event, context).label ?? event.enclosure ?? event.repoId ?? event.lifecycleId,
    diagnosticKind: event.kind,
    label: event.kind,
    meta: [],
    title: rawDiagnosticTitle(event),
    visibility: "normal",
  };
}

function enclosureLabel(enclosure: EnclosureNode, fallback: string): string {
  return enclosure.leafId || enclosure.taskName || fallback;
}

function lifecycleBranch(
  context: EventSummaryContext,
  lifecycle: LifecycleProjection,
): LifecycleContext {
  const directDocs = taskDocsForLifecycle(lifecycle, context.analytics?.taskDocuments ?? []);
  const enclosure = findLifecycleEnclosure(
    lifecycle,
    context.enclosures,
    context.enclosuresByLifecycle,
  );
  return {
    enclosure: lifecycle.enclosure,
    label: taskLabel(lifecycle, directDocs, enclosure),
    lifecycleId: lifecycle.id,
    repoId: lifecycle.repoId,
  };
}

function enclosureBranch(
  event: ObserverEvent,
  context: EventSummaryContext,
): LifecycleContext {
  const enclosure = context.enclosures[event.enclosure ?? ""];
  if (!enclosure) {
    return { enclosure: event.enclosure, repoId: event.repoId };
  }
  return {
    enclosure: event.enclosure,
    label: enclosureLabel(enclosure, event.enclosure ?? ""),
    lifecycleId: event.lifecycleId ?? enclosure.lifecycleId,
    repoId: enclosure.repoName,
  };
}

function docsBranch(
  event: ObserverEvent,
  context: EventSummaryContext,
  lifecycleId: string,
): LifecycleContext {
  const directDocs = context.taskDocsByLifecycle.get(lifecycleId) ?? [];
  return {
    enclosure: event.enclosure,
    label: taskDocumentLabel(directDocs, event.enclosure ?? lifecycleId),
    lifecycleId,
    repoId: event.repoId ?? directDocs[0]?.repository,
  };
}

function lifecycleContext(
  event: ObserverEvent,
  context: EventSummaryContext,
): LifecycleContext {
  if (event.lifecycleId) {
    const lifecycle = context.lifecycles[event.lifecycleId];
    if (lifecycle) return lifecycleBranch(context, lifecycle);
    if (event.enclosure && context.enclosures[event.enclosure]) {
      return enclosureBranch(event, context);
    }
    return docsBranch(event, context, event.lifecycleId);
  }
  if (event.enclosure && context.enclosures[event.enclosure]) {
    return enclosureBranch(event, context);
  }
  return { enclosure: event.enclosure, repoId: event.repoId };
}

function groupTaskDocsByLifecycle(taskDocuments: TaskDocNode[]): Map<string, TaskDocNode[]> {
  const byLifecycle = new Map<string, TaskDocNode[]>();
  for (const doc of taskDocuments) {
    if (!doc.lifecycleId) continue;
    const existing = byLifecycle.get(doc.lifecycleId);
    if (existing) {
      existing.push(doc);
    } else {
      byLifecycle.set(doc.lifecycleId, [doc]);
    }
  }
  return byLifecycle;
}

function rawDiagnosticTitle(event: ObserverEvent): string {
  const details = [
    `kind: ${event.kind}`,
    `actor: ${event.actor}`,
    `trust: ${event.trust}`,
    event.lifecycleId ? `lifecycle: ${event.lifecycleId}` : undefined,
    event.enclosure ? `enclosure: ${event.enclosure}` : undefined,
    event.repoId ? `repo: ${event.repoId}` : undefined,
    event.data ? `data: ${JSON.stringify(event.data, null, 2)}` : undefined,
  ];
  return compact(details).join("\n");
}

function toolLabel(tool: string | undefined): string {
  if (!tool) return "Completed tool call";
  return TOOL_LABELS[tool] ?? humanizeIdentifier(tool);
}

function tokenLabel(tokens: number | undefined): string | undefined {
  return tokens === undefined ? undefined : `${tokens.toLocaleString()} tokens`;
}

function askPrompt(ask: unknown): string | undefined {
  if (!isRecord(ask)) return undefined;
  return stringField(ask, "prompt") ?? stringField(ask, "question") ?? stringField(ask, "ask");
}

function askKindLabel(ask: unknown): string | undefined {
  if (!isRecord(ask)) return undefined;
  const kind = stringField(ask, "kind");
  return kind ? `${humanizeIdentifier(kind)} request` : undefined;
}

function stringField(record: Record<string, unknown>, key: string): string | undefined {
  const value = record[key];
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isPhase(value: string | undefined): value is Phase {
  return value !== undefined && value in PHASE_LABELS;
}

function humanizeIdentifier(value: string): string {
  const words = value
    .replace(/[-_.]+/g, " ")
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  if (words.length === 0) return value;
  const [first, ...rest] = words;
  return [capitalize(first), ...rest].join(" ");
}

function capitalize(value: string): string {
  return `${value.slice(0, 1).toUpperCase()}${value.slice(1)}`;
}

function compact(values: Array<string | undefined>): string[] {
  return values.filter((value): value is string => !!value);
}
