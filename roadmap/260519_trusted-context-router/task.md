# Task: C-04 Trusted Context Router

**Status:** planning
**Repo:** agents-remember-md
**Type:** Skill | Script | Config | Docs
**Created:** 2026-05-19T13:05

---

## Objective

Redesign C-04 from an onboarding-read protocol into a trusted context routing skill. The skill should route agents across optional discovery providers such as GrepAI, CodeGraphContext, deferred Graphify support, and future tools while preserving Agents Remember as the verification, drift, branch-validity, source-proof, and memory-promotion layer.

---

## Requirements

- Models are instructed to use a discovery provider based on what they need to know.
- Discovery providers are part of three categories: relationship (e.g. CodeGraphContext, deferred Graphify support), semantic (e.g. GrepAI), and path-based truth layer (onboarding).
- Agents Remember must be positioned as complementary to specialized retrieval/index providers, not as a competitor to them.
- Retrieval/index providers may reduce discovery cost, token pressure, and search friction, but the implementation must not promise wall-time speedups as the main value claim.
- Provider results are candidate-routing evidence only; source files, verified onboarding, drift checks, branch validity, and route indexes remain the proof layer.
- GrepAI, Graphify, and future tools must be optional provider profiles/instruction surfaces, not hard dependencies. Skipping them should degrade gracefully to onboarding-only routing like it is
  today. But skipping will increase retrieval costs for the user.
- The first implementation should avoid API-normalization adapters that wrap provider result formats; agents should be instructed which provider to use for which question shape and should form provider-native queries/results within the normal tool-use loop.
- Provider installation must be opt-in and explain the advantage in user-friendly language. If agreed system needs to take care of installation.
- Provider freshness must be workflow-managed; users should not need to remember random watchers.
- Before implementation, GrepAI and the selected relationship provider installation, daemon/watch behavior, index storage, re-ingestion commands, stale-index failure modes, and hook/refresh points must be mapped so provider lifecycle does not clash with Agents Remember's operational model.
- When a settings file enables a GrepAI provider, the system must check that the GrepAI watcher is running for the configured root before relying on GrepAI results. This should become an always-on installed-runtime rule, either in `AGENTS.md` or the resolved `system/*` guidance.
- GrepAI's managed memory provider should run at the `ar-coordination/memory-repos` root so one watcher can cover multiple memory repos.
- Graphify provider artifacts must live under the coordination runtime, for example `ar-coordination/providers/graphify/<repo>/`, not inside code repositories or memory repos. If Graphify cannot build, update, query, and serve from a configured external artifact root, it is not acceptable as an Agents Remember managed provider.
- Graphify managed mode must be driven by the Agents Remember provider lifecycle manager, not by Graphify's own agent installers or git hooks. The lifecycle manager must run with a coordination-owned working directory, set `GRAPHIFY_OUT` to an absolute coordination-runtime artifact path, pass `--out` or `--graph` explicitly where supported, and disable or sandbox Graphify features that still assume repo-local `graphify-out`.
- CodeGraphContext should be evaluated as the preferred Graphify replacement for the relationship provider role because it offers native CLI/MCP relationship queries, watch/index/delete/doctor lifecycle commands, and configurable embedded database paths.
- CodeGraphContext lifecycle checks must distinguish between its long-lived components: `cgc watch` is a blocking foreground watcher process, `cgc mcp start` is a long-lived MCP server that can own watcher state internally, FalkorDB Lite may spawn a database worker subprocess, and KuzuDB is embedded without a separate daemon.
- CodeGraphContext should be managed per code repository, with one runtime root and one supervised watcher/MCP process per configured code repo. Several code repos must be supported by registering several provider instances keyed by resolved C-08 code root or stable repo id.
- A foreground watcher is acceptable when Agents Remember owns the process lifecycle. The watcher must self-update on file changes, and the lifecycle manager must also expose a hard refresh path that rebuilds the provider index from source.
- GrepAI and CodeGraphContext must both be queryable through CLI commands. MCP may be supported as an ergonomic alternate transport, but token economy is governed by returned result size, call count, and evidence selection rather than by CLI vs MCP alone.
- A small provider lifecycle manager is the minimum programmatic integration: status, start, stop, refresh, and doctor checks for configured providers.
- C-03 and C-05 must notify or refresh provider indexes after onboarding updates.
- GrepAI should be the default semantic provider for the memory layer when configured.
- CodeGraphContext should be the default relationship provider for code repos when configured. Graphify remains deferred fallback/research until its artifact and hook behavior is less costly to contain.
- The implementation must preserve C-08 context resolution, C-02 drift discipline, W-02 approval gates, and C-05 onboarding propagation.
- Existing route indexes and `overview.index.json` remain first-class context inputs.

