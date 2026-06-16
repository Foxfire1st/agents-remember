import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { dashboardStore } from "../data/store";
import { GALLERY } from "../dev/fixtures";
import { CockpitShell } from "./Cockpit";

function seed(stateName: string) {
  const fixture = GALLERY.find((entry) => entry.name === stateName);
  if (!fixture) throw new Error(`fixture not found: ${stateName}`);
  dashboardStore.getState().applySnapshot(fixture.projection);
}

afterEach(cleanup);

describe("CockpitShell full-bleed machine-map views (5f S1)", () => {
  it("rails the Operations view but goes full-bleed (no rails) for the Engine Room", () => {
    seed("engine-fleet");
    const { container, getByRole } = render(<CockpitShell />);

    // Operations (default): the railed 3-column shell.
    expect(container.querySelector(".shell__body")?.getAttribute("data-fullbleed")).toBe("false");
    expect(container.querySelector(".rail--left")).not.toBeNull();
    expect(container.querySelector(".rail--right")).not.toBeNull();

    // Switch to the Engine Room machine-map view via the mode bar.
    fireEvent.click(getByRole("radio", { name:"Engine Room" }));

    // Full-bleed: both rails gone, single full-width column, and the room's own 3-zone layout
    // (header + boot/diagnostics zone) is present.
    expect(container.querySelector(".shell__body")?.getAttribute("data-fullbleed")).toBe("true");
    expect(container.querySelector(".rail--left")).toBeNull();
    expect(container.querySelector(".rail--right")).toBeNull();
    expect(container.querySelector('[data-testid="engine-room-header"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="engine-room-diagnostics"]')).not.toBeNull();
  });

  it("keeps the rails for the Operations and Memory views", () => {
    seed("engine-fleet");
    const { container, getByRole } = render(<CockpitShell />);

    fireEvent.click(getByRole("radio", { name:"Memory" }));
    expect(container.querySelector(".shell__body")?.getAttribute("data-fullbleed")).toBe("false");
    expect(container.querySelector(".rail--left")).not.toBeNull();
    expect(container.querySelector(".rail--right")).not.toBeNull();
  });
});
