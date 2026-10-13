# Conversation-Handover-Packet Template

**One schema, three uses** (design record, the COMMS model): **role takeover** (profile-fit — the orchestrator's
Profile check (takeover) — `roles/orchestrator.md`, The Event Loop — spawns the correct profile and hands over), **worker respawn** (a fresh worker continues a leaf), and
any **master-complete handover** that needs the receiver to onboard from **state, not the transcript**.
The receiver always onboards from this packet, never from a prior conversation.

## Rules

1. The receiver must be able to act from this packet **alone** — assume the transcript is gone.
2. Name the request, the decisions already made, the constraints, the links, and the open questions.
3. For a **takeover spawn**, state the profile mismatch that triggered it, so the successor knows why it
   exists and does not repeat the wrong-profile work.
4. Hand the packet over as a file. Messages wake; files carry. A message says what is asked or what is handed over, and names the files: evidence, verdicts, lists and logs are files, never pasted into a message. The full text goes in `body` and its files in `attach`; the tool files every message in the `messages` folder of the narrowest task the two agents share, and a leaf workspace links that folder as `messages`. The recipient reads the message and, where a file belongs to it, one last line naming the file and its SHA-256. A text holds at most the limit of the settings. A return goes to the parent's role with its task references, never to an id read from an earlier message.

## Shape

```md
# Conversation Handover — <use: role-takeover | worker-respawn | master-handover>

| Field         | Value                                             |
| ------------- | ------------------------------------------------- |
| use           | role-takeover | worker-respawn | master-handover   |
| seat / role   | designer | orchestrator | manager | worker | reviewer |
| from seat     | <task_doc path + role> | developer                |
| to seat       | <task_doc path + role; profile is settings-owned> |
| task document | <canonical sprint / master / leaf task_doc path>  |
| written       | <YYYY-MM-DDTHH:MM>                                 |

## Why This Handover
- role-takeover: profile mismatch — wanted <role knobs>, session was <actual>; taking over on the right profile.
- worker-respawn: <session died / compacted / leaf continued>.
- master-handover: <master complete → next seat>.

## The Request (as agreed)
- <the developer-agreed frame / the leaf plan / the master objective>

## Decisions Already Made
- <decision> (decision-log ref …)

## Constraints & Invariants
- <what must remain true / must not regress / boundaries>

## Links (onboard from these, not the transcript)
- task_doc: <path>   · integration branch: <ref>   · prior turn report(s): <ref>
- durable notes / reports: <paths>

## Open Questions For The Successor
- <the short list only the successor or the developer can resolve>

## Current State
- Position in the plan / DAG:
- Committed vs uncommitted:
- The one thing to know before the next action:
```
