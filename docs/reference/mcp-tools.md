# MCP Tool Reference

The Agents Remember MCP server exposes 38 public tools (`PUBLIC_TOOLS`). Tools **apply by default** — pass
`dry_run=true` to preview first. Repository-scoped tools
take a `repo_id` that must be an allowed repo in the MCP settings. Search the code with
the harness's own tools: the former `cgc_*`, `grepai_*` and `provider_*` code-search tools
were removed (MIK-R112) and are not answered by any text on this page.

This page is a map of the surface, not a complete index of it: the tables below name a subset
of the roster above. Behavior detail lives in the linked skill and reference pages.

**Skills vs tools.** Many skills wrap one or more of these tools and add the
procedure, gates, and ordering around them: the `c-00-initialize-memory-repo` skill drives the `memory_init` MCP tool, the `c-02-memory-quality-control` skill
drives drift reporting (`context_packet` / `memory_quality_check`), the `c-09-git-worktree-manager` skill drives the `worktree_*`
MCP tools, and the `c-12-closeout` skill drives the `*_closeout_*` MCP tools. When the docs say "run a core skill,"
the agent runs the skill, which calls the matching tool with the right
preconditions. Call the raw tool directly only when you do not need the skill's
surrounding procedure. See the [Skills reference](skills.md).

## Server & context

| Tool | Purpose | Key args |
| --- | --- | --- |
| `server_info` | Report the resolved configuration: build, roots, allowed repos, provider keys left unused, and, on a role-launched server, the tool server name and agent binding. It does not list the tools. | — |
| `context_packet` | Where the caller works and what needs action: the roots, the task, action lines only when needed, the serving build commit and the pair's two commits. | `repo_id`, `include_drift=false`, `include_freshness=false` |

## Structural role control

| Tool | Purpose | Key args |
| --- | --- | --- |
| `dispatch_agent` | Create or replace one canonical role seat and durably pin its exact initial brief. Plane-hosted callers use injected seat identity plus direct-child scope; identity-free developer launchers use target-document resolution plus role-altitude validation. | `task_document_ref`, `role`, `brief`, optional `label` |
Structural messaging, seat retirement/rename, lifecycle signals, and gates left the public
surface (MIK-R112): `message_parent`, `message_child`, `retire_child`, `rename_child`,
`rename_self`, `lifecycle_start`, `lifecycle_resume`, `lifecycle_turn_end_notification`,
`lifecycle_end`, `switch_lifecycle`, `lifecycle_phase`, `lifecycle_gate`, `gate_decide`,
`gate_list`. Messaging between seats goes through `role_message` (by agent id or by role
plus task references); lifecycle moves are session-driven. `role_start` and `role_message`
stay public; see Role agents below for their rows.

Runtime session, lifecycle, inbox-row, adapter, vendor, and gate identifiers are private
control-plane correlations. The plane may use exact addressing internally for the initial brief,
delivery, recovery, diagnostics, and trusted dashboard administration, but those operations are
not registered as agent MCP tools and must not appear in role briefs or handoffs.

`dispatch_agent` is the only public spawn verb and its two caller kinds are disjoint. Presence of
plane-injected hosted identity selects structural child authorization; absence selects the ambient
launcher, which has no parent seat and must address a canonical task document at the target role's
altitude. The request never supplies caller identity or selects a mode. Both modes share settings,
internal creation/readiness, exact brief pinning, rollback, and canonical seat publication. A
plane authorization failure remains a refusal and never retries as ambient. `dispatched` and
`dispatch-queued` both mean the brief is durable; callers do not poll readiness or send it again.

`role_start` answers `ok`, the agent id, and the handover location; the rest rides in the
detail file. Only a role agent that AR launched can call it, and only roles its rule allows
under its own sprint or master. A `request_id` reconciles the same agent, never a second: only
the agent that started an execution repeats its `request_id`. Starts run one at a time; a call
waits up to 60 seconds for another start to end, then is refused with a `nextAction` that says
to call again with the same arguments. A finished, canceled, or rejected recipient is reconciled,
never relaunched; an archived recipient is refused. Requests that already have a receipt are
answered from that receipt.

