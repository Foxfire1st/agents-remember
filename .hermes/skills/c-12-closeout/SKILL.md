---
name: c-12-closeout
description: "Close out approved Agents Remember edits as code and memory Git transactions with exact pair identity, conflict safety, and no automatic push."
---

# c-12-closeout Closeout

Use this skill when approved Agents Remember edits in an external-memory
worktree need to be committed.

The `c-12-closeout` skill owns closeout sequencing for worktree-backed tasks.
**Closeout is worktree-only:** every change affecting the code repo runs through a
`c-09-git-worktree-manager` dual worktree (code + memory) — there is no
direct-checkout closeout path. Use the `c-09-git-worktree-manager` skill for
worktree start, attach, status, integration, lifecycle finalization, and cleanup;
use this skill for the explicit closeout authority and code-then-memory commit order. Closeout is a
Git transaction; it runs no code-quality checks, test suites, or independent review itself, and
curation is already complete before it starts — the curator's full memory-quality result travels with
the handoff as a prerequisite.

**Handoff note:** the worker reports targeted checks and the curator maintains affected onboarding
before handoff. The closeout seat consumes the prepared code and memory content; it does not author
onboarding or rerun the worker's or curator's checks, because curation already ran the full
memory-quality operation. An independent review may be requested by the
developer or the task's approved plan, but its record is an input for context only and is never an
automatic closeout prerequisite. When a review is requested, the `l-01-agent-lifecycles` skill's
three-round monotonic rule applies: review 1 fixes the complete finding list, reviews 2 and 3 verify
only that list and require a shrinking remaining count, and a fourth round asks the developer.

## MCP Tools

Use the worktree closeout tools against the task contract:

```text
worktree_closeout_preview(contract_path="<enclosure series-contract.md>", code_commit_message="<message>", memory_commit_message="<message>")
worktree_closeout_apply(contract_path="<enclosure series-contract.md>", intent_note="<developer intent>", code_commit_message="<message>", memory_commit_message="<message>")
worktree_status(repo_id="<repo-id>", contract_path="<enclosure series-contract.md>")
worktree_operation_control(contract_path="<enclosure series-contract.md>", operation_kind="closeout", action="cancel|resume", expected_generation=<generation>, intent_note="<audit intent>")
worktree_legacy_operation(contract_path="<enclosure series-contract.md>", operation_kind="closeout", action="inspect|migrate|archive", ...)
direct_landing(contract_path="<task-root series-contract.md>", code_commit="<verified branch HEAD>", memory_commit_message="<message>", intent_note="<authority>", candidate_tree="<gated tree>")
```

Worktree closeout records closeout state in the contract the
`c-09-git-worktree-manager` skill created or attached; the
`c-09-git-worktree-manager` skill owns later integration, lifecycle finalization,
cleanup, and task-document completion.

On converted memory (the memory tree holds `knowledge/layout.json`) both closeout tools ask the
mandatory invariant gate (MIK-R09) about the leaf's exact code and memory candidate:

- `worktree_closeout_preview` answers `state: "knowledge-gate-refused"` instead of
  `"would-closeout"` while a worklist item is open, the worklist run is incomplete or the
  knowledge validator fails. `knowledge_gate.findingCount` and `knowledge_gate.findings` name what
  is open (at most 50 are listed; `truncated` says so). It asks for no commit approval. Answer
  the findings, rerun `memory_quality_check`, and preview again.
- A passing preview carries `knowledge_gate: {"state": "pass"}`.
- `worktree_closeout_apply` refuses the same leaf with the same findings before it claims the
  approval or commits either side, and judges the exact memory tree once more at the memory
  commit. A preview in the same server shortly before the apply saves the apply's first
  evaluation.
- The memory commit records exactly the tree the gate judged. A file written to the memory
  worktree while the closeout runs is either refused ("changed while the gate ran"; rerun) or
  left as an uncommitted change. It is never committed unjudged.

## Approval Authority

Closeout is always authority-gated, but the authority is contextual.

For standalone work, final super-branch landing, or any closeout where the accepted task/series
authority is unclear, agents must request the matching preview tool first, relay the proposed code
and memory commit messages to the developer, and ask for explicit commit approval.

