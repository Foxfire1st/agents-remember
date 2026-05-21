# Phase 0 Quality Findings

**Repo:** agents-remember-md  
**Date:** 2026-05-22T00:11  
**Venv:** `C:/ew/agents-remember-md/.venv`  
**Status:** ready for developer review

---

## Summary

The quality suite is installed and runnable from the repo-local `.venv`.

The current test suite passes when invoked with the explicit nested test path:

```powershell
.\.venv\Scripts\python -m unittest discover -s runtime\skills\U-01-core-skills\tests
```

Result:

```text
Ran 118 tests in 53.217s
OK
```

The default root command is misleading:

```powershell
.\.venv\Scripts\python -m unittest discover
```

Result:

```text
Ran 0 tests in 0.000s
NO TESTS RAN
```

The static-analysis findings support the kernel/MCP direction. The main pressure points are not random; they cluster around the monolithic operational scripts and service-like modules that the design note already wants to extract:

- provider lifecycle
- provider setup
- context resolver
- worktree manager
- drift detection
- shared provider layout/helpers
- benchmark runner

---

## Environment

Installed with:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
```

The sandboxed `pip install` and `pip freeze` both hit a Windows permission boundary while pip inspected an external Python installation path. Rerunning elevated succeeded. This is environment friction, not a repo logic failure.

Tool versions:

```text
Python 3.13.9
ruff 0.15.13
radon 6.0.1
```

Installed packages:

```text
colorama==0.4.6
mando==0.7.1
radon==6.0.1
ruff==0.15.13
six==1.17.0
```

Local setup note: `.venv/` was added to `C:/ew/agents-remember-md/.git/info/exclude` so the repo-local tooling environment does not appear as untracked source state. This was not a committed `.gitignore` change.

---

## Commands Run

```powershell
.\.venv\Scripts\python --version
.\.venv\Scripts\ruff --version
.\.venv\Scripts\radon --version
.\.venv\Scripts\python -m unittest discover -s runtime\skills\U-01-core-skills\tests
.\.venv\Scripts\python -m unittest discover
.\.venv\Scripts\ruff check . --statistics --exit-zero
.\.venv\Scripts\ruff check . --select F401,F541,RUF034 --exit-zero
.\.venv\Scripts\radon cc runtime installer -s -a
.\.venv\Scripts\radon mi runtime installer -s
```

---

## Test Findings

### T1 - Explicit Test Suite Passes

The real test suite currently lives under:

```text
runtime/skills/U-01-core-skills/tests
```

That suite is healthy on this pass:

```text
118 tests OK
```

### T2 - Default Test Discovery Is Misleading

Root-level `unittest discover` runs zero tests and exits with failure. This should be fixed or documented before the project expects new contributors or MCP/CI jobs to run a default test command.

Recommended follow-up:

- add a standard test command wrapper, or
- document the canonical command in `pyproject.toml`/README/dev docs, or
- adjust package/test layout so `python -m unittest discover` finds the suite.

---

## Ruff Findings

Ruff summary:

```text
729 E501    line-too-long
 29 PLR2004 magic-value-comparison
 24 I001    unsorted-imports
 21 ARG005  unused-lambda-argument
 12 PLR0911 too-many-return-statements
 11 PLR0912 too-many-branches
 10 UP022   replace stdout/stderr PIPE with capture_output
  8 ARG001  unused-function-argument
  4 PLR0915 too-many-statements
  3 F401    unused-import
  3 SIM117  multiple-with-statements
  2 RUF010  explicit-f-string-type-conversion
  1 F541    f-string-missing-placeholders
  1 RUF005  collection-literal-concatenation
  1 RUF034  useless-if-else
  1 UP035   deprecated-import
  1 UP037   quoted-annotation
  1 UP045   non-pep604-annotation-optional
```

Total:

```text
862 diagnostics
34 fixable with --fix
```

### Ruff Hotspots By File

```text
165 runtime/scripts/provider-lifecycle.py
124 runtime/skills/U-01-core-skills/C-09-git-worktree-manager/scripts/git_worktree_manager.py
115 runtime/skills/U-01-core-skills/tests/test_worktree_support.py
 99 runtime/skills/U-01-core-skills/C-08-ar-coordination-context-resolver/scripts/ar_coordination_context_resolver.py
 71 runtime/skills/U-01-core-skills/_shared/agents_remember/context_providers.py
 51 runtime/skills/U-01-core-skills/tests/test_provider_lifecycle.py
 44 runtime/skills/U-01-core-skills/C-02-onboarding-drift-detection/scripts/check_onboarding_drift.py
 40 runtime/scripts/run-benchmarks.py
 31 runtime/skills/U-01-core-skills/tests/test_context_providers.py
 27 runtime/scripts/provider-setup.py
 18 installer/install-runtime.py
