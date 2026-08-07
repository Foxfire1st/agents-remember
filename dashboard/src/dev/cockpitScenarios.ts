import { announcerStore } from "../data/announcer";
import { resetCatalogPollForDev } from "../data/catalogPoll";
import {
  capabilityCatalogStore,
  resetCapabilityCatalogForDev,
} from "../data/capabilityCatalog";
import type { HarnessInfo } from "../data/harnessCatalog";
import { sessionCockpitStore } from "../data/sessionCockpitStore";
import { lifecycleNoticeStore } from "../data/sessionLifecycle";
import { ptyHarvestStore } from "../data/ptyHarvest";
import { resetSetClientForDev } from "../data/setClient";
import {
  fromTerminalSessionInfo,
  resetSessionConnectionRegistriesForDev,
  sessionStore,
} from "../data/sessions";
import { resetSubmissionLifecycleClientForDev } from "../data/submissionLifecycleClient";
import {
  capabilityEnvelope,
  CLAUDE_FRESH_SESSION_SNAPSHOT,
  effortOption,
  modelRow,
} from "../test/fixtures/capabilityEnvelopes";
import {
  FLEET,
  RAW_TERMINAL_ROW,
  catalogRow,
} from "../test/fixtures/catalogRows";
import {
  FAILED_LAUNCH_ROWS,
  LAUNCH_CONFLICT,
  OPENED_STARTING,
  PENDING_INTERACTION_ROW,
} from "../test/fixtures/openResponses";
import {
  L5_BRIDGE_EPOCH,
  L5_READY_SESSION,
} from "../test/fixtures/submitScenarios";
import type { TerminalCatalogRow } from "../types/terminalCatalog";
import type { TerminalOpenSuccessBody } from "../types/terminalOpen";
// The probe shapes and the `window` augmentation live in benchProbes.ts: the Playwright drivers
// read these globals from their own tsconfig project, so the contract cannot be declared here.
import type { CockpitBenchProbe, CockpitResetAudit } from "./benchProbes";

// Dedicated Chats-cockpit scenarios. They are catalogued by scenarios.ts, but their server
// facts stay here so the already-large Engine Room timeline file does not become a second API
// fixture. Every scenario drives the real stores and the real fetch clients; only transport is
// replaced, and only under /dev/bench.

export type CockpitScenarioKind =
  | "launch-happy"
  | "launch-conflict"
  | "failed-harnesses"
  | "set-promotion"
  | "submit-reconcile"
  | "interaction-answer"
  | "fleet-12"
  | "ended-presentation"
  | "terminal-focus"
  | "pty-dropped"
  | "catalog-stale";

export interface CockpitScenarioDefinition {
  name: string;
  label: string;
  caption: string;
  kind: CockpitScenarioKind;
  rows: readonly TerminalCatalogRow[];
  socket: "live" | "dropped";
}

const READY = catalogRow({
  id: "scenario-ready",
  label: "worker-scenario-ready",
  lifecycleId: "lc-scenario-ready",
  spawnRole: "worker",
  seatRole: "worker",
  controlState: "ready",
  turnState: "turn-ended",
  resolvedModel: "claude-fable-5[1m]",
  resolvedEffort: "high",
});

const SET_PROMOTION = catalogRow({
  ...READY,
  id: "scenario-set-promotion",
  label: "worker-set-promotion",
  lifecycleId: "lc-set-promotion",
  turnState: "working",
});

const INTERACTION = {
  ...PENDING_INTERACTION_ROW,
  lifecycleId: "lc-interaction-scenario",
};

const LANDED_TRANSCRIPT = catalogRow({
  id: "scenario-landed-transcript",
  label: "landed transcript",
  status: "landed",
  landedReason: "leaf integrated",
});

const ENDED_EXITED = catalogRow({
  id: "scenario-ended-exited",
  label: "restored exited chat",
  status: "exited",
  exitEvidence: "tmux-command-failed",
});

const ENDED_RETIRED = catalogRow({
  id: "scenario-ended-retired",
  label: "restored retired chat",
  status: "terminated",
  retiredReason: "seat superseded",
});

