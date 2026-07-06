import { cva } from "../../styled-system/css";

// The rank insignia (L14, developer-picked V4): military CHEVRON badges carried by the two command
// tiers of an orchestrated run — three gold chevrons under a command pip for the ORCHESTRATION seat
// (the sprint task), two purple chevrons for MANAGEMENT (a commanded master). Crisp inline SVG with a
// soft drop-shadow glow; `row` (16px) is the tasks-list/row size, `sm` (~13px) the rail/group-header
// size. Insignia are never decoration on a leaf: the tier encodes the real hierarchy, so the badge
// renders only where an orchestration task exists (D3 — flat runs carry no insignia at all).
const badge = cva({
  base: {
    flex: "none",
    display: "block",
    alignSelf: "center",
    "& path": {
      fill: "none",
      strokeWidth: "1.9",
      strokeLinecap: "round",
      strokeLinejoin: "round",
    },
  },
  variants: {
    tier: {
      orchestration: {
        color: "gold",
        filter: "drop-shadow(0 0 3px color-mix(in oklch, token(colors.gold) 60%, transparent))",
      },
      management: {
        color: "purple",
        filter: "drop-shadow(0 0 3px color-mix(in oklch, token(colors.purple) 55%, transparent))",
      },
    },
  },
});

export type RankTier = "orchestration" | "management";
export type RankBadgeSize = "row" | "sm";

// Rendered width/height per tier and size; the viewBox stays fixed so the glyph scales crisply.
const DIMENSIONS: Record<RankTier, Record<RankBadgeSize, { width: number; height: number }>> = {
  orchestration: { row: { width: 16, height: 17 }, sm: { width: 13, height: 14 } },
  management: { row: { width: 16, height: 12 }, sm: { width: 13, height: 10 } },
};

export function RankBadge({ tier, size = "row" }: { tier: RankTier; size?: RankBadgeSize }) {
  const { width, height } = DIMENSIONS[tier][size];
  if (tier === "orchestration") {
    return (
      <svg
        className={badge({ tier })}
        viewBox="0 0 16 17"
        width={width}
        height={height}
        aria-hidden="true"
        data-rank-tier="orchestration"
        data-rank-size={size}
      >
        {/* the command pip — filled, unlike the stroked chevrons (inline style outranks the cva rule) */}
        <path d="M8 1.6 L9 3.4 L7 3.4 Z" style={{ fill: "currentColor", stroke: "none" }} />
        <path d="M2.5 7 L8 4.4 L13.5 7" stroke="currentColor" />
        <path d="M2.5 11 L8 8.4 L13.5 11" stroke="currentColor" />
        <path d="M2.5 15 L8 12.4 L13.5 15" stroke="currentColor" />
      </svg>
    );
  }
  return (
    <svg
      className={badge({ tier })}
      viewBox="0 0 16 12"
      width={width}
      height={height}
      aria-hidden="true"
      data-rank-tier="management"
      data-rank-size={size}
    >
      <path d="M2.5 5 L8 2.4 L13.5 5" stroke="currentColor" />
      <path d="M2.5 9.5 L8 6.9 L13.5 9.5" stroke="currentColor" />
    </svg>
  );
}
