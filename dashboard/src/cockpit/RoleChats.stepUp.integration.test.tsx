import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { RoleChatsPane } from "./RoleChats";
import { readTasklessActiveRequests, tasklessRequestStorageKey, type LauncherRole } from "./roleLaunchModel";
import { stepUpOptions, stepUpReceipt } from "./stepUp.test-utils";

const options = {
  roleDefaults: { agent: "codex", model: "base", available: true, stepUp: { harness: "claude", model: "alternative" } },
  agents: [{ id: "codex", label: "Codex", models: [{ id: "base", label: "Base", isDefault: true }] }],
  catalogOrigin: "local-component-test",
  executions: [],
};
let dispatches: Record<string, unknown>[];
let resultReads: Record<string, unknown>[];

beforeEach(() => {
  sessionStorage.clear();
  dispatches = [];
  resultReads = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const body = JSON.parse(String(init?.body ?? "{}"));
    if (url === "/api/role-launch/options") return Response.json(options);
    if (url === "/api/role-launch/result") {
      resultReads.push(body);
      return Response.json({}, { status: 404 });
    }
    if (url === "/api/role-launch/dispatch") {
      dispatches.push(body);
      // No server receipt exists: the first HTTP dispatch never reaches the server.
      if (dispatches.length === 1) throw new TypeError("offline before server receipt");
      return Response.json({ status: "completed", requestId: body.requestId, canStart: true, canRevive: false });
    }
    return Response.json({ available: false, frameUrl: null }, { status: 503 });
  }));
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function pane(role: LauncherRole) {
  return render(<RoleChatsPane active tasks={[]} boundSelection={{ role }} />);
}

