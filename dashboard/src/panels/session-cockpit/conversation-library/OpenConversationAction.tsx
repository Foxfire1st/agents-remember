// `Open as new chat` — the SOLE resume action (design §4.4, §9.4, R4). It mints a caller-stable
// requestId, sends the opaque key + expected identity digest + cwd + launch context, and reflects
// the exact open operation phase/outcome/rollback. It NEVER focuses; the parent focuses the new rail
// row only after `openedForFocus` (phase="opened" && outcome="opened") with catalog proof. Any other
// outcome leaves the current chat, draft, focus, and scroll untouched.

import { useEffect, useMemo } from "react";

import { css } from "../../../../styled-system/css";
import type { HarnessId } from "../../../data/conversation/types";
import {
  beginOpen,
  reconcileOpen,
  useConversationLibrary,
  type LibraryDeps,
  type OpenTracker,
} from "../../../data/conversation-library/store";
import type { ConversationLibraryRow } from "../../../data/conversation-library/types";

const wrap = css({ display: "flex", flexDirection: "column", gap: "0.25rem", flexShrink: 0 });
const button = css({
  font: "inherit",
  fontSize: "0.72rem",
  letterSpacing: "0.04em",
  color: "amber",
  background: "transparent",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "amber",
  borderRadius: "2px",
  paddingInline: "0.6rem",
  paddingBlock: "0.2rem",
  cursor: "pointer",
  alignSelf: "flex-start",
  _hover: { background: "color-mix(in oklch, token(colors.amber) 10%, transparent)" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
  _disabled: { color: "muted", borderColor: "grid", cursor: "not-allowed" },
});
const status = css({ fontSize: "0.66rem", color: "muted" });
const rollbackNote = css({ fontSize: "0.66rem", color: "alarm" });

function newRequestId(): string {
  const cryptoObj = globalThis.crypto;
  if (cryptoObj !== undefined && typeof cryptoObj.randomUUID === "function") {
    return `open-${cryptoObj.randomUUID()}`;
  }
  return `open-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function outcomeCopy(outcome: string): string {
  switch (outcome) {
    case "pending":
      return "opening…";
    case "timeout-unknown":
      return "still opening — outcome unknown; reconciling…";
    case "opened":
      return "opened";
    case "unsupported":
      return "resume is unavailable for this harness";
    case "stale-identity":
      return "this conversation changed — refresh the list";
    case "launch-failed":
      return "launch failed — the current chat is unchanged";
    case "identity-mismatch":
      return "identity mismatch — not opened; the current chat is unchanged";
    case "request-conflict":
      return "a different open used this request id";
    default:
      return outcome;
  }
}

export function OpenConversationAction({
  harnessId,
  row,
  cwd,
  launchContext,
  deps,
  onOpened,
}: {
  harnessId: HarnessId;
  row: ConversationLibraryRow;
  cwd?: string;
  launchContext?: { leafKey?: string; seatRole?: string };
  deps?: LibraryDeps;
  onOpened: (arSessionId: string) => void;
}) {
  const open = useConversationLibrary((state) => state.open);
  const resume = row.capabilities.resume;
  const resumeUnavailable = resume.state === "unavailable";
  const active = open?.conversationKey === row.conversationKey ? open : undefined;
  const activeForThisRow = active !== undefined;
  // busy from dispatch through non-terminal polling, but NOT while exhausted/errored (reconcilable).
  const busy = openBusy(activeForThisRow, active);
  // A transport failure or a spent poll budget leaves a re-drivable state under the SAME requestId.
  const reconcilable = openReconcilable(activeForThisRow, busy, active);

  const label = useMemo(
    () => actionLabel(resumeUnavailable, busy, reconcilable),
    [busy, reconcilable, resumeUnavailable],
  );

  // Focus the new session ONLY after exact opened catalog proof (§9.4 step 6, R4).
  useEffect(() => {
    if (
      open?.conversationKey === row.conversationKey &&
      open.openedForFocus &&
      open.operation?.arSessionId !== undefined
    ) {
      onOpened(open.operation.arSessionId);
    }
  }, [open, onOpened, row.conversationKey]);

  const start = (): void => {
    if (resumeUnavailable) return;
    // Re-drive under the SAME requestId after a transport failure or poll exhaustion (F6a/F6b).
    if (reconcilable && active?.requestId !== undefined) {
      reconcileOpen(harnessId, row.conversationKey, active.requestId, deps);
      return;
    }
    void beginOpen(
      harnessId,
      row.conversationKey,
      newRequestId(),
      row.identityDigest,
      { cwd, launchContext },
      deps,
    );
  };

  return (
    <div className={wrap} data-testid="open-conversation-action">
      <button
        type="button"
        className={button}
        onClick={start}
        disabled={resumeUnavailable || busy}
        title={resumeUnavailable ? resume.reason : "Launch a new controlled session bound to this exact conversation"}
        data-testid="open-as-new-chat"
      >
        {label}
      </button>
      <OpenStatusRow active={active} activeForThisRow={activeForThisRow} />
      {activeForThisRow &&
      open?.operation !== undefined &&
      open.operation.rollback !== "not-needed" ? (
        <span className={rollbackNote} data-testid="open-rollback">
          spawned session rollback: {open.operation.rollback}
        </span>
      ) : null}
    </div>
  );
}

function openBusy(activeForThisRow: boolean, active: OpenTracker | undefined): boolean {
  return Boolean(
    activeForThisRow &&
      (active?.dispatching === true ||
        (active?.operation !== undefined &&
          !active.openedForFocus &&
          !isTerminal(active.operation.outcome) &&
          !active.pollsExhausted)),
  );
}

function openReconcilable(
  activeForThisRow: boolean,
  busy: boolean,
  active: OpenTracker | undefined,
): boolean {
  return Boolean(
    activeForThisRow && !busy && (active?.error !== undefined || active?.pollsExhausted === true),
  );
}

function actionLabel(
  resumeUnavailable: boolean,
  busy: boolean,
  reconcilable: boolean,
): string {
  if (resumeUnavailable) return "Open as new chat — unavailable";
  if (busy) return "opening…";
  if (reconcilable) return "Reconcile open";
  return "Open as new chat";
}

function OpenStatusRow({
  active,
  activeForThisRow,
}: {
  active: OpenTracker | undefined;
  activeForThisRow: boolean;
}) {
  if (activeForThisRow && active?.pollsExhausted) {
    return (
      <span className={status} role="status" data-testid="open-exhausted">
        outcome unknown — reconcile under the same request
      </span>
    );
  }
  if (activeForThisRow && active?.operation !== undefined) {
    return (
      <span className={status} role="status" data-testid="open-status">
        {outcomeCopy(active.operation.outcome)}
      </span>
    );
  }
  if (activeForThisRow && active?.error !== undefined) {
    return (
      <span className={status} role="alert" data-testid="open-error">
        {active.error.detail}
      </span>
    );
  }
  return null;
}

function isTerminal(outcome: string): boolean {
  return outcome !== "pending" && outcome !== "timeout-unknown";
}