---

## Implementation Steps

### S1 — Finalize provider contract and settings shape

- Update the machine-readable settings examples and reference docs for `contextProviders`.
- Define provider fields for `type`, `scope`, `roots`, `runtimeRoot`, `env`, `watch`, `freshness`, and `transportPolicy`.
- Keep settings declarative: settings say which providers exist and where they live; the lifecycle manager owns process decisions.
- Preserve existing settings compatibility when `contextProviders` is absent.
- Target files:
  - `runtime/system/defaults/examples/coordinator/settings.json`
  - `runtime/system/defaults/examples/coordinator/settings.md`
  - `runtime/system/defaults/examples/coordinator/tools.md`
  - `docs/reference/settings-json.md`

Completion gate: a user can express one GrepAI memory provider and multiple CGC code providers without changing code or memory repo contents.

### S2 — Run provider quality and volume evaluation before deeper integration

- Evaluate GrepAI against the current `ar-coordination/memory-repos` index.
- Install/evaluate CodeGraphContext only after opt-in consent, then index at least two code repos if available: the active `agents-remember-md` repo and one additional sibling code repo.
- Store all test artifacts under the active task folder, for example `provider-evaluation.md`, with no provider artifacts in code repos.
- Use CLI first for repeatability; use MCP only if already mounted and bounded.
- For each query, record result volume and quality before treating the provider as useful.
- Compare provider output against source/onboarding truth for a small sample, because provider output is candidate routing only.

Evaluation table format:

| Provider | Scope/root | Transport | Query / command shape | Returned volume | Summary of returned candidates | Verification sample | Quality/quantity judgement | Design consequence |
| -------- | ---------- | --------- | --------------------- | --------------- | ------------------------------ | ------------------- | -------------------------- | ------------------ |
| GrepAI | `<coordination_root>/memory-repos` | CLI | Semantic route query for a known onboarding concept | Lines/items/chars | Which memory routes/files came back | Source/onboarding checked? | Good/noisy/sparse, too much/too little | Keep, adjust query guidance, or cap harder |
| GrepAI | `<coordination_root>/memory-repos` | CLI | Ambiguous semantic query that should not overclaim | Lines/items/chars | Candidate routes and confidence shape | Source/onboarding checked? | Good/noisy/sparse, too much/too little | Update C-04 fallback rule |
| CGC | `<code_repo_root>` | CLI | Caller/callee query for a known function/class | Lines/items/chars | Relationship candidates returned | Source checked? | Good/noisy/sparse, too much/too little | Keep command as supported probe or avoid |
| CGC | `<code_repo_root>` | CLI | Dependency/impact query across modules | Lines/items/chars | Candidate impact path(s) returned | Source checked? | Good/noisy/sparse, too much/too little | Keep, cap depth, or require source-first |
| CGC | `<code_repo_root>` | CLI | Negative or low-signal query | Lines/items/chars | Whether it fails quietly or floods output | Source checked? | Good/noisy/sparse, too much/too little | Add guardrail or avoid command |

Completion gate: the task has a written judgement about whether GrepAI and CGC produce useful candidate routes at acceptable output volume, plus specific query limits for C-04.

### S3 — Implement provider lifecycle manager

- Add a small shared provider manager surface rather than provider-specific adapters that normalize query outputs.
- Provide `status`, `start`, `stop`, `refresh`, and `doctor` for each configured provider instance.
- Track runtime state under `<coordination_root>/providers/<provider>/<id>/`, including logs, PID/process metadata, last status, and last refresh.
- GrepAI implementation:
  - status: run from configured root with `grepai status --no-ui` and `grepai watch --status`
  - start: `grepai watch --background --log-dir <runtimeRoot>/logs`
  - stop: `grepai watch --stop`
  - refresh: stop/start managed watcher, with destructive `.grepai` rebuild only as explicit doctor remediation
  - doctor: command availability, root `.grepai`, index stats, watcher state, log path
