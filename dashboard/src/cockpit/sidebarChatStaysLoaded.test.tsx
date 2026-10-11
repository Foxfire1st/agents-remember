// MIK-R122: the sidebar chat stays loaded and switches (one instance, message switches).
import { act, cleanup, render, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RightRail } from "./Cockpit";
import { chatLeaf as leaf, chatMaster as master, chatSeries, chatSprint as sprint } from "../test/fixtures/documentChat";
import type { DocumentAgent } from "./document-chat/model";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const agent = (agentId: string, workspaceId: string, createdAt: string, labels: Record<string, string>): DocumentAgent => ({
  agentId, workspaceId, archivedAt: null, createdAt, labels,
});
const agentsFor = (role: string): DocumentAgent[] => {
  if (role === "architect") return [agent("a-arch", "wks_projects", "2026-01-01", { "ar.role": "architect" })];
  if (role === "manager") return [agent("a-mgr", "wks_projects", "2026-01-02", { "ar.role": "manager", "ar.master-ref": "repo/master/task.json" })];
  return [agent("a-wrk", "wks_leaf", "2026-01-03", { "ar.role": "worker", "ar.task-ref": "repo/master/leaf.json" })];
};

function stubBackend(): void {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const body = init?.body ? JSON.parse(String(init.body)) as { role?: string } : {};
    const value = String(url).endsWith("/frame")
      ? { available: true, frameBaseUrl: "https://host.example", serverId: "s", projectsWorkspaceId: "wks_projects" }
      : String(url).endsWith("/document-chats") ? { agents: agentsFor(body.role ?? "architect") }
      : { roleDefaults: {}, agents: [], executions: [] };
    return new Response(JSON.stringify(value));
  }));
}

const REFS: Record<string, { repository: string; path: string } | undefined> = {
  projects: undefined,
  master: { repository: "repo", path: "master/task.json" },
  leaf: { repository: "repo", path: "master/leaf.json" },
  ghost: { repository: "repo", path: "master/ghost.json" },
};

function Rig() {
  const [doc, setDoc] = useState("projects");
  const [railView, setRailView] = useState<"river" | "chat">("chat");
  return (
    <div>
      {(Object.keys(REFS) as Array<keyof typeof REFS>).map((d) => (
        <button key={d} type="button" data-testid={`doc-${d}`} onClick={() => setDoc(d)}>{d}</button>
      ))}
      <button type="button" data-testid="go-river" onClick={() => setRailView("river")}>river</button>
      <button type="button" data-testid="go-chat" onClick={() => setRailView("chat")}>chat</button>
      <RightRail
        fullBleed={false} railEnter={{}} railView={railView} rightRailWidth={360}
        setRailView={setRailView}
        viewedTask={REFS[doc] ? { taskDocumentRef: REFS[doc]! } : undefined}
        taskDocuments={[sprint, master, leaf]} series={chatSeries} onResize={() => {}} />
    </div>
  );
}

async function readyFrame(container: HTMLElement): Promise<{ frame: HTMLIFrameElement; posts: ReturnType<typeof vi.fn> }> {
  await waitFor(() => expect(container.querySelector("iframe[title='Role chats']")).not.toBeNull());
  const frame = container.querySelector("iframe[title='Role chats']") as HTMLIFrameElement;
  const posts = vi.fn();
  if (frame.contentWindow) frame.contentWindow.postMessage = posts as unknown as Window["postMessage"];
  act(() => window.dispatchEvent(new MessageEvent("message", {
    origin: "https://host.example", source: frame.contentWindow,
    data: { source: "ar-plugin", type: "ready" },
  })));
  return { frame, posts };
}

const generation = (container: HTMLElement): string | null =>
  container.querySelector('[data-testid="paseo-frame"]')?.getAttribute("data-generation") ?? null;

