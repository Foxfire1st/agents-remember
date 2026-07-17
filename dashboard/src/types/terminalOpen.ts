// TypeScript mirror of the `POST /api/terminal/{session}` response bodies (260715-FEUI-L3 R5):
// serving/app.py `api_terminal_open`. Every path is mirrored so the launch flow can render each
// outcome verbatim; the Python route is the source of truth — keep in lockstep by hand.

import type { HarnessControlState, TerminalOpenKind } from "./terminalCatalog";

/** 200 — the row was opened (or idempotently reused): controlState starts at 'starting' for a
 *  native harness; resolvedModel/resolvedEffort are the REQUESTED pair persisted verbatim BEFORE
 *  any validation (terminal_opener.py `_resolved_pair`) — provenance, never proof. */
export interface TerminalOpenSuccessBody {
  session: string;
  label: string;
  kind: TerminalOpenKind;
  harness: string | null;
  lifecycleId: string | null;
  leafKey: string | null;
  seatRole: string | null;
  cwd: string;
  tmuxName: string;
  status: "running";
  controlState: HarnessControlState | null;
  controlEndpoint: string | null;
  controlProtocol: string | null;
  resolvedModel: string | null;
  resolvedEffort: string | null;
}

/** 400 — the ONLY synchronous launch-selection refusal: a partial pair or a non-native harness
 *  (harness_control_api.py `resolve_terminal_open_selection`). Catalog validity is NOT checked
 *  here — an invalid pair opens 200/'starting' and fails asynchronously on every harness. */
export interface TerminalOpenSelectionInvalidBody {
  status: "launch-selection-invalid";
  detail: string;
}

/** 400 — unknown kind / unknown or undetected harness (`bad-kind`). */
export interface TerminalOpenBadKindBody {
  status: "bad-kind";
  detail: string | null;
}

/** 409 — the (leaf, role) pair already has a live owner; the server names it. */
export interface TerminalOpenLeafTakenBody {
  status: "leaf-taken";
  leafKey: string | null;
  session: string | null;
}

/** 409 — the session id is live with a DIFFERENT launch identity; the body carries the live
 *  row's retained pair (process truth) — reopening never rewrites provenance. */
export interface TerminalOpenConflictBody {
  status: "launch-selection-conflict";
  detail: string | null;
  session: string;
  kind: TerminalOpenKind;
  harness: string | null;
  tmuxName: string;
  controlState: HarnessControlState | null;
  controlEndpoint: string | null;
  controlProtocol: string | null;
  resolvedModel: string | null;
  resolvedEffort: string | null;
}
