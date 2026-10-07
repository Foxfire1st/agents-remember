// The launcher shows one actionable failure line. Execution details belong to the chat.
import type { RoleScopedExecution } from "./roleLaunchModel";

export const HOST_UNREACHABLE_LINE = "The host is unreachable. Check the connection and refresh.";

export function RoleExecutionStatus({
  execution,
  failure,
  failureTestId,
  className,
}: {
  execution?: RoleScopedExecution;
  failure?: string;
  failureTestId?: string;
  className: string;
}) {
  const reason = execution?.hostUnreachableReason;
  const hostFailure = execution?.hostUnreachable
    ? HOST_UNREACHABLE_LINE + (reason ? " (" + reason + ")" : "")
    : undefined;
  const uncertainty = execution?.status.toLowerCase() === "unknown"
    ? "The launch is unresolved. " + (execution.detail || "No usable execution receipt has been returned.") + " Refresh the result or retry the same saved request."
    : undefined;
  const message = [failure, hostFailure, uncertainty].filter(Boolean).join(" ");
  if (!message) return null;
  return (
    <div className={className} role="alert" data-testid={failure ? failureTestId : "role-host-unreachable"} style={{ color: "var(--alarm)" }}>
      {message}
    </div>
  );
}

/** Revive is offered only for an execution the backend calls revivable. */
export function RoleReviveControl({
  execution,
  enabled,
  className,
  onRevive,
}: {
  execution: RoleScopedExecution | undefined;
  enabled: boolean;
  className: string;
  onRevive: () => void;
}) {
  if (execution?.canRevive !== true) return null;
  return (
    <button className={className} type="button" aria-label="Revive role" title="Resume this agent's closed session; no message is sent" disabled={!enabled} onClick={onRevive}>
      Revive
    </button>
  );
}
