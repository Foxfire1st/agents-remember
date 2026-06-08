---
name: c-13-install-and-onboard
description: "After the harness starter package and MCP server are wired and the harness has restarted, run/verify runtime_install, set up the memory repo, bootstrap onboarding when scaffolding new, and configure providers to index the code and memory."
---

# c-13-install-and-onboard Install And Onboard

Take over setup **after** the developer has copied the harness starter package,
wired the Agents Remember MCP server, and restarted the harness once.

The harness package owns harness-native files: skills, hooks, rules,
instructions, MCP registration templates, and settings templates. This skill does
not install or repair those files during first-run setup. If they are missing,
stop and tell the developer to copy the correct starter package from this repo,
render the copied package with its `render-starter` script or by hand, restart
the harness, and invoke this skill again.

The MCP layer still exposes deterministic maintenance tools such as
`runtime_install()` and `skills_install()`. In the normal first-run path,
`skills_install()` is not used because the starter package already placed the
skills where the harness can discover them. `runtime_install()` remains the first
runtime action this skill performs because it creates or refreshes the
coordinator scaffold under `ar-coordination/`.

## When To Use

Use this skill after these prerequisites are in place:

1. The developer copied the harness-native starter package for the active
   harness into the workspace and ran that package's `render-starter` script
   with an explicit `--repo` list, or manually replaced every path, repository,
   and hook-command placeholder. The renderer infers the workspace root from the
   copied harness folder.
2. The Agents Remember MCP server is registered with the harness, typically with
   a command like:

   ```text
   uvx agents-remember-mcp@latest --config <absolute path to agents-remember-settings.json>
   ```

3. The harness was restarted once so it can load the MCP server, native skills,
   hooks, rules, and instruction files from the copied package.

If any prerequisite is missing, do not improvise a harness installer. Point the
developer at the appropriate `docs/install/<harness>.md` page, have them copy the
starter package, and resume after the restart.

## What This Skill Owns

Run this sequence in order:

0. Preflight: verify the copied package and MCP setup are usable enough to
   continue.
1. Runtime scaffold: run or verify `runtime_install()`.
2. Memory repo: ask scaffold-new vs use-existing, unless memory already exists.
3. Bootstrap: when a new memory repo was scaffolded, hand off to
   `c-03-repo-bootstrap`.
4. Providers: when providers are enabled, start/refresh indexing and verify
   readiness.

This skill orchestrates and delegates. It does not reimplement
`c-00-initialize-memory-repo`, `c-03-repo-bootstrap`,
`c-10-adopt-memory-baseline`, context resolution, or provider lifecycle tools.

## Stage 0 - Preflight

Before runtime or memory work, report a short pass/fail checklist. For each
failed check, give the exact fix and stop when the developer must act.

Check, in order:

1. **MCP reachable.** Confirm the MCP responds (`ping`, `server_info`, or the
   equivalent available tool). If not, the harness did not load the server; tell
   the developer to verify the MCP registration path and restart the harness.
2. **Harness package present.** Confirm this skill is running from a
   harness-discoverable skill root and that the active harness package includes
   its native settings/instruction files. Do not create missing harness files
   here; ask the developer to recopy the package and restart.
3. **Settings sane.** Confirm `server_info` reports the expected
   `coordinationRoot`, `workspaceRoot`, allowed repositories, and providers.
   Surface the resolved absolute paths because wrong paths are the common cause
   of resolver failures.
4. **Runtime state.** Check whether the coordinator scaffold already exists under
   `coordinationRoot` (`AGENTS.md`, `skills/`, `tasks/`, `memory-repos/`,
   `system/` as applicable). If it is missing or stale, Stage 1 will run
   `runtime_install()`.
5. **Provider prerequisites, only when providers are enabled.** Providers run in
   Docker. If provider diagnostics report Docker/Ollama/image problems, explain
   the gap. A developer may explicitly defer providers and continue with core
   memory setup.
6. **Topology consistency.** If MCP settings point at an external coordination
   root, memory setup must remain consistent with that topology. Do not let
   memory initialization silently choose internal memory when settings clearly
   describe an external-memory layout.

