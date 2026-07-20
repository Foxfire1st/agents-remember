// Exact-turn interrupt wiring (design §9.5, R6; L4-facing ruling 3 + register L3.5). It reads the
// live turn + capability from the active-conversation projection, dispatches interrupt with a
// caller-stable requestId (reconciled under the SAME id — invariant 27), and reports acknowledgement
// vs. settlement as distinct announcer-keyed transitions (ack is never voiced as interrupted).
//
// Turn-id correlation (round-1 F1 / register L4.R1): on the default hosted-codex topology the live
// `ConversationStatus.turn` carries no `turnId` during a working turn, so the stop must resolve the
// id from the landed projector item evidence — streaming items carry `turnId` (codex
// native_parent_id; pi carries the AR operation id per ruling 3). Only when a working turn's id is
// genuinely unresolvable does the control render visible-disabled with an HONEST reason, never the
// stale L1 capability text.
//
// Capability gating (L3.5): the active page's `capabilities.controls` is the KNOWN-STALE L1 view
// (`unverified` for all three harnesses on the wire) and no GET exposes the true control capability,
// so the stop enables on a working+resolvable turn unless the capability is a hard `unavailable`, and
// reflects the server's typed refusal reactively. A refusal for the current turn then disables the
// control with that exact reason (F5) until the turn changes.

import { useCallback, useMemo, useRef, useState } from "react";

import { announcePolite } from "../../../data/announcer";
import {
  interruptReconcile,
  requestInterrupt,
  type ControlResult,
} from "../../../data/conversation/client";
import type { ActiveConversationProjection } from "../../../data/conversation/reducer";
import { useActiveConversation } from "../../../data/conversation/store";
import type { InterruptOperation } from "../../../data/conversation/types";
import {
  ariaKeyshortcuts,
  bindingFor,
  useEffectiveKeymap,
} from "../../../data/keymap/preferences";

export interface ConversationInterrupt {
  /** True when a stop control should be offered (working turn, resolvable id, not refused/unavailable). */
  available: boolean;
  /** The honest reason copy for a non-supported capability, an unresolvable turn, or a server refusal. */
  reason?: string;
  /** In-flight between request and terminal settlement. */
  pending: boolean;
  onStop?: () => void;
  /** The EFFECTIVE registry chord assignment mirrored to aria-keyshortcuts (never a phantom — F2;
   *  derived from the live keymap, not a hardcoded default, so a rebind stays truthful — F25). */
  keyshortcut: string;
}

// Fallback tinykeys chord for `conversation.stop` if the command is somehow absent from the keymap
// (it never is — it lives in CHROME_CHORDS). The advertised aria-keyshortcuts value is DERIVED from
// the effective keymap at render time (F25), so a user rebind through `cockpit.sessions.keymap.v1`
// keeps the assistive-tech advertisement honest.
const STOP_COMMAND_ID = "conversation.stop";
const DEFAULT_STOP_CHORD = "Control+Shift+Period";

/**
 * Resolve the id of the currently working turn (design §9.5, F1). Prefer the canonical status turn id;
 * when the wire omits it during a working turn, correlate from the newest streaming/pending item's
 * `turnId` (falling back to the newest item that carries one). Returns null when genuinely unresolvable.
 */
export function resolveWorkingTurnId(
  projection: ActiveConversationProjection | undefined,
): string | null {
  const status = projection?.status;
  if (status === undefined) return null;
  if (status.turn.turnId !== null && status.turn.turnId !== undefined) return status.turn.turnId;
  if (status.turn.state !== "working") return null;
  const ids = projection?.orderedItemIds ?? [];
  let fallback: string | null = null;
  for (let index = ids.length - 1; index >= 0; index -= 1) {
    const item = projection?.itemsById[ids[index]];
    const turnId = item?.turnId;
    if (turnId === undefined) continue;
    if (item?.phase === "streaming" || item?.phase === "pending") return turnId;
    if (fallback === null) fallback = turnId;
  }
  return fallback;
}

