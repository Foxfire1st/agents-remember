import type { Actor, ObserverEvent, Trust } from '../types/event';

export type EventVisibility = 'normal' | 'quiet' | 'hidden';

export interface EventSummary {
  label: string;
  context?: string;
  meta: string[];
  title?: string;
  diagnosticKind: string;
  visibility: EventVisibility;
}

const ACTOR_LABELS: Record<Actor, string> = {
  developer: 'developer',
  model: 'agent',
  system: 'system',
};

const TRUST_LABELS: Record<Trust, string> = {
  approved: 'approved',
  declared: 'declared',
  inferred: 'inferred',
  observed: 'observed',
};

const TOOL_LABELS: Record<string, string> = {
  context_packet: 'Checked workspace context',
  memory_quality_check: 'Checked memory quality',
  read_ar_files: 'Read source with onboarding',
  role_message: 'Sent message',
  role_start: 'Started agent',
  route_index_refresh: 'Checked route indexes',
  task_doc: 'Updated task document',
  worktree_attach: 'Attached to task',
  worktree_cleanup: 'Cleaned up task worktree',
  worktree_integrate: 'Integrated task branch',
  worktree_start: 'Started task worktree',
  worktree_status: 'Checked worktree status',
};

const TIME_FORMATTER = new Intl.DateTimeFormat(undefined, {
  hour: '2-digit',
  hour12: false,
  minute: '2-digit',
  second: '2-digit',
});

const SUMMARIZERS: Record<string, (event: ObserverEvent) => EventSummary> = {
  'read.packet': summarizeReadPacket,
  'tool.completed': summarizeToolCompleted,
};

export function summarizeEvent(event: ObserverEvent): EventSummary {
  const summarizer = SUMMARIZERS[event.kind] ?? summarizeUnknown;
  return summarizer(event);
}

export function formatEventTime(ts: string | undefined): string {
  if (!ts) return '-';
  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) return '-';
  return TIME_FORMATTER.format(date);
}

export function actorLabel(actor: Actor): string {
  return ACTOR_LABELS[actor] ?? actor;
}

export function trustLabel(trust: Trust): string {
  return TRUST_LABELS[trust] ?? trust;
}

function summarizeReadPacket(event: ObserverEvent): EventSummary {
  const data = event.data ?? {};
  const files = Array.isArray(data.files) ? (data.files as unknown[]) : [];
  const paths = files
    .map((file) => (isRecord(file) && typeof file.path === 'string' ? file.path : ''))
    .filter((path) => path.length > 0);
  const repo = typeof data.repoId === 'string' ? data.repoId : undefined;
  const firstName = paths.length > 0 ? (paths[0].split('/').pop() ?? paths[0]) : '(no file)';
  const label =
    paths.length > 1 ? `Read: ${firstName} +${paths.length - 1} more` : `Read: ${firstName}`;
  return {
    context: event.repoId ?? repo,
    diagnosticKind: event.kind,
    label,
    meta: compact([repo]),
    title: paths.join('\n'),
    visibility: 'normal',
  };
}

function summarizeToolCompleted(event: ObserverEvent): EventSummary {
  const data = event.data ?? {};
  const tool = typeof data.tool === 'string' ? data.tool : undefined;
  const ok = typeof data.ok === 'boolean' ? data.ok : undefined;
  const tokens = typeof data.tokens === 'number' ? data.tokens : undefined;
  const label = `${toolLabel(tool)}${ok === false ? ' failed' : ''}`;
  // Earlier-kind rows carry no caller facts and keep their base outcome
  // labels (ok/failed); rows with caller facts use the host-path outcome.
  const hasCallerFacts = stringField(data, 'role') !== undefined;
  return {
    context: toolCallerContext(event) ?? event.repoId,
    diagnosticKind: event.kind,
    label,
    meta: compact([
      hasCallerFacts ? toolOutcome(data, ok) : legacyOutcome(ok),
      hasCallerFacts ? toolTargetRole(data) : undefined,
      tokenLabel(tokens),
    ]),
    title: rawDiagnosticTitle(event),
    visibility: 'normal',
  };
}

