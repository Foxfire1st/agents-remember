// The launcher bar's lines about the selected execution: its status and detail, the note that
// the host could not be reached, the agent's last reply, the report path, and the Revive control.
// The status comes from the agent's state on every refresh; nothing here polls.
import type { OrcaScopedExecution } from "./orcaLaunchModel";

export const HOST_UNREACHABLE_LINE = "host unreachable; showing the last known status";

export function OrcaExecutionStatus({
  execution,
  className,
}: {
  execution: OrcaScopedExecution;
  className: string;
}) {
  const summary = execution.result?.summary;
  const report = execution.report;
  return (
    <>
      <div className={className} role="status" data-status={execution.status.toLowerCase()} data-testid="orca-execution-status">
        Current Orca execution: {execution.status}
        {execution.detail ? " · " + execution.detail : ""}
      </div>
      {execution.hostUnreachable ? (
        <div className={className} role="status" data-testid="orca-host-unreachable" title={execution.hostUnreachableReason} style={{ color: "var(--alarm)" }}>
          {HOST_UNREACHABLE_LINE}
        </div>
      ) : null}
      {summary ? (
        <div className={className} data-testid="orca-result-summary" style={{ maxHeight: "6.5rem", overflowY: "auto", whiteSpace: "pre-wrap" }}>
          Last reply: {summary}
        </div>
      ) : null}
      {report?.path ? (
        <div className={className} data-testid="orca-report-path" title={report.canonicalPath}>
          Report: {report.path}
          {report.available ? "" : " (not written yet)"}
        </div>
      ) : null}
    </>
  );
}

/** Revive is offered only for an execution the backend calls revivable. */
export function OrcaReviveControl({
  execution,
  enabled,
  className,
  onRevive,
}: {
  execution: OrcaScopedExecution | undefined;
  enabled: boolean;
  className: string;
  onRevive: () => void;
}) {
  if (execution?.canRevive !== true) return null;
  return (
    <button className={className} type="button" aria-label="Revive Orca" title="Resume this agent's closed session; no message is sent" disabled={!enabled} onClick={onRevive}>
      Revive
    </button>
  );
}
