# Task: Phase 00 Refactor Strategy Backdrop

**Status:** planning
**Repo:** agents-remember-md
**Type:** Docs | Architecture Strategy
**Created:** 2026-05-22T00:36

---

## Objective

Hold the shared refactoring strategy for the Agentic Context Kernel MCP work. This file is a living backdrop for the whole task, not a one-time implementation plan.

The strategy is to build toward smaller deterministic modules under `src/agents_remember/...`, with controller modules composing those services into higher-level operations such as the startup context packet. The domain layout is the important direction; individual file names are provisional until each domain has been inspected against the current scripts.

---

## Request And Deeper Request

### Surface Request

Create `phase-00-refactor-strategy.md` as a durable strategy artifact for the task. Include the intended `src/agents_remember/...` domain structure and capture specific context-packet direction.

### Deeper Request

Use the refactor strategy to prevent the MCP work from becoming a thin shell around today's painful startup scripts. The deeper goal is a cleaner architecture where deterministic core services, provider drivers, controllers, MCP tools, CLI wrappers, skills, and views each have distinct jobs.

### Highest-Leverage Framing

Use an MCP-driven vertical slice with minimal enabling refactor. Do not pause the project for a broad refactor campaign, and do not build the MCP directly on top of monolithic scripts as if those scripts were the final service boundary.

The first meaningful slice should be the context packet, because it exercises the startup path that is currently painful and proves whether the proposed controller/service separation actually improves day-to-day agent operation.

### Assumptions

- The current script surface remains useful as a compatibility and operator layer while internals are extracted.
- The project is pre-1.0, so fallback and compatibility code must be justified rather than assumed.
- GrepAI databases are replaceable speed caches, not durable memory or proof layers.
- Provider state is useful context, but source files, onboarding files, ledgers, and explicit evidence remain the proof layer.
- The context packet should be fast and bounded; it should not become a full validation, indexing, closeout operation, or a place to introduce unproven concepts.

### Boundaries

- Do not use this strategy as permission for broad mechanical cleanup such as global line-length churn.
- Do not expose arbitrary shell execution through MCP.
- Do not treat provider responses as authoritative without source/onboarding confirmation when correctness matters.
- Do not make `context.packet` depend on long-running indexing or watcher startup.
- Do not hide mutations inside read-only tools; mutation-capable operations must be explicit, typed, facade-backed, and first exercised in the isolated workbench.

---

## Requirements

- Keep this file as the living refactor backdrop for all phase tasks in this folder.
- Prefer vertical slices that extract only the services needed for the next durable MCP capability.
- Make `context.packet` the first architecture-proving slice.
- Treat the domain directories as the stable structure from the idea note, while treating proposed file names as candidates to validate per domain.
- Decompose each domain only after inspecting the relevant scripts and identifying useful responsibility boundaries.
- Keep controllers thin: controllers compose facts from services and shape responses; they do not own provider internals, path probing, or workflow doctrine.
- Keep services deterministic and testable without MCP transport.
- Keep provider drivers replaceable and cache-aware.
- Keep skills focused on workflow teaching and routing, not low-level process management.
- Keep CLI entry points as adapters over services and controllers, not the canonical business-logic home.
- Pin existing function/script contracts before extraction and treat those surfaces as facades while internals move behind them.
- Change code organization first, not behavior: every extracted service must preserve the current contract until a later task explicitly approves a contract change.

---

## Refactor Strategy

### Core Direction

The target shape is:

1. Extract deterministic Python services from the current scripts.
2. Compose those services through small controller modules.
3. Expose selected controller operations through MCP tools.
4. Keep existing CLI/script commands as wrappers over the same services where useful.
5. Rewire skills to prefer MCP-backed operations once the read surface is stable.
6. Move mutation-heavy lifecycle actions behind services only after the read-first boundary is proven.

This keeps momentum while avoiding two weak paths: refactoring everything before the MCP exists, or building an MCP that only automates the current script sprawl.

### Contract-Pinned Facade Rule

The current resolver, provider lifecycle, provider setup, drift, and worktree entrypoints are the safest contract evidence the project has today. Before refactoring a domain, the phase should pin the current observable contract with tests, fixtures, or recorded response-shape examples. The existing public function/script surface then becomes a facade over the newly extracted service code.