`role_message` names its recipient either by `agent_id` or by `role` plus the task references
its class requires; an address by role never means the caller itself. The recipient reads the
text behind one line naming the sender: `From <role> · <task id or Projects> · agent <sender
agent id>`. A mid-turn recipient is handed the message in its running turn, which the host may
cancel. A recipient waiting on a permission decision, whose start has not finished,
or that is in a turn the host cannot write into is answered `held`, not refused: the
answer carries `status` `held`, the reason (`heldReason`), what waits and why (`detail`),
the permission as the host names it or null when unnamed (`permission`), the sender's
waiting counts (`waitingMine`, `waitingTotal`), and the issued id (`heldMessageId`), which a
later call can withdraw with `withdraw_held_message_id`. Delivery happens in the order taken
when the recipient can be written to; nothing has to be sent again. Code `recipient-busy`
is refused outright only when the recipient already holds twenty messages; that refusal
names waiting for a delivery or withdrawing a waiting message instead. Without `wait` the call
answers `ok`, the status, and how the
recipient took the message once the host accepted it. With `wait` it returns when the turn that
consumed the message ends. When the host cannot say which turn consumed the message, the call
returns `accepted` without a text and detail says the reply must be read later. A reply to a
message delivered during a turn can be the running turn's text; detail says so then. A reply
over the exception bound rides as its beginning with the whole in the detail file. Never wait on
an agent that may be waiting on you.

## Install & scaffolding

| Tool | Purpose | Key args |
| --- | --- | --- |
| `runtime_install` | Reconcile package-owned runtime (AGENTS.md templates, skills, docs, runtime folders) into the coordinator; optionally install benchmark fixtures. The final `host` part provisions the build-pinned host/plugin from the per-use shared `system/settings.json` block; shared authority is never written and needed restarts are deferred. Missing block is named; dry run has no host mutations. **Preserves** user data (`memory-repos/`, `providers/data/`, `providers/runners/`) and **replaces** the managed scaffold (`skills/`, `docs/`). No provider install/start/watch path remains (MIK-R112): the install stops leftover watcher processes as its only watcher action and reports enabled provider keys once as unused (`unusedProviderKeys`); `install_provider_deps` no longer drives installation. Leftover provider runtime folders are reported with sizes; only the `providers-remove` command deletes them. `experiment` is a per-call input (never a setting) selecting an experimental cutover for one run; its source is named in the record. | `dry_run=false`, `include_benchmarks=false`, `install_provider_deps=true`, `no_cache=false` |

## Memory & onboarding

| Tool | Purpose | Key args |
| --- | --- | --- |
| `memory_init` | Initialize (or repair) a repository's memory root. | `repo_id`, `dry_run=false`, `initialize_git=true` |
| `memory_quality_check` | Closeout memory-quality gate (drift integrity + style checks). A full contract-scoped call also atomically replaces the curator worklist at `<worktree enclosure>/reports/curator-memory-quality.md`, combining quality findings, current-addition onboarding coverage, route-index preview, drift candidates, and report-only evidence. | `request={"mode":"sync", "repo_id":..., "checks":..., "detail_limit":50, "contract_path":...}`; use `mode:"start"` for async admission and poll only with `mode:"poll"`, `repo_id`, `run_id` |
| `citation_fix` | Regenerate anchored citation ranges only inside a contract-selected leaf memory worktree; official memory, ambiguous anchors, renames, and deletions refuse. | `repo_id`, `contract_path`, `document=None`, `expected_snapshot=None`, `dry_run=false` |
| `route_index_refresh` | Regenerate `overview.index.json` route indexes to match the onboarding tree. **Writes** into the memory root it resolves. | `repo_id`, `dry_run=false`, `contract_path=None` |
| `knowledge_read` | Name the records of one memory-tree view (`source_context`, `invariant`, `family`): each row holds the text ID, kind, state and memory-checkout location. No record text: open the named files with your own tools. | `memoryRoot`, `view`, `limit=32`, `continuation`, view seeds |
| `knowledge_diff` | Name the changed records of two memory trees beside a caller-readable report holding the whole comparison: the two trees, counts by kind, changed records, report location. | `memoryRoot`, `beforeRevision="HEAD"`, `afterRevision`, `recordId`, `path` |

