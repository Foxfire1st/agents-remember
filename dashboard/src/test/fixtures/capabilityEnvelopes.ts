import type {
  CapabilityCacheStatus,
  CapabilityEnvelope,
  CapabilityRouteErrorBody,
  CapabilitySnapshotWire,
  EffortOptionWire,
  ModelCapabilityWire,
  SetAcceptance,
  SetResultWire,
} from "../../types/harnessCapabilities";
import { CAPABILITY_SCHEMA } from "../../types/harnessCapabilities";

// Capability-contract fixtures (260715-FEUI-L3 R3), typed against the wire mirrors and shaped by
// the RECORDED L5 evidence (260716-ACPUI-L5-{claude,codex}-live-conformance.md and
// -pi-live-and-resource-proof.md): Claude's five rows with an effortless Haiku, Codex's eight
// rows with a hidden entry and per-row default efforts, Pi's two provider-qualified rows.
// FIXTURES ONLY: these keys/menus are evidence examples for tests — they must never enter
// production UI constants (dynamic-only invariant).

export function effortOption(
  key: string,
  overrides: Partial<EffortOptionWire> = {},
): EffortOptionWire {
  return {
    key,
    displayName: key,
    description: null,
    launchSettable: true,
    sessionSettable: true,
    ...overrides,
  };
}

export function modelRow(
  key: string,
  overrides: Partial<ModelCapabilityWire> = {},
): ModelCapabilityWire {
  return {
    key,
    displayName: key,
    resolvedModel: null,
    description: null,
    supportsEffort: (overrides.effortOptions ?? []).length > 0,
    effortOptions: [],
    defaultEffort: null,
    isDefault: false,
    hidden: false,
    selectable: true,
    provider: null,
    ...overrides,
  };
}

const efforts = (keys: string[]): EffortOptionWire[] => keys.map((key) => effortOption(key));

// ── Claude (L5: five rows; model-gated — Haiku advertises NO effort rows) ──────────────────────

const CLAUDE_EFFORTS = ["low", "medium", "high", "xhigh", "max"];

export const CLAUDE_MODEL_ROWS: ModelCapabilityWire[] = [
  modelRow("default", {
    resolvedModel: "claude-opus-4-8[1m]",
    effortOptions: efforts(CLAUDE_EFFORTS),
    supportsEffort: true,
    isDefault: true,
  }),
  modelRow("opus[1m]", {
    resolvedModel: "claude-opus-4-8[1m]",
    effortOptions: efforts(CLAUDE_EFFORTS),
    supportsEffort: true,
  }),
  modelRow("claude-fable-5[1m]", {
    resolvedModel: "claude-fable-5",
    effortOptions: efforts(CLAUDE_EFFORTS),
    supportsEffort: true,
  }),
  modelRow("sonnet", {
    resolvedModel: "claude-sonnet-5",
    effortOptions: efforts(CLAUDE_EFFORTS),
    supportsEffort: true,
  }),
  // The observed Haiku row: NO effortOptions — the fixture the effort-gating tests lean on.
  modelRow("haiku", { resolvedModel: "claude-haiku-4-5-20251001" }),
];

// ── Codex (L5: eight rows; per-row default efforts; one hidden row) ────────────────────────────

export const CODEX_MODEL_ROWS: ModelCapabilityWire[] = [
  modelRow("gpt-5.6-sol", {
    effortOptions: efforts(["low", "medium", "high", "xhigh", "max", "ultra"]),
    supportsEffort: true,
    defaultEffort: "low",
    isDefault: true,
  }),
  modelRow("gpt-5.6-terra", {
    effortOptions: efforts(["low", "medium", "high", "xhigh", "max", "ultra"]),
    supportsEffort: true,
    defaultEffort: "medium",
  }),
  modelRow("gpt-5.6-luna", {
    effortOptions: efforts(["low", "medium", "high", "xhigh", "max"]),
    supportsEffort: true,
    defaultEffort: "medium",
  }),
  modelRow("gpt-5.5", {
    effortOptions: efforts(["low", "medium", "high", "xhigh"]),
    supportsEffort: true,
    defaultEffort: "medium",
  }),
  modelRow("gpt-5.4", {
    effortOptions: efforts(["low", "medium", "high", "xhigh"]),
    supportsEffort: true,
    defaultEffort: "medium",
  }),
  modelRow("gpt-5.4-mini", {
    effortOptions: efforts(["low", "medium", "high", "xhigh"]),
    supportsEffort: true,
    defaultEffort: "medium",
  }),
  modelRow("gpt-5.3-codex-spark", {
    effortOptions: efforts(["low", "medium", "high", "xhigh"]),
    supportsEffort: true,
    defaultEffort: "high",
  }),
  modelRow("codex-auto-review", {
    effortOptions: efforts(["low", "medium", "high", "xhigh"]),
    supportsEffort: true,
    defaultEffort: "medium",
    hidden: true,
  }),
];

