// The launcher bar against the host catalog (PNT-R02): an unoffered role default disables Start
// until an offered value is picked, a failed model listing leaves its agent launchable, and an
// options error is shown with a Refresh control that repeats the call.
import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RoleChatsPane } from "./RoleChats";
import { launchChoiceProblem, type RoleAgentChoice, type RoleDefaults } from "./roleLaunchModel";


const AGENTS: RoleAgentChoice[] = [
  {
    id: "codex",
    label: "Codex",
    models: [
      { id: "gpt-a", label: "GPT A", efforts: [{ id: "low", label: "Low" }, { id: "high", label: "High" }], defaultEffort: "low", isDefault: true, serviceTiers: [{ id: "default", label: "Normal" }, { id: "priority", label: "Fast" }] },
      { id: "gpt-b", label: "GPT B", efforts: [] },
    ],
  },
  { id: "eve", label: "Eve", models: [] },
  { id: "pi", label: "Pi", models: [], listingError: "auth missing" },
];

type Reply = { status: number; body: unknown };
let optionsReplies: Reply[] = [];
let optionsRequests: Record<string, unknown>[] = [];
let optionsBarrier: Promise<void> | null = null;

function optionsReply(roleDefaults: Record<string, unknown>): Reply {
  return { status: 200, body: { roleDefaults, agents: AGENTS, catalogOrigin: "paseo:1", executions: [] } };
}

function renderLauncher() {
  return render(
    <RoleChatsPane active taskDocuments={[]} series={[]} />,
  );
}

