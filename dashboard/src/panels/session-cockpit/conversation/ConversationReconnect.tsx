// Honest reconnect/failure banner (design §4.3, §14.5). A projector failure NEVER silently falls
// back to raw PTY as if equivalent — it renders a fail-loud structured-surface error with
// `retry projection` and `show terminal diagnostics` actions. Transient states keep current items
// visible and say what is happening, never a fabricated calm.

import { css } from "../../../../styled-system/css";
import type { StreamPhase } from "../../../data/conversation/types";

const banner = css({
  display: "flex",
  alignItems: "center",
  gap: "0.5rem",
  flexWrap: "wrap",
  fontSize: "0.7rem",
  paddingBlock: "0.2rem",
  paddingInline: "0.4rem",
  borderWidth: "1px",
  borderStyle: "solid",
  borderRadius: "2px",
  flexShrink: 0,
});
const calm = css({ color: "muted", borderColor: "grid" });
const warn = css({ color: "amber", borderColor: "amber" });
const alarmTone = css({ color: "alarm", borderColor: "alarm" });
const action = css({
  font: "inherit",
  fontSize: "0.66rem",
  color: "muted",
  background: "transparent",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  paddingInline: "0.4rem",
  cursor: "pointer",
  _hover: { color: "amber", borderColor: "amber" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});

interface StateCopy {
  text: string;
  tone: string;
  retry: boolean;
  diagnostics: boolean;
}

function copyFor(phase: StreamPhase): StateCopy | null {
  switch (phase) {
    case "connecting":
      return { text: "connecting…", tone: calm, retry: false, diagnostics: false };
    case "reconnecting":
      return { text: "reconnecting — current messages stay visible", tone: warn, retry: false, diagnostics: false };
    case "gap":
      return { text: "re-syncing from server history…", tone: warn, retry: false, diagnostics: false };
    case "identity-changed":
      return { text: "session identity changed — refocus to continue", tone: alarmTone, retry: false, diagnostics: true };
    case "projection-failed":
      return { text: "structured surface unavailable", tone: alarmTone, retry: true, diagnostics: true };
    case "failed":
      return { text: "conversation stream failed", tone: alarmTone, retry: true, diagnostics: true };
    case "live":
    case "idle":
    default:
      return null;
  }
}

export function ConversationReconnect({
  phase,
  reason,
  onRetry,
  onShowDiagnostics,
}: {
  phase: StreamPhase;
  /** The server's typed refusal reason (§14.5, F15) — shown instead of a generic message. */
  reason?: string;
  onRetry: () => void;
  onShowDiagnostics: () => void;
}) {
  const copy = copyFor(phase);
  if (copy === null) return null;
  return (
    <div className={`${banner} ${copy.tone}`} role="status" data-testid="conversation-reconnect" data-phase={phase}>
      <span>{reason !== undefined && reason.length > 0 ? `${copy.text} — ${reason}` : copy.text}</span>
      {copy.retry ? (
        <button type="button" className={action} onClick={onRetry} data-testid="conversation-retry">
          retry projection
        </button>
      ) : null}
      {copy.diagnostics ? (
        <button
          type="button"
          className={action}
          onClick={onShowDiagnostics}
          data-testid="conversation-show-diagnostics"
        >
          show terminal diagnostics
        </button>
      ) : null}
    </div>
  );
}
