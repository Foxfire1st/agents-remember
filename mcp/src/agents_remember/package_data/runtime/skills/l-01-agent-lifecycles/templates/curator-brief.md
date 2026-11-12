# Curator brief

The Manager uses this template to supply a leaf's Curator with the frozen change set, intent and current source lineage. The Curator reads those inputs instead of inferring them from conversation history.

The Worker sends its frozen change set and handoff list directly. The Reviewer sends the passed freeze and code verdict directly. The leaf task document, existing intent anchors, approved developer and design rulings, the Manager's immediately preceding source-lineage projection, and task notes complete the inputs.

This template supplies values, not new rules. The [Curator role](../roles/curator.md) owns duties, the [curation operation](../operations/curation.md) owns the coherence procedure, and [acceptance](../core/acceptance.md) owns the truth boundary. Those files govern a disagreement with a value in this intake record.

## Intake values to fill

```md
CURATOR INTAKE RECORD

# CURATOR COHERENCE INTAKE — <leaf-id> · <leaf title>

You are the Curator for leaf `<leaf-id>` of master `<master>` in repository `<repo-id>`.
Your compiled first message follows the [Curator role](../roles/curator.md); this record supplies intake values, not a session-start brief.
Reconcile the three intent sources below conservatively and write the leaf's coherence pass.
Then hand the memory change and report directly to the Reviewer.

## Worktrees

- Code: `<code-worktree-path>`, branch `<work-branch>`, base `<base-commit>`. Read-only for you.
- Memory: `<memory-worktree-path>`, branch `<memory-work-branch>`. Your only write surface.
- Enclosure contract: `<enclosure-contract-path>`. Pass it as `contract_path` to every memory tool below so the tool acts on this leaf, not the official memory repository.
- Pre-curator lineage: `<worktree_status sourceLineage projection>`. The Manager captured it immediately before the start. It has `state=current` across every applicable super → master → leaf code and external-memory edge. This is evidence, not caller-supplied commit-identifier authority.

## Frozen change set supplied by the Worker

- Code diff: `<base-commit>..<worker-head-commit-or-HEAD>` in the code worktree. Supply `<changed-path list, or the dashboard change-set view ref (/api/changeset/task scope, or the leaf's committed/working change-set) recorded by the Worker for this exact freeze>`.
- Memory diff, if existing memory-worktree changes carry forward: `<memory-base>..<HEAD>`.
- Counters: `<files changed / insertions / deletions>` from the change set the Worker handed over. Do not infer these from a guess about what changed.

## Task inputs

Before checking the approved revision, read [the approval-record definition](../core/every-role.md#requirements-and-limits) for the approved manifest entry, its content hash and the corpus hash.

- Leaf task document: `<leaf-doc-path>`. Read its objective, requirements and decision log first.
- Requirement approval record: `<durable approval-record citation>`.
- Primary requirement revision: `<stable-id>@<version>`, canonical packet `<packet-path>`.
- Worker delivery attempt: `<attempt-id>`. This candidate-handoff identity differs from the semantic requirement revision and every evidence digest.
- Adjacent preservation and dependency revisions: `<stable-id>@<version> — <packet-path> | none`.
- Requirement adjudication: `<reviewer-verdict-path | none when review was not requested>`. Attach the Reviewer's independent `accepted | rejected` row only when this leaf has an approved review.
- Review mode: `<baseline | fix-verification | none>`.
- Sealed review baseline and predecessor: `<sealed baseline ref> / <immediately preceding result or N/A>` when review was requested.
- Outstanding issues: `<exact IDs and dispositions supplied by the reviewer | none>`. Preserve these judgments when review exists.
- Task notes: `<series-notes-path>`. Include the builder turn report (`notes/reports/<leaf-id>-worker-report.md`), optional route-review verdict and task-local notes that state a factual current-state clarification.
- Producers' Curator handoff lists: `<path or fenced-block location of the builder's list, plus the reviewer's own list when review was requested>`. Attach the lists themselves as data, in the supplied handoff-list format, without paraphrasing.

For an atomic child, requested route-review adjudication waits for the accumulated canonical master integration review. Retain Worker evidence. Treat rejected or Worker-blocked revisions as blockers, never current intent to put into onboarding.

Before checking the supplied producer lists, read [the Curator handoff-list contract](curator-handoff-list.md#the-entry) for producer fields and Curator-owned fields.

## Three-way intent inputs

### Existing system intent

- Governing onboarding and entity paths: `<paths>`.
- Load-bearing tests, incidents and negative knowledge: `<paths or none>`.
- Contracts expected to remain unchanged: `<contracts>`.

### Ruled change intent

- Developer decisions and approved design notes: `<paths + concise rulings>`.
- Requirements authorizing a contract change: `<exact stable-id>@<version> — <canonical packet path>; repeat for each applicable revision`.
- Explicit non-goals: `<non-goals>`.

### Implemented reality

- Supplied code range or diff: `<exact base-to-head or reviewed working-tree evidence>`.
- Builder report: `<path>`.
- Reviewer verdict: `<path or review evidence under developer-chosen direct coordination | none when not requested>`.
- Verification evidence: `<commands/artifacts>`.

## Routing and handoff

Before routing each change-set or task-notes item, read [change routing](../reference/curator/change-routing.md) for changed-file and notes-item routing to records, cards and indexes.

Before handing memory over, read [Curator memory handover](../reference/curator/memory-handover.md) for the exact reviewed trees, lanes and whole requested work.

## Messages

Before composing a message, read [Send a message that names the files](../reference/message-handover.md#send-a-message-that-names-the-files) for the sender and payload rules.

Before handling a full-store refusal, read [full-store receipt handling](../reference/message-handover.md#handle-a-full-store-refusal) for the pending record, one resend and second-refusal report.

```

## Manager's compilation checks

Fill every placeholder before supplying the intake values. Do not send an unresolved placeholder.

Immediately before starting the Curator, call `worktree_status` for the canonical leaf. Require `sourceLineage.state=current`. If it is stale or unavailable, synchronize and reconcile before opening the Curator.

Use the leaf's `series-contract.md` under the master's `enclosures/<leaf-id>/` for `<enclosure-contract-path>`. Before supplying memory-tool scope, read [Authority gates](../operations/curation.md#authority-gates) for the enclosure-contract requirement and unscoped `route_index_refresh` consequence.

Before starting the Curator stage, read [Start leaf stages and deliver the first intake](../operations/coordination.md#start-leaf-stages-and-deliver-the-first-intake) for stage timing and direct producer handovers.

Supply the task, intent and current-lineage anchors.

Name the Worker report and candidate-bound Reviewer verdict paths as task-note inputs supplied directly by those producers. The Curator does not request evidence again when it already exists in `notes/reports/`.

Before recording ordinary code review, read [Record code review rounds](../operations/review.md#record-code-review-rounds) for ordinary leaf begin and result records.

Before checking code and memory lane results, read [the leaf verdict handover procedure](../operations/review.md#receive-the-intake-and-hand-over-the-leaf-verdict) for separate code and memory results, direct handovers and stage-start notices.