export const COCKPIT_SCENARIOS: readonly CockpitScenarioDefinition[] = [
  {
    name: "sessions-launch-happy",
    label: "Chats · launch happy path",
    caption: "dynamic harness → model → effort → starting row",
    kind: "launch-happy",
    rows: [],
    socket: "live",
  },
  {
    name: "sessions-launch-conflict",
    label: "Chats · launch 409 conflict",
    caption: "live retained pair wins; selection remains visible",
    kind: "launch-conflict",
    rows: [READY],
    socket: "live",
  },
  {
    name: "sessions-failed-harnesses",
    label: "Chats · all harness launches fail",
    caption:
      "Claude, Codex, and Pi starting→failed rows retain refusal evidence",
    kind: "failed-harnesses",
    rows: [],
    socket: "live",
  },
  {
    name: "sessions-set-promotion",
    label: "Chats · queued set promotion",
    caption:
      "queued effort remains requested until turn-ended readback proves promotion",
    kind: "set-promotion",
    rows: [SET_PROMOTION],
    socket: "live",
  },
  {
    name: "sessions-submit-reconcile",
    label: "Chats · ambiguous submit reconciles",
    caption:
      "lost submit response; same requestId reconciliation proves delivery",
    kind: "submit-reconcile",
    rows: [{ ...L5_READY_SESSION, lifecycleId: "lc-submit-scenario" }],
    socket: "live",
  },
  {
    name: "sessions-interaction-answer",
    label: "Chats · answer pending interaction",
    caption: "choice answer travels through the projected agent-question gate",
    kind: "interaction-answer",
    rows: [INTERACTION],
    socket: "live",
  },
  {
    name: "sessions-fleet-12",
    label: "Chats · fleet 12 mixed",
    caption:
      "12 mixed seats, collapsed completed groups, and attention rollups",
    kind: "fleet-12",
    rows: FLEET,
    socket: "live",
  },
  {
    name: "sessions-ended-exited",
    label: "Chats · restored exited state",
    caption: "ended overview versus landed read-only terminal inspection",
    kind: "ended-presentation",
    rows: [ENDED_EXITED, LANDED_TRANSCRIPT],
    socket: "live",
  },
  {
    name: "sessions-ended-retired",
    label: "Chats · restored retired state",
    caption: "retired overview versus landed read-only terminal inspection",
    kind: "ended-presentation",
    rows: [ENDED_RETIRED, LANDED_TRANSCRIPT],
    socket: "live",
  },
  {
    name: "sessions-terminal-focus",
    label: "Chats · raw terminal focus",
    caption: "legacy-raw seat: interactive PTY, keyboard path, and cleanup continuity",
    kind: "terminal-focus",
    rows: [RAW_TERMINAL_ROW, LANDED_TRANSCRIPT],
    socket: "live",
  },
  {
    name: "sessions-pty-dropped",
    label: "Chats · dropped PTY websocket",
    caption: "connected pane drops and the focused freshness surface says so",
    kind: "pty-dropped",
    rows: [READY],
    socket: "dropped",
  },
  {
    name: "sessions-catalog-stale",
    label: "Chats · stale catalog poll",
    caption:
      "catalog failures leave the last honest rows and surface stale poll health",
    kind: "catalog-stale",
    rows: [READY],
    socket: "live",
  },
] as const;

const json = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

function requestPath(input: RequestInfo | URL): string {
  const raw =
    typeof input === "string"
      ? input
      : input instanceof URL
        ? input.href
        : input.url;
  return new URL(raw, window.location.origin).pathname;
}

function requestMethod(input: RequestInfo | URL, init?: RequestInit): string {
  return (
    init?.method ?? (input instanceof Request ? input.method : "GET")
  ).toUpperCase();
}

function requestBody(init?: RequestInit): unknown {
  if (typeof init?.body !== "string") return undefined;
  try {
    return JSON.parse(init.body) as unknown;
  } catch {
    return init.body;
  }
}

function count(
  probe: CockpitBenchProbe,
  method: string,
  path: string,
  init?: RequestInit,
): void {
  const key = `${method} ${path}`;
  probe.requestCounts[key] = (probe.requestCounts[key] ?? 0) + 1;
  probe.totalRequests += 1;
  const body = requestBody(init);
  probe.requests.push({
    method,
    path,
    ...(body === undefined ? {} : { body }),
  });
}