function legacyOutcome(ok: boolean | undefined): string | undefined {
  if (ok === undefined) return undefined;
  return ok ? 'ok' : 'failed';
}

// MIK-R114 rule 8: a row of a host-path tool event shows who called -- the
// role and the task -- with the tool and its outcome. Rows without caller facts
// keep the available event context. A role or a task the panel does not know
// is shown as it is: known roles keep
// their friendly labels, unknown role values are shown verbatim.
const KNOWN_ROLES = new Set([
  'architect',
  'curator',
  'manager',
  'orchestrator',
  'reviewer',
  'system-specialist',
  'worker',
]);

function roleLabel(value: string): string {
  return KNOWN_ROLES.has(value) ? humanizeIdentifier(value) : value;
}

function toolCallerContext(event: ObserverEvent): string | undefined {
  const data = event.data ?? {};
  const role = stringField(data, 'role');
  if (!role) return undefined;
  if (role === 'chat') return chatCallerContext(data);
  const task = firstPresent([data.taskTitle, data.taskId, data.taskPath]);
  const who = roleLabel(role);
  return task ? `${who} · ${task}` : who;
}

function chatCallerContext(data: Record<string, unknown>): string {
  const harness = stringField(data, 'harness');
  return harness ? `chat without a role · ${harness}` : 'chat without a role';
}

function firstPresent(values: unknown[]): string | undefined {
  for (const value of values) {
    if (typeof value === 'string' && value.length > 0) return value;
  }
  return undefined;
}

// The outcome of a tool with its result: done, refused with its code, or the
// state the answer reached.
function toolOutcome(data: Record<string, unknown>, ok: boolean | undefined): string | undefined {
  const outcome = baseOutcome(data, ok);
  if (outcome === undefined) return undefined;
  const code = stringField(data, 'code');
  if (code && (outcome === 'refused' || ok === false)) return `refused (${code})`;
  return outcome;
}

function baseOutcome(data: Record<string, unknown>, ok: boolean | undefined): string | undefined {
  const state = stringField(data, 'state');
  if (state) return state;
  if (ok === true) return 'done';
  if (ok === false) return 'refused';
  return undefined;
}

// For role_start and role_message: the role of the agent started or addressed.
// Known roles keep their friendly labels; an unknown value is shown verbatim.
function toolTargetRole(data: Record<string, unknown>): string | undefined {
  const target = typeof data.targetRole === 'string' ? data.targetRole : undefined;
  return target && target.length > 0 ? roleLabel(target) : undefined;
}

function summarizeUnknown(event: ObserverEvent): EventSummary {
  return {
    context: event.enclosure ?? event.repoId,
    diagnosticKind: event.kind,
    label: event.kind,
    meta: [],
    title: rawDiagnosticTitle(event),
    visibility: 'normal',
  };
}

function rawDiagnosticTitle(event: ObserverEvent): string {
  const details = [
    `kind: ${event.kind}`,
    `actor: ${event.actor}`,
    `trust: ${event.trust}`,
    event.enclosure ? `enclosure: ${event.enclosure}` : undefined,
    event.repoId ? `repo: ${event.repoId}` : undefined,
    event.data ? `data: ${JSON.stringify(event.data, null, 2)}` : undefined,
  ];
  return compact(details).join('\n');
}

function toolLabel(tool: string | undefined): string {
  if (!tool) return 'Completed tool call';
  return TOOL_LABELS[tool] ?? humanizeIdentifier(tool);
}

function tokenLabel(tokens: number | undefined): string | undefined {
  return tokens === undefined ? undefined : `${tokens.toLocaleString()} tokens`;
}

function stringField(record: Record<string, unknown>, key: string): string | undefined {
  const value = record[key];
  return typeof value === 'string' && value.length > 0 ? value : undefined;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function humanizeIdentifier(value: string): string {
  const words = value
    .replace(/[-_.]+/g, ' ')
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  if (words.length === 0) return value;
  const [first, ...rest] = words;
  return [capitalize(first), ...rest].join(' ');
}

function capitalize(value: string): string {
  return `${value.slice(0, 1).toUpperCase()}${value.slice(1)}`;
}

function compact(values: Array<string | undefined>): string[] {
  return values.filter((value): value is string => !!value);
}
