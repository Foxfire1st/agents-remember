import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { capabilityCatalogStore } from "../../data/capabilityCatalog";
import { sessionCockpitStore } from "../../data/sessionCockpitStore";
import { fromTerminalSessionInfo } from "../../data/sessions";
import { CAPABILITY_SCHEMA, type CapabilitySnapshotWire } from "../../types/harnessCapabilities";
import { catalogRow } from "../../test/fixtures/catalogRows";
import { CapabilitiesPane } from "./CapabilitiesPane";

const SNAPSHOT: CapabilitySnapshotWire = {
  selectedModelKey: "claude/sonnet",
  selectedEffort: null,
  configOptions: [],
  models: [
    {
      key: "claude/sonnet",
      displayName: "Sonnet",
      resolvedModel: "claude-sonnet-4",
      description: "Balanced model",
      supportsEffort: true,
      effortOptions: [
        {
          key: "low",
          displayName: "Low",
          description: null,
          launchSettable: true,
          sessionSettable: true,
        },
        {
          key: "high",
          displayName: "High",
          description: null,
          launchSettable: true,
          sessionSettable: true,
        },
      ],
      defaultEffort: "high",
      isDefault: true,
      hidden: false,
      selectable: true,
      provider: "anthropic",
    },
    {
      key: "claude/haiku",
      displayName: "Haiku",
      resolvedModel: "claude-haiku-4",
      description: null,
      supportsEffort: false,
      effortOptions: [],
      defaultEffort: null,
      isDefault: false,
      hidden: false,
      selectable: true,
      provider: "anthropic",
    },
  ],
};

const session = () =>
  fromTerminalSessionInfo(
    catalogRow({ id: "l7-capabilities", harness: "claude", controlState: "ready" }),
  );

beforeEach(() => {
  capabilityCatalogStore.setState({ perHarness: {} });
  sessionCockpitStore.setState({ focusedSessionId: null, perSession: {} });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("CapabilitiesPane", () => {
  it("keeps live exact-session truth separate from the pre-session envelope", () => {
    const seat = session();
    sessionCockpitStore.getState().setLiveSnapshot(seat.id, {
      sessionId: seat.id,
      fetchedAt: 100,
      payload: SNAPSHOT,
    });
    capabilityCatalogStore.setState({
      perHarness: {
        claude: {
          fetchState: "idle",
          fetchedAt: 90,
          envelope: {
            schema: CAPABILITY_SCHEMA,
            harness: "claude",
            cacheStatus: "hit",
            installFingerprint: "claude-bin:sha256:abc123",
            capabilities: SNAPSHOT,
          },
        },
      },
    });
    const view = render(
      <CapabilitiesPane
        session={seat}
        cockpit={sessionCockpitStore.getState().perSession[seat.id]}
      />,
    );

    expect(view.getByTestId("capabilities-selected-model").textContent).toContain(
      "claude/sonnet (snapshot)",
    );
    expect(view.getByTestId("capabilities-selected-effort").textContent).toContain(
      "effort not echoed",
    );
    const models = view.getByTestId("capabilities-live-models").textContent ?? "";
    expect(models).toContain("current · Sonnet · claude/sonnet");
    expect(models).toContain("low (launch+session)");
    expect(models).toContain("Haiku · claude/haiku");
    expect(models).toContain("— (none advertised for this model)");
    expect(view.getByTestId("capabilities-cache-status").textContent).toContain("cache hit");
    expect(view.getByTestId("capabilities-install-fingerprint").textContent).toBe(
      "claude-bin:sha256:abc123",
    );
    const cost = view.getByTestId("capabilities-refresh-cost").textContent ?? "";
    expect(cost).toContain("starts a short-lived native claude process");
    expect(cost).toContain("No duration is assumed");
  });

  it("uses only the existing exact-session and harness refresh routes", async () => {
    const seat = session();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(SNAPSHOT), { status: 200 }))
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            schema: CAPABILITY_SCHEMA,
            harness: "claude",
            cacheStatus: "refreshed",
            installFingerprint: "new-fingerprint",
            capabilities: SNAPSHOT,
          }),
          { status: 200 },
        ),
      );
    vi.stubGlobal("fetch", fetchMock);
    const view = render(<CapabilitiesPane session={seat} cockpit={undefined} />);

    fireEvent.click(view.getByTestId("capabilities-live-refresh"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(fetchMock.mock.calls[0][0]).toBe("/api/terminal/l7-capabilities/capabilities");

    fireEvent.click(view.getByTestId("capabilities-catalog-refresh"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(fetchMock.mock.calls[1][0]).toBe("/api/harnesses/claude/capabilities?refresh=true");
  });
});
