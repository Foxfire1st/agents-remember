import { useState, type MouseEvent } from "react";

import { css } from "../../styled-system/css";
import { dismissOperatorInboxEntry } from "../data/operatorInbox";
import type { AgentPickupNode } from "../types/projection";

const shell = css({
  flex: "0 0 auto",
  display: "inline-flex",
  alignItems: "center",
  gap: "0.25rem",
  minWidth: "0",
  maxWidth: "7.5rem",
  color: "amber",
  fontSize: "0.66rem",
});
const deliveryMarker = css({
  width: "0.65rem",
  height: "0.65rem",
  flex: "0 0 auto",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "amber",
  borderRadius: "2px",
});
const notice = css({
  minWidth: "0",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
});
const dismiss = css({
  flex: "0 0 auto",
  font: "inherit",
  color: "inherit",
  background: "transparent",
  borderStyle: "none",
  padding: "0",
  cursor: "pointer",
});

export function AgentPickupIndicator({ pickup }: { pickup: AgentPickupNode | undefined }) {
  const [dismissing, setDismissing] = useState(false);
  if (!pickup) return null;
  const expired = pickup.state === "check-chat";
  const subject = pickup.messageKind === "dispatch-brief" ? "brief" : "message";
  const pendingLabel = `${subject} unacknowledged`;
  const dismissPickup = (event: MouseEvent<HTMLButtonElement>) => {
    event.stopPropagation();
    if (dismissing) return;
    setDismissing(true);
    void dismissOperatorInboxEntry(pickup.entryId).finally(() => setDismissing(false));
  };
  return (
    <span
      className={shell}
      title={
        expired
          ? "Inbox acknowledgment overdue; check chat"
          : `Inbox delivery: ${pickup.deliveryState}; ${subject} awaiting acknowledgment`
      }
      aria-label={expired ? "Inbox acknowledgment overdue; check chat" : pendingLabel}
      data-testid="agent-pickup"
    >
      {expired ? null : <span className={deliveryMarker} aria-hidden="true" />}
      <span className={notice}>
        {expired ? "check chat" : pendingLabel}
      </span>
      {expired ? (
        <button
          type="button"
          className={dismiss}
          onClick={dismissPickup}
          disabled={dismissing}
          aria-label="Dismiss pickup warning"
          data-testid="agent-pickup-dismiss"
        >
          x
        </button>
      ) : null}
    </span>
  );
}
