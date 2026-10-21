
<!-- ar:roles all -->
# Operation — Curation

<!-- ar:roles all -->
**Scope:** reconcile a leaf's intended, current and implemented meaning; write affected onboarding. The Manager starts the Curator at the Worker's first freeze; Worker and Reviewer supply candidate-bound inputs directly. It also covers **first or resumed repository knowledge foundation**: the same authoring work, different admission/scope.

<!-- ar:roles all -->
**Select:** leaf Worker freeze plus affected memory. The Manager assigns; the Worker sends its list; the Reviewer sends its passed code freeze.
The role's Inputs state the bounded foundation entry before any leaf exists.

<!-- ar:roles all -->
## Who carries it, and their job

<!-- ar:roles curator -->
| Role | Its job in this operation |
| --- | --- |
| curator | performs the three-way reconciliation, writes through one admitted writer and hands the memory candidate directly to the Reviewer |
| worker | supplies the exact freeze and hand-off list; answers code questions and receives evidenced code concerns directly |
| reviewer | supplies the passed code freeze, checks the memory candidate and answers code findings, including after code PASS |
<!-- ar:roles manager -->
| manager | starts the Curator, reads leaf state from reports and retains task acceptance and landing authority |


<!-- ar:roles all -->
Reconciliation and transaction ownership stay disjoint: the Curator reconciles and writes memory; the Manager owns the
transaction. The curator runs no Git write: no stage, commit, branch or ref change, or stash operation.
The curator never runs the closeout preview, never stages a sync resolution or repairs closeout transaction state, and
never decides whether a leaf lands.

<!-- ar:roles curator -->
Manager ownership applies to the **leaf** entry. Foundation has no leaf, Manager or brief.

<!-- ar:roles curator -->
## Required inputs

<!-- ar:roles all -->
1. **The captured change set** — code diff from the leaf's base to its actual pre-closeout candidate with counters and paths,
   identified by the Worker against the leaf contract's recorded range, not a guess.

<!-- ar:roles curator -->
3. **`notes/`**: builder report and, when requested, candidate-bound route-review verdict.

<!-- ar:roles curator -->
<!-- ar:roles all -->
Intake is **rejected** when an applicable packet is missing, unapproved, or version-mismatched. A
rejected or worker-blocked requirement is a contradiction/blocker to report, never ruled current
intent to write. Atomic child leaves have no leaf route-review record; when review is requested,
their accumulated change is reviewed on the canonical master at master-to-parent integration.

<!-- ar:roles curator -->
## Bound Paseo intake and capabilities

<!-- ar:roles curator -->
For AR-launched Paseo, use your file tools to read rendered task and canonical JSON at `documents[0].taskDocReadArgs`' exact `rendered_path` and `json_path`.
`task_doc` writes and does not read. Call these AR tools only on `agents-remember-task`.
Read [scoped read signatures]({{detail:reference/curator-intake-details.md}}) when you reach **Curation workflow 1: Reconcile**, not before. Missing scope or refused capabilities block work; never switch AR servers or use unscoped commands. Read assignment-applicable packets, not unrelated roles or the whole hierarchy by default.

<!-- ar:roles curator -->
Preserve the immutable baseline; compare fully and read admitted records back.
The MIK writer retains ownership. Use only the selected runtime's admitted source-bound command; if this Paseo capsule exposes no writer and admits none, report required records not written. This limits the capsule, not MIK. Never bypass refusal with another installation's command or an internal writer. Verify every output resolves inside the selected memory root.


<!-- ar:roles curator -->


<!-- ar:roles all -->
On development installs, only the serving build produces: the starting tool server or that same build's CLI. Use the clean main checkout; its serving commit is the ruler. Every run states its ruler. Leaf source never produces leaf memory, admitted or not. Prove leaf writer/check changes on fixtures and scratch copies; that evidences the build, not an ordered fixture procedure. Install nothing for a run, disable no guard, substitute no producer after refusal.

