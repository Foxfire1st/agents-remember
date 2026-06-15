import { cva } from "../../styled-system/css";
import type { ActionAvailability } from "../types/projection";

// Display-only action affordance (slice 5c): renders enabled/disabled + the reducer's precomputed
// reason. Uses aria-disabled (not `disabled`) so the title tooltip still shows and the node is
// announced. No onClick, no POST — slice 06 wires enforcement; this never mutates.
const afford = cva({
  base: {
    font: "inherit",
    fontSize: "0.72rem",
    paddingInline: "0.45rem",
    paddingBlock: "0.12rem",
    borderRadius: "2px",
    borderWidth: "1px",
    borderStyle: "solid",
    background: "transparent",
    cursor: "default",
  },
  variants: {
    tone: {
      ready: { borderColor: "cyan", color: "cyan" },
      off: { borderColor: "grid", color: "oklch(0.55 0.02 250)" },
    },
  },
});

export function Affordance({ action }: { action: ActionAvailability }) {
  const title = action.enabled
    ? (action.nextSafeAction ?? action.action)
    : (action.disabledReason ?? "unavailable");
  return (
    <button
      type="button"
      aria-disabled="true"
      className={afford({ tone: action.enabled ? "ready" : "off" })}
      data-testid="affordance"
      title={title}
    >
      {action.action}
    </button>
  );
}
