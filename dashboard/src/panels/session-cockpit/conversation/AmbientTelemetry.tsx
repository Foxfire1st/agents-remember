// Ambient evidence-bound telemetry (design §9.9, §12.2; round-1 F3). The landed L3 telemetry route
// (codex cumulative token usage is a supported metric) is rendered ambiently in the conversation
// toolbar — NOT as noisy transcript rows. Every value is absent-not-zero (a missing metric is omitted,
// never shown as 0 — finding A2) and carries its evidence (origin/observed/runtime) in a tooltip. A
// non-fresh metric degrades to a quiet marker (finding A4), never an alarm. This is also where the
// A-convention module (`joinChips`/`ABSENT`/`freshnessTone`) becomes product truth (F19).

import { useEffect, useState } from "react";

import { css } from "../../../../styled-system/css";
import { fetchConversationTelemetry } from "../../../data/conversation/client";
import { freshnessTone, humanizeAge, joinChips } from "../../../data/conversation/format";
import type { ConversationTelemetry } from "../../../data/conversation/types";

const chip = css({
  fontSize: "0.64rem",
  color: "muted",
  fontVariantNumeric: "tabular-nums",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
});
const staleChip = css({ fontSize: "0.6rem", color: "dormant", fontStyle: "italic", flexShrink: 0 });

function formatTokens(value: number | undefined): string | null {
  if (value === undefined || !Number.isFinite(value)) return null;
  if (value < 1000) return `${value}`;
  if (value < 1_000_000) return `${(value / 1000).toFixed(1)}k`;
  return `${(value / 1_000_000).toFixed(1)}M`;
}

function usageChips(telemetry: ConversationTelemetry): string {
  const chips: (string | null)[] = [];
  const usage = telemetry.usage?.value;
  if (usage !== undefined) {
    const input = formatTokens(usage.inputTokens);
    const output = formatTokens(usage.outputTokens);
    const cached = formatTokens(usage.cachedTokens);
    if (input !== null) chips.push(`in ${input}`);
    if (output !== null) chips.push(`out ${output}`);
    if (cached !== null) chips.push(`cached ${cached}`);
  }
  const context = telemetry.context?.value;
  if (context !== undefined && Number.isFinite(context.percent)) {
    chips.push(`ctx ${Math.round(context.percent)}%`);
  }
  const cost = telemetry.cost?.value;
  if (cost !== undefined && Number.isFinite(cost.amount)) {
    chips.push(`${cost.amount} ${cost.currency}`);
  }
  return joinChips(chips);
}

export function AmbientTelemetry({
  sessionId,
  epoch,
  statusRevision,
  active = true,
  base,
}: {
  sessionId: string;
  epoch: string | undefined;
  /** Bumps refresh when the turn/status advances (ambient, not per-token). */
  statusRevision: number | undefined;
  /** A retained hidden chat keeps its last chips but owns no background telemetry request. */
  active?: boolean;
  base?: string;
}) {
  const [telemetry, setTelemetry] = useState<ConversationTelemetry | null>(null);

  useEffect(() => {
    if (!active || epoch === undefined) return undefined;
    const controller = new AbortController();
    void fetchConversationTelemetry(sessionId, epoch, base ?? "", fetch, controller.signal).then((next) => {
      if (!controller.signal.aborted) setTelemetry(next);
    });
    return () => {
      controller.abort();
    };
  }, [sessionId, epoch, statusRevision, active, base]);

  if (telemetry === null) return null;
  const chips = usageChips(telemetry);
  if (chips.length === 0) return null;

  // The primary metric's freshness governs the quiet-stale marker (A4).
  const primary = telemetry.usage ?? telemetry.context ?? telemetry.cost;
  const tone = primary !== undefined ? freshnessTone(primary.freshness, null) : "fresh";
  const evidenceTitle =
    primary !== undefined
      ? `${primary.origin} · observed ${humanizeAge(primary.observedAt)} · runtime ${primary.runtimeVersion}`
      : undefined;

  return (
    <>
      <span className={chip} data-testid="ambient-telemetry" title={evidenceTitle}>
        {chips}
      </span>
      {tone !== "fresh" ? (
        <span className={staleChip} data-testid="ambient-telemetry-freshness">
          {tone}
        </span>
      ) : null}
    </>
  );
}
