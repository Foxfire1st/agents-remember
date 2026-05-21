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
  _bin/grepai(.exe)
  _venvs/codegraphcontext/
  requirements/codegraphcontext.txt
  requirements/grepai.txt
  requirements/codegraphcontext-falkordb-docker.lock
  patches/codegraphcontext/
  grepai/memory-repos/
  codegraphcontext/agents-remember-md/.codegraphcontext/
  codegraphcontext/resolve_auto_editor/.codegraphcontext/
  codegraphcontext/tensorflow/.codegraphcontext/
ar-coordination/provider-data/
  codegraphcontext/falkordb/
```

The earlier CGC experiment that produced `providers/codegraphcontext/_venv`,
`providers/codegraphcontext/<repo>/home/.codegraphcontext/`, and a split
legacy split DB layout is no longer active.

CGC v0.4.10 was installed into
`providers/_venvs/codegraphcontext/` from
`providers/requirements/codegraphcontext.txt` with explicit Tree-Sitter parser
dependency pins, then patched with the managed patches
`codegraphcontext-0.4.10-cgcignore-runtime-root-v2` and
`codegraphcontext-0.4.10-windows-delete-prefix-v1`. Patch verification passes
for the installed `codegraphcontext/core/cgcignore.py` and
`codegraphcontext/tools/indexing/persistence/writer.py`.

The explicit parser pins are required in practice. On Windows with Python
3.13, `codegraphcontext==0.4.10` installed without `tree_sitter` because the
package metadata excludes the parser dependencies for Python 3.13. A forced
refresh in that state succeeded but produced a file-only graph with 162 files
and 0 functions/classes/modules. Installing and pinning
`tree-sitter==0.25.2`, `tree-sitter-language-pack==0.13.0`, and
`tree-sitter-c-sharp==0.23.5` fixed symbol extraction; `cgc doctor` then
reported Tree-Sitter and the language pack healthy.

GrepAI v0.35.0 is now represented by the source-copied
`providers/requirements/grepai.txt` pin and the installed
`providers/_bin/grepai.exe` binary on Windows.

The CGC backend target is FalkorDB Docker. The current managed runtime uses the
browser-capable `falkordb/falkordb:v4.18.7` image, one lifecycle-owned DBMS
container per coordination root, and loopback-only Redis/FalkorDB plus browser
ports. Per-repo CGC provider instances remain separate at the Agents Remember
lifecycle layer and share the same FalkorDB DBMS through repo-scoped graph
namespaces.

## Lifecycle Findings

| Provider | Scope/root                     | Lifecycle result                                                                                                                                                                                                                                                                                             | Artifact containment                                                                                                                                                                         | Current judgement                                                                                    |
| -------- | ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| GrepAI   | `ar-coordination/memory-repos` | Windows managed watcher is lifecycle-owned with PID `37160`. `grepai status --no-ui` reports 373 files, 2616 chunks, 16.2 MB, provider `ollama (nomic-embed-text)`. Native `grepai watch --status` still reports not running because it only sees GrepAI's own background daemon, not the supervised foreground `watch --no-ui` process. | GrepAI index remains provider-owned under `memory-repos/.grepai`; Agents Remember runtime logs/state live under `providers/grepai/memory-repos/`; binary lives under `providers/_bin/`. | Usable as the semantic memory provider when lifecycle-owned PID plus index status are checked together. |
| CGC      | `agents-remember-md`           | FalkorDB Docker refresh succeeded after explicit Tree-Sitter parser dependencies were installed. `cgc stats` reports 179 files, 570 functions, 25 classes, and 40 modules in graph `cgc_agents_remember_md`. | Clean. Runtime state is under `providers/codegraphcontext/agents-remember-md/.codegraphcontext/`; no `.cgcignore`, `.codegraphcontext`, or `CGC_REPORT.md` exists in the source repo. | Usable relationship provider instance on the target backend. |
| CGC      | `resolve_auto_editor`          | Initial refresh crashed after indexing gitignored `samples/` and `/tools` paths. The root cause was managed-context routing plus CGC's Windows hard-refresh delete-prefix bug. After the two managed patches and a scoped hard refresh, FalkorDB reports 146 files, 908 functions, 119 classes, and 0 file nodes under root `samples/`, root `tools/`, or `.tmp_drt`. | Clean. Runtime root exists under `providers/codegraphcontext/resolve_auto_editor/.codegraphcontext/`; no source-repo provider artifacts were found. | Usable after the managed patches; keep the gitignore and delete-prefix checks as install/doctor evidence. |
| CGC      | `tensorflow`                   | FalkorDB Docker refresh completed with return code 0 after about 1473 seconds. Direct FalkorDB query reports 11740 files, 98106 functions, and 12928 classes in graph `cgc_tensorflow`. | Clean. No `.cgcignore`, `.codegraphcontext`, or `CGC_REPORT.md` exists in the TensorFlow source repo. | Covered, but first full indexing is expensive; branch movement should use watcher/incremental reconciliation rather than implicit hard refresh. |
| CGC backend | FalkorDBLite native on Windows | Isolated temp-venv install of `falkordblite` failed because `redislite` reports that `win32` is unsupported. CGC source also hard-blocks FalkorDB Lite on Windows and recommends WSL or Docker. | No provider DB touched; test venv was outside provider runtime. | Not a cross-platform default. Accept only on Linux/macOS with Python 3.12+ after a separate smoke test. |
| CGC backend | FalkorDB Docker | Docker Desktop is running; container `ar-cgc-falkordb` answers `PONG`; browser UI is exposed at `http://127.0.0.1:3000`; Redis/FalkorDB is loopback-bound at `127.0.0.1:6379`. The image lock records `falkordb/falkordb:v4.18.7` and digest `sha256:e93fcd753fe612fb0a222166a0620a1ae31b826a12f223c3b6d06038d9d7a364`. | DBMS data belongs under `provider-data/codegraphcontext/falkordb/data`; `providers/` is disposable reinstall scaffolding. | Managed CGC backend direction because it supports watcher plus query clients and gives a browser UI for inspection/debugging. |