function cockpitStateSnapshot(): CockpitResetAudit {
  const cockpit = sessionCockpitStore.getState();
  const announcements = announcerStore.getState();
  return {
    sessionIds: sessionStore.getState().sessions.map((row) => row.id),
    activeId: sessionStore.getState().activeId,
    focusedSessionId: cockpit.focusedSessionId,
    cockpitSessionIds: Object.keys(cockpit.perSession),
    capabilityHarnesses: Object.keys(
      capabilityCatalogStore.getState().perHarness,
    ),
    polite: announcements.polite.text,
    assertive: announcements.assertive.text,
    lifecycleResiduals: lifecycleNoticeStore.getState().residuals.length,
    ptyHarvestSessions: Object.keys(ptyHarvestStore.getState().bySession),
    pollHealth: { ...cockpit.pollHealth },
  };
}

export function resetCockpitScenario(
  definition: CockpitScenarioDefinition,
): void {
  const rows = definition.rows.map((row) => ({ ...row }));
  resetCatalogPollForDev();
  resetSubmissionLifecycleClientForDev();
  resetCapabilityCatalogForDev();
  resetSetClientForDev();
  resetSessionConnectionRegistriesForDev();
  announcerStore.setState({
    polite: { text: "", seq: 0 },
    assertive: { text: "", seq: 0 },
  });
  lifecycleNoticeStore.setState({
    residuals: [],
    cleanupOutcome: null,
    cleanupFailure: null,
    sweptRetire: {},
  });
  ptyHarvestStore.setState({ bySession: {} });
  sessionStore.setState({ sessions: [], activeId: null, count: 0 });
  sessionStore
    .getState()
    .hydrate(rows.map(fromTerminalSessionInfo), rows[0]?.id);
  sessionCockpitStore.setState({
    focusedSessionId: rows[0]?.id ?? null,
    layout: { railCollapsed: false, inspectorCollapsed: false },
    paletteOpen: false,
    perSession: {},
    pollHealth:
      definition.kind === "catalog-stale"
        ? { lastBeatAt: Date.now(), missedBeats: 3, healthy: false }
        : { lastBeatAt: Date.now(), missedBeats: 0, healthy: true },
  });
  // Keymap/profile and orchestration-tree mode are intentional per-user preferences, not fixture
  // state, so this boundary preserves them explicitly while clearing every transient cockpit store.
  const resetAudit = cockpitStateSnapshot();
  window.__cockpitBenchResetAudit = resetAudit;
  if (window.__cockpitBench) window.__cockpitBench.resetAudit = resetAudit;
}

function failedLaunchCapabilities(harness: "claude" | "codex" | "pi") {
  const envelope = capabilityEnvelope(harness, "hit");
  if (harness === "claude") {
    return {
      ...envelope,
      capabilities: {
        ...envelope.capabilities,
        models: [
          ...envelope.capabilities.models,
          modelRow("ar-unknown-model", {
            effortOptions: [effortOption("max")],
            supportsEffort: true,
          }),
        ],
      },
    };
  }
  if (harness === "codex") {
    return {
      ...envelope,
      capabilities: {
        ...envelope.capabilities,
        models: envelope.capabilities.models.map((model) =>
          model.key === "gpt-5.6-sol"
            ? {
                ...model,
                effortOptions: [...model.effortOptions, effortOption("turbo")],
              }
            : model,
        ),
      },
    };
  }
  return {
    ...envelope,
    capabilities: {
      ...envelope.capabilities,
      models: [
        ...envelope.capabilities.models,
        modelRow("deepseek-v4-flash", {
          effortOptions: [effortOption("max")],
          supportsEffort: true,
          provider: "deepseek",
        }),
      ],
    },
  };
}

function failedRowForStarting(row: TerminalCatalogRow): TerminalCatalogRow {
  const fixture = FAILED_LAUNCH_ROWS.find(
    (candidate) => candidate.harness === row.harness,
  );
  if (!fixture) return row;
  return {
    ...fixture,
    id: row.id,
    label: row.label,
    resolvedModel: row.resolvedModel,
    resolvedEffort: row.resolvedEffort,
  };
}

interface ScenarioAuthority {
  definition: CockpitScenarioDefinition;
  rows: TerminalCatalogRow[];
  submitLost: boolean;
  launchFailuresReleased: boolean;
  setTurnEndedReleased: boolean;
  deferNextOpen: boolean;
  releaseOpen: (() => void) | null;
  probe: CockpitBenchProbe;
}

