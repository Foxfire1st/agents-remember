# Verdict Template (adversarial reviewer)

The Reviewer uses this template to report an adversarial review at a seam: a boundary where work is checked before handover or integration. The gate owner and developer read the verdict as evidence; the gate owner decides acceptance.

In this template, ID means identifier.

Write the artifact under the series `notes/reports/` directory. Organizational leaf route review and atomic-master integration review bind through the task document. Other seam verdicts attach to handover gates as evidence for the decider.

The variants below are organizational leaf route review, atomic-master integration review, master exit (Manager to Orchestrator), super exit (Orchestrator to the Architect, who takes it to the developer for the decision), and the loop-review adaptation. Atomic child leaves do not receive a separate route-review verdict.

The [Reviewer role](../roles/reviewer.md) owns its duties and applicable criteria. [Review](../operations/review.md) owns the mode contract, and [acceptance](../core/acceptance.md) owns adjudication and the completion boundary.

This template shapes the artifact; it does not author rules. The sections below are the fields its recipient reads. Where wording differs, those files govern.

## Rules

1. **A verdict is evidence, not a decision.** State an explicit pass/block **recommendation**; the gate's
   decider decides. Never write the verdict as if it were the gate outcome.
2. **Route the verdict to the seam's recipient** (leaf block directly to the Worker with the sealed findings report; master/super findings stay with their owning Manager/Orchestrator). The verdict travels as a file: the handover message names its path, never its pasted text.
3. **Refute-or-confirm:** every finding must survive an attempt to refute it. Rank findings and cite
   the backing evidence file.
