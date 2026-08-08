import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { fromTerminalSessionInfo } from "../../data/sessions";
import {
  L7_DECISION_PICKUP,
  L7_LIFECYCLE_ONLY_PICKUP,
  L7_LEGACY_PICKUP,
  L7_PICKUPS,
  L7_SENDER_AGENT_ONLY_PICKUP,
  L7_SENDER_ROLE_ONLY_PICKUP,
  L7_AGENT_NOTIFIER_HEARTBEAT,
} from "../../test/fixtures/busScenarios";
import { L6_CONTROLLED_WORKING, L6_LEGACY_RAW } from "../../test/fixtures/catalogRows";
import { developerReplyRequest } from "./BusDeveloperReply";
import { BusPane, pickupMatchesFocusedSeat } from "./BusPane";

const originalSize: Record<string, PropertyDescriptor | undefined> = {};
beforeAll(() => {
  for (const [property, value] of [
    ["offsetHeight", 240],
    ["offsetWidth", 320],
  ] as const) {
    originalSize[property] = Object.getOwnPropertyDescriptor(HTMLElement.prototype, property);
    Object.defineProperty(HTMLElement.prototype, property, {
      configurable: true,
      get(this: HTMLElement) {
        if (property === "offsetHeight") {
          return this.dataset.testid?.endsWith("-scroll") ? value : 58;
        }
        return value;
      },
    });
  }
});
afterAll(() => {
  for (const property of ["offsetHeight", "offsetWidth"]) {
    const descriptor = originalSize[property];
    if (descriptor) Object.defineProperty(HTMLElement.prototype, property, descriptor);
    else delete (HTMLElement.prototype as unknown as Record<string, unknown>)[property];
  }
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("BusPane", () => {
  it("defaults fleet-global and renders sender-to-owner, redelivery, escalation, heartbeat, and UA-3 limits", () => {
    const session = fromTerminalSessionInfo(L6_CONTROLLED_WORKING);
    const view = render(
      <BusPane session={session} pickups={L7_PICKUPS} heartbeat={L7_AGENT_NOTIFIER_HEARTBEAT} />,
    );

    expect(view.getByTestId("bus-pickup-list").getAttribute("data-virtualized")).toBe("false");
    expect(view.getByTestId("bus-pickup-inbox-decision-1").textContent).toContain(
      "architect:architect-1 → manager:manager-l7",
    );
    expect(view.getByTestId("bus-pickup-inbox-decision-1").textContent).toContain(
      "attempts 2",
    );
    expect(view.getByTestId("bus-pickup-inbox-escalation-1").textContent).toContain(
      "escalated 2026-07-17T19:06:00Z",
    );
    const legacy = view.getByTestId("bus-pickup-inbox-legacy-1").textContent ?? "";
    expect(legacy).toContain("system → owner unavailable");
    expect(legacy).toContain("attempts 0 · last — · next —");
    expect(view.getByTestId("bus-heartbeat-state").textContent).toBe("active");
    expect(view.getByTestId("bus-heartbeat-counts").textContent).toBe("3 / 1");
    expect(view.getByTestId("bus-limits-copy").textContent).toContain(
      "Full message bodies, consumed history, and escalation rung are not projected (UA-3)",
    );
    expect(view.getByTestId("bus-limits-copy").textContent).toContain(
      "Session composer submits never traverse this inbox",
    );
  });

  it("filters by exact focused-seat identity and makes an empty filter explicitly non-healthy", () => {
    const focused = fromTerminalSessionInfo(L6_CONTROLLED_WORKING);
    const other = fromTerminalSessionInfo(L6_LEGACY_RAW);
    const view = render(
      <BusPane session={focused} pickups={L7_PICKUPS} heartbeat={L7_AGENT_NOTIFIER_HEARTBEAT} />,
    );
    fireEvent.click(view.getByTestId("bus-focused-filter"));
    expect(view.getByTestId("bus-pickup-inbox-decision-1")).not.toBeNull();
    expect(view.queryByTestId("bus-pickup-inbox-escalation-1")).toBeNull();

    view.rerender(
      <BusPane session={other} pickups={L7_PICKUPS} heartbeat={L7_AGENT_NOTIFIER_HEARTBEAT} />,
    );
    const empty = view.getByTestId("bus-focused-empty").textContent ?? "";
    expect(empty).toContain("fleet-global bus still has 3 projected pending rows");
    expect(empty).toContain("not a bus-health verdict");
    expect(pickupMatchesFocusedSeat(L7_LEGACY_PICKUP, other)).toBe(false);
  });

  it("falls back to the fleet-global bus when focus disappears while the exact-seat filter is on", async () => {
    const view = render(
      <BusPane
        session={fromTerminalSessionInfo(L6_CONTROLLED_WORKING)}
        pickups={L7_PICKUPS}
        heartbeat={L7_AGENT_NOTIFIER_HEARTBEAT}
      />,
    );
    fireEvent.click(view.getByTestId("bus-focused-filter"));
    expect(view.queryByTestId("bus-pickup-inbox-escalation-1")).toBeNull();

    view.rerender(
      <BusPane session={undefined} pickups={L7_PICKUPS} heartbeat={L7_AGENT_NOTIFIER_HEARTBEAT} />,
    );
    expect(view.getByTestId("bus-focused-filter").getAttribute("aria-pressed")).toBe("false");
    expect(view.getByTestId("bus-focused-filter").hasAttribute("disabled")).toBe(true);
    expect(view.getByTestId("bus-pickup-inbox-escalation-1")).not.toBeNull();
    expect(view.queryByTestId("bus-focused-empty")).toBeNull();
    await waitFor(() =>
      expect(view.getByTestId("bus-focused-filter").textContent).toBe("filter to focused seat"),
    );
  });

  it("keeps a reply draft keyed to its entry across focused-seat filter unmounts", () => {
    const view = render(
      <BusPane
        session={fromTerminalSessionInfo(L6_CONTROLLED_WORKING)}
        pickups={L7_PICKUPS}
        heartbeat={L7_AGENT_NOTIFIER_HEARTBEAT}
      />,
    );
    fireEvent.click(view.getByTestId("bus-reply-toggle-inbox-escalation-1"));
    fireEvent.change(view.getByTestId("bus-reply-input-inbox-escalation-1"), {
      target: { value: "retain across the exact-seat filter" },
    });

    fireEvent.click(view.getByTestId("bus-focused-filter"));
    expect(view.queryByTestId("bus-reply-input-inbox-escalation-1")).toBeNull();
    fireEvent.click(view.getByTestId("bus-focused-filter"));

    expect(
      (view.getByTestId("bus-reply-input-inbox-escalation-1") as HTMLTextAreaElement).value,
    ).toBe("retain across the exact-seat filter");
    expect(
      view.getByTestId("bus-reply-toggle-inbox-escalation-1").getAttribute("aria-expanded"),
    ).toBe("true");
  });

  it("posts a developer decision to the original sender through /api/operator-inbox only", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const view = render(
      <BusPane
        session={fromTerminalSessionInfo(L6_CONTROLLED_WORKING)}
        pickups={[L7_DECISION_PICKUP]}
        heartbeat={L7_AGENT_NOTIFIER_HEARTBEAT}
      />,
    );
    fireEvent.click(view.getByTestId("bus-reply-toggle-inbox-decision-1"));
    expect(view.getByTestId("bus-reply-input-inbox-decision-1").getAttribute("name")).toBe(
      "developerDecision",
    );
    expect(view.getByTestId("bus-reply-input-inbox-decision-1").getAttribute("autocomplete")).toBe(
      "off",
    );
    fireEvent.change(view.getByTestId("bus-reply-input-inbox-decision-1"), {
      target: { value: "Proceed with the narrow implementation." },
    });
    fireEvent.click(view.getByTestId("bus-reply-submit-inbox-decision-1"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    expect(fetchMock.mock.calls[0][0]).toBe("/api/operator-inbox");
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({
      agentId: "architect-1",
      senderRole: "developer",
      recipientRole: "architect",
      gateId: "gate-l7",
      artifactPath: "notes/decision-l7.md",
      deliverToHosted: true,
      messageKind: "decision-ruling",
      ask: "Developer decision for inbox inbox-decision-1",
      response: "Proceed with the narrow implementation.",
    });
    expect(view.getByRole("status").textContent).toContain("recipient acknowledgment remains MCP-only");
  });

  it("renders a lifecycle-only source as unavailable and performs zero POSTs", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const view = render(
      <BusPane
        session={undefined}
        pickups={[L7_LIFECYCLE_ONLY_PICKUP]}
        heartbeat={L7_AGENT_NOTIFIER_HEARTBEAT}
      />,
    );

    expect(developerReplyRequest(L7_LIFECYCLE_ONLY_PICKUP, "do not post")).toBeNull();
    expect(
      view.getByTestId("bus-reply-unavailable-inbox-lifecycle-only").textContent,
    ).toContain("no sender address");
    expect(view.queryByTestId("bus-reply-toggle-inbox-lifecycle-only")).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("keeps each reply's open, draft, posted, and error state across >100-row unmounts", async () => {
    let resolvePosted: (response: Response) => void = () => {};
    let resolveError: (response: Response) => void = () => {};
    const postedRequest = new Promise<Response>((resolve) => {
      resolvePosted = resolve;
    });
    const errorRequest = new Promise<Response>((resolve) => {
      resolveError = resolve;
    });
    const fetchMock = vi
      .fn()
      .mockReturnValueOnce(postedRequest)
      .mockReturnValueOnce(errorRequest);
    vi.stubGlobal("fetch", fetchMock);
    const pickups = Array.from({ length: 120 }, (_, index) => ({
      ...L7_DECISION_PICKUP,
      id: `pickup-virtual-${index}`,
      entryId: `inbox-virtual-${index}`,
      senderAgentId: `sender-${index}`,
      gateId: `gate-virtual-${index}`,
    }));
    const view = render(
      <BusPane session={undefined} pickups={pickups} heartbeat={L7_AGENT_NOTIFIER_HEARTBEAT} />,
    );
    expect(view.getByTestId("bus-pickup-list").getAttribute("data-virtualized")).toBe("true");
    await waitFor(() =>
      expect(view.getByTestId("bus-reply-toggle-inbox-virtual-0")).not.toBeNull(),
    );

    fireEvent.click(view.getByTestId("bus-reply-toggle-inbox-virtual-0"));
    fireEvent.change(view.getByTestId("bus-reply-input-inbox-virtual-0"), {
      target: { value: "unsent decision" },
    });

    fireEvent.click(view.getByTestId("bus-reply-toggle-inbox-virtual-1"));
    fireEvent.change(view.getByTestId("bus-reply-input-inbox-virtual-1"), {
      target: { value: "posted decision" },
    });
    fireEvent.click(view.getByTestId("bus-reply-submit-inbox-virtual-1"));
    expect(document.getElementById("bus-reply-form-inbox-virtual-1")?.getAttribute("aria-busy")).toBe(
      "true",
    );

    fireEvent.click(view.getByTestId("bus-reply-toggle-inbox-virtual-2"));
    fireEvent.change(view.getByTestId("bus-reply-input-inbox-virtual-2"), {
      target: { value: "error decision" },
    });
    fireEvent.click(view.getByTestId("bus-reply-submit-inbox-virtual-2"));
    expect(document.getElementById("bus-reply-form-inbox-virtual-2")?.getAttribute("aria-busy")).toBe(
      "true",
    );
    expect(fetchMock).toHaveBeenCalledTimes(2);

    const scroll = view.getByTestId("bus-pickup-list-scroll");
    scroll.scrollTop = 58 * 90;
    fireEvent.scroll(scroll);
    await waitFor(() =>
      expect(view.queryByTestId("bus-reply-toggle-inbox-virtual-0")).toBeNull(),
    );

    await act(async () => {
      resolvePosted(new Response("{}", { status: 200 }));
      resolveError(new Response("no", { status: 500 }));
      await Promise.all([postedRequest, errorRequest]);
      await Promise.resolve();
    });

    scroll.scrollTop = 0;
    fireEvent.scroll(scroll);
    await waitFor(() =>
      expect(view.getByTestId("bus-reply-toggle-inbox-virtual-0")).not.toBeNull(),
    );
    expect(view.getByTestId("bus-reply-toggle-inbox-virtual-0").getAttribute("aria-expanded")).toBe(
      "true",
    );
    expect((view.getByTestId("bus-reply-input-inbox-virtual-0") as HTMLTextAreaElement).value).toBe(
      "unsent decision",
    );
    expect(view.getByTestId("bus-reply-status-inbox-virtual-1").textContent).toContain("posted");
    expect((view.getByTestId("bus-reply-input-inbox-virtual-1") as HTMLTextAreaElement).value).toBe(
      "",
    );
    expect(view.getByTestId("bus-reply-status-inbox-virtual-2").textContent).toContain(
      "draft is retained",
    );
    expect((view.getByTestId("bus-reply-input-inbox-virtual-2") as HTMLTextAreaElement).value).toBe(
      "error decision",
    );
  });
});

describe("developerReplyRequest reverse address", () => {
  it("uses only senderAgentId for an agent-only source", () => {
    expect(developerReplyRequest(L7_SENDER_AGENT_ONLY_PICKUP, "agent reply")).toEqual({
      agentId: "reviewer-agent-only",
      senderRole: "developer",
      gateId: "gate-agent-only",
      artifactPath: "notes/escalation-agent-only.md",
      deliverToHosted: true,
      messageKind: "message",
      ask: "Developer reply to escalation inbox-sender-agent-only",
      response: "agent reply",
    });
  });

  it("uses only senderRole for a role-only source", () => {
    expect(developerReplyRequest(L7_SENDER_ROLE_ONLY_PICKUP, "role ruling")).toEqual({
      senderRole: "developer",
      recipientRole: "architect",
      deliverToHosted: true,
      messageKind: "decision-ruling",
      ask: "Developer decision for inbox inbox-sender-role-only",
      response: "role ruling",
    });
  });

  it("uses the coherent sender agent and role pair without copying target lifecycle", () => {
    expect(developerReplyRequest(L7_DECISION_PICKUP, "paired ruling")).toEqual({
      agentId: "architect-1",
      senderRole: "developer",
      recipientRole: "architect",
      gateId: "gate-l7",
      artifactPath: "notes/decision-l7.md",
      deliverToHosted: true,
      messageKind: "decision-ruling",
      ask: "Developer decision for inbox inbox-decision-1",
      response: "paired ruling",
    });
  });

  it("refuses target-lifecycle-only rows", () => {
    expect(developerReplyRequest(L7_LIFECYCLE_ONLY_PICKUP, "nowhere")).toBeNull();
  });
});
