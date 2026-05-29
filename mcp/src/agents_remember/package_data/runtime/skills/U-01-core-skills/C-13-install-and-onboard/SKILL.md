---
name: c-13-install-and-onboard
description: "After the MCP installs the runtime and skills (scaffolding), take over the rest of setup: install a start-of-session/chat hook (or place the coordinator directive in the harness's always-on instructions), set up the memory repo (ask scaffold vs existing), bootstrap onboarding when scaffolding new, and configure providers to index the code and memory."
---

# C-13 Install And Onboard

Take over installation **after** the MCP has done the deterministic scaffolding,
and lead the developer through the rest of setup until the project is operational.

This skill is model-driven on purpose. The MCP layer does what is deterministic
(`runtime_install`, `skills_install`); everything harness-specific or
project-specific is done by the model with the developer, because a capable
harness can write the right config in the right place itself. Do not expect a
hardcoded per-harness installer tool — there is none, by design.

## When To Use

Use this after the prerequisites are in place:

1. The Agents Remember MCP server is wired and reachable (the developer did this
   and restarted the harness).
2. `runtime_install()` installed the coordinator runtime scaffold (it applies by
   default; preview first with `runtime_install(dry_run=true)`).
3. `skills_install()` exposed the packaged skills to the harness.

If those are not done, do not improvise around them — guide the developer through
the pre-C-13 runbook first: get and wire the MCP server, author valid MCP
settings, restart the harness, then `runtime_install` and `skills_install`. See
the install docs for the copy-pasteable zero-to-operational runbook, and
`C-00-initialize-memory-repo` for memory scaffolding. Stage 0 below re-checks
these so a too-early invocation fails loudly with a fix, not silently.

## What This Skill Owns

A preflight check plus a four-stage sequence. Run them in order; stop and report
between stages when a stage needs a developer decision.

0. Preflight — verify host/setup prerequisites and explain any fix before working.
1. Install a start-of-session/chat hook, or place the coordinator directive.
2. Set up the memory repo — ask scaffold-new vs use-existing.
3. If a new memory repo was scaffolded, hand off to `C-03-repo-bootstrap`.
4. Configure providers so they index the code and memory.

This skill orchestrates and delegates. It does not reimplement `C-00`
(memory init), `C-03` (bootstrap), or the provider lifecycle; it sequences them
and fills the harness/provider-wiring gap between them.

## Stage 0 — Preflight (prerequisites)

Before any work, verify the setup and, for each failed check, tell the developer
in plain language what is wrong and the exact command to fix it. Do not proceed
past a failed check silently — a half-installed system strands a non-expert.
Report a short pass/fail checklist with the fix for each failure.

Check, in order:

1. **MCP reachable.** You are running as an installed skill, so `skills_install`
   already ran — but confirm the MCP responds (`ping` / `server_info`). If it does
   not, the harness was not restarted after the MCP was wired: tell the developer
   to restart the harness, then retry.
2. **Scaffolding present.** Confirm `runtime_install` ran (coordinator `AGENTS.md`,
   `skills/`, `tasks/`, `memory-repos/` exist under the coordination root). If
   missing, ask the developer to request `runtime_install()` (it applies by
   default; preview first with `runtime_install(dry_run=true)`).
3. **Settings sane.** Confirm `server_info` reports the expected `coordinationRoot`,
   `workspaceRoot`, allowed repos, and providers. A wrong path here is the usual
   cause of later resolver errors; surface the resolved absolute paths.
4. **Hook prerequisite (for Stage 1).** If the harness hook command needs `jq`
   (Claude Code), confirm `jq` is on `PATH`. If it is missing the hook installs but
   injects **nothing, silently** — install `jq` (`apt install jq` / `brew install jq`)
   or use the instructions-file fallback. Never leave a silent dead hook.
5. **Provider prerequisites (for Stage 4), only if providers are enabled.**
   Providers run in Docker, and grepai's embedder needs Ollama with a pulled model.
   Confirm Docker is running and (for grepai) Ollama is reachable with its model.
   If not, say so plainly: the providers will report `degraded` and index nothing
   until Docker/Ollama are up. Use `provider_diagnostics` for the exact gap.
6. **Topology consistency.** If the MCP settings point at an external coordination
   root, Stage 2 should use external memory; do not let `C-00` default to internal
   in a way that contradicts the configured topology.

Pass everything — or the developer explicitly accepts a known gap (e.g. providers
deferred) — before moving on. When a prerequisite is the developer's to fix, stop
with the exact fix and resume once they confirm.

## Stage 1 — Start Hook Or Instructions

Goal: make the coordinator first-action directive **authoritative** so every new
session reads and follows `ar-coordination/AGENTS.md` before sibling-repo work.

Decide per harness:

- If the harness supports a **context-injecting** start-of-session/chat hook,
  install the hook (write the directive file + register the hook in the harness's
  settings).
- Otherwise, place the directive into the harness's **always-on instructions
  file** and tell the developer it is lower-authority (it loads as context that
  the harness may treat as optional), so they may need to nudge at session start.

### The directive

Write this content (the file the hook injects, or the body you place into the
instructions file). It points at the coordinator `AGENTS.md`:

```markdown
MANDATORY FIRST ACTION for this workspace (ar-coordination).

Before doing ANY work in a sibling repository, read and follow
`ar-coordination/AGENTS.md`. Required first steps, in order:

1. Infer the target code repository from the developer's request. Ask if it
   is unclear.
2. Resolve coordination/memory context FIRST — via the C-08 resolver, or the
   agents-remember MCP tools `resolve_context` then
   `context_packet(repo_id=..., include_providers=true)`.
3. Pick a task format per AGENTS.md routing (chat / W-02 light / W-01 heavy)
   before changing code or task-plan items.

This instruction is harness-injected and authoritative. Treat it as a required
first step, not optional "maybe relevant" context.
```

