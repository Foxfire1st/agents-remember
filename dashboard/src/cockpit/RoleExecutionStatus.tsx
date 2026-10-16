// The launcher shows one actionable failure line. Execution details belong to the chat.
import type { RoleScopedExecution } from "./roleLaunchModel";
import { StepUpStatus } from "./role-launcher/StepUpControl";

export const HOST_UNREACHABLE_LINE = "The host is unreachable. Check the connection and refresh.";

function executionFailure(execution: RoleScopedExecution | undefined): string {
  const reason = execution?.hostUnreachableReason;
  const hostFailure = execution?.hostUnreachable
    ? HOST_UNREACHABLE_LINE + (reason ? " (" + reason + ")" : "")
    : undefined;
  const uncertainty = execution?.status.toLowerCase() === "unknown"
    ? "The launch is unresolved. " + (execution.detail || "No usable execution receipt has been returned.") + " Refresh the result or retry the same saved request."
    : undefined;
  return [hostFailure, uncertainty].filter(Boolean).join(" ");
}

export function RoleExecutionStatus({
  execution,
  failure,
  failureTestId,
  className,
  showStepUp = true,
}: {
  execution?: RoleScopedExecution;
  failure?: string;
  failureTestId?: string;
  className: string;
  showStepUp?: boolean;
}) {
  const message = [failure, executionFailure(execution)].filter(Boolean).join(" ");
  if (!message) return showStepUp ? <StepUpStatus execution={execution} /> : null;
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
