// The two live regions (260715-FEUI-L4 R8, design §9.5): exactly ONE polite + ONE assertive,
// permanent nodes fed by the announcer store — the strings themselves are asserted against the
// copy module in data/announcer.test.ts.
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { announceAssertive, announcePolite, announcerStore } from "../../data/announcer";
import { CockpitLiveRegions } from "./CockpitLiveRegions";

beforeEach(() =>
  announcerStore.setState({ polite: { text: "", seq: 0 }, assertive: { text: "", seq: 0 } }),
);
afterEach(cleanup);

describe("CockpitLiveRegions", () => {
  it("renders one polite and one assertive region, both present BEFORE any announcement", () => {
    const { getByTestId } = render(<CockpitLiveRegions />);
    const polite = getByTestId("cockpit-live-polite");
    const assertive = getByTestId("cockpit-live-assertive");
    expect(polite.getAttribute("aria-live")).toBe("polite");
    expect(polite.getAttribute("role")).toBe("status");
    expect(assertive.getAttribute("aria-live")).toBe("assertive");
    expect(assertive.getAttribute("role")).toBe("alert");
    expect(polite.textContent).toBe("");
    expect(assertive.textContent).toBe("");
  });

  it("routes announcements to their region and bumps the sequence for repeats", () => {
    const { getByTestId } = render(<CockpitLiveRegions />);
    act(() => announcePolite("effort xhigh queued — applies on next turn"));
    act(() => announceAssertive("scout failed"));
    expect(getByTestId("cockpit-live-polite").textContent).toBe(
      "effort xhigh queued — applies on next turn",
    );
    expect(getByTestId("cockpit-live-assertive").textContent).toBe("scout failed");
    act(() => announceAssertive("scout failed"));
    expect(getByTestId("cockpit-live-assertive").getAttribute("data-announce-seq")).toBe("2");
  });
});