## Query / Output Checks

| Provider | Scope/root                     | Transport | Query / command shape                                        | Returned volume                                                       | Summary of returned candidates                                                                                                             | Verification sample                                                                                    | Quality/quantity judgement                                                                          | Design consequence                                                                                     |
| -------- | ------------------------------ | --------- | ------------------------------------------------------------ | --------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| GrepAI   | `ar-coordination/memory-repos` | CLI       | Semantic route query for memory/onboarding root concepts     | 5 compact search hits in the earlier spike                            | Returned route candidates in memory onboarding, including overview-level entries.                                                          | Top hit was checked against onboarding content.                                                        | Useful when the model knows the concept but not the file route; output needs capping.               | Keep `maxSemanticQueriesPerPacket: 1` for the initial router test.                                     |
| GrepAI   | `ar-coordination/memory-repos` | CLI       | TensorFlow check numerics / XLA semantic query               | 5 compact search hits in the earlier spike                            | Returned the expected `tensorflow/compiler/tf2xla/kernels/check_numerics_op.cc.md` onboarding route.                                       | Source confirmed the registration anchor in `tensorflow/compiler/tf2xla/kernels/check_numerics_op.cc`. | Good candidate-routing quality for known concept / unknown route.                                   | GrepAI should return candidate onboarding routes, not final answers.                                   |
| CGC      | `agents-remember-md`           | FalkorDB  | `GRAPH.QUERY cgc_agents_remember_md "MATCH (f:File) RETURN count(f)"` | 179 files | Confirms the repo is indexed in the target FalkorDB Docker backend. | Docker `redis-cli` query returned in under 1 ms. | Good smoke coverage for the managed backend. | Keep direct backend count checks as provider doctor/smoke evidence. |
| CGC      | `agents-remember-md`           | FalkorDB  | Function/class/module count queries                           | 570 functions; 25 classes; 40 modules                                 | Confirms relationship-bearing nodes exist beyond file metadata.                                                                            | `cgc stats` against `cgc_agents_remember_md` after parser dependencies were installed.                  | Good enough for managed-backend smoke coverage; query-quality evaluation still needs bounded caller/callee probes. | Keep caller/callee probes bounded and source-checkable.                                                |
| CGC      | `agents-remember-md`           | CLI       | `cgc find name cgc_runtime_layout`                           | 1 match                                                               | Returned `cgc_runtime_layout` in `runtime/skills/U-01-core-skills/_shared/agents_remember/context_providers.py:298`.                       | Source path and line were checked against the helper.                                                  | Good precise anchor lookup.                                                                         | Keep exact-name find as a supported relationship-anchor probe.                                         |
| CGC      | `agents-remember-md`           | CLI       | `cgc analyze callers cgc_runtime_layout`                     | 5 caller rows                                                         | Returned `cgc_layout_from_args`, `cgc_runtime_layout_from_provider_settings`, `test_cgc_layout_uses_managed_runtime_root`, `test_ensure_cgc_runtime_layout_writes_pinned_defaults`, and `test_cleanup_cgc_runtime_artifacts_removes_stale_runtime_only`. | Source/test names match the changed helper and unit test.                                              | Good quantity for a narrow relationship question.                                                   | Keep caller/callee probes bounded and require source follow-up before claims.                          |
| CGC      | `agents-remember-md`           | CLI       | `cgc analyze calls cgc_runtime_layout`                       | 1 callee row                                                          | Returned `stable_provider_id` in the same helper.                                                                                          | Source path and helper relationship are plausible and source-checkable.                                | Good relationship signal.                                                                           | Good candidate for Relationship substrate when an anchor is known.                                     |
| CGC      | `resolve_auto_editor`          | FalkorDB  | File-count and ignored-path count queries                    | 146 files; 0 under root `samples/`; 0 under root `tools/` or `.tmp_drt` | Confirms the clean hard refresh removed stale nodes from the failed run and the managed `.cgcignore` excludes gitignored folders.            | `cgc refresh --repo-id resolve_auto_editor --timeout 900` returned 0 in 8.475 seconds after deleting the old index. | Good smoke coverage after the managed patches.                                                     | Treat top-level `.gitignore` inheritance and Windows delete-prefix patch as required CGC install checks. |
| CGC      | `tensorflow`                   | FalkorDB  | File/function/class count queries                            | 11740 files; 98106 functions; 12928 classes                           | Confirms TensorFlow completed its first full index on the target backend.                                                                  | Provider state records return code 0 and about 1473 seconds for the hard refresh.                      | Covered but expensive.                                                                              | Treat large-repo hard refresh as explicit opt-in; prefer incremental watcher reconciliation for branch diffs. |