`memory_quality_check.request` is exactly one shape. `sync` and `start` accept repository,
scope, checks, and detail fields; `poll` accepts only `mode`, `repo_id`, and `run_id`. Do not resend
start fields while polling, even when their values equal defaults: the discriminated boundary
rejects contradictory presence before it reads the run registry. A unique start at capacity returns
`capacity-reached` without a `runId`; poll or wait for active work, then submit a new start request.

The three rows above support the same optional `contract_path` as the `worktree_*` verbs: a leaf
enclosure contract path. `memory_quality_check` carries it inside its discriminated `request`;
the other two tools take it directly. Omitted, they resolve the configured **official** memory repo
(unchanged). Supplied, they act on that leaf's **memory worktree** and measure it against the leaf's code
worktree — how a curator checks its own change-set before handing it back, and the only correct way
to run `route_index_refresh` from inside a leaf, since without it that tool writes indexes into the
official repo and leaves it dirty. A contract naming another repo, or one whose memory worktree is
gone, is refused; nothing falls back to the official repo. The response carries `onboardingRoot`, so
which tree was acted on is always visible.

Only a full leaf-scoped sync/start `memory_quality_check` request writes the curator checklist. It reports
`curatorActionableCount`, the **raw** `qualityChecklistStatus`, and the combined `checklistStatus`; the
curator reruns it until the actionable count is zero and the raw status reads `ready-for-closeout`. The
combined field is rewritten to `coherence-required` only when the coherence record is then missing or
stale — that is the coherence gate, cleared with `prepare` → `publish` → `validate` — and `closeoutReady`
becomes true only once that validation passes. Subset checks and official-repository checks do not create
that artifact. The checklist is
outside both Git worktrees, replaces its predecessor instead of accumulating timestamped files,
and `worktree_cleanup`/`worktree_abandon` remove its reserved `reports/` directory with the
enclosure. Dirty-source and real-commit residuals remain listed separately so the pre-commit loop
does not fabricate closeout metadata.

The `reports/` directory is disposable evidence, not the live operation journal. Lifecycle reads
have two strict routes. A live locator reaches the exact root-local
`.lifecycle/enclosure-manifest.json` and canonical journal/history. A terminal locator reaches only
the exact external archive/receipt plus surviving configured-contract truth; it never falls through
to the old root. Terminal cleanup archives and reads back the canonical evidence, publishes the
external receipt, and only then may remove the enclosure root and its reports. A task document,
task/worktree scan, naming inference, caller-supplied root, or reports path is never
operation-location authority.

The repository-profile quality gate has the same enclosure-owned lifetime for its evidence. Every
completed leaf-closeout or leaf/master-integration gate atomically replaces
`<worktree enclosure>/reports/test-results.md` with the run status, command, timestamps, scope,
exit code, profile/plan/adapter identities, memory-cap facts when applicable, and the complete
adapter output.
A failed gate writes the report before refusing and names its path in the error. An interrupted
run cannot replace the previous completed result, and cleanup/abandon removes the file with the
same `reports/` directory. The gate payload returns that stable path as `reportPath`.

Executed closeout and integration gates expose their typed result under `code_quality_gate` or
`quality_gate`. `reportPath` names the developer-facing `reports/test-results.md` summary.
`publishedResultPath` names the manifest-verified immutable generation's profile-declared decoder
artifact for both fresh success and recovery; `resultArtifact` carries its repository-relative
identity. The immutable machine result never replaces or renames the human summary, and there is no
universal result filename fallback.

