// The launcher retains selected-execution behavior while showing only actionable failures.
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RoleChatsPane } from "./RoleChats";
import { HOST_UNREACHABLE_LINE, RoleExecutionStatus, RoleReviveControl } from "./RoleExecutionStatus";
import { tasklessRequestStorageKey, type RoleScopedExecution } from "./roleLaunchModel";
import type { TaskDocNode } from "../types/projection";


const REQUEST_ID = "5f0c1f6e-8a53-4d5b-9d53-6f0f1f6f2a10";
const REPORT = { path: "/projects/.agents-remember/reports/architect.md", canonicalPath: "/projects/.agents-remember/reports/architect.md", available: false };

function execution(fields: Partial<RoleScopedExecution>): RoleScopedExecution {
  return { status: "running", requestId: REQUEST_ID, canStart: false, canRevive: false, canRetry: false, report: REPORT, ...fields };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("execution failure line", () => {
  it("leaves ordinary execution, reply and report details to the chat", () => {
    const { container, rerender } = render(<RoleExecutionStatus execution={execution({})} className="meta" />);
    for (const summary of ["READY", "Long reply.\n".repeat(300)]) {
      rerender(<RoleExecutionStatus execution={execution({ status: "completed", detail: "last turn finished", result: { summary }, report: { ...REPORT, available: true } })} className="meta" />);
      expect(container.childElementCount).toBe(0);
    }
  });

  it("shows one actionable line with every host failure reason and clears after recovery", () => {
    const { container, getByRole, rerender } = render(<RoleExecutionStatus className="meta" />);
    for (const reason of ["paseo_daemon_unreachable: connection refused", "paseo_bridge_timeout: The bridge call ran out of time.", undefined]) {
      rerender(<RoleExecutionStatus execution={execution({ hostUnreachable: true, hostUnreachableReason: reason, result: { summary: "READY" } })} className="meta" />);
      expect(container.childElementCount).toBe(1);
      expect(getByRole("alert").textContent).toBe(HOST_UNREACHABLE_LINE + (reason ? " (" + reason + ")" : ""));
    }
    rerender(<RoleExecutionStatus execution={execution({})} className="meta" />);
    expect(container.childElementCount).toBe(0);
  });

  it("combines concurrent action and host failures into one line", () => {
    const { container, getByRole } = render(<RoleExecutionStatus execution={execution({ hostUnreachable: true })} failure="Start was refused: selection occupied." className="meta" />);
    expect(container.childElementCount).toBe(1);
    expect(getByRole("alert").textContent).toBe("Start was refused: selection occupied. " + HOST_UNREACHABLE_LINE);
  });

  it("offers Revive only for a revivable execution", () => {
    const onRevive = vi.fn();
    const { queryByRole, getByRole, rerender } = render(<RoleReviveControl execution={execution({})} enabled className="button" onRevive={onRevive} />);
    expect(queryByRole("button")).toBeNull();
    rerender(<RoleReviveControl execution={undefined} enabled className="button" onRevive={onRevive} />);
    expect(queryByRole("button")).toBeNull();

    rerender(<RoleReviveControl execution={execution({ status: "interrupted", canRevive: true })} enabled={false} className="button" onRevive={onRevive} />);
    expect((getByRole("button", { name: "Revive role" }) as HTMLButtonElement).disabled).toBe(true);
    rerender(<RoleReviveControl execution={execution({ status: "interrupted", canRevive: true })} enabled className="button" onRevive={onRevive} />);
    fireEvent.click(getByRole("button", { name: "Revive role" }));
    expect(onRevive).toHaveBeenCalledTimes(1);
  });
});

describe("launcher bar and the execution's state", () => {
  let results: RoleScopedExecution[] = [];
  let dispatched: Record<string, unknown>[] = [];
  let resultReads = 0;
  let optionsBarrier: Promise<void> | null = null;
  let optionsReplies: { status: number; body: unknown }[] = [];
  let reportReply: { status: number; body: unknown };
  let reportRequests: Record<string, unknown>[] = [];
  let reportBarrier: Promise<void> | null = null;
  let reportTransportFailure: "network" | "non-json" | null = null;
  let afterDispatch: ((sent: Record<string, unknown>) => void) | null = null;
  let dispatchTransportFailure = false;
  let missingResult = false;
  let dispatchReply: { status: number; body: unknown } = { status: 200, body: {} };
  // How many of the next result reads meet the backend's launch lock, held by a launch.
  let lockedResults = 0;

  // `saved` is what the options route returns: the taskless executions, or the selection's one.
  function renderLauncher(saved: RoleScopedExecution, taskDocuments: TaskDocNode[] = []) {
    sessionStorage.setItem(tasklessRequestStorageKey("architect"), JSON.stringify({ requestId: REQUEST_ID }));
    const catalog = { roleDefaults: { agent: "codex", available: true }, agents: [{ id: "codex", label: "Codex", models: [] }], catalogOrigin: "paseo:1" };
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      const sent = JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>;
      if (url === "/api/role-launch/options") {
        if (optionsBarrier) await optionsBarrier;
        const reply = optionsReplies.shift();
        if (reply) return new Response(JSON.stringify(reply.body), { status: reply.status });
        return new Response(JSON.stringify(sent.role === "architect" ? { ...catalog, executions: [saved] } : { ...catalog, execution: saved }));
      }
      if (url === "/api/role-launch/result") {
        resultReads += 1;
        if (missingResult) return new Response(JSON.stringify({ detail: "No saved receipt." }), { status: 404 });
        if (lockedResults > 0) {
          lockedResults -= 1;
          return new Response(JSON.stringify({ detail: "A role launch or result check is already in progress.", launchInProgress: true }), { status: 409 });
        }
        const next = results.length > 1 ? results.shift() : results[0];
        return new Response(JSON.stringify(next));
      }
      if (url === "/api/role-launch/report") {
        reportRequests.push(sent);
        if (reportBarrier) await reportBarrier;
        if (reportTransportFailure === "network") throw new TypeError("Failed to fetch");
        if (reportTransportFailure === "non-json") return new Response("Internal Server Error", { status: 500 });
        return new Response(JSON.stringify(reportReply.body), { status: reportReply.status });
      }
      if (url === "/api/role-launch/dispatch") {
        dispatched.push(sent);
        if (dispatchTransportFailure) throw new TypeError("dispatch connection closed");
        afterDispatch?.(sent);
        return new Response(JSON.stringify(dispatchReply.body), { status: dispatchReply.status });
      }
      return new Response(JSON.stringify({ available: false }), { status: 503 });
    }));
    return render(<RoleChatsPane active taskDocuments={taskDocuments} series={[]} />);
  }

  beforeEach(() => {
    results = [];
    dispatched = [];
    lockedResults = 0;
    resultReads = 0;
    optionsBarrier = null;
    optionsReplies = [];
    reportTransportFailure = null;
    afterDispatch = null;
    dispatchTransportFailure = false;
    missingResult = false;
    reportRequests = [];
    reportBarrier = null;
    reportReply = { status: 200, body: { path: "architect.md", language: "markdown", size: 20, truncated: false, content: "# The role report" } };
    sessionStorage.clear();
  });

  it("keeps Result refreshing execution capabilities without displaying short or long replies", async () => {
    const completed = execution({ status: "completed", canStart: true, result: { summary: "READY" } });
    results = [completed, execution({ canRevive: true, result: { summary: "Long reply.\n".repeat(300) } }), completed];
    const { getByTestId, getByRole } = renderLauncher(execution({}));
    const start = getByRole("button", { name: "Start role" }) as HTMLButtonElement;
    await waitFor(() => expect(start.disabled).toBe(false));
    const launcher = getByTestId("role-launcher");
    const controls = launcher.childElementCount;
    expect(launcher.outerHTML).not.toMatch(/Current role execution|Last reply|Report:|READY/);
    expect(launcher.lastElementChild?.querySelector('[aria-label="Refresh role agents"]')).not.toBeNull();
    fireEvent.click(getByRole("button", { name: "Refresh role result" }));
    await waitFor(() => expect(resultReads).toBe(2));
    const reader = await waitFor(() => getByTestId("notes-reader-viewer"));
    expect(reader.textContent).toContain("The role report");
    expect(reader.querySelectorAll('[data-testid="dual-pane"]')).toHaveLength(1);
    expect(getByTestId("notes-reader-open").textContent).toBe("architect.md");
    fireEvent.click(getByTestId("notes-reader-back"));
    expect(reportRequests).toEqual([{ role: "architect", requestId: REQUEST_ID }]);
    await waitFor(() => expect(getByRole("button", { name: "Revive role" })).toBeDefined());
    expect(launcher.childElementCount).toBe(controls);
    expect(launcher.outerHTML).not.toContain("Long reply.");
    expect(launcher.outerHTML).not.toContain(REPORT.path);
    expect(launcher.lastElementChild?.querySelector('[aria-label="Refresh role agents"]')).not.toBeNull();
    await waitFor(() => expect((getByRole("button", { name: "Refresh role result" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(getByRole("button", { name: "Refresh role result" }));
    await waitFor(() => expect(getByTestId("notes-reader-viewer")).toBeDefined());
    fireEvent.click(getByTestId("notes-reader-back"));
    await waitFor(() => expect(launcher.querySelector('[aria-label="Revive role"]')).toBeNull());
    expect(resultReads).toBe(3);
  });

  it("names an explicit Result held by a launch and clears the line after its reread", async () => {
    results = [execution({}), execution({ status: "completed", canStart: true, result: { summary: "DONE" } })];
    const { getByTestId, getByRole } = renderLauncher(execution({}));
    await waitFor(() => expect(resultReads).toBe(1));
    const result = getByRole("button", { name: "Refresh role result" }) as HTMLButtonElement;
    await waitFor(() => expect(result.disabled).toBe(false));
    lockedResults = 1;
    fireEvent.click(result);
    await waitFor(() => expect(resultReads).toBe(2));
    const launcher = getByTestId("role-launcher");
    await waitFor(() => expect(launcher.querySelector('[role="alert"]')?.textContent).toContain("report could not be opened while a role launch"));
    expect(launcher.querySelectorAll('[role="alert"]')).toHaveLength(1);
    expect(reportRequests).toHaveLength(0);
    await waitFor(() => expect(resultReads).toBe(3), { timeout: 4000 });
    await waitFor(() => expect((getByRole("button", { name: "Start role" }) as HTMLButtonElement).disabled).toBe(false));
    expect(launcher.textContent).not.toContain("DONE");
    expect(launcher.querySelector('[role="status"],[role="alert"]')).toBeNull();
  });

  it("clears a task-bound host failure on the next successful Result", async () => {
    const sprint = { kind: "master", orchestrates: ["sbx-m1"], repository: "sandbox-app", docPath: "/coordination/tasks/sandbox-app/sbx-sprint/task.json", id: "SBX-SPRINT", title: "Sandbox sprint" } as unknown as TaskDocNode;
    const lastKnown = execution({ status: "completed", canStart: true, hostUnreachable: true });
    results = [execution({})];
    const { getByLabelText, findByLabelText, findByTestId, getByTestId, getByRole, queryByTestId } = renderLauncher(lastKnown, [sprint]);
    fireEvent.change(getByLabelText("Role"), { target: { value: "orchestrator" } });
    fireEvent.change(await findByLabelText("AR sprint"), { target: { value: "0" } });
    expect((await findByTestId("role-host-unreachable")).textContent).toBe(HOST_UNREACHABLE_LINE);
    let release!: () => void;
    optionsBarrier = new Promise<void>((resolve) => { release = resolve; });
    fireEvent.click(getByRole("button", { name: "Refresh role agents" }));
    await waitFor(() => expect((getByRole("button", { name: "Refresh role agents" }) as HTMLButtonElement).disabled).toBe(true));
    expect(queryByTestId("role-host-unreachable")?.textContent).toBe(HOST_UNREACHABLE_LINE);
    release();
    await waitFor(() => expect((getByRole("button", { name: "Refresh role result" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(getByRole("button", { name: "Refresh role result" }));
    await waitFor(() => expect(getByTestId("notes-reader-viewer")).toBeDefined());
    fireEvent.click(getByTestId("notes-reader-back"));
    await waitFor(() => expect(queryByTestId("role-host-unreachable")).toBeNull());
    expect((getByRole("button", { name: "Start role" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("keeps a refused Revive visible through automatic reads and clears on successful Refresh", async () => {
    const closed = execution({ status: "completed", canStart: true, canRevive: true });
    results = [closed];
    dispatchReply = { status: 409, body: { detail: "The Paseo runtime cannot be reached, so the agent was not revived.", hostUnreachable: true } };
    const { findByRole, getByTestId, getByRole, queryByRole } = renderLauncher(closed);
    const revive = await findByRole("button", { name: "Revive role" });
    await waitFor(() => expect((revive as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(revive);
    await waitFor(() => expect(dispatched).toEqual([{ requestId: REQUEST_ID, action: "revive", role: "architect" }]));
    expect((await findByRole("alert")).textContent).toBe("The role launch was not accepted (HTTP 409): The Paseo runtime cannot be reached, so the agent was not revived.");
    await waitFor(() => expect((getByRole("button", { name: "Refresh role agents" }) as HTMLButtonElement).disabled).toBe(false));
    expect(getByTestId("role-launcher").querySelectorAll('[role="alert"]')).toHaveLength(1);
    expect(queryByRole("alert")?.textContent).toContain("was not revived");
    let release!: () => void;
    optionsBarrier = new Promise<void>((resolve) => { release = resolve; });
    fireEvent.click(getByRole("button", { name: "Refresh role agents" }));
    await waitFor(() => expect((getByRole("button", { name: "Refresh role agents" }) as HTMLButtonElement).disabled).toBe(true));
    expect(queryByRole("alert")?.textContent).toContain("was not revived");
    release();
    await waitFor(() => expect(queryByRole("alert")).toBeNull());
  });

  it("names a refused Start and clears the failure after successful Result", async () => {
    const completed = execution({ status: "completed", canStart: true });
    results = [completed];
    dispatchReply = { status: 409, body: { detail: "The selected role already has an open execution." } };
    const sprint = { kind: "master", orchestrates: ["sbx-m1"], repository: "sandbox-app", docPath: "/coordination/tasks/sandbox-app/sbx-sprint/task.json", id: "SBX-SPRINT", title: "Sandbox sprint" } as unknown as TaskDocNode;
    const { getByRole, getByTestId, getByLabelText, findByLabelText, findByRole } = renderLauncher(completed, [sprint]);
    fireEvent.change(getByLabelText("Role"), { target: { value: "orchestrator" } });
    fireEvent.change(await findByLabelText("AR sprint"), { target: { value: "0" } });
    const start = getByRole("button", { name: "Start role" }) as HTMLButtonElement;
    await waitFor(() => expect(start.disabled).toBe(false));
    fireEvent.click(start);
    expect((await findByRole("alert")).textContent).toContain("The selected role already has an open execution.");
    await waitFor(() => expect((getByRole("button", { name: "Refresh role result" }) as HTMLButtonElement).disabled).toBe(false));
    expect(getByTestId("role-launcher").querySelectorAll('[role="alert"]')).toHaveLength(1);
    fireEvent.click(getByRole("button", { name: "Refresh role result" }));
    await waitFor(() => getByTestId("notes-reader-back"));
    fireEvent.click(getByTestId("notes-reader-back"));
    expect(getByTestId("role-launcher").querySelector('[role="alert"]')).toBeNull();
  });
  it("shows missing, unreadable and refused report reads as one failure and recovers", async () => {
    results = [execution({ status: "completed", canStart: true })];
    const { getByRole, findByRole, getByTestId, queryByTestId } = renderLauncher(results[0]);
    await waitFor(() => expect((getByRole("button", { name: "Refresh role result" }) as HTMLButtonElement).disabled).toBe(false));
    for (const detail of ["The report is not written yet.", "The report cannot be read.", "The recorded report lies outside its report root."]) {
      reportReply = { status: 404, body: { status: "report-refused", detail } };
      fireEvent.click(getByRole("button", { name: "Refresh role result" }));
      expect((await findByRole("alert")).textContent).toBe("The report could not be opened (HTTP 404): " + detail);
      expect(queryByTestId("notes-reader-viewer")).toBeNull();
      expect(getByTestId("role-launcher").querySelectorAll('[role="alert"]')).toHaveLength(1);
      await waitFor(() => expect((getByRole("button", { name: "Refresh role result" }) as HTMLButtonElement).disabled).toBe(false));
    }
    reportReply = { status: 200, body: { path: "architect.md", language: "markdown", size: 20, truncated: false, content: "# Written report" } };
    let release!: () => void;
    reportBarrier = new Promise<void>((resolve) => { release = resolve; });
    fireEvent.click(getByRole("button", { name: "Refresh role result" }));
    await waitFor(() => expect(reportRequests).toHaveLength(4));
    expect(getByTestId("role-launcher").querySelector('[role="alert"]')?.textContent).toBe("The report could not be opened (HTTP 404): The recorded report lies outside its report root.");
    release();
    await waitFor(() => expect(getByTestId("notes-reader-viewer")).toBeDefined());
    fireEvent.click(getByTestId("notes-reader-back"));
    expect(getByTestId("role-launcher").querySelector('[role="alert"]')).toBeNull();
  });

  it("keeps a 502 runtime refusal through automatic reads and clears it after explicit success", async () => {
    const reason = "The Paseo runtime refused the launch; no agent exists under the minted agent id. Provider allowance exhausted.";
    const completed = execution({ status: "completed", canStart: true });
    results = [completed];
    dispatchReply = { status: 502, body: {} };
    afterDispatch = (sent) => {
      const refused = execution({ status: "rejected", requestId: String(sent.requestId), canStart: true, detail: reason });
      dispatchReply.body = refused;
      results = [refused];
    };
    const { getByRole, getByTestId } = renderLauncher(completed);
    const start = getByRole("button", { name: "Start role" }) as HTMLButtonElement;
    await waitFor(() => expect(start.disabled).toBe(false));
    fireEvent.click(start);
    await waitFor(() => expect(resultReads).toBeGreaterThanOrEqual(3));
    await waitFor(() => expect((getByRole("button", { name: "Refresh role agents" }) as HTMLButtonElement).disabled).toBe(false));
    const launcher = getByTestId("role-launcher");
    expect(launcher.querySelectorAll('[role="alert"]')).toHaveLength(1);
    expect(launcher.querySelector('[role="alert"]')?.textContent).toBe("The role handover was not accepted (HTTP 502): " + reason);
    fireEvent.click(getByRole("button", { name: "Refresh role agents" }));
    await waitFor(() => expect(launcher.querySelector('[role="alert"]')).toBeNull());
  });

  it("clears a task-bound report error after Retry launch options succeeds", async () => {
    const sprint = { kind: "master", orchestrates: ["sbx-m1"], repository: "sandbox-app", docPath: "/coordination/tasks/sandbox-app/sbx-sprint/task.json", id: "SBX-SPRINT", title: "Sandbox sprint" } as unknown as TaskDocNode;
    const saved = execution({ status: "completed", canStart: true });
    results = [saved];
    const { getByRole, getByTestId, getByLabelText, findByLabelText, findByRole } = renderLauncher(saved, [sprint]);
    fireEvent.change(getByLabelText("Role"), { target: { value: "orchestrator" } });
    fireEvent.change(await findByLabelText("AR sprint"), { target: { value: "0" } });
    await waitFor(() => expect((getByRole("button", { name: "Refresh role result" }) as HTMLButtonElement).disabled).toBe(false));
    reportReply = { status: 404, body: { detail: "The report is not written yet." } };
    fireEvent.click(getByRole("button", { name: "Refresh role result" }));
    expect((await findByRole("alert")).textContent).toContain("report is not written yet");
    optionsReplies = [{ status: 503, body: { detail: "Catalog connection failed." } }];
    fireEvent.click(getByRole("button", { name: "Refresh role agents" }));
    const retry = await findByRole("button", { name: "Retry launch options" });
    const launcher = getByTestId("role-launcher");
    expect(launcher.querySelectorAll('[role="alert"]')).toHaveLength(1);
    expect(launcher.querySelector('[role="alert"]')?.textContent).toContain("Catalog connection failed.");
    expect(launcher.querySelector('[role="alert"]')?.textContent).toContain("report is not written yet");
    fireEvent.click(retry);
    await waitFor(() => expect(launcher.querySelector('[role="alert"],[role="status"]')).toBeNull());
  });

  it("names malformed JSON, validation details and connection failures of a report read", async () => {
    results = [execution({ status: "completed", canStart: true })];
    const { getByRole, getByTestId } = renderLauncher(results[0]);
    await waitFor(() => expect((getByRole("button", { name: "Refresh role result" }) as HTMLButtonElement).disabled).toBe(false));
    const cases: ["network" | "non-json" | null, { status: number; body: unknown }, string][] = [
      [null, { status: 422, body: { detail: [{ msg: "Field required" }] } }, "The report could not be opened (HTTP 422)."],
      ["non-json", { status: 500, body: null }, "The report could not be opened (HTTP 500)."],
      ["network", { status: 500, body: null }, "The report could not be opened. Check the dashboard connection and retry."],
    ];
    for (const [failure, reply, expected] of cases) {
      reportTransportFailure = failure;
      reportReply = reply;
      fireEvent.click(getByRole("button", { name: "Refresh role result" }));
      const launcher = getByTestId("role-launcher");
      await waitFor(() => expect(launcher.querySelector('[role="alert"]')?.textContent).toBe(expected));
      expect(launcher.querySelectorAll('[role="alert"]')).toHaveLength(1);
      await waitFor(() => expect((getByRole("button", { name: "Refresh role result" }) as HTMLButtonElement).disabled).toBe(false));
    }
  });

  it("shows a 202 unresolved launch without claiming an agent failed until a known receipt arrives", async () => {
    const initial = execution({ status: "completed", canStart: true });
    results = [initial];
    dispatchReply = { status: 202, body: {} };
    afterDispatch = (sent) => {
      const unresolved = execution({ status: "unknown", requestId: String(sent.requestId), canRetry: true, retryPayload: { role: "architect" }, detail: "The launch has no usable answer: the bridge timed out." });
      dispatchReply.body = unresolved;
      results = [unresolved];
    };
    const { getByRole, getByTestId } = renderLauncher(initial);
    const start = getByRole("button", { name: "Start role" }) as HTMLButtonElement;
    await waitFor(() => expect(start.disabled).toBe(false));
    fireEvent.click(start);
    await waitFor(() => expect(resultReads).toBeGreaterThanOrEqual(3));
    const launcher = getByTestId("role-launcher");
    expect(launcher.querySelectorAll('[role="alert"]')).toHaveLength(1);
    expect(launcher.querySelector('[role="alert"]')?.textContent).toContain("bridge timed out");
    expect(launcher.textContent).not.toMatch(/agent failed|Current role execution|Last reply|Report:/i);
    const known = execution({ status: "completed", canStart: true, requestId: dispatched[0].requestId as string });
    results = [known];
    fireEvent.click(getByRole("button", { name: "Refresh role result" }));
    await waitFor(() => getByTestId("notes-reader-back"));
    fireEvent.click(getByTestId("notes-reader-back"));
    expect(launcher.querySelector('[role="alert"]')).toBeNull();
  });

  it("names a saved pending request with no receipt and resolves the line after a known read", async () => {
    missingResult = true;
    const view = renderLauncher(execution({}));
    // Simulate reload of the exact persisted pending launch, without any saved execution.
    view.unmount();
    sessionStorage.setItem(tasklessRequestStorageKey("architect"), JSON.stringify({ requestId: REQUEST_ID, pending: true, retryPayload: { role: "architect" } }));
    const fetch = vi.mocked(globalThis.fetch);
    fetch.mockImplementationOnce(async () => new Response(JSON.stringify({ roleDefaults: { agent: "codex", available: true }, agents: [{ id: "codex", label: "Codex", models: [] }], catalogOrigin: "paseo:1", executions: [] })));
    const { getByTestId, getByRole } = render(<RoleChatsPane active taskDocuments={[]} series={[]} />);
    const launcher = getByTestId("role-launcher");
    await waitFor(() => expect(launcher.querySelector('[role="alert"]')?.textContent).toContain("No saved role execution receipt"));
    expect(launcher.querySelectorAll('[role="alert"]')).toHaveLength(1);
    expect(launcher.textContent).not.toContain("agent failed");
    missingResult = false;
    results = [execution({ status: "completed", canStart: true })];
    fireEvent.click(getByRole("button", { name: "Refresh role result" }));
    await waitFor(() => getByTestId("notes-reader-back"));
    fireEvent.click(getByTestId("notes-reader-back"));
    expect(launcher.querySelector('[role="alert"]')).toBeNull();
  });

  it("preserves the base controls when a task-bound dispatch and its options read fail", async () => {
    const sprint = { kind: "master", orchestrates: ["sbx-m1"], repository: "sandbox-app", docPath: "/coordination/tasks/sandbox-app/sbx-sprint/task.json", id: "SBX-SPRINT", title: "Sandbox sprint" } as unknown as TaskDocNode;
    const saved = execution({ status: "completed", canStart: true });
    results = [saved];
    const { getByRole, getByTestId, getByLabelText, findByLabelText, queryByRole } = renderLauncher(saved, [sprint]);
    fireEvent.change(getByLabelText("Role"), { target: { value: "orchestrator" } });
    fireEvent.change(await findByLabelText("AR sprint"), { target: { value: "0" } });
    await waitFor(() => expect((getByRole("button", { name: "Start role" }) as HTMLButtonElement).disabled).toBe(false));
    dispatchTransportFailure = true;
    missingResult = true;
    optionsReplies = [{ status: 503, body: { detail: "Catalog connection failed." } }];
    fireEvent.click(getByRole("button", { name: "Start role" }));
    const launcher = getByTestId("role-launcher");
    await waitFor(() => expect(launcher.querySelector('[role="alert"]')?.textContent).toContain("Catalog connection failed."));
    expect((getByLabelText("Role") as HTMLSelectElement).disabled).toBe(false);
    expect((getByLabelText("AR sprint") as HTMLSelectElement).disabled).toBe(false);
    expect((getByLabelText("Role agent override") as HTMLSelectElement).disabled).toBe(false);
    expect((getByRole("button", { name: "Start role" }) as HTMLButtonElement).disabled).toBe(false);
    expect(queryByRole("button", { name: "Retry role launch" })).toBeNull();
    expect(launcher.querySelectorAll('[role="alert"]')).toHaveLength(1);
    expect(launcher.querySelector('[role="alert"]')?.textContent).toContain("No dispatch receipt was returned");
  });

});
