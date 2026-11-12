import { afterEach, describe, expect, it, vi } from "vitest";

import { docRefPath, listTaskReports } from "./taskReports";

afterEach(() => vi.unstubAllGlobals());

describe("data/taskReports client", () => {
  it("builds the per-task reports URL", async () => {
    const fn = vi.fn(
      async () => ({ ok: true, status: 200, statusText: "", json: async () => ({}) }) as unknown as Response,
    );
    vi.stubGlobal("fetch", fn);
    await listTaskReports("agents-remember", "m", "/tasks/agents-remember/m/09_x.json");
    const [url] = fn.mock.calls[0] as unknown as [string];
    expect(url).toBe(
      "/api/role-launch/reports?repo=agents-remember&master=m&doc=%2Ftasks%2Fagents-remember%2Fm%2F09_x.json",
    );
  });

  it("strips the dashboard prefix down to the recorded task reference", () => {
    expect(docRefPath("agents-remember", "/tasks/agents-remember/m/09_x.json")).toBe("m/09_x.json");
    expect(docRefPath("agents-remember", "m/09_x.json")).toBe("m/09_x.json");
  });
});