Citation ranges are not repaired by hand. MCP `citation_fix(repo_id=<id>,
contract_path=<enclosure contract>, dry_run=true|false)` regenerates every range that can be
regenerated from its anchor — the whole tree in one call after a package move — and prints a work
order for the rest: the anchor, the range, the file, and every location in the code tree that does
hold that anchor. It repairs pure MOVES only, where a symbol kept its name and changed file; a
rename, a deletion and an ambiguous match are refused rather than guessed at. `contract_path` is
**required**: there is no argument list that names the official memory repo, and a contract that
points at it is refused. The checkout-only CLI remains a development adapter to the same guarded
application operation, not an agent-facing route around MCP.
| `read_ar_files` | Name which knowledge governs up to 20 paths: existence, governing card as a memory-worktree location, record counts by state, and action-asking records by ID, kind, state and card heading; overviews once per call, nearest first. No source, card or overview text: read the named files with your own tools. `refresh` and object-form entries are refused as `argument-removed`. | `repo_id`, `files:[strings]`, `task_context` for a leaf |

## Memory baseline & carryover

| Tool | Purpose | Key args |
| --- | --- | --- |
| `memory_baseline_status` | Report drift and Git attribution/cache observations for adopting an external-memory baseline. | `repo_id` |
| `memory_baseline_adopt` | Adopt an external-memory baseline and refresh its computed ledger cache (gated on clean/accepted drift). | `repo_id`, accept-drift / dry-run options |
| `memory_carryover_plan` | Plan carrying richer onboarding from a source branch into an open recovery leaf after code lands. | `repo_id`, `contract_path`, repo/branch scope |
| `memory_carryover_apply` | Apply an approved carryover plan in that recovery leaf once the code has landed officially. | `repo_id`, `contract_path`, plan + intent |

## Worktree lifecycle & closeout