function newRequestId(): string {
  const cryptoObj = globalThis.crypto;
  if (cryptoObj !== undefined && typeof cryptoObj.randomUUID === "function") {
    return `interrupt-${cryptoObj.randomUUID()}`;
  }
  return `interrupt-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export function useConversationInterrupt(sessionId: string | undefined): ConversationInterrupt {
  const projection = useActiveConversation((state) =>
    sessionId === undefined ? undefined : state.bySession[sessionId],
  );
  // The advertised chord is the EFFECTIVE keymap assignment (F25), not the default constant, so a
  // rebind through the documented preference seam stays truthful to assistive tech.
  const keymap = useEffectiveKeymap();
  const keyshortcut = ariaKeyshortcuts(
    bindingFor(keymap, STOP_COMMAND_ID)?.chord ?? DEFAULT_STOP_CHORD,
  );
  // A refusal is scoped to the exact turn it refused, so a later turn re-enables the control (F5).
  const [refusal, setRefusal] = useState<{ turnId: string; reason: string } | null>(null);
  const [pending, setPending] = useState(false);
  const requestIdByTurn = useRef<Map<string, string>>(new Map());
  const announcedRef = useRef<Set<string>>(new Set());

  const epoch = projection?.identity.bridgeEpoch;
  const turnId = resolveWorkingTurnId(projection);
  const turnState = projection?.status?.turn.state;
  const capability = projection?.capabilities?.controls.interrupt;

  const announceOnce = useCallback((key: string, text: string) => {
    if (announcedRef.current.has(key)) return;
    announcedRef.current.add(key);
    announcePolite(text);
  }, []);

  const applyResult = useCallback(
    (result: ControlResult<InterruptOperation>, forTurn: string) => {
      if (result.ok) {
        const op = result.operation;
        setRefusal((current) => (current?.turnId === forTurn ? null : current));
        announceOnce(`interrupt:${op.requestId}:ack:${op.revision}`, `interrupt ${op.acknowledgement}`);
        if (op.settlement !== "pending") {
          announceOnce(`interrupt:${op.requestId}:settle:${op.revision}`, `turn ${op.settlement}`);
        }
        setPending(op.acknowledgement === "requested" || op.settlement === "pending");
      } else {
        // A typed refusal is surfaced as the honest reason and disables the control for this turn.
        setRefusal({ turnId: forTurn, reason: result.error.detail || result.error.status });
        setPending(false);
      }
    },
    [announceOnce],
  );

  const onStop = useCallback(() => {
    if (sessionId === undefined || epoch === undefined || turnId === null) return;
    const existing = requestIdByTurn.current.get(turnId);
    const requestId = existing ?? newRequestId();
    requestIdByTurn.current.set(turnId, requestId);
    setPending(true);
    const dispatch = existing
      ? interruptReconcile(sessionId, epoch, turnId, requestId)
      : requestInterrupt(sessionId, epoch, turnId, requestId);
    void dispatch.then((result) => applyResult(result, turnId));
  }, [applyResult, epoch, sessionId, turnId]);

  return useMemo<ConversationInterrupt>(() => {
    const working = turnState === "working";
    const hardUnavailable = capability?.state === "unavailable";
    const refusedThisTurn = refusal !== null && turnId !== null && refusal.turnId === turnId;
    const unresolvable = working && turnId === null;
    const available = working && turnId !== null && !hardUnavailable && !refusedThisTurn;
    // Reason copy is ONLY the honest, current signal. The KNOWN-STALE L1 `unverified` reason
    // (`capability.reason` when the view is merely not-`supported`) is deliberately NEVER surfaced as
    // control copy (F24 / register L3.5): it once leaked onto the ENABLED control's tooltip and the
    // catalog-lag placeholder. An enabled control carries no reason (WorkingLine shows an action
    // tooltip instead); a not-working placeholder falls back to the honest pre-L4 constant.
    let reason: string | undefined;
    if (available) reason = undefined;
    else if (hardUnavailable) reason = capability?.reason ?? "unavailable";
    else if (refusedThisTurn) reason = refusal?.reason;
    else if (unresolvable) reason = "turn identity unavailable on this wire";
    return {
      available,
      reason,
      pending,
      onStop: available && !pending ? onStop : undefined,
      keyshortcut,
    };
  }, [capability, keyshortcut, onStop, pending, refusal, turnId, turnState]);
}
