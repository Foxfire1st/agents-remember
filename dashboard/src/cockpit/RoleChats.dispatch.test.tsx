// Dispatch through the chat pane (MIK-R150 rule 5): sending a start, a refused start, and the
// end-of-turn result refresh — plus when the options effect runs (rule 4). Written before the cut;
// they pass on the base bytes and must pass unchanged after it.
import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RoleChatsPane } from "./RoleChats";
import type { RoleAgentChoice } from "./roleLaunchModel";

const AGENTS: RoleAgentChoice[] = [
  {
    id: "codex",
    label: "Codex",
    models: [
      { id: "gpt-a", label: "GPT A", efforts: [], isDefault: true, serviceTiers: [{ id: "default", label: "Normal" }, { id: "priority", label: "Fast" }], features: [
        { id: "service_tier", label: "Speed", type: "select", value: "default", permissionBearing: false, options: [{ id: "priority", label: "Fast" }] },
      ] },
    ],
  },
];

function optionsBody() {
  return {
    roleDefaults: { agent: "codex", model: "gpt-a", available: true },
    agents: AGENTS,
    catalogOrigin: "paseo:1",
    executions: [],
  };
}

type Route = { status: number; body: unknown };
const routes: Record<string, Route[]> = {};
const seen: { url: string; body: unknown }[] = [];

function reply(url: string, status: number, body: unknown) {
  (routes[url] ??= []).push({ status, body });
}

function receiptBody(body: unknown, over: Record<string, unknown> = {}) {
  const request = (body ?? {}) as Record<string, unknown>;
  return {
    status: "completed",
    requestId: request["requestId"],
    canStart: false,
    canRevive: false,
    ...over,
  };
}

function take(url: string): Route {
  const queue = routes[url] ?? [];
  return queue.length > 1 ? queue.shift()! : queue[0]!;
}

function bodies(url: string): unknown[] {
  return seen.filter((call) => call.url === url).map((call) => call.body);
}