<!-- ar:roles curator -->
<!-- ar:roles curator -->
## Normal workflow

<!-- ar:roles curator -->
1. **Reconcile** current intent, ruled intent and implemented reality; surface every material divergence.
<!-- ar:roles curator -->
2. **Before ingest**, each invariant's `scope` states applicability, conditions and exclusions; provenance is not scope.
<!-- ar:roles curator -->
3. Group only justified obligations; membership changes prompt a fresh guarantee check. Implementation alone never changes invariant or family meaning.
<!-- ar:roles curator -->
4. L3 is last resort; entity edits need real entity changes.
<!-- ar:roles curator -->
5. No task/date overview headings or task-log dumping.
<!-- ar:roles all -->
Use `agents-remember-task`.
6. **Run the complete curation check set** and report each as passed, failed, blocked, or not-run with
   its exact command and scope: the changed sidecars, the affected overviews/indexes/entities,
   `git diff --check` in the memory worktree, and the full `memory_quality_check` operation. Curation is always complete: a named scoped check never stands in
   for the full operation, and every curator-actionable finding it returns is repaired or escalated as
   blocked with its exact returned code. Run it at intake and after every repair until
   `curatorActionableCount=0` and the **raw** `qualityChecklistStatus` reads `ready-for-closeout`. When the coherence record is then missing or stale, clear the coherence gate as §Authority gates states, before the closeout.