| Tool | Purpose | Key args |
| --- | --- | --- |
| `worktree_start` | Create/load a task contract and code (+ external-memory) worktrees. | `repo_id`, `task_name`, `worktree_name`, `workflow_kind="light-task"`, `dry_run=false`, `memory_choice` |
| `worktree_enclosure_adopt` | Explicitly adopt one exact readable pre-locator enclosure. Validates the configured contract/root pair and writes an audited locator receipt; normal readers never invoke it. Dry-run by default. | `contract_path`, `expected_worktree_group`, nonblank `rationale`, `dry_run=true`, `approved=false`, optional `expected_publication_request_id` |
| `worktree_attach` | Re-attach to an existing task contract without mutating Git. | `repo_id`, `task_name` / `contract_path` |
| `worktree_sync` | Pull the moved official line into a live worktree. | `contract_path`, `dry_run=false` |
| `worktree_pause` | Pause an atomic master and hand control back; publishes nothing. | `contract_path` |
| `worktree_checkpoint_landing` | Partially publish an unfinished series master by landing its finished leaves; not a pause. | `contract_path`, `strategy`, `dry_run=false` |
| `worktree_record_landing` | Record a commit the landing target already holds. | `contract_path`, `code_commit`, `memory_commit`, dry-run options |
| `worktree_status` | Report strict task-addressed lifecycle status without queue input. A live locator resolves to the root manifest/journal and its executable controls; a terminal locator resolves to the exact external archive/receipt plus surviving contract truth, distinguishes archive-ready from cleanup-completed, and returns the archived `cleanupArguments` with exact retry `nextArgs`. | `repo_id`, `task_name` / `contract_path` |
| `worktree_closeout_preview` | Non-mutating preview of a worktree-backed closeout. | `contract_path`, code/memory commit messages |
| `worktree_closeout_apply` | Validate every enabled explicit nonblank input, claim the exact first-ready door generation through a short CAS, then start or observe its durable task-bound closeout and return promptly. Poll `worktree_status`; no operation ID is exposed. Agents Remember source commits run the leaf change-set-scoped quality contract (`--targeted`) before Git commit — the full wrapper runs once per master at the master integration gate. | `contract_path`, `intent_note`, explicit message for each enabled commit leg |
| `worktree_integrate` | Start or observe durable task-bound landing (`ff-only` or `replay`) and return promptly. Poll `worktree_status`; retries with conflicting input refuse. | `contract_path`, `strategy`, `dry_run=false` |
| `worktree_sync` | Pull the moved official line into a live worktree: fetch, merge code, fast-forward memory, advance the recorded base pair. Conflicts are retained for agent resolution (`continue` / `cancel` / `reconcile`). | `contract_path`, `memory_sync_choice`, `resolution_action`, `knowledge_resolution`, `dry_run=false` |
| `worktree_pause` | Pause an atomic master and hand control back to the developer; publishes nothing. | `contract_path` |
| `worktree_checkpoint_landing` | Land an unfinished atomic master's accumulated line into its super branch and keep the master open; requires developer approval. | `contract_path`, `strategy`, `dry_run=false` |
| `worktree_record_landing` | Record a task's code landing through a pull request with the landed commit. | `contract_path`, `landed_code_commit`, `landed_memory_content_commit`, `dry_run=false` |
| `worktree_operation_control` | Execute one currently advertised task-addressed control for an exact closeout/integrate/direct-landing generation. Retry/recover preserve accepted input; cancellation proves worker exit and Git safety; revise/retire/supersede are evidence-aware. | `contract_path`, `operation_kind`, `action`, `expected_generation`, nonblank `intent_note`, action-specific inputs, `dry_run=false` |
| `worktree_legacy_operation` | Explicitly inspect, migrate, or archive one exact schema-1 operation. Migration is limited to the proven blank-message incident; normal readers remain current-schema-only. | `contract_path`, `operation_kind`, `action`, inspect-bound `expected_digest`, action-specific messages/reason, `dry_run=false` |
| `direct_landing` | Grant-gated branch-addressed landing for a named granted leaf, without a leaf worktree enclosure. Verifies the series code commit at the branch HEAD, or creates it from the leaf's uncommitted changes with `code_commit_message`; persists a durable direct-landing generation before Git mutation and recovers it through `worktree_operation_control`. | `contract_path`, `leaf_task_document_ref`, optional `code_commit` (verify) or `code_commit_message` (create), explicit enabled-leg messages, `intent_note`, optional gated `candidate_tree`, `dry_run=false` |
| `direct_execution_grant` | Give, take back, or reclaim direct-execution grants before work starts, as an architect. The developer gives the same record with the dashboard control; `task_doc` has no operation that gives, changes, or removes a grant. | `task_document_ref`, `action` (`give`, `revoke`, or `reclaim` — reclaim takes back only abandoned leaves' grants with clean checkouts and reports what was preserved and why; started leaves keep their grants under rule 5) |
| `worktree_cleanup` | After integration, archive/read back canonical lifecycle evidence, publish an external terminal receipt, then remove worktrees, merged task branches, reports, and the enclosure root. Archive-ready is not cleanup-completed; retry this exact public call with its archived `teardown_providers` value until surviving contract truth records completion. Bounded pre-locator exception (MIK-R82 rule 5): with no locator, no worktree folder, and no work branches, the call finishes with nothing to archive/delete, records the why in the surviving contract, and reads it back. This is non-terminal for task documents. | `contract_path`, `dry_run=false`, `teardown_providers=true` |
| `worktree_abandon` | Abandon an unintegrated generation through exact contract/journal/Git authority and preserve terminal archive proof before deletion. Archive-ready but incomplete abandon retries this exact public call with its archived `force` value until surviving contract truth records abandonment. Bounded pre-locator exception (MIK-R82 rule 5): with no locator, no worktree folder, and no work branches, the call finishes with nothing to archive/delete, records the why in the surviving contract, and reads it back. | `contract_path`, `dry_run=false`, `force=false` |
| `lifecycle_finalize_task` | Prove the landed edge, resolve the exact contract-bound leaf, refuse before cleanup unless every parent/nested step is done, then complete that leaf and, when it declares an existing immediate parent (or names none and its folder's `task.json` master lists it), automatically derive and reconcile that exact row; a sub-task naming none whose folder `task.json` is not a master is refused. Standalone/no-parent tasks remain supported; the parent document's own task status and higher ancestors are not completed. | `contract_path`; optional `task_doc_path`, `master_doc_path`, and `subtask_number` are independent identity assertions; `dry_run=false` |

Closeout and landing record the actual code/memory Git pair. New memory-content commits carry
`Code-Commit` attribution; `memory.md` remains an ignored computed cache, never an additional commit
leg or admission/recovery authority. Missing or damaged cache data does not block those operations.

Receipt-file existence alone does not select the terminal route. While the locator is still live,
an identical accepted `worktree_cleanup` or `worktree_abandon` retry reuses the exact published
archive/receipt bytes and completes terminal-locator publication. Only after the locator becomes
`terminal-archived` does `worktree_status` use the terminal route to distinguish archive-ready from
surviving contract completion; the same accepted disposition with its original public arguments
finishes an incomplete destructive tail. This is not a `worktree_operation_control` action. A
conflicting terminal request must expose the archive-accepted verb as its public retry action; it
never invents a fallback reader or second cleanup route. The archive request identity includes
typed `cleanupArguments`: `{teardown_providers: <bool>}` for `worktree_cleanup` or
`{force: <bool>}` for `worktree_abandon`. Status and conflict payloads return that object and the
exact `nextArgs`. A retry with a different value refuses; omitted/default arguments are not
reconstructed as recovery intent.

For a same-address reopened or abandoned successor, reservation first proves the exact terminal
archive and restartable predecessor contract. Under that reservation, the stable contract file is
atomically changed only when its bytes equal the accepted predecessor tombstone; already accepted
successor bytes are idempotent, and every other byte state refuses. This is a strict successor
publication transaction, not a generic overwrite or compatibility reader.

Landing and worktree answers carry the state reached plus the next call (`nextTool` with
`nextArgs`); the whole result stays in the `log` file. `worktree_status` additionally reports one
line when task-document copies differ from their sources. A task-state refusal answers `ok:false`
with the state's own sentence, a `code`, and the one following call; it never raises. A dry run
that names blockers answers a blocked state. Role start writes the leaf document and its
requirement files and nothing else; a missing requirement file still succeeds, with the INDEX row,
the handover, and the answer naming it.

## Closeout scheduling projection

| Tool | Purpose | Key args |
| --- | --- | --- |
| `closeout_door` | Publish or inspect one contract-owned closeout generation. Declare/update-provenance bind complete current evidence; defer/resume/withdraw change only the exact generation's scheduling disposition. Claim is intentionally absent. | request with `action`, `contract_path`, action-specific `candidate_task_document_ref`, `expected_generation_id`, `grade`, `admission`, optional declared `caller` |
| `closeout_queue` | Inspect or rebuild one sprint's disposable waiting-only projection. It is either exact-current `valid-built` or non-admitting `invalid-empty`; rebuild derives solely from current task plus waiting-door facts. | request with `action:"status"|"rebuild"`, `sprint_task_document_ref`, optional declared `caller` |

The queue has no declare, select, claim, defer, withdraw, lifecycle, commit, certification,
recovery, replan, drain, or stale-row transition. Claim transfers the current generation from its
door into the enclosure-root operation journal. Queue invalidation or absence cannot destroy or
strand accepted operation evidence.

## Task documents

| Tool | Purpose | Key args |
| --- | --- | --- |
| `task_doc` | Author JSON-primary task documents and deterministic markdown. Every intrinsically valid mutation publishes during every queue/operation phase and returns per-sprint `projectionEffects`; an incomplete effect carries its exact rebuild `nextAction`. `skip_step` requires a nonblank reason; `discard-unstarted` removes only centrally proven never-started planning work without completion fiction; `Completed` refuses unresolved units. | `repo_id`, task/contract target, `operation`, edit payload, `dry_run=false` |
| `task_reopen` | Reopen a fully landed leaf under the same leaf id so new work can be declared explicitly. | `contract_path`, `dry_run=false` |
### task_doc operations

`task_doc` authors the JSON-primary task document (`ar-task-document/v1`); `task.md` /
`<slug>.md` is generated and never parsed back. Every operation is a write: `get` and
`read_steps` are removed operations (calling one is refused with the rendered `.md` and
JSON file names to read instead). Locate the doc by `task_name` (also resolves the
contract for the lifecycle key) or `contract_path`; pass `slug` for a series sub-task
(`<slug>.json`), omit for a standalone task (`task.json`). `dry_run=true` builds,
validates and returns diff/wouldLose without writing. A write answers `ok` and
the changed unit in a phrase (`renderedPath` rides `create`, `reviewState` rides reviews);
the rendered Markdown and per-sprint `projectionEffects` ride the whole result in the log
file. `linkage_report`
answers the fact counts with the report location. Long `fields`/`section_body` may ride
as files (`fields_file`, `section_body_file`). A master-sync failure answers `ok:false`
with `master-sync-failed` and leaves the leaf unchanged.

- `create`: new doc from `fields` (`id`, `slug`, `title`, `kind` `subTask`|`master` — the old
`light` kind is refused — `repo`, `type`, `createdAt`, `objective`, `requirements`, `steps`,
...). A master takes `subTasks` plus ordered sections instead of steps; an orchestration
sprint is scaffolded with empty canonical Judgment and Priority Register sections.
- `replace`: whole-document rewrite from `fields` after schema validation.
- `get` and `read_steps` are removed: calling one is refused with the file names
to read instead. `set_status`: set `fields.status`. `set_field`: scalar/list
field updates. `append_decision`: append `decision={at, decision, rationale}`.
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
leaf doc (`json`+`md`) unless `keep_file=true`. `disposition:'discard-unstarted'` with a
reason discards planning work that never started: forbids `keep_file`, proves absence of
canonical enclosure/operation/seat/review/commit evidence, removes the child sources, keeps a
typed parent audit; started or ambiguous evidence refuses with the next lifecycle route.
- `set_section` writes `section={heading, kind?, body?}`; a canonical register heading must
keep the exact register table shape (write-time validation).
- Review: `begin_review` takes an empty review object (or bounded `developerApproval` /
`additionalRounds`) and increments the round before reviewer work — a repeat resumes the
pending round. `record_review` takes `{verdict, findings:[{findingId, description}]}` first,
then `remainingFindingIds:[...]`; successors only shrink the sealed set. `record_route_review`
takes `{verdict, verdictRef, routes:[{route, verdict, evidenceRef}]}`; the plane stamps the
candidate tree and time, every evidence path must be a real task-relative file, and it
overwrites the prior candidate's review.
- Atomic masters: the operation targets the task-root master document and binds the accumulated
candidate, membership and evidence; a child publishes no independent review; master review is
required only at master-to-parent integration. `branch_addressed=true` (policy-gated,
sanctioned direct execution only) binds the task-root series contract and stamps the candidate
tree from the branch HEAD.
- Execution graph (orchestration sprint): `author_execution_graph` applies one validated atomic
batch of `mutations` (`add_node`, `remove_node`, `add_edge`, `remove_edge`, `move_leaf`,
`set_nature`); judgment-bearing mutations need a `judgmentId` from the sprint Judgment
Register. `attach_master` attaches one master as a single validated batch
(`masterRef`, `number`, `name?`, `scope?`, `status?`, `executionNature?`, `judgmentId?`);
`detach_master` takes `{masterRef}` and refuses while any edge touches the node.
`linkage_report` is the read-only drift report (seat-doc rows, slug-only membership,
mismatches, uncommanded masters).
- `Completed` refuses while any declared step/substep or master row stays unresolved.

See [c-09-git-worktree-manager Worktrees And Closeout](worktrees-c09.md) for the lifecycle and gates.

## Knowledge operations

| Tool | Purpose | Key args |
| --- | --- | --- |
| `knowledge_read` | Name the records of one memory-tree view (`source_context`, `invariant`, `family`) at one snapshot, paged; each row holds the text ID, kind, state and memory-checkout location. No record text: open the named files with your own tools. | `memoryRoot`, `view`, `limit=32`, `continuation`, view seeds |
| `knowledge_diff` | Name the changed records of two memory trees beside a caller-readable report holding the whole comparison. | `memoryRoot`, `beforeRevision="HEAD"`, `afterRevision`, `recordId`, `path` |
| `knowledge_integrity_check` | Structural-rule violations, matched review conditions, scope and limits; no verdict, no causal explanation. | `memoryRoot`, `codeRoot` |
| `curator_coherence` | Author, publish, inspect or validate one leaf's curator-coherence authority (`prepare` → `publish` → `validate`). | one action's request shape at a time |

## Role agents

| Tool | Purpose | Key args |
| --- | --- | --- |
| `role_start` | Start one role agent for one canonical selection, caller as parent; repeating `request_id` reconciles the same agent. | `role`, `request_id`, the role's task references, `agent` / `model` / `effort` |
| `role_message` | Send one text message to one role agent; with `wait`, return its reply. | `text`, `agent_id` or role + task references, `wait=false`, `timeout_seconds` |

The capsule operation and skill catalog left the surface with the skills surface tools
(MIK-R112): `role_capsule_compile`, `skill_catalog_list`, `skill_catalog_read`. Skills are
read through the `skill://` resources and the `skills/list` extension instead.

`role_message` answers `held`, not refused, when the recipient waits for a permission
decision, its start has not finished, or it is in a turn the host cannot write into; only
a full hold store (twenty waiting messages) is refused as `recipient-busy`. Other refusals
name one reason: `recipient-not-found`, `recipient-archived`, `recipient-ambiguous`,
`recipient-cannot-be-resumed`, `scope-check-failed`, `caller-has-no-binding`,
`no-paseo-runtime-configured`, `host-unreachable`. A finished turn is a fact about the
recipient's turn, never AR acceptance of a requirement. `role_start` calls run one at a time
(60s); only the starter repeats its `request_id`.

## Session lifecycle

The lifecycle signal tools left the surface (MIK-R112): `lifecycle_start`,
`lifecycle_resume`, `lifecycle_turn_end_notification`, `lifecycle_end`, `switch_lifecycle`,
`lifecycle_phase`. Lifecycle moves are session-driven; states and the ambient correlation
are described in the lifecycle frame.

## Providers

The CGC, GrepAI and provider tools were removed (MIK-R112): `cgc_symbol_search`, `cgc_callers`,
`cgc_callees`, `cgc_dependencies`, `cgc_complexity`, `cgc_visualize`, `grepai_search`,
`grepai_trace`, `provider_status`, `provider_diagnostics`, `provider_watchers`. Nothing starts,
installs or watches those provider processes anymore; search the code with the harness's own
tools. A settings file that still holds their keys loads without error; `server_info`
names the keys once as no longer used.

## Benchmarks

The benchmark tools left the surface (MIK-R112): `codex_benchmark_prepare`,
`codex_benchmark_run`. Benchmark cases run through the benchmark runner CLI instead.

---

The authoritative source for this surface is `mcp/src/agents_remember/mcp/registration/`
(one module per tool family, each holding that family's `@server.tool()` registrations;
`create_server` only walks `TOOL_REGISTRARS`); response shapes are enforced by the models in
`mcp/src/agents_remember/models/` via `tool_registry.PUBLIC_TOOL_RESPONSE_MODELS`.
