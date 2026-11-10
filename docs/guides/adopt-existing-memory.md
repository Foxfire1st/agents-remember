# Adopt Existing Memory

Use this guide when an external memory repo already exists and should become the first ledgered memory baseline.

Convert first: when the memory is still in the legacy format (no `knowledge/layout.json`), every ordinary memory operation refuses it with the code `legacy-format`. Follow [Make the First Conversion](first-conversion.md) before adopting — case A for memory with no attributed baseline, case B for memory with an official line. `memory_baseline_status` answers the state `legacy-format` until the conversion is done.

## Purpose

Baseline adoption turns the current external-memory onboarding into the first `memory.md` baseline. It does not refresh stale onboarding. It records that the current memory content is accepted as matching a code commit.

Adoption commits real memory content and then computes the ledger cache; it never commits the cache. A missing, stale or malformed `memory.md` is a cache state, not evidence that a baseline exists or is absent.

## Before Adoption

Resolve the target repository through `context_packet` and classify drift with
`context_packet(include_drift=true)`, using the
[knowledge command and tool reference](../../skills/l-01-agent-lifecycles/reference/knowledge/commands.md).
The classification is returned in the packet and files no temporary report. In a leaf enclosure,
the contract-scoped result is the checklist `memory_quality_check` replaces at the `reportPath`
it returns, the enclosure's `reports/curator-memory-quality.md`.

If drift is actionable, choose one path:

- have the Curator refresh affected onboarding through the [card procedure](../../skills/l-01-agent-lifecycles/reference/curator/card-formats-and-citations.md) before adoption
- explicitly accept directional drift when the developer decides the current memory is factual enough to baseline

Do not use adoption to hide uncertainty. Use `memory_baseline_status` first, and use
`memory_baseline_adopt` only after the developer approves the baseline decision.

## Status Check

Ask the agent to run the baseline status for the target repository. Inspect the reachable memory
commits for an existing `Code-Commit:` attribution. The states are:

- `ready`: no attributed memory baseline exists and drift is clean enough to adopt.
- `blocked-drift`: drift has actionable findings and `accept_drift=true` was not supplied.
- `unavailable`: a current memory HEAD exists but its history cannot be read; adoption waits for
  readable Git history.
- `already-adopted`: reachable memory history already carries code attribution.
- `would-adopt`: a dry run would create the baseline. It reports this only when the staged-tree
  validation would pass; a refusal or a dry run leaves the index, the refs and every file as
  they were.
- `adopted`: the attributed memory-content baseline was committed and the cache refresh was
  attempted.

Cache presence does not determine adoption: only reachable `Code-Commit:` attribution does. A
missing, stale or malformed `memory.md` does not contradict an `already-adopted` answer.

## Adopt

When ready, preview and then adopt:

```text
memory_baseline_adopt(repo_id="<repo-id>", accept_drift=false, dry_run=true)   # preview
memory_baseline_adopt(repo_id="<repo-id>")                                     # apply
```

Adoption commits the memory content on the configured memory default branch. Existing
`onboarding/`, `system/` and `docs/` become one memory-content commit with a `Code-Commit:`
trailer; `memory.md` is computed afterward without another commit. `accept_drift=true` lifts the
drift gate only; a validator violation still refuses.

Read `memory_baseline_status` before adoption, put the drift-acceptance decision to the
developer rather than assuming it, adopt through `memory_baseline_adopt`, then read
`memory_baseline_status` again and report both answers. Baseline adoption is valid only before
attributed memory exists, so an `already-adopted` report means this half is already done.

The `onboarding/bootstrap/` working files ship with the first baseline commit as the run's audit
trail: adoption stages the whole onboarding tree. Whether they are removed beforehand is the
developer's decision; adoption carries them either way. Only a memory-root-level `bootstrap/`
staging directory is transient and excluded from every memory commit.

## After Adoption

Future external-memory work runs in paired worktrees through the [authorized lifecycle operation](../../skills/l-01-agent-lifecycles/operations/closeout.md). The ledger gives that flow a known baseline for code-memory compatibility.

Adopting a baseline is not the same as recording the repository's knowledge foundation. Report
the foundation's state separately, and hand its making to the user-started Architect.
