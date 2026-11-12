# Tool details

This page holds the longer explanation of each tool whose short description points here. The
[MCP Tool Reference](reference/mcp-tools.md) has a row for every tool. This file ships with the
product: it lies at `docs/tool-details.md` in the source repository and inside the package, and
an install copies it to `<coordination-root>/docs/tool-details.md`. The short descriptions name
this one place, `docs/tool-details.md`, in both layouts.

## `server_info`

Report the resolved configuration of this tool server: the build, the roots, the allowed
repositories, the build state of the running servers, and, on a tool server that a role start
launched, the tool server name and the agent binding. It does not list the tools.

Arguments: none.

Answer fields:

- `ok`, `server` (this package's own name), `version`, `transport`;
- `configPath`, `coordinationRoot`, `workspaceRoot`, `transcriptRoot`;
- `allowedRepoIds`: the configured repositories;
- `unusedProviderKeys`: the ids of [search providers](concepts.md#search-provider) that the key
  `providers` of the settings file lists. Nothing reads them; a settings file that holds them
  loads without error;
- `unusedSettingsKeys`: the settings keys that the files hold and that nothing reads;
- `checkoutBinding`, only on a process that loaded the package from a linked Git worktree of the
  Agents Remember source repository and declared no execution mode before it read its settings.
  The tool server and the dashboard's server declare one at their start; an `agents-remember`
  command does not. The field holds `state` (`disposable-coordination-root`), `worktree`,
  `coordinationRoot`, `configuredRoot` and `remedy`;
- `openUserSettings`, only while the user settings file or its folder is open to writing:
  `userPath`, `fileExists`, `fileOpen`, `folderOpen`, and the user's two commands for that exact
  path: `closeCommand` closes the file and its folder to writing, and `editCommand` opens both for
  an edit (see [Closing the user's file to writing](reference/settings-json.md#closing-the-users-file-to-writing));
- `servingBuild`: the [serving build](concepts.md#serving-build), the build that this tool server
  loaded at its start: `version` and `bootedAt`, and where they are known `sourceDigest`,
  `pythonExecutable`, `packageRoot`, `commit`, `dashboardBuild` and `dirty` (only `true`, for a
  checkout with uncommitted changes);
- `buildCurrentness`: the loaded Python code compared with the files at its package root: `mode`
  (`installed` or `checkout`), `state` (`current`, `stale` or `unknown`), `loadedDigest`,
  `installedDigest`, `firstSeenAt`, `reason` and `remedy`;
- `runningServers`: the tool servers and dashboards that run on the coordination root, each with
  `pid`, `role`, `loadedDigest`, `startedAt`, `configPath`, `packageRoot` and `currentness`;
- `toolServer` and `agentBinding`, only on a tool server that a role start launched.

Operations: one (report). The call is read-only; it reflects the settings
and runtime loaded at startup, and the modes of the user settings file and its
folder as they are at the call. It changes no mode and no byte of them.

The tool servers and the dashboard's server keep the records behind `runningServers` under
`<coordination-root>/runtime/servers/`.

A process with a `checkoutBinding` works on the disposable coordination root of its source
worktree, the folder `provider-runtime/dev-ar-coordination` beside the worktree, and not on the
configured coordination root; `--config` does not change this. An `agents-remember` command that
runs in the same way prints the same fact as one line on standard error.

A tool server of an installed build can run Python code that is older than the installed files,
for example after an upgrade of the package. Its `buildCurrentness.state` is then `stale`, and it
refuses every call that publishes with the code `server-build-stale`. The refusal names both
builds and says to restart or reconnect the tool server. The calls that publish are closeout,
landing, sync and finalize, the writers of knowledge records and cards, the `task_doc` operations except
`linkage_report`, `task_reopen`, and the `publish` action of `curator_coherence`. A read and a
call with `dry_run=true` are answered.

Refusals: none in the ordinary path — the call cannot fail on arguments
(it takes none). A tool server that cannot read its own configuration refuses at
startup, before any tool is served.

## `runtime_install`

Install or refresh the files that the package owns in the coordination root; safe to re-run.
The install preserves user data (`<coordination-root>/memory-repos/`,
`<coordination-root>/providers/data/`, and `<coordination-root>/providers/runners/` when
`install_provider_deps=false`) and replaces the managed documentation
(`<coordination-root>/docs/`) with the documentation of the package.
Skills are served from the package as MCP resources.

An install creates the folders `memory-repos`, `tasks`, `worktrees`, `notes`, `temp` and `logs`
under the coordination root. It writes the folder `<coordination-root>/providers/` with the
files of the two [search providers](concepts.md#search-provider) from the package, and the folders
under `<coordination-root>/logs/providers/`; no part of the product starts, indexes or queries a
search provider. It creates
`<workspace-root>/.agents-remember/role-executions/` and adds that folder to
`<workspace-root>/.gitignore`.

Arguments:

- `dry_run=false`: with `true` the call is a preview and changes nothing.
- `include_benchmarks=false`: with `true` the install copies the benchmark fixtures.
- `install_provider_deps=true`: the argument installs no dependency of a search provider,
  because the two search providers, `codegraphcontext-code` and `grepai-memory`, never count as
  enabled. With `true` the install removes what `<coordination-root>/providers/runners/` holds;
  with `false` it keeps that folder as it is.
- `no_cache=false`: the value is carried as the provider-dependency step's cache-reuse flag,
  where `true` would forbid reuse. No path of the product reads it, so it changes nothing
  observable.
- `experiment=null`: selects an experimental install for this one call. It is never a setting,
  and the record of the run names its source.

`experiment=role-capsules` is the one supported experiment. Selection precedence:
the request field, then the environment variable `AR_EXPERIMENT`; empty at
both means that no experiment is selected. An unknown id is refused by name
before anything is written. A selected run probes its capabilities and has the delivery mode
`capsule` or `refused`; a run without a selection has the mode `legacy`. A selected run
returns one record with `experiment`, `source`, `instructionSource`, `mode`, `reason`,
`selectionSource`, `corpusAnchor` and `capabilities`.

Operations: one install run. No path of the product sets up a search provider: the two
provider ids never count as enabled, so no install/start/watch runs — no install, prepare,
seed, watcher start or status. The one provider step of an install is a stop of leftover
watchers of search providers, reported in `providerWatcherRebind`; with no search provider
declared, it stops nothing. The [benchmark runner](concepts.md#benchmark-runner) runs
providerless, that is without any search provider, with the same empty setup.

The `host` part installs or updates the host and its plug-in at the version that the
`paseoRuntime` block of the settings file names. The install reads that block at each use and
never writes it. It never restarts a running host: a needed restart is deferred and reported in
`restartRequired`, with the remedy to run
`agents-remember paseo provision --config <settings file>` from a terminal
outside the host. That restart ends agent sessions and running turns. A preview
(`dry_run`) observes only and reports what would change (`wouldInstallHost`,
`wouldStart`). A settings file without a `paseoRuntime` block answers `configured: false` with a
message that the [Bootstrap call](concepts.md#bootstrap-call) writes the block.

After the host part, the install looks at the dashboard. A running dashboard whose version or
Python source digest differs from the installed build is restarted; a dashboard that does not run
stays stopped. A preview reports `would-adopt` or `would-restart`. `agents-remember dashboard
--daemon` restarts a running dashboard in the same way, and also one whose host or port differs.

Answer fields (delivered):

- `ok`, `coordinationRoot`, `dryRun`, `includeBenchmarks`;
- `installProviderDeps`: the value of `install_provider_deps`;
- `providerWatcherRebind`: the stop of leftover watchers of search providers that every install
  runs: `attempted`, `ok` and `phases`, each with `phase`, `action` and `ok`;
- `experiment`: `selection`, `delivery` and `record`, and for a selected run also `capabilities`,
  `withheldStartupTargets` and `rollback`;
- `host`: `ok`, `changed`, `configured` and `settingsPath`, and, as they apply, `message`,
  `restartRequired`, `changes`, `deferred`, `notice` and `error`; a preview adds
  `wouldInstallHost` and `wouldStart`;
- `dashboard`, when a dashboard runs: `action`, `detail`, `pid` and `sourceDigest`;
- `runningServers`: the tool servers and dashboards that run on the coordination root with their
  build state, as in `server_info`;
- `summary`: `createdDirs`, `copiedFiles`, `unchangedFiles`, `replacedLinks` and
  `removedPaths`; `dependencyRuns`, which is `0` on every run; and `unusedProviderKeys`, the ids
  of the search providers that the settings enable under `contextProviders`, on which the install
  does not act;
- `messages`: the first 5 inline, the rest in the report;
- `reportPath`: the report file of an executed install. A dry run files no report and carries no
  `reportPath`;
- `recoveryActions`, when the run lists any;
- `log`: the detail file that holds the whole result.

Refusals: an experimental install is refused before anything is written (nothing
under the coordination root is created, replaced or removed, and the selected
mode is preserved).

## Drift check: where a stale anchor reports and where it refuses

A [drift check](concepts.md#drift-check), a call of `context_packet` with `include_drift`,
measures the reference anchors of onboarding cards and the validity of the tree. That read
reports and never blocks by itself. Publication is a different question: every code and test
[reference anchor](concepts.md#anchor) must be current at the exact code tree that the memory
is published with, its paired code tree. One that is not refuses the task's closeout, its
landing and the full `memory_quality_check` run with the rule
`R198.1-reference-anchor`: the anchor does not name the blob of the paired code tree, or does not resolve there to its recorded content. <!-- docs-check: identifier: the product prints this code -->
Authoring and a sync merge report that anchor debt for repair instead of waiving it; the
publication that follows still refuses it. An anchor carried at a path that the task deleted
refuses in the same way and also answers `R22.6-carried-stale`, the rule for a carried anchor at a path that is gone; a sync merge only reports it. <!-- docs-check: identifier: the product prints this code -->


## `citation_fix`

Regenerate anchored citation ranges inside the memory worktree of one task.

The call is admitted, that is allowed to write, in two ways: with the
[worktree contract](concepts.md#worktree-contract) of a task (the ordinary way; a contract whose
memory worktree is the memory repository itself is refused), or without a contract on the
configured memory repository while it has no [baseline](concepts.md#baseline) (the admission
before the first baseline, which answers with `admission: "pre-baseline"`).
In every other case a call without a contract refuses and says to start or attach a worktree and
pass its contract path; once the baseline exists, the refusal says so. A
pure move is repaired; renamed, deleted, or ambiguous anchors remain work for the Curator.
Preview with dry_run=true. Check the cards of a change in one call and repair them in
one call: pass `documents` with every onboarding-relative file (a path relative to the folder
`onboarding/`, for example `src/pkg/journal.py.md`), or `document` for one
file (a list of one). A call that names both is refused, and so is an empty list; a
call that names more documents than one task may hold changed files with cards is
refused before anything is read. A named document that does not exist is a refusal
of that document and does not stop the others.
The answer is a status: whether the step went well, with the counts (rewritten,
stale, unreadable, refused). The whole result with every list is in the log's
detail file. The call authors the cards' citation rows into sidecar
references and re-records moved anchors; document selection scopes the run to those
cards.

For a long preview, use `mode="start", dry_run=true` with the same `repo_id`,
`contract_path` and document selection. It returns a `runId` immediately. Poll with
`mode="poll", run_id=<runId>` and the same repository and contract, without document
selectors. A poll returns `running`, `completed` or `failed`; a completed answer is
the synchronous preview answer with `status` and `runId` added. Background previews
share the bounded run registry of `memory_quality_check`: the tool server keeps at most 8
background runs of the two tools, and a start beyond them answers `capacity-reached`. A poll is
answered only for the repository and the worktree contract of its start, and it is refused with
`memory-candidate-pair-stale` when the task's code or memory tree has changed since the start.
Background previews never apply repairs. The default is `mode="sync"`, and a call that writes
runs synchronously.

While a long synchronous call runs, the tool server still answers reads and polls. Calls that
may write run one at a time, in the order in which the tool server received them; a cancelled
call keeps its place until its work has stopped. A tool that runs asynchronously by itself does
not wait in that order.


## `citation_note_revision`

Revise one reference's note in the memory worktree of one task, changing nothing else.

A reference's `note` is a claim about the code its targets anchor. Re-authoring through a citation table
resolves those targets again; a Curator who must change only the note uses this tool.
The reference identity is `(sidecar path, references key [n])`: locator and blob are not
identity, so a mechanical re-point keeps it. Pass `repo_id`, `contract_path`, the `card`
(for example `onboarding/src/pkg/journal.py.md`), `reference` (`n`), the new `note`, and `base_digest`:
the sidecar's SHA-256 as the caller read it. Preview with `dry_run=true`.

Every target, anchor and other reference of the card keeps its exact value, no reference number is
minted, and the task the contract names is recorded as the author of the revision. Refused, with
nothing written: a key the sidecar does not hold, a `base_digest` that is not the sidecar's current
bytes (a stale input: read it again), an empty note (a removal is a different act), an absent card or
sidecar, and any revision whose written bytes would change something other than that
note. Moved anchors stay `citation_fix`'s work, and the publication guard judges anchors, not whether a
note is true.


## `context_packet`

Where the caller works and what needs action.

The answer is slim: the roots (the code worktree, the memory worktree, the enclosure and the task
document), the task's ID and its master, action lines when they are needed, the serving build and
the pair's commits. A call for a task passes `task_context`.

`include_drift` measures the reference anchors of the cards and the validity of the tree.
A stale reference is reported and never blocks by itself. Whether an invariant is still current
is answered by `knowledge_read` and `knowledge_integrity_check`, not here. A task that changes a
source file must change that file's card or record why not.
Nothing is repaired: `citation_fix` re-records anchors that only moved, and a Curator re-authors
a stale one. Without `include_drift` nothing is measured.

`include_freshness` fetches the remotes and writes `FETCH_HEAD`, so the tool is marked as a
write.


## `curator_coherence`

Prepare, publish, inspect or validate the [coherence record](concepts.md#coherence-record) of one
task, which the tool's description calls the "curator-coherence authority". In the record the
Curator judges each candidate: each card, route overview and row of the entity catalog that the
task's change reaches. The record is a JSON file that a manifest names by the SHA-256 of its
content; the manifest is `<task id>-curator-coherence.json` in the folder `notes/reports/` of the
task folder. A Markdown report beside the record is generated from it for reading, and the tool
never reads it back.
`prepare` writes the candidates to the enclosure's
`reports/curator-coherence-candidates.json` (with a `.md` rendering beside it for reading),
together with the six `expected_…` values that `publish` needs under `expected`, and answers with
the count of candidates by kind, the set's digest and the locations; the Curator reads the
candidates from the file. `status` and `validate` answer with the state, the counts and what
blocks. `publish` requires the unchanged candidates plus one `disposition`, `rationale` and
`evidenceRef` for every candidate, written by the caller; the tool fills in none of them, and the
[Curator's guidance on coherence judgments](https://github.com/Foxfire1st/agents-remember/blob/main/skills/l-01-agent-lifecycles/reference/curator/coherence-judgments.md)
explains the eight dispositions. It answers the outcome with the count of published judgments;
on a refusal it names which expectation does not hold. `publish` takes `judgments` (one entry for
each candidate) and `review_assessments` (the Curator's assessments of knowledge records, each
`concern_found`, `no_concern_found` or `unresolved`) inline, or from one JSON file
(`judgments_file`, an absolute path) holding the two lists; the file is read once and its SHA-256
is answered, and a call that supplies a list both ways is refused.
`publish` also requires every one of `semantic_requirement_revision`, `delivery_attempt`,
`expected_predecessor_digest`, `expected_code_candidate_tree`,
`expected_memory_candidate_tree`, `expected_task_topology_fingerprint`,
`expected_task_intent`, `expected_attestation_sha256` and `caller` to be supplied and
non-null. `semantic_requirement_revision` names the task's requirement packet as `<ID>@v<N>`, and
`delivery_attempt` names the Worker's delivery attempt. The six `expected_…` values hold what
`prepare` saw: the digest of the record that the publication replaces (empty for the first), the
code tree and the memory tree, a digest of the task's place in its master and sprint, the task's
intent, and the SHA-256 of the attestation of the [Curator's checklist](concepts.md#curators-checklist).
`caller` names the caller's role and a task document: the task's own document for a Curator, the
sprint's document for any other role. Each action takes only its own arguments, so `status`,
`prepare` and `validate` refuse a call that supplies any of these nine. `publish` rejects missing,
extra, duplicate, malformed, or stale judgments and publishes atomically; with
`freeze_snapshot=true` it also writes an unchangeable snapshot that ties the delivery attempt to
the record. An `evidenceRef` names one file in one of three namespaces, `code:`, `memory:` or
`task:` (for example `code:src/example.py`); the tool records the SHA-256 of that file and checks
it again at each later validation. `validate` is the same validator that `memory_quality_check`
and the closeout use.

After the closeout of a task, `status` and `validate` answer the state `closed-out` with the
closeout's code commit and memory commit and the digest of the coherence record. They compare
nothing with the live tree then. A task without a coherence record answers `absent`.


## `memory_baseline_adopt`

Create the first [baseline](concepts.md#baseline) of a memory repository; the tool's description
calls it the "first attributed external-memory baseline". Mutating: commits memory content and
refreshes the [memory attribution cache](concepts.md#memory-attribution-cache) `memory.md`.
While anchors are stale, the adoption stops with the state `blocked-drift`, unless accept_drift=true.
`accept_drift` lifts that stop and no other. It never waives a
validator violation: a code or test reference anchor that does not hold at the exact paired code
tree is refused with `R198.1-reference-anchor` whether the option is passed or not. A tree <!-- docs-check: identifier: the product prints this code -->
that holds such an anchor answers `blocked-invalid`, the state of a tree that the validator refuses,
also when the option is passed, and the repair is the anchor, not the option.
The first baseline is validated before it is
committed. Read `memory_baseline_status` first. Preview with `dry_run=true`: it answers
`would-adopt` only when the staged-tree validation would pass, and a refusal or a dry run leaves
the index, the refs and every file as they were. A missing, stale or malformed `memory.md` is a
cache state and does not decide adoption; only reachable `Code-Commit:` attribution does. The
adoption procedure is documented in the adopt-existing-memory guide (`docs/guides/adopt-existing-memory.md`).


## `memory_carryover_plan`

Plan (non-mutating) the [carryover](concepts.md#carryover) of richer cards and knowledge records
from a source branch into the exact open task. Review it, then apply before the task's normal closeout and landing.
Files no report.

Arguments: `repo_id`, `contract_path`, `source_memory`, `official_code_ref`, `source_code_ref`,
`old_base` and `replace_existing=false`. The guide
[Carry Memory From a Branch](guides/carryover-from-branch.md) explains each argument and the
plan's decisions.


## `memory_carryover_apply`

Apply a reviewed carryover plan in the memory worktree of the same open task. The call takes the
arguments of `memory_carryover_plan`, a required `intent_note` and the optional list
`include_review_required`. It makes no Git commit: the task's closeout commits the result. An
executed apply files a report and names it in `reportPath`. The guide
[Carry Memory From a Branch](guides/carryover-from-branch.md) describes the procedure.


## `memory_quality_check`

Check memory before closeout using drift-integrity and style checks.
It never changes code or memory. A full call with the worktree contract of a task (a
contract-scoped call that names no `checks`) atomically replaces the
[Curator's checklist](concepts.md#curators-checklist) at
`<enclosure>/reports/curator-memory-quality.md` and its `.json` attestation, which names the code
tree and the memory tree that the run judged and the SHA-256 of the checklist; a call that names
`checks` and a call without a contract write neither. Every contract-scoped execution also
publishes the complete structural census, the cards, route overviews and rows of the entity
catalog that the task's change reaches, to `<enclosure>/reports/memory-census.json`; the answer
carries the counts and the checklist's location instead of the census, the sample and the
catalogue. The checklist needs no gate result and does not certify semantic decisions.
ok=false means findings exist (e.g. dirty-source
drift), not that the tool failed. `curatorActionableCount` and
`qualityChecklistStatus` tell the Curator when the repairs of the cards are done: the count must
reach `0` and the status must read `ready-for-closeout`. Once the checklist is
`ready-for-closeout`, the combined `checklistStatus=coherence-required` means the caller must
prepare, publish and validate the [coherence record](concepts.md#coherence-record) with `curator_coherence`;
`closeoutReady=true` is impossible until that same validator accepts the record. The checklist
and the attestation are written byte for byte the same for the same inputs, so a rerun with the
same inputs keeps the published coherence record current; changed inputs make it stale and require
a new publication. The final commit stamps of the cards stay the closeout's work. The request has
exactly one `mode`:
`sync` and `start` carry the repository and optional fields for the scope, the checks and the
detail, while `poll` carries the repository, the run id and, for a run on a task, the same
contract path. A call with the repository only checks the memory repository itself, and its
result never counts as the check of a task's candidate. A start beyond the capacity returns
`capacity-reached` without launching work. A poll returns the run's result unchanged, bound to
the code and memory trees that the run judged; `run-not-found` means the run was evicted,
belongs to another repository, or the tool server restarted, so start the run again
with the original contract path for a run on a task.

The answer carries `invariantsWithoutProofCount`, the number of invariants that have no proof.
The checklist lists them as information, not as a gate. An item of the
[worklist](concepts.md#worklist-and-knowledge-gate) carries in `facts.context.routeChain`, for
each path it names, the [families](concepts.md#family) whose [routes](concepts.md#route) the path
lies under, each with its `id`, `title` and `routes`.

A check on a task also reports two findings that refuse nothing and do not count as actionable:
`landing-cut-card` for a card that lost text against the task's memory base (at least 8 lines
removed, and more than twice the lines it adds), and `landing-added-file` for a file that the
task adds outside the knowledge folders.

The synchronous check uses Git's tracked and non-ignored untracked
source files. Its `sourceFiles` listing counts the files it lists (`listedCount`), the files it
read (`indexedCount`) and the files it skipped (`skippedCount`), and names each skipped file with
its reason in `skippedFiles`, for example a binary file, even when no claim cites it. Inspection
reads at most 4 MiB plus one byte per file without retaining uncited bodies
or following symbolic links.
A readable citation into an ignored or binary file still verifies its
content; `referenceSourceNotes` names the listing reason on that anchor.
The [path rules](concepts.md#path-rules) govern the notes on each file, not which files a
citation may point into: a readable file excluded by those rules is checked normally, without a
remark.
A citation into an oversized file, link or otherwise unreadable path produces
a finding naming why it could not be read. Uncited build output and binaries
do not fail the check.


## `retire_child`

Retire one role agent of a task. Only the role agent that started it can call it.

The caller retires the role agent
without giving it a turn, and the launch record names the caller and the
reason. An agent in a turn is not retired unless interrupt_turn says the
turn may be interrupted. Any other role agent is refused with the starter
named; an unknown or ambiguous agent id is refused by name and nothing
is archived; a repeated call for an already retired agent changes nothing
and says so.

Arguments: `task_document_ref`, `role`, `reason` (default `delegated seat retired`; texts of the
product also call a role agent a seat), `agent_id` and `interrupt_turn=false`.


## `role_start_set`

Start the Worker, the Reviewer and the Curator of one task in one call.

Only the Manager of the task's master can call this: any other caller is refused
before anything is prepared. Each of the three is then checked as a start of its
role is. The three have the calling Manager as their parent and the same
task as their selection, and each takes its agent, model, effort and
feature values as any start of its role does: pass them
for each role in `worker`, `reviewer` and `curator` (`agent`, `model`, `effort`, `featureValues`).

The Worker opens at once. The Reviewer and the Curator are held: each exists on
the host, is reached by its role and the task's document reference, and has taken no
turn, with its handover written when the role agent is prepared. reviewer_message is
the Reviewer's first work: carried, the Reviewer opens at once with its handover
and that message. `replaces` names, for each role, the role agent that holds that role on
the task, its holder; the start retires the holder first and then starts the successor, as
[One role agent for each role of a task](reference/mcp-tools.md#one-role-agent-for-each-role-of-a-task)
describes for `role_start`. `replaces_interrupt_turn` is the word, for the whole set, that a
replaced holder's turn may be interrupted.

The answer names the three agent ids and says for each whether it has taken a
turn. A repeat with the same request id reconciles the same three agents: it
creates no fourth and opens no held role agent. If one of the three cannot be created,
the call says which, leaves the others as they are, and a repeat completes the
set.


## `route_index_refresh`

Regenerate the [route indexes](concepts.md#route-index), the file `overview.index.json` beside
the `overview.md` of each route under `onboarding/`, so they match the current cards. Apply
requires the worktree contract of a task and writes only that task's memory
worktree, or no contract on the configured memory repository while it has no
baseline (the admission before the first baseline, which answers with
`admission: "pre-baseline"`). Any other call without a contract is read-only and therefore
requires dry_run=true.