describe("MIK-R122: the sidebar chat stays loaded and switches", () => {
  it("keeps the same frame element across the river toggle with the load count unchanged", async () => {
    stubBackend();
    const ui = render(<Rig />);
    const { frame } = await readyFrame(ui.container);
    const gen = generation(ui.container);
    ui.getByTestId("go-river").click();
    await waitFor(() => expect(ui.getByTestId("rail-chat-keepalive").getAttribute("aria-hidden")).toBe("true"));
    expect(ui.container.querySelector("iframe[title='Role chats']")).toBe(frame);
    ui.getByTestId("go-chat").click();
    await waitFor(() => expect(ui.getByTestId("rail-chat-keepalive").getAttribute("aria-hidden")).toBe("false"));
    expect(ui.container.querySelector("iframe[title='Role chats']")).toBe(frame);
    expect(generation(ui.container)).toBe(gen);
  });

  it("switches the selection by message without loading again", async () => {
    stubBackend();
    const ui = render(<Rig />);
    const { frame, posts } = await readyFrame(ui.container);
    const gen = generation(ui.container);
    ui.getByTestId("doc-leaf").click();
    await waitFor(() => expect(posts).toHaveBeenCalledWith(
      { type: "ar.open", agentId: "a-wrk", workspaceId: "wks_leaf" }, "https://host.example"));
    expect(ui.container.querySelector("iframe[title='Role chats']")).toBe(frame);
    expect(generation(ui.container)).toBe(gen);
    ui.getByTestId("doc-master").click();
    await waitFor(() => expect(posts).toHaveBeenCalledWith(
      { type: "ar.open", agentId: "a-mgr", workspaceId: "wks_projects" }, "https://host.example"));
    expect(ui.container.querySelector("iframe[title='Role chats']")).toBe(frame);
    expect(generation(ui.container)).toBe(gen);
  });

  it("hides the frame without unloading it where the selection has no agent, and switches back", async () => {
    stubBackend();
    const ui = render(<Rig />);
    const { frame } = await readyFrame(ui.container);
    const gen = generation(ui.container);
    ui.getByTestId("doc-ghost").click();
    await waitFor(() => expect(ui.getByText(/No role can start here/)).not.toBeNull());
    const hidden = ui.container.querySelector("iframe[title='Role chats']");
    expect(hidden).toBe(frame);
    expect((hidden as HTMLIFrameElement).closest("div[aria-hidden='true']")).not.toBeNull();
    expect(generation(ui.container)).toBe(gen);
    ui.getByTestId("doc-leaf").click();
    await waitFor(() => expect(
      ui.container.querySelector("iframe[title='Role chats']")?.closest("div[aria-hidden='true']"),
    ).toBeNull());
    expect(ui.container.querySelector("iframe[title='Role chats']")).toBe(frame);
  });

  it("loads nothing for the selections in between when they change faster than the channel answers", async () => {
    stubBackend();
    const ui = render(<Rig />);
    await waitFor(() => expect(ui.container.querySelector("iframe[title='Role chats']")).not.toBeNull());
    const frame = ui.container.querySelector("iframe[title='Role chats']") as HTMLIFrameElement;
    const posts = vi.fn();
    if (frame.contentWindow) frame.contentWindow.postMessage = posts as unknown as Window["postMessage"];
    const gen = generation(ui.container);
    // Burst of selections while the application has not reported ready: nothing may load.
    ui.getByTestId("doc-leaf").click();
    ui.getByTestId("doc-master").click();
    ui.getByTestId("doc-projects").click();
    expect(generation(ui.container)).toBe(gen);
    expect(posts).not.toHaveBeenCalled();
    // The ready report brings only the latest selection forward, by message, still no load.
    act(() => window.dispatchEvent(new MessageEvent("message", {
      origin: "https://host.example", source: frame.contentWindow,
      data: { source: "ar-plugin", type: "ready" },
    })));
    await waitFor(() => expect(
      posts.mock.calls.filter(([message]) => (message as { type: string }).type === "ar.open"),
    ).toHaveLength(1));
    const opens = posts.mock.calls.filter(([message]) => (message as { type: string }).type === "ar.open");
    expect(opens).toHaveLength(1);
    expect(posts).toHaveBeenCalledWith(
      { type: "ar.open", agentId: "a-arch", workspaceId: "wks_projects" }, "https://host.example");
    expect(ui.container.querySelector("iframe[title='Role chats']")).toBe(frame);
    expect(generation(ui.container)).toBe(gen);
  });

  it("keeps the hidden chat out of reach: no focus stays inside it", async () => {
    stubBackend();
    const ui = render(<Rig />);
    await readyFrame(ui.container);
    const toggle = ui.getByTestId("go-river");
    (ui.getByTestId("go-chat") as HTMLButtonElement).focus();
    expect(document.activeElement).toBe(ui.getByTestId("go-chat"));
    toggle.focus();
    ui.getByTestId("go-river").click();
    await waitFor(() => expect(ui.getByTestId("rail-chat-keepalive").getAttribute("aria-hidden")).toBe("true"));
    const keepalive = ui.getByTestId("rail-chat-keepalive");
    expect(document.activeElement === toggle || !keepalive.contains(document.activeElement)).toBe(true);
    expect(keepalive.getAttribute("aria-hidden")).toBe("true");
  });
});
