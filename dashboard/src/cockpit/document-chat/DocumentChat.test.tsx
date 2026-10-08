import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { chatSprint as sprint, chatMaster as master, chatLeaf as leaf, chatSeries } from "../../test/fixtures/documentChat";
import { DocumentChat } from "./DocumentChat";
import { boundTasklessRequest } from "../roleLaunchModel";
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
    fireEvent.change(getByRole("combobox", { name: "Role" }), { target: { value: "system-specialist" } });
    await waitFor(() => expect((getByRole("button", { name: "Start role" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(getByRole("button", { name: "Start role" }));
    await waitFor(() => expect(posts).toHaveBeenCalledWith({ type: "ar.open", agentId: "specialist", workspaceId: "projects" }, "https://host.example"));
    expect(container.querySelector("iframe")).toBe(frame);
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
    await waitFor(() => expect([...container.querySelectorAll("#launcher-role option")].map((item) => item.textContent)).toEqual(["Architect", "System specialist"]));
    rerender(<DocumentChat active taskDocumentRef={ref("orphan/task.json")} taskDocuments={[sprint, master, leaf]} series={chatSeries} />);
    expect(getByRole("status").textContent).toContain("commanding sprint document");
    expect(queryByRole("button", { name: "Start role" })).toBeNull();
  });

  it("admits exactly the roles for Projects, sprint, commanded master and leaf", () => {
    const docs = [sprint, master, leaf];
    expect(documentChatBinding(undefined, docs, chatSeries).roles).toEqual(["architect", "system-specialist"]);
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