This rule keeps the refactor low-risk: callers keep seeing the same behavior while implementation moves into smaller modules. Contract changes are allowed only as explicit phase decisions, not as accidental side effects of decomposition.

### Separation Of Concerns

| Layer            | Owns                                                                                  | Does Not Own                                                                      |
| ---------------- | ------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| Skills           | Workflow doctrine, routing choices, human-facing escalation rules.                    | Provider process management or deterministic repo facts.                          |
| MCP tools        | Safe host-side operation boundary and typed model-facing API.                         | Arbitrary shell execution or business logic hidden in handlers.                   |
| Controllers      | High-level composition such as `context.packet` or provider query orchestration.      | Low-level path probing, watcher management internals, indexing logic.             |
| Core services    | Deterministic facts, settings resolution, branch/worktree state, and drift summaries. | Transport formatting or skill prose.                                              |
| Provider drivers | CGC/GrepAI lifecycle, health, and query contracts.                                    | Durable memory semantics, source-of-truth decisions, or MCP run-artifact storage. |
| CLI/scripts      | Operator commands and backward-compatible entry points.                               | Primary architecture ownership after extraction.                                  |
| Views/harnesses  | Dashboards, test harnesses, human inspection surfaces.                                | Workflow truth or provider authority.                                             |

### Extraction Order

1. Stabilize the package skeleton and import path.
2. Extract read-only resolver/context services needed by `context.packet`.
3. Build a controller that composes those services into one bounded startup response.
4. Add CLI or test harness coverage for the controller before MCP transport carries it.
5. Expose the read-only MCP operation.
6. Wire additional provider/worktree operations one by one through pinned facades, using the workbench for destructive or stateful tests.
7. Rewire skills toward stable MCP operations after their names and response shapes are proven.

---

## Intended Domain Structure

This is the working domain structure, not a frozen file-by-file spec. The idea document got the domains right. The tree below is the target package domain skeleton, not a move list for current scripts.

Actual decomposed modules should be decided domain by domain after reading the relevant current files and finding useful responsibility boundaries.

```text
runtime/
  src/
    agents_remember/
      __init__.py

      kernel/
      controllers/
      providers/
      drift/
      worktrees/
      tasks/
      mcp/
        # MCP transport, schemas, safety, and tool registration.
        # Candidate modules: server.py, tools.py, schemas.py, safety.py

      cli/
        # Thin command adapters over controllers/services.
        # Candidate modules: context_packet.py, provider_lifecycle.py,
        # worktree.py
```

### Current Source Domain Map

These current files are evidence sources for each domain. They are not destination paths, and setup/install entrypoints should not be moved wholesale into `src/`.

| Domain         | Current source files                                                                                                | Extraction stance                                                                                               |
| -------------- | ------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| `kernel/`      | `runtime/skills/U-01-core-skills/C-08-ar-coordination-context-resolver/scripts/ar_coordination_context_resolver.py` | Source for context-resolution services.                                                                         |
| `kernel/`      | `runtime/skills/U-01-core-skills/_shared/agents_remember/memory_ledger.py`                                          | Current shared memory-ledger module; exact future domain can be revisited when memory boundaries are inspected. |
| `kernel/`      | `runtime/skills/U-01-core-skills/_shared/agents_remember/route_index.py`                                            | Current shared route-index module; keep as evidence until route/index boundaries are inspected.                 |
| `controllers/` | None yet.                                                                                                           | Introduce only when an actual composed operation is extracted, such as session start/context packet.            |
| `providers/`   | `runtime/scripts/provider-lifecycle.py`                                                                             | Current provider lifecycle/operator script; extract reusable provider services only after inspection.           |
| `providers/`   | `runtime/scripts/provider-setup.py`                                                                                 | Setup entrypoint; should stay a script, with only reusable provider setup internals extracted if justified.     |
| `providers/`   | `runtime/skills/U-01-core-skills/_shared/agents_remember/context_providers.py`                                      | Current shared provider layout/patch helper module.                                                             |
| `drift/`       | `runtime/skills/U-01-core-skills/C-02-onboarding-drift-detection/scripts/check_onboarding_drift.py`                 | Existing drift-check script; further split only after inspection.                                               |
| `worktrees/`   | `runtime/skills/U-01-core-skills/C-09-git-worktree-manager/scripts/git_worktree_manager.py`                         | Current task Git/worktree lifecycle script; extract only stable services.                                       |
| `worktrees/`   | `runtime/skills/U-01-core-skills/_shared/agents_remember/worktree_contract.py`                                      | Current shared worktree contract module.                                                                        |
| `tasks/`       | `runtime/skills/U-01-core-skills/C-05-create-or-update-onboarding-files/scripts/build_route_indexes.py`             | Current route-index generation entrypoint; future domain may move after route/index inspection.                 |
| `tasks/`       | `runtime/skills/U-01-core-skills/C-10-adopt-memory-baseline/scripts/adopt_memory_baseline.py`                       | Current memory-baseline task script.                                                                            |
| `tasks/`       | `runtime/skills/U-01-core-skills/C-11-memory-carryover-from-branch/scripts/memory_carryover.py`                     | Current branch-memory carryover task script.                                                                    |

