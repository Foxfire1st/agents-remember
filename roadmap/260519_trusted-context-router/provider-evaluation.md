# Provider Evaluation

**Task:** `260519_trusted-context-router`  
**Repo:** `agents-remember-md`  
**Started:** 2026-05-20  
**Status:** in progress

This artifact records the provider lifecycle and output-volume checks requested
before deeper C-04 routing integration. Provider output is treated as candidate
routing evidence only; source files, onboarding, drift checks, branch validity,
and memory promotion remain the proof layer.

## Runtime Layout Verified

Active provider state now uses the contained runtime layout:

```text
ar-coordination/providers/
  _venvs/codegraphcontext/
  requirements/codegraphcontext.txt
  patches/codegraphcontext/codegraphcontext-0.4.10-cgcignore-runtime-root.patch
  grepai/memory-repos/
  codegraphcontext/agents-remember-md/.codegraphcontext/
  codegraphcontext/tensorflow/.codegraphcontext/
```

The earlier CGC experiment that produced `providers/codegraphcontext/_venv`,
`providers/codegraphcontext/<repo>/home/.codegraphcontext/`, and a split
`providers/codegraphcontext/<repo>/db/kuzu` layout is no longer active.

CGC v0.4.10 was installed into
`providers/_venvs/codegraphcontext/` from
`providers/requirements/codegraphcontext.txt` and patched with
`codegraphcontext-0.4.10-cgcignore-runtime-root`. The patch verification passes
for the installed `codegraphcontext/core/cgcignore.py`.

## Lifecycle Findings

| Provider | Scope/root                     | Lifecycle result                                                                                                                                                                                                                                                                                             | Artifact containment                                                                                                                                                                         | Current judgement                                                                                    |
| -------- | ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| GrepAI   | `ar-coordination/memory-repos` | Managed watcher starts only when the command can reach local Ollama. A sandboxed start failed with `socket: operation not permitted`; the escalated managed start succeeded. `grepai status --no-ui` reports 1402 files, 4959 chunks, 29.6 MB, watcher running with PID `2535475`.                           | GrepAI index remains provider-owned under `memory-repos/.grepai`; Agents Remember runtime logs/state live under `providers/grepai/memory-repos/`.                                            | Usable as the semantic memory provider when watcher status is checked from the real runtime context. |
| CGC      | `agents-remember-md`           | `provider-lifecycle.py cgc refresh` successfully ran `cgc index <repo> --force`. It took 380.546 seconds; CGC reported successful re-index in 378.32 seconds and emitted the known asyncio executor shutdown warning. Managed `cgc watch` is running with PID `595434` after the relationship-probe restart. | Clean. KuzuDB files are under `providers/codegraphcontext/agents-remember-md/.codegraphcontext/db/kuzu`; no `.cgcignore`, `.codegraphcontext`, or `CGC_REPORT.md` exists in the source repo. | Usable as a relationship provider, but hard refresh cost is high and should be budgeted explicitly.  |
| CGC      | `tensorflow`                   | Clean-layout refresh is currently running. The contained runtime DB has appeared under `providers/codegraphcontext/tensorflow/.codegraphcontext/db/`, but final stats are not available yet.                                                                                                                 | Clean so far. No `.cgcignore`, `.codegraphcontext`, or `CGC_REPORT.md` exists in the TensorFlow source repo.                                                                                 | Pending. Do not claim TensorFlow relationship coverage until refresh exits and `cgc stats` succeeds. |

## Query / Output Checks

