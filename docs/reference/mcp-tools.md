# MCP Tool Reference

The public MCP roster is `PUBLIC_TOOLS` in
`mcp/src/agents_remember/models/tools/public_roster.py`. `dry_run` defaults vary by tool.
Pass `dry_run=true` explicitly for a preview when the tool supports it. Repository-scoped tools
take a `repo_id` that must be an allowed repo in the MCP settings. Search the code with
the harness's own tools: the former `cgc_*`, `grepai_*` and `provider_*` code-search tools
were removed (MIK-R112) and are not answered by any text on this page.

The tables below name every registered public tool. Behavior detail lives in the linked skill and reference pages.

**Skills vs tools.** Many skills wrap one or more of these tools and add the
procedure, gates, and ordering around them: the `c-00-initialize-memory-repo` skill drives the `memory_init` MCP tool; the [curation operation](../../skills/l-01-agent-lifecycles/operations/curation.md)
uses drift reporting (`context_packet` / `memory_quality_check`), and the [authorized lifecycle operation](../../skills/l-01-agent-lifecycles/operations/closeout.md) coordinates the `worktree_*`
and `*_closeout_*` MCP tools. When the docs say "run a core skill,"
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
| `retire_child` | Retire the agent in one seat, by the agent that started it, without giving it a turn; the launch record names the caller and the reason. | `task_document_ref`, `role`, optional `reason`, `agent_id`, `interrupt_turn=false` |

Structural messaging, seat rename, lifecycle signals, and gates left the public
surface (MIK-R112), and the earlier start tool was removed with the product's own hosting of
chats (MIK-R113): `dispatch_agent`, `message_parent`, `message_child`, `rename_child`,
`rename_self`, `lifecycle_start`, `lifecycle_resume`, `lifecycle_turn_end_notification`,
`lifecycle_end`, `switch_lifecycle`, `lifecycle_phase`, `lifecycle_gate`, `gate_decide`,
`gate_list`. Role agents are started with `role_start` / `role_start_set` and messaged with
`role_message` (by agent id or by role plus task references). `role_start` and `role_message`
stay public; see Role agents below for their rows.

`role_start` answers `ok`, the agent id, and the handover location; the rest rides in the
detail file. Only a role agent that AR launched can call it, and only roles its rule allows
under its own sprint or master. A chat without a role (a tool server no role agent was
started for) starts through the same table as the dashboard's start instead: every role with
its canonical selection, no replacement, no parent; it cannot use `role_message`. A
`request_id` reconciles the same agent, never a second: only
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

### Message files and receipts

Messages are filed at the narrowest shared task. Same-leaf messages use that task's
`notes/messages/<leaf-slug>/`; messages whose narrowest shared task is the master or sprint use
its `notes/messages/`. With no shared task, the sender's role execution record owns the message
folder under `<coordinationRoot>/notes/reports/paseo-native-executions/<sender-role>/messages/`.
An enclosure's `messages` link is a view of its message folder, not a separate filing authority.
The response's `messageFile` and `messageFileSha256` identify the actual filed bytes, including a
refused delivery's `.undelivered.md` file or a wait reply cut at the message limit. A filed message
and a tool receipt show what was attempted and returned; they do not show semantic acceptance.

