# MCP Tool Reference

The tool server `agents-remember-mcp` serves the tools of this page to a harness. Every tool has
one row below, and the sections follow the groups of the tools. The last column names the main
arguments of a tool, not every argument; the tool server serves the full schema of each tool.

A tool that takes `dry_run` previews with `dry_run=true`. The default of `dry_run` differs from
tool to tool, so name the value in the call. A tool that works on one code repository takes a
`repo_id`, which must be an id under `repositories` in the settings file.

An agent searches the code with the tools of its own harness.

The instructions of the roles in the skill `l-01-agent-lifecycles` tell a role agent which tool
to call at which step and what must hold before the call. The
[curation operation](https://github.com/Foxfire1st/agents-remember/blob/main/skills/l-01-agent-lifecycles/operations/curation.md)
orders `memory_quality_check` and `curator_coherence`, and the
[closeout operation](https://github.com/Foxfire1st/agents-remember/blob/main/skills/l-01-agent-lifecycles/operations/closeout.md)
orders `worktree_closeout_preview` and `worktree_closeout_apply`. A chat without a role calls a
tool directly. The [Skills reference](skills.md) lists the instruction files, and
[Tool details](../tool-details.md) holds the longer explanation of some tools.

## Tool answers

A tool answer holds the outcome, what the caller needs for its next step and, when the result is
larger, `log`: the place of the file that holds the whole result. An answer is bounded at 1,500
tokens, or at the lower bound of its tool; a few tools have a higher bound of at most 4,000 tokens.
A list in an answer is cut at 20 entries, or at 10 in a status answer, and carries its count. An
answer over its bound arrives as a short form of at most 400 tokens. It always holds
`overflow: true`, `answerTokens` and `log` (or `logError` where the place cannot be given), and
`ok`, `code` and `buildCurrentness` where the answer has them. It then adds `error`, cut when long
(`errorCut`), and the answer's other numbers, booleans and short texts while they fit;
`droppedFields` counts the fields left out.

A worktree, closeout or landing answer names the state reached and the next call in `nextTool`
with `nextArgs`. A refusal that follows from the state of a task answers `ok: false` with the
state's own sentence, a `code` and the one following call. A dry run that names blockers answers
a blocked state.

Every tool carries the annotation `readOnlyHint`. It is `true` for the six tools that only read:
`server_info`, `read_ar_files`, `knowledge_read`, `knowledge_integrity_check`, `worktree_status`
and `worktree_closeout_preview`. It is also `true` for `render`, one of the explainer's tools
below, which writes only its run files and the task's next revision. It is `false` for every other tool, so a harness
that asks before a write can tell the reads apart.

## Server and context

| Tool | Purpose | Main arguments |
| --- | --- | --- |
| `server_info` | Report the resolved configuration: the build, the roots, the allowed repositories, the build state of the running tool servers and dashboards, and, on a tool server that a role start launched, the tool server name and the agent binding. It does not list the tools. | none |
| `context_packet` | Where the caller works and what needs action: the roots, the task, action lines only when needed, the serving build commit and the pair's two commits. | `repo_id`, `include_drift=false`, `include_freshness=false`, `task_context` for a task |
| `read_ar_files` | Name which knowledge governs up to 20 paths: existence, governing card as a memory-worktree location, record counts by state, and action-asking records by ID, kind, state and card heading; overviews once per call, nearest first. `openWeaknesses` and `dependentDocuments` are one count each for a path and are absent at zero. No source, card or overview text: read the named files with your own tools. | `repo_id`, `files` (a list of paths), `task_context` for a task |
| `runtime_install` | Install or refresh the files that the package owns in the coordination root; safe to run again. The install mirrors the packaged documentation into `<coordination-root>/docs/` and the packaged files of the two [search providers](../concepts.md#search-provider) into `<coordination-root>/providers/`, creates the folders for tasks, memory repositories, worktrees, notes and logs, and creates `<workspace-root>/.agents-remember/role-executions/`. No part of the product starts, indexes or queries a search provider. The tool server serves the skills as resources from the package. The `host` part installs or updates the host that the `paseoRuntime` block of the settings file names and reports the outcome and an owed restart; the install never writes that block, names a missing block and never restarts a running host. With `dry_run=true` the install changes nothing. **Preserves** user data (`<coordination-root>/memory-repos/`, `<coordination-root>/providers/data/`, and `<coordination-root>/providers/runners/` when `install_provider_deps=false`) and **replaces** the managed documentation (`<coordination-root>/docs/`). `experiment` selects an experimental install for one call and is never a setting. | `dry_run=false`, `include_benchmarks=false`, `experiment` |

When the [route index](../concepts.md#route-index), the generated file `overview.index.json` that
lists the files of a route that have a card, does not list a file but the file's card exists,
`read_ar_files` serves the card. The answer then says `routeIndexStale` and names
`route_index_refresh`, which repairs the index.

With the `task_context` of a task, `read_ar_files` reads the code worktree and the memory worktree
of that task. Every answer names whose worktrees it read: `worktreeScope` is `leaf` for the
worktrees of a task, or `official-line` for the checkouts of the code repository and the memory
repository themselves that the settings name; `sourceRoot` and `memoryRoot` name the two folders.

[Tool details](../tool-details.md) lists the answer fields of `server_info` and
`runtime_install` and explains each argument of the install.

## Memory

| Tool | Purpose | Main arguments |
| --- | --- | --- |
| `memory_init` | Initialize (or repair) the memory repository of a code repository. Its `knowledge` block reports `not-recorded` (no authored records), `recorded` (records present, with the exact identity) or `unusable` (a partial index, with its code, detail and next action). The `datasetPath` names the derived index; its existence does not prove an authored knowledge foundation. The call writes the layout marker `knowledge/layout.json` when it creates the memory; never create the marker by hand. | `repo_id`, `dry_run=false`, `initialize_git=true`, `initial_branch` |
| `memory_quality_check` | Closeout memory-quality checks; the full logged result includes the `knowledgeWorklist` and exact-tree `knowledgeGate` blocks of a task. A full contract-scoped call atomically replaces `<enclosure>/reports/curator-memory-quality.md` and its attestation `curator-memory-quality.json`, combining quality findings, current-addition coverage, route-index preview, drift candidates and report-only evidence. The persisted change-to-knowledge worklist is a separate artifact. | `request={"mode":"sync", "repo_id":..., "checks":..., "detail_limit":50, "contract_path":...}`; use `mode:"start"` for async admission and poll only with `mode:"poll"`, `repo_id`, `run_id` and, for a candidate run, the same `contract_path` |
| `citation_fix` | Regenerate anchored citation ranges in an admitted memory scope; preview synchronously or in the shared background run registry. Ambiguous anchors, renames and deletions remain Curator work. | `repo_id`, `contract_path`, `document` or `documents`, `dry_run=false`, `mode="sync"`; preview with `mode="start", dry_run=true`, then `mode="poll", run_id=...` with the same repository and contract and no selectors |
| `citation_note_revision` | Revise the note of one sidecar reference in a task's memory worktree, preserving targets, anchors and other references. Refuse a stale sidecar digest, missing key or empty note. | `repo_id`, `contract_path`, `card`, reference number `reference`, `note`, `base_digest`, `dry_run=false` |
| `route_index_refresh` | Regenerate the [route indexes](../concepts.md#route-index), the files `overview.index.json` that list the files of each route that have a card, to match the cards under `onboarding/`. **Writes** into the memory root it resolves. | `repo_id`, `dry_run=false`, `contract_path` |
| `memory_baseline_status` | Report drift and Git attribution/cache observations for adopting a memory baseline. | `repo_id` |
| `memory_baseline_adopt` | Adopt a memory baseline and refresh the computed cache `memory.md` (gated on clean/accepted drift). | `repo_id`, `accept_drift=false`, `source_branch`, `work_branch`, `dry_run=false` |
| `memory_carryover_plan` | Plan the [carryover](../concepts.md#carryover) of the richer cards and knowledge records of a source branch into the memory worktree of an open task that is started for the carryover, after the branch's code has landed. The plan writes nothing. | `repo_id`, `contract_path`, `source_memory`, `official_code_ref`, `source_code_ref`, `old_base`, `replace_existing=false` |
| `memory_carryover_apply` | Apply an approved carryover plan in the memory worktree of that same task once the branch's code has landed on the official line. | the arguments of the plan, `intent_note`, optional `include_review_required` |

The guide [Onboard an existing repository](../guides/onboard-existing-repo.md) describes the
steps around `memory_init`.

`memory_quality_check.request` is exactly one shape. `sync` and `start` accept repository,
scope, checks, and detail fields; `poll` accepts only `mode`, `repo_id`, `run_id` and `contract_path`. Do not resend
start fields while polling, even when their values equal defaults: a poll that carries a field
of a start, such as `checks`, is refused before the tool looks up the run. A unique start at capacity returns
`capacity-reached` without a `runId`; poll or wait for active work, then submit a new start request.

`memory_quality_check` carries an optional `contract_path`, the path of a task's
[worktree contract](../concepts.md#worktree-contract), inside its `request` of the mode `sync`
or `start`; `citation_fix` and `route_index_refresh`
take it directly. With it, they act on the task's memory worktree against that
task's code. A wrong repository or absent worktree refuses rather than acting on
the memory repository. The response identifies the measured `onboardingRoot`.

An unscoped quality check or route-index preview reads the memory repository. Unscoped
citation repair and route-index writes have only the narrow pre-baseline admission described below: the configured memory root, no adopted
baseline and no linked memory worktree. `citation_note_revision` requires a task's
contract. Use the task's contract for candidate work.

Only a full task-scoped sync/start `memory_quality_check` request writes the
[Curator's checklist](../concepts.md#curators-checklist). It reports
`curatorActionableCount`, the **raw** `qualityChecklistStatus`, and the combined `checklistStatus`; the
Curator reruns it until the actionable count is zero and the raw status reads `ready-for-closeout`. The
combined field is rewritten to `coherence-required` only when the
[coherence record](../concepts.md#coherence-record) is then missing or stale. `curator_coherence`
renews the record with the actions `prepare`, `publish` and `validate`, in this order, and
`closeoutReady` becomes true only once that validation passes;
[Tool details](../tool-details.md#curator_coherence) names the fields of each action's `request`.
Subset checks and unscoped checks do not create that artifact. The checklist is
outside both Git worktrees, replaces its predecessor instead of accumulating timestamped files,
and `worktree_cleanup`/`worktree_abandon` remove its reserved `reports/` directory with the
enclosure. Dirty-source and real-commit residuals remain listed separately so the pre-commit loop
does not fabricate closeout metadata.

The checklist also lists the invariants that have no proof. That list is information and not a
gate; the answer carries its length as `invariantsWithoutProofCount`.

A check on a task reports an entity row that cites a path the task's code candidate lacks. The
finding is actionable and names the catalog file, the row and the path.

Use `citation_fix` first for matching content at stale same-path citation anchors. When it refuses
a correct input, the Curator follows the
[same-tool ladder for a refused correct write](https://github.com/Foxfire1st/agents-remember/blob/main/skills/l-01-agent-lifecycles/reference/refused-write.md)
of the skill, only for that refusing tool and input class; permission decisions and product
guards remain stops.
`citation_fix` with `repo_id`, the `contract_path` of the task's worktree contract and
`dry_run` true or false authors supplied card citation tables
and re-records blob/range anchors whose content still matches at the same target path.
It does not relocate a symbol into another file. Cross-file moves, renamed or deleted targets
and stale or ambiguous references remain Curator reconciliation work. `contract_path` is
the ordinary write route of a task; a worktree contract that names the memory repository itself
is refused.
The only unscoped admission is the configured memory root before baseline
adoption, with no linked memory worktree; the repository and resolved root must
match its settings. Other unscoped calls refuse and name the task route.

The guides [Adopt existing memory](../guides/adopt-existing-memory.md) and
[Carryover from a branch](../guides/carryover-from-branch.md) describe the baseline and the
carryover.

<a id="knowledge-operations"></a>

## Knowledge

| Tool | Purpose | Main arguments |
| --- | --- | --- |
| `knowledge_read` | Name the records of one memory-tree view (`source_context`, `invariant`, `family`, `record`, `weakness`, `dependent`) at one snapshot, paged; each row holds the text ID, kind, state and memory-checkout location. No record text: open the named files with your own tools. | `memoryRoot`, `view`, `limit=32`, `continuation`, the seed of the view (`sourcePath`, `invariantRevisionId`, `familyRevisionId`, `recordId`), filters `weaknessKind`, `includeClosed`, `staleOnly`, `standsOn` |
| `knowledge_write` | Plan or write the [hand-off list](../concepts.md#hand-off-list) that a Curator gives it, through the one writer of knowledge files, which `agents-remember knowledge-ingest` and `agents-remember knowledge-bootstrap` also use. Without `commit` it plans and validates; with it, writes knowledge files and makes no Git commit. The answer names the whole report and all refusals. | `contractPath`, `handoff` + `handoffName` or `handoffPath`, `authorizationRef`, `commit=false`; for a [foundation wave](../concepts.md#knowledge-foundation), a write that belongs to no task, `repoId` + `wave`; for a crossing, the sync of a master's branch that opened a history file for conflicting records, the master's `contractPath` + `crossing` |
| `knowledge_diff` | Name the changed records of two memory trees beside a caller-readable report holding the whole comparison: the two trees, counts by kind, changed records, report location. | `memoryRoot`, `beforeRevision="HEAD"`, `afterRevision`, `recordId`, `path` |
| `knowledge_integrity_check` | Validate a memory tree: `validation.ok`, refusal/report counts, rule counts and bounded violations. With `contractPath`, use the task's code and memory worktrees and return its latest persisted worklist; no semantic judgment is inferred. | `memoryRoot`, `codeRoot`, `baseCommits`, or a task's `contractPath` |

A `knowledge_read` with the view `source_context` and a `sourcePath` also lists every family
whose route is the path's directory or one of its ancestors. It lists such a family even when the
file realizes none of the family's members.

`knowledge_read` answers a large selection in pages, and each page stays within one token
threshold. The answer then carries a `continuation`. A call that passes the `continuation` back
resumes the same selection at the same memory tree, until every row has been returned once.

The views `invariant` and `family` accept the text ID of a record in `recordId`, beside the
revision IDs. The view `record` answers one record of any kind: its state, its links with the
state of each target, the records that link to it and its [judgment rows](../concepts.md#judgment-row).

`knowledge_integrity_check` also answers, in `routes`, one row for each family: the routes that
the family records (the code folders in which its members are realized), the routes that the
product suggests from the files that hold those realizations, and whether the two differ.
`agents-remember knowledge-routes` prints the same rows. The answer holds at most 10 rows and
then carries their count; the whole result holds every row.

See [Knowledge system](knowledge-system.md) for record/sidecar/history ownership,
worklist and knowledge gate fields, validator, derived index, memory merges and the
reader/review surfaces. The [knowledge command
reference](https://github.com/Foxfire1st/agents-remember/blob/main/skills/l-01-agent-lifecycles/reference/knowledge/commands.md)
lists command callers, timing and examples.

<a id="worktree-lifecycle--closeout"></a>

## Worktrees

| Tool | Purpose | Main arguments |
| --- | --- | --- |
| `worktree_start` | Create or load the worktree contract of a task with its code worktree and its memory worktree; `task_name` is the folder name of the master, `leaf_id` is the id of the task, and `parent_task` names the folder of the master or of the sprint that commands it. A dry run changes no task document, contract, Git ref or worktree; it returns the start preview even for the first task of a master whose own contract does not exist yet. The tool does not read the dependencies that a requirement packet names: tasks start in parallel, and the order of landing is the Manager's decision. | `repo_id`, `task_name`, `worktree_name`, `leaf_id`, `parent_task`, `workflow_kind="light-task"`, `source_branch`, `work_branch`, `memory_choice`, `stale_base_choice`, `dry_run=false` |
| `worktree_enclosure_adopt` | Give one exact readable enclosure that has no [enclosure locator](../concepts.md#enclosure) its locator. The call validates the configured contract/root pair and writes the locator with an audit record; no other tool calls it. Dry-run by default. | `contract_path`, `expected_worktree_group`, nonblank `rationale`, `dry_run=true`, `approved=false`, optional `expected_publication_request_id` |
| `worktree_attach` | Re-attach to an existing worktree contract of a task without changing Git. | `repo_id`, `task_name` or `contract_path`, `leaf_id`, `parent_task` |
| `worktree_status` | Report the lifecycle status of one task by its address, without reading the closeout queue. While the task's enclosure locator is live, the status comes from the enclosure's manifest and [operation journal](../concepts.md#operation-journal), with the controls that can run now. Once cleanup or abandon has archived the enclosure, the locator is terminal: the status comes from the archive outside the enclosure, its [archive record](../concepts.md#archive-record) and the worktree contract; it tells `archive-ready` (the archive stands, the contract does not yet record the cleanup) from `cleanup-completed` and returns the archived `cleanupArguments` with the exact `nextArgs` of the retry. It also reports one line when copies of task documents differ from their sources. | `repo_id`, `task_name` or `contract_path`, `leaf_id`, `parent_task`, `caller` |
| `worktree_sync` | Pull the moved official line into a live worktree: fetch, merge code, fast-forward or merge memory under the admitted sync plan, and advance the recorded base pair. With `sync_source="repository-default"` it pulls each repository's default branch instead: the base pair stays, the journal records the resolved branches and pinned tips, and one sync-log entry names the sources, incoming tips and produced heads; resume and cancel use the journal alone, and `continue` refuses with `sync-source-moved` when the default changed. Knowledge merges structurally and is validated; conflicts retain the advertised `continue` / `cancel` route. | `contract_path`, `memory_sync_choice`, `sync_source`, `resolution_action`, `dry_run=false` |
| `worktree_pause` | Pause an atomic master and hand control back to the user; publishes nothing. | `contract_path` |

The lifecycle sets the status of a task document itself. `worktree_start` sets `inProgress`,
`worktree_abandon` sets `abandoned`, and `lifecycle_finalize_task` sets `Completed`. No agent has
to set the status; the `task_doc` operation `set_status` serves a correction.

A sync that stopped on a conflict continues with `resolution_action: "continue"` after the
conflict is resolved. When the sync had parked uncommitted work and the resolution leaves some of
that work out, `continue` answers `sync-resolution-incomplete` with a report of what is missing.
The parked copy stays, and the sync finishes only when a later `continue` finds the work resolved.

A sync that stopped can always be ended with `resolution_action: "cancel"`. Every branch returns
to where it stood before the sync, and the uncommitted work that the sync had parked comes back.
Work in the tree that the cancel cannot attribute is saved as patch files in the task's scratch
folder before it is discarded. When the sync cannot prove that the parked work came back, its
refusal names the paths, at most ten of them, and the cancel as the way out. It says not to edit
the worktree until the sync is closed.

A sync judges the memory that it adopts before any ref moves, on a fast-forward too. A refusal
names the source line and its violations, and it stays in the journal of the sync:
`worktree_status` and a repeated `worktree_sync` show it.

The `reports/` directory of an enclosure holds evidence that may be deleted; the operation journal
in the enclosure's folder `.lifecycle/` records each direct landing. A lifecycle read takes one of two
ways. While the enclosure locator is live, it reads the enclosure's
`.lifecycle/enclosure-manifest.json` and the journal with its history. Once the locator is
terminal, it reads only the archive outside the enclosure, its archive record and the worktree
contract; it never reads the removed enclosure. Terminal cleanup copies the manifest and the
journal into the archive and reads the copy back, publishes the archive record, and only then may
remove the enclosure with its reports. A lifecycle read never finds the operations of a task
through a task document, a scan of tasks or worktrees, a guessed name, a folder that the caller
passes or a path under `reports/`.

See [Worktrees And Closeout](worktrees-c09.md) for the lifecycle and its gates.

## Closeout and landing

| Tool | Purpose | Main arguments |
| --- | --- | --- |
| `worktree_closeout_preview` | Preview a closeout without changing anything: the changed code paths, the proposed commits and what blocks. The [knowledge gate](../concepts.md#knowledge-gate) judges the exact candidate trees; `knowledge-gate-refused` carries its findings. | `contract_path`, `code_commit_message`, `memory_commit_message` |
| `worktree_closeout_apply` | Judge the exact candidate trees with the knowledge gate; `knowledge-gate-refused` carries its findings. Validate every enabled explicit nonblank input, then run the whole closeout inside the call: spend the approval of the closeout, commit the code, commit the memory and record the closeout in the worktree contract. The answer is the state reached, `closed` or a refusal such as `blocked` or `closeout-gate-blocked`; nothing runs after the call returns, and there is nothing to poll. The call reads no [closeout door](../concepts.md#closeout-door), the record by which a task waits for closeout; no tool of the tool server creates one. Before a commit, the code repository's own pre-commit hook runs on the tree to be committed; the paragraph below this table describes that check. | `contract_path`, `intent_note`, explicit message for each enabled commit leg (`code_commit_message`, `memory_commit_message`), `dry_run=false`, `corrective_dispositions` |
| `worktree_integrate` | Land a closed-out task (`ff-only` or `replay`) inside the call and answer the state reached: `integrated`, `would-integrate` for a dry run, or a refusal such as `blocked-non-ff`, `blocked` or `handover-gate-blocked`. Nothing runs after the call returns, and the call leaves no record in the operation journal. | `contract_path`, `strategy="ff-only"`, `dry_run=false` |
| `worktree_checkpoint_landing` | Land the work gathered on the branch of an unfinished atomic master onto the branch that the master itself lands on, and keep the master open; requires the user's approval. It is not a pause: `worktree_pause` pauses a master. | `contract_path`, `strategy="ff-only"`, `dry_run=false` |
| `worktree_record_landing` | Record a commit the landing target already holds. | `contract_path`, `landed_code_commit`, optional `landed_memory_content_commit`, `dry_run=false` |
| `worktree_operation_control` | Execute one currently advertised task-addressed control for an exact generation in the operation journal. Only a direct landing leaves a generation there: for `operation_kind` `closeout` or `integrate`, a request that passes the checks of its shape, its contract and its caller is refused with `lifecycle-operation-missing`. Retry/recover preserve accepted input; cancellation proves worker exit and Git safety; retire/supersede are evidence-aware. | `contract_path`, `operation_kind` (`closeout`, `integrate`, `direct-landing`), `action` (`retry`, `recover`, `resume`, `cancel`, `retire`, `supersede`), `expected_generation`, nonblank `intent_note`, action-specific inputs, `dry_run=false` |
| `direct_landing` | Grant-gated landing, from the master's branch, of a named task that holds a [direct-execution grant](../concepts.md#direct-execution), without an enclosure for that task. Verifies the code commit at the HEAD of the master's branch, or creates it from the task's uncommitted changes with `code_commit_message`; persists a durable direct-landing generation before Git mutation and recovers it through `worktree_operation_control`. | `contract_path` (the worktree contract of the master), `leaf_task_document_ref`, optional `code_commit` (verify) or `code_commit_message` (create), explicit enabled-leg messages, `intent_note`, optional gated `candidate_tree`, `leaf_id`, `dry_run=false` |
| `direct_execution_grant` | Give, take back, or reclaim direct-execution grants before work starts. Only a caller bound to the Architect role can call it. The user gives the same record with the dashboard's switch "Direct execution"; `task_doc` has no operation that gives, changes, or removes a grant. | `task_document_ref`, `action` (`give`, `revoke`, or `reclaim` — reclaim takes back only abandoned tasks' grants with clean checkouts and reports what was preserved and why; started tasks keep their grants) |
| `worktree_cleanup` | After the landing, archive/read back canonical lifecycle evidence, publish the external archive record, then remove worktrees, merged task branches, reports, and the enclosure. Archive-ready is not cleanup-completed; retry this exact public call with its archived `teardown_providers` value until surviving contract truth records completion. Bounded pre-locator exception: with no enclosure locator, no worktree folder, and no work branches, the call finishes with nothing to archive/delete, records the why in the surviving contract, and reads it back. This is non-terminal for task documents. | `contract_path`, `dry_run=false`, `teardown_providers=true` (also removes the containers, networks and folder `provider-runtime/` that a [search provider](../concepts.md#search-provider) left in the enclosure) |
| `worktree_abandon` | Abandon an unintegrated generation through exact contract/journal/Git authority and preserve terminal archive proof before deletion. Archive-ready but incomplete abandon retries this exact public call with its archived `force` value until surviving contract truth records abandonment. Bounded pre-locator exception: with no enclosure locator, no worktree folder, and no work branches, the call finishes with nothing to archive/delete, records the why in the surviving contract, and reads it back. | `contract_path`, `dry_run=false`, `force=false` |

A closeout commits a tree only after two checks on it. First the knowledge gate and the validator
judge the candidate trees. Then the code repository's own pre-commit hook runs on the staged tree:
the hook that Git would run for a commit in that worktree. A failing hook, a change that the
hook's run made, a hook that cannot run and a hook that does not end within its bound each refuse
the closeout, and no ref moves. A pass is recorded on the commit as the trailer
`Commit-Check-Pass: tree=<tree> hook=<hook>`, where `<hook>` is the path of the hook in the
repository, and `hook=none` for a repository without a hook. A
landing reads that trailer; for a commit that carries none, the landing runs the hook itself on
the candidate tree. Neither closeout nor landing runs a certification of the repository;
[Repository certification profile](repository-certification-profile.md) describes the
certification.

`worktree_operation_control` serves a direct landing. The owner of a completed direct
landing that has not landed can retire it, and `recover` finishes an interrupted direct landing.
A `supersede` of a direct landing is refused as `lifecycle-direct-supersede-unavailable`; the
refusal names a new `direct_landing` call.

Closeout and landing record the actual code/memory Git pair. A memory-content commit that they
create carries `Code-Commit` attribution; `memory.md` is an ignored computed cache, never an additional commit
leg or admission/recovery authority. Missing or damaged cache data does not block those operations.

An archive record alone does not make the enclosure locator terminal. While the locator is still
live, a retry of the accepted `worktree_cleanup` or `worktree_abandon` with the same arguments
reuses the archive and its record byte for byte and then makes the locator terminal. Only when the
locator's state is `terminal-archived` does `worktree_status` read the archive and tell
`archive-ready` from a completed cleanup; the same accepted call with its original arguments then
finishes a removal that stopped part-way. This retry is not a `worktree_operation_control`
action. A refused terminal request names the accepted call as its retry and never a second way to
clean up. The identity of the archive request includes `cleanupArguments`:
`{teardown_providers: <bool>}` for `worktree_cleanup` or `{force: <bool>}` for
`worktree_abandon`. Status and conflict answers return that object and the exact `nextArgs`. A
retry with a different value refuses; an argument that the retry leaves out takes its default,
never the archived value.

A task that was reopened or abandoned can start again at the same worktree contract; the new
enclosure is then a successor. Its start first proves the archive of the earlier enclosure and
that the worktree contract is one that a successor may start from: a contract that
`worktree_abandon` left, or that `task_reopen` reset. The start then replaces the contract file in
one step, and only when its bytes equal that earlier contract; bytes that already equal the
successor's contract are accepted again, and any other bytes refuse.

## Task documents

| Tool | Purpose | Main arguments |
| --- | --- | --- |
| `task_doc` | Author JSON-primary task documents and deterministic markdown. Every intrinsically valid mutation publishes during every queue/operation phase and returns per-sprint `projectionEffects`; an incomplete effect carries its exact rebuild `nextAction`. `skip_step` requires a nonblank reason; `discard-unstarted` removes only centrally proven never-started planning work without completion fiction; `Completed` refuses unresolved units. | `repo_id`, task/contract target, `operation`, the input of the edit (`fields`, `step`, `subtask`, `section`, `decision` or `review`), `dry_run=false` |
| `task_reopen` | Reopen a fully landed task under the same id so new work can be declared explicitly. A task that is cleaned up and stands behind the tip of its master starts again from that tip when its recorded landing is an ancestor of the tip; otherwise the call refuses. | `contract_path`, `dry_run=false` |
| `lifecycle_finalize_task` | Prove the landed edge, resolve the exact contract-bound task, refuse before cleanup unless every parent/nested step is done, then complete that task and, when it declares an existing immediate parent (or names none and its folder's `task.json` master lists it), automatically derive and reconcile that exact row; a task naming none whose folder `task.json` is not a master is refused. A dry run reports the row that it would complete. The parent document's own task status and higher ancestors are not completed. The call writes the status `Completed` and the time `completedAt` into the task document. Finalizing a master marks it `Completed` and never archives it: the answer says that the archive was skipped and names the `task_doc` operation `retire_master`. | `contract_path`; optional `task_doc_path`, `master_doc_path`, and `subtask_number` are independent identity assertions; `dry_run=false`, `teardown_providers=true` |
| `closeout_queue` | Inspect or rebuild the [closeout queue](../concepts.md#closeout-queue) of one sprint, a list that may be deleted and that holds only the tasks whose closeout door waits. It is either `valid-built`, current to the task documents, or `invalid-empty`, which lists no task; a rebuild reads only the current task documents and the waiting closeout doors. No tool of the tool server creates a closeout door, so the queue holds no task. A request without `caller` is refused with `closeout-queue-caller-required`; only the sprint's Architect or Orchestrator, or the Manager of a master that the sprint commands, may call it. Each `task_doc` write rebuilds the queue of every sprint that governs the written task, and so does `rebuild`; after that, `status` answers `valid-built` with no member, and its `summary` says that the queue "contains 0 waiting door generations"; a door generation is one version of the record of a [closeout door](../concepts.md#closeout-door). `invalid-empty` is the state of a sprint for which no queue file exists yet. | `request` with `action` (`status` or `rebuild`), `sprint_task_document_ref`, declared `caller` (`role` and `task_document_ref`) |
| `curator_coherence` | Author, publish, inspect or validate the coherence record of one task, which the tool's description calls its "curator-coherence authority" (`prepare` → `publish` → `validate`). | `request`: `action` (`status`, `prepare`, `publish` or `validate`) and `contract_path`, plus the fields of `publish` that [Tool details](../tool-details.md#curator_coherence) lists; one action's request shape at a time |

`worktree_closeout_apply` reads no closeout door and writes nothing
into the operation journal of the enclosure. The closeout's state stands in the worktree contract,
and `worktree_status` reports it; a direct landing keeps its generation in the journal.

### task_doc operations

`task_doc` authors the JSON-primary task document (`ar-task-document/v1`); `task.md` /
`<slug>.md` is generated and never parsed back. Authoring operations write task documents.
`linkage_report` leaves them unchanged; a non-dry call writes a separate report artifact,
while its dry run writes nothing. `task_doc` has no reading operation: read the rendered `.md`
file or the JSON file. Locate the doc by `task_name` (also resolves the contract for the
lifecycle key) or `contract_path`; pass `slug` for a task of a master (`<slug>.json`), omit it for
the master itself (`task.json`). `dry_run=true` builds,
validates and returns diff/wouldLose without writing. A write answers `ok` and
the changed unit in a phrase (`renderedPath` rides `create`, `reviewState` rides reviews);
the rendered Markdown and per-sprint `projectionEffects` ride the whole result in the log
file. `linkage_report`
answers the fact counts with the report location. Long `fields`/`section_body` may ride
as files (`fields_file`, `section_body_file`). A master-sync failure answers `ok:false`
with `master-sync-failed` and leaves the task document unchanged.

A successful `create` of a task, or a `set_field`/`replace` that sets or replaces the
task's `requirements`, adds `alignmentSweep` when its master has other open
tasks. Dry runs include it too. The field is advice, not a gate: it refuses
nothing and changes no document bytes. Refusals and masters with no other open
task omit it. Siblings come from the master's rows, excluding resolved rows, rows that carry
`retirement` and the edited task.

At most 12 declared siblings are listed as `openLeaves` (the other open tasks of the master),
each with `id` and `state`. A present sibling has `state: "available"` and `requirements` identities
of the form `<id>@v<n>`; `requirements: []` means present with no requirement
identities. A declared sibling whose file does not yet exist retains its `id`
with `state: "unavailable"`; that row must not contain `requirements`. An
undeclared sibling is neither read nor listed. These states are advisory and do
not block the write. Only a missing file is unavailable: malformed JSON remains
an error, not an unavailable row.

Structured packet references supply requirement identities; prose requirement
entries contribute their explicit `<id>@v<n>` mentions. With more than 12
siblings, the bounded answer instead supplies `openLeafCount` and
`masterTaskDocument`, the master's rendered Markdown path; this count form does
not replace unavailable rows at or below 12. Both forms include `message`:
"Sweep these against the new or revised packet before release
(operations/planning.md)." The path in the message names the
[planning operation](https://github.com/Foxfire1st/agents-remember/blob/main/skills/l-01-agent-lifecycles/operations/planning.md)
of the skill `l-01-agent-lifecycles`.

- `create`: new doc from `fields` (`id`, `slug`, `title`, `kind` `subTask`|`master`,
`repo`, `type`, `createdAt`, `objective`, `requirements`, `steps`,
...). A master takes `subTasks` plus ordered sections instead of steps. A sprint is created with
two empty sections: "Judgment Register", whose rows record the decisions that a `judgmentId`
names, and "Priority Register", whose rows grade a task or a master `critical`, `high`, `normal`
or `low` for the closeout queue.
- `replace`: whole-document rewrite from `fields` after schema validation.
- `set_status`: set `fields.status`. `set_field`: scalar/list
field updates. `append_decision`: append `decision={at, decision, rationale}`.
- `put_open_question` takes `fields={key, text}`: append or replace the question starting
with the nonempty key within the task owner's publication; `text` starts with that key.
Alternatively, `fields={questions: [{key, text}, ...]}` validates every entry before one
publication of the whole keyed list; the single-question form is supported as well.
- Step plane (one exact existing unit, addressed by `step={id, parent?}`; `parent` selects top
level when absent or that parent's substeps): `set_step` updates exactly one unit
(`{id, title?, status?, note?, parent?}`) and never creates — an explicit status clears an
earlier skip. `add_step` creates exactly one unit, requires `{id, title}`, refuses a
duplicate id. `remove_step` deletes exactly one unit, requires nonblank `step.reason`,
records a decision entry, and may remove a done unit or operate on a Completed document.
`skip_step` takes `{id, reason, parent?}`, sets only that unit done, records intentional-skip
provenance without cascading.
- Master rows: `set_subtask` writes `subtask={number, name, file?, status?, scope?}`;
`remove_subtask` takes `{number, keep_file?}` for a terminal row and drops the row plus its
task document (`json`+`md`) unless `keep_file=true`. `disposition:'discard-unstarted'` with a
reason discards planning work that never started: forbids `keep_file`, proves absence of
canonical enclosure/operation/role-agent/review/commit evidence, removes the child sources, keeps a
typed parent audit; started or ambiguous evidence refuses with the next lifecycle route.
A row with the status `abandoned` blocks nothing: a master counts as complete when every row
is `Completed` or `abandoned`.
- `set_section` writes `section={heading, kind?, body?}`; a canonical register heading must
keep the exact register table shape (write-time validation). A new free-form section whose
heading repeats a fixed one (objective, requirements, design, non-goals, acceptance, decisions,
sub-tasks, order) is refused, and so is a new header note that restates a field (master,
dependencies, execution nature, integration branch, intent review). The refusal names the field
or the operation to use. Sections and notes that a document already holds stay.
- Review: `begin_review` takes an empty review object and opens the next review round before
the Reviewer works; the first round is round 1, and a repeat while a round is pending resumes
that round. The number of rounds is bounded by `orchestration.loops.defaults.maxRounds`, read
when the round begins, plus the extra rounds that were approved. At the bound the call is
refused `review-budget-exhausted`; a further round then needs, in the review object,
`approval={by, text}` with `by` `architect` or `developer`, and a positive `additionalRounds`.
An approval before the bound is refused `review-admission-invalid`. `reviewKind: "memory"`
selects the separate sequence of memory reviews; the default is `code`.
`record_review` takes `{verdict, findings:[{findingId, description}]}` first,
then `remainingFindingIds:[...]`; successors only shrink the sealed set. `record_route_review`
takes `{verdict, verdictRef, routes:[{route, verdict, evidenceRef}]}`; the plane stamps the
candidate tree and time, every evidence path must be a real task-relative file, and it
overwrites the prior candidate's review.
- Atomic masters: the operation targets the master's task document and binds the accumulated
candidate, membership and evidence; a child publishes no independent review; master review is
required only when the master lands into its parent. `branch_addressed=true` (accepted only for
`record_route_review` of a task that holds a direct-execution grant) binds the worktree contract
of the master and stamps the candidate tree from the branch HEAD.
- Execution graph of a sprint (the field `executionGraph`, which says which masters, or parts of
a master, wait for which): `author_execution_graph` applies one validated atomic batch of
`mutations` (`add_node`, `remove_node`, `add_edge`, `remove_edge`, `move_leaf`, `set_nature`); a
mutation that expresses a decision needs a `judgmentId`, the id of a row of the sprint's
"Judgment Register". The [execution-topology page](execution-topology.md) describes the graph
and each mutation. `attach_master` attaches one master as a single validated batch (`masterRef`, `number`, `name?`, `scope?`, `status?`, `executionNature?`, `judgmentId?`);
`detach_master` takes `{masterRef}` and refuses while any edge touches the node.
- `retire_master` archives one master with a reason. It takes
`fields={masterRef, reason, removeEdges?}` and is addressed to the sprint that commands the
master, or to the master's own `task.json` when no sprint commands it. The master's folder moves
under `0_archive/`, and the same step removes the master from its sprint. A dry run lists what
would change. A repeat of the same request finishes a cleanup that stopped part-way; a request
that differs is refused. A master is archived by this operation or, together with its sprint, by
`archive_sprint`; `lifecycle_finalize_task` never archives it.
- `archive_sprint` archives a completed sprint together with the masters it commands. It takes
`fields={reason}` and requires the sprint to be `Completed` and every row to be `Completed`,
`abandoned` or the row of a retired master, which carries `retirement`. A dry run reports
exactly what would change. The sprint and then its masters move under `tasks/<repository>/0_archive/` of the coordination root. When a cleanup step
fails, the answer says `archived-with-hook-failures`, and a repeat of the same request runs the
remaining cleanup.
- `linkage_report` reports drift without changing task documents (`seat-doc-row` rows, which are
sprint rows without `masterRef` that name in `file` the document of the role agent that
coordinates the master, slug-only membership, where `orchestrates` names a master whose row has
no `masterRef`, mismatches, and uncommanded masters, which the sprint's decisions name and the
sprint does not command); non-dry calls write a separate report artifact.
It also lists, as `stray-task-file`, every file that is named like a task document or a contract
and lies outside the product's own places in a task folder, for example under `notes/`. No lookup
reads such a copy. A contract at one of the product's own places whose recorded path names another
file is refused with both paths.
- `Completed` refuses while any declared step/substep or master row stays unresolved.

`create`, `replace` and `set_field` also write these fields:

- `waitsFor`: the tasks of the same master that must land before this task. `task_doc` refuses an
  id that the master does not list, an abandoned task, the task itself, a task of another master
  and a cycle, each with the offending id.
- `nonGoals` and `acceptance`: two Markdown texts of a master or a task. The rendered Markdown
  shows "Non-goals" after the objective and "Acceptance" after the design.
- `intentReview`: the intent review of a task, with the state `pending`, `review-first` or `go`.
  A new task starts with `pending`. Every start of the task, by `worktree_start` or by a role
  start, is refused `intent-review-open` until `go` is recorded through `set_field` with
  `fields.intentReview`. A master carries no intent review.
- `knowledgeMaintenanceScope`: with `true`, the
  [worklist](../concepts.md#worklist-and-knowledge-gate) of the task, the list of knowledge that
  the Curator must answer, classifies every knowledge entry of the memory base, not only the
  entries that the task's change reaches.
- `expectedKnowledgeEffects`: the invariant and family effects that the task expects. The
  worklist then marks each reached item as planned or unplanned and raises every declared effect
  that no [judgment row](../concepts.md#judgment-row) of the Curator delivers.
- `completedAt`: the time of completion. `lifecycle_finalize_task` writes it.

## Role agents

A role agent is a chat of a harness that the host (Paseo) runs in one role for one assignment.
Some codes and labels of the product say "seat" for a role agent or for its place in a task.
[Roles and role agents](../roles.md) describes the roles and how they work together. This
section describes the tools.

| Tool | Purpose | Main arguments |
| --- | --- | --- |
| `role_start` | Start one role agent for one selection of task documents. A role agent that calls it becomes the parent of the role agent it starts. Repeating `request_id` reconciles the same role agent and never starts a second. | `role`, `request_id`, the document references that the role requires (`sprint_document_ref`, `master_document_ref`, `task_document_ref`), optional `agent`, `model`, `effort`, `feature_values`, `step_up`, `replaces_agent_id`, `replaces_interrupt_turn=false` |
| `role_start_set` | Start the Worker, the Reviewer and the Curator of one task in one call. Only the Manager of the task's master can call it. The Worker opens; the Reviewer and the Curator are held, unless `reviewer_message` opens the Reviewer. Repeating `request_id` reconciles the same three role agents and opens no held role agent. The tool does not prescribe the order in which the instructions of the roles start the stages. | `request_id`, the three document references, optional `reviewer_message`, the choices for each role in `worker` / `reviewer` / `curator`, `replaces`, `replaces_interrupt_turn=false` |
| `user_settings_init` | Preview or create the user settings file with one harness for all roles or a harness for each named role. An existing file is read and never overwritten. The target `host-authority` is refused, because the preparation of the host writes the `paseoRuntime` block. | `target="role-settings"`, `harness` or `roles`, `dry_run=false` |
| `role_message` | Send one text message to one role agent; with `wait`, return its reply. | `text`, `agent_id` or `role` with the document references, optional `body`, `attach`, `wait=false`, `timeout_seconds=300`, `withdraw_held_message_id` |
| `retire_child` | Retire one role agent of a task without giving it a turn. Only the role agent that started it can call it; the launch record names the caller and the reason. | `task_document_ref`, `role`, optional `reason`, `agent_id`, `interrupt_turn=false` |

[Tool details](../tool-details.md) explains `role_start_set` and `retire_child` at length. The
section "user_settings_init" of the [settings reference](settings-json.md) describes the file
that `user_settings_init` creates.

### Who can start which role

`role_start` has two kinds of caller.

A role agent starts only the roles that its own role allows:

- An Architect starts an Investigator, an Orchestrator, a Manager, a Worker, a Reviewer or a
  Curator.
- An Orchestrator starts a Manager, a Worker, a Reviewer, a Curator or an Investigator, each
  under its own sprint.
- A Manager starts a Worker, a Reviewer, a Curator or an Investigator, each under its own master.

A start of another role is refused `role-may-not-start-role`. A selection outside the caller's
own sprint or master is refused `selection-outside-callers-scope`.

A chat without a role is a chat of a harness whose tool server no role start launched. It starts
every role with that role's selection, as the role launcher of the dashboard does. The role agent
it starts has no parent, and such a call cannot replace a role agent.

Each role takes one selection of task documents. An Architect and a Bootstrap name no document.
An Orchestrator names a sprint. A Manager names a sprint and a master. A Worker, a Reviewer and a
Curator name a sprint, a master and a task. An Investigator names no document, a sprint, or a
sprint and a master, and never a task. One selection carries at most eight Investigators that are
not closed; a further start is refused `investigator-capacity`.

Starts run one at a time. A call waits up to 60 seconds for another start to end; then it is
refused, and its `nextAction` says to call again with the same arguments. Only the role agent that
started an execution repeats its `request_id`. A request that has a stored answer is answered
from it. A role agent that finished, was canceled or was rejected is reconciled and never started
again; a repeat for an archived role agent is refused.

`step_up` takes one sentence of reason. The start then uses the alternative launch choices that
`orchestration.roles.<role>.stepUp` names in the user settings file; the
[settings reference](settings-json.md) describes that entry.

### What a start on a task prepares

A start of a Worker, a Reviewer or a Curator prepares the task's enclosure itself: the code
worktree, the memory worktree and the task's workspace in the host. No other call has to come
first. The whole result says in `preparation` whether the enclosure and the workspace were
created or found. When the start cannot prepare them, it is refused with the reason and the next
step.

The start is refused `intent-review-open` while the task's intent review is not `go`.

When the task's workspace in the host is archived, the start opens a new workspace for the task
and starts the role agent there. The archived workspace stays archived, and a repeated start
finds the same new workspace. When more than eight workspaces of the task in a row are archived,
the start is refused and names the task and the count.

The start writes the copies of the task document and of its requirement files into the folder
`task-documents/` of the enclosure and nothing else; the file `INDEX.json` there has one row for
each copy. A missing requirement file does not stop the start: its row in `INDEX.json`, the
[handover](../concepts.md#handover) and the answer name it.

### One role agent for each role of a task

A task holds one role agent for each role. A start with a new `request_id` for a role that has a
holder is refused and names the holder. A start with `replaces_agent_id` names the holder, retires
it first and then starts the successor; the successor's handover names the report paths of the
role agent it replaces. A role agent replaces only a holder that it started itself. A holder that
is in a turn is replaced only with `replaces_interrupt_turn=true`. In `role_start_set`, `replaces`
names the holder for each role.

### The answer of a start

A start that carried through answers `ok`, the id of the role agent in `agentId`, and the places
of the handover in `handoverArtifactPath` and `handoverFilePath`. It adds a `warning` when the
caller must know something, for example a missing requirement file. A start of a Bootstrap adds
the addresses of the dashboard and of the chat (`dashboardAddress`, `chatAddress`, each with a
detail field) and a `nextAction`.

Every other outcome answers with its `status`, its `detail` and a `nextAction`. The status `held`
means that the role agent takes its first turn with the first message addressed to it. The status
`rejected` means that the launch is closed and `agentId` names no usable role agent. After the
status `unknown`, the same call with the same `request_id` reconciles the same role agent.

The whole result, in the file that `log` names, also holds `preparation`, `parentAgentId`,
`reportPath`, the `choices` of the launch, `taskDocumentFiles` and `secondCopyLoaded`.

A role agent that the host runs receives the tools of Agents Remember once, from the tool server
`agents-remember-task` that the start launches for it. `secondCopyLoaded` says whether the role
agent's harness also loaded a second copy of the tools. The start changes no configuration file
of the user or of the harness for this.

### Messages

`role_message` sends one text to one role agent. Only a role agent that AR launched can call it
(AR is short for Agents Remember): a chat without a role is refused `caller-has-no-binding`.

The call names its recipient by `agent_id`, or by `role` with the document references that the
role's selection requires; an address by role never means the caller itself. The recipient reads
the text behind one line that names the sender:
`From <role> · <task id or Projects> · agent <sender agent id>`.

`text` holds at most the number of characters that `orchestration.roleMessageLimit` sets, which
is 1,000 unless the settings name another number. A longer text is refused `message-too-long`.
`body` takes the full text of any length, and `attach` takes the paths of files. Both go into the
message file, and the delivered text names that file.

Without `wait` the call answers `ok`, the status, and how the recipient took the message in
`taken`, once the host accepted the message. With `wait` it returns when the turn that consumed
the message ends. When the host cannot say which turn consumed the message, the call returns
`accepted` without a text and detail says the reply must be read later. A reply to a message
delivered during a turn can be the running turn's text; detail says so then. A reply over the
bound of the answer arrives as its beginning, and the whole reply is in the detail file that
`log` names. A finished turn is a fact about the recipient's turn; it does not say that the
recipient accepted a requirement. Advice: never wait on a role agent that may be waiting on you.

A recipient that is in a turn takes the message into its running turn only where the host can
steer the recipient's harness; the answer then says `taken: "steered"`. For every other harness
the message is held with the reason `recipient-in-turn`, and the turn goes on. Where the host
replaced the running turn all the same, the answer says `taken: "replaced"` and carries a
`warning`.

### Holds and refusals

A recipient waiting on a permission decision, whose start has not finished, or that is in a turn
the host cannot write into is answered `held`, not refused: the answer carries `status` `held`,
the reason (`heldReason`), what waits and why (`detail`), the permission as the host names it or
null when unnamed (`permission`), the sender's waiting counts (`waitingMine`, `waitingTotal`),
and the issued id (`heldMessageId`). A held message is delivered, in the order taken, when the
recipient can be written to; the sender does not send it again. A later call withdraws a held
message with `withdraw_held_message_id`, and its answer reports the withdrawal in
`withdrawnHeldMessageId`.

`heldReason` has one of these values:

- `permission-pending`: the recipient waits for a permission decision.
- `start-unfinished`: the recipient's start has not finished.
- `recipient-in-turn`: the recipient is in a turn that the host cannot write into.
- `held-seat-unopened`: the role agent is held and takes its first turn with its first message.
- `held-behind-waiting`: earlier messages still wait for the recipient.
- `host-unreachable`: the host did not answer. The message is held, not lost, and is delivered at
  most once when the host answers.
- `provider-limit`: the usage limit of the recipient's
  [model provider](../concepts.md#model-provider) holds it. The answer carries `limitedUntil`
  when the end of the limit is known.

Code `recipient-busy` is refused outright only when the recipient already holds twenty messages;
that refusal names two ways on: wait for a delivery, or withdraw a waiting message. Every other
refusal names one reason: `recipient-not-found`, `recipient-archived`, `recipient-ambiguous`,
`recipient-cannot-be-resumed`, `scope-check-failed`, `caller-has-no-binding`,
`no-paseo-runtime-configured`, `message-too-long`, or `host-unreachable` when the host does not
answer and the recipient cannot be found.

The full procedure of a sender for held answers and for a full store is
[Message handover](https://github.com/Foxfire1st/agents-remember/blob/main/skills/l-01-agent-lifecycles/reference/message-handover.md).

### The line a parent receives when a turn ends

When a turn of a role agent ends and the turn delivered no `role_message` to the role agent that
started it, the host's plug-in gives that parent one line:

```text
turn ended: <role> <agent id> · <task>; last line: <text>; newest report: <path>
```

The text after `last line` is the role agent's last line, cut at 300 characters. A parent that is
in a turn receives the line when it is free. When the parent of a Worker, a Reviewer or a Curator
is archived, the line goes to the current Manager of the task's master. When a Worker, a Reviewer
or a Curator ends a turn with no [hand-off](../concepts.md#hand-off), the plug-in first sends a
message to that role agent itself, at most two times in a row; from the third such end on, the
parent receives the line, marked with the count.
[Roles and role agents](../roles.md) describes a turn that ends without a hand-off.

### Message files and the tool log

Messages are filed at the narrowest task document that sender and recipient share. Messages
between role agents of one task use that task's `notes/messages/<task-slug>/`; messages whose
narrowest shared task document is the master or the sprint use its `notes/messages/`. With no
shared task document, the message is filed in the folder of the sender's role beside the launch
records of the role agents of that role that work on no task document:
`<coordination-root>/notes/reports/paseo-native-executions/<sender-role>/messages/`.
An enclosure's `messages` link is a view of its message folder, not a separate place of filing.
The whole result of the call, in the file that the answer names in `log`, holds `messageFile` and
`messageFileSha256`. The two identify the bytes that were filed, including the `.undelivered.md`
file of a refused delivery and a wait reply longer than the message limit. A filed message and
the log line of a call show what was attempted and returned; they do not show that the recipient
accepted the content.

Before claiming no send, search the complete message folder for the narrowest shared task and
the tool log with its detail files. The calls of an active enclosure are logged under its `logs/`;
otherwise use the [transcript root](../concepts.md#tool-log), the folder that the key
`transcriptRoot` of the settings file names, whose default is `<coordination-root>/logs/mcp`.
Terminal lifecycle calls use that configured transcript root. See
[Tool log](settings-json.md#tool-log) for which call is logged where and how long a log is kept.
A missing file in one assumed default folder is not evidence that no message was sent.

## Review page

| Tool | Purpose | Main arguments |
| --- | --- | --- |
| `explainer_control` | Update or retire one task's explainer, or claim a master as its architect. `retire` archives the explainer's chat in the host and removes its workspace completely, for an explainer in any state (live, closed, archived, missing): the Git worktrees are removed and pruned and the folder of the workspace is deleted. The task's published revisions, grades and retire records stay. `claim` is open to any Architect that the host runs; it writes the record that binds the master to that Architect and nothing else. `update` and `retire` are accepted only from the Architect that the record names; any other caller is refused `not-this-masters-architect` and claims first. A refused action is a typed answer, never an error of the tool server. | `action` (`update`, `retire`, `claim`), `repo_id`, `task_name` (the folder name of the master), `leaf_id` (the id of the task) for the two task actions, `receipt_id` for a `retire` |

The start of an explainer adds four tools to the explainer's tool server
`agents-remember-task`. Only an explainer has them; no other role is offered them. The
start supplies the explainer's workspace and the task folder; a grade call carries the
explainer's current binding and the fingerprints of the blocks. No caller selects a workspace. The explainer reports
to the Architect with `role_message`.

| Tool of the explainer | Purpose | Main arguments |
| --- | --- | --- |
| `render` | Check the draft in Chromium and publish the next task revision. Write admitted claims to `drafts/assets/claim-map.json` first. Answers publication state, revision and run-file locations; open images from those files. | none; uses the workspace that the start supplied |
| `define_category` | Define a review-block category with 2–5 grades, highest first; a conflicting existing scale/meaning refuses. | `name`, `scale`, `binding`, optional `meaning` |
| `remove_category` | Remove a category and its grades in the workspace. | `name`, `binding` |
| `grade` | Grade named review blocks with a reason; stale binding refuses and long refusal lists move to a named file. | `references`, `category`, `grade`, `reason`, `binding` |

The review page has no control that runs an update. The Architect that claimed the task's master
runs it with `explainer_control` and the action `update`. The update binds the explainer's
workspace again; it starts no role agent and sends no message.

The page's button "Retire chat" does not pass through this tool: it calls a route of the
dashboard. The `task_doc` operations `retire_master` and `archive_sprint` retire the explainer
of every task they archive, with its workspace. The explainer's workspace of a task is built by
the first request that the user sends from the page, never by opening the page, and none of
these calls starts a model turn. [Review page and explainer](../guides/review-page-and-explainer.md) describes the page.
