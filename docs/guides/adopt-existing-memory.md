# Adopt Existing Memory

Use this guide when an external memory repo already exists and should become the first ledgered memory baseline.

Convert first: when the memory is still in the legacy format (no `knowledge/layout.json`), every ordinary memory operation refuses it with the code `legacy-format`. Follow [Make the First Conversion](first-conversion.md) before adopting — case A for memory with no attributed baseline, case B for memory with an official line. `memory_baseline_status` answers the state `legacy-format` until the conversion is done.

## Purpose

`c-10-adopt-memory-baseline` turns current external-memory onboarding into the first `memory.md` baseline. It does not refresh stale onboarding. It records that the current memory content is accepted as matching a code commit.

## Before Adoption

Resolve the target repository through `context_packet` and check drift using the [knowledge command and tool reference](../../skills/l-01-agent-lifecycles/reference/knowledge/commands.md).

If drift is actionable, choose one path:

- have the Curator refresh affected onboarding through the [card procedure](../../skills/l-01-agent-lifecycles/reference/curator/card-formats-and-citations.md) before adoption
- explicitly accept directional drift when the developer decides the current memory is factual enough to baseline

Do not use adoption to hide uncertainty.

## Status Check

Ask the agent to run the `c-10-adopt-memory-baseline` skill in status mode for the target repository. The status should report whether the memory repo is ready, already ledgered, missing, or blocked by drift.

## Adopt

When ready, ask the agent to run the `c-10-adopt-memory-baseline` skill adoption. It uses the existing `memory_baseline_adopt` tool for the memory-content commit and computed ledger refresh; see [the baseline tool contract](../../skills/l-01-agent-lifecycles/reference/knowledge/commands.md).

## After Adoption

Future external-memory work runs in paired worktrees through the [authorized lifecycle operation](../../skills/l-01-agent-lifecycles/operations/closeout.md). The ledger gives that flow a known baseline for code-memory compatibility.
