# Job Variant — Orchestrator on Claude Code

> **Overlay, not a replacement.** This file overlays the portable `roles/orchestrator.md` when the
> orchestrator seat runs on the **claude-code** harness. It carries only what is harness-specific: the
> concrete knobs, the sub-agent fan-out mechanic, and the durable-report rule. It **does not restate**
> the orchestrator's duties, the spirit test, or the topology — read `roles/orchestrator.md` for those.
>
> Resolution: `roles/orchestrator.md` (base) → **this overlay** → settings.json orchestration block.

## Harness Knobs (override the base knob block)

| Knob    | Value on Claude Code                              |
| ------- | ------------------------------------------------- |
| harness | claude-code                                       |
| model   | the strongest available reasoning model (settings.json `orchestration.roles.orchestrator.model` names the concrete id) |
| effort  | high                                              |
| tools   | full tool surface + the `Agent`/`Task` sub-agent tool for fan-out |

## Sub-Agent Fan-Out With Durable Reports (the Claude Code idiom)

On Claude Code the orchestrator's portfolio analysis fans out through the **`Agent`/`Task` sub-agent
tool**. The invariant from the design record (addendum item 5) maps to this harness as follows:

- **Sub-agents WRITE durable report artifacts.** Dispatch each fan-out analysis (route-coherence scan,
  conflict/regression scan, per-designer adversarial review) as a sub-agent whose task is to **write** a
  templated report to a durable path (`../templates/impact-analysis.md`,
  `../templates/onboarding-coherency.md`) and return only a compact summary. The report is the artifact of
  record; the returned summary is not. This is what keeps the orchestrator's context from exploding and
  what makes the analysis survive compaction, a session clear, or termination.
- **A sub-agent that only returns prose is a bug.** If the analysis matters, it lands as a durable
  report file; a sub-agent may not be the sole holder of a finding.
- **AR state mutations STAY IN THE MAIN LOOP.** Sub-agents never call the mutating Agents Remember MCP
  tools — no `task_doc` writes, no gate decisions, no `spawn_agent_session`, no closeout. Those are the
  orchestrator's own main-loop calls, made after reading the sub-agents' durable reports. Sub-agents are
  read-and-write-reports actors; the orchestrator is the only mutator.
- **Fan-out is capped** by settings.json `orchestration.concurrency.maxSubAgents`.

## Continuing a Sub-Agent

Prefer continuing an existing sub-agent (its report already in flight) over spawning a fresh one for a
follow-up on the same analysis, so the durable report accretes rather than fragmenting across files.