4. Use [Check baseline evidence](../reference/review-baseline.md#check-baseline-evidence) for the three review lenses. Full test suites and drift reports appear only when
   the developer asked for them. The Curator's complete memory quality check in normal curation
   is always required and always reported.
5. Record the gate evidence reference exactly: `kind=reviewer-verdict`, `ref=<artifact path>`, and
   `verdict=<pass | pass-with-notes | block>`.
6. **Report the phase-appropriate catalog result** in the Criteria Catalog Results table: a
   `baseline` reports every bound standing criterion; a `fix-verification` reports only the
   sealed-baseline criteria/evidence used to verify its listed IDs.
7. **The reviewer seat is distinct from the author seat, and every requirement verdict cites
   evidence of the requirement's class.** A self-review or a wrong-class
   verdict is a verdict-laundering finding.
8. Before reporting retained evidence, read [Record how retained evidence will be maintained](../core/acceptance.md#record-how-retained-evidence-will-be-maintained) for the independent maintenance obligation; fill the artifact table below.
9. Before adjudicating a requirement attempt, read [Independently judge the exact manifestation](../core/acceptance.md#independently-judge-the-exact-manifestation) for its acceptance boundary.
10. Before appending the Reviewer record, read [Append the independent review record](../core/acceptance.md#append-the-independent-review-record) for record ownership and the five rejection classes. Use the block below and link the authoritative journal anchor.
11. Before reporting successor dispositions, read [review Boundaries](../operations/review.md#boundaries) for the mode and sealed-finding rules. Preserve its admitted additions in the fields below.

## Additions to a sealed findings list

For additions after sealing, follow the two routes and round authority in the [review operation’s Boundaries](../operations/review.md#boundaries). Record each new sealed identifier, its targeted reopening and the ruling on validity; retain earlier sealed findings.

## Mandatory Requirement Adjudication Block (repeat once per stable ID + version in every variant)

Use this authoring block for [the independent-review record](../roles/reviewer.md#write-the-review-artifacts). The verdict links the resulting leaf-journal anchor rather than duplicating the appended record. Before inspecting the canonical packet, read [the approval-record definition](../core/every-role.md#requirements-and-limits) for the approved manifest entry, its content hash and the corpus hash.

```md
### <stable requirement ID> @ <version> — <exact requirement label>

- Record kind: `reviewer-attempt-adjudication`
- Reviewer record ID: `<leaf-local reviewer record id>`
- Worker attempt ID: `<exact immutable worker attempt id>`
- Worker record reference: `<leaf journal path + attempt anchor>`
- Leaf manifestation: `<leaf-id>/<stable requirement ID>@<version>`
- Exact candidate inspected: `<Git tree/commit | non-code artifact digest + durable anchors>`
- Predecessor and carried findings checked: `<attempt id + finding ids | none>`
- Reviewer record appended at: `<YYYY-MM-DDTHH:MM>`
- Canonical packet inspection: <version-addressed path + matching ID/version + approved state +
  durable approval record citation | mismatch/unapproved, therefore rejected>
- Worker/handoff status: `satisfied` | `blocked` | `approved-change`
- Reviewer adjudication: `accepted` | `rejected`
- Independently inspected delivery/implementation artifacts:
  - <code: `path` — `symbol`; non-code: `path` — `section/anchor`; include inspection result>
- Independently inspected verification artifacts:
  - <`path` — `test symbol/node`, report section, scenario anchor, or other exact evidence>
- Evidence-class check: <required class> → <observed class> → <match | mismatch>
- Reviewer rationale: <why the independently inspected delivery and verification do or do not
  satisfy this exact requirement>
- Demonstrated behavior and failure caught: <reviewer's account, not copied worker prose>
- Refutation attempted: <how the claim was challenged and result>
- Exact command/result or durable evidence reference: <verified value>
- Exception approval (`blocked` or `approved-change` only):
  - Why unchanged delivery is impossible: <reviewed reason>
  - Changed delivery: <reviewed delivery | none>
  - Durable developer ruling: <citation + inspection result | missing, therefore rejected>
- Rejection findings (`accepted` may state `none`):

| Finding ID | Failure class (exactly one) | Reviewer evidence and rationale | Required next action |
| ---------- | --------------------------- | ------------------------------- | -------------------- |
| <id or none> | <one exact failure class> | <independent anchors and reason> | <class-owned recovery> |

Failure class: `<one of the five classes defined by [Append the independent review record](../core/acceptance.md#append-the-independent-review-record)>`.
```

## Organizational Leaf Route-Review Variant

```md
# Independent Route-Review Verdict — <leaf id>

| Field           | Value                                              |
| --------------- | -------------------------------------------------- |
| scope           | <leaf code candidate; the lifecycle tools bind its tree>    |
| reviewer seat   | <leaf task_doc path> + reviewer (must differ from author seat) |
| author seat     | <implementer seat that authored the change set>    |
| review mode     | <baseline \| fix-verification>                |
| sealed baseline | <sealed first-review baseline/verdict ref>         |
| preceding result / outstanding IDs | <result ref + exact IDs \| N/A for baseline> |
| recommendation  | PASS, PASS-WITH-NOTES, or BLOCK                   |
| decider         | owning Manager (Architect under developer-chosen direct coordination)       |
| artifact path   | notes/reports/<leaf-id>-route-review-verdict.md    |
| written         | <YYYY-MM-DDTHH:MM>                                 |

## Requirement Adjudication
<Repeat the Mandatory Requirement Adjudication Block once for every exact in-scope stable ID + version.>

## Major-Route Coverage (every material route accounted for)
| Major route | Independent reviewer report | Verdict | Changed + surrounding scope reviewed |
| ----------- | --------------------------- | ------- | ------------------------------------ |
| <route>     | notes/reports/<route-report>.md | pass, pass-with-notes, or block | <paths/owners/tests> |

## Criteria Catalog Results
| Criterion (id · catalog) | Ran | Finding | Evidence |
| ------------------------ | --- | ------- | -------- |
| <CS-1 · code-seam>       | yes | none    | <what was refute-tested> |

## Durable-Evidence Promotion Hold Point
| Artifact | Decision | Owner + consumers | Executable contract or expiry/removal | Lifecycle validator |
| -------- | -------- | ----------------- | ------------------------------------- | ------------------- |
| <path or N/A> | <stable-contract, expiry, or N/A> | <owner + source-observed consumers> | <contract id + node, or date + replacement/removal> | <command + result> |

## Findings (ranked; each refute-tested)
| # | Severity | Route | Finding | Evidence file/ref | Refutation attempted | Survives? |
| - | -------- | ----- | ------- | ----------------- | -------------------- | --------- |

## Fix-Verification Dispositions (required only for `reviewMode=fix-verification`)
| Sealed issue ID | Preceding status | Worker fix/evidence | Fixed or unfixed | Remaining-set membership |
| --------------- | ---------------- | ------------------- | ---------------- | ----------------------- |
| <ID>             | <open>           | <exact refs>        | <fixed \| unfixed> | <present \| removed> |

## Reviewer Recording Packet — ordinary leaf CODE lane
- begin: `task_doc(operation="begin_review")` before reviewer work
- result: `task_doc(operation="record_review")` or the existing `task_doc(operation="record_route_review")`
- verdict: `<pass | pass-with-notes | block>`
- verdictRef: `notes/reports/<leaf-id>-route-review-verdict.md`
- routes: `<one {route, verdict, evidenceRef} row per table row>`

Before recording an ordinary code round, read [Record code review rounds](../operations/review.md#record-code-review-rounds) for the recording owner and operations.

Before handing over the leaf verdict, read [Receive the intake and hand over the leaf verdict](../operations/review.md#receive-the-intake-and-hand-over-the-leaf-verdict) for the Manager notices, direct peer handovers and separate memory lane.
```

## Atomic-Master Integration Route-Review Variant

```md
# Independent Route-Review Verdict — atomic master integration · <master id>

| Field           | Value                                              |
| --------------- | -------------------------------------------------- |
| scope           | <exact accumulated atomic master candidate; the lifecycle tools bind its tree> |
| reviewer seat   | <master's acceptance leaf task_doc path> + reviewer (must differ from author seat) |
| author seat     | <manager/owner that assembled the accumulated candidate> |
| membership      | <canonical ordered child refs and normative intent identities> |
| review mode     | <baseline \| fix-verification>                |
| sealed baseline | <sealed first-review baseline/verdict ref>         |
| preceding result / outstanding IDs | <result ref + exact IDs \| N/A for baseline> |
| recommendation  | PASS, PASS-WITH-NOTES, or BLOCK                   |
| decider         | integration owner at the master-to-parent gate    |
| artifact path   | notes/reports/<master-id>-integration-route-review-verdict.md |
| written         | <YYYY-MM-DDTHH:MM>                                 |

## Requirement Adjudication
<Repeat the Mandatory Requirement Adjudication Block once for every exact master/child stable ID + version in scope.>

## Major-Route Coverage (every material route accounted for)
| Major route | Independent reviewer report | Verdict | Changed + surrounding scope reviewed |
| ----------- | --------------------------- | ------- | ------------------------------------ |
| <route>     | notes/reports/<route-report>.md | pass, pass-with-notes, or block | <paths/owners/tests> |

## Candidate and Dependency Binding
- candidate tree: <accumulated master tree stamped by the lifecycle tools; never caller-supplied>
- master ref: <canonical task-root `task.json` ref>
- child membership/intents: <exact ordered refs and digest identities>
- evidence: <verdict and route evidence refs, all task-relative and digest-bound>
- integration seam: <master-to-parent movement only>

## Criteria Catalog Results
| Criterion (id · catalog) | Ran | Finding | Evidence |
| ------------------------ | --- | ------- | -------- |
| <CS-1 · code-seam>       | yes | none    | <what was refute-tested> |

## Findings (ranked; each refute-tested)
| # | Severity | Route | Finding | Evidence file/ref | Refutation attempted | Survives? |
| - | -------- | ----- | ------- | ----------------- | ------------------- | --------- |

## Fix-Verification Dispositions (required only for `reviewMode=fix-verification`)
| Sealed issue ID | Preceding status | Worker fix/evidence | Fixed or unfixed | Remaining-set membership |
| --------------- | ---------------- | ------------------- | ---------------- | ----------------------- |
| <ID>             | <open>           | <exact refs>        | <fixed \| unfixed> | <present \| removed> |

## Owner Recording Packet
- verdict: `<pass | pass-with-notes | block>`
- verdictRef: `notes/reports/<master-id>-integration-route-review-verdict.md`
- routes: `<one {route, verdict, evidenceRef} row per table row>`

The master owner records its review begin and result on the canonical master document. Atomic child closeout and leaf-to-master accumulation do not call this operation.
```

## Master-Exit Variant

```md
# Adversarial Verdict — master-exit · <master id>

| Field           | Value                                              |
| --------------- | -------------------------------------------------- |
| seam            | master-exit                                        |
| execution nature | <organizational or atomic>                        |
| scope           | <exact proposed organizational super candidate including final leaf; atomic branch ref> |
| review mode     | <baseline \| fix-verification>                |
| sealed baseline | <sealed first-review baseline/verdict ref>         |
| preceding result / outstanding IDs | <result ref + exact IDs \| N/A for baseline> |
| reviewer seat   | <review task_doc path> + reviewer                  |
| task docs       | <master task_doc + leaf task docs>                 |
| recommendation  | PASS, PASS-WITH-NOTES, or BLOCK                       |
| decider         | orchestrator (delegated `master-handover-approval`; serious issues escalate → developer) |
| artifact path   | notes/reports/<master-id>-master-exit-verdict.md   |
| gate evidence   | kind=reviewer-verdict; ref=<artifact path>; verdict=<pass, pass-with-notes, or block> |
| written         | <YYYY-MM-DDTHH:MM>                                  |

## Requirement Adjudication
<Repeat the Mandatory Requirement Adjudication Block once for every exact master/leaf stable ID + version in scope.>

## Lens 1 — Completion vs Task Docs
- Master requirement/substep coverage: addressed | delta justified | MISSING
- Leaf task docs and worker reports reconcile with the master task_doc: yes | <gaps>
- backing evidence file: <impact-analysis report path>
- durable-evidence promotion hold point: N/A | <artifact → stable executable contract or dated expiry/removal; validator evidence>

## Lens 2 — Scoped Implementation Evidence
- targeted leaf acceptance: current | failing:<which>
- full quality/test suite: not run unless explicitly requested — <request/result if any>
- regressions vs the past (route indexes · native search): none | <finding>
- backing evidence file: <quality/impact report path>

## Lens 3 — Onboarding vs Code
- changed files' sidecars refreshed same pass: yes | <gaps>
- affected onboarding checks + full `memory_quality_check` curation result: pass | failed | blocked | not-run — <finding/returned codes>
- drift reporting when the workflow requests it: pass | failed | blocked | not-run — <request/result if any>
- master-side route overviews current: yes | <finding>
- backing evidence file: <onboarding-coherency report path>

## Criteria Catalog Results (baseline: every bound criterion; fix-verification: sealed-ID evidence only — see roles/reviewer.md)
| Criterion (id · catalog) | Ran | Finding | Evidence |
| ------------------------ | --- | ------------- | -------- |
| <CS-1 · code-seam>       | yes | none | <what was walked/checked> |
- Proposed catalog amendments (baseline only): <surviving novel finding-class → catalog + evidence> | none

## Findings (ranked; each refute-tested)
| # | Severity | Finding | Evidence file/ref | Refutation attempted | Survives? |
| - | -------- | ------- | ----------------- | -------------------- | --------- |

## Fix-Verification Dispositions (required only for `reviewMode=fix-verification`)
| Sealed issue ID | Preceding status | Worker fix/evidence | Fixed or unfixed | Remaining-set membership |
| --------------- | ---------------- | ------------------- | ---------------- | ----------------------- |
| <ID>             | <open>           | <exact refs>        | <fixed \| unfixed> | <present \| removed> |

## If BLOCK — Worker Repair Or Owner Fix Leaf
1. <inside a leaf: Worker, sealed finding IDs/report, exact blocked freeze and repair evidence; send directly to the Worker without a Manager relay>
2. <master scope needing a fix leaf: owning Manager · authorized fix-leaf scope · target files/docs · evidence refs · done-when; fix-verification: existing owner and sealed outstanding IDs only>
   (Name the concrete repair owner and action; a local leaf repair needs no new fix leaf.)

## Judge-Evidence Note
This verdict attaches to the master-exit handover gate as judge evidence. The decider decides; this
reviewer does not.
```

## Super-Exit Variant

```md
# Adversarial Verdict — super-exit · <super branch>

| Field           | Value                                              |
| --------------- | -------------------------------------------------- |
| seam            | super-exit                                         |
| scope           | <super integration branch ref>                     |
| review mode     | <baseline \| fix-verification>                |
| sealed baseline | <sealed first-review baseline/verdict ref>         |
| preceding result / outstanding IDs | <result ref + exact IDs \| N/A for baseline> |
| reviewer seat   | <review task_doc path> + reviewer                  |
| task docs       | <portfolio task docs + master task docs>           |
| recommendation  | PASS, PASS-WITH-NOTES, or BLOCK                       |
| decider         | developer (human review concentrates at the super gate) |
| artifact path   | notes/reports/<series-id>-super-exit-verdict.md    |
| gate evidence   | kind=reviewer-verdict; ref=<artifact path>; verdict=<pass, pass-with-notes, or block> |
| written         | <YYYY-MM-DDTHH:MM>                                  |

## Requirement Adjudication
<Repeat the Mandatory Requirement Adjudication Block once for every exact portfolio/master stable ID + version in scope.>

## Lens 1 — Completion vs Portfolio Task Docs
- Portfolio objective and dependency order satisfied: yes | <gaps>
- Master handover packets and prior master-exit verdicts reconciled: yes | <gaps>
- Cross-master conflicts, duplicate implementations, and deferred follow-ups surfaced: yes | <finding>
- backing evidence file: <impact-analysis report path>
- durable-evidence promotion hold point: N/A | <artifact → stable executable contract or dated expiry/removal; validator evidence>

## Lens 2 — Scoped Implementation Evidence
- targeted branch evidence: pass | failing:<which> | not-run:<reason>
- full system/tools.md suite: not run unless explicitly requested — <request/result if any>
- branch-wide regressions vs the past and across masters (route indexes · native search): none | <finding>
- backing evidence file: <quality/impact report path>

## Lens 3 — Onboarding vs Code
- changed files' sidecars refreshed same pass: yes | <gaps>
- affected onboarding checks + full `memory_quality_check` curation result: pass | failed | blocked | not-run — <finding/returned codes>
- drift reporting when the workflow requests it: pass | failed | blocked | not-run — <request/result if any>
- route overviews describe the accumulated behavior: yes | <finding>
- carryover and ledger mapping coherent under the branch carry-over guide (`docs/guides/carryover-from-branch.md`) for the recorded landing line: yes | <finding>
- backing evidence file: <onboarding-coherency report path>

## Criteria Catalog Results (baseline: every bound criterion; fix-verification: sealed-ID evidence only — see roles/reviewer.md)
| Criterion (id · catalog) | Ran | Finding | Evidence |
| ------------------------ | --- | ------------- | -------- |
| <CS-1 · code-seam>       | yes | none | <what was walked/checked> |
- Proposed catalog amendments (baseline only): <surviving novel finding-class → catalog + evidence> | none

## Findings (ranked; each refute-tested)
| # | Severity | Finding | Evidence file/ref | Refutation attempted | Survives? |
| - | -------- | ------- | ----------------- | -------------------- | --------- |

## Fix-Verification Dispositions (required only for `reviewMode=fix-verification`)
| Sealed issue ID | Preceding status | Worker fix/evidence | Fixed or unfixed | Remaining-set membership |
| --------------- | ---------------- | ------------------- | ---------------- | ----------------------- |
| <ID>             | <open>           | <exact refs>        | <fixed \| unfixed> | <present \| removed> |

## If BLOCK — Orchestrator Fix Leaves
1. <baseline: fix leaf routed by the orchestrator, including an owning/reopened/new scoped fix leaf · scope · target files/docs · evidence refs · done-when; fix-verification: the existing owner and sealed outstanding issue IDs only>
   (A BLOCK with no fix leaves here is invalid — resolve to PASS-WITH-NOTES or name the leaves.)

## Judge-Evidence Note
This verdict attaches to the super-exit handover gate as judge evidence. The decider decides; this
reviewer does not.
```

## Loop-Review Adaptation (leaf full-loop · plan review)

A full-loop leaf review or a plan review submitted by the Architect uses the **master-exit variant's shape minus the gate machinery**. Drop the `gate evidence` header row and the Judge-Evidence Note: neither review attaches to a gate. Set `decider` to the leaf's owning seat for a leaf review, or to the Architect for a plan review. Scope it to the round's change set or **the reviewed plan**. For a leaf, use the direct Worker repair section instead of a Manager fix-leaf request.

Keep everything else: recommendation, Criteria Catalog Results with the bound catalogs (for example, `plan-review` + `report-verification` for a plan review), ranked refute-tested findings and fix decomposition. The verdict is **evidence to the recipient, never a decision**. Keep the mandatory Requirement Adjudication section and one block per stable ID + version. The Architect accepts the plan review's adjudications.

Before writing a delta-verification section, read [review boundaries](../operations/review.md#boundaries) for successor findings and the required retained history. Append a dated delta section to this same artifact. Record the preceding result, exact outstanding identifiers and their fixed/unfixed dispositions, and retain earlier accepted adjudications.

Before requesting a review beyond the settings limit, read [the Manager’s extra-round approval procedure](../roles/manager.md#make-decisions-within-your-authority) for the request and recorded approval. This template grants no round.

## Handover

Before composing a message, read [Send a message that names the files](../reference/message-handover.md#send-a-message-that-names-the-files) for the sender and payload rules.

Before handling a full-store refusal, read [full-store receipt handling](../reference/message-handover.md#handle-a-full-store-refusal) for the pending record, one resend and second-refusal report.