it.each(["architect", "investigator"] as const)("replays %s step-up after storage reload and a real 404 result fold", async (role) => {
  vi.spyOn(window, "prompt").mockReturnValue("  Another approach is needed.  ");
  const first = pane(role);
  await waitFor(() => expect((first.getByRole("button", { name: "Step up role" }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(first.getByRole("button", { name: "Step up role" }));
  await waitFor(() => expect((first.getByRole("button", { name: "Retry role launch" }) as HTMLButtonElement).disabled).toBe(false));
  const original = dispatches[0];
  expect(original).toEqual({ role, action: "start", requestId: expect.any(String), stepUp: "Another approach is needed." });
  const stored = JSON.parse(sessionStorage.getItem(tasklessRequestStorageKey(role))!);
  expect(stored.retryPayload).toEqual({ role, stepUp: "Another approach is needed." });
  if (role === "investigator") expect(tasklessRequestStorageKey(role)).toBe("ar-role-taskless-request-v1:system-specialist");
  first.unmount();

  // Remount uses the real run-state initializer/decoder, not an injected retry selection.
  const reloaded = pane(role);
  await waitFor(() => expect((reloaded.getByRole("button", { name: "Retry role launch" }) as HTMLButtonElement).disabled).toBe(false));
  const beforeRead = resultReads.length;
  fireEvent.click(reloaded.getByRole("button", { name: "Refresh role result" }));
  await waitFor(() => expect(resultReads.length).toBeGreaterThan(beforeRead));
  expect(resultReads.at(-1)).toEqual({ role, requestId: original.requestId });
  await waitFor(() => expect((reloaded.getByRole("button", { name: "Retry role launch" }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(reloaded.getByRole("button", { name: "Retry role launch" }));
  await waitFor(() => expect(dispatches).toHaveLength(2));
  expect(dispatches[1]).toEqual(original);
});

it.each(["architect", "system-specialist"] as const)("retains ordinary and legacy %s storage without inventing step-up", (role) => {
  const canonical = role === "system-specialist" ? "investigator" : role;
  const payload = { role, agentOverride: { agentId: "codex", modelId: "base" } };
  sessionStorage.setItem(tasklessRequestStorageKey(canonical), JSON.stringify({ requestId: "saved-ordinary", pending: true, retryPayload: payload }));
  expect(readTasklessActiveRequests()[canonical]).toEqual({ requestId: "saved-ordinary", pending: true, retryPayload: payload });
});

it.each(["architect", "system-specialist"] as const)("retries an ordinary saved %s request through the pane without step-up", async (role) => {
  const canonical = role === "system-specialist" ? "investigator" : role;
  const retryPayload = { role, agentOverride: { agentId: "codex", modelId: "base" } };
  sessionStorage.setItem(tasklessRequestStorageKey(canonical), JSON.stringify({ requestId: "ordinary-exact-id", pending: true, retryPayload }));
  const view = pane(role);
  await waitFor(() => expect((view.getByRole("button", { name: "Retry role launch" }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(view.getByRole("button", { name: "Retry role launch" }));
  await waitFor(() => expect(dispatches).toHaveLength(1));
  expect(dispatches[0]).toEqual({ ...retryPayload, action: "start", requestId: "ordinary-exact-id" });
});

it.each(["  Saved reason.  ", "", "   ", 7, null, { reason: "not a string" }])("decodes only a nonempty string step-up reason (%j)", (stepUp) => {
  sessionStorage.setItem(tasklessRequestStorageKey("architect"), JSON.stringify({ requestId: "decode-id", retryPayload: { role: "architect", stepUp } }));
  const expected = typeof stepUp === "string" && stepUp.trim() ? { stepUp: stepUp.trim() } : {};
  expect(readTasklessActiveRequests().architect?.retryPayload).toEqual({ role: "architect", ...expected });
});

it("marks the real row's base choices non-applied after a cross-harness step-up receipt", async () => {
  vi.spyOn(window, "prompt").mockReturnValue("Use the configured alternative.");
  let receipt: ReturnType<typeof stepUpReceipt> | undefined;
  vi.mocked(fetch).mockImplementation(async (url, init) => {
    const body = JSON.parse(String(init?.body ?? "{}"));
    if (url === "/api/role-launch/options") return Response.json({ ...stepUpOptions, executions: receipt ? [receipt] : [] });
    if (url === "/api/role-launch/dispatch") {
      dispatches.push(body);
      receipt = stepUpReceipt(body.requestId);
      return Response.json(receipt);
    }
    if (url === "/api/role-launch/result") return Response.json(receipt ?? {}, { status: receipt ? 200 : 404 });
    return Response.json({ available: false, frameUrl: null }, { status: 503 });
  });
  const view = pane("architect");
  await view.findByText("Codex Auto");
  expect(view.getByText("Plan mode On").getAttribute("title")).toContain("user's settings");
  expect(view.queryByLabelText("Plan mode override")).toBeNull();
  fireEvent.change(view.getByLabelText("Speed override"), { target: { value: "priority" } });
  fireEvent.click(view.getByRole("button", { name: "Step up role" }));
  await view.findByText("Stepped-up · claude-observed");
  expect(view.getAllByTestId("role-launcher")).toHaveLength(1);
  expect(view.getByText("Base · Codex Auto").getAttribute("title")).toContain("not applied");
  expect(view.getByText("Base · Plan mode On").getAttribute("title")).toContain("not applied");
  expect(view.getByText("Base · Instructions").getAttribute("title")).toContain("not applied");
  expect(view.getByTestId("role-service-tier").textContent).toBe("Base · Speed: Fast");
  expect(view.getByTestId("role-service-tier").getAttribute("title")).toContain("not applied");
  expect(view.getByText("Mode/features unreported").getAttribute("title")).toContain("receipt");
  expect(view.getByText("Base · Speed Fast").getAttribute("title")).toContain("not applied");
  expect(view.queryByLabelText("Speed override")).toBeNull();
  expect(view.queryByText("Codex Auto")).toBeNull();
  expect(view.queryByText("Plan mode On")).toBeNull();
  expect(view.queryByLabelText("Auto accept override")).toBeNull();
  expect(dispatches).toEqual([{ role: "architect", action: "start", requestId: expect.any(String), stepUp: "Use the configured alternative." }]);
});