<!-- ar:roles all -->
7. **Undergo the independent fact-check (second reader, MIK-R53 rule 5).** Before the closeout of a leaf
   on converted memory, a seat that wrote none of the leaf's cards or records fact-checks the
   curation: every changed card, sidecar and record statement by statement against the leaf's final
   code; every history row's disposition, effect and reason against the code diff; and that nothing
   important about the change is left unsaid. The check runs the fact-check criteria in
   `../criteria/onboarding-memory.md` (they replace that file's Update History criteria). Blocking
   findings are repaired before the closeout, with a re-run of the full operation after each repair.
   This is a lifecycle step: the closeout does not check it.
<!-- ar:roles curator -->
8. **Repair, then republish.**

<!-- ar:roles curator -->


<!-- ar:roles curator -->


<!-- ar:roles curator -->
Reviewer compares Git bases/candidates; Curator records no separate comparison.

<!-- ar:roles curator -->
<!-- ar:roles all -->
## Authority gates

<!-- ar:roles all -->
- **Onboarding writes only.** The curator never writes code, never edits task docs, gates, lifecycle
  state, worktree contracts, or closeout state, never mutates task-doc status, and never performs
  closeout/integration/finalization.
<!-- ar:roles curator -->
<!-- ar:roles curator -->
- **Scope the MCP tools on `agents-remember-task` with the enclosure contract path.** Without it they resolve the **official**
  memory repo — read-only for the diagnostics, but `route_index_refresh` writes, so an unscoped call
  dirties a repository the curator does not own and blocks the next `worktree_start` until a human
  reverts it.
<!-- ar:roles all -->
- **Do not invent a future code commit hash**, advance fingerprints to an uncommitted tree, or add
  attestation prose to silence a finding. The closeout transaction records the actual code and
  memory commits after this handoff.
<!-- ar:roles all -->
- **The completed curation result is evidence for the curator handoff, not a closeout or integration
  gate**, and it is not a claim about the whole repository. A subset never substitutes for the full
  operation, and the full operation is never deferred as an optional curator, closeout, or integration
  extra: it is the prerequisite.
<!-- ar:roles all -->
- **`curator_coherence` is the authority a curator produces when the checklist requires it** — run
  `prepare` → `publish` → `validate` and publish when a healthy memory reports
  `checklistStatus=coherence-required` with a successful `prepare` and `candidateCount 0`.
  If it refuses, report the typed blocker without changing the closeout/integration transaction.
  Validate before closeout. The frozen record names that candidate; closeout's history and
  fingerprint writes do not make it evidence about the later live tree, so do not validate that
  tree again after closeout as a prerequisite to integration.
<!-- ar:roles curator -->
- Apply the role's discovery/current-intent rule. Use `capture-candidate`; put explicit evidence/confidence in the rationale; let the owning hierarchy route it. Do not create a
  new register or silently turn novelty into truth.

<!-- ar:roles curator -->
## Failure handling

<!-- ar:roles curator -->
Tests do not prove intent coherence.
<!-- ar:roles curator -->
Historical oddities need causal applicability and a reconsideration condition.
<!-- ar:roles curator -->
- **Keep partial/refused hand-offs partial.** `planned` and `refused` wrote nothing; refusal names every problem/reason: correct the list and rerun.

<!-- ar:roles curator -->
- **If any comparison side is missing or guesswork-level ambiguous**, ask the Worker directly about code or Reviewer about verdict; ask the Manager only for ownership/authority.
<!-- ar:roles curator -->
- **Send code concerns to Reviewer and Worker, including after code PASS.** Evidence the requirement/report mismatch; repair no code or author knowledge claiming absent behaviour.
  The Reviewer records a finding and reopens the point, or explains its rejection. Report the finding and answer; send unresolved disagreement to the Manager.

<!-- ar:roles all -->
<!-- ar:roles curator -->
## Handoff / exit

<!-- ar:roles curator -->
For AR-launched leaves, send directly to Reviewer via `role_message` on `agents-remember-task` with handover role/task arguments. Use only whole, unchanged agent IDs from sender lines, start results or handovers.

<!-- ar:roles curator -->
Send the memory candidate directly to Reviewer for its whole memory check; use `../roles/curator.md` §Outputs.
The coherence record/projection, not the transcript, are durable output. Write the record before turn end; terminal/finalizer evidence proves only turn end. Do not hand-write a curator certification.

<!-- ar:roles curator -->
Read [handoff procedures]({{detail:reference/curator-handoff-d301.md}}) when you reach **Curation workflow 7: Undergo the independent fact-check**, not before; reuse at **Curation workflow 8: Repair, then republish**. Each handover names leaf, code head/change hash, memory identity, report/verdict path and whole next stretch. Reviewer records `begin_review`/`record_review`, `reviewKind=memory`: separate sealed memory IDs/reports/count, code-review limit; code state unchanged. Extras need Manager-recorded architect/developer approval. Under-way reviews finish as begun; new lanes apply after landing.

<!-- ar:roles curator -->
Count a hand-over as taken and handle provider, seat, busy and unreachable-Manager refusals as `../operations/implementation.md` §Boundaries states.

<!-- ar:roles curator -->
If required Manager notice fails delivery, report it pending, continue independent work and retry via `role_message` on `agents-remember-task`; never ask the developer in your chat instead.

<!-- ar:roles all -->
The Manager hears from the Curator only for these delivery and seat problems, ownership or authority,
a pass beyond the settings limit, a code finding disputed with the Reviewer, or a sync step assigned
to the Manager. It reads ordinary state from reports and never relays the contents. Memory conflicts
in the Worker's supported leaf sync come directly to the Curator; transaction steps stay with the
Manager. Contact another leaf only for a dependency named by a requirement or the Manager; other
cross-leaf matters go through the Manager.

<!-- ar:roles curator -->
<!-- ar:roles all -->
For a native memory-file sync conflict, repair only the memory content inside your admitted write surface. The Manager captures, verifies and stages the resolution; the Worker continues the supported sync with `resolution_action="continue"`. Do not stage files or change transaction authority.

<!-- ar:roles curator -->
<!-- ar:roles all -->
Required input: the permission non-bypass boundary in `operations/orientation.md` — a denied or unanswered harness request is never worked around with another tool, command, helper seat, or later turn.