type ScenarioRoute = (
  authority: ScenarioAuthority,
  path: string,
  method: string,
  init?: RequestInit,
) => Promise<Response | null>;

async function sessionsRoute(
  authority: ScenarioAuthority,
  path: string,
  method: string,
): Promise<Response | null> {
  if (path !== "/api/terminal/sessions" || method !== "GET") return null;
  if (authority.definition.kind === "catalog-stale") {
    return json({ status: "unavailable" }, 503);
  }
  if (authority.definition.kind === "failed-harnesses" && authority.launchFailuresReleased) {
    authority.rows = authority.rows.map((row) =>
      row.controlState === "starting" ? failedRowForStarting(row) : row,
    );
  }
  if (authority.definition.kind === "set-promotion" && authority.setTurnEndedReleased) {
    authority.rows = authority.rows.map((row) =>
      row.id === SET_PROMOTION.id
        ? { ...row, turnState: "turn-ended" }
        : row,
    );
  }
  return json({ sessions: authority.rows });
}

async function landedCleanupRoute(
  authority: ScenarioAuthority,
  path: string,
  method: string,
  init?: RequestInit,
): Promise<Response | null> {
  if (
    (authority.definition.kind !== "fleet-12" &&
      authority.definition.kind !== "terminal-focus") ||
    path !== "/api/terminal/landed-cleanup" ||
    method !== "POST"
  ) {
    return null;
  }
  const body = JSON.parse(String(init?.body)) as { sessionIds: string[] };
  const requested = new Set(body.sessionIds);
  const closedSessions = authority.rows
    .filter((row) => requested.has(row.id) && row.status === "landed")
    .map((row) => row.id);
  const closed = new Set(closedSessions);
  authority.rows = authority.rows.filter((row) => !closed.has(row.id));
  return json({
    closed: closedSessions.length,
    skipped: body.sessionIds.length - closedSessions.length,
    closedSessions,
    skippedSessions: body.sessionIds
      .filter((id) => !closed.has(id))
      .map((session) => ({ session, reason: "status:not-landed" })),
  });
}

async function harnessesRoute(
  _authority: ScenarioAuthority,
  path: string,
  method: string,
): Promise<Response | null> {
  if (path !== "/api/harnesses" || method !== "GET") return null;
  // `satisfies` rather than a bare literal: `HarnessInfo` lives in `data/harnessCatalog.ts`,
  // which carries no mirror marker and so is NOT in `wireFixtureGuard.ts`'s vocabulary. Nothing
  // else in the tree would notice an invented field here — a `control` was live on these three
  // rows, and the server's `DetectedHarness` is `extra="forbid"` over exactly the three below.
  return json({
    harnesses: [
      { id: "claude", name: "Claude", detected: true },
      { id: "codex", name: "Codex", detected: true },
      { id: "pi", name: "Pi", detected: true },
    ] satisfies HarnessInfo[],
  });
}

async function harnessCapabilitiesRoute(
  authority: ScenarioAuthority,
  path: string,
  method: string,
): Promise<Response | null> {
  const preSession = path.match(/^\/api\/harnesses\/([^/]+)\/capabilities$/);
  if (!preSession || method !== "GET") return null;
  const harness = decodeURIComponent(preSession[1]) as "claude" | "codex" | "pi";
  return json(
    authority.definition.kind === "failed-harnesses"
      ? failedLaunchCapabilities(harness)
      : capabilityEnvelope(harness, "hit"),
  );
}

async function sessionCapabilitiesRoute(
  authority: ScenarioAuthority,
  path: string,
  method: string,
): Promise<Response | null> {
  const sessionCapabilities = path.match(/^\/api\/terminal\/([^/]+)\/capabilities$/);
  if (!sessionCapabilities || method !== "GET") return null;
  const selectedEffort =
    authority.definition.kind === "set-promotion" && !authority.setTurnEndedReleased
      ? "high"
      : "max";
  return json({ ...CLAUDE_FRESH_SESSION_SNAPSHOT, selectedEffort });
}

async function setEffortRoute(
  authority: ScenarioAuthority,
  path: string,
  method: string,
): Promise<Response | null> {
  if (
    path !== `/api/terminal/${SET_PROMOTION.id}/set-effort` ||
    method !== "POST" ||
    authority.definition.kind !== "set-promotion"
  ) {
    return null;
  }
  return json({
    ok: true,
    acceptance: "queued",
    requestedValue: "max",
    effectiveValue: null,
    detail: "applies when the next accepted turn starts",
  });
}