Installation/support entrypoints such as `installer/install-runtime.py`, `runtime/scripts/install-skills.sh`, and benchmark harnesses are not package-domain modules. They may call package services later, but they should remain installer/support scripts unless a separate task deliberately changes that.

### Structure Notes

- `kernel/` owns reusable deterministic facts and core context assembly primitives.
- `controllers/` owns model-facing operation composition, including the context packet.
- `providers/` owns provider-specific state, health, lifecycle, and query contracts.
- `drift/` currently owns the existing drift check. Further splitting requires script evidence.
- `worktrees/` owns worktree state and closeout facts, not Git side effects hidden inside unrelated modules.
- `mcp/` owns transport, schemas, safety gates, and tool registration.
- `cli/` owns command adapters that call into controllers/services.
- Non-`mcp`/`cli` domains list current source files only. Candidate module names should be added only when script inspection shows they represent useful responsibilities.
- If a script's real decomposition differs from the current source grouping, prefer the script evidence over this placeholder map and record the decision here.
- Setup, install, and support scripts are allowed to remain script entrypoints outside `src/` while importing extracted services later.
- `transcripts.py` is not accepted as a provider-domain module from current evidence. The term appears to come from the old MCP/read-surface idea of preserving raw provider command output or large run artifacts; if that feature survives, it should be placed after inspecting the MCP/read-surface and artifact-storage design.

---

## Context Packet Strategy

### Purpose

The context packet is the first architecture-proving slice. It should turn the painful start process into one bounded operation that returns the facts an agent needs before deciding what to read, which providers to trust, and which actions are safe.

It is not meant to replace deep research, provider indexing, task closeout, or source verification.

### Target Operation

```text
context.packet(repo: path | name, options?: { includeProviders?: bool, includeDrift?: bool })
```

The exact MCP name can be decided in Phase 1/2, but the operation should be conceptually stable.

### Packet Contents

The packet should compose only concepts that are already real in the repository:

- repo identity, source root, current branch, and coordination root
- memory root, onboarding root, ledger location, and task root
- worktree state and branch/workflow state
- provider configuration, watcher state, freshness, and `mayUse` flags
- GrepAI cache presence without treating it as durable memory
- drift status when requested

The packet must not include separate `warnings`, `recommendations`, or `provenance` sections unless a later domain inspection proves those concepts exist in the current scripts or a deliberate design decision introduces them.

### Minimal Shape

Session start / Context packet in principle combain the outputs of existing functions.

What the package does is combine the responses of scripts that would have been before individually triggered to get that response. Which adds a lot of latency from reasoning.
So those executions where they are always the same and in the same order, can just get bundled in a wrapper script reducing several consequitive executions to one.

In regards to the modularisation. That is also very easy. Every existing function shall keep their contract but turn themselves into a facade. The refactor happens behind that facade.
And all that needs to be done is that the contract stay true.

```json

{
  "coordination_context": ar_coordination_context_resolver.py (code repo, coordination, memory),
  "provider_status": provider-lifecycle.py watchers status,
  "drift_status": provider-lifecycle.py (watcher status / watcher start),
}

```

