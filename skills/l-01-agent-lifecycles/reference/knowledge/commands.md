# Knowledge, workflow and runtime command reference

All eight roles use this reference before choosing or interpreting knowledge commands, workflow tools, runtime or settings commands. It gives effects, admitted interpreter and workspace bindings, asynchronous results, producer-list checks and owning-source locations; it grants no write, review, commit, integration or cleanup authority.

Preserve the task contract, role gates and independent acceptance. Producer lists describe observed code; the Curator authors knowledge judgments.

## Bind examples to the assigned workspace

Replace the illustrative repository `app`, roots `/work/code` and `/work/memory`, contract `/work/series-contract.md`, and evidence directory `/work/report/` with the supplied bindings.

Model Context Protocol (MCP) examples use JavaScript Object Notation (JSON) for tool calls, not shell commands. Leaf readers preserve their complete supplied `task_context`.

## Bind command-line work to the admitted interpreter

Before command-line interface (CLI) work, read [serving-build binding](serving-build.md) for the admitted interpreter, explicit package selection and stamp comparison. `AR_SERVING_PYTHON` denotes the admitted interpreter; `AR_SERVING_PARENT` is the stamped package parent.

An `agents-remember` command found through `PATH` can select an older build. When the admitted binding is unavailable, follow the [admitted-interpreter procedure](serving-build.md#obtain-the-admitted-interpreter) for the response it owns. Leaf-produced code cannot write its own memory.

## Choose a public command-line subcommand

The public command registry is `mcp/src/agents_remember/cli/__main__.py::build_parser` and its registrars.
`paseo` and `knowledge-census` require subcommands.

### Knowledge and memory

| Command | What it does | Who and when | Example |
| --- | --- | --- | --- |
| `handoff-check` | Checks every producer entry through writer readers against the frozen tree; optional memory checks record links. Writes nothing; details below. | Worker/Reviewer before sending a freeze’s producer list; Curator requires PASS for that freeze. | `agents-remember handoff-check /work/report/producer-list.json --code /work/code --tree FROZEN_TREE --memory /work/memory` |
| `knowledge-ingest` | Plans/writes records, sidecar entries and history in the contract’s converted memory. Requires `--authorization-ref`; `--commit` writes files, never Git. `--crossing` uses the master’s open crossing. | Curator during admitted curation/crossing repair, after list/candidate checks. | `PYTHONPATH="$AR_SERVING_PARENT" "$AR_SERVING_PYTHON" -P -m agents_remember.cli knowledge-ingest --contract /work/series-contract.md --list /work/report/producer-list.json --authorization-ref admitted-curation --json` |
| `knowledge-bootstrap` | Plans or writes one foundation wave for a repository. It takes the repository and a wave name instead of a leaf contract; the foundation work itself runs inside an ordinary task, as the [repository-foundation procedure](../curator/repository-foundation.md#enter-through-the-real-foundation-task) states. Requires list/authorization; `--commit` writes files; `--wave` names history. | Admitted bootstrap author at the foundation-writing step. | `PYTHONPATH="$AR_SERVING_PARENT" "$AR_SERVING_PYTHON" -P -m agents_remember.cli knowledge-bootstrap --repo app --list /work/report/foundation.json --authorization-ref admitted-bootstrap --wave W1 --json` |
| `knowledge-format` | Canonicalizes knowledge JSON formatting; it does not validate meaning. Directories contribute non-excluded JSON files. Without `--check`, it rewrites parseable noncanonical files. | Curator or maintainer when checking or formatting admitted authored files. | `agents-remember knowledge-format --check /work/memory/knowledge` |
| `knowledge-validate` | Checks converted formats and integrity against paired code. Repeat `--base` for memory merge parents; without a base, authored records are judged as new. `--coordination-root` adds report-only requirement-endpoint observations. | Curator checking final memory; Reviewer or maintainer investigating validator findings for the selected tree. | `agents-remember knowledge-validate /work/memory --code /work/code --base MEMORY_BASE --json` |
| `knowledge-index` | Builds/reuses a derived SQLite index; text stays authoritative. Working capture uses disposable objects, not repository objects. Cache writes occur; duplicate IDs yield partial state. | Reader/maintainer inspecting retrieval; ordinary reads index on demand. | `agents-remember knowledge-index --memory-root /work/memory --cache-dir /work/cache/knowledge-index` |
| `knowledge-worklist` | Computes work from a contract or the base/candidate code trees (`B`/`C`) and their paired memory trees (`K_B`/`K_C`). May write captured objects/cache; `--output` writes a report, not judgments. | Curator starting/rechecking curation; maintainer reproducing the comparison. | `agents-remember knowledge-worklist --contract /work/series-contract.md --output /work/report/worklist.json` |
| `knowledge-routes` | Reports family routes, route state and mechanical suggestions, without choosing a route or changing records. Select families with repeated `--family`; `--code-commit` selects committed source instead of the working tree. | Curator inspecting routing evidence; Investigator or Reviewer checking a family manifestation. | `agents-remember knowledge-routes /work/memory --code /work/code --json` |
| `memory-citations` | Reports references; `--fix` repairs regenerable sidecars, `--dry-run` previews. `--document` bounds repairs; `--census` writes/names full evidence. Only explicit pre-baseline admission exempts the contract. | Curator repairing moved anchors; census owner preparing evidence. | `agents-remember memory-citations --repo app --contract /work/series-contract.md --fix --document src/widget.py.md --dry-run` |

Ingest/bootstrap planning checks writer admission and validation and may cache the base. A planning result grants no write authority; the existing admission requirements still apply.

### Runtime and settings

| Command | What it does | Who and when | Example |
| --- | --- | --- | --- |
| `dashboard` | Runs the local dashboard. `--status` observes dashboard/host state; `--daemon` ensures the daemon and starts the installed host; `--stop` stops the dashboard without stopping host sessions. It does not install the host. | Developer or authorized runtime operator starting or inspecting the dashboard. | `agents-remember dashboard --config /work/settings.json --status` |
| `paseo` | Selects `provision`, `status` or `stop`; each requires the explicit active settings file. | Runtime operator choosing the host operation. | `agents-remember paseo status --config /work/settings.json` |
| `paseo provision` | Installs the pinned runtime/plugin and daemon configuration and starts the host. Changed start settings can restart it, ending open turns and permission prompts. | Authorized runtime operator provisioning the configured host. | `agents-remember paseo provision --config /work/settings.json` |
| `paseo status` | Reports whether the configured host daemon is running and its identity. | Runtime operator diagnosing the configured host. | `agents-remember paseo status --config /work/settings.json` |
| `paseo stop` | Stops the configured host; open turns and permission prompts end, while sessions remain resumable. | Authorized runtime operator intentionally stopping that host. | `agents-remember paseo stop --config /work/settings.json` |
| `tool-log-stats` | Reads tool-call logs and prints per-tool answer sizes; optional date limits and JSON output. | Investigator or maintainer measuring captured calls. | `agents-remember tool-log-stats /work/coordination/logs/mcp --json` |

`start-leaf-enclosure` is an internal dispatcher branch, deliberately absent from the public
parser/help. Only the dashboard backend calls the serving interpreter with
`-P -m agents_remember.cli start-leaf-enclosure --config /work/settings.json`, supplying its
root-bound start request on standard input when a dashboard launch needs an enclosure. It is not an
agent's alternate worktree-start route. `agents-remember-mcp` is the separate MCP server
console script, not a subcommand of `agents-remember`.

## Choose a knowledge tool

Registration: `mcp/src/agents_remember/mcp/registration/knowledge.py`. Reads name locations; open the files natively.
Retrieved claims are not fresh proof. Use supplied memory/code bindings.

| Tool | What it does | Who and when | Example |
| --- | --- | --- | --- |
| `knowledge_read` | Names records/state in `source_context`, `invariant`, `family`, `record`, `weakness`, `dependent` views. Accepts path/ID/revision/filter/paging selectors; builds/reuses derived index, returns no text. | Any role before source confirmation; Curator examining realizations/siblings. | `knowledge_read({"memoryRoot":"/work/memory","view":"source_context","sourcePath":"src/widget.py","repositoryRoot":"/work/code"})` |
| `knowledge_diff` | Names changed records and a full comparison report for two memory trees; it infers no semantic effect label. An omitted `afterRevision` compares HEAD to the working tree and creates loose unreferenced Git objects; the report is also an artifact. | Investigator, Reviewer or Curator comparing exact memory states. | `knowledge_diff({"memoryRoot":"/work/memory","beforeRevision":"BASE","afterRevision":"CANDIDATE"})` |
| `knowledge_integrity_check` | Reads converted-memory validation: status, refusing/report-only counts, rule counts and bounded violations. A contract call also returns the latest leaf worklist and fact-file locations. It authors no knowledge. | Curator checking the final tree; Reviewer or Investigator diagnosing integrity at the selected pair. | `knowledge_integrity_check({"contractPath":"/work/series-contract.md"})` |
| `knowledge_write` | Mounted ingest/bootstrap writer: path or named inline handoff, leaf contract or repository/wave, optional crossing. `commit:false` plans; `true` writes files, never Git. Names report/refusals. | Admitted Curator/bootstrap author after list/candidate/authorization checks. | `knowledge_write({"contractPath":"/work/series-contract.md","handoffPath":"/work/report/producer-list.json","authorizationRef":"admitted-curation","commit":false})` |

## Use related workflow tools

Supporting registrations: `mcp/src/agents_remember/mcp/registration/core.py`, `mcp/src/agents_remember/mcp/registration/memory.py`, `mcp/src/agents_remember/mcp/registration/tasks.py`, `mcp/src/agents_remember/mcp/registration/worktrees.py`, `mcp/src/agents_remember/mcp/registration/closeout.py`.
Reads maintain the index, writers format, and quality/integrity expose worklists.

| Tool | What it does | Who and when | Example |
| --- | --- | --- | --- |
| `server_info` | Reports the loaded serving build/interpreter, roots, repository settings and role binding; it does not enumerate tools. | Any role establishing the exact serving producer before using CLI details. | `server_info({})` |
| `context_packet` | Orients a repository/leaf. `include_drift:true` measures anchor drift; `include_freshness:true` fetches remotes and writes FETCH_HEAD. Neither repairs content. | Any role at orientation; leaf callers retain their supplied task context. | `context_packet({"repo_id":"app","task_context":{"task_document_ref":{"repository":"app","path":"SPRINT/leaf.json"},"contract_path":"/work/series-contract.md"}})` |
| `read_ar_files` | Names knowledge governing up to 20 repository-relative path strings, cards, overviews and action-asking record IDs. It returns no file text. | Any role retrieving bounded context before inspecting named source/card files. | `read_ar_files({"repo_id":"app","files":["src/widget.py"],"task_context":{"task_document_ref":{"repository":"app","path":"SPRINT/leaf.json"},"contract_path":"/work/series-contract.md"}})` |
| `runtime_install` | Reconciles package-owned coordinator scaffold, preserves user memory/data and serves skills from the package. The host step can provision configured runtime/plugin assets and defers needed restarts. Installation copies the old convention tool-server settings file into the user config folder only when the destination is absent; it leaves the source and an existing destination unchanged. Preview with `dry_run:true`. | Authorized installation owner when preparing/updating the runtime; not a Curator memory-publication shortcut. | `runtime_install({"dry_run":true})` |
| `memory_quality_check` | Measures memory-quality findings/counts and, for full contract-scoped runs, the Curator checklist/worklist. It is not semantic certification; readiness also needs current validated coherence. | Curator checking final bytes before handoff; Manager at the memory gate. | `memory_quality_check({"request":{"mode":"sync","repo_id":"app","contract_path":"/work/series-contract.md"}})` |
| `citation_fix` | Repairs moved anchors in admitted converted-memory sidecars. Does not choose renamed/deleted/ambiguous targets. Preview with `dry_run:true`; background preview uses `mode:start` then polls the returned run ID. | Curator executing the bounded citation repair step. | `citation_fix({"repo_id":"app","contract_path":"/work/series-contract.md","documents":["src/widget.py.md"],"dry_run":true})` |
| `citation_note_revision` | Revises exactly one paired reference note while preserving targets, anchors and other references. Requires the sidecar’s 256-bit Secure Hash Algorithm (SHA-256) hash as `base_digest`; stale digest/missing or empty input refuses without writes. | Curator correcting a note at the admitted note-revision step. | `citation_note_revision({"repo_id":"app","contract_path":"/work/series-contract.md","card":"src/widget.py.md","reference":1,"note":"Explains the current guard.","base_digest":"SIDECAR_SHA256","dry_run":true})` |
| `route_index_refresh` | Refreshes onboarding route indexes in the resolved memory scope. Use the leaf contract; unscoped writes need explicit pre-baseline admission. | Curator after admitted card/layout changes, previewing before applying. | `route_index_refresh({"repo_id":"app","contract_path":"/work/series-contract.md","dry_run":true})` |
| `memory_init` | Creates missing memory scaffold paths and optionally Git initialization, preserving existing onboarding. `initial_branch` is the foundation code branch; if omitted it must be resolvable from configuration/current code. | Initialization/bootstrap owner before first attributed memory baseline. | `memory_init({"repo_id":"app","initial_branch":"main","dry_run":true})` |
| `memory_baseline_status` | Reads drift and Git attribution state for first-baseline admission. | Bootstrap/baseline owner deciding whether adoption can proceed. | `memory_baseline_status({"repo_id":"app"})` |
| `memory_baseline_adopt` | Adopts and commits the first external-memory baseline. `accept_drift` only lifts the stale-anchor drift gate, not stale code/test reference-anchor refusals. | Authorized baseline owner after admission and explicit commit approval. | `memory_baseline_adopt({"repo_id":"app","dry_run":true})` |
| `memory_carryover_plan` | Plans landed converted cards/sidecars/records from another memory branch, observing official-code anchors and conflicts. Does not commit or author Curator history. | Recovery Curator/owner in the admitted carryover leaf, before apply. | `memory_carryover_plan({"repo_id":"app","contract_path":"/work/series-contract.md","source_memory":"/work/source-memory","official_code_ref":"OFFICIAL","source_code_ref":"SOURCE","old_base":"OLD_BASE"})` |
| `memory_carryover_apply` | Applies admitted carryover pairs/records, reanchors, indexes and validates; skips edited targets and restores bytes on refusal. History is authored by the Curator rather than copied. Requires `intent_note`; no Git commit. | Authorized recovery owner after inspecting the carryover plan. | `memory_carryover_apply({"repo_id":"app","contract_path":"/work/series-contract.md","source_memory":"/work/source-memory","official_code_ref":"OFFICIAL","source_code_ref":"SOURCE","old_base":"OLD_BASE","intent_note":"Apply reviewed landed carryover."})` |
| `curator_coherence` | Prepares/publishes/inspects/validates candidate-bound judgments. Publication binds requirement, attempt, predecessor, trees, topology, intent, attestation and caller. Stable manifest is authority, not Markdown. | Curator completing coherence; Manager inspecting/validating candidate authority. | `curator_coherence({"request":{"action":"status","contract_path":"/work/series-contract.md"}})` |
| `worktree_sync` | Supported official-line sync with gate judgment. Follow returned choice/resolution calls; preview uses `dry_run:true`. It is not a raw Git merge recipe. | Owner admitted by the sync procedure; Curator supplies content repair and Manager performs reserved sync actions. | `worktree_sync({"contract_path":"/work/series-contract.md","dry_run":true})` |
| `worktree_closeout_preview` | Previews proposed code/memory closeout and blockers without committing. Converted-memory gate evidence concerns the exact candidate tree, not the mere existence of history rows. | Manager/authorized closeout owner after independent code and memory review. | `worktree_closeout_preview({"contract_path":"/work/series-contract.md"})` |
| `worktree_closeout_apply` | Applies approved closeout, subject to the [closeout gates](../../operations/closeout.md#judge-the-exact-candidate-at-each-publication-step). A knowledge-gate refusal carries findings as `knowledge-gate-refused`; apply is not implicit in a successful preview. | Authorized closeout owner under the [closeout approval authority](../../operations/closeout.md#confirm-the-authorized-owner), which determines explicit or delegated approval; never a Worker/Curator self-acceptance. | `worktree_closeout_apply({"contract_path":"/work/series-contract.md","intent_note":"Preview the approved closeout.","dry_run":true})` |

## Read asynchronous check results

Quality `sync` and `start` accept execution fields. For `poll`, pass the returned `run_id`, repository and optional matching contract, without `checks` or `detail_limit`.

Retain the completion and candidate identity: a started run is not clean. Citation polling retains the repository, contract and run identifier; follow its returned next arguments.

## Check the producer list before handover

Before sending a freeze's producer list, run `handoff-check` with the serving interpreter and exact frozen tree. It replaces neither Curator authoring nor final integrity, quality or fired-link checks.

```sh
PYTHONPATH="$AR_SERVING_PARENT" "$AR_SERVING_PYTHON" -P -m agents_remember.cli handoff-check /work/report/producer-list.json \
  --code /work/code --tree FROZEN_TREE --memory /work/memory
```

The check reads every entry through writer readers: list shape and rationale, targets, covers, weakness anchors, dependent documents, `stands_on`, proof test cases, and `still_rejected` repoint targets in that candidate.

A proof may use `path::name`, `{path, symbol}`, or an admitted line-range locator covering one test case. A bare test file is a finding. Repoint shape and target checks do not validate a fired link: the output states `fired link: checked by the writer`.

With `--memory`, record links must name stored, unretired records. Without it, the output states `record links: not run`. The command takes no worklist input and writes neither files nor Git objects. Candidate resolution reads `CodeSnapshot.of_tree`, not working-tree capture.

Read the exit status with the output:

- `0`: clean;
- `1`: findings naming entry, field and reason, or unchecked record links;
- `2`: invocation error.

Name the result, list and freeze in the handover. `PASS` does not prove prospective writer admission.

Before producing the list, read [the handoff-list template](../../templates/curator-handoff-list.md#shape) for its format.

Before checking writer admission, read [serving-build binding](serving-build.md) for interpreter admission and package identity.

Before interpreting final writer checks, read [publication reports](../curator/publication-report.md) for result states, refusals and remaining work.

## Find the owning source

CLI sources are in `mcp/src/agents_remember/cli/`; each command module owns its arguments and effects. `knowledge_census.py::add_arguments` and `paseo_runtime.py::_COMMANDS` own nested names. `leaf_enclosure_start.py::COMMAND` owns internal dispatch.

The MCP registrations named above own tool signatures. Request shapes belong to `mcp/src/agents_remember/models/memory.py` and `mcp/src/agents_remember/models/lifecycles/curator_coherence.py`.

Follow these sources and writer readers; do not invent a parallel tool surface.