// ── Pi (L5: two auth-visible rows; keys stay provider-qualified `provider/id` VERBATIM) ────────

export const PI_MODEL_ROWS: ModelCapabilityWire[] = [
  modelRow("deepseek/deepseek-v4-flash", {
    effortOptions: efforts(["off", "high", "max"]),
    supportsEffort: true,
    provider: "deepseek",
  }),
  modelRow("deepseek/deepseek-v4-pro", {
    effortOptions: efforts(["off", "high", "max"]),
    supportsEffort: true,
    provider: "deepseek",
  }),
];

// ── Envelopes (pre-session: no selection yet, so configOptions is empty) ───────────────────────

const HARNESS_ROWS: Record<string, ModelCapabilityWire[]> = {
  claude: CLAUDE_MODEL_ROWS,
  codex: CODEX_MODEL_ROWS,
  pi: PI_MODEL_ROWS,
};

export function preSessionSnapshot(models: ModelCapabilityWire[]): CapabilitySnapshotWire {
  return { models, selectedModelKey: null, selectedEffort: null, configOptions: [] };
}

export function capabilityEnvelope(
  harness: "claude" | "codex" | "pi",
  cacheStatus: CapabilityCacheStatus,
): CapabilityEnvelope {
  return {
    schema: CAPABILITY_SCHEMA,
    harness,
    cacheStatus,
    // Shape of the recorded fingerprint (a sha256 hex digest); value is per-install evidence.
    installFingerprint: `${harness}-install-fingerprint-779167e5910dd810af4252ddcc61b319`,
    capabilities: preSessionSnapshot(HARNESS_ROWS[harness]),
  };
}

/** The envelope in ALL THREE cacheStatus values (R3) — same catalog, different cache truth. */
export const ENVELOPES_BY_CACHE_STATUS: Record<CapabilityCacheStatus, CapabilityEnvelope> = {
  hit: capabilityEnvelope("claude", "hit"),
  miss: capabilityEnvelope("claude", "miss"),
  refreshed: capabilityEnvelope("claude", "refreshed"),
};

/**
 * A FRESH Claude exact-session snapshot (R3): the launch model echoed via `system/init`, but
 * `selectedEffort` is null — stream-json emits no launch-effort echo (L5), so the effort config
 * category is absent until a mid-session set is echo-verified.
 */
export const CLAUDE_FRESH_SESSION_SNAPSHOT: CapabilitySnapshotWire = {
  models: CLAUDE_MODEL_ROWS,
  selectedModelKey: "claude-fable-5[1m]",
  selectedEffort: null,
  configOptions: [
    {
      type: "select",
      id: "model",
      category: "model",
      name: "Model",
      description: "Model used by this harness session",
      currentValue: "claude-fable-5[1m]",
      options: CLAUDE_MODEL_ROWS.filter(
        (model) => model.key === "claude-fable-5[1m]" || (model.selectable && !model.hidden),
      ).map((model) => ({
        value: model.key,
        name: model.displayName,
        description: model.description,
      })),
    },
  ],
};

// ── SetResult in every acceptance (R3) — honest evidence words, never a success boolean ───────

export const SET_RESULTS: Record<SetAcceptance, SetResultWire> = {
  "echo-verified": {
    ok: true,
    acceptance: "echo-verified",
    requestedValue: "max",
    effectiveValue: "max",
    detail: null,
  },
  immediate: {
    ok: true,
    acceptance: "immediate",
    requestedValue: "gpt-5.6-sol",
    effectiveValue: "gpt-5.6-sol",
    detail: "requested value already effective",
  },
  queued: {
    ok: true,
    acceptance: "queued",
    requestedValue: "xhigh",
    effectiveValue: null,
    detail: "applies when the next accepted turn starts",
  },
  unknown: {
    ok: false,
    acceptance: "unknown",
    requestedValue: "high",
    effectiveValue: null,
    detail: "outcome unresolved — refresh the exact-session snapshot",
  },
  unsupported: {
    ok: false,
    acceptance: "unsupported",
    requestedValue: "ar-unknown-model",
    effectiveValue: null,
    detail: "requested model is absent from the dynamic catalog",
  },
};