- CGC implementation:
  - status: command availability, runtime paths, provider process state, `cgc doctor`, `cgc list`, and `cgc stats <code_repo_root>`
  - start: supervised foreground `cgc watch <code_repo_root>` or managed `cgc mcp start` with provider env and cwd `<runtimeRoot>`
  - stop: terminate the lifecycle-manager-owned process
  - refresh: `cgc index <code_repo_root> --force`, or delete-then-index fallback if needed
  - doctor: path containment, env resolution, backend availability, query sanity check
- Likely target files:
  - new shared module under `runtime/skills/U-01-core-skills/_shared/agents_remember/`
  - optional script under `runtime/scripts/` for manual provider lifecycle commands

Completion gate: provider lifecycle commands can be dry-run and can report actionable status without relying on a model to remember shell incantations.

### S4 — Rewrite C-04 as Trusted Context Router

- Replace `C-04-onboarding-read-mode` behavior with a provider-aware routing protocol while preserving onboarding-only fallback.
- Keep the skill path stable unless a separate rename decision is approved.
- Add provider selection rules:
  - relationship/impact questions: try CGC when configured and healthy
  - semantic memory questions: try GrepAI when configured and healthy
  - path-derived truth and proof: use onboarding/source regardless of provider output
- Make provider output a candidate packet input, not an answer.
- Add query budgets from S2: maximum provider calls, maximum returned lines/items, and fallback behavior when output is noisy or stale.
- Target file:
  - `runtime/skills/U-01-core-skills/C-04-onboarding-read-mode/SKILL.md`

Completion gate: an agent can read C-04 and know when to query GrepAI, when to query CGC, when to ignore a provider, and how to prove the final claim.

### S5 — Encode always-on provider rules in installed runtime guidance

- Update installed `AGENTS.md` or system guidance so provider checks happen before reliance.
- Required rule: if settings enables GrepAI, verify the watcher/index for the configured root before using GrepAI output.
- Required rule: if settings enables CGC, verify provider status for the specific code repo before using CGC output.
- Make unhealthy providers degrade to onboarding-only routing rather than blocking all work.
- Target files:
  - `runtime/agents-md-files/system/AGENTS.md`
  - possibly `runtime/agents-md-files/coordinator/AGENTS.md`

Completion gate: installed guidance protects users from stale provider output even if they never read the provider manager docs.

### S6 — Add refresh hooks to onboarding workflows

- Update C-03 and C-05 so onboarding bootstrap/update work triggers provider refresh instructions or lifecycle-manager calls after successful memory changes.
- GrepAI: after C-03/C-05 changes memory files, ensure the memory-repos watcher sees changes or run managed refresh if unhealthy.
- CGC: after code closeout or branch/worktree transitions, refresh the affected code repo provider when configured.
- Do not refresh before approval gates or before memory/code commits that establish truth.
- Target files:
  - `runtime/skills/U-01-core-skills/C-03-repo-bootstrap/SKILL.md`
  - `runtime/skills/U-01-core-skills/C-05-create-or-update-onboarding-files/SKILL.md`
  - possibly `runtime/skills/U-01-core-skills/C-09-git-worktree-manager/SKILL.md`

Completion gate: provider freshness becomes part of workflow closeout instead of user memory.

### S7 — Document operation and failure modes

- Add concise provider docs covering installation consent, artifact layout, lifecycle commands, status interpretation, stale-index behavior, and fallback.
- Explain CLI vs MCP as transport choice; token economy is controlled by returned evidence budgets.
- Document multi-repo CGC setup with one provider instance per code repo.
- Target docs:
  - `docs/reference/settings-json.md`
  - new or existing provider reference under `docs/reference/`
  - README/docs wording that no longer frames Agents Remember as competing with retrieval systems

Completion gate: a user can install/configure providers without reverse-engineering this task thread.

### S8 — Test and review