For subordinate work inside an accepted orchestrated series, the owning seat may apply closeout
under delegated series authority after the transaction preview is coherent. Managers govern leaf commits;
the orchestrator governs manager/master edges and direct flat work when it is wearing the manager
or worker hat itself. Do not stop for the developer merely because closeout will create code
and memory commits. The `intent_note` records the authority source, e.g. the accepted
planner/series task and the owning seat's review of the preview.

Closeout still stops for the developer when the work reaches the final completed super branch /
PR-carryover gate, when a `closeout-approval` gate has been deliberately raised, when the change is
outside the accepted scope, when Git inputs/conflicts prevent the transaction, or when a quo-vadis
decision is required. A curator-actionable finding that was neither repaired nor escalated as blocked
is not closable; worker targeted-check failures remain reported evidence for the owning role rather
than an automatic closeout quality gate.

Real closeout uses the matching apply tool with an `intent_note`. The note records the applicable
authority: either explicit developer commit approval or delegated accepted-series authority. Agents
must not treat a vague "looks good" or their own preference as authority.

Every contract-enabled code or memory leg also requires its own explicit nonblank commit
message. Preview and apply normalize the same effective input before any claim, worker, journal, or
Git authority is acquired; a blank required cell is a typed no-effect refusal, not an immutable
half-operation. A typed not-applicable leg omits its message instead of receiving a synthesized
default.

Approval remains outside and before apply: preview, relay, and the applicable explicit or delegated
authority must be complete before `worktree_closeout_apply`. Apply validates the immutable
transaction input, stages and commits the enabled code and memory-content legs through the
existing transaction owner, and records each resulting commit. It does not run or demand a
certification profile, code-quality check, test suite, or review record — curation is carried, never
invoked — and it does not re-derive the curator's full memory-quality result, which is already
complete before apply: the coherence authority the curator produced when the checklist required it
is validated at admission, so a record that is missing or stale refuses there with `publish` as the
remedy rather than being produced here. Full code-quality or full-suite tools run only when the developer explicitly requests
them through their existing tools; their absence does not block this transaction.

The transaction owner may use only the existing bounded retry/recovery behavior. It must preserve
the accepted source/destination identities, refuse moved refs or unresolved Git conflicts before a
ref move, and publish recoverable journal evidence for every completed leg. A worker or curator
check result may be reported alongside the preview, but it is diagnostic evidence rather than a
closeout gate.

Staging is **not** undone if the gate refuses. The worktree stays fully staged, nothing is
committed, and that is the intended end state rather than a gap: the checkout being staged is the
task's own worktree, created by `worktree_start` and destroyed by `lifecycle_finalize_task`, so no
one is holding a partial staging in it — and a retry does not inherit that index, because each gate
run begins with `git reset` and restages from the working tree. The reset is what makes the retry
equivalent to a first run rather than an assertion that it is. `git add -A` on its own is not
enough: git applies ignore rules only to paths it does not already track or hold staged, so a file
staged by a refused attempt stays staged even after the retry adds it to `.gitignore`, and the
commit carries it. Resetting first recomputes what gets staged on every run under the ignore rules
in force at that moment, and `--mixed` is index-only, so no file content is touched.

Two refusals guard the transaction before staging or ref movement. Closeout refuses when the code
checkout is **not** the declared task worktree (unless the declared transaction route is a sanctioned
branch-direct landing) or when code or memory content has unresolved merge conflicts. A missing
quality profile, review record, or suite result never creates a compatibility route and never blocks
an otherwise authorized transaction. Curation is not a third such refusal for the same reason it is
not a route: it is mechanical. The coherence authority the curator produces when the checklist
requires it is validated at admission, so a missing or stale record refuses there with `publish` as
its remedy — and nothing here runs the operation. Older tasks without code changes remain valid and
do not need a profile merely to be read or resumed.

