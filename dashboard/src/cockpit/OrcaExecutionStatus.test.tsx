// The launcher bar's status lines (PNT-R07): status, detail, last reply and report path of the
// selected execution, the host-unreachable line, and the Revive control; first the component by
// itself, then inside the launcher against the result and dispatch routes.
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ChatsModePanels } from "./OrcaChats";
import { HOST_UNREACHABLE_LINE, OrcaExecutionStatus, OrcaReviveControl } from "./OrcaExecutionStatus";
import { tasklessRequestStorageKey, type OrcaScopedExecution } from "./orcaLaunchModel";
import type { TaskDocNode } from "../types/projection";

vi.mock("../panels/session-cockpit/sessions-view/SessionsView", () => ({ SessionsView: () => null }));

const REQUEST_ID = "5f0c1f6e-8a53-4d5b-9d53-6f0f1f6f2a10";
const REPORT = { path: "/projects/.agents-remember/reports/architect.md", canonicalPath: "/projects/.agents-remember/reports/architect.md", available: false };

function execution(fields: Partial<OrcaScopedExecution>): OrcaScopedExecution {
  return { status: "running", requestId: REQUEST_ID, canStart: false, canRevive: false, canRetry: false, report: REPORT, ...fields };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("status lines of an execution", () => {
  it("shows the status, the detail, the last reply and the report path", () => {
    const completed = execution({ status: "completed", detail: "last turn finished; the agent is idle", result: { summary: "READY\nsecond line" }, report: { ...REPORT, available: true } });
    const { getByTestId, queryByTestId, rerender } = render(<OrcaExecutionStatus execution={completed} className="meta" />);

    const status = getByTestId("orca-execution-status");
    expect(status.textContent).toBe("Current Orca execution: completed · last turn finished; the agent is idle");
    expect(status.getAttribute("data-status")).toBe("completed");
    expect(getByTestId("orca-result-summary").textContent).toBe("Last reply: READY\nsecond line");
    expect(getByTestId("orca-report-path").textContent).toBe("Report: " + REPORT.path);
    expect(queryByTestId("orca-host-unreachable")).toBeNull();

    // A running execution without a reply yet: no summary line, and the report is not written.
    rerender(<OrcaExecutionStatus execution={execution({ detail: "waiting for permission: Bash" })} className="meta" />);
    expect(getByTestId("orca-execution-status").textContent).toBe("Current Orca execution: running · waiting for permission: Bash");
    expect(queryByTestId("orca-result-summary")).toBeNull();
    expect(getByTestId("orca-report-path").textContent).toBe("Report: " + REPORT.path + " (not written yet)");
  });

  it("says that the host is unreachable and the status is the last known one", () => {
    const stale = execution({ status: "completed", hostUnreachable: true, hostUnreachableReason: "paseo_daemon_unreachable: connection refused" });
    const { getByTestId } = render(<OrcaExecutionStatus execution={stale} className="meta" />);

    const line = getByTestId("orca-host-unreachable");
    expect(line.textContent).toBe("host unreachable; showing the last known status");
    expect(HOST_UNREACHABLE_LINE).toBe(line.textContent);
    expect(line.getAttribute("title")).toBe("paseo_daemon_unreachable: connection refused");
    expect(getByTestId("orca-execution-status").textContent).toBe("Current Orca execution: completed");
  });

  it("offers Revive only for a revivable execution", () => {
    const onRevive = vi.fn();
    const { queryByRole, getByRole, rerender } = render(<OrcaReviveControl execution={execution({})} enabled className="button" onRevive={onRevive} />);
    expect(queryByRole("button")).toBeNull();
    rerender(<OrcaReviveControl execution={undefined} enabled className="button" onRevive={onRevive} />);
    expect(queryByRole("button")).toBeNull();

    rerender(<OrcaReviveControl execution={execution({ status: "interrupted", canRevive: true })} enabled={false} className="button" onRevive={onRevive} />);
    expect((getByRole("button", { name: "Revive Orca" }) as HTMLButtonElement).disabled).toBe(true);
    rerender(<OrcaReviveControl execution={execution({ status: "interrupted", canRevive: true })} enabled className="button" onRevive={onRevive} />);
    fireEvent.click(getByRole("button", { name: "Revive Orca" }));
    expect(onRevive).toHaveBeenCalledTimes(1);
  });
});

describe("launcher bar and the execution's state", () => {
  let results: OrcaScopedExecution[] = [];
  let dispatched: Record<string, unknown>[] = [];
  let dispatchReply: { status: number; body: unknown } = { status: 200, body: {} };

  // `saved` is what the options route returns: the taskless executions, or the selection's one.
  function renderLauncher(saved: OrcaScopedExecution, taskDocuments: TaskDocNode[] = []) {
    sessionStorage.setItem(tasklessRequestStorageKey("architect"), JSON.stringify({ requestId: REQUEST_ID }));
    const catalog = { roleDefaults: { agent: "codex", available: true }, agents: [{ id: "codex", label: "Codex", models: [] }], catalogOrigin: "paseo:1" };
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      const sent = JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>;
      if (url === "/api/orca/launcher/options") return new Response(JSON.stringify(sent.role === "architect" ? { ...catalog, executions: [saved] } : { ...catalog, execution: saved }));
      if (url === "/api/orca/result") {
        const next = results.length > 1 ? results.shift() : results[0];
        return new Response(JSON.stringify(next));
      }
      if (url === "/api/orca/dispatch") {
        dispatched.push(sent);
        return new Response(JSON.stringify(dispatchReply.body), { status: dispatchReply.status });
      }
      return new Response(JSON.stringify({ available: false }), { status: 503 });
    }));
    return render(<ChatsModePanels active selectedLifecycleId={undefined} selectedLeafKey={undefined} taskDocuments={taskDocuments} series={[]} contextMaster={undefined} />);
  }

  beforeEach(() => {
    results = [];
    dispatched = [];
    sessionStorage.clear();
  });

  it("shows a closed execution and follows it into a further turn when Result is pressed", async () => {
    const completed = execution({ status: "completed", detail: "last turn finished; the agent is idle", canStart: true, result: { summary: "READY" } });
    results = [completed, execution({ status: "running", detail: "a turn is in progress", result: { summary: "READY" } }), execution({ status: "completed", canStart: true, result: { summary: "AGAIN" } })];
    const { findByTestId, getByTestId, getByRole } = renderLauncher(execution({}));

    await waitFor(async () => expect((await findByTestId("orca-execution-status")).textContent).toContain("completed · last turn finished"));
    expect(getByTestId("orca-result-summary").textContent).toBe("Last reply: READY");
    expect(getByTestId("orca-report-path").textContent).toContain(REPORT.path);

    // Result stays available for a closed execution: the agent may have taken another turn.
    fireEvent.click(getByRole("button", { name: "Refresh Orca result" }));
    await waitFor(() => expect(getByTestId("orca-execution-status").textContent).toBe("Current Orca execution: running · a turn is in progress"));
    await waitFor(() => expect((getByRole("button", { name: "Refresh Orca result" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(getByRole("button", { name: "Refresh Orca result" }));
    await waitFor(() => expect(getByTestId("orca-result-summary").textContent).toBe("Last reply: AGAIN"));
  });

  it("shows the host-unreachable line from a refresh and keeps the last known status", async () => {
    results = [execution({ status: "completed", canStart: true, hostUnreachable: true, result: { summary: "READY" } })];
    const { findByTestId, getByTestId } = renderLauncher(execution({}));

    expect((await findByTestId("orca-host-unreachable")).textContent).toBe("host unreachable; showing the last known status");
    expect(getByTestId("orca-execution-status").textContent).toBe("Current Orca execution: completed");
    expect(getByTestId("orca-result-summary").textContent).toBe("Last reply: READY");
  });

  it("shows a task-bound execution from the options answer and from Result", async () => {
    const sprint = { kind: "master", orchestrates: ["sbx-m1"], repository: "sandbox-app", docPath: "/coordination/tasks/sandbox-app/sbx-sprint/task.json", id: "SBX-SPRINT", title: "Sandbox sprint" } as unknown as TaskDocNode;
    // The options route answered from its cached catalog while the daemon was down.
    const lastKnown = execution({ status: "completed", canStart: true, hostUnreachable: true, result: { summary: "READY" } });
    results = [execution({ status: "running", detail: "a turn is in progress", result: { summary: "READY" } })];
    const { getByLabelText, findByLabelText, findByTestId, getByTestId, getByRole, queryByTestId } = renderLauncher(lastKnown, [sprint]);

    fireEvent.change(getByLabelText("Role"), { target: { value: "orchestrator" } });
    fireEvent.change(await findByLabelText("AR sprint"), { target: { value: "0" } });
    expect((await findByTestId("orca-host-unreachable")).textContent).toBe("host unreachable; showing the last known status");
    expect(getByTestId("orca-execution-status").textContent).toBe("Current Orca execution: completed");
    expect(getByTestId("orca-result-summary").textContent).toBe("Last reply: READY");

    fireEvent.click(getByRole("button", { name: "Refresh Orca result" }));
    await waitFor(() => expect(getByTestId("orca-execution-status").textContent).toBe("Current Orca execution: running · a turn is in progress"));
    expect(queryByTestId("orca-host-unreachable")).toBeNull();
    expect(getByTestId("orca-result-summary").textContent).toBe("Last reply: READY");
    expect(getByTestId("orca-report-path").textContent).toContain(REPORT.path);
  });

  it("revives the selected execution and keeps a refusal on screen", async () => {
    const closed = execution({ status: "completed", detail: "session closed", canStart: true, canRevive: true });
    results = [closed];
    dispatchReply = { status: 409, body: { detail: "The Paseo runtime cannot be reached, so the agent was not revived.", hostUnreachable: true } };
    const { findByRole, getByTestId, queryByRole } = renderLauncher(closed);

    const revive = await findByRole("button", { name: "Revive Orca" });
    await waitFor(() => expect((revive as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(revive);

    await waitFor(() => expect(dispatched).toEqual([{ requestId: REQUEST_ID, action: "revive", role: "architect" }]));
    const alert = await findByRole("alert");
    expect(alert.textContent).toBe("Orca could not accept this launch (HTTP 409): The Paseo runtime cannot be reached, so the agent was not revived.");
    // The refreshes that follow a dispatch do not wipe the refusal.
    await waitFor(() => expect(queryByRole("button", { name: "Revive Orca" })).not.toBeNull());
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(queryByRole("alert")?.textContent).toContain("was not revived");
    expect(getByTestId("orca-execution-status").textContent).toBe("Current Orca execution: completed · session closed");
  });
});
