import { Button } from "react-aria-components";

import { css } from "../../../styled-system/css";
import { useSessionCockpit } from "../../data/sessionCockpitStore";
import type { OpenSession } from "../../data/sessions";
import { acknowledgeSetAttention } from "../../data/setClient";
import { deriveSetChips, hasUnackedSetAttention } from "../../data/setChips";
import { AcceptanceChip } from "./AcceptanceChip";

// Unfocused set-outcome toasts (260715-FEUI-L4 R6, design §9.8 toast discipline): an async
// outcome on a session the operator is NOT looking at persists until dismissed — never a
// timed toast, never silent. Background outcomes never pile: ONE session with attention shows
// its chips; several collapse into one stack ("N sessions with unacknowledged set outcomes")
// whose rows jump to the seat. Dismissing is the explicit mark-seen act (F22); focusing the
// seat and viewing its ledger acknowledges too (SeatInspector).

const stack = css({
  position: "absolute",
  right: "0.6rem",
  bottom: "2.4rem",
  zIndex: "15",
  display: "grid",
  gap: "0.35rem",
  maxWidth: "min(420px, 80%)",
});
const toast = css({
  display: "grid",
  gap: "0.3rem",
  background: "bgPanel",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "amber",
  borderRadius: "3px",
  padding: "0.4rem 0.5rem",
  fontSize: "0.7rem",
  color: "muted",
  boxShadow: "0 6px 24px oklch(0 0 0 / 0.4)",
});
const headRow = css({ display: "flex", alignItems: "baseline", gap: "0.4rem", minWidth: "0" });
const label = css({ color: "ink", minWidth: "0", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" });
const chipsColumn = css({ display: "grid", gap: "0.25rem", minWidth: "0" });
const toastButton = css({
  font: "inherit",
  fontSize: "0.64rem",
  flex: "none",
  paddingInline: "0.35rem",
  background: "transparent",
  color: "muted",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  cursor: "pointer",
  _hover: { color: "amber", borderColor: "amber" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});

export function SetOutcomeToasts({
  sessions,
  focusedSessionId,
  onFocusSession,
}: {
  sessions: OpenSession[];
  focusedSessionId: string | null;
  onFocusSession: (id: string) => void;
}) {
  const perSession = useSessionCockpit((state) => state.perSession);
  const withAttention = sessions.filter(
    (session) =>
      session.id !== focusedSessionId &&
      (session.status ?? "running") === "running" &&
      hasUnackedSetAttention(perSession[session.id]),
  );
  if (withAttention.length === 0) return null;

  return (
    <div className={stack} data-testid="set-outcome-toasts">
      {withAttention.length === 1 ? (
        withAttention.map((session) => (
          <div key={session.id} className={toast} role="status" data-testid={`set-toast-${session.id}`}>
            <div className={headRow}>
              <span className={label}>{session.label} — set outcome needs attention</span>
              <Button
                className={toastButton}
                onPress={() => onFocusSession(session.id)}
                data-testid={`set-toast-view-${session.id}`}
              >
                view
              </Button>
              <Button
                className={toastButton}
                onPress={() => acknowledgeSetAttention(session.id)}
                aria-label={`dismiss set outcomes for ${session.label} (mark seen)`}
                data-testid={`set-toast-dismiss-${session.id}`}
              >
                dismiss
              </Button>
            </div>
            <div className={chipsColumn}>
              {deriveSetChips(perSession[session.id])
                .filter((chip) => chip.demandsAck)
                .map((chip) => (
                  <AcceptanceChip key={chip.id} chip={chip} />
                ))}
            </div>
          </div>
        ))
      ) : (
        // Toast discipline (§9.8): several background outcomes collapse into ONE stack.
        <div className={toast} role="status" data-testid="set-toast-collapsed">
          <div className={headRow}>
            <span className={label}>
              {withAttention.length} sessions with unacknowledged set outcomes
            </span>
          </div>
          <div className={chipsColumn}>
            {withAttention.map((session) => (
              <div key={session.id} className={headRow}>
                <span className={label}>{session.label}</span>
                <Button
                  className={toastButton}
                  onPress={() => onFocusSession(session.id)}
                  data-testid={`set-toast-view-${session.id}`}
                >
                  view
                </Button>
                <Button
                  className={toastButton}
                  onPress={() => acknowledgeSetAttention(session.id)}
                  aria-label={`dismiss set outcomes for ${session.label} (mark seen)`}
                  data-testid={`set-toast-dismiss-${session.id}`}
                >
                  dismiss
                </Button>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