For a developer-gated closeout, the relay follows the `l-01-agent-lifecycles` orchestrator hand-off protocol: run the
preview/dry-run first, then call
`lifecycle_turn_end_notification(summary={…the preview facts + the commit ask…})` as the **last tool
call**, then deliver the preview facts and proposed messages as plain
chat output ending with the commit ask, and **STOP / end your
turn**. The notification sets the `awaiting-developer` lifecycle state, surfaces a dashboard attention item, and
returns immediately (no wait, no inbox). The developer approves on the dashboard or in the leaf's
attached chat; the **first AR tool call of your next turn** auto-resumes the lifecycle (`running`),
clears the attention item, and runs `worktree_closeout_apply` — you send no explicit `lifecycle_resume`.
Never invoke `worktree_closeout_apply` in the same turn as the relay; the preview report is what the
developer sees.

Apply returns promptly after starting or observing the task-bound closeout generation. Use
`worktree_status` against the configured contract to read the current journal projection. Do not
wait on a queue row or retain an operation id. If the journal advertises `cancel`, it stops the
worker and preserves source changes and historical evidence. If it advertises `resume`, it accepts
the fixed transaction input and continues the unfinished commit leg; it does not reconstruct
certification history or rerun an acceptance suffix. Actual publication keeps its existing
transactional unfinished-leg protection. Execute only the advertised closeout action through
`worktree_operation_control`; never run Git directly, edit journal bytes, or use a queue row as
recovery evidence.

## Explicit Durable Closeout Gates

`closeout-approval` is the sole human commit gate for code and memory when one is
explicitly raised. It gates only admission of the addressed closeout generation; it never freezes
the task document, another sprint, or an already accepted operation. Apply accepts only a current
developer-attributed approval and consumes it once. Open, rejected, revision-requested, applied,
or model-approved gates refuse closeout admission. A model never self-approves a human-pinned gate.

Do not create a durable gate as an incident workaround or compatibility route. Ordinary
subordinate closeouts use recorded accepted-series authority; standalone/final work uses the
developer hand-off above. The closeout preview/apply payload's `closeout_gate` block is evidence of
an explicitly existing gate, not a second lifecycle or recovery mechanism.

## Preconditions

The `c-12-closeout` skill resolves or consumes the current
`c-08-ar-coordination-context-resolver` context. External-memory closeout
requires the code checkout/worktree and memory repo/worktree to be on the same
selected branch. A `disabled`-memory contract has no memory leg to commit.

The accepted code and memory commits, their trees, ancestry, and recorded refs are transaction
authority. Each newly created memory-content commit carries `Code-Commit: <code sha>` in its message.
Unchanged memory retains its actual accepted commit; a missing attribution for a newer code commit
does not require a synthetic memory commit.

`memory.md` remains available to consumers as an ignored, computed cache of memory commit trailers.
Keep it outside staging and commits. Missing, stale, or malformed cache bytes or rows never block
closeout or recovery. Cache regeneration does not rewrite Git history; historical attribution
rewrites require a separately authorized deployment operation.

The transaction consumes the worker's targeted-check report and the curator's
onboarding handoff as context; it does not rerun either one. The curator owns
affected onboarding and runs the complete memory-quality operation before
handoff, so that full result is a closeout precondition rather than a separate
explicitly requested operation.

The transaction does not inspect coding guidelines, certification profiles, or
historical quality artifacts. Those concerns remain available through their
own tools when the developer explicitly requests them. Closeout reports the
actual Git inputs and conflicts, then either performs the authorized
code-and-memory transaction or refuses before mutation.

## Closeout Order

External-memory closeout order is:

1. Confirm the worker's targeted-check report and the curator's complete onboarding
   handoff when those roles are present. Record failed or not-run checks as
   reported; do not reinterpret a subset as full green.
   Carry the curator's knowledge hand-off result (the file writer's report) as handoff context. The
   reviewer compares the leaf's base and candidate from Git, so no separate comparison record exists
   and closeout produces none.
2. Preview the exact enabled code and memory-content commit legs with
   their messages, source refs, destination refs, and current conflict/ref
   facts. Preview performs no quality, test, memory, certification, or review
   invocation.
3. After the applicable explicit or delegated authority is complete, call
   `worktree_closeout_apply`; it validates the same immutable Git input.
