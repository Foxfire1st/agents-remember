# Message handover

## Message payload and files

Messages wake; files carry. A message says what is asked or what is handed over, and names the files: evidence, verdicts, lists and logs are files, never pasted into a message. The full text goes in `body` and its files in `attach`. A text holds at most the limit of the settings.

## When you finish a step another seat awaits

Whoever finishes a step that another seat waits on messages that seat. Name the finished object and its evidence file; a finished turn or a filed report alone is not that notification. Use the existing role/task route and send no duplicate message for a held handover.

## Held receipt and full-store refusal

Held means queued eventual delivery, possibly many turns later: never resend it; consumption needs the recipient artifact and superseded held messages are withdrawn. Full-store recipient-busy is a refusal: pending record, independent work, one resend before turn end, then remain pending. Keep noncircular Manager/parent routes and source-specific fields. A receipt is not acceptance.

Withdraw a superseded held message by `withdraw_held_message_id`. A receiver does not poll. A receipt or turn-end does not establish semantic acceptance. Bootstrap has no message-handover duty or pointer.

## Filed message and receipt scope

Messages are filed in the narrowest task shared by sender and recipient: same-leaf messages under `notes/messages/<leaf-slug>/`, shared master or sprint messages under that task's `notes/messages/`. The active enclosure holds its own receipts; otherwise receipts use the configured `transcriptRoot`. Search the complete applicable message folder and receipt scope before claiming that no message was sent.

A held message is queued and is not resent. A recipient-busy refusal is recorded and retried before ending the turn; report a second refusal under the standing message procedure.
