# Conversation-Handover-Packet Template

**One schema, three uses** (design record, the COMMS model): **role takeover** (profile-fit — the frame
spawns the correct profile and hands over), **worker respawn** (a fresh worker continues a leaf), and
any **master-complete handover** that needs the receiver to onboard from **state, not the transcript**.
The receiver always onboards from this packet, never from a prior conversation.

## Rules

1. The receiver must be able to act from this packet **alone** — assume the transcript is gone.
2. Name the request, the decisions already made, the constraints, the links, and the open questions.
3. For a **takeover spawn**, state the profile mismatch that triggered it, so the successor knows why it
   exists and does not repeat the wrong-profile work.

## Shape

```md
# Conversation Handover — <use: role-takeover | worker-respawn | master-handover>

| Field         | Value                                             |
| ------------- | ------------------------------------------------- |
| use           | role-takeover | worker-respawn | master-handover   |
| seat / role   | designer | orchestrator | manager | worker | reviewer |
| from          | <handing-over agent/lifecycle id> | (developer)   |
| to            | <successor profile: harness/model/effort>         |
| leaf / master | <task_doc path this seat owns>                    |
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
