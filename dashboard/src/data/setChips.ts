import type { SetAcceptance } from "../types/harnessCapabilities";
import {
  pairPartialFailureCopy,
  pairProgressCopy,
  pairRouteTerminationCopy,
} from "./pairChange";
import { effectiveSelection } from "./sessionCapabilities";
import type { PerSessionCockpit, SetLedgerEntry } from "./sessionCockpitStore";
import { isClamp, type SetKind } from "./setAcceptance";
import {
  clampChipCopy,
  composerQueuedHintCopy,
  echoWithoutValueChipCopy,
  queuedChipCopy,
  setRouteErrorCopy,
  setWaitingCopy,
  unknownChipCopy,
  unknownKeptPriorChipCopy,
  unsupportedChipCopy,
} from "./setControlsCopy";

// The chip row PURE derivation (260715-FEUI-L4 R2/R5/R6): one function maps the per-session
// cockpit state to the chips every surface renders (control row, toasts, inspector), so the
// requested-vs-effective honesty story cannot fork between surfaces. Every chip carries its
// acceptance WORD inside `text` (from data/setControlsCopy) — never color-only.

export interface SetChipModel {
  id: string;
  kind: SetKind | "pair" | "route";
  /** The acceptance word this chip testifies to (absent for pair-progress/route chips). */
  acceptance?: SetAcceptance;
  tone: "info" | "affirm" | "warn" | "alarm";
  text: string;
  /** True ⇒ the in-flight spinner glyph rides the chip (the honest ~35 s wait). */
  spinner: boolean;
  /** True ⇒ the chip persists until explicitly acknowledged (clamp/unsupported/unknown/pair). */
  demandsAck: boolean;
  /** 503 route outage — the only chip with a retry affordance (R3). */
  retryable: boolean;
}

function chip(partial: Omit<SetChipModel, "spinner" | "demandsAck" | "retryable"> & Partial<SetChipModel>): SetChipModel {
  return { spinner: false, demandsAck: false, retryable: false, ...partial };
}

/** Latest unacknowledged ledger entry per kind — the chips' ledger source. */
function latestUnacked(
  ledger: readonly SetLedgerEntry[],
  kind: SetKind,
): SetLedgerEntry | undefined {
  for (let index = ledger.length - 1; index >= 0; index -= 1) {
    const entry = ledger[index];
    if (entry.kind === kind && !entry.acknowledged) return entry;
  }
  return undefined;
}