## CGC Configuration Detail

CGC v0.4.10 accepts backend-selection and path controls as process environment,
but `cgc doctor` may report process-only controls as invalid when they are
persisted in `<runtimeRoot>/.codegraphcontext/.env`.

The lifecycle helper should therefore keep process-only controls out of the
persisted `.env` and write only CGC-recognized persisted keys such as
`DEFAULT_DATABASE`, `LOG_FILE_PATH`, `DEBUG_LOG_PATH`, and `ENABLE_AUTO_WATCH`.

On Windows, `HOME` and `USERPROFILE` must point at a child run/home directory,
not the CGC instance root itself. If they point at `<instanceRoot>`, CGC treats
`<instanceRoot>/.codegraphcontext` as global config and bypasses the local
runtime-owned `.cgcignore`. The managed environment now routes `HOME`,
`USERPROFILE`, `APPDATA`, `LOCALAPPDATA`, `PYTHONIOENCODING=utf-8`, and
`PYTHONUTF8=1` as process-only keys. That keeps CGC config discovery out of the
user's profile, preserves the local context, and avoids Unicode decode failures
from diagnostic output.

For FalkorDB Docker mode, CGC should use the remote backend:

```text
CGC_RUNTIME_DB_TYPE=falkordb-remote
DEFAULT_DATABASE=falkordb-remote
FALKORDB_HOST=127.0.0.1
FALKORDB_PORT=<allocated-redis-port>
FALKORDB_GRAPH_NAME=<repo-or-shared-namespace>
```

The Docker backend needs a provider-owned state object that records the image
pin/digest, container name/id, data volume or bind path, loopback Redis port,
loopback browser port, graph namespace strategy, and last successful client
health query. The full browser image is preferred over the server-only image so
the developer can inspect/debug the graph through the FalkorDB browser UI.

Runtime reinstall must preserve DBMS data. Reinstall can replace scaffolding,
venvs, copied requirements, config files, containers, and missing directories,
but deleting a FalkorDB volume/bind path, graph namespace, or repo index must be
an explicit destructive `purge-db`/`delete-index` action.

## CGC Incremental Indexing Detail

CodeGraphContext's watcher code is built around complete initial indexing plus
incremental updates. When a repo is already indexed, `watch` skips the initial
scan, starts a watchdog observer, and handles create/modify/delete/move events
with a debounced update path that reparses the changed file plus affected caller
and inheritance-neighbor files. This supports the intended branch-switch model:
complete baseline first, then cheap reconciliation for branch diffs.

`cgc index --force` is a different operation. It deletes the repository graph
and rebuilds it. That is useful for missing/corrupt/stale indexes, but it should
not be the default branch-switch path for large repos.

On Windows, CGC v0.4.10 needs a managed patch for hard refresh correctness:
repository paths are stored with backslashes, while the upstream delete helper
uses a slash-only child prefix. Without the patch, `cgc index --force` can leave
stale nodes from previously indexed ignored folders. The managed patch makes the
delete step match both slash and backslash child prefixes before rebuilding.

Current source inspection shows a gap: when a repo is already indexed,
`cgc watch` skips the initial scan and observes only future filesystem events.
It does not backfill source edits that landed while the watcher was down.
Until Agents Remember patches this behavior or CGC adds a non-destructive
catch-up/sync command, the workflow rule should be: start or verify the CGC
watcher before source edits and branch switches whenever CGC coverage matters.

## Discarded Embedded-Backend Locking Detail

The prototype embedded-backend spike showed that when the managed `cgc watch`
process owns the DB files, separate CLI relationship queries can fail with:

```text
Database Connection Error: IO exception: Could not set lock on file
```

Stopping the managed watcher, running the bounded CLI probes, and restarting the
watcher allowed `find`, `analyze callers`, and `analyze calls` to succeed.
This is one reason the embedded backend is not the managed direction.

FalkorDB Docker changes this operating model. The watcher and query commands
would connect as clients to a shared DBMS instead of opening embedded database
files from separate processes.

## Closeout TODO

- Add a CGC catch-up/sync fix or upstream issue for edits made while the watcher was down. Until then, require watcher-first behavior before source edits and branch switches.
- Add lifecycle state reporting for `missing`, `installed`, `configured`, `indexed`, `watching`, `stale`, `refreshing`, and `faulted`.
- Promote the two CGC managed patches into a removable patch-policy section with upstream issue links once the implementation leaves task-local hardening.
- Run bounded Windows relationship probes against the FalkorDB Docker backend after watcher lifecycle is active for all three repos.
