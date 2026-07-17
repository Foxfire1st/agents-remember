import { cva } from "../../styled-system/css";

import { TIER_SENSE } from "../data/launchEvidence";
import type { EvidenceTier } from "../data/sessionCockpitStore";

// The launch-evidence badge (260715-FEUI-L3 R7): five tiers, five DISTINCT glyphs so tiers never
// collapse at ambient sizes — '…' pending (in-flight provenance, deliberately not a verification
// mark), '✓' readback (OK echo), '◇' model-validated (diamond validated), '·' defaults (dot),
// '✕' refused. The tier WORD is ALWAYS present in the accessible name at every size (the glyph
// alone is aria-hidden); assignment comes from data/launchEvidence.launchTier — this component
// only renders, it never decides.

export const EVIDENCE_GLYPHS: Record<EvidenceTier, string> = {
  pending: "…",
  readback: "✓",
  "model-validated": "◇",
  defaults: "·",
  refused: "✕",
};

export type EvidenceBadgeSize = "row" | "sm";

const badge = cva({
  base: {
    display: "inline-flex",
    alignItems: "baseline",
    gap: "0.25em",
    whiteSpace: "nowrap",
    lineHeight: "1",
  },
  variants: {
    tier: {
      pending: { color: "muted" },
      readback: { color: "mint" },
      "model-validated": { color: "cyan" },
      defaults: { color: "dormant" },
      refused: { color: "alarm" },
    },
    size: {
      row: { fontSize: "0.72rem" },
      sm: { fontSize: "0.62rem" },
    },
  },
});

export function EvidenceBadge({
  tier,
  size = "row",
  showWord = false,
}: {
  tier: EvidenceTier;
  size?: EvidenceBadgeSize;
  /** Also render the tier word visibly (banners); the ACCESSIBLE name carries it regardless. */
  showWord?: boolean;
}) {
  return (
    <span
      className={badge({ tier, size })}
      role="img"
      aria-label={`evidence ${tier}: ${TIER_SENSE[tier]}`}
      title={`evidence ${tier} — ${TIER_SENSE[tier]}`}
      data-evidence-tier={tier}
      data-evidence-size={size}
    >
      <span aria-hidden="true">{EVIDENCE_GLYPHS[tier]}</span>
      {showWord ? <span>{tier}</span> : null}
    </span>
  );
}