Use the resolved coordinator `AGENTS.md` path (relative to the workspace when it
is inside the workspace, otherwise absolute).

### Harness routing (researched 2026-05-29 — verify before relying on it)

| Harness | Mechanism | What to do |
| --- | --- | --- |
| Claude Code | `SessionStart` hook injects `additionalContext` | Install the hook: write `.claude/hooks/coordinator-first-action.md`, add a `SessionStart` command entry to `.claude/settings.json` that emits the file as `additionalContext`. Full worked example in [docs/install/claude-code.md](agents-remember-md/docs/install/claude-code.md). Keep `CLAUDE.md` import only as a degraded fallback. |
| Codex | `SessionStart` hook (Claude-style), `~/.codex/hooks.json` or `.codex/config.toml [hooks]`; `stdout` JSON `hookSpecificOutput.additionalContext` is injected | Install the hook with the same directive; also note Codex reads `AGENTS.md` natively. |
| Pi.dev | `session_start` / `before_agent_start` can inject context | Install the harness's start hook with the directive. |
| OpenClaw | `session_start` / `before_prompt_build` can inject context | Install the harness's start hook with the directive. |
| Antigravity | Supports hooks (skills/MCP under `~/.gemini/...`); exact hook format not yet confirmed | If you can confirm the hook format from current Antigravity docs, install it; otherwise fall back to its instructions file and say so. |
| Cursor | Hooks are governance/observe (e.g. `beforeSubmitPrompt` is informational only) — a hook cannot inject authoritative context | Do NOT install a hook. Place the directive in an `alwaysApply` rule: `.cursor/rules/agents-remember.mdc` with `alwaysApply: true`. |
| VS Code + Copilot | No start command hook | Place the directive in `.github/copilot-instructions.md`. |
| Hermes | `HERMES.md` is highest-priority context; no injecting start hook confirmed | Place the directive in `HERMES.md` (or `AGENTS.md`). |

If the harness is not listed, check its current docs: prefer a context-injecting
start hook if one exists; otherwise use its highest-priority always-on
instructions file. Never fabricate a hook format you cannot confirm — fall back
to the instructions file and tell the developer.

After installing, report exactly what was written and where, and whether it is a
true hook (authoritative) or an instructions-file fallback.

## Stage 2 — Memory Repo: Ask Scaffold Vs Existing

Do not assume the developer wants a fresh memory repo. Ask which case applies:

1. **Scaffold a new memory repo** — they have no existing memory for this code
   repo. Run `C-00-initialize-memory-repo` (internal by default; external only if
   they ask). Continue to Stage 3.
2. **Use an existing memory repo** — they already have one. Clone/checkout it to
   the resolved memory location, then adopt it as the ledgered baseline with
   `C-10-adopt-memory-baseline`. Skip Stage 3 (its onboarding already exists).

Internal-memory note: pre-existing internal memory lives inside the code repo
(`<repo>/ar-memory/`), so it is already present on checkout. That is the usual
non-case — detect it via `C-08-ar-coordination-context-resolver` and skip the
question when memory is already there.

## Stage 3 — Bootstrap (only when scaffolding new)

If Stage 2 scaffolded a new memory repo, hand off to `C-03-repo-bootstrap` to
generate the initial onboarding (a thin root `overview.md` is enough to start;
deeper onboarding grows as work touches new areas).

Skip this stage when an existing memory repo was adopted in Stage 2.

## Stage 4 — Configure Providers To Index

`runtime_install` builds the provider runtimes (containers) but they index
**nothing** until they are pointed at repos and their watchers run. Make the
configured providers start indexing the code and its memory:

1. Confirm the target repo is in the MCP provider scope (it is the `repo_id` in
   the MCP settings; providers index the configured repos).
2. Start the watchers: `provider_watchers(action="start")` — applies by default;
   preview first with `provider_watchers(action="start", dry_run=true)`. Use
   `action="refresh"` to re-seed an already-running provider after the repo or
   memory changed.
3. Verify with `provider_status` (or `context_packet(repo_id=..., include_providers=true)`)
   that each provider is `ready`, its watcher is up, and it targets the repo. Use
   `provider_diagnostics` for raw detail when a provider is degraded.

Both providers should cover the work surface: code-relationship search
(CodeGraphContext) over the code repo, and semantic-memory search (grepai) over
the memory/onboarding. If a provider reports stopped/degraded, report that state
and the recovery action rather than treating setup as complete.

## Report Result

Summarize, per stage:

1. hook installed (harness + file/setting paths) or directive placed in which
   instructions file, and whether it is authoritative or a degraded fallback;
2. memory repo: scaffolded (internal/external) or existing-adopted, and the
   resolved memory root;
3. bootstrap: run via `C-03` or skipped (existing memory);
4. providers: which are indexing the code and memory, and any degraded state.

End by telling the developer the project is ready for normal work, or list the
remaining manual step if a stage needed something only they can do.

## Boundaries

1. This skill is guidance for the model; it adds no MCP tool and no hardcoded
   per-harness installer.
2. It must not scaffold a memory repo without asking (Stage 2), and must not
   assume the start hook is supported without checking the harness.
3. It delegates memory init to `C-00`, bootstrap to `C-03`, baseline adoption to
   `C-10`, and context resolution to `C-08`.
4. It must not fabricate a harness hook format; when unconfirmed, use the
   instructions-file fallback and say so.