export function deriveSetChips(cockpit: PerSessionCockpit | undefined): SetChipModel[] {
  if (!cockpit) return [];
  const chips: SetChipModel[] = [];

  // Pair change first (R5): the two-step progress chip, or the designed failure rendering.
  const pair = cockpit.pairChange;
  if (pair) {
    if (!pair.outcome) {
      chips.push(
        chip({ id: "pair-progress", kind: "pair", tone: "info", text: pairProgressCopy(pair), spinner: true }),
      );
    } else if (
      pair.outcome.kind !== "completed" &&
      pair.outcome.routeErrorStep !== undefined
    ) {
      chips.push(
        chip({
          id: pair.outcome.kind === "aborted" ? "pair-aborted" : "pair-partial",
          kind: "pair",
          tone: "alarm",
          text: pairRouteTerminationCopy(pair.outcome.routeErrorStep),
          demandsAck: true,
        }),
      );
    } else if (pair.outcome.kind === "aborted") {
      chips.push(
        chip({
          id: "pair-aborted",
          kind: "pair",
          tone: "alarm",
          text: `pair change aborted — nothing changed: ${pair.outcome.detail}`,
          demandsAck: true,
        }),
      );
    } else if (pair.outcome.kind === "partial") {
      chips.push(
        chip({
          id: "pair-partial",
          kind: "pair",
          tone: "alarm",
          text: pairPartialFailureCopy(pair.outcome, effectiveSelection(cockpit).effort),
          demandsAck: true,
        }),
      );
    }
  }

  for (const kind of ["model", "effort"] as const) {
    const pending = cockpit.pendingSets[kind];
    if (pending) {
      if (pending.phase === "inflight") {
        chips.push(
          chip({
            id: `pending-${kind}`,
            kind,
            tone: "info",
            text: setWaitingCopy(kind, pending.requestedValue),
            spinner: true,
          }),
        );
      } else if (pending.phase === "queued-awaiting-turn") {
        chips.push(
          chip({
            id: `queued-${kind}`,
            kind,
            acceptance: "queued",
            tone: "warn",
            text: queuedChipCopy(kind, pending.requestedValue),
          }),
        );
      } else {
        chips.push(
          chip({
            id: `unknown-${kind}`,
            kind,
            acceptance: "unknown",
            tone: "warn",
            text: unknownChipCopy(kind, pending.requestedValue),
          }),
        );
      }
    }

    // Ledger attention chips (clamp / unsupported / resolved-unknown) — a pending for the same
    // requested value already tells the current story, so its own queued/unknown entry is
    // skipped; clamp and unsupported entries never coexist with a pending (resolved on arrival).
    const entry = latestUnacked(cockpit.setLedger, kind);
    if (!entry) continue;
    if (pending && pending.requestedValue === entry.requestedValue) continue;
    const result = entry.result;
    if (result.acceptance === "echo-verified") {
      if (result.effectiveValue === undefined) {
        chips.push(
          chip({
            id: `echo-no-value-${kind}`,
            kind,
            acceptance: "echo-verified",
            tone: "warn",
            text: echoWithoutValueChipCopy(kind, result.requestedValue),
            demandsAck: true,
          }),
        );
      } else if (isClamp({ ...result, effectiveValue: result.effectiveValue ?? null })) {
        chips.push(
          chip({
            id: `clamp-${kind}`,
            kind,
            acceptance: "echo-verified",
            tone: "warn",
            text: clampChipCopy(result.requestedValue, result.effectiveValue),
            demandsAck: true,
          }),
        );
      }
    } else if (result.acceptance === "unsupported") {
      chips.push(
        chip({
          id: `unsupported-${kind}`,
          kind,
          acceptance: "unsupported",
          tone: "alarm",
          text: unsupportedChipCopy(kind, {
            ok: false,
            acceptance: "unsupported",
            requestedValue: result.requestedValue,
            effectiveValue: result.effectiveValue ?? null,
            detail: result.detail ?? null,
          }),
          demandsAck: true,
        }),
      );
    } else if (result.acceptance === "unknown") {
      chips.push(
        chip({
          id: `unknown-resolved-${kind}`,
          kind,
          acceptance: "unknown",
          tone: "warn",
          text: unknownKeptPriorChipCopy(kind, result.requestedValue),
          demandsAck: true,
        }),
      );
    }
  }

  const routeError = cockpit.setRouteError;
  if (routeError) {
    chips.push(
      chip({
        id: `route-${routeError.kind}`,
        kind: "route",
        tone: "alarm",
        text: setRouteErrorCopy(routeError),
        retryable: routeError.retryable,
      }),
    );
  }
  return chips;
}

/** The composer-hint slot (R2): present while any set is queued-awaiting-turn. */
export function queuedComposerHint(cockpit: PerSessionCockpit | undefined): string | null {
  if (!cockpit) return null;
  const kinds = (["model", "effort"] as const).filter(
    (kind) => cockpit.pendingSets[kind]?.phase === "queued-awaiting-turn",
  );
  return kinds.length > 0 ? composerQueuedHintCopy(kinds) : null;
}

/** True when the session's set state deserves the rail attention marker / toasts (R6). */
export function hasUnackedSetAttention(cockpit: PerSessionCockpit | undefined): boolean {
  if (!cockpit) return false;
  if (cockpit.pairChange?.outcome && cockpit.pairChange.outcome.kind !== "completed") return true;
  return cockpit.setLedger.some((entry) => !entry.acknowledged);
}
