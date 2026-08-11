// The failed-launch banner (260715-FEUI-L3 R6/S4): the refusal renders VERBATIM for ALL THREE
// harnesses, the retained pair renders as refused (never validated), and the only actions are an
// honest Retire confirm and 'Launch corrected…' — no auto-retry path exists.
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { fromTerminalSessionInfo } from "../../data/sessions";
import { catalogRow } from "../../test/fixtures/catalogRows";
import {
  FAILED_CLAUDE_ROW,
  FAILED_CODEX_ROW,
  FAILED_LAUNCH_ROWS,
  FAILED_PI_ROW,
} from "../../test/fixtures/openResponses";
import { FailedLaunchBanner } from "./FailedLaunchBanner";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderBanner(row = FAILED_CLAUDE_ROW) {
  const onLaunchCorrected = vi.fn();
  const view = render(
    <FailedLaunchBanner session={fromTerminalSessionInfo(row)} onLaunchCorrected={onLaunchCorrected} />,
  );
  return { ...view, onLaunchCorrected };
}

describe("FailedLaunchBanner (R6) — uniform across Claude, Codex, and Pi", () => {
  it("renders the bridgeError VERBATIM for every harness's failed row", () => {
    for (const row of FAILED_LAUNCH_ROWS) {
      const { getByTestId, unmount } = renderBanner(row);
      expect(getByTestId("failed-launch-bridge-error").textContent).toBe(
        row.controlRaw?.bridgeError,
      );
      unmount();
    }
  });

  it("renders the retained pair as the REFUSED pair, never as validated evidence", () => {
    const { getByTestId } = renderBanner(FAILED_CODEX_ROW);
    const pair = getByTestId("failed-launch-refused-pair");
    expect(pair.textContent).toContain("gpt-5.6-sol · turbo");
    expect(pair.textContent).toContain("never validated");
    const badge = getByTestId("failed-launch-banner").querySelector("[data-evidence-tier]");
    expect(badge?.getAttribute("data-evidence-tier")).toBe("refused");
    expect(badge?.getAttribute("aria-label")).toContain("refused");
  });

  it("'Launch corrected…' pre-fills the flow from the refused pair", () => {
    const { getByTestId, onLaunchCorrected } = renderBanner(FAILED_PI_ROW);
    fireEvent.click(getByTestId("failed-launch-correct"));
    expect(onLaunchCorrected).toHaveBeenCalledWith({
      harness: "pi",
      modelKey: "deepseek-v4-flash",
      effort: "max",
    });
  });

  it("Retire arms an HONEST confirm naming the session and leaf; confirming retires once", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ sessions: [] }),
    } as Response);
    vi.stubGlobal("fetch", fetchMock);
    const row = catalogRow({
      ...FAILED_CLAUDE_ROW,
      id: "failed-leafy",
      taskDocumentRef: {
        repository: "agents-remember",
        path: "260715_react-tui-cockpit-frontend/03_capability-catalog-and-launch.json",
      },
    });
    const { getByTestId, queryByTestId } = renderBanner(row);
    // nothing fires before the explicit confirm — no auto-retry, no auto-retire
    expect(fetchMock).not.toHaveBeenCalled();
    fireEvent.click(getByTestId("failed-launch-retire"));
    const confirm = getByTestId("failed-launch-retire-confirm");
    expect(confirm.textContent).toContain("scout-claude"); // names the session label
    expect(confirm.textContent).toContain("03_capability-catalog-and-launch"); // names the leaf
    expect(fetchMock).not.toHaveBeenCalled();
    fireEvent.click(getByTestId("failed-launch-retire-confirm-yes"));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith("/api/terminal/failed-leafy/terminate", {
        method: "POST",
      }),
    );
    // 'keep' path: arming again and declining sends nothing further
    await waitFor(() => expect(queryByTestId("failed-launch-retire-confirm")).toBeNull());
  });

  it("declining the confirm keeps the row untouched", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { getByTestId, queryByTestId } = renderBanner();
    fireEvent.click(getByTestId("failed-launch-retire"));
    fireEvent.click(getByTestId("failed-launch-retire-confirm-no"));
    expect(queryByTestId("failed-launch-retire-confirm")).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("a failed row WITHOUT a retained bridgeError states the absence rather than inventing one", () => {
    const bare = catalogRow({ id: "failed-bare", controlState: "failed", harness: "claude" });
    const { getByTestId } = renderBanner(bare);
    expect(getByTestId("failed-launch-bridge-error").textContent).toContain("no bridgeError retained");
  });
});
