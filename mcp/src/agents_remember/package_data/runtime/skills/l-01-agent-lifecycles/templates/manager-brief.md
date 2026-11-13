# Template — Manager Brief

The Orchestrator or Architect uses this template to prepare a Manager’s master documents and start inputs. The Manager uses its exit and reporting sections for handover. The developer can inspect the inputs before the start.

In this template, ID means identifier.

Start through `role_start` with role `manager` and the master reference; name its sprint when a sprint commands it. The call carries no brief: the Manager's first message is compiled from the documents.

The [Manager role](../roles/manager.md) owns its duties. [Coordination](../operations/coordination.md) and [closeout](../operations/closeout.md) own their procedures, and [acceptance](../core/acceptance.md) owns the completion boundary.

This template supplies this master's values; it does not author rules. If a value disagrees with those files, they govern.

---

```md
MANAGER PREPARATION RECORD

# MANAGER PREPARATION — <master> · <master title>

Manager assignment: master `<master>` (repo: <repo-id>). The Manager’s compiled first message
comes from the canonical documents, not this record. Prepare the inputs for its leaf loop
and master-exit handover under the [Manager role](../roles/manager.md).

## The master
- Task doc: `<master task.json/md path>`. Before reading the assignment, read [the Manager’s assignment rule](../roles/manager.md#read-the-assignment-and-keep-its-scope) for the decision-bounded document scope.
- Planner master: <path or n/a (flat run)>
- Execution nature: <organizational | atomic> (exact value from the master task document).
- Sprint graph ref / derived wave: <canonical sprint document> / <wave and predecessor refs>.
- Plan priority grade: <critical | high | normal | low> · rationale/evidence <refs>.
- Leaves: <L0 …, with status and local dependency notes>; build order is not portfolio landing
  authority.
- Trust facts (compiled by the orchestrator — do not re-run the checkpoint): providers <state /
  stack key or NONE>, drift <count>, freshness <state>.

## The source edge owned by the lifecycle tools

- Execution nature and current structural edge: `<organizational | atomic; canonical admission evidence>`.
- Source-move result, when applicable: `<start refusal, contract-addressed recovery and current status>`.

Before interpreting execution nature, read [Connect task, branch and worktree](../core/invariants.md#connect-task-branch-and-worktree) for the applicable branch structure.
Before recovering a moved-source start, read [Recovery](../operations/recovery.md#recover-after-synchronization) for the contract-addressed synchronization route.

## Optional review phase packet
- Review mode: `<baseline | fix-verification | none>` for the current review purpose.
- Baseline agreed scope: `<complete master/leaf scope, required routes, lenses, and standing
  criteria>`.
- Sealed baseline: `<sealed baseline verdict/ref | none when review is not requested>`.
- Immediately preceding result: `<prior successor verdict/result ref | N/A for baseline>`.
- Exact outstanding IDs: `<prior remaining IDs and digest | none for baseline>`.
- Successor worker scope: `<fixes/evidence for listed IDs only>`.

## Start defaults

### Check dependencies

Before starting or integrating a leaf, read [the Manager’s dependency check](../roles/manager.md#start-leaf-agents-and-preserve-their-work) for prerequisite packets and leaf-stage start authority.

Before handling a landing-gate refusal, read [Authorized Closeout](../operations/closeout.md#repair-a-gate-refusal-through-its-owner) for the gate-finding route.

### Start each stage

Before starting a leaf stage, read [Start leaf stages and deliver the first intake](../operations/coordination.md#start-leaf-stages-and-deliver-the-first-intake) for timing, intake and direct peer handovers.

Knob overrides per seat (agent, model, effort): <settings/orchestration notes or none>.

### Bind the primary requirement

Before compiling a Worker dispatch, read [Bind acceptance to one requirement revision](../core/acceptance.md#bind-acceptance-to-one-requirement-revision) for the primary revision, approval and evidence fields.

- Owned primary: `<stable identifier>@<version>`; packet `<canonical approved packet>`; approval `<durable citation>`.
- Deliverable and verification evidence classes: `<exact classes>`.
- Adjacent dependency/preservation revisions: `<separate versioned refs and checks>`.
- Acceptance block: `<Worker report/evidence anchor>`.

### Bind attempts and evidence

Before preparing a review handoff, read [Keep requirement revisions and attempts distinct](../core/acceptance.md#keep-requirement-revisions-and-attempts-distinct) for candidate binding, attempt advancement, immutable records and invalid-record handling.

- Leaf journal: `<path>`; next handoff attempt `<identifier>`; predecessor `<identifier or none>`.
- Carried findings: `<exact identifiers and dispositions>`.
- Candidate identity class and frozen expanded evidence: `<exact refs>`.
- Internal-run protocol: `<separate artifact/ref>`.

### Prepare closeout

After that handoff and a current-lineage proof, the Manager closes the leaf out directly:

1. `worktree_closeout_preview`;
2. the approval stop: the developer's commit approval for standalone or final work, or the recorded delegated series authority;
3. `worktree_closeout_apply`, with an explicit message for each enabled leg and that authority as its intent note.

The apply proves admission itself (task intent, lineage, the knowledge gate and the Curator's coherence record) and records journal evidence per leg. No release by an Orchestrator is needed.

Before recovering an unfinished closeout, read [Recover an unfinished closeout](../operations/coordination.md#recover-an-unfinished-closeout) for its generation-bound controls and journal status. Unfinished-closeout result: `<current operation reference and outcome, or none>`.

When an Orchestrator coordinates the sprint, send it the apply's result or refusal with the leaf, master and sprint references, blockers and acceptance facts. Report facts only; do not rank other masters.

`closeout_queue` (`status`, `rebuild`) is a disposable waiting list. It confers no authority.

### Apply task edits

Before applying task edits or responding to their projection effects, read [Apply task edits and rebuild projections](../operations/coordination.md#apply-task-edits-and-rebuild-projections) for mutation-first ordering, incomplete effects and accepted-generation evidence.

### Assign the review

Before dispatching an independent review, read [the review intake and handover procedure](../operations/review.md#receive-the-intake-and-hand-over-the-leaf-verdict) for the first intake and peer handover sequence.

Before adjudicating its exact requirement attempt, read [Independently judge the exact manifestation](../core/acceptance.md#independently-judge-the-exact-manifestation) for independence and evidence-class acceptance.

- Review purpose and mode: `<canonical purpose; baseline | fix-verification | none>`.
- Bound scope, route coverage and independence: `<scope and distinct author/reviewer seat refs>`.
- Primary revision and Worker envelope: `<exact refs>`.
- Baseline, preceding result and outstanding identifiers: `<refs or N/A>`.
- Recording and round authority: `<Reviewer record; Manager gate decision; extra-round approval when needed>`.

### Preserve rejected findings

Before classifying a rejected finding, read [Append the independent review record](../core/acceptance.md#append-the-independent-review-record) for the five exact classes.

Before handling an addition to the sealed list, read [review Boundaries](../operations/review.md#boundaries) for the two admission routes and their round authority.

- Sealed findings and current dispositions: `<exact refs; valid new identifier and targeted reopening when admitted>`.

### Maintain the attempt summary

- Summary path: `notes/reports/<master-id>-requirement-attempt-summary.md`.

Before maintaining the summary, read [Keep the master summary rebuildable](../core/acceptance.md#keep-the-master-summary-rebuildable) for its source journals and non-gating status.

### Record retained evidence

Before recording retained evidence, read [Record how retained evidence will be maintained](../core/acceptance.md#record-how-retained-evidence-will-be-maintained) for its maintenance information, separate from requirement acceptance.

### Publish the authorized pair

Before publishing the pair, read [Change instructions and publish within authority](../core/authority.md#change-instructions-and-publish-within-authority) for publication and check authority.

- Worker targeted checks and complete Curator result: `<exact reports, failures and not-run checks>`.

### Start curation

Before starting ordinary curation or prerequisite-sync reconciliation, read [Start leaf stages and deliver the first intake](../operations/coordination.md#start-leaf-stages-and-deliver-the-first-intake) for the two distinct curation occasions.

### Coordinate parallel work and serial landing

Before coordinating build and landing order, read [Let dependencies determine the work order](../core/invariants.md#let-dependencies-determine-the-work-order) for parallel build readiness and dependency-ordered landings.

- This master’s actual dependencies and landing authority: `<refs>`.

An atomic block exposes no intermediate leaf to super. For post-claim journal status and controls, follow [Recover an unfinished closeout](../operations/coordination.md#recover-an-unfinished-closeout) for its generation-bound controls and journal status.

### Retire leaf agents

Cleanup: the worker/reviewer/curator agents of a leaf are archived on the host when the leaf is finalized, cleaned up or abandoned; their reports stay. Manager/orchestrator seats are excluded.

Before retiring a seat you started, follow [starter retirement](../core/every-role.md#start-and-configure-role-agents) for the starter’s `retire_child` duty and the refusal for another caller.

## The exit
- When the master reaches its completion boundary, prepare the master-handover packet with the
  exact code/memory transaction, worker targeted-check report, the curator's complete onboarding/
  check report, current refs, and concrete conflicts or failed/not-run checks. If the developer or
  approved brief requests a master-exit review, dispatch the reviewer on its canonical document
  and preserve the monotonic sealed finding list across rounds within the settings limit. The verdict is evidence, not a routine transaction gate.
- Escalation: use the Manager's named parent or instructed reporting recipient. When an
  Orchestrator owns coordination, do not bypass it. Without a parent, report and ask in your own
  chat; invent no parent. Human-pinned kinds you may meet:
  `integration-approval`, `push-approval`, `cleanup-approval`.

## Reports
- Before preparing the master handover, read [the master-handover Shape](master-handover-packet.md#shape) for candidate, ancestry, completion, transaction and check evidence.
- The packet states execution nature, exact scope refs, readiness facts, and the prepared
  transaction; carry-over is named only when an actual divergence required it.
- Leaf-review notes on the relevant leaf document; decision-log entries for every delegated gate
  you decide and every reopen. Each leaf's status names its next seat and since when. Read the
  reports for progress; request no acknowledgement-only handovers.

## Messages

Before composing a message, read [Send a message that names the files](../reference/message-handover.md#send-a-message-that-names-the-files) for the sender and payload rules.

Before handling a full-store refusal, read [full-store receipt handling](../reference/message-handover.md#handle-a-full-store-refusal) for the pending record, one resend and second-refusal report.
```

---

**Preparation checks for the Orchestrator or Architect.**

- Fill every `<placeholder>`; an unresolved placeholder is not dispatchable.
- Do not compile branch names, commit ids, or private contract/session ids into this preparation record. The
  canonical sprint/master documents and their structural admission are the reconciliation anchor.
