import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { chatSprint as sprint, chatMaster as master, chatLeaf as leaf, chatSeries } from "../../test/fixtures/documentChat";
import { DocumentChat } from "./DocumentChat";
import { boundTasklessRequest, readTasklessActiveRequests } from "../roleLaunchModel";
import { documentChatAgent, documentChatBinding, type DocumentAgent } from "./model";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const ref = (path: string) => ({ repository: "repo", path });
const architect: DocumentAgent = { agentId: "architect", workspaceId: "projects", archivedAt: null, createdAt: "2026-01-01", labels: { "ar.role": "architect" } };
const worker: DocumentAgent = { agentId: "worker", workspaceId: "leaf-workspace", archivedAt: null, createdAt: "2026-01-02", labels: { "ar.role": "worker", "ar.task-ref": "repo/master/leaf.json" } };

describe("the document's native chat and the shared bound launcher", () => {
  it("keeps an unreadable host distinct from no agent and offers the shared Retry notice", async () => {
    let unavailable = true;
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const value = url.endsWith("/frame") ? { available: true, frameBaseUrl: "https://host.example", serverId: "server", projectsWorkspaceId: "projects" }
        : url.endsWith("/document-chats") ? unavailable ? { agents: [], unavailable: true, detail: "host lookup failed" } : { agents: [] }
        : { roleDefaults: {}, agents: [], executions: [] };
      return new Response(JSON.stringify(value));
    }));
    const { findByTestId, getByRole, queryByRole } = render(<DocumentChat active taskDocuments={[sprint, master, leaf]} series={chatSeries} />);
    expect((await findByTestId("paseo-frame-unavailable")).getAttribute("data-reason")).toBe("unreachable");
    expect(queryByRole("button", { name: "Start role" })).toBeNull();
    unavailable = false;
    fireEvent.click(getByRole("button", { name: "Retry" }));
    await findByTestId("role-launcher");
  });

  it("shows the exact Projects role started from the row even when no Architect exists", async () => {
    sessionStorage.clear();
    let requestId: string | undefined;
    const execution = { kind: "paseo-agent", agentId: "specialist", workspaceId: "projects" };
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      const body = init?.body ? JSON.parse(String(init.body)) as { requestId?: string; role?: string } : {};
      if (url.endsWith("/dispatch")) requestId = body.requestId;
      const value = url.endsWith("/frame") ? { available: true, frameBaseUrl: "https://host.example", serverId: "server", projectsWorkspaceId: "projects" }
        : url.endsWith("/document-chats") ? { agents: body.requestId === requestId && requestId ? [{ ...architect, agentId: "specialist", labels: { "ar.role": "system-specialist" } }] : [] }
        : url.endsWith("/dispatch") || url.endsWith("/result") ? { status: "running", requestId, canStart: true, canRevive: false, execution }
        : { roleDefaults: { agent: "codex", available: true }, agents: [{ id: "codex", label: "Codex", models: [] }], executions: [] };
      return new Response(JSON.stringify(value));
    }));
    const { container, getByRole } = render(<DocumentChat active taskDocuments={[sprint, master, leaf]} series={chatSeries} />);
    await waitFor(() => expect(container.querySelector("iframe")).not.toBeNull());
    const frame = container.querySelector("iframe")!;
    const posts = vi.spyOn(frame.contentWindow!, "postMessage");
    act(() => window.dispatchEvent(new MessageEvent("message", { origin: "https://host.example", source: frame.contentWindow, data: { source: "ar-plugin", type: "ready" } })));
    await waitFor(() => expect((getByRole("combobox", { name: "Role agent override" }) as HTMLSelectElement).disabled).toBe(false));
    fireEvent.change(getByRole("combobox", { name: "Role" }), { target: { value: "investigator" } });
    await waitFor(() => expect((getByRole("button", { name: "Start role" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(getByRole("button", { name: "Start role" }));
    await waitFor(() => expect(posts).toHaveBeenCalledWith({ type: "ar.open", agentId: "specialist", workspaceId: "projects" }, "https://host.example"));
    expect(container.querySelector("iframe")).toBe(frame);
  });

  it.each([
    ["sprint", "orchestrator"], ["master", "manager"],
  ] as const)("restores and keeps exact Investigator requests on %s while primary %s stays open", async (scope, primaryRole) => {
    sessionStorage.clear();
    const projectsKey = "ar-role-taskless-request-v1:system-specialist";
    const projectsRequest = JSON.stringify({ requestId: "original-projects-request", retryPayload: { role: "system-specialist" } });
    sessionStorage.setItem(projectsKey, projectsRequest);
    const documentRef = ref(scope + "/task.json");
    const selection = { role: "investigator" as const, sprintDocumentRef: ref("sprint/task.json"),
      ...(scope === "master" ? { masterDocumentRef: documentRef } : {}) };
    const primary = { ...architect, agentId: primaryRole, labels: { "ar.role": primaryRole, ["ar." + scope + "-ref"]: "repo/" + scope + "/task.json" } };
    const investigator = { ...primary, agentId: "investigator", createdAt: "2026-01-05", labels: { ...primary.labels, "ar.role": "investigator" } };
    const existingId = "00000000-0000-4000-8000-000000000001";
    let requestId = existingId;
    let status = "running";
    let restoresWithNewerSibling = false;
    const requests: { url: string; body: Record<string, unknown> }[] = [];
    const receipt = (id = requestId) => ({ status, requestId: id, canStart: true, canRevive: status === "completed",
      ...(status === "unknown" ? { canRetry: true, retryPayload: selection } : {}),
      execution: { kind: "paseo-agent", agentId: "investigator", workspaceId: "projects" } });
    const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
      const body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : {};
      requests.push({ url, body });
      if (url.endsWith("/dispatch")) {
        if (body.action === "revive") status = "running";
        else if (body.requestId === requestId) status = "completed";
        else { requestId = String(body.requestId); status = "unknown"; }
      }
      if ((url.endsWith("/result") || url.endsWith("/report")) && body.role === "investigator" && !body.requestId) {
        return new Response(JSON.stringify({ detail: "Taskless role results require the exact saved requestId." }), { status: 400 });
      }
      const executions = restoresWithNewerSibling
        ? [receipt("00000000-0000-4000-8000-000000000099"), receipt()] : [receipt()];
      const value = url.endsWith("/frame") ? { available: true, frameBaseUrl: "https://host.example", serverId: "server", projectsWorkspaceId: "projects" }
        : url.endsWith("/document-chats") ? { agents: body.requestId ? [investigator] : [investigator, primary] }
        : url.endsWith("/dispatch") || url.endsWith("/result") ? receipt(String(body.requestId))
        : url.endsWith("/report") ? { path: "/report.md", language: "markdown", size: 16, truncated: false, content: "Investigator report" }
        : { roleDefaults: { agent: "codex", available: true }, agents: [{ id: "codex", label: "Codex", models: [] }],
            ...(body.role === "investigator" ? { executions, omittedCount: 0 } : { execution: null }) };
      return new Response(JSON.stringify(value));
    });
    vi.stubGlobal("fetch", fetcher);
    const props = { active: true, taskDocumentRef: documentRef, taskDocuments: [sprint, master, leaf], series: chatSeries };
    const ui = render(<DocumentChat {...props} />);
    await waitFor(() => expect(ui.container.querySelector("iframe")).not.toBeNull());
    const frame = ui.container.querySelector("iframe")!;
    const posts = vi.spyOn(frame.contentWindow!, "postMessage");
    act(() => window.dispatchEvent(new MessageEvent("message", { origin: "https://host.example", source: frame.contentWindow, data: { source: "ar-plugin", type: "ready" } })));
    await waitFor(() => expect(posts).toHaveBeenCalledWith({ type: "ar.open", agentId: primaryRole, workspaceId: "projects" }, "https://host.example"));
    fireEvent.click(ui.getByText("Launch role"));
    await waitFor(() => expect((ui.getByRole("combobox", { name: "Role" }) as HTMLSelectElement).disabled).toBe(false));
    expect([...ui.container.querySelectorAll("#launcher-role option")].map((option) => option.textContent)).toEqual(["Investigator", primaryRole === "manager" ? "Manager" : "Orchestrator"]);
    fireEvent.change(ui.getByRole("combobox", { name: "Role" }), { target: { value: "investigator" } });
    const resultCalls = () => requests.filter(({ url, body }) => url.endsWith("/result") && body.role === "investigator");
    await waitFor(() => expect(resultCalls().map(({ body }) => body)).toContainEqual({ ...selection, requestId: existingId }));
    await waitFor(() => expect((ui.getByRole("button", { name: "Refresh role result" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(ui.getByRole("button", { name: "Refresh role result" }));
    await ui.findByTestId("notes-reader-back");
    expect(requests.filter(({ url }) => url.endsWith("/report")).at(-1)?.body).toEqual({ ...selection, requestId: existingId });
    fireEvent.click(ui.getByTestId("notes-reader-back"));
    await waitFor(() => expect((ui.getByRole("button", { name: "Start role" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(ui.getByRole("button", { name: "Start role" }));
    await waitFor(() => expect(ui.getByRole("button", { name: "Retry role launch" })).not.toBeNull());
    const freshId = requestId;
    expect(freshId).not.toBe(existingId);
    await waitFor(() => expect(resultCalls().map(({ body }) => body)).toContainEqual({ ...selection, requestId: freshId }));
    fireEvent.click(ui.getByRole("button", { name: "Retry role launch" }));
    await waitFor(() => expect((ui.getByRole("button", { name: "Revive role" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(ui.getByRole("button", { name: "Revive role" }));
    await waitFor(() => expect(ui.queryByRole("button", { name: "Revive role" })).toBeNull());
    await waitFor(() => expect((ui.getByRole("button", { name: "Refresh role result" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(ui.getByRole("button", { name: "Refresh role result" }));
    await ui.findByTestId("notes-reader-back");
    expect(requests.filter(({ url }) => url.endsWith("/report")).at(-1)?.body).toEqual({ ...selection, requestId: freshId });
    fireEvent.click(ui.getByTestId("notes-reader-back"));
    expect(requests.filter(({ url }) => url.endsWith("/dispatch")).map(({ body }) => body)).toEqual([
      { ...selection, action: "start", requestId: freshId },
      { ...selection, action: "start", requestId: freshId },
      { ...selection, action: "revive", requestId: freshId },
    ]);
    expect(resultCalls().every(({ body }) => typeof body.requestId === "string")).toBe(true);
    expect(posts.mock.calls.some(([message]) => message.agentId === "investigator")).toBe(false);
    expect(requests.filter(({ url }) => url.endsWith("/document-chats")).every(({ body }) => body.role === primaryRole && !body.requestId)).toBe(true);
    // Remounting restores this task's saved exact request, even with a newer listed sibling.
    ui.unmount(); restoresWithNewerSibling = true;
    const before = requests.length;
    const restored = render(<DocumentChat {...props} />);
    await restored.findByText("Launch role");
    fireEvent.click(restored.getByText("Launch role"));
    await waitFor(() => expect((restored.getByRole("combobox", { name: "Role" }) as HTMLSelectElement).disabled).toBe(false));
    fireEvent.change(restored.getByRole("combobox", { name: "Role" }), { target: { value: "investigator" } });
    await waitFor(() => expect(requests.slice(before).filter(({ url }) => url.endsWith("/result")).map(({ body }) => body)).toContainEqual({ ...selection, requestId: freshId }));
    expect(sessionStorage.getItem(projectsKey)).toBe(projectsRequest);
    console.log(JSON.stringify({ scope, optionsShape: "executions only", existingId, freshId, requests, primaryPreserved: true }));
  });

  it("recovers the old browser request and retries it unchanged under the single Investigator choice", async () => {
    sessionStorage.clear();
    const storageKey = "ar-role-taskless-request-v1:system-specialist";
    const saved = JSON.stringify({ requestId: "original-specialist-request", pending: true, retryPayload: { role: "system-specialist" } });
    sessionStorage.setItem(storageKey, saved);
    expect(readTasklessActiveRequests().investigator).toEqual(JSON.parse(saved));
    expect(sessionStorage.getItem(storageKey)).toBe(saved);
    expect(sessionStorage.getItem("ar-role-taskless-request-v1:investigator")).toBeNull();
    const dispatches: object[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      const body = init?.body ? JSON.parse(String(init.body)) : {};
      if (url.endsWith("/dispatch")) dispatches.push(body);
      const value = url.endsWith("/frame") ? { available: true, frameBaseUrl: "https://host.example", serverId: "server", projectsWorkspaceId: "projects" }
        : url.endsWith("/document-chats") ? { agents: [] }
        : url.endsWith("/dispatch") ? { status: "running", requestId: "original-specialist-request", canStart: true, canRevive: false }
        : { roleDefaults: { agent: "codex", available: true }, agents: [{ id: "codex", label: "Codex", models: [] }], executions: [] };
      return new Response(JSON.stringify(value), { status: url.endsWith("/result") ? 404 : 200 });
    }));
    const { container, getByRole } = render(<DocumentChat active taskDocuments={[sprint, master, leaf]} series={chatSeries} />);
    await waitFor(() => expect(container.querySelector("#launcher-role")).not.toBeNull());
    await waitFor(() => expect((getByRole("combobox", { name: "Role" }) as HTMLSelectElement).disabled).toBe(false));
    fireEvent.change(getByRole("combobox", { name: "Role" }), { target: { value: "investigator" } });
    await waitFor(() => expect((getByRole("button", { name: "Retry role launch" }) as HTMLButtonElement).disabled).toBe(false));
    expect(container.querySelector('#launcher-role option[value="system-specialist"]')).toBeNull();
    fireEvent.click(getByRole("button", { name: "Retry role launch" }));
    await waitFor(() => expect(dispatches).toEqual([{ requestId: "original-specialist-request", action: "start", role: "system-specialist" }]));
  });

  it("marks an open Projects role as running and does not offer a second start in the bound row", async () => {
    sessionStorage.clear();
    const execution = { requestId: "00000000-0000-4000-8000-000000000001", status: "running", canStart: true, canRevive: false, execution: { kind: "paseo-agent", agentId: "architect", workspaceId: "projects" } };
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const value = url.endsWith("/frame") ? { available: true, frameBaseUrl: "https://host.example", serverId: "server", projectsWorkspaceId: "projects" }
        : url.endsWith("/document-chats") ? { agents: [architect] }
        : url.endsWith("/result") ? execution : { roleDefaults: { agent: "codex", available: true }, agents: [{ id: "codex", label: "Codex", models: [] }], executions: [execution] };
      return new Response(JSON.stringify(value));
    }));
    const { getByText, getByRole } = render(<DocumentChat active taskDocuments={[sprint, master, leaf]} series={chatSeries} />);
    await waitFor(() => expect(getByText("Launch role")).not.toBeNull());
    fireEvent.click(getByText("Launch role"));
    await waitFor(() => expect((getByRole("combobox", { name: "Role agent override" }) as HTMLSelectElement).disabled).toBe(false));
    expect((getByRole("button", { name: "Start role" }) as HTMLButtonElement).disabled).toBe(true);
    expect(getByRole("combobox", { name: "Role" }).textContent).toContain("Architect (running)");
    const pending = { requestId: "uncertain-original", pending: true };
    expect(boundTasklessRequest([execution], pending)).toBe(pending);
  });

  it("offers the bound leaf row or Projects row with no agents and names unsupported selections", async () => {
    const requests: object[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/options")) requests.push(JSON.parse(String(init?.body)));
      const value = url.endsWith("/frame") ? { available: true, frameBaseUrl: "https://host.example", serverId: "server", projectsWorkspaceId: "projects" }
        : url.endsWith("/document-chats") ? { agents: [] } : { roleDefaults: {}, agents: [] };
      return new Response(JSON.stringify(value));
    }));
    const { container, rerender, findByTestId, getByRole, queryByRole } = render(<DocumentChat active taskDocumentRef={ref("master/leaf.json")} taskDocuments={[sprint, master, leaf]} series={chatSeries} />);
    await findByTestId("role-launcher");
    expect([...container.querySelectorAll("#launcher-role option")].map((item) => item.textContent)).toEqual(["Worker", "Reviewer", "Curator"]);
    await waitFor(() => expect(requests).toContainEqual({ role: "worker", sprintDocumentRef: ref("sprint/task.json"), masterDocumentRef: ref("master/task.json"), taskDocumentRef: ref("master/leaf.json") }));
    rerender(<DocumentChat active taskDocuments={[sprint, master, leaf]} series={chatSeries} />);
    await waitFor(() => expect([...container.querySelectorAll("#launcher-role option")].map((item) => item.textContent)).toEqual(["Architect", "Investigator"]));
    rerender(<DocumentChat active taskDocumentRef={ref("orphan/task.json")} taskDocuments={[sprint, master, leaf]} series={chatSeries} />);
    expect(getByRole("status").textContent).toContain("commanding sprint document");
    expect(queryByRole("button", { name: "Start role" })).toBeNull();
  });

  it("admits exactly the roles for Projects, sprint, commanded master and leaf", () => {
    const docs = [sprint, master, leaf];
    expect(documentChatBinding(undefined, docs, chatSeries).roles).toEqual(["architect", "investigator"]);
    expect(documentChatBinding(ref("sprint/task.json"), docs, chatSeries).roles).toEqual(["orchestrator"]);
    expect(documentChatBinding(ref("master/task.json"), docs, chatSeries).roles).toEqual(["manager"]);
    expect(documentChatBinding(ref("master/leaf.json"), docs, chatSeries).roles).toEqual(["worker", "reviewer", "curator"]);
    expect(documentChatBinding(ref("orphan/task.json"), docs, chatSeries).problem).toContain("commanding sprint");
    expect(documentChatAgent(undefined, [architect, { ...architect, agentId: "latest", createdAt: "2026-01-03" }, { ...architect, agentId: "archived", archivedAt: "today", createdAt: "2026-01-04" }])?.agentId).toBe("latest");
  });

  it("uses the trusted host snapshot, moves by message and exposes the launcher after archival", async () => {
    const fetcher = vi.fn(async (url: string) => new Response(JSON.stringify(url.endsWith("/document-chats") ? { agents: [architect, worker] } : url.endsWith("/frame") ? {
      available: true, frameBaseUrl: "https://host.example", serverId: "server", projectsWorkspaceId: "projects",
    } : { roleDefaults: {}, agents: [] })));
    vi.stubGlobal("fetch", fetcher);
    const { container, rerender, getByText, queryByTestId } = render(<DocumentChat active taskDocuments={[sprint, master, leaf]} series={chatSeries} />);
    await waitFor(() => expect(container.querySelector("iframe")).not.toBeNull());
    const frame = container.querySelector("iframe")!;
    const posts = vi.spyOn(frame.contentWindow!, "postMessage");
    const send = (data: object, origin = "https://host.example") => act(() => {
      window.dispatchEvent(new MessageEvent("message", { origin, source: frame.contentWindow, data: { source: "ar-plugin", ...data } }));
    });
    send({ type: "hierarchy", agents: [architect, worker] }, "https://untrusted.example");
    expect(queryByTestId("role-launcher")).toBeNull();
    send({ type: "ready" });
    await waitFor(() => expect(posts).toHaveBeenCalledWith({ type: "ar.open", agentId: "architect", workspaceId: "projects" }, "https://host.example"));
    expect(posts).toHaveBeenCalledWith({ type: "ar.open", agentId: "architect", workspaceId: "projects" }, "https://host.example");
    send({ type: "shown", agentId: "architect" });
    rerender(<DocumentChat active taskDocumentRef={ref("master/leaf.json")} taskDocuments={[sprint, master, leaf]} series={chatSeries} />);
    expect(container.querySelector("iframe")).toBe(frame);
    await waitFor(() => expect(posts).toHaveBeenCalledWith({ type: "ar.open", agentId: "worker", workspaceId: "leaf-workspace" }, "https://host.example"));
    fireEvent.click(getByText("Launch role"));
    await waitFor(() => expect(queryByTestId("role-launcher")).not.toBeNull());
    expect(container.querySelector('[aria-label="AR task"]')).toBeNull();
    expect(container.querySelector('[aria-label="AR master"]')).toBeNull();
    expect(container.querySelector('[aria-label="AR sprint"]')).toBeNull();
    expect(queryByTestId("role-launcher")).not.toBeNull();
  });
});
