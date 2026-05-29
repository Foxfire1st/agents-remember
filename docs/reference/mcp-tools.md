# MCP Tool Reference

The Agents Remember MCP server exposes 36 tools. Most state-changing tools default
to `dry_run=true` — call them with `dry_run=false` to apply. Repository-scoped
tools take a `repo_id` that must be an allowed repo in the MCP settings.

This page is a map of the surface; behavior detail lives in the linked skill and
reference pages.

## Server & context

| Tool | Purpose | Key args |
| --- | --- | --- |
| `ping` | Liveness check; returns server name/version/transport. | — |
| `server_info` | Report resolved roots, allowed repos/providers, and the tool list. | — |
| `resolve_context` | Resolve a repository's coordination/memory context (topology, roots, settings, storage, pathRules). | `repo_id`, optional `task_name` / `contract_path` / `worktree_name` / `topology` |
| `context_packet` | Bundle repo state, paths, memory, worktree, and provider status into one packet. | `repo_id`, `include_providers=true`, `include_drift=false` |

## Install & scaffolding

| Tool | Purpose | Key args |
| --- | --- | --- |
| `runtime_install` | Reconcile package-owned runtime (AGENTS.md templates, skills, provider defaults, runtime folders) into the coordinator; optionally install provider deps and benchmark fixtures. | `dry_run=true`, `include_benchmarks=false`, `install_provider_deps=true` |
| `skills_install` | Copy packaged skills into the harness skill root (`tree` or `flat` layout). | `layout="tree"`, `dry_run=true`, `overwrite=false`, `archive_existing=false` |

## Memory & onboarding

| Tool | Purpose | Key args |
| --- | --- | --- |
| `memory_init` | Initialize (or repair) a repository's memory root. | `repo_id`, `dry_run=true`, `initialize_git=true` |
| `drift_check` | Task-start onboarding drift classification; writes a temp drift report. | `repo_id`, `detail_limit=50` |
| `memory_quality_check` | Closeout memory-quality gate (drift integrity + style checks). | `repo_id`, `checks=None`, `detail_limit=50` |
| `route_index_refresh` | Regenerate `overview.index.json` route indexes to match the onboarding tree. | `repo_id`, `dry_run=true` |

## Memory baseline & carryover

| Tool | Purpose | Key args |
| --- | --- | --- |
| `memory_baseline_status` | Report drift + ledger state for adopting an external-memory baseline. | `repo_id` |
| `memory_baseline_adopt` | Create the first ledgered memory baseline (gated on clean/accepted drift). | `repo_id`, accept-drift / dry-run options |
| `memory_carryover_plan` | Plan carrying richer onboarding from a source branch into official memory. | repo/branch scope |
| `memory_carryover_apply` | Apply an approved carryover plan once the code has landed officially. | plan + intent |

## Worktree lifecycle & closeout

| Tool | Purpose | Key args |
| --- | --- | --- |
| `worktree_start` | Create/load a task contract and code (+ external-memory) worktrees. | `repo_id`, `task_name`, `worktree_name`, `workflow_kind="light-task"`, `dry_run=true`, `memory_choice` |
| `worktree_attach` | Re-attach to an existing task contract without mutating Git. | `repo_id`, `task_name` / `contract_path` |
| `worktree_status` | Report worktree lifecycle phase, dirty flags, and next-step hints. | `repo_id`, `task_name` / `contract_path` |
| `worktree_closeout_preview` | Non-mutating preview of a worktree-backed closeout. | `contract_path`, code/memory/ledger commit messages |
| `worktree_closeout_apply` | Apply a worktree closeout after explicit commit approval. | `contract_path`, `intent_note`, commit messages |
| `direct_closeout_preview` | Non-mutating preview of a direct (current-checkout) closeout. | `repo_id`, `task_name`, `code_commit_message`, … |
| `direct_closeout_apply` | Apply a direct closeout (code → memory → ledger) after explicit commit approval. | `repo_id`, `task_name`, `intent_note`, commit messages |
| `worktree_integrate` | Land closed task branches back onto source branches (`ff-only` or `replay`). | `contract_path`, `strategy`, `dry_run=true` |
| `worktree_cleanup` | Remove worktrees and merged task branches after integration. | `contract_path`, `dry_run=true` |

See [C-09 Worktrees And Closeout](worktrees-c09.md) for the lifecycle and gates.

## Providers

| Tool | Purpose | Key args |
| --- | --- | --- |
| `provider_status` | Compact provider readiness summary. | `detail_limit=20` |
| `provider_diagnostics` | Raw provider-native diagnostic detail. | `detail_limit=20` |
| `provider_watchers` | Start/stop/report provider watchers. | `action`, `dry_run=true` |

## Semantic memory search (grepai)

| Tool | Purpose | Key args |
| --- | --- | --- |
| `grepai_search` | Semantic search over memory/onboarding. | `query`, `repo_ids=None`, `all_repos=true`, `limit=10`, `output_format="json"`, `dry_run=true` |
| `grepai_trace` | Trace relationships in the semantic graph. | `trace_action`, `symbol`, scope + `output_format`, `dry_run=true` |

## Code-relationship search (CodeGraphContext)

| Tool | Purpose | Key args |
| --- | --- | --- |
| `cgc_symbol_search` | Find a symbol in the code graph. | `repo_id`, `name`, `dry_run=true` |
| `cgc_callers` | Who calls a function. | `repo_id`, `function`, `file=None`, `dry_run=true` |
| `cgc_callees` | What a function calls. | `repo_id`, `function`, `dry_run=true` |
| `cgc_dependencies` | A module's dependencies. | `repo_id`, `module`, `dry_run=true` |
| `cgc_complexity` | Complexity metrics from the code graph. | `repo_id`, `dry_run=true` |
| `cgc_visualize` | Produce a graph visualization. | `repo_id`, `dry_run=true` |

## Benchmarks

| Tool | Purpose | Key args |
| --- | --- | --- |
| `codex_benchmark_prepare` | Prepare resettable benchmark case workspaces. | case/scope options |
| `codex_benchmark_run` | Run a Codex benchmark case. | case + optional sandbox |

---

The authoritative source for this surface is `mcp/server.py` (`@server.tool()`
registrations); response shapes are enforced by the models in
`mcp/src/agents_remember/models/` via `tool_registry.PUBLIC_TOOL_RESPONSE_MODELS`.