function openedString(body: Record<string, unknown>, key: string, fallback: string | null): string | null {
  return typeof body[key] === "string" ? (body[key] as string) : fallback;
}

function openedLabel(body: Record<string, unknown>): string {
  return typeof body.label === "string" ? (body.label as string) : "claude";
}

function openedHarness(body: Record<string, unknown>, kind: "terminal" | "harness"): string | null {
  return kind === "harness" && typeof body.harness === "string" ? (body.harness as string) : null;
}

function openedResolved(
  body: Record<string, unknown>,
  kind: "terminal" | "harness",
  key: string,
): string | null {
  return kind === "harness" && typeof body[key] === "string" ? (body[key] as string) : null;
}

function openedSessionBody(
  id: string,
  body: Record<string, unknown>,
): TerminalOpenSuccessBody {
  const kind = body.kind === "terminal" ? "terminal" : "harness";
  const harness = openedHarness(body, kind);
  return {
    ...OPENED_STARTING,
    session: id,
    label: openedLabel(body),
    kind,
    harness,
    lifecycleId: openedString(body, "lifecycleId", null),
    leafKey: openedString(body, "leafKey", null),
    seatRole: kind === "harness" ? "chat" : null,
    controlState: kind === "harness" ? "starting" : null,
    resolvedModel: openedResolved(body, kind, "model"),
    resolvedEffort: openedResolved(body, kind, "effort"),
    tmuxName: `ar-${id}`,
    controlEndpoint:
      kind === "harness" ? `/workspace/.agents-remember-control/${id}.sock` : null,
    controlProtocol: kind === "harness" ? OPENED_STARTING.controlProtocol : null,
  };
}

function buildScenarioProbe(
  definition: CockpitScenarioDefinition,
  authority: ScenarioAuthority,
): CockpitBenchProbe {
  return {
    scenario: definition.name,
    requestCounts: {},
    totalRequests: 0,
    requests: [],
    launchedSessionIds: [],
    snapshot: cockpitStateSnapshot,
    advance: (transition) => {
      if (transition === "launch-failures") authority.launchFailuresReleased = true;
      if (transition === "set-turn-ended") authority.setTurnEndedReleased = true;
      if (transition === "defer-next-open") authority.deferNextOpen = true;
      if (transition === "release-open") {
        authority.releaseOpen?.();
        authority.releaseOpen = null;
      }
    },
  };
}

function openedCatalogRow(opened: TerminalOpenSuccessBody): TerminalCatalogRow {
  return catalogRow({
    id: opened.session,
    label: String(opened.label),
    kind: opened.kind,
    harness: opened.harness ?? undefined,
    lifecycleId: opened.lifecycleId ?? undefined,
    leafKey: opened.leafKey ?? undefined,
    seatRole: opened.seatRole ?? undefined,
    command:
      opened.kind === "harness"
        ? [opened.harness ?? "claude"]
        : ["/bin/sh"],
    controlState: opened.controlState ?? undefined,
    resolvedModel: opened.resolvedModel ?? undefined,
    resolvedEffort: opened.resolvedEffort ?? undefined,
  });
}

async function openSessionRoute(
  authority: ScenarioAuthority,
  path: string,
  method: string,
  init?: RequestInit,
): Promise<Response | null> {
  const open = path.match(/^\/api\/terminal\/([^/]+)$/);
  if (!open || method !== "POST") return null;
  if (authority.deferNextOpen) {
    authority.deferNextOpen = false;
    await new Promise<void>((resolve) => {
      authority.releaseOpen = resolve;
    });
    authority.releaseOpen = null;
  }
  const id = decodeURIComponent(open[1]);
  if (authority.definition.kind === "launch-conflict") return json(LAUNCH_CONFLICT, 409);
  const body = init?.body
    ? (JSON.parse(String(init.body)) as Record<string, unknown>)
    : {};
  const opened = openedSessionBody(id, body);
  authority.probe.launchedSessionIds.push(id);
  authority.rows = [...authority.rows, openedCatalogRow(opened)];
  return json(opened);
}