4. Commit the code changes, then the prepared memory-content changes with their
   code attribution. Keep the existing transaction owner, pair identity, and
   recoverable publication journal for each leg. Refresh the consumer ledger
   cache from Git history without staging or committing it.
5. Refuse before mutation when a declared branch/ref moved, a worktree has an
   unresolved code or memory-content conflict, a required commit message is blank,
   or the code/memory pair cannot be reconciled. Name the concrete corrective action.
6. Update the task contract closeout state after the actual commit pair is recorded.

Push behavior is not automatic. Closeout commits code and memory only;
it never pushes. Pushing the integration branch is part of the landing tail the
`c-09-git-worktree-manager` skill owns: call
`lifecycle_turn_end_notification(summary=…)` as the **last tool call**, then present the push intent as
your final prose, and **STOP**; push only after the developer approves and
your next turn auto-resumes. A separately raised human-pinned `push-approval` gate, when present,
must be decided by the developer; it is not a closeout recovery route.

Closeout does not mark the task `Completed`. Continue with
`c-09-git-worktree-manager`: after integration, any PR-gated merge/pull, and
memory carryover are done, run `worktree_cleanup(contract_path=..., dry_run=true)`
and then apply `worktree_cleanup(contract_path=...)` under the existing authority.
Clean up every finished worktree enclosure before handing back the task, including
after an authorized manual Git landing. If cleanup refuses, report its concrete
reason and the enclosure still pending cleanup. Then use `lifecycle_finalize_task`
to prove the landed parent-child branch edge, verify cleanup, and update the current
task plus immediate parent row.

## Sanctioned Branch-Direct Landing

`direct_landing` is not a direct-checkout closeout path. It is the policy-gated,
branch-addressed counterpart for a **leaf delivered without its own worktree enclosure**, where a
code commit already exists at the exact series branch HEAD. A series contract is necessary address
authority for this route; it is not by itself evidence that an operation is direct execution.
An atomic master's closeout needs a landed enclosure for each row other than `abandoned`.
An abandoned row is outside every walk of a master's rows. It needs no enclosure, no landing, no
contract and no document: a row with status `abandoned` and an empty file cell blocks nothing, and
a row that has a document keeps it. A label never removes a landed leaf: a row labelled abandoned
whose enclosure records completed integration refuses by name until that contradiction is
reconciled. For an atomic master that is the closeout; for an organizational master, which has no
closeout, it is the moment its status is set to `Completed` through `task_doc`.
Finalization decides the abandoned-but-landed rule again and refuses such a row by name, for
both atomic and organizational masters. Every row must be
`Completed` or `abandoned`. Progress figures leave abandoned rows out of the total and name them
separately, for example "5/5, 1 abandoned".

Finalizing a master never archives it, whether or not a sprint commands it. When a sprint
commands the master, finalization completes its row on the sprint, keeps its task folder, and the
result says the archive was skipped because that sprint commands it, naming the sprint. A sprint
that commands the master without a typed row does not refuse: the one legacy seat row that
correlates with the master is completed, and otherwise the result reports the sprint row as
skipped with the linkage fact. When no sprint commands the master, the folder also stays, and the
result says a master is archived only by `task_doc(operation="retire_master")`.

`retire_master` is the one operation that archives a master. It has one route and writes one
record, and it finds out itself whether a sprint commands the master or has recorded its
retirement. A request sent to the wrong document is refused and names the right one.
- A master that a sprint commands is retired on that sprint: `task_doc(operation="retire_master",
  task_name="<sprint>", fields={"masterRef": {"repository": "<repo>", "path":
  "<master>/task.json"}, "reason": "<reason>"}, dry_run=true)` first. The preview lists the
  membership entry, the graph nodes, every touching edge, the retained retirement row, the archive
  destination and what the cleanup hook would delete. An outgoing edge to a successor that is not
  Completed must be affirmed with its exact predecessor and successor in `fields.removeEdges`. The
  record is one plain row on the sprint; it takes the place of the master's typed row, or of the
  one legacy seat row that correlates with it (and keeps that row's `file` cell, so the seat's
  documents stay reachable), and is added at the end when the sprint holds no row for the master.