- Unit-test settings parsing and token/runtime-root expansion if a shared Python module is added.
- Run lifecycle manager `doctor/status` in dry-run mode without requiring CGC installation.
- When CGC is installed, run real smoke tests with runtime roots under `ar-coordination/providers/codegraphcontext/<repo-id>/`.
- Verify C-04 remains usable with no providers configured.
- Run markdown/doc checks if the repo has them.

Completion gate: the implementation passes no-provider, GrepAI-only, and GrepAI+CGC configured scenarios, and the provider evaluation table has informed the final C-04 query budgets.

---

## Proposed Code Examples

### E1 — Provider settings extension

Coordination configuration for optional context providers.

Why this example is included: the provider registry should live in ar-coordination settings to simplify installation and management. Provider indexes are local discovery accelerators, not durable memory. Their stores should not become distributed truth like onboarding.
What they offer is specialized discovery and lower retrieval friction, but they should not change Agents Remember's role as the routing, verification, drift, branch-validity, source-proof, and memory-promotion layer.

```json
{
  "version": 3,
  "contextProviders": {
    "enabled": true,
    "providers": {
      "grepai-memory": {
        "type": "semantic",
        "scope": "memory",
        "enabled": true,
        "roots": ["<coordination_root>/memory-repos"],
        "runtimeRoot": "<coordination_root>/providers/grepai/memory-repos",
        "watch": {
          "mode": "background",
          "cwd": "<coordination_root>/memory-repos",
          "logDir": "<runtimeRoot>/logs"
        },
        "freshness": {
          "refreshAfter": ["C-03", "C-05"]
        }
      },
      "codegraphcontext-code": {
        "type": "relationship",
        "scope": "code",
        "enabled": false,
        "roots": ["<code_repository_root>"],
        "runtimeRoot": "<coordination_root>/providers/codegraphcontext/<repo>",
        "env": {
          "CGC_RUNTIME_DB_TYPE": "kuzudb",
          "DEFAULT_DATABASE": "kuzudb",
          "KUZUDB_PATH": "<runtimeRoot>/db/kuzu",
          "FALKORDB_PATH": "<runtimeRoot>/db/falkordb.db",
          "FALKORDB_SOCKET_PATH": "<runtimeRoot>/run/falkordb.sock",
          "LOG_FILE_PATH": "<runtimeRoot>/logs/cgc.log",
          "DEBUG_LOG_PATH": "<runtimeRoot>/logs/debug.log",
          "ENABLE_AUTO_WATCH": "false"
        },
        "watch": {
          "mode": "managed-foreground",
          "cwd": "<runtimeRoot>",
          "logFile": "<runtimeRoot>/logs/watch.log"
        },
        "freshness": {
          "refreshAfter": ["C-09-closeout"]
        }
      }
    },
    "policy": {
      "discoveryOnly": true,
      "sourceProofRequired": true,
      "maxSemanticQueriesPerPacket": 1,
      "maxGraphQueriesPerPacket": 2,
      "transportPolicy": {
        "default": "cli",
        "mcp": "optional",
        "tokenEconomy": "budget-returned-evidence"
      }
    }
  }
}
```

---

## Provider Lifecycle Notes

### GrepAI memory provider

- Purpose: semantic discovery over Agents Remember memory repos. GrepAI returns candidate memory routes, not truth.
- Scope: one configured root at `<coordination_root>/memory-repos` so the same index covers multiple memory repos.
- Artifacts: GrepAI-owned index/config stays at `<coordination_root>/memory-repos/.grepai`. Agents Remember-owned process metadata, PID files, and logs stay at `<coordination_root>/providers/grepai/memory-repos/`.
- Managed start: run from `<coordination_root>/memory-repos` with `grepai watch --background --log-dir <runtimeRoot>/logs`. Foreground `grepai watch` is allowed only for manual development sessions.
- Status gate: before using GrepAI, run status checks against the configured root, including `grepai status --no-ui` for index state and `grepai watch --status` for managed background watcher state. If a developer is running a foreground watcher, the lifecycle manager may treat it as an observed dev state but should not encode that as the required runtime mode.
- Self-update: the watcher performs an initial scan, skips unchanged files by modification time, indexes new/modified files, and handles create/modify/delete/rename events with debouncing.
- Hard refresh: stop and restart the managed watcher so the initial scan reconciles disk/index state. A full destructive rebuild of `.grepai` is a doctor/remediation action and should require explicit approval because it rewrites provider-owned index state.
- Query mode: agents may use `grepai search` or GrepAI MCP as semantic discovery. Results must be verified through onboarding/source proof before memory promotion.
- Transport choice: prefer CLI for deterministic lifecycle/status/search flows. MCP is acceptable when the runtime has already mounted it and it returns similarly bounded results. Neither transport is proof.

