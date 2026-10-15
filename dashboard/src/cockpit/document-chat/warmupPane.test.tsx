// L81-R1-F28 mounted pictures: the document pane renders the shown agent's own
// warmup truth -- pending/in flight, failed with the host's words, not loaded
// with the run's reason -- from the shared record, not a fixed line.
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { chatSprint as sprint, chatMaster as master, chatLeaf as leaf, chatSeries } from "../../test/fixtures/documentChat";
import { DocumentChat } from "./DocumentChat";
import type { DocumentAgent } from "./model";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const architect: DocumentAgent = { agentId: "architect", workspaceId: "projects", archivedAt: null, createdAt: "2026-01-01", labels: { "ar.role": "architect" } };

function serve(warmup: unknown) {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const value = url.endsWith("/api/role-launch/warmup") ? warmup
      : url.endsWith("/frame") ? { available: true, frameBaseUrl: "https://host.example", serverId: "server", projectsWorkspaceId: "projects" }
      : url.endsWith("/document-chats") ? { agents: [architect] }
      : { roleDefaults: {}, agents: [], executions: [] };
    return new Response(JSON.stringify(value));
  }));
}

const running = (agents: Record<string, { state: string; reason: string | null }>, line = "loading agents after the host's start: 0 of 1") => ({
  running: true, total: 1, finished: 0, line, pending: [], inFlight: [], agents, stoppedForMemory: false,
});

describe("the document pane's warmup truth", () => {
  it("says an in-flight agent is being loaded now", async () => {
    serve(running({ architect: { state: "loading", reason: null } }));
    const { findByTestId } = render(<DocumentChat active taskDocuments={[sprint, master, leaf]} series={chatSeries} />);
    const pane = await findByTestId("host-warmup-agent");
    expect(pane.getAttribute("data-warmup-state")).toBe("loading");
    expect(pane.textContent).toContain("being loaded now");
  });

  it("names a failed agent with the host's words", async () => {
    serve(running({ architect: { state: "failed", reason: "the host flaked mid-resume" } }));
    const { findByTestId } = render(<DocumentChat active taskDocuments={[sprint, master, leaf]} series={chatSeries} />);
    const pane = await findByTestId("host-warmup-agent");
    expect(pane.getAttribute("data-warmup-state")).toBe("failed");
    expect(pane.textContent).toContain("Loading this agent failed: the host flaked mid-resume.");
  });

  it("shows a pending agent's recorded memory reason without calling it loading", async () => {
    serve({ ...running({ architect: { state: "pending", reason: "available memory is below6 GiB; waiting without starting a load" } }), stoppedForMemory: true });
    const { findByTestId } = render(<DocumentChat active taskDocuments={[sprint, master, leaf]} series={chatSeries} />);
    const pane = await findByTestId("host-warmup-agent");
    expect(pane.getAttribute("data-warmup-state")).toBe("pending");
    expect(pane.textContent).toBe("This agent is waiting to load: available memory is below6 GiB; waiting without starting a load.");
  });

  it("names a memory-skipped agent with the run's reason", async () => {
    serve(running({ architect: { state: "skipped", reason: "paused for memory: 1 of 1" } }));
    const { findByTestId } = render(<DocumentChat active taskDocuments={[sprint, master, leaf]} series={chatSeries} />);
    const pane = await findByTestId("host-warmup-agent");
    expect(pane.getAttribute("data-warmup-state")).toBe("skipped");
    expect(pane.textContent).toContain("was not loaded: paused for memory: 1 of 1.");
  });

  it("says nothing for a loaded agent", async () => {
    serve(running({ architect: { state: "loaded", reason: null } }));
    const { queryByTestId, findByTestId } = render(<DocumentChat active taskDocuments={[sprint, master, leaf]} series={chatSeries} />);
    // The warmup line proves the read resolved; a loaded agent still says nothing.
    await findByTestId("host-warmup-line");
    expect(queryByTestId("host-warmup-agent")).toBeNull();
  });

  it("falls back to the pending list for older builds", async () => {
    serve({ running: true, line: "loading agents after the host's start: 0 of 1", pending: ["architect"] });
    const { findByTestId } = render(<DocumentChat active taskDocuments={[sprint, master, leaf]} series={chatSeries} />);
    const pane = await findByTestId("host-warmup-agent");
    expect(pane.getAttribute("data-warmup-state")).toBe("pending");
    expect(pane.textContent).toBe("This agent is waiting to load.");
  });
});