- A master that no sprint commands is retired on its own document (`task_name="<master>"`,
  `masterRef` its own `task.json`, no `removeEdges`). No sprint is edited, and the record is
  `notes/reports/master-retirement.json` in the master's folder, which moves with it.
A sprint is not a master: the operation refuses a sprint document, also one whose last master was
retired. Readiness refuses open work of the master's leaves, and an unfinished or unreadable
operation of the master's own enclosure: a leaf worktree directory that exists, a leaf branch
that holds commits its landing line does not reach, and an operation record
that is unfinished or cannot be read. The refusal names each open resource and the action that
removes it: the lifecycle tools, or, where those tools cannot act on the enclosure because it has no
live operation locator, what is done by hand (the Git command that removes a worktree or a branch,
the move that takes an operation record out of the directory it is read from). Carry the named
actions out and repeat the request. The layout or age of a contract, a leaf branch
with nothing unlanded, and the master's own branch and worktree never refuse; the dry run lists
them as `readinessFacts`, and the retirement record keeps them. While any `task.json` of the
repository cannot be opened or parsed, the request is refused and names that file. A sprint's only
graphed master cannot be retired, because a graph cannot be empty, and a master nested inside
another task's folder is refused, because only root task folders can be archived. The dry run also
lists every file a real run writes (`wouldWrite`). Apply the same request with `dry_run=false`. A repeated request with the same reason and
edges resumes from the record wherever an earlier attempt stopped; a request that differs from the
record is refused. A retired row cannot be changed or dropped by any other task-document
operation. Hook failures after the folder is archived are reported as
`retired-with-hook-failures` (`ok=false`); repeat the same request to retry the cleanup only. Every
attempt that deleted something, failed, or found something newly absent keeps its own numbered
receipt (`notes/reports/review-artifact-cleanup.json` and its `.attempt-<n>.json` predecessors),
written before anything is deleted. A dry run after a completed retirement says that nothing would
change. Every answer names the restart that a server started before this build needs before it
reads a sprint that holds a retired row.

Ordinary master/series closeout and the later master-to-parent `worktree_integrate` edge are not
branch-direct leaf delivery and must work while `directExecutionEnabled` is false.
The tool verifies that code commit and gated candidate tree, requires an explicit nonblank memory
message when that leg is enabled, and validates the complete effective input before acquiring
landing authority.

Apply persists a task/contract-addressed `direct-landing` operation generation before memory Git
mutation. The memory commit intent, commit proof, and terminal publication are journaled
independently. After interruption, read the same generation
through `worktree_status` and execute only its advertised action through
`worktree_operation_control(operation_kind="direct-landing", ...)`. Recovery reconciles exact
code/tree/memory evidence and reuses each already produced commit once; the queue is not an
input. A transient landing lock, synthesized subject, repeat-from-scratch, or raw Git is not
recovery.

A generation stays in flight, with a recovery action, only when its memory commit is, or may be, on
the branch. A landing that is refused or fails before that leaves nothing behind: the memory branch
moved, `HEAD` was switched, a merge is unfinished, the commit object could not be written, or the
memory checkout changed after it was judged. The generation is then cancelled in the same call, the
leaf's history file and the ignore rule are restored, and the index is given back (unless it holds
someone else's staged edit). The refusal names `direct_landing` as the next action: repeat the same
call once the cause is gone, any number of times; each repeat judges the memory checkout as it is
then.

Hook behavior belongs to the selected publication route. The ordinary closeout and direct-landing
commits written through `publish_tree_commit` use `commit-tree`, which runs no `pre-commit`,
`commit-msg`, `prepare-commit-msg` or `post-commit` hook. The prepared closeout retains its declared
code and memory policies: its code commit uses `--no-verify`, which skips `pre-commit` and
`commit-msg` but still runs `prepare-commit-msg` and `post-commit`; its ordinary memory commit runs
all four hooks. The mandatory invariant gate and knowledge validator remain the publication checks.
A `reference-transaction` hook still runs when a branch moves through a Git ref transaction.