### CodeGraphContext code provider

- Purpose: relationship and impact discovery over code repositories. CGC returns candidate relationships, not final architecture claims.
- Scope: one provider instance per C-08 resolved code repo. Multi-repo support is achieved by registering multiple `codegraphcontext-code:<repo-id>` instances, each with its own runtime root and process supervision.
- Artifacts: all Agents Remember-managed CGC artifacts for a repo live under `<coordination_root>/providers/codegraphcontext/<repo-id>/`. The planned layout is `db/` for KuzuDB or FalkorDB files, `run/` for sockets/PIDs, `logs/` for process logs, and optional `bundles/` for `.cgc` exports.
- Configuration: prefer explicit runtime environment over user-global CGC config. Minimum env: `CGC_RUNTIME_DB_TYPE=kuzudb`, `DEFAULT_DATABASE=kuzudb`, `KUZUDB_PATH=<runtimeRoot>/db/kuzu`, `FALKORDB_PATH=<runtimeRoot>/db/falkordb.db`, `FALKORDB_SOCKET_PATH=<runtimeRoot>/run/falkordb.sock`, `LOG_FILE_PATH=<runtimeRoot>/logs/cgc.log`, `DEBUG_LOG_PATH=<runtimeRoot>/logs/debug.log`, and `ENABLE_AUTO_WATCH=false`.
- Managed start: foreground/blocking CGC watchers are acceptable when launched and supervised by Agents Remember. Start with the provider env, cwd `<runtimeRoot>`, and absolute code repo root, e.g. `cgc watch <code_repository_root>` or `codegraphcontext watch <code_repository_root>`.
- Status gate: check that the provider command is installed, runtime paths are writable, `cgc doctor` succeeds under the provider env, the target repo appears in `cgc list`, `cgc stats <code_repository_root>` reports indexed content, and the lifecycle manager has a live watcher/MCP process when watch mode is enabled.
- Self-update: `cgc watch` uses a watchdog observer and updates the graph on create/modify/delete/move events. The CLI watcher blocks; the MCP server can also own watcher state through `watch_directory`, `list_watched_paths`, and `unwatch_directory`.
- Hard refresh: run `cgc index <code_repository_root> --force` under the provider env, or the equivalent delete-then-index flow if the installed version lacks `--force`. If a watcher is running, stop or pause it during the rebuild and restart it afterward.
- Query mode: agents may use CGC CLI/MCP relationship commands such as callers, callees, call chain, dependencies, inheritance tree, variable usage, complexity, dead-code, or read-only Cypher. Results must lead to source/onboarding verification before claims are promoted.
- Transport choice: prefer CLI for scripted provider manager actions and bounded relationship probes. MCP is acceptable for interactive agent workflows, especially when mounted once per provider instance. Token economy must be enforced through result limits and source-follow-up discipline either way.

### Provider manager responsibilities

- Install providers only after opt-in consent.
- Keep provider roots out of code repos and memory repos unless the provider's own index format requires a repo-local config file already approved for that provider.
- Track configured roots, runtime roots, process ids, log paths, command versions, and last successful refresh timestamps.
- Support `status`, `start`, `stop`, `refresh`, and `doctor` for each provider instance.
- Allow multiple GrepAI memory scopes if needed later, but default to one memory-repos root.
- Allow multiple CGC code scopes from the start, one per code repo, keyed by stable repo id and C-08 resolved code root.

---

## Decision Log

