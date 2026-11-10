# Role capsules and message handovers

A role's launch delivers its role and operation instructions with the selected task and the
handover file locations. Skills are read from the serving package's MCP resources; there is no
public capsule-compilation tool or installed harness skill copy to edit. See
[Skills](skills.md) for the canonical and generated source trees.

## Launch and message holds

A held launch seat has not opened its first turn. A held message is queued, not a refused send;
the role-message response names the reason, waiting counts and message id. These are distinct
states: a receipt about a seat or message is not evidence that the recipient consumed its content.
The sender's complete procedure is
[Message handover](../../skills/l-01-agent-lifecycles/reference/message-handover.md), delivered at
the applicable role step. Bootstrap receives no message-handover duty or pointer.

Only the host request is promised. Without `wait`, host acceptance ends the call; with `wait`,
the consuming turn's end ends it. A mid-turn message may reach the running turn, which the host
may cancel. Receipt/turn-end is not semantic acceptance.

## Filing and response evidence

Same-leaf messages are filed in `notes/messages/<leaf-slug>/`; the shared master or sprint uses
`notes/messages/` when it is the narrowest shared task. Read the returned `messageFile` and
`messageFileSha256`, and search the complete folder and applicable receipts before claiming no
send. Active-enclosure receipts and the configured `transcriptRoot` have different scopes.
[MCP tools](mcp-tools.md#message-files-and-receipts) documents the source-specific queue fields,
filing and receipts; [Settings](settings-json.md#mcp-fields) documents receipt scope.
