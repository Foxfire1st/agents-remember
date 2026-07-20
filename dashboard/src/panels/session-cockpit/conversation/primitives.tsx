// Shared conversation-item primitives. They enforce the ruled cross-item behaviors once so every
// block reads as one grammar (design §12.2, §14.2) and matches the cockpit house style (bare color
// tokens, inline amber focus ring, lowercase interpunct chips — A8). No new palette or recipe.

import { useId, useMemo } from "react";

import { css } from "../../../../styled-system/css";
import type {
  ConversationLane,
  ConversationSource,
  FeatureCapability,
} from "../../../data/conversation/types";

const clampButton = css({
  font: "inherit",
  fontSize: "0.66rem",
  letterSpacing: "0.04em",
  color: "muted",
  background: "transparent",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  paddingInline: "0.4rem",
  paddingBlock: "0.04rem",
  cursor: "pointer",
  marginTop: "0.2rem",
  _hover: { color: "amber", borderColor: "amber" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});

/**
 * The ONLY expand/collapse control for a clamped message/tool/diff (§14.2). It is a real button with
 * aria-expanded/aria-controls. It shows an exact `+N lines` ONLY when a logical source-line count is
 * known (counted from source newlines, never visual wrapping/pixels); otherwise plain show more/less.
 */
export function ClampButton({
  expanded,
  onToggle,
  controlsId,
  hiddenLines,
  noun = "lines",
}: {
  expanded: boolean;
  onToggle: () => void;
  controlsId: string;
  hiddenLines?: number;
  noun?: string;
}) {
  const label = expanded
    ? "Show less"
    : hiddenLines !== undefined && hiddenLines > 0
      ? `Show more (+${hiddenLines} ${noun})`
      : "Show more";
  return (
    <button
      type="button"
      className={clampButton}
      aria-expanded={expanded}
      aria-controls={controlsId}
      onClick={onToggle}
      data-testid="conversation-clamp"
    >
      {label}
    </button>
  );
}

/** Count logical source lines in normalized text — the honest basis for an exact clamp count (§12.2). */
export function sourceLineCount(text: string): number {
  if (text.length === 0) return 0;
  return text.split("\n").length;
}

const badge = css({
  display: "inline-flex",
  alignItems: "center",
  fontSize: "0.6rem",
  letterSpacing: "0.06em",
  textTransform: "uppercase",
  color: "muted",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  paddingInline: "0.28rem",
  paddingBlock: "0.02rem",
});

// A terse source/lane badge shown ONLY when origin changes interpretation (design §13): a normal
// operator message needs no badge; durable-bus, native-replay, and unknown-input inputs do.
export function SourceBadge({
  lane,
  source,
}: {
  lane: ConversationLane;
  source: ConversationSource;
}) {
  const label = useMemo(() => sourceBadgeLabel(lane, source), [lane, source]);
  if (label === null) return null;
  return (
    <span className={badge} data-testid="conversation-source-badge">
      {label}
    </span>
  );
}

export function sourceBadgeLabel(
  lane: ConversationLane,
  source: ConversationSource,
): string | null {
  if (lane === "agent-bus" || source === "durable-inbox") return "agent bus";
  if (source === "harness-replay") return "native replay";
  if (source === "native-history") return "native history";
  if (lane === "unknown-input") return "input source unavailable";
  if (source === "terminal-controlled") return "terminal";
  return null; // ordinary operator/harness content is unbadged
}

const reason = css({
  fontSize: "0.64rem",
  color: "muted",
  fontStyle: "italic",
});

/**
 * A capability reason line (§8): renders the exact server reason for a partial/unavailable/unverified
 * capability. Copy is honest and present, never hidden — but structured, not a wall (A3).
 */
export function CapabilityReason({ capability }: { capability: FeatureCapability }) {
  if (capability.state === "supported") return null;
  return (
    <span className={reason} data-testid="capability-reason" data-state={capability.state}>
      {capability.state}: {capability.reason}
    </span>
  );
}

/** A stable id pair for a clamp button + its controlled region. */
export function useClampIds(): { buttonId: string; regionId: string } {
  const base = useId();
  return { buttonId: `${base}-btn`, regionId: `${base}-region` };
}