| Date-Time        | Decision | Rationale |
| ---------------- | -------- | --------- |
| 2026-05-19T17:15 | Make provider quality/volume evaluation an implementation gate before deeper integration. | Developer requested that the actual implementation plan include testing both tools for output volume and quality, with a table documenting what was queried, what was returned, and a judgement about usefulness. S2 now gates C-04 query budgets and provider-default decisions on measured GrepAI/CGC behavior rather than assumptions from docs. |
| 2026-05-19T17:06 | Treat CLI and MCP as transports with similar token economics when result budgets are equal. | Developer clarified the expectation that both GrepAI and CGC can be queried through CLI, and asked whether CLI/MCP token economy is probably the same. The design answer is that transport is secondary: CLI is preferred for deterministic lifecycle/status/query commands, MCP is acceptable for mounted interactive workflows, and token cost is controlled by bounding returned provider output and only promoting claims after source/onboarding verification. |
| 2026-05-19T16:58 | Accept managed foreground CGC watchers as long as Agents Remember owns the lifecycle. | Developer clarified that foreground runtime is acceptable when Agents Remember can start, stop, monitor, and refresh it without user babysitting. The important contract is self-update through watch mode plus a hard-refresh path. GrepAI remains a memory-repos-wide semantic watcher, while CGC should be made multi-code-repo capable through one runtime root and supervised watcher/MCP process per configured code repo. |
| 2026-05-19T16:43 | Treat CodeGraphContext as a supervised-process provider, not a no-process library. | Source confirms CGC has multiple runtime modes: the CLI `watch_helper` starts a watchdog observer thread and then blocks until Ctrl+C; `MCPServer` owns a `CodeWatcher` and exposes `watch_directory`, `list_watched_paths`, and `unwatch_directory`; `FalkorDBManager` can start a worker subprocess using `FALKORDB_PATH` and `FALKORDB_SOCKET_PATH`; KuzuDB runs embedded at `KUZUDB_PATH`. The provider lifecycle manager should therefore own the process boundary instead of assuming CGC provides an independent daemon manager. |
| 2026-05-19T16:31 | Treat CodeGraphContext as the leading candidate to replace Graphify for relationship discovery. | Current docs and source show it is much closer to the desired provider shape: native MCP and CLI, commands for callers/callees/call chains/dependencies/inheritance/dead-code, `index`, `watch`, `delete`, `doctor`, and portable bundles. Source exposes runtime-controllable paths such as `FALKORDB_PATH`, `FALKORDB_SOCKET_PATH`, `KUZUDB_PATH`, and log paths, so Agents Remember can place artifacts under `ar-coordination/providers/codegraphcontext/<repo>/` without relying on repo-local generated files. It is not installed locally yet, so an installation/source spike remains required before adoption. |
| 2026-05-19T15:58 | Use Graphify only through an Agents Remember-managed lifecycle, not Graphify's installed hooks or generated agent instructions. | Source inspection of `graphifyy==0.7.10` confirms core graph paths are configurable enough for a managed provider: `_GRAPHIFY_OUT` comes from `GRAPHIFY_OUT`, watch/update/check-update use it, query/path/explain accept `--graph`, MCP serving accepts an explicit graph path, and `extract --out DIR` writes primary outputs under `<DIR>/graphify-out`. However `extract --out` alone does not redirect caches unless `GRAPHIFY_OUT` is also set, packaged hooks and platform instructions hard-code `graphify-out`, `cluster-only` writes to `<watch_path>/graphify-out`, and secondary memory/converted-file paths still assume repo-local `graphify-out`. |
| 2026-05-19T15:32 | Treat Graphify artifact-root support as partial until tested across the lifecycle. | Public Graphify docs show default outputs under `<path>/graphify-out/`; release notes document `graphify extract --out DIR`, and CLI docs show `graphify query --graph path/to/graph.json`. This is enough to justify a spike, but not enough to prove build/update/watch/hook/MCP can all be contained under `ar-coordination/providers/graphify/<repo>/`. |
| 2026-05-19T14:53 | Do not treat the developer's manual foreground GrepAI watcher as the required runtime mode. | Developer clarified that the current `grepai watch` terminal session explains local observations but does not prescribe how Agents Remember should run GrepAI. The provider lifecycle manager should choose a managed mode that is reliable to start, stop, verify, and refresh. |
| 2026-05-19T14:50 | GrepAI watcher checks must be scoped to the configured provider root and account for manual foreground watcher observations during development. | Developer clarified the runner operates above `ar-coordination/memory-repos`. Local inspection found the relevant `.grepai` config under `ar-coordination/memory-repos/.grepai` with a fresh 1401-file index, but `grepai watch --status` still reports no background watcher from Codex's process context. The lifecycle manager should not rely only on global/background status while evaluating current local state. |
| 2026-05-19T14:45 | Require provider runtime lifecycle management and containment before implementation. | Developer clarified that GrepAI must be running before reliance, configured GrepAI providers should trigger watcher checks as an always-on rule, Graphify artifacts must stay in the coordination runtime rather than code or memory repos, and a provider lifecycle manager is the minimum programmatic integration. |
| 2026-05-19T14:15 | Add a provider lifecycle preflight before implementation. | Developer wants GrepAI and Graphify installation, runtime, maintenance, and re-ingestion behavior understood before code changes so provider quirks do not surface only after integration. |
| 2026-05-19T14:03 | Prefer instructional provider profiles over API-normalization adapters. | Developer clarified that normalizing GrepAI/Graphify outputs would create fragile adapter maintenance and make tool swaps expensive. The router should tell the model which provider to use for which question shape and let the model query the provider natively. |
| 2026-05-19T13:53 | Keep CocoIndex out of the concrete provider scope until separately evaluated. | Developer clarified that the task has only considered GrepAI and Graphify so far. CocoIndex appears adjacent and possibly overlapping, but it should remain future-tool context rather than a named task requirement. |
| 2026-05-19T13:47 | Reframe Agents Remember as complementary trust/control infrastructure over optional discovery providers. | Developer clarified that Agents Remember should not compete with GrepAI, Graphify, or future retrieval/index tools. Local TensorFlow benchmark summaries show onboarding can save tokens in some variants but does not reliably reduce wall time, so speed cannot be the primary claim. |

