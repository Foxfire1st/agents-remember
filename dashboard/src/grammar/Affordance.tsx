import type { ActionAvailability } from "../types/projection";

// Display-only action affordance (slice 5c): renders enabled/disabled + the reducer's
// precomputed reason. Uses aria-disabled (not `disabled`) so the title tooltip still shows and
// the node is announced. No onClick, no POST — slice 06 wires enforcement; this never mutates.
export function Affordance({ action }: { action: ActionAvailability }) {
  const title = action.enabled
    ? (action.nextSafeAction ?? action.action)
    : (action.disabledReason ?? "unavailable");
  return (
    <button
      type="button"
      aria-disabled="true"
      className={`afford afford--${action.enabled ? "ready" : "off"}`}
      data-testid="affordance"
      title={title}
    >
      {action.action}
    </button>
  );
}