async function submissionAuthorityRoute(
  _authority: ScenarioAuthority,
  path: string,
  method: string,
): Promise<Response | null> {
  if (!path.endsWith("/submission-authority") || method !== "GET") return null;
  return json({ bridgeEpoch: L5_BRIDGE_EPOCH });
}

async function submitRoute(
  authority: ScenarioAuthority,
  path: string,
  method: string,
  init?: RequestInit,
): Promise<Response | null> {
  if (!path.endsWith("/submit") || method !== "POST") return null;
  if (authority.definition.kind === "submit-reconcile" && !authority.submitLost) {
    authority.submitLost = true;
    throw new TypeError("scenario: browser lost the submit response");
  }
  const body = JSON.parse(String(init?.body)) as { requestId: string };
  return json({
    requestId: body.requestId,
    acceptance: "immediate",
    submittedAt: "2026-07-18T00:00:00Z",
    vendorCorrelationId: "scenario-vendor",
    acceptedAt: "2026-07-18T00:00:01Z",
    detail: null,
    bridgeEpoch: L5_BRIDGE_EPOCH,
  });
}

async function reconcileRoute(
  _authority: ScenarioAuthority,
  path: string,
  method: string,
  init?: RequestInit,
): Promise<Response | null> {
  if (!path.endsWith("/reconcile") || method !== "POST") return null;
  const body = JSON.parse(String(init?.body)) as { requestId: string };
  return json({
    requestId: body.requestId,
    state: "accepted",
    reconciledAt: "2026-07-18T00:00:02Z",
    vendorCorrelationId: "scenario-vendor",
    detail: null,
    bridgeEpoch: L5_BRIDGE_EPOCH,
    submissionState: "delivered",
  });
}

async function approveRoute(
  _authority: ScenarioAuthority,
  path: string,
  method: string,
): Promise<Response | null> {
  if (path !== "/api/actions/approve" || method !== "POST") return null;
  return new Response("", { status: 202 });
}

async function submissionStatusRoute(
  _authority: ScenarioAuthority,
  path: string,
  method: string,
  init?: RequestInit,
): Promise<Response | null> {
  if (!path.endsWith("/submission-status") || method !== "POST") return null;
  const body = JSON.parse(String(init?.body)) as { requestIds?: string[] };
  return json({
    bridgeEpoch: L5_BRIDGE_EPOCH,
    submissions: (body.requestIds ?? []).map((requestId) => ({
      requestId,
      outcome: "not-found",
    })),
  });
}

const SCENARIO_ROUTES: ScenarioRoute[] = [
  sessionsRoute,
  landedCleanupRoute,
  harnessesRoute,
  harnessCapabilitiesRoute,
  sessionCapabilitiesRoute,
  setEffortRoute,
  openSessionRoute,
  submissionAuthorityRoute,
  submitRoute,
  reconcileRoute,
  approveRoute,
  submissionStatusRoute,
];

/** Install the scenario's deterministic in-browser HTTP authority. Returns the restore callback. */
export function installCockpitScenarioFetch(
  definition: CockpitScenarioDefinition,
): () => void {
  const original = window.fetch;
  const authority: ScenarioAuthority = {
    definition,
    rows: definition.rows.map((row) => ({ ...row })),
    submitLost: false,
    launchFailuresReleased: false,
    setTurnEndedReleased: false,
    deferNextOpen: false,
    releaseOpen: null,
    probe: null as unknown as CockpitBenchProbe, // assigned below; the probe's advance mutates this state
  };
  authority.probe = buildScenarioProbe(definition, authority);
  window.__cockpitBench = authority.probe;

  window.fetch = async (
    input: RequestInfo | URL,
    init?: RequestInit,
  ): Promise<Response> => {
    const path = requestPath(input);
    const method = requestMethod(input, init);
    count(authority.probe, method, path, init);
    for (const route of SCENARIO_ROUTES) {
      const response = await route(authority, path, method, init);
      if (response !== null) return response;
    }
    return json(
      { status: "scenario-route-missing", detail: `${method} ${path}` },
      404,
    );
  };

  return () => {
    window.fetch = original;
    delete window.__cockpitBench;
  };
}

export const INTERACTION_SCENARIO_GATE = {
  lifecycleId: "lc-interaction-scenario",
  sessionId: INTERACTION.id,
  interactionId: "ix_7",
  gateId: "gate-interaction-scenario",
} as const;