---

## Open Questions

- Should C-04 be physically renamed from `C-04-onboarding-read-mode` to `C-04-trusted-context-router`, or should the installed skill path stay stable while the title and behavior change?
- Should provider refresh hooks execute commands directly where configured, or only emit explicit workflow instructions until each provider adapter has its own tested helper?
- Can the provider lifecycle manager fully avoid or safely sandbox Graphify's remaining hard-coded secondary paths (`cluster-only`, `save-result` memory, office/Google converted files, packaged hooks), or should those Graphify features be disabled in managed mode?
- Does CodeGraphContext create any hidden repo-local artifacts beyond optional `.cgcignore`, and does its current published package honor `KUZUDB_PATH`/`FALKORDB_PATH` exactly as the main-branch source suggests?

---

## References

- Developer clarification in this task discussion.
- `agents-remember-md/benchmarks/cases/tensorflow-check-numerics-xla/author-results/2026-05-19/summary.md`
- GrepAI semantic search docs: https://yoanbernabeu.github.io/grepai/search-guide/
- Local GrepAI CLI help checked for `grepai watch --background --status --stop --log-dir`, `grepai status --no-ui`, and watcher self-update semantics.
- Graphify docs: https://graphify.net/zh/
- CocoIndex docs checked only as adjacent future-tool context: https://cocoindex.io/cocoindex-code/
- Graphify CLI reference: https://graphify.net/graphify-cli-commands.html
- Graphify v0.7.3 release note for `graphify extract --out DIR`: https://newreleases.io/project/github/safishamsi/graphify/release/v0.7.3
- Local source inspection of the published `graphifyy==0.7.10` wheel, especially `graphify/__main__.py`, `graphify/watch.py`, `graphify/cache.py`, `graphify/detect.py`, `graphify/hooks.py`, and `graphify/serve.py`.
- CodeGraphContext docs and source: https://github.com/CodeGraphContext/CodeGraphContext and https://codegraphcontext.github.io/CodeGraphContext/
- CodeQL CLI database docs checked as a more mature but less plug-and-play fallback: https://docs.github.com/en/code-security/reference/code-scanning/codeql/codeql-cli-manual/database-create
- Joern docs checked as a CPG fallback with configurable outputs but heavier query/lifecycle integration: https://docs.joern.io/