// ── L4 fixture extensions (260715-FEUI-L4 R9): clamp, queued→immediate, unknown→readback ──────

/** Pi's silent thinking clamp (L5 evidence): echo-verified with effective ≠ requested — BOTH
 *  values render persistently and the chip demands acknowledgment. */
export const SET_RESULT_CLAMP: SetResultWire = {
  ok: true,
  acceptance: "echo-verified",
  requestedValue: "max",
  effectiveValue: "high",
  detail: "thinking level clamped by the model",
};

/** Defensive shape: echo-verified but the adapter echoed no value — proves nothing, marker
 *  must not move to the requested value. */
export const SET_RESULT_ECHO_NO_VALUE: SetResultWire = {
  ok: true,
  acceptance: "echo-verified",
  requestedValue: "xhigh",
  effectiveValue: null,
  detail: null,
};

/** The repeat-set resolution sequence (R4): a queued set, then the SAME value re-set returning
 *  'immediate' — which resolves the queued pending without a readback. */
export const QUEUED_THEN_IMMEDIATE_SEQUENCE: SetResultWire[] = [
  {
    ok: true,
    acceptance: "queued",
    requestedValue: "xhigh",
    effectiveValue: null,
    detail: "applies when the next accepted turn starts",
  },
  {
    ok: true,
    acceptance: "immediate",
    requestedValue: "xhigh",
    effectiveValue: "xhigh",
    detail: "requested value already effective",
  },
];

/** A LIVE Codex exact-session snapshot (bare capability_snapshot_json — no envelope). */
export function codexLiveSessionSnapshot(
  selectedModelKey: string,
  selectedEffort: string | null,
): CapabilitySnapshotWire {
  return {
    models: CODEX_MODEL_ROWS,
    selectedModelKey,
    selectedEffort,
    configOptions: [], // the serializer's projection — deliberately NOT consumed by the menu
  };
}

/** The unknown-then-readback pair (R9): the unknown SetResult plus the snapshot whose readback
 *  CONFIRMS the requested effort took effect. */
export const UNKNOWN_THEN_READBACK: {
  result: SetResultWire;
  confirmingSnapshot: CapabilitySnapshotWire;
  disprovingSnapshot: CapabilitySnapshotWire;
} = {
  result: {
    ok: false,
    acceptance: "unknown",
    requestedValue: "high",
    effectiveValue: null,
    detail: "outcome unresolved — refresh the exact-session snapshot",
  },
  confirmingSnapshot: codexLiveSessionSnapshot("gpt-5.6-sol", "high"),
  disprovingSnapshot: codexLiveSessionSnapshot("gpt-5.6-sol", "low"),
};

/** Exact-session error bodies, verbatim server wording (harness_control_api.py). */
export const SESSION_CAPABILITY_ERROR_BODIES = {
  sessionGone: { httpStatus: 404, body: { status: "unknown-session" } },
  noNativeControl: {
    httpStatus: 409,
    body: { status: "unsupported", detail: "session has no native protocol control endpoint" },
  },
  outage: {
    httpStatus: 503,
    body: { status: "control-unavailable", detail: "harness control socket did not answer" },
  },
} as const;

// ── Capability-route errors, verbatim server wording (R1) ──────────────────────────────────────

export const CAPABILITY_ERROR_BODIES: Record<
  "notInstalled" | "nonNative" | "controlUnavailable",
  { httpStatus: number; body: CapabilityRouteErrorBody }
> = {
  notInstalled: {
    httpStatus: 404,
    body: { status: "capability-unavailable", detail: "harness not installed: 'codex'" },
  },
  nonNative: {
    httpStatus: 409,
    body: {
      status: "capability-unavailable",
      detail: "harness 'hermes' has no normalized native capability adapter",
    },
  },
  controlUnavailable: {
    httpStatus: 503,
    body: {
      status: "control-unavailable",
      detail: "claude capability discovery failed: native process exited before advertise",
    },
  },
};
