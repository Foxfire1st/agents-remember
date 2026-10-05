// The launcher bar's lines about the selected execution: its status and detail, the note that
// the host could not be reached, the agent's last reply, the report path, and the Revive control.
// The status comes from the agent's state on every refresh; nothing here polls.
import type { RoleScopedExecution } from "./roleLaunchModel";

export const HOST_UNREACHABLE_LINE = "host unreachable; showing the last known status";
// The reason of a daemon that does not answer; every other reason is shown beside the line.
const DAEMON_DOWN_REASON = "paseo_daemon_unreachable";

export function RoleExecutionStatus({
  execution,
  className,
}: {
  execution: RoleScopedExecution;
  className: string;
}) {
  const summary = execution.result?.summary;
  const report = execution.report;
  const reason = execution.hostUnreachableReason;
  return (
    <>
      <div className={className} role="status" data-status={execution.status.toLowerCase()} data-testid="role-execution-status">
        Current role execution: {execution.status}
        {execution.detail ? " · " + execution.detail : ""}
      </div>
      {execution.hostUnreachable ? (
        <div className={className} role="status" data-testid="role-host-unreachable" title={reason} style={{ color: "var(--alarm)" }}>
          {HOST_UNREACHABLE_LINE}
          {reason && !reason.startsWith(DAEMON_DOWN_REASON) ? " (" + reason + ")" : ""}
        </div>
      ) : null}
      {summary ? (
        <div className={className} data-testid="role-result-summary" style={{ maxHeight: "6.5rem", overflowY: "auto", whiteSpace: "pre-wrap" }}>
          Last reply: {summary}
        </div>
      ) : null}
      {report?.path ? (
        <div className={className} data-testid="role-report-path" title={report.canonicalPath}>
          Report: {report.path}
          {report.available ? "" : " (not written yet)"}
        </div>
      ) : null}
    </>
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