beforeEach(() => {
  optionsReplies = [];
  optionsRequests = [];
  optionsBarrier = null;
  sessionStorage.clear();
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (url !== "/api/role-launch/options") return new Response(JSON.stringify({ available: false, frameUrl: null }), { status: 503 });
    optionsRequests.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
    if (optionsBarrier) await optionsBarrier;
    const reply = optionsReplies.length > 1 ? optionsReplies.shift() : optionsReplies[0];
    return new Response(JSON.stringify(reply?.body), { status: reply?.status ?? 500 });
  }));
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("launcher bar and the host catalog", () => {
  it("displays the configured tier, revalidates a model override and discloses other-agent defaults", async () => {
    optionsReplies = [optionsReply({ agent: "codex", model: "gpt-a", effort: "high", serviceTier: "fast", available: true })];
    const { getByRole, getByLabelText, getByTestId, queryByTestId } = renderLauncher();
    const start = getByRole("button", { name: "Start role" }) as HTMLButtonElement;
    await waitFor(() => expect(start.disabled).toBe(false));
    expect(getByTestId("role-service-tier").textContent).toBe("Speed: Fast");
    expect(getByTestId("role-service-tier").getAttribute("title")).toContain("default agent");
    fireEvent.change(getByLabelText("Role model override"), { target: { value: "gpt-b" } });
    await waitFor(() => expect(start.disabled).toBe(true));
    expect(getByTestId("role-choice-problem").textContent).toContain("Service tier fast is not offered");
    fireEvent.change(getByLabelText("Role agent override"), { target: { value: "eve" } });
    await waitFor(() => expect(start.disabled).toBe(false));
    expect(getByTestId("role-service-tier").textContent).toBe("Speed: provider default");
    expect(queryByTestId("role-choice-problem")).toBeNull();
  });

  it("disables Start for an unoffered role default until an offered value is picked", async () => {
    optionsReplies = [optionsReply({ agent: "codex", model: "gpt-z", effort: null, available: false })];
    const { getByRole, getByLabelText, findByTestId, queryByTestId, queryByRole } = renderLauncher();

    const problem = await findByTestId("role-choice-problem");
    expect(problem.textContent).toContain("Role default codex · gpt-z is not offered by the Paseo runtime");
    const start = getByRole("button", { name: "Start role" }) as HTMLButtonElement;
    expect(start.disabled).toBe(true);
    expect(optionsRequests).toEqual([{ role: "architect" }]);
    const unavailable = await findByTestId("paseo-frame-unavailable");
    expect(queryByRole("button", { name: /chat navigation/ })).toBeNull();
    expect(getByLabelText("Role").closest('[data-testid="role-launcher"]')?.querySelector("button,select,input")).toBe(getByLabelText("Role"));
    expect(await findByTestId("paseo-frame-unavailable")).toBe(unavailable);
    expect(optionsRequests).toEqual([{ role: "architect" }]);
    expect(start.disabled).toBe(true);

    // Naming the role's own agent keeps the role's unoffered model, so Start stays disabled.
    fireEvent.change(getByLabelText("Role agent override"), { target: { value: "codex" } });
    await waitFor(() => expect(optionsRequests.length).toBe(2));
    await waitFor(() => expect(queryByTestId("role-choice-problem")?.textContent).toContain("Model gpt-z is not offered for Codex"));
    expect(start.disabled).toBe(true);

    fireEvent.change(getByLabelText("Role model override"), { target: { value: "gpt-a" } });
    await waitFor(() => expect(start.disabled).toBe(false));
    expect(queryByTestId("role-choice-problem")).toBeNull();
  });

  it("keeps an agent whose models could not be listed selectable without a model", async () => {
    optionsReplies = [optionsReply({ agent: "pi", model: null, effort: null, available: true })];
    const { getByRole, getByLabelText, findByTestId } = renderLauncher();

    const problem = await findByTestId("role-choice-problem");
    expect(problem.textContent).toBe("Models of Pi could not be listed: auth missing");
    expect((getByLabelText("Role model override") as HTMLSelectElement).disabled).toBe(true);
    expect((getByRole("button", { name: "Start role" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("shows an options error and repeats the call from Refresh", async () => {
    optionsReplies = [
      { status: 503, body: { detail: "no Paseo runtime configured: /settings/mcp.json has no paseoRuntime block" } },
      optionsReply({ agent: "codex", model: null, effort: null, available: true }),
    ];
    const { getByRole, findByRole, queryByRole } = renderLauncher();

    const alert = await findByRole("alert");
    expect(alert.textContent).toContain("(HTTP 503): no Paseo runtime configured: /settings/mcp.json has no paseoRuntime block");
    expect((getByRole("button", { name: "Start role" }) as HTMLButtonElement).disabled).toBe(true);
    const refresh = getByRole("button", { name: "Refresh role agents" }) as HTMLButtonElement;
    expect(refresh.disabled).toBe(false);

    let release!: () => void;
    optionsBarrier = new Promise<void>((resolve) => { release = resolve; });
    fireEvent.click(refresh);
    await waitFor(() => expect(refresh.disabled).toBe(true));
    expect(queryByRole("alert")?.textContent).toContain("no Paseo runtime configured");
    release();
    await waitFor(() => expect(optionsRequests).toEqual([{ role: "architect" }, { role: "architect", refreshCatalog: true }]));
    await waitFor(() => expect(queryByRole("alert")).toBeNull());
    expect((getByRole("button", { name: "Start role" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("rereads a launch in progress without an ordinary status line", async () => {
    vi.useFakeTimers();
    // The backend's launch lock is held by a launch: the options route marks its refusal.
    optionsReplies = [
      { status: 409, body: { detail: "A role launch or result check is already in progress.", launchInProgress: true } },
      optionsReply({ agent: "codex", model: null, effort: null, available: true }),
    ];
    const { getByRole, getByTestId, queryByRole } = renderLauncher();

    await act(async () => {});
    expect(optionsRequests.length).toBe(1);
    expect(getByTestId("role-launcher").querySelector('[role="status"]')).toBeNull();
    expect(queryByRole("alert")).toBeNull();
    expect(optionsRequests).toEqual([{ role: "architect" }]);
    expect((getByRole("button", { name: "Start role" }) as HTMLButtonElement).disabled).toBe(true);

    // No click: the launcher reads again by itself and restores Start when the read succeeds.
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    expect(optionsRequests.length).toBe(2);
    expect(getByTestId("role-launcher").querySelector('[role="status"],[role="alert"]')).toBeNull();
    expect(queryByRole("alert")).toBeNull();
    expect((getByRole("button", { name: "Start role" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("keeps showing another refusal of the options route as an error", async () => {
    optionsReplies = [{ status: 409, body: { detail: "The selected Projects repository is not admitted by MCP settings." } }];
    const { findByRole, getByTestId } = renderLauncher();

    expect((await findByRole("alert")).textContent).toContain("(HTTP 409): The selected Projects repository is not admitted");
    expect(getByTestId("role-launcher").querySelector('[role="status"]')).toBeNull();
    expect(optionsRequests.length).toBe(1);
  });
});

describe("launchChoiceProblem", () => {
  const role: RoleDefaults = { agent: "codex", model: "gpt-a", effort: "high", available: true };

  it("mirrors the backend's launch validation", () => {
    const unset = null as unknown as undefined; // the backend sends an unset default as null
    const offered: [RoleDefaults, Parameters<typeof launchChoiceProblem>[2]][] = [
      [role, undefined],
      [{ agent: "codex" }, undefined],
      [role, { agentId: "codex", modelId: "gpt-b" }],
      [role, { agentId: "codex", effortId: "low" }],
      [role, { agentId: "eve" }],
      [{ agent: "claude", model: "opus", available: false }, { agentId: "eve" }],
      [{ agent: "codex", model: unset, effort: unset, available: true }, { agentId: "codex" }],
    ];
    for (const [defaults, override] of offered) expect(launchChoiceProblem(defaults, AGENTS, override)).toBeNull();

    const refused: [RoleDefaults, Parameters<typeof launchChoiceProblem>[2], string][] = [
      [{ ...role, model: "gpt-z", available: false }, undefined, "Role default codex · gpt-z · high is not offered"],
      [{ available: false }, undefined, "No agent is configured for this role"],
      [role, { agentId: "claude" }, "Agent claude is not offered"],
      [role, { agentId: "codex", modelId: "gpt-z" }, "Model gpt-z is not offered for Codex"],
      [role, { agentId: "codex", modelId: "gpt-a", effortId: "max" }, "Effort max is not offered for GPT A"],
      [{ agent: "codex", model: "gpt-b", effort: "low" }, { agentId: "codex" }, "Effort low is not offered for GPT B"],
      [role, { agentId: "eve", effortId: "low" }, "Effort low needs a model"],
      [role, { agentId: "eve", modelId: "eve-1" }, "Model eve-1 is not offered for Eve"],
    ];
    for (const [defaults, override, text] of refused) expect(launchChoiceProblem(defaults, AGENTS, override)).toContain(text);
  });
});
