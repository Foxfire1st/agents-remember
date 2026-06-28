import { motion } from "motion/react";
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
const spinner = css({
  width: "0.65rem",
  height: "0.65rem",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderTopColor: "amber",
  borderRadius: "999px",
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
          ? "The agent did not receive your approval, check your chat"
          : "Waiting for agent"
      }
      data-testid="agent-pickup"
    >
      {expired ? null : (
        <motion.span
          className={spinner}
          animate={{ rotate: 360 }}
          transition={{ duration: 0.9, ease: "linear", repeat: Infinity }}
          aria-hidden="true"
        />
      )}
      <span className={notice}>
        {expired ? "check chat" : "waiting for agent"}
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
