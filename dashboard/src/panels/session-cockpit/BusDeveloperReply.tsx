import type { FormEvent } from "react";

import { css } from "../../../styled-system/css";
import { postOperatorInbox, type OperatorInboxPostRequest } from "../../data/operatorInbox";
import type { AgentPickupNode } from "../../types/projection";
import { inspectorAction } from "./InspectorPrimitives";

// The Bus pane's one write boundary. Keeping it named and isolated makes the invariant reviewable:
// this posts a new developer reply/decision; it never consumes or acknowledges the source row.

const meta = css({ color: "muted", overflowWrap: "anywhere" });
const errorMeta = css({ color: "alarm", overflowWrap: "anywhere" });
const form = css({
  display: "grid",
  gap: "0.3rem",
  marginTop: "0.25rem",
  paddingTop: "0.3rem",
  borderTopWidth: "1px",
  borderTopStyle: "solid",
  borderTopColor: "grid",
});
const textarea = css({
  width: "100%",
  minHeight: "4.5rem",
  resize: "vertical",
  font: "inherit",
  color: "ink",
  background: "bgPanel",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  padding: "0.35rem",
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});

export function developerReplyRequest(
  pickup: AgentPickupNode,
  response: string,
): OperatorInboxPostRequest | null {
  // The row's lifecycle/agent/role fields address its ORIGINAL recipient. A reverse reply can
  // only use the projected sender identity; mixing the two parties produces a mailbox key that
  // durable polling can never satisfy coherently.
  if (!pickup.senderAgentId && !pickup.senderRole) return null;
  const decision = pickup.messageKind === "decision-item";
  return {
    ...(pickup.senderAgentId ? { agentId: pickup.senderAgentId } : {}),
    senderRole: "developer",
    ...(pickup.senderRole ? { recipientRole: pickup.senderRole } : {}),
    ...(pickup.gateId ? { gateId: pickup.gateId } : {}),
    ...(pickup.artifactPath ? { artifactPath: pickup.artifactPath } : {}),
    deliverToHosted: true,
    messageKind: decision ? "decision-ruling" : "message",
    ask: decision
      ? `Developer decision for inbox ${pickup.entryId}`
      : `Developer reply to escalation ${pickup.entryId}`,
    response,
  };
}

export type BusReplyStatus = "idle" | "sending" | "posted" | "error";

export interface BusReplyState {
  open: boolean;
  draft: string;
  status: BusReplyStatus;
}

export const EMPTY_BUS_REPLY_STATE: BusReplyState = {
  open: false,
  draft: "",
  status: "idle",
};

export function BusDeveloperReply({
  pickup,
  state,
  updateState,
}: {
  pickup: AgentPickupNode;
  state: BusReplyState;
  updateState: (update: (current: BusReplyState) => BusReplyState) => void;
}) {
  const canAddress = Boolean(pickup.senderAgentId || pickup.senderRole);
  const decision = pickup.messageKind === "decision-item";
  const formId = `bus-reply-form-${pickup.entryId}`;
  const statusId = `bus-reply-status-${pickup.entryId}`;

  if (!canAddress) {
    return (
      <span className={meta} data-testid={`bus-reply-unavailable-${pickup.entryId}`}>
        developer reply unavailable — the projection carries no sender address
      </span>
    );
  }

  return (
    <div>
      <button
        type="button"
        className={inspectorAction}
        aria-expanded={state.open}
        aria-controls={formId}
        onClick={() => updateState((current) => ({ ...current, open: !current.open }))}
        data-testid={`bus-reply-toggle-${pickup.entryId}`}
      >
        {decision ? "record decision" : "reply to escalation"}
      </button>
      {state.open ? (
        <BusReplyForm
          pickup={pickup}
          state={state}
          formId={formId}
          statusId={statusId}
          decision={decision}
          updateState={updateState}
        />
      ) : null}
    </div>
  );
}

function BusReplyForm({
  pickup,
  state,
  formId,
  statusId,
  decision,
  updateState,
}: {
  pickup: AgentPickupNode;
  state: BusReplyState;
  formId: string;
  statusId: string;
  decision: boolean;
  updateState: (update: (current: BusReplyState) => BusReplyState) => void;
}) {
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const response = state.draft.trim();
    if (!response || state.status === "sending") return;
    const request = developerReplyRequest(pickup, response);
    if (!request) return;
    updateState((current) => ({ ...current, status: "sending" }));
    const result = await postOperatorInbox(request);
    updateState((current) => ({
      ...current,
      draft: result === "posted" ? "" : current.draft,
      status: result === "posted" ? "posted" : "error",
    }));
  };
  return (
    <form
      id={formId}
      className={form}
      aria-busy={state.status === "sending"}
      onSubmit={(event) => void submit(event)}
    >
      <label htmlFor={`bus-reply-${pickup.entryId}`}>
        {decision ? "Developer decision" : "Developer reply"} to the projected sender
      </label>
      <textarea
        id={`bus-reply-${pickup.entryId}`}
        name={decision ? "developerDecision" : "developerReply"}
        autoComplete="off"
        className={textarea}
        value={state.draft}
        disabled={state.status === "sending"}
        aria-describedby={state.status === "idle" ? undefined : statusId}
        onChange={(event) => {
          const draft = event.currentTarget.value;
          updateState((current) => ({ ...current, draft, status: "idle" }));
        }}
        data-testid={`bus-reply-input-${pickup.entryId}`}
      />
      <button
        type="submit"
        className={inspectorAction}
        disabled={state.draft.trim().length === 0 || state.status === "sending"}
        data-testid={`bus-reply-submit-${pickup.entryId}`}
      >
        {state.status === "sending" ? "posting…" : "post to operator inbox"}
      </button>
      <ReplyStatusSpan pickup={pickup} state={state} statusId={statusId} />
    </form>
  );
}

function ReplyStatusSpan({
  pickup,
  state,
  statusId,
}: {
  pickup: AgentPickupNode;
  state: BusReplyState;
  statusId: string;
}) {
  if (state.status === "sending") {
    return (
      <span
        id={statusId}
        className={meta}
        role="status"
        aria-live="polite"
        aria-atomic="true"
      >
        posting to the operator inbox…
      </span>
    );
  }
  if (state.status === "posted") {
    return (
      <span
        id={statusId}
        className={meta}
        role="status"
        aria-live="polite"
        aria-atomic="true"
        data-testid={`bus-reply-status-${pickup.entryId}`}
      >
        posted — recipient acknowledgment remains MCP-only
      </span>
    );
  }
  if (state.status === "error") {
    return (
      <span
        id={statusId}
        className={errorMeta}
        role="alert"
        aria-live="assertive"
        aria-atomic="true"
        data-testid={`bus-reply-status-${pickup.entryId}`}
      >
        POST /api/operator-inbox failed; the draft is retained — check the connection and
        retry
      </span>
    );
  }
  return null;
}
