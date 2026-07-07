import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { RankBadge } from "./RankBadge";

afterEach(cleanup);

// The V4 rank insignia (L14): the glyph anatomy is the contract — three gold chevrons under a
// filled command pip for the orchestration seat, two purple chevrons for management — because the
// sketch the developer approved is pinned to exactly these shapes at 16px row / ~13px sm sizes.
describe("RankBadge (L14 V4 chevrons)", () => {
  it("renders the orchestration tier as a command pip over three chevrons", () => {
    const { container } = render(<RankBadge tier="orchestration" />);
    const svg = container.querySelector("svg");
    expect(svg?.getAttribute("data-rank-tier")).toBe("orchestration");
    expect(svg?.getAttribute("aria-hidden")).toBe("true");
    const paths = [...(svg?.querySelectorAll("path") ?? [])];
    expect(paths).toHaveLength(4); // pip + 3 chevrons
    expect(paths[0].style.fill).toBe("currentColor"); // the pip is filled, not stroked
    expect(paths.slice(1).every((path) => path.getAttribute("stroke") === "currentColor")).toBe(
      true,
    );
  });

  it("renders the management tier as two chevrons with no pip", () => {
    const { container } = render(<RankBadge tier="management" />);
    const svg = container.querySelector("svg");
    expect(svg?.getAttribute("data-rank-tier")).toBe("management");
    expect(svg?.querySelectorAll("path")).toHaveLength(2);
  });

  it("keeps the viewBox fixed and shrinks only the rendered box for the sm size", () => {
    const { container: row } = render(<RankBadge tier="orchestration" size="row" />);
    const { container: sm } = render(<RankBadge tier="orchestration" size="sm" />);
    const rowSvg = row.querySelector("svg");
    const smSvg = sm.querySelector("svg");
    expect(rowSvg?.getAttribute("width")).toBe("16");
    expect(rowSvg?.getAttribute("height")).toBe("17");
    expect(smSvg?.getAttribute("width")).toBe("13");
    expect(smSvg?.getAttribute("height")).toBe("14");
    expect(smSvg?.getAttribute("viewBox")).toBe(rowSvg?.getAttribute("viewBox"));
    expect(smSvg?.getAttribute("data-rank-size")).toBe("sm");
  });

  it("colours by tier token class (gold vs purple)", () => {
    const { container: orch } = render(<RankBadge tier="orchestration" />);
    const { container: mgmt } = render(<RankBadge tier="management" />);
    expect(orch.querySelector("svg")?.className.baseVal).toContain("c_gold");
    expect(mgmt.querySelector("svg")?.className.baseVal).toContain("c_purple");
  });
});