beforeEach(() => {
  for (const key of Object.keys(routes)) delete routes[key];
  seen.length = 0;
  sessionStorage.clear();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      seen.push({ url, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      if (url === "/api/role-launch/options") {
        const next = take(url);
        return new Response(JSON.stringify(next?.body ?? {}), { status: next?.status ?? 500 });
      }
      if (url === "/api/role-launch/dispatch" || url === "/api/role-launch/result") {
        const next = take(url);
        const requestBody = init?.body ? JSON.parse(String(init.body)) : undefined;
        const body = typeof next?.body === "function"
          ? (next.body as (request: unknown) => unknown)(requestBody)
          : next?.body;
        return new Response(JSON.stringify(body ?? {}), { status: next?.status ?? 404 });
      }
      if (url === "/api/role-launch/report") {
        const next = take(url);
        return new Response(JSON.stringify(next?.body ?? {}), { status: next?.status ?? 404 });
      }
      return new Response(JSON.stringify({ available: false, frameUrl: null }), { status: 503 });
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderPane() {
  return render(<RoleChatsPane active taskDocuments={[]} series={[]} />);
}

async function startEnabled(view: ReturnType<typeof render>) {
  const start = view.getByRole("button", { name: "Start role" }) as HTMLButtonElement;
  await waitFor(() => expect(start.disabled).toBe(false));
  return start;
}

describe("sending a role start through the chat pane", () => {
  it("posts one start dispatch and offers the result of the returned receipt", async () => {
    reply("/api/role-launch/options", 200, optionsBody());
    const view = renderPane();
    const start = await startEnabled(view);
    reply("/api/role-launch/dispatch", 200, (request: unknown) => receiptBody(request));
    reply("/api/role-launch/result", 200, (request: unknown) => receiptBody(request));
    fireEvent.click(start);
    await waitFor(() => expect(bodies("/api/role-launch/dispatch")).toHaveLength(1));
    const dispatch = bodies("/api/role-launch/dispatch")[0] as Record<string, unknown>;
    expect(dispatch["action"]).toBe("start");
    expect(dispatch["role"]).toBe("architect");
    expect(typeof dispatch["requestId"]).toBe("string");
    // The end of the dispatch leaves the run settled: Start is back and Result is offered.
    await waitFor(() => expect(view.queryByRole("button", { name: "Refresh role result" })).not.toBeNull());
    await waitFor(() =>
      expect((view.getByRole("button", { name: "Start role" }) as HTMLButtonElement).disabled).toBe(false),
    );
  });

  it("keeps a refused start as a sticky error and offers the saved request again", async () => {
    reply("/api/role-launch/options", 200, optionsBody());
    const view = renderPane();
    const start = await startEnabled(view);
    reply("/api/role-launch/dispatch", 409, { detail: "another launch holds the lock" });
    reply("/api/role-launch/result", 404, {});
    fireEvent.click(start);
    const error = await view.findByTestId("role-launch-error");
    expect(error.textContent).toContain("not accepted (HTTP 409)");
    expect(error.textContent).toContain("another launch holds the lock");
    // The saved request survives the refusal: its retry is offered.
    await waitFor(() => expect(view.queryByRole("button", { name: "Retry role launch" })).not.toBeNull());
  });

  it("reads the result at the end of a turn and opens its report", async () => {
    reply("/api/role-launch/options", 200, optionsBody());
    const view = renderPane();
    const start = await startEnabled(view);
    reply("/api/role-launch/dispatch", 200, (request: unknown) => receiptBody(request));
    reply("/api/role-launch/result", 200, (request: unknown) => receiptBody(request));
    fireEvent.click(start);
    fireEvent.click(await view.findByRole("button", { name: "Refresh role result" }));
    reply("/api/role-launch/report", 200, {
      path: "req-turn.md",
      language: "markdown",
      size: 20,
      truncated: false,
      content: "The **turn** ended.",
    });
    await waitFor(() => expect(bodies("/api/role-launch/report")).toHaveLength(1));
    expect(await view.findByTestId("notes-reader-viewer")).toBeTruthy();
    expect((await view.findByText("turn")).tagName.toLowerCase()).toBe("strong");
  });
});

describe("when the options effect runs", () => {
  it("loads on mount, reloads when the selection changes, and not on an unrelated rerender", async () => {
    reply("/api/role-launch/options", 200, optionsBody());
    const view = renderPane();
    await startEnabled(view);
    expect(bodies("/api/role-launch/options")).toEqual([{ role: "architect" }]);
    // The scope memos hold across a rerender with the same selection: no second load.
    view.rerender(<RoleChatsPane active taskDocuments={[]} series={[]} />);
    await waitFor(() => expect(view.getByRole("button", { name: "Start role" })).toBeTruthy());
    // Flush effects and settled fetches deterministically instead of a wall-clock sleep.
    await act(async () => {});
    expect(bodies("/api/role-launch/options")).toHaveLength(1);
    // A new selection recomputes the scope memos, so the effect loads its scope exactly once.
    fireEvent.change(view.getByLabelText("Role"), { target: { value: "investigator" } });
    await waitFor(() => expect(bodies("/api/role-launch/options")).toHaveLength(2));
    expect(bodies("/api/role-launch/options")[1]).toEqual({ role: "investigator" });
  });

  it("does not reload when only a feature choice changes, keeping Start offered", async () => {
    reply("/api/role-launch/options", 200, optionsBody());
    const view = renderPane();
    const start = await startEnabled(view);
    expect(start.disabled).toBe(false);
    // A feature-only change leaves the scope memos (and the options load) alone.
    fireEvent.change(view.getByLabelText("Speed override"), { target: { value: "priority" } });
    // Flush effects and settled fetches deterministically instead of a wall-clock sleep.
    await act(async () => {});
    expect(bodies("/api/role-launch/options")).toHaveLength(1);
    expect((view.getByRole("button", { name: "Start role" }) as HTMLButtonElement).disabled).toBe(false);
  });
});

describe("L150-CODE-04: newer Result authority survives a delayed options reply", () => {
  it("keeps the Result offered when an older options execution settles later", async () => {
    reply("/api/role-launch/options", 200, optionsBody());
    const view = renderPane();
    const start = await startEnabled(view);
    // Park the options load the dispatch's end triggers; everything else answers at once.
    const parked: { answer: (value: Response) => void }[] = [];
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      seen.push({ url, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      if (url === "/api/role-launch/options") {
        return new Promise<Response>((answer) => {
          parked.push({ answer });
        });
      }
      const next = take(url);
      const requestBody = init?.body ? JSON.parse(String(init.body)) : undefined;
      const body = typeof next?.body === "function"
        ? (next.body as (request: unknown) => unknown)(requestBody)
        : next?.body;
      return new Response(JSON.stringify(body ?? {}), { status: next?.status ?? 404 });
    });
    reply("/api/role-launch/dispatch", 200, (request: unknown) => receiptBody(request));
    reply("/api/role-launch/result", 200, (request: unknown) => receiptBody(request));
    fireEvent.click(start);
    // The dispatch completes first: its receipt is the active authority.
    await waitFor(() => expect(view.queryByRole("button", { name: "Refresh role result" })).not.toBeNull());
    await waitFor(() => expect(parked).toHaveLength(1));
    // The older options reply settles later with a foreign execution: authority stays.
    await act(async () => {
      parked.splice(0)[0].answer(
        new Response(
          JSON.stringify({ ...optionsBody(), execution: { status: "completed", requestId: "options-old-id" } }),
          { status: 200 },
        ),
      );
    });
    // Flush effects and settled fetches deterministically instead of a wall-clock sleep.
    await act(async () => {});
    expect(view.queryByRole("button", { name: "Refresh role result" })).not.toBeNull();
    expect(view.queryByTestId("role-launch-error")).toBeNull();
  });
});
