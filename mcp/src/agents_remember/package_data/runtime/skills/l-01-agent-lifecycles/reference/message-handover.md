# Message handover

Roles read the sections named by their role before composing a message, handing work to another seat or handling its delivery receipt. It defines what to send, how to handle refused delivery, and what evidence establishes consumption.

## Send a message that names the files

Messages wake; files carry. Say what you ask for or hand over, and name the files. Evidence, verdicts, lists and logs are files; never paste them into a message.

Put the full message text in `body` and its files in `attach`. Keep the short `text` field within the settings' limit.

## Handle a full-store refusal

Full-store recipient-busy is a refusal:

1. Record the handover as pending and continue independent work.
2. Resend once before the turn ends.
3. If it is refused again, report the second refusal and leave it pending.

Report the second refusal through the route named by your role, using its recipient and required notice fields. Never address the notice to yourself. A receipt or turn-end does not establish semantic acceptance.

## Find filed messages and receipts

### Message folder

Messages are filed in the narrowest task shared by sender and recipient:

- same-leaf messages: `notes/messages/<leaf-slug>/`;
- shared-master or shared-sprint messages: that task's `notes/messages/`.

A leaf workspace links that folder as `messages`.

### Receipt scope

The active enclosure holds its own receipts. Otherwise, receipts use the configured `transcriptRoot`. Before claiming that no message was sent, search the complete applicable message folder and receipt scope.