The role-message response keeps its own queue fields: `status: held`, `heldReason`, `detail`,
`permission` (the host's name or null), `heldMessageId`, `waitingMine` and `waitingTotal`.
`limitedUntil` is present for a provider-limit hold when known. `withdraw_held_message_id`
withdraws the exact waiting message; `withdrawnHeldMessageId` reports the withdrawal. These are
the role-message fields, not field aliases for another message surface. The full sender procedure
for held and full-store responses is
[Message handover](../../skills/l-01-agent-lifecycles/reference/message-handover.md).

Before claiming no send, search the complete message folder for the narrowest shared task and the
applicable tool-call receipts and detail files. An active enclosure's receipts are under its
`logs/`; otherwise use the configured `transcriptRoot`, whose default is
`<coordinationRoot>/logs/mcp`. Terminal lifecycle calls use that configured transcript root.
See [MCP fields](settings-json.md#mcp-fields) for receipt scope and retention. A missing file in one
assumed default folder is not evidence that no message was sent.

## Install & scaffolding

| Tool | Purpose | Key args |
| --- | --- | --- |
| `runtime_install` | Reconcile package-owned runtime (AGENTS.md templates, docs and runtime folders) into the coordinator; optionally install benchmark fixtures. The `host` part reconciles the configured build-pinned host/plugin and reports installation and restart outcomes; configuration authority is not rewritten. Missing block is named; dry run has no host mutations. **Preserves** user data (`memory-repos/`, `providers/data/`, `providers/runners/`) and **replaces** managed documentation (`docs/`). Skills are served as resources from the package; installation creates or replaces no coordination-root skill tree. No provider install/start/watch path remains (MIK-R112): the install stops leftover watcher processes as its only watcher action and reports enabled provider keys once as unused (`unusedProviderKeys`); `install_provider_deps` no longer drives installation. Leftover provider runtime folders are reported with sizes; only the `providers-remove` command deletes them. `experiment` is a per-call input (never a setting) selecting an experimental cutover for one run; its source is named in the record. | `dry_run=false`, `include_benchmarks=false`, `install_provider_deps=true`, `no_cache=false` |

## Memory & onboarding

| Tool | Purpose | Key args |
| --- | --- | --- |
| `memory_init` | Initialize (or repair) a repository's memory root. | `repo_id`, `dry_run=false`, `initialize_git=true` |
| `memory_quality_check` | Closeout memory-quality checks; the full logged result includes `knowledgeWorklist` and exact-tree `knowledgeGate` blocks for converted leaves. A full contract-scoped call atomically replaces `<worktree enclosure>/reports/curator-memory-quality.md`, combining quality findings, current-addition coverage, route-index preview, drift candidates and report-only evidence. The persisted change-to-knowledge worklist is a separate artifact. | `request={"mode":"sync", "repo_id":..., "checks":..., "detail_limit":50, "contract_path":...}`; use `mode:"start"` for async admission and poll only with `mode:"poll"`, `repo_id`, `run_id` |
| `citation_fix` | Regenerate anchored citation ranges in an admitted memory scope; preview synchronously or in the shared background run registry. Ambiguous anchors, renames and deletions remain curator work. | `repo_id`, `contract_path=None`, `document=None` or `documents=None`, `dry_run=false`, `mode="sync"`; preview with `mode="start", dry_run=true`, then `mode="poll", run_id=...` with the same repository/contract and no selectors |
| `citation_note_revision` | Revise one converted sidecar reference's note in a leaf memory worktree, preserving targets, anchors and other references. Refuse a stale sidecar digest, missing key or empty note. | `repo_id`, `contract_path`, `card`, reference number `reference`, `note`, `base_digest`, `dry_run=false` |
| `route_index_refresh` | Regenerate `overview.index.json` route indexes to match the onboarding tree. **Writes** into the memory root it resolves. | `repo_id`, `dry_run=false`, `contract_path=None` |
| `knowledge_read` | Name the records of one memory-tree view (`source_context`, `invariant`, `family`, `record`, `weakness`, `dependent`): each row holds the text ID, kind, state and memory-checkout location. No record text: open the named files with your own tools. | `memoryRoot`, `view`, `limit=32`, `continuation`, view seeds |
| `knowledge_diff` | Name the changed records of two memory trees beside a caller-readable report holding the whole comparison: the two trees, counts by kind, changed records, report location. | `memoryRoot`, `beforeRevision="HEAD"`, `afterRevision`, `recordId`, `path` |

`memory_quality_check.request` is exactly one shape. `sync` and `start` accept repository,
scope, checks, and detail fields; `poll` accepts only `mode`, `repo_id`, and `run_id`. Do not resend
start fields while polling, even when their values equal defaults: the discriminated boundary
rejects contradictory presence before it reads the run registry. A unique start at capacity returns
`capacity-reached` without a `runId`; poll or wait for active work, then submit a new start request.

`memory_quality_check` carries an optional leaf enclosure `contract_path` inside
its discriminated sync/start `request`; `citation_fix` and `route_index_refresh`
take it directly. With it, they act on the leaf memory worktree against that
leaf's code. A wrong repository or absent worktree refuses rather than selecting
the official root. The response identifies the measured `onboardingRoot`.

An unscoped quality check or route-index preview reads the configured official
memory scope. Unscoped citation repair and route-index writes have only the narrow
pre-baseline admission described below: a configured converted root, no adopted
baseline and no linked memory worktree. `citation_note_revision` requires a leaf
contract. Use the leaf contract for candidate work.

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

Use the primary `citation_fix` route for matching content at stale same-path citation anchors. A correct-input
refusal follows the delivered Curator recovery procedure only for that refusing
tool and input class; permission decisions and product guards remain stops. MCP
`citation_fix(repo_id=<id>,
contract_path=<enclosure contract>, dry_run=true|false)` authors supplied card citation tables
and re-records blob/range anchors whose content still matches at the same target path.
It does not relocate a symbol into another file. Cross-file moves, renamed or deleted targets
and stale or ambiguous references remain Curator reconciliation work. `contract_path` is
the ordinary leaf write route; a contract naming the official memory repo is refused.
The only unscoped admission is the configured converted memory root before baseline
adoption, with no linked memory worktree; the repository and resolved root must
match its settings. Other unscoped calls refuse and name the leaf route. The checkout-only CLI remains a development adapter to the same guarded
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
| `worktree_status` | Report strict task-addressed lifecycle status without queue input. A live locator resolves to the root manifest/journal and its executable controls; a terminal locator resolves to the exact external archive/receipt plus surviving contract truth, distinguishes archive-ready from cleanup-completed, and returns the archived `cleanupArguments` with exact retry `nextArgs`. | `repo_id`, `task_name` / `contract_path` |
| `worktree_closeout_preview` | Non-mutating preview of a worktree-backed closeout. On converted memory, the invariant gate judges the exact candidate trees; `knowledge-gate-refused` carries its findings. | `contract_path`, code/memory commit messages |
| `worktree_closeout_apply` | On converted memory, judge the exact candidate trees with the invariant gate; `knowledge-gate-refused` carries its findings. Validate every enabled explicit nonblank input, claim the exact first-ready door generation through a short CAS, then start or observe its durable task-bound closeout and return promptly. Poll `worktree_status`; no operation ID is exposed. Agents Remember source commits run the leaf change-set-scoped quality contract (`--targeted`) before Git commit — the full wrapper runs once per master at the master integration gate. | `contract_path`, `intent_note`, explicit message for each enabled commit leg |
| `worktree_integrate` | Start or observe durable task-bound landing (`ff-only` or `replay`) and return promptly. Poll `worktree_status`; retries with conflicting input refuse. | `contract_path`, `strategy`, `dry_run=false` |
| `worktree_sync` | Pull the moved official line into a live worktree: fetch, merge code, fast-forward or merge memory under the admitted sync plan, and advance the recorded base pair. Converted knowledge merges structurally and is validated; conflicts retain the advertised `continue` / `cancel` route. | `contract_path`, `memory_sync_choice`, `resolution_action`, `dry_run=false` |
| `worktree_pause` | Pause an atomic master and hand control back to the developer; publishes nothing. | `contract_path` |
| `worktree_checkpoint_landing` | Land an unfinished atomic master's accumulated line into its super branch and keep the master open; requires developer approval. | `contract_path`, `strategy`, `dry_run=false` |
| `worktree_record_landing` | Record a commit the landing target already holds. | `contract_path`, `landed_code_commit`, optional `landed_memory_content_commit`, `dry_run=false` |
| `worktree_operation_control` | Execute one currently advertised task-addressed control for an exact closeout/integrate/direct-landing generation. Retry/recover preserve accepted input; cancellation proves worker exit and Git safety; revise/retire/supersede are evidence-aware. | `contract_path`, `operation_kind`, `action`, `expected_generation`, nonblank `intent_note`, action-specific inputs, `dry_run=false` |
| `direct_landing` | Grant-gated branch-addressed landing for a named granted leaf, without a leaf worktree enclosure. Verifies the series code commit at the branch HEAD, or creates it from the leaf's uncommitted changes with `code_commit_message`; persists a durable direct-landing generation before Git mutation and recovers it through `worktree_operation_control`. | `contract_path`, `leaf_task_document_ref`, optional `code_commit` (verify) or `code_commit_message` (create), explicit enabled-leg messages, `intent_note`, optional gated `candidate_tree`, `dry_run=false` |
| `direct_execution_grant` | Give, take back, or reclaim direct-execution grants before work starts, as an architect. The developer gives the same record with the dashboard control; `task_doc` has no operation that gives, changes, or removes a grant. | `task_document_ref`, `action` (`give`, `revoke`, or `reclaim` — reclaim takes back only abandoned leaves' grants with clean checkouts and reports what was preserved and why; started leaves keep their grants under rule 5) |
| `worktree_cleanup` | After integration, archive/read back canonical lifecycle evidence, publish an external terminal receipt, then remove worktrees, merged task branches, reports, and the enclosure root. Archive-ready is not cleanup-completed; retry this exact public call with its archived `teardown_providers` value until surviving contract truth records completion. Bounded pre-locator exception (MIK-R82 rule 5): with no locator, no worktree folder, and no work branches, the call finishes with nothing to archive/delete, records the why in the surviving contract, and reads it back. This is non-terminal for task documents. | `contract_path`, `dry_run=false`, `teardown_providers=true` |
| `worktree_abandon` | Abandon an unintegrated generation through exact contract/journal/Git authority and preserve terminal archive proof before deletion. Archive-ready but incomplete abandon retries this exact public call with its archived `force` value until surviving contract truth records abandonment. Bounded pre-locator exception (MIK-R82 rule 5): with no locator, no worktree folder, and no work branches, the call finishes with nothing to archive/delete, records the why in the surviving contract, and reads it back. | `contract_path`, `dry_run=false`, `force=false` |
| `lifecycle_finalize_task` | Prove the landed edge, resolve the exact contract-bound leaf, refuse before cleanup unless every parent/nested step is done, then complete that leaf and, when it declares an existing immediate parent (or names none and its folder's `task.json` master lists it), automatically derive and reconcile that exact row; a sub-task naming none whose folder `task.json` is not a master is refused. The parent document's own task status and higher ancestors are not completed. | `contract_path`; optional `task_doc_path`, `master_doc_path`, and `subtask_number` are independent identity assertions; `dry_run=false` |

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
| `closeout_queue` | Inspect or rebuild one sprint's disposable waiting-only projection. It is either exact-current `valid-built` or non-admitting `invalid-empty`; rebuild derives solely from current task plus waiting-door facts. | request with `action:"status"|"rebuild"`, `sprint_task_document_ref`, optional declared `caller` |

The queue has no declare, select, claim, defer, withdraw, lifecycle, commit, certification,
recovery, replan, drain, or stale-row transition. Claim transfers the current generation from its
door into the enclosure-root operation journal. Queue invalidation or absence cannot destroy or
strand accepted operation evidence.

## The review page's explainer

| Tool | Purpose | Key args |
| --- | --- | --- |
| `explainer_control` | Update or retire one leaf's explainer, or claim a master as its architect. `retire` archives the explainer's chat in Paseo and removes its workspace completely -- Git worktrees removed and pruned, the leaf's folder deleted -- for a seat in any state (live, closed, archived, missing); the task's published revisions, grades and receipts stay. `claim` is open to any hosted architect seat, writes the master-to-architect record and nothing else. `update` and `retire` are accepted only from the seat that record names; any other caller is refused `not-this-masters-architect` and claims first. A refused action is a typed answer, never a server error. | `action` (`update`, `retire`, `claim`), `repo_id`, `task_name`, `leaf_id` for the two leaf actions, `receipt_id` for a `retire` |

The explainer launch also adds four bound tools to that seat's normal
`agents-remember-task` server instance. They are available to the bound explainer,
not advertised as general tools to every role. The launch supplies the workspace,
task root and seat; grade calls carry that seat's current binding and block
fingerprints. There is no separate explainer server or caller-selected workspace.

| Bound explainer tool | Purpose | Key args |
| --- | --- | --- |
| `render` | Check the draft in Chromium and publish the next task revision. Write admitted claims to `drafts/assets/claim-map.json` first. Answers publication state, revision and run-file locations; open images from those files. | none; uses the launch-bound workspace |
| `define_category` | Define a review-block category with 2–5 grades, highest first; a conflicting existing scale/meaning refuses. | `name`, `scale`, `binding`, optional `meaning` |
| `remove_category` | Remove a category and its grades in the bound environment. | `name`, `binding` |
| `grade` | Grade named review blocks with a reason; stale binding refuses and long refusal lists move to a named file. | `references`, `category`, `grade`, `reason`, `binding` |

The developer's own presses do not pass through this tool: the page's Update and Retire call the
dashboard's routes, and master retirement and sprint archive retire every environment through their
hook. A leaf's environment is built at the developer's first request, never by opening the page, and
none of these calls starts a model turn.

## Task documents

| Tool | Purpose | Key args |
| --- | --- | --- |
| `task_doc` | Author JSON-primary task documents and deterministic markdown. Every intrinsically valid mutation publishes during every queue/operation phase and returns per-sprint `projectionEffects`; an incomplete effect carries its exact rebuild `nextAction`. `skip_step` requires a nonblank reason; `discard-unstarted` removes only centrally proven never-started planning work without completion fiction; `Completed` refuses unresolved units. | `repo_id`, task/contract target, `operation`, edit payload, `dry_run=false` |
| `task_reopen` | Reopen a fully landed leaf under the same leaf id so new work can be declared explicitly. | `contract_path`, `dry_run=false` |
### task_doc operations

`task_doc` authors the JSON-primary task document (`ar-task-document/v1`); `task.md` /
`<slug>.md` is generated and never parsed back. Authoring operations write task documents.
`linkage_report` leaves them unchanged; a non-dry call writes a separate report artifact,
while its dry run writes nothing. `get` and
`read_steps` are removed operations (calling one is refused with the rendered `.md` and
JSON file names to read instead). Locate the doc by `task_name` (also resolves the
contract for the lifecycle key) or `contract_path`; pass `slug` for a series sub-task
(`<slug>.json`), omit for a master (`task.json`). `dry_run=true` builds,
validates and returns diff/wouldLose without writing. A write answers `ok` and
the changed unit in a phrase (`renderedPath` rides `create`, `reviewState` rides reviews);
the rendered Markdown and per-sprint `projectionEffects` ride the whole result in the log
file. `linkage_report`
answers the fact counts with the report location. Long `fields`/`section_body` may ride
as files (`fields_file`, `section_body_file`). A master-sync failure answers `ok:false`
with `master-sync-failed` and leaves the leaf unchanged.

A successful leaf `create`, or a `set_field`/`replace` that sets or replaces the
leaf's `requirements`, adds `alignmentSweep` when its master has other open
leaves. Dry runs include it too. The field is advice, not a gate: it refuses
nothing and changes no document bytes. Refusals and masters with no other open
leaf omit it. Siblings come from the master's rows, excluding resolved or retired
rows and the edited leaf.

At most 12 declared siblings are listed as `openLeaves`, each with `id` and
`state`. A present sibling has `state: "available"` and `requirements` identities
such as `MIK-R62@v12`; `requirements: []` means present with no requirement
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
(operations/planning.md)."

- `create`: new doc from `fields` (`id`, `slug`, `title`, `kind` `subTask`|`master` — the old
`light` kind is refused — `repo`, `type`, `createdAt`, `objective`, `requirements`, `steps`,
...). A master takes `subTasks` plus ordered sections instead of steps; an orchestration
sprint is scaffolded with empty canonical Judgment and Priority Register sections.
- `replace`: whole-document rewrite from `fields` after schema validation.
- `get` and `read_steps` are removed: calling one is refused with the file names
to read instead. `set_status`: set `fields.status`. `set_field`: scalar/list
field updates. `append_decision`: append `decision={at, decision, rationale}`.
- `put_open_question` takes `fields={key, text}`: append or replace the question starting
with the nonempty key within the task owner's publication; `text` starts with that key.
Alternatively, `fields={questions: [{key, text}, ...]}` validates every entry before one
publication of the whole keyed list; the single-question form remains supported.
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
`linkage_report` reports drift without changing task documents (seat-doc rows, slug-only
membership, mismatches, uncommanded masters); non-dry calls write a separate report artifact.
- `Completed` refuses while any declared step/substep or master row stays unresolved.

See [Worktrees And Closeout](worktrees-c09.md) for the lifecycle and gates.

## Knowledge operations

| Tool | Purpose | Key args |
| --- | --- | --- |
| `knowledge_read` | Name the records of one memory-tree view (`source_context`, `invariant`, `family`, `record`, `weakness`, `dependent`) at one snapshot, paged; each row holds the text ID, kind, state and memory-checkout location. No record text: open the named files with your own tools. | `memoryRoot`, `view`, `limit=32`, `continuation`, view seeds |
| `knowledge_diff` | Name the changed records of two memory trees beside a caller-readable report holding the whole comparison. | `memoryRoot`, `beforeRevision="HEAD"`, `afterRevision`, `recordId`, `path` |
| `knowledge_integrity_check` | Validate converted memory: `validation.ok`, refusal/report counts, rule counts and bounded violations. With `contractPath`, use the leaf code/memory worktrees and return its latest persisted worklist; no semantic judgment is inferred. | `memoryRoot`, `codeRoot`, `baseCommits`, or leaf `contractPath` |
| `knowledge_write` | Plan or write a Curator hand-off through the admitted writer. Without `commit` it plans and validates; with it, writes knowledge files and makes no Git commit. The answer names the whole report and all refusals. | `contractPath`, `handoff` + `handoffName` or `handoffPath`, `authorizationRef`, `commit=false`; foundation `repoId` + `wave`, or master `crossing` |
| `curator_coherence` | Author, publish, inspect or validate one leaf's curator-coherence authority (`prepare` → `publish` → `validate`). | one action's request shape at a time |

See [Knowledge system](knowledge-system.md) for record/sidecar/history ownership,
worklist and gate fields, validator, derived index, conversion/crossing and the
reader/review surfaces. The [knowledge command
reference](../../skills/l-01-agent-lifecycles/reference/knowledge/commands.md)
lists command callers, timing and examples.

## Role agents

| Tool | Purpose | Key args |
| --- | --- | --- |
| `role_start` | Start one role agent for one canonical selection, caller as parent; repeating `request_id` reconciles the same agent. | `role`, `request_id`, the role's task references, `agent` / `model` / `effort` |
| `role_start_set` | Existing Manager-only leaf set API: start Worker and hold Reviewer/Curator, unless `reviewer_message` opens Reviewer. Repeating `request_id` reconciles the same set and opens no held seat. Its availability does not prescribe the lifecycle's stage-start order. | `request_id`, canonical task references, optional `reviewer_message`, per-seat `worker` / `reviewer` / `curator` launch choices, `replaces` |
| `user_settings_init` | Preview/create role-settings with one harness or per-role harness choices; read existing choices without overwriting. `host-authority` is refused because host preparation owns it. | `target="role-settings"`, `harness` or `roles`, `dry_run=true` first |
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

The lifecycle signal tools left the surface (MIK-R112), and the session lifecycle behind them
was removed (MIK-R113); they are named under Structural role control above. `worktree_start` and
`worktree_attach` adopt the lifecycle of a worktree contract; nothing else begins one.

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
`mcp/src/agents_remember/models/` via `tool_registry.TOOL_RESPONSE_MODELS`.
