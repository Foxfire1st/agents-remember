import { Button } from "react-aria-components";

import { css, cva, cx } from "../../../styled-system/css";
import type { SetChipModel } from "../../data/setChips";

// The acceptance chip primitive (260715-FEUI-L4 R2, design §6.3): renders ONE SetChipModel —
// the text already carries the acceptance WORD (data/setControlsCopy), so a chip can never be
// color-only. The spinner glyph is the slow 2.4 s ease-in-out pulse (the animation ruling);
// acknowledge/retry are the only actions and belong to the chip's owner.

const shell = cva({
  base: {
    display: "inline-flex",
    alignItems: "baseline",
    gap: "0.3rem",
    maxWidth: "100%",
    minWidth: "0",
    fontSize: "0.66rem",
    borderWidth: "1px",
    borderStyle: "solid",
    borderRadius: "2px",
    paddingInline: "0.3rem",
    whiteSpace: "nowrap",
  },
  variants: {
    tone: {
      info: { color: "cyan", borderColor: "color-mix(in oklch, token(colors.cyan) 45%, transparent)" },
      affirm: { color: "mint", borderColor: "color-mix(in oklch, token(colors.mint) 45%, transparent)" },
      warn: { color: "amber", borderColor: "color-mix(in oklch, token(colors.amber) 45%, transparent)" },
      alarm: { color: "alarm", borderColor: "color-mix(in oklch, token(colors.alarm) 45%, transparent)" },
    },
  },
});
const text = css({ minWidth: "0", overflow: "hidden", textOverflow: "ellipsis" });
const spinner = css({
  flex: "none",
  // The ruling literal (stateGrammar.PULSE_ANIMATION) — Panda needs the string.
  animation: "pulseSlow 2.4s ease-in-out infinite",
  _motionReduce: { animation: "none" },
});
const chipButton = css({
  font: "inherit",
  fontSize: "0.62rem",
  flex: "none",
  paddingInline: "0.25rem",
  background: "transparent",
  color: "inherit",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "currentcolor",
  borderRadius: "2px",
  cursor: "pointer",
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});

export function AcceptanceChip({
  chip,
  onAcknowledge,
  onRetry,
  className,
}: {
  chip: SetChipModel;
  /** Wired only for chips that demand acknowledgment (clamp/unsupported/unknown/pair failure). */
  onAcknowledge?: () => void;
  /** Wired only for the retryable 503 route chip. */
  onRetry?: () => void;
  className?: string;
}) {
  return (
    <span
      className={cx(shell({ tone: chip.tone }), className)}
      data-testid={`set-chip-${chip.id}`}
      data-chip-kind={chip.kind}
      data-chip-acceptance={chip.acceptance}
      title={chip.text}
    >
      {chip.spinner ? (
        <span className={spinner} aria-hidden="true" data-testid={`set-chip-spinner-${chip.id}`}>
          ◐
        </span>
      ) : null}
      <span className={text}>{chip.text}</span>
      {chip.demandsAck && onAcknowledge ? (
        <Button
          className={chipButton}
          onPress={onAcknowledge}
          aria-label={`mark seen: ${chip.text}`}
          data-testid={`set-chip-ack-${chip.id}`}
        >
          mark seen
        </Button>
      ) : null}
      {chip.retryable && onRetry ? (
        <Button
          className={chipButton}
          onPress={onRetry}
          aria-label={`retry: ${chip.text}`}
          data-testid={`set-chip-retry-${chip.id}`}
        >
          retry
        </Button>
      ) : null}
    </span>
  );
}