Do not check for legacy hook prerequisites such as `jq`. Hook and instruction
files are part of the copied, rendered harness package; this skill no longer
installs them.

## Stage 1 - Runtime Scaffold

Run or verify:

```text
runtime_install()
```

Use `runtime_install(dry_run=true)` only when the developer asks for a preview or
when local workflow policy requires one. Otherwise apply the runtime scaffold so
setup can proceed.

After it runs, verify the coordinator root exists and contains the expected
package-owned runtime files. `runtime_install()` may also prepare provider
runtime assets when providers are enabled, but it does not create memory repos,
bootstrap onboarding, or start provider indexing.

Do not run `skills_install()` as part of first-run setup. The harness starter
package already carries the skills. `skills_install()` remains available for
manual maintenance or non-package setups, but it is not the default path.

## Stage 2 - Memory Repo: Ask Scaffold Vs Existing

Do not assume the developer wants a fresh memory repo. Ask which case applies,
unless a memory repo is already present and resolvable:

1. **Scaffold a new memory repo** - they have no existing memory for this code
   repo. Run `c-00-initialize-memory-repo` (internal by default; external only if
   the developer asks or the configured topology requires it). Continue to Stage
   3.
2. **Use an existing memory repo** - they already have one. Clone or checkout it
   to the resolved memory location, then adopt it as the ledgered baseline with
   `c-10-adopt-memory-baseline`. Skip Stage 3 because its onboarding already
   exists.

Internal-memory note: pre-existing internal memory lives inside the code repo
(`<repo>/ar-memory/`), so it is already present on checkout. Detect that through
`c-08-ar-coordination-context-resolver` and skip the question when the memory
layer is already there.

## Stage 3 - Bootstrap

Run this stage only when Stage 2 scaffolded a new memory repo.

Hand off to `c-03-repo-bootstrap` to generate initial onboarding. A thin
`overview.md` is enough to start; deeper route-local overviews and file-level
onboarding should grow as work touches new areas.

Skip this stage when an existing memory repo was adopted.

## Stage 4 - Configure Providers To Index

If providers are enabled, make them index the configured code and memory:

1. Confirm the target repo is in MCP provider scope.
2. Start watchers:

   ```text
   provider_watchers(action="start")
   ```

   Use `provider_watchers(action="start", dry_run=true)` only when a preview is
   requested or workflow policy requires one. Use `action="refresh"` to reseed
   providers after repo or memory changes.
3. Verify with `provider_status` or
   `context_packet(repo_id=..., include_providers=true)` that providers are
   `ready`, watchers are up, and the target repo is covered.
4. Use `provider_diagnostics` when a provider reports `stopped` or `degraded`.

Providers are accelerators. If they are deferred, say that core by-path memory
still works and list the provider recovery action separately.

## Report Result

Summarize:

1. harness package: detected and left untouched, or missing with the exact copy
   step the developer must perform;
2. runtime scaffold: `runtime_install()` run or already current, with the
   resolved coordination root;
3. memory repo: scaffolded, existing-adopted, or already present, with the
   resolved memory root;
4. bootstrap: run via `c-03-repo-bootstrap` or skipped;
5. providers: indexing status and any deferred/degraded state.

End by telling the developer whether the project is ready for normal work. Do
not tell them to restart for hooks installed by this skill, because this skill no
longer installs hooks.

## Boundaries

1. This skill is guidance for the model; it adds no MCP tool and no hardcoded
   per-harness installer.
2. It must not install, overwrite, or invent harness hooks, rules, instruction
   files, skills, or MCP registration files during first-run setup.
3. It must not call `skills_install()` as part of the package-based first-run
   path. Mention it only as a maintenance/manual option.
4. It must not scaffold a memory repo without asking unless memory already
   exists and resolves cleanly.
5. It delegates memory init to `c-00-initialize-memory-repo`, bootstrap to
   `c-03-repo-bootstrap`, baseline adoption to
   `c-10-adopt-memory-baseline`, and context resolution to
   `c-08-ar-coordination-context-resolver`.