```json
{
  "repo": {
    "name": "agents-remember-md",
    "root": "C:/ew/agents-remember-md",
    "branch": "feature/example"
  },
  "coordination": {
    "root": "C:/ew/ar-coordination",
    "taskRoot": "C:/ew/ar-coordination/tasks/agents-remember-md"
  },
  "memory": {
    "root": "C:/ew/ar-coordination/memory/agents-remember-md",
    "onboardingRoot": "C:/ew/ar-coordination/memory/agents-remember-md/onboarding",
    "ledger": "C:/ew/ar-coordination/memory/agents-remember-md/system/ledger.jsonl"
  },
  "providers": {
    "configured": true,
    "mayUse": true,
    "items": [
      {
        "id": "cgc",
        "watcher": "running",
        "freshness": "unknown"
      },
      {
        "id": "grepai",
        "watcher": "running",
        "freshness": "replaceable-cache"
      }
    ]
  },
  "drift": {
    "status": "notChecked"
  }
}
```

### Controller Composition

The context-packet controller should orchestrate services roughly like this:

```python
def build_context_packet(request: ContextPacketRequest) -> ContextPacket:
    resolved = resolver.resolve_context(request.repo)
    branch = branches.inspect(resolved.repo_root)
    providers = provider_status.inspect(resolved.coordination_root, resolved.repo_root)
    drift = drift_check.inspect(resolved, enabled=request.include_drift)

    return context_packet_model.from_parts(
        resolved=resolved,
        branch=branch,
        providers=providers,
        drift=drift,
    )
```

This example is intentionally schematic. The important point is that the controller composes smaller services and shapes a typed result; it should not bury process startup, provider parsing, path discovery, policy decisions, or invented helper concepts inline.

### Relationship To Session Start

`session_start.py` and `context_packet.py` should not be duplicate names for the same operation.

`session_start.py` is the actionful bootstrap controller for beginning a conversation. It may resolve the repository context, inspect provider configuration, check or start watchers when policy allows, run the normal drift check, collect resulting statuses, and return one JSON result so the agent does not have to stitch together several tool calls manually.

`context_packet.py` is the reusable packet contract and builder. It defines what the startup snapshot contains, how facts are normalized, and how provider/worktree/drift facts are shaped for model consumption. It should be callable without starting watchers or mutating process state.

A practical response shape is:

```json
{
  "operation": "session.start",
  "actions": [
    {
      "kind": "provider.watcher.start",
      "provider": "cgc",
      "status": "started"
    }
  ],
  "contextPacket": {
    "repo": {},
    "memory": {},
    "providers": {},
    "worktree": {},
    "drift": {}
  }
}
```

This keeps `session_start` focused on the use case and keeps `context_packet` as the stable view model that other read-only tools can reuse.

---

## Implementation Steps

### S1 - Keep The Strategy Current

- [ ] Update this document when later phases make a material architectural decision.
  - [ ] Record the decision in the Decision Log.
  - [ ] Update the intended domain structure when domain boundaries change.
  - [ ] Promote candidate module names only after domain-specific script inspection.
  - [ ] Keep the context-packet contract aligned with implemented reality.

### S2 - Use This As A Phase Gate

- [ ] Before each implementation phase, compare the proposed work against this strategy.
  - [ ] Confirm the phase is extracting a bounded vertical slice.
  - [ ] Confirm controller/service/provider responsibilities stay separated.
  - [ ] Confirm any fallback or compatibility code is explicitly justified.
  - [ ] Confirm current contract surfaces are pinned and preserved as facades before internals are moved.

### S3 - Validate The First Slice

- [ ] Treat `context.packet` as the first proof of the architecture.
  - [ ] Confirm it can be tested without MCP transport.
  - [ ] Confirm MCP can expose it without adding business logic to the handler.
  - [ ] Confirm it makes startup materially less painful for an agent.

---

## Proposed Code Examples

### E1 - Context Packet Controller Shape

Distinct change covered: controller composes smaller services into a typed startup packet.

Why this example is included: this is the central architecture pattern the refactor should prove before broader provider lifecycle work starts.

```python
def build_context_packet(request: ContextPacketRequest) -> ContextPacket:
    resolved = resolver.resolve_context(request.repo)
    branch = branches.inspect(resolved.repo_root)
    providers = provider_status.inspect(resolved)
    drift = drift_check.inspect(resolved, request.include_drift)
    return ContextPacket.from_parts(resolved, branch, providers, drift)
```

