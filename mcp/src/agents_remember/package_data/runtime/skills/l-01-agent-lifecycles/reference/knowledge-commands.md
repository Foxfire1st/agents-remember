# Knowledge commands

Read this at the role's command step. It grants no write, review, commit, integration or
cleanup authority; preserve the task contract, role gates and independent acceptance.
Producer lists describe observed code; the Curator authors knowledge judgments.

Replace illustrative repository `app`, roots `/work/code` and `/work/memory`, contract
`/work/series-contract.md`, and evidence `/work/report/` with supplied bindings. MCP examples show tool calls, not shell commands;
leaf readers preserve their complete supplied `task_context`.

For CLI work preserve the original admitted interpreter: launch receipt
`toolServer.command[0]` or selected server startup `command`. The resolved
`servingBuild.pythonExecutable` is comparison identity, not launch authority. Follow
[serving-build binding](curator-writer-details.md#step-2--serving-build-command-and-options)
for explicit package selection and stamp comparison. `AR_PYTHON` below denotes that admitted
interpreter; `AR_SERVING_PARENT` is the stamped package parent. PATH `agents-remember` can
select an older build. If binding is unavailable, report it; do not infer another interpreter
or install a build. Leaf-produced code cannot write its own memory.

## Every public CLI subcommand

Registry: `mcp/src/agents_remember/cli/__main__.py::build_parser` and its registrars.
Nested commands follow; `paseo` and `knowledge-census` require a child.

### Knowledge and memory

| Command | What it does | Who and when | Example |
| --- | --- | --- | --- |
| `handoff-check` | Checks every producer entry through writer readers against the frozen tree; optional memory checks record links. Writes nothing; details below. | Worker/Reviewer before sending a freeze’s producer list; Curator requires PASS for that freeze. | `agents-remember handoff-check /work/report/producer-list.json --code /work/code --tree FROZEN_TREE --memory /work/memory` |
| `knowledge-ingest` | Plans/writes records, sidecar entries and history in the contract’s converted memory. Requires `--authorization-ref`; `--commit` writes files, never Git. `--crossing` uses the master’s open crossing. | Curator during admitted curation/crossing repair, after list/candidate checks. | `agents-remember knowledge-ingest --contract /work/series-contract.md --list /work/report/producer-list.json --authorization-ref admitted-curation --json` |
| `knowledge-bootstrap` | Plans/writes a taskless foundation wave. Requires list/authorization; `--commit` writes files; `--wave` names history. | Admitted bootstrap author at the foundation-writing step. | `agents-remember knowledge-bootstrap --repo app --list /work/report/foundation.json --authorization-ref admitted-bootstrap --wave W1 --json` |
| `knowledge-format` | Canonicalizes knowledge JSON formatting; it does not validate meaning. Directories contribute non-excluded JSON files. Without `--check`, it rewrites parseable noncanonical files. | Curator or maintainer when checking or formatting admitted authored files. | `agents-remember knowledge-format --check /work/memory/knowledge` |
| `knowledge-convert` | Converts legacy memory/database to text, paired to code. `--check` reports without writing. | Conversion owner: pre-baseline in place, otherwise a new leaf’s first memory change. Supported sync owns crossing conversion. | `agents-remember knowledge-convert /work/memory --code /work/code --code-commit HEAD --check --report /work/report/conversion.json` |
| `knowledge-validate` | Checks converted formats and integrity against paired code. Repeat `--base` for memory merge parents; without a base, authored records are judged as new. `--coordination-root` adds report-only requirement-endpoint observations. | Curator checking final memory; Reviewer or maintainer investigating validator findings for the selected tree. | `agents-remember knowledge-validate /work/memory --code /work/code --base MEMORY_BASE --json` |
| `knowledge-index` | Builds/reuses a derived SQLite index; text stays authoritative. Working capture uses disposable objects, not repository objects. Cache writes occur; duplicate IDs yield partial state. | Reader/maintainer inspecting retrieval; ordinary reads index on demand. | `agents-remember knowledge-index --memory-root /work/memory --cache-dir /work/cache/knowledge-index` |
| `knowledge-worklist` | Computes work from a contract or code B/C and memory K_B/K_C. May write captured objects/cache; `--output` writes a report, not judgments. | Curator starting/rechecking curation; maintainer reproducing the comparison. | `agents-remember knowledge-worklist --contract /work/series-contract.md --output /work/report/worklist.json` |
| `knowledge-routes` | Reports family routes, route state and mechanical suggestions, without choosing a route or changing records. Select families with repeated `--family`; `--code-commit` selects committed source instead of the working tree. | Curator inspecting routing evidence; Investigator or Reviewer checking a family manifestation. | `agents-remember knowledge-routes /work/memory --code /work/code --json` |
| `knowledge-census` | Selects `inventory` or `report`; no standalone operation. | Migration owner choosing the census step. | `agents-remember knowledge-census report /work/memory --json` |
| `knowledge-census inventory` | Pins the named code/memory baseline and writes the census inventory. `--scope` can restrict code directories. This is an authored census operation, not a read-only listing. | Admitted migration owner creating the census before migration decisions. | `agents-remember knowledge-census inventory /work/memory --census CENSUS-1 --code /work/code --code-commit HEAD --memory-commit HEAD` |
| `knowledge-census report` | Reads census measures and route status from the working tree or `--revision`; can narrow with `--census`. | Migration owner or Reviewer inspecting the pinned inventory and progress. | `agents-remember knowledge-census report /work/memory --census CENSUS-1 --json` |
| `knowledge-copies` | Inventories leftover retired-database copies. Default is dry run; `--apply --reviewed REPORT` deletes exactly the reviewed dry-run set or refuses. `--report` also writes the evidence file. | Authorized cleanup owner after reviewing candidates and retention requirements. | `agents-remember knowledge-copies --coordination-root /work/coordination --report /work/report/copies.json --json` |
| `memory-citations` | Reports references; `--fix` repairs regenerable sidecars, `--dry-run` previews. `--document` bounds repairs; `--census` writes/names full evidence. Only explicit pre-baseline admission exempts the contract. | Curator repairing moved anchors; census owner preparing evidence. | `agents-remember memory-citations --repo app --contract /work/series-contract.md --fix --document src/widget.py.md --dry-run` |
| `memory-backfill` | Plans attribution-trailer backfill. `--apply` rewrites history and moves named refs, guarded by the contract and unused rescue ref; `--expected-digest` binds the reviewed plan. | Authorized migration/maintenance owner, after the history-rewrite approval and preview. | `agents-remember memory-backfill --contract /work/series-contract.md` |

Ingest/bootstrap planning includes writer admission/validation and possibly base caching,
without write authority. `handoff-check` checks producer shape/targets without writing.

### Runtime and settings

| Command | What it does | Who and when | Example |
| --- | --- | --- | --- |
| `dashboard` | Runs the local dashboard. `--status` observes dashboard/host state; `--daemon` ensures the daemon and starts the installed host; `--stop` stops the dashboard without stopping host sessions. It does not install the host. | Developer or authorized runtime operator starting or inspecting the dashboard. | `agents-remember dashboard --config /work/settings.json --status` |
| `paseo` | Selects `provision`, `status` or `stop`; each requires the explicit active settings file. | Runtime operator choosing the host operation. | `agents-remember paseo status --config /work/settings.json` |
| `paseo provision` | Installs the pinned runtime/plugin and daemon configuration and starts the host. Changed start settings can restart it, ending open turns and permission prompts. | Authorized runtime operator provisioning the configured host. | `agents-remember paseo provision --config /work/settings.json` |
| `paseo status` | Reports whether the configured host daemon is running and its identity. | Runtime operator diagnosing the configured host. | `agents-remember paseo status --config /work/settings.json` |
| `paseo stop` | Stops the configured host; open turns and permission prompts end, while sessions remain resumable. | Authorized runtime operator intentionally stopping that host. | `agents-remember paseo stop --config /work/settings.json` |
| `tool-log-stats` | Reads tool-call logs and prints per-tool answer sizes; optional date limits and JSON output. | Investigator or maintainer measuring captured calls. | `agents-remember tool-log-stats /work/coordination/logs/mcp --json` |
| `upgrade-user-settings` | Migrates the old coordination settings file to the user's configuration directory. Default performs the migration; `--dry-run` previews. | Authorized settings owner during the settings migration. | `agents-remember upgrade-user-settings --coordination-root /work/coordination --dry-run` |
| `providers-remove` | Stops leftover provider watchers and removes their runtime folders. Default applies; `--dry-run` reports folders and sizes without stopping/removing. | Authorized runtime cleanup owner after inspecting the retired provider assets. | `agents-remember providers-remove --config /work/settings.json --dry-run` |
| `removed-stores` | Removes stores belonging to removed product parts. Default applies; `--dry-run` reports stores and sizes. | Authorized migration/cleanup owner following the removed-store procedure. | `agents-remember removed-stores --config /work/settings.json --dry-run` |

`start-leaf-enclosure` is an internal dispatcher branch, deliberately absent from the public
parser/help. Only the dashboard backend calls the serving interpreter with
`-P -m agents_remember.cli start-leaf-enclosure --config /work/settings.json`, supplying its
root-bound start request on stdin when a dashboard launch needs an enclosure. It is not an
agent's alternate worktree-start route. `agents-remember-mcp` is the separate MCP server
console script, not a subcommand of `agents-remember`.

## Knowledge MCP tools

Registration: `mcp/src/agents_remember/mcp/registration/knowledge.py`. Reads name locations; open the files natively.
Retrieved claims are not fresh proof. Use supplied memory/code bindings.

| Tool | What it does | Who and when | Example |
| --- | --- | --- | --- |
| `knowledge_read` | Names records/state in `source_context`, `invariant`, `family`, `record`, `weakness`, `dependent` views. Accepts path/ID/revision/filter/paging selectors; builds/reuses derived index, returns no text. | Any role before source confirmation; Curator examining realizations/siblings. | `knowledge_read({"memoryRoot":"/work/memory","view":"source_context","sourcePath":"src/widget.py","repositoryRoot":"/work/code"})` |
| `knowledge_diff` | Names changed records and a full comparison report for two memory trees; it infers no semantic effect label. An omitted `afterRevision` compares HEAD to the working tree and creates loose unreferenced Git objects; the report is also an artifact. | Investigator, Reviewer or Curator comparing exact memory states. | `knowledge_diff({"memoryRoot":"/work/memory","beforeRevision":"BASE","afterRevision":"CANDIDATE"})` |
| `knowledge_integrity_check` | Reads converted-memory validation: status, refusing/report-only counts, rule counts and bounded violations. A contract call also returns the latest leaf worklist and fact-file locations. It authors no knowledge and opens no legacy database. | Curator checking the final tree; Reviewer or Investigator diagnosing integrity at the selected pair. | `knowledge_integrity_check({"contractPath":"/work/series-contract.md"})` |
| `knowledge_write` | Mounted ingest/bootstrap writer: path or named inline handoff, leaf contract or taskless repo/wave, optional crossing. `commit:false` plans; `true` writes files, never Git. Names report/refusals. | Admitted Curator/bootstrap author after list/candidate/authorization checks. | `knowledge_write({"contractPath":"/work/series-contract.md","handoffPath":"/work/report/producer-list.json","authorizationRef":"admitted-curation","commit":false})` |

## Related knowledge workflow tools

Supporting registrations: `mcp/src/agents_remember/mcp/registration/core.py`, `mcp/src/agents_remember/mcp/registration/memory.py`, `mcp/src/agents_remember/mcp/registration/tasks.py`, `mcp/src/agents_remember/mcp/registration/worktrees.py`, `mcp/src/agents_remember/mcp/registration/closeout.py`.
There are no MCP conversion/index/formatter/worklist/handoff-check tools. Reads maintain
the index, writers format, crossing sync converts, and quality/integrity expose worklists.

| Tool | What it does | Who and when | Example |
| --- | --- | --- | --- |
| `server_info` | Reports the loaded serving build/interpreter, roots, repository settings and role binding; it does not enumerate tools. | Any role establishing the exact serving producer before using CLI details. | `server_info({})` |
| `context_packet` | Orients a repository/leaf. `include_drift:true` measures anchor drift; `include_freshness:true` fetches remotes and writes FETCH_HEAD. Neither repairs content. | Any role at orientation; leaf callers retain their supplied task context. | `context_packet({"repo_id":"app","task_context":{"task_document_ref":{"repository":"app","path":"SPRINT/leaf.json"},"contract_path":"/work/series-contract.md"}})` |
| `read_ar_files` | Names knowledge governing up to 20 repository-relative path strings, cards, overviews and action-asking record IDs. No file text; removed `refresh`/object entries are refused. | Any role retrieving bounded context before inspecting named source/card files. | `read_ar_files({"repo_id":"app","files":["src/widget.py"],"task_context":{"task_document_ref":{"repository":"app","path":"SPRINT/leaf.json"},"contract_path":"/work/series-contract.md"}})` |
| `runtime_install` | Reconciles package-owned coordinator scaffold, preserves user memory/data and serves skills from the package. The host step can provision configured runtime/plugin assets; it writes no settings and defers needed restarts. Preview with `dry_run:true`. | Authorized installation owner when preparing/updating the runtime; not a Curator memory-publication shortcut. | `runtime_install({"dry_run":true})` |
| `memory_quality_check` | Measures memory-quality findings/counts and, for full contract-scoped runs, the Curator checklist/worklist. It is not semantic certification; readiness also needs current validated coherence. | Curator checking final bytes before handoff; Manager at the memory gate. | `memory_quality_check({"request":{"mode":"sync","repo_id":"app","contract_path":"/work/series-contract.md"}})` |
| `citation_fix` | Repairs moved anchors in admitted converted-memory sidecars. Does not choose renamed/deleted/ambiguous targets. Preview with `dry_run:true`; background preview uses `mode:start` then polls the returned run ID. | Curator executing the bounded citation repair step. | `citation_fix({"repo_id":"app","contract_path":"/work/series-contract.md","documents":["src/widget.py.md"],"dry_run":true})` |
| `citation_note_revision` | Revises exactly one paired reference note while preserving targets, anchors and other references. Requires the sidecar SHA-256 as `base_digest`; stale digest/missing or empty input refuses without writes. | Curator correcting a note at the admitted note-revision step. | `citation_note_revision({"repo_id":"app","contract_path":"/work/series-contract.md","card":"src/widget.py.md","reference":1,"note":"Explains the current guard.","base_digest":"SIDECAR_SHA256","dry_run":true})` |
| `route_index_refresh` | Refreshes onboarding route indexes in the resolved memory scope. Use the leaf contract; unscoped writes need explicit pre-baseline admission. | Curator after admitted card/layout changes, previewing before applying. | `route_index_refresh({"repo_id":"app","contract_path":"/work/series-contract.md","dry_run":true})` |
| `memory_init` | Creates missing memory scaffold paths and optionally Git initialization, preserving existing onboarding. `initial_branch` is the foundation code branch; if omitted it must be resolvable from configuration/current code. | Initialization/bootstrap owner before first attributed memory baseline. | `memory_init({"repo_id":"app","initial_branch":"main","dry_run":true})` |
| `memory_baseline_status` | Reads drift and Git attribution state for first-baseline admission. | Bootstrap/baseline owner deciding whether adoption can proceed. | `memory_baseline_status({"repo_id":"app"})` |
| `memory_baseline_adopt` | Adopts and commits the first external-memory baseline. `accept_drift` only lifts the stale-anchor drift gate, not stale code/test reference-anchor refusals. | Authorized baseline owner after admission and explicit commit approval. | `memory_baseline_adopt({"repo_id":"app","dry_run":true})` |
| `memory_carryover_plan` | Plans landed converted cards/sidecars/records from another memory branch, observing official-code anchors and conflicts. Does not commit or author Curator history. | Recovery Curator/owner in the admitted carryover leaf, before apply. | `memory_carryover_plan({"repo_id":"app","contract_path":"/work/series-contract.md","source_memory":"/work/source-memory","official_code_ref":"OFFICIAL","source_code_ref":"SOURCE","old_base":"OLD_BASE"})` |
| `memory_carryover_apply` | Applies admitted carryover pairs/records, reanchors, indexes and validates; skips edited targets and restores bytes on refusal. History is authored by the Curator rather than copied. Requires `intent_note`; no Git commit. | Authorized recovery owner after inspecting the carryover plan. | `memory_carryover_apply({"repo_id":"app","contract_path":"/work/series-contract.md","source_memory":"/work/source-memory","official_code_ref":"OFFICIAL","source_code_ref":"SOURCE","old_base":"OLD_BASE","intent_note":"Apply reviewed landed carryover."})` |
| `curator_coherence` | Prepares/publishes/inspects/validates candidate-bound judgments. Publication binds requirement, attempt, predecessor, trees, topology, intent, attestation and caller. Stable manifest is authority, not Markdown. | Curator completing coherence; Manager inspecting/validating candidate authority. | `curator_coherence({"request":{"action":"status","contract_path":"/work/series-contract.md"}})` |
| `worktree_sync` | Supported official-line sync, including memory crossing/conversion and gate judgment. Follow returned choice/resolution calls; preview uses `dry_run:true`. It is not a raw Git merge recipe. | Owner admitted by the sync procedure; Curator supplies content repair and Manager performs reserved sync actions. | `worktree_sync({"contract_path":"/work/series-contract.md","dry_run":true})` |
| `worktree_closeout_preview` | Previews proposed code/memory closeout and blockers without committing. Converted-memory gate evidence concerns the exact candidate tree, not the mere existence of history rows. | Manager/authorized closeout owner after independent code and memory review. | `worktree_closeout_preview({"contract_path":"/work/series-contract.md"})` |
| `worktree_closeout_apply` | Applies approved closeout, subject to the same candidate, review, memory and quality gates. A knowledge-gate refusal carries findings as `knowledge-gate-refused`; apply is not implicit in a successful preview. | Authorized closeout owner after explicit commit approval; never a Worker/Curator self-acceptance. | `worktree_closeout_apply({"contract_path":"/work/series-contract.md","intent_note":"Preview the approved closeout.","dry_run":true})` |

Quality `sync`/`start` accept execution fields; `poll` takes returned `run_id`, repository and
optional matching contract, without `checks`/`detail_limit`. Retain completion/candidate
identity: a started run is not clean. Citation polling retains repository/contract/run ID;
follow returned next arguments.

## Producer hand-off check

`handoff-check` checks the producer list before sending that freeze. It replaces neither
Curator authoring nor final integrity/quality/fired-link checks. Use the serving interpreter
and exact frozen tree:

```sh
PYTHONPATH="$AR_SERVING_PARENT" "$AR_PYTHON" -P -m agents_remember.cli handoff-check /work/report/producer-list.json \
  --code /work/code --tree FROZEN_TREE --memory /work/memory
```

It reads every entry through writer readers: list shape/rationale, targets, covers, weakness
anchors, dependent documents, `stands_on`, proof test cases and `still_rejected` repoint targets
in that candidate. Proofs may use `path::name`, `{path, symbol}` or an admitted line-range locator
covering one test case; a bare test file is a finding. Repoint shape/target checks do not
pretend to validate a fired link: the output states `fired link: checked by the writer`.
With `--memory`, record links must name stored, unretired records; without it the output
states `record links: not run`. It takes no worklist input and writes neither files nor Git
objects: candidate resolution reads `CodeSnapshot.of_tree`, not working-tree capture.

Exit 0: clean; 1: findings (entry, field, reason) or unchecked record links; 2: invocation
error. Name result/list/freeze in the handoff; PASS does not prove prospective writer admission. See
[curator-handoff-list.md](../templates/curator-handoff-list.md) for the producer format and
[curator-writer-details.md](curator-writer-details.md) for writer admission and final checks.

## Source map

CLI sources: `mcp/src/agents_remember/cli/`; command modules own arguments/effects.
`knowledge_census.py::add_arguments` and `paseo_runtime.py::_COMMANDS` own nested names;
`leaf_enclosure_start.py::COMMAND` owns internal dispatch. MCP registrations above own tool
signatures; `mcp/src/agents_remember/models/memory.py` and `mcp/src/agents_remember/models/lifecycles/curator_coherence.py` own request shapes.
Follow these sources and writer readers, not an invented parallel tool surface.