| Provider | Scope/root                     | Transport | Query / command shape                                        | Returned volume                                                       | Summary of returned candidates                                                                                                             | Verification sample                                                                                    | Quality/quantity judgement                                                                          | Design consequence                                                                                     |
| -------- | ------------------------------ | --------- | ------------------------------------------------------------ | --------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| GrepAI   | `ar-coordination/memory-repos` | CLI       | Semantic route query for memory/onboarding root concepts     | 5 compact search hits in the earlier spike                            | Returned route candidates in memory onboarding, including overview-level entries.                                                          | Top hit was checked against onboarding content.                                                        | Useful when the model knows the concept but not the file route; output needs capping.               | Keep `maxSemanticQueriesPerPacket: 1` for the initial router test.                                     |
| GrepAI   | `ar-coordination/memory-repos` | CLI       | TensorFlow check numerics / XLA semantic query               | 5 compact search hits in the earlier spike                            | Returned the expected `tensorflow/compiler/tf2xla/kernels/check_numerics_op.cc.md` onboarding route.                                       | Source confirmed the registration anchor in `tensorflow/compiler/tf2xla/kernels/check_numerics_op.cc`. | Good candidate-routing quality for known concept / unknown route.                                   | GrepAI should return candidate onboarding routes, not final answers.                                   |
| CGC      | `agents-remember-md`           | CLI       | `cgc stats /home/mohamedreadone/Projects/agents-remember-md` | Files: 177; Functions: 591; Classes: 25; Imported Modules: 34         | Confirms CGC ingested the repo into the contained KuzuDB runtime.                                                                          | `cgc list` shows `agents-remember-md` as an indexed project.                                           | Useful and contained, but slow to build.                                                            | CGC can be used for Relationship substrate tests after watcher/status checks.                          |
| CGC      | `agents-remember-md`           | CLI       | `cgc stats` overall database check                           | Repositories: 1; Files: 177; Functions: 506; Classes: 25; Modules: 34 | Confirms the runtime DB is queryable from the managed environment.                                                                         | DB file exists at `.codegraphcontext/db/kuzu` and is about 73 MB after indexing.                       | Good enough for closeout smoke coverage; query-quality evaluation still needs caller/callee probes. | Add bounded relationship probes before final C-04 query budgets.                                       |
| CGC      | `agents-remember-md`           | CLI       | `cgc find name cgc_runtime_layout`                           | 1 match                                                               | Returned `cgc_runtime_layout` in `runtime/skills/U-01-core-skills/_shared/agents_remember/context_providers.py:120`.                       | Source path and line were checked against the helper.                                                  | Good precise anchor lookup.                                                                         | Keep exact-name find as a supported relationship-anchor probe.                                         |
| CGC      | `agents-remember-md`           | CLI       | `cgc analyze callers cgc_runtime_layout`                     | 3 caller rows                                                         | Returned `cgc_layout_from_args`, `test_cgc_layout_uses_managed_runtime_root`, and `test_ensure_cgc_runtime_layout_writes_pinned_defaults`. | Source/test names match the changed helper and unit test.                                              | Good quantity for a narrow relationship question.                                                   | Keep caller/callee probes bounded and require source follow-up before claims.                          |
| CGC      | `agents-remember-md`           | CLI       | `cgc analyze calls cgc_runtime_layout`                       | 1 callee row                                                          | Returned `stable_provider_id` in the same helper.                                                                                          | Source path and helper relationship are plausible and source-checkable.                                | Good relationship signal.                                                                           | Good candidate for Relationship substrate when an anchor is known.                                     |
| CGC      | `tensorflow`                   | CLI       | `provider-lifecycle.py cgc refresh --repo-id tensorflow`     | Pending                                                               | Indexing started and created contained KuzuDB files.                                                                                       | Source artifact probe remained clean.                                                                  | Pending.                                                                                            | Record final duration/stats before starting a TensorFlow watcher or using it for relationship routing. |

## CGC Configuration Detail

CGC v0.4.10 accepts `CGC_RUNTIME_DB_TYPE`, `KUZUDB_PATH`, and
`CGC_RUNTIME_DB_PATH` as process environment, and those values are needed to
force the contained KuzuDB path. However, `cgc doctor` reports those keys as
invalid if they are persisted in `<runtimeRoot>/.codegraphcontext/.env`.

The lifecycle helper therefore keeps these as process-only controls and writes
only CGC-recognized persisted keys such as `DEFAULT_DATABASE`, `FALKORDB_PATH`,
`FALKORDB_SOCKET_PATH`, `LOG_FILE_PATH`, `DEBUG_LOG_PATH`, and
`ENABLE_AUTO_WATCH` into `.env`.

## CGC Locking Detail

When the managed `cgc watch` process is running against the embedded KuzuDB
runtime, separate CLI relationship queries can fail with:

```text
Database Connection Error: IO exception: Could not set lock on file
```

Stopping the managed watcher, running the bounded CLI probes, and restarting the
watcher allowed `find`, `analyze callers`, and `analyze calls` to succeed.
Initial C-04 guidance should therefore treat CLI query probes as mutually
exclusive with the managed embedded watcher unless a future CGC transport
supports shared read sessions safely.

## Closeout TODO

- Capture final TensorFlow CGC duration and stats after the current refresh exits.
- Run final source/memory validation checks after the TensorFlow refresh resolves.
- Keep the `agents-remember-md` CGC watcher running after any additional CLI
  relationship probes that require stop-query-restart around the KuzuDB lock.
