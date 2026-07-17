import type { TerminalCatalogRow } from "../../types/terminalCatalog";
import type {
  TerminalOpenBadKindBody,
  TerminalOpenConflictBody,
  TerminalOpenLeafTakenBody,
  TerminalOpenSelectionInvalidBody,
  TerminalOpenSuccessBody,
} from "../../types/terminalOpen";
import { catalogRow } from "./catalogRows";

// Open-route and failed-launch fixtures (260715-FEUI-L3 R3/R6): every `POST /api/terminal/{id}`
// response path plus the sweep-projected failed rows for ALL THREE harnesses. Details mirror the
// server's exact wording (harness_control_api.py, app.py, harness_launch.py) and the recorded L5
// refusals — a catalog-invalid pair opens 200/'starting' on every harness and fails ASYNC.

/** 200 — controlState 'starting'; the pair is the REQUESTED pair persisted before validation. */
export const OPENED_STARTING: TerminalOpenSuccessBody = {
  session: "launch-1",
  label: "claude",
  kind: "harness",
  harness: "claude",
  lifecycleId: null,
  leafKey: null,
  seatRole: "chat",
  cwd: "/workspace",
  tmuxName: "ar-launch-1",
  status: "running",
  controlState: "starting",
  controlEndpoint: "/workspace/.agents-remember-control/launch-1.sock",
  controlProtocol: "ar-harness-control/v1",
  resolvedModel: "claude-fable-5[1m]",
  resolvedEffort: "max",
};

/** 200 — an intentionally selectionless open: BOTH knobs omitted ⇒ both retained as null. */
export const OPENED_VENDOR_DEFAULTS: TerminalOpenSuccessBody = {
  ...OPENED_STARTING,
  session: "launch-defaults-1",
  tmuxName: "ar-launch-defaults-1",
  controlEndpoint: "/workspace/.agents-remember-control/launch-defaults-1.sock",
  resolvedModel: null,
  resolvedEffort: null,
};

/** 400 — partial pair (the only synchronous selection refusal besides non-native). */
export const INVALID_PARTIAL_PAIR: TerminalOpenSelectionInvalidBody = {
  status: "launch-selection-invalid",
  detail: "model and effort must be provided together",
};

/** 400 — a selection sent at a non-native harness session. */
export const INVALID_NON_NATIVE: TerminalOpenSelectionInvalidBody = {
  status: "launch-selection-invalid",
  detail: "model/effort launch selection requires an AR native harness session",
};

/** 400 — unknown/undetected kind or harness. */
export const BAD_KIND: TerminalOpenBadKindBody = {
  status: "bad-kind",
  detail: "harness not installed: 'codex'",
};

/** 409 — the (leaf, role) pair already has a live owner; the server NAMES it. */
export const LEAF_TAKEN: TerminalOpenLeafTakenBody = {
  status: "leaf-taken",
  leafKey: "agents-remember/260715_react-tui-cockpit-frontend/03_capability-catalog-and-launch",
  session: "worker-l3-live",
};

/** 409 — reopening a LIVE session with a different pair: the body carries the live row's
 *  retained pair (process truth); provenance is never rewritten. */
export const LAUNCH_CONFLICT: TerminalOpenConflictBody = {
  status: "launch-selection-conflict",
  detail:
    "live session 'launch-1' already runs model/effort 'claude-fable-5[1m]'/'max'; " +
    "requested 'sonnet'/'high'",
  session: "launch-1",
  kind: "harness",
  harness: "claude",
  tmuxName: "ar-launch-1",
  controlState: "ready",
  controlEndpoint: "/workspace/.agents-remember-control/launch-1.sock",
  controlProtocol: "ar-harness-control/v1",
  resolvedModel: "claude-fable-5[1m]",
  resolvedEffort: "max",
};

// ── Uniform async fail-loud (R6): sweep-projected failed rows, ALL THREE harnesses ─────────────
// Each row retains the REFUSED pair verbatim (resolvedModel/resolvedEffort — requested
// provenance) and the runner's exact bridgeError, which NAMES the advertised alternatives
// (harness_launch.validate_launch_selection wording; refusal texts live-recorded in L5).

export const FAILED_CLAUDE_ROW: TerminalCatalogRow = catalogRow({
  id: "failed-claude",
  label: "scout-claude",
  harness: "claude",
  resolvedModel: "ar-unknown-model",
  resolvedEffort: "max",
  controlState: "failed",
  controlAcceptance: "rejected",
  controlRaw: {
    bridgeError:
      "claude launch model 'ar-unknown-model' is absent from the dynamic catalog; " +
      "advertised models: [default, opus[1m], claude-fable-5[1m], sonnet, haiku]",
  },
});

export const FAILED_CODEX_ROW: TerminalCatalogRow = catalogRow({
  id: "failed-codex",
  label: "scout-codex",
  harness: "codex",
  command: ["codex"],
  resolvedModel: "gpt-5.6-sol",
  resolvedEffort: "turbo",
  controlState: "failed",
  controlAcceptance: "rejected",
  controlRaw: {
    bridgeError:
      "codex launch effort 'turbo' is not advertised as launch-settable for model " +
      "'gpt-5.6-sol'; advertised launch efforts: [low, medium, high, xhigh, max, ultra]",
  },
});

export const FAILED_PI_ROW: TerminalCatalogRow = catalogRow({
  id: "failed-pi",
  label: "scout-pi",
  harness: "pi",
  command: ["pi"],
  resolvedModel: "deepseek-v4-flash",
  resolvedEffort: "max",
  controlState: "failed",
  controlAcceptance: "rejected",
  controlRaw: {
    bridgeError:
      "pi launch model 'deepseek-v4-flash' must use an exact provider-qualified catalog key; " +
      "matching alternatives: [deepseek/deepseek-v4-flash]",
  },
});

export const FAILED_LAUNCH_ROWS: TerminalCatalogRow[] = [
  FAILED_CLAUDE_ROW,
  FAILED_CODEX_ROW,
  FAILED_PI_ROW,
];

/** A second refusal shape: the effort (not the model) is what the catalog refused. */
export const FAILED_CLAUDE_EFFORT_ROW: TerminalCatalogRow = catalogRow({
  id: "failed-claude-effort",
  label: "scout-claude-effort",
  harness: "claude",
  resolvedModel: "claude-fable-5[1m]",
  resolvedEffort: "ar-unknown-effort",
  controlState: "failed",
  controlRaw: {
    bridgeError:
      "claude launch effort 'ar-unknown-effort' is not advertised as launch-settable for model " +
      "'claude-fable-5[1m]'; advertised launch efforts: [low, medium, high, xhigh, max]",
    paneDiagnostic: "runner kept the refusal addressable until retired",
  },
});

/** A READY row holding a pending interaction (R3: controlPendingInteraction in the pack) —
 *  mirrors L2's FLEET shape; answering it is L6's surface. */
export const PENDING_INTERACTION_ROW: TerminalCatalogRow = catalogRow({
  id: "interaction-1",
  label: "worker-interaction",
  harness: "claude",
  resolvedModel: "sonnet",
  resolvedEffort: "high",
  controlState: "ready",
  turnState: "awaiting-input",
  controlPendingInteraction: {
    interactionId: "ix_7",
    kind: "approval",
    prompt: "Overwrite the existing fixture file?",
    choices: ["overwrite", "keep both", "cancel"],
  },
});