```

### High-Signal Ruff Findings

These are likely worth fixing before or during nearby refactors:

```text
F401 runtime/scripts/provider-lifecycle.py
  unused GREPAI_POSTGRES_DEFAULT_PORT
  unused assert_no_grepai_root_provider_artifacts

F541 runtime/skills/U-01-core-skills/C-09-git-worktree-manager/scripts/git_worktree_manager.py
  f-string without placeholders in direct-closeout preview ledger message

RUF034 runtime/skills/U-01-core-skills/_shared/agents_remember/route_index.py
  return "" if "" in routes else "" is a useless conditional

F401 runtime/skills/U-01-core-skills/tests/test_context_providers.py
  unused CGC_PIN
```

Interpretation:

- `E501` dominates the count and should not drive architecture decisions by itself.
- The real design signal comes from `PLR0911`, `PLR0912`, and `PLR0915` clustering in operational scripts.
- Import formatting and simple unused-code findings are good candidates for small mechanical cleanup tasks, not the kernel/MCP design phase.

---

## Radon Findings

Radon cyclomatic complexity average:

```text
589 blocks analyzed
Average complexity: A (4.624787775891341)
```

The average is healthy because many small helpers exist, but the pressure points are concentrated.

### Maintainability Index

`radon mi runtime installer -s` reports these files below A:

```text
C runtime/scripts/provider-lifecycle.py
C runtime/scripts/provider-setup.py
C runtime/scripts/run-benchmarks.py
C runtime/skills/U-01-core-skills/C-02-onboarding-drift-detection/scripts/check_onboarding_drift.py
C runtime/skills/U-01-core-skills/C-08-ar-coordination-context-resolver/scripts/ar_coordination_context_resolver.py
C runtime/skills/U-01-core-skills/C-09-git-worktree-manager/scripts/git_worktree_manager.py
C runtime/skills/U-01-core-skills/_shared/agents_remember/context_providers.py
B runtime/skills/U-01-core-skills/_shared/agents_remember/route_index.py
B installer/install-runtime.py
```

### Highest Complexity Blocks

Provider lifecycle:

```text
F runtime/scripts/provider-lifecycle.py:2466 grepai_run
D runtime/scripts/provider-lifecycle.py:845 cgc_backend_start
D runtime/scripts/provider-lifecycle.py:2214 grepai_backend_start
D runtime/scripts/provider-lifecycle.py:2364 grepai_install
C runtime/scripts/provider-lifecycle.py:1315 cgc_patch
C runtime/scripts/provider-lifecycle.py:3036 main
C runtime/scripts/provider-lifecycle.py:788 cgc_backend_status
C runtime/scripts/provider-lifecycle.py:2778 watchers_run
C runtime/scripts/provider-lifecycle.py:2160 grepai_backend_status
C runtime/scripts/provider-lifecycle.py:1044 cgc_install
```

Provider setup:

```text
D runtime/scripts/provider-setup.py:439 cgc_seed_bundle
C runtime/scripts/provider-setup.py:368 rewrite_cgc_bundle_paths
C runtime/scripts/provider-setup.py:77 configured_cgc_repo_root
```

Context resolver:

```text
F runtime/skills/U-01-core-skills/C-08-ar-coordination-context-resolver/scripts/ar_coordination_context_resolver.py:483 parse_settings_block
C runtime/skills/U-01-core-skills/C-08-ar-coordination-context-resolver/scripts/ar_coordination_context_resolver.py:1105 build_coordination_context
C runtime/skills/U-01-core-skills/C-08-ar-coordination-context-resolver/scripts/ar_coordination_context_resolver.py:795 resolve_storage_for_source
C runtime/skills/U-01-core-skills/C-08-ar-coordination-context-resolver/scripts/ar_coordination_context_resolver.py:889 resolve_cross_repo_entry
C runtime/skills/U-01-core-skills/C-08-ar-coordination-context-resolver/scripts/ar_coordination_context_resolver.py:1017 resolve_coordination_context
```

Worktree manager:

```text
D runtime/skills/U-01-core-skills/C-09-git-worktree-manager/scripts/git_worktree_manager.py:1126 command_integrate
D runtime/skills/U-01-core-skills/C-09-git-worktree-manager/scripts/git_worktree_manager.py:1271 command_cleanup
C runtime/skills/U-01-core-skills/C-09-git-worktree-manager/scripts/git_worktree_manager.py:1031 validate_integrate_contract
C runtime/skills/U-01-core-skills/C-09-git-worktree-manager/scripts/git_worktree_manager.py:246 command_start
C runtime/skills/U-01-core-skills/C-09-git-worktree-manager/scripts/git_worktree_manager.py:829 command_closeout
C runtime/skills/U-01-core-skills/C-09-git-worktree-manager/scripts/git_worktree_manager.py:445 prepare_memory_for_start
```

Shared provider helpers:

```text
C runtime/skills/U-01-core-skills/_shared/agents_remember/context_providers.py:843 cgc_runtime_layout_from_provider_settings
C runtime/skills/U-01-core-skills/_shared/agents_remember/context_providers.py:1098 cleanup_cgc_runtime_artifacts
C runtime/skills/U-01-core-skills/_shared/agents_remember/context_providers.py:556 grepai_roots_from_provider_settings
C runtime/skills/U-01-core-skills/_shared/agents_remember/context_providers.py:789 cgc_runtime_layout
C runtime/skills/U-01-core-skills/_shared/agents_remember/context_providers.py:729 grepai_workspace_config_text
```

---

## Architectural Interpretation

The quality data supports the context-kernel direction.

The codebase does not appear broken: tests pass. The problem is that several core operational domains have accumulated too much orchestration inside script-shaped files.

The main design pressure is:

```text
script CLI boundary
  mixed with parsing
  mixed with service logic
  mixed with filesystem layout
  mixed with provider process control
  mixed with rendering
  mixed with safety policy
