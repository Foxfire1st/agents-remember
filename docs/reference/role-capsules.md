# Role capsules, handovers and messages

A role capsule is the instruction text that a role agent receives at its start. It holds the text
of the role, the text of one operation that fits the assignment, and the rules that hold for every
role. An operation is an operation file of the skill `l-01-agent-lifecycles`: the instructions for
one kind of work, for example planning, a review or a closeout. The first message of a role agent
carries the capsule together with the [handover](../concepts.md#handover). The handover names the
assignment: the selected task documents, the working folders, the report folder and the parent.
[Roles and role agents](../roles.md) describes the roles.

The tool server serves the skill as MCP resources from the package. See
[Skills](skills.md) for the source tree and the generated copy.

## The files of a launch

The product stores the first message before it sends the start, so a role agent can read its
instructions again as plain files on any harness. The files lie in the folder `role-launch` of
the role agent's report folder; [Where a role agent's files lie](../roles.md#where-a-role-agents-files-lie)
names the report folder of each role:

| File ending | Content |
| --- | --- |
| `.handover.txt` | the text of the first message: the capsule and a short form of the handover |
| `.handover.json` | the complete handover |
| `.detail-<sha256>.md` | one file for each detail text: a reference file of the skill that a step of the instructions tells the role agent to read, copied whole |

The first message opens with one line that names the two handover files and the SHA-256 of the
text file. The answer of `role_start` names the text file in `handoverArtifactPath`, the JSON file
in `handoverFilePath` and the role agent's report file in `reportPath`.

## Held starts and held messages

A start can answer the status `held`: `role_start_set` holds the Reviewer and the Curator that it
starts, unless its `reviewer_message` opens the Reviewer at once. A held role agent exists in the
host and has taken no turn. The first message addressed to it opens its first turn, with its
handover. The role launcher of the dashboard shows the role of such a role agent with the mark
"(held)".

A held message is stored for delivery; it is not a refused send. The answer of `role_message`
then carries `status: "held"`, the reason in `heldReason`, the sender's waiting counts in
`waitingMine` and `waitingTotal`, and the id of the stored message in `heldMessageId`.

A held start and a held message are distinct states. Neither the answer of `role_start` nor the
answer of `role_message` shows that the recipient read the content. The sender's complete
procedure is
[Message handover](https://github.com/Foxfire1st/agents-remember/blob/main/skills/l-01-agent-lifecycles/reference/message-handover.md).
It tells the sender to say in `text` what it asks for and to name the files, to put the full text
in `body` and the files in `attach`, and to send once more a message refused with
`recipient-busy`; it also says where filed messages lie. The role capsule names the procedure at
the step of the role that it applies to. The instructions of Bootstrap hold no message-handover
duty and no pointer to that procedure.

A role agent's chat text is not a message. A role agent sends its results, and what it waits for,
with `role_message` to the role agent that waits. When a turn ends without a
`role_message` to the parent, the host's plug-in gives the parent one "turn ended" line. That
line carries the last line of the role agent's text, cut at 300 characters, and nothing else of
the chat. [Roles and role agents](../roles.md#when-a-turn-ends) describes the line and a turn
that ends without a [hand-off](../concepts.md#hand-off).

`role_message` promises only that the host accepted the message. Without `wait`, the call returns
when the host has accepted it. With `wait`, the call returns when the turn that consumed the
message ends, or after `timeout_seconds`: 300 unless the call names another value, and at most
1,800. A message to a recipient in a turn enters the running turn where the host can steer the
recipient's harness. Where the host replaced the running turn to take the message, the answer
carries `taken: "replaced"` and a `warning`. Neither the acceptance by the host nor the end of a
turn shows that the recipient agreed with the content.

## Filing and response evidence

Messages between the role agents of one task are filed in `notes/messages/<task-slug>/` in the
folder of the task's master, `<coordination-root>/tasks/<repository>/<master-folder>/`. A pair
whose narrowest shared task document is the master or the sprint uses its `notes/messages/`. The
whole result of the call, in the file that the answer names in `log`, names the filed bytes in
`messageFile` and `messageFileSha256`.

Before you conclude that a message was not sent, search the whole message folder and the tool log
in both of its places: the `logs/` of an active enclosure and the folder that the key
`transcriptRoot` of the settings file names, by default `<coordination-root>/logs/mcp/`.
The two places hold different calls. [MCP tools](mcp-tools.md#message-files-and-the-tool-log)
describes the filing and the fields of a held message, and
[Tool log](settings-json.md#tool-log) describes which call is logged where.
