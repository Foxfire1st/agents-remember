# Harness Capability Matrix — Observable Lifecycle

The observable lifecycle (design `observable-lifecycle.md`) relies on three
harness capabilities. This matrix records what is **verified** per harness so the
ambient-attribution and return-channel mechanics degrade knowingly, not silently.

## Capabilities

1. **Ambient stdio scoping** — one stdio MCP server process ≈ one harness session,
   so the process-singleton ambient lifecycle (`observer/ambient.py`) attributes
   every tool call to the right session. If a harness shared one server process
   across concurrent sessions, ambient attribution would cross-contaminate and an
   explicit `lifecycle_id` override would be required instead.
2. **Background-completion wake** — the harness re-invokes the model when a
   backgrounded task exits, so the return channel can wake a blocked lifecycle
   without active polling.
3. **Sleep / loop** — the model can actively poll or wait on a timer (the
   alternative return path when a harness has no background-completion wake).

## Matrix

| Harness | Ambient stdio scoping | Background-completion wake | Sleep / loop |
| --- | --- | --- | --- |
| Claude Code | ✅ verified — one stdio server per session | ✅ verified 2026-06-12 | ✅ `ScheduleWakeup` / `/loop` |
| Codex CLI | ⚠️ unverified | ⚠️ unverified | ⚠️ unverified |
| Cursor | ⚠️ unverified | ⚠️ unverified | ⚠️ unverified |
| Other MCP hosts | ⚠️ unverified | ⚠️ unverified | ⚠️ unverified |

✅ = confirmed; ⚠️ = not yet verified — assume the capability is **absent** until a
row is confirmed.

## Verification method

For ambient scoping, confirm the host launches a **fresh** stdio server process
per session rather than sharing one long-lived server: open two sessions, emit a
tool call in each, and confirm each lands in its own `lifecycles/<id>/events.jsonl`
log. Record the result and date in the row when verified. Until a row is verified,
the safe assumption is that the capability is absent — the lifecycle then falls
back to explicit attribution (no ambient) and a polling return channel (no
background wake).