```

This is exactly the kind of pressure the MCP/kernel split should address.

Good extraction candidates:

1. `runtime/scripts/provider-lifecycle.py`
   - Split CLI/parser/rendering from provider lifecycle services.
   - Separate GrepAI backend, GrepAI watcher, CGC backend, CGC patching, CGC watcher, and aggregate watcher orchestration.
   - Introduce transcript/run-artifact service before MCP provider queries.

2. `C-08 ar_coordination_context_resolver.py`
   - Split settings parsing from context assembly.
   - `parse_settings_block` is the clearest complexity hotspot.
   - This is relevant to the future `context.packet` service.

3. `C-09 git_worktree_manager.py`
   - Split status/plan, closeout, integrate, cleanup, and provider-prep concerns.
   - Keep approval gates visible and explicit.
   - Do not expose mutation through MCP before read/status planning is stable.

4. `context_providers.py`
   - Split layout dataclasses, settings expansion, filesystem sync, cleanup, patch verification, and provider env construction.
   - This is likely the shared service substrate for both CLI and MCP.

5. `check_onboarding_drift.py`
   - Split classification logic from report rendering and CLI handling.
   - This matters if `drift.check` becomes a context-kernel service.

---

## Recommended Next Decisions

### D1 - Fix Test Discovery Or Add A Canonical Test Wrapper

Before wider refactor, make the canonical test command obvious and machine-runnable.

Options:

- add a small script/command wrapper
- document the exact test command in dev docs
- adjust test discovery layout

Do not rely on root `python -m unittest discover` in automation as-is.

### D2 - Do A Small Mechanical Cleanup Separately

The five high-signal simple Ruff findings can be handled in a small cleanup task:

- unused imports
- useless conditional
- f-string without placeholders

Avoid mixing broad E501 cleanup with architecture work.

### D3 - Use Phase 1 To Decide Service Boundaries Before MCP Tool Count

The first design decision should not be "one MCP tool or many." It should be:

```text
Which service boundary owns this truth?
```

Then MCP tools become thin controllers over those services.

### D4 - Prioritize Read-Only Kernel Services

The data argues for this first extraction sequence:

1. context resolver service cleanup enough to support context packet
2. provider status/query service and transcript persistence
3. MCP read surface over those services
4. skill rewiring
5. provider lifecycle mutations later
6. worktree mutation later

### D5 - Keep E501 As Noise Until Formatting Policy Is Decided

Line length is the largest Ruff category. It should be handled only after deciding whether the repo wants:

- a formatter
- a different line-length policy
- selective ignores for docs/test tables
- gradual cleanup in touched files only

---

## Proposed Phase 1 Inputs

Phase 1 should start with these concrete questions:

1. What importable package layout should own kernel services?
2. What service should produce the first context packet?
3. Should provider transcript persistence be extracted before MCP server scaffolding?
4. Which direct CLI paths must remain and why?
5. Which fallbacks are actually justified in a pre-1.0 project?

---

## Open Review Questions

- Should Phase 0 include a separate mechanical cleanup task before Phase 1?
- Should root-level test discovery be fixed immediately?
- Should Phase 1 begin with context packet service extraction or MCP server scaffolding research?
- Should E501 be ignored temporarily while architecture work proceeds?