### E2 - Provider Boundary Shape

Distinct change covered: provider state is exposed through typed service results, not raw script output.

Why this example is included: provider health and watcher state are needed by the context packet, but provider internals should stay behind a driver/service boundary.

```python
@dataclass(frozen=True)
class ProviderHealth:
    provider_id: str
    configured: bool
    watcher_state: str
    may_use: bool
    freshness: str
```

---

## Decision Log

| Date-Time        | Decision                                                                         | Rationale                                                                                                                                                     |
| ---------------- | -------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 2026-05-22T00:36 | Use an MCP-driven vertical slice with minimal enabling refactor.                 | This avoids both a broad refactor-first pause and an MCP surface that merely wraps monolithic scripts.                                                        |
| 2026-05-22T00:36 | Make `context.packet` the first architecture-proving slice.                      | Startup is currently painful, and the packet exercises resolver, worktree, provider, and drift boundaries in one bounded read-only operation.                 |
| 2026-05-22T00:36 | Treat GrepAI as a replaceable speed cache.                                       | GrepAI helps semantic discovery, but durable memory and proof remain in onboarding, source, and ledgers.                                                      |
| 2026-05-22T00:41 | Treat the proposed package tree as domain-stable but file-provisional.           | The idea note captured the right domains, but useful module decomposition can only be decided after inspecting each domain's current scripts.                 |
| 2026-05-22T00:46 | Split `session_start.py` from `context_packet.py`.                               | `session_start` is the actionful conversation bootstrap; `context_packet` is the reusable read-only packet contract and builder that startup can return.      |
| 2026-05-22T00:47 | Remove the artificial fast/full drift-check distinction.                         | Drift checks are expected to be fast; heavier maintenance work should not be modeled as a second drift-check class unless code evidence proves that boundary. |
| 2026-05-22T00:51 | Remove unproven `warnings`, `recommendations`, and `provenance` packet sections. | The packet must not sneak in concepts that are not already present in the repository or explicitly accepted through a later design decision.                  |
| 2026-05-22T00:53 | Remove `transcripts.py` as a presumed provider-domain module.                    | Transcript language comes from MCP/provider-output artifact handling, not from an inspected current provider script boundary.                                 |
| 2026-05-22T00:55 | Keep the drift domain to `drift_check.py` for now.                               | Summary/report modules are not remembered as current concepts and should not be introduced before inspecting the actual drift-check script.                   |
| 2026-05-22T00:59 | Treat the domain map as evidence, not a move list.                               | Existing setup/install/support scripts should remain script entrypoints outside `src/`; only reusable internals should move after domain inspection.          |
| 2026-05-22T11:30 | Use contract-pinned facades for refactoring.                                     | Existing function/script contracts should remain stable while internals are decomposed, so behavior and organization do not change in the same step.          |
| 2026-05-22T12:05 | Replace the blanket mutation delay with explicit workbench-gated mutation wiring. | The isolated coordinator workbench makes destructive/stateful behavior testable; MCP safety should focus on typed facades and visible contracts, not read-only-only scope. |

---

## Open Questions

- Should the first package layout live under `runtime/src/agents_remember/`, or should Phase 1 introduce a different source-root convention after inspecting packaging constraints?
- Should `context.packet` first ship as a CLI/test harness operation before MCP transport, or should CLI and MCP land in the same phase once the service/controller boundary exists?
- Which fields are mandatory in the first packet version, and which should be opt-in to keep startup fast?

---

## References

- `C:/ew/ar-coordination/tasks/agents-remember-md/260521_provider-lifecycle-mcp-bridge/task.md`
- `C:/ew/ar-coordination/tasks/agents-remember-md/260521_provider-lifecycle-mcp-bridge/phase-00-quality-baseline.md`
- `C:/ew/ar-coordination/tasks/agents-remember-md/260521_provider-lifecycle-mcp-bridge/phase-00-quality-findings.md`
- `C:/ew/ar-coordination/tasks/agents-remember-md/260521_provider-lifecycle-mcp-bridge/phase-02-context-packet.md`
- `C:/ew/ar-coordination/tasks/agents-remember-md/260521_provider-lifecycle-mcp-bridge/agentic-context-kernel-mcp-design-note.md`