## Explicit Legacy Operation Repair

Normal lifecycle readers accept only the current schema. For an exact historical schema-1 record,
use `worktree_legacy_operation(action="inspect")`, bind the returned digest, and then choose the
single supported audited transition: `migrate` fills only proven-missing unfinished commit
message cells for the known blank-input incident and preserves live code-output evidence in one
canonical generation; `archive` accepts only proven terminal/no-live-authority evidence. Canonical
`worktree_operation_control` then resumes the migrated generation. The legacy tool is explicit and
bounded; it is never called by status, normal journal reads, cleanup, or closeout apply.

## Failure Conditions

Closeout refuses before mutation when external memory is unresolved, the
declared code and memory checkouts do not identify the same transaction, a
required commit message is blank, a declared
source/destination ref moved, or a Git index contains unresolved code or memory-content conflicts.
The refusal names the exact input and corrective action. Missing certification
profiles, code-quality results, review records, and full-test results are not
refusal reasons. Curation is not one of them, and it is not an exemption either: the curator's
complete memory-quality result and the coherence authority it produced travel with the handoff this
transaction records, and admission validates that authority, so a record that is missing or stale
refuses there with `publish` as its remedy while this transaction never runs the operation.

The code worktree must be the contract's task worktree unless the declared
route is the sanctioned branch-direct landing. Refuse before staging when it
is a normal checkout or when a merge, rebase, cherry-pick, or revert has
unmerged entries. This preserves Git's conflict state instead of allowing a
blind stage to commit conflict markers. Existing operation journals keep
recoverable per-leg evidence; after interruption, resume only the advertised
transaction action.

Every enabled blank/whitespace commit-message cell fails before operation authority and leaves no
claim, journal generation, worker, commit, or queue-lifecycle residue. A moved source or stale door
provenance refuses only the landing edge and returns the exact sync/republish route; it never locks
task authoring. Once a generation has been accepted, failures are classified from its journal and
live Git evidence and expose only evidence-safe task-addressed controls.

Worker and curator reports may identify missing onboarding or failed/not-run
checks. Route those reports to the owning role for repair or developer
direction; closeout does not rerun the memory-quality operation or certify the
handoff, because curation already ran it completely and its result is the
handoff evidence this transaction records.

## Boundaries

1. The `c-12-closeout` skill owns closeout approval and code-then-memory commit sequencing.
2. The `c-12-closeout` skill does not create worktrees, integrate worktrees, finalize lifecycles, or clean up worktrees.
3. The `c-12-closeout` skill does not initialize memory roots; use the `c-00-initialize-memory-repo` skill.
4. The `c-12-closeout` skill must not commit without the applicable authority after a closeout
   preview: explicit developer commit approval for standalone/final work, or recorded delegated
   series authority for subordinate accepted-series work.
5. The `c-12-closeout` skill must publish only the explicitly enabled code and
   memory-content legs through their existing transaction owners.
6. The `c-12-closeout` skill must not invoke or require code-quality checks,
   test suites, or independent review as a closeout prerequisite. Curation is
   the exception: the curator's full memory-quality result is complete before
   closeout starts and is carried as that prerequisite, while full code quality
   and full tests run only after an explicit developer request through their
   existing tools.
7. The `c-12-closeout` skill must preserve the accepted code/memory pair,
   source/destination identity, commit messages, and recoverable per-leg journal
   evidence.
8. The `c-12-closeout` skill must refuse moved refs, unresolved conflicts,
   missing inputs, and ambiguous partial publication before unsafe ref movement.
9. The `c-12-closeout` skill must not push automatically.
10. The `c-12-closeout` skill must report worker/curator check failures or
    not-run checks without converting a subset into a full-green claim.
11. The `c-12-closeout` skill must not reconstruct historical certification or
    review state as a compatibility route.
12. The `c-12-closeout` skill must not acquire closeout authority until every enabled immutable
    input cell and the exact current door generation have validated.
13. The `c-12-closeout` skill must not resume from a queue row, direct Git, a reports file, a
    permanent compatibility reader, or synthesized commit-message input.
