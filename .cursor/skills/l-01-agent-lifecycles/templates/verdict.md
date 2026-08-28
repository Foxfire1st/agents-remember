# Verdict Template (adversarial reviewer)

The artifact an **adversarial reviewer** writes at a seam (`roles/reviewer.md`). It lands
under the series `notes/reports/` directory. Leaf route review binds through the task document;
seam verdicts attach to handover gates as **judge evidence**. Variants are **leaf route review**,
**master-exit** (before manager → orchestrator), **super-exit** (before orchestrator → developer),
and the loop-review adaptation below.

## Rules

1. **A verdict is evidence, not a decision.** State an explicit pass/block **recommendation**; the gate's
   decider decides. Never write the verdict as if it were the gate outcome.
2. **A block must decompose into fix leaves** — concrete, leaf-shaped findings the owning
   manager/orchestrator can dispatch. A block that cannot be named as fix leaves is not yet a block.
3. **Refute-or-confirm:** every finding must survive an attempt to refute it. Rank findings and cite
   the backing evidence file.
4. Cover all three lenses (completion · code quality · onboarding-vs-code) explicitly, even to say a
   lens is clean.
5. Record the gate evidence reference exactly: `kind=reviewer-verdict`, `ref=<artifact path>`, and
   `verdict=<pass | pass-with-notes | block>`.
6. **Report every bound catalog criterion** in the Criteria Catalog Results table (the catalogs the
   binding table in `roles/reviewer.md` assigns this review type, `../criteria/`) — one row per
   standing criterion even when it found nothing, candidate rows when run — and carry any proposed
   catalog amendments (the promotion ratchet) in that section.
7. **The reviewer seat is distinct from the author seat, and every requirement verdict cites
   evidence of the requirement's class** (rendering → mounted-UI proof, scheduling →
   operation-level proof, data model → artifact-level proof). A self-review or a wrong-class
   verdict is a verdict-laundering finding.
8. **Report the durable-evidence promotion hold point:** `N/A`, or one row per introduced/retained
   artifact naming its stable executable contract or dated expiry/retirement event, owner,
   source-observed consumers, compatibility consequence, and public lifecycle-validator result.
9. **Adjudicate every requirement attempt separately.** Use the mandatory block below once per
   exact stable ID + version, leaf manifestation, worker attempt, and exact candidate, and choose
   only `accepted` or `rejected`. Independently inspect the matching canonical packet and every cited artifact;
   missing rationale, wrong-class evidence, invalid citations, or missing developer approval forces
   rejection. PASS and PASS-WITH-NOTES are forbidden while any requirement is rejected. A worker
   row correctly adjudicated as `blocked` still requires an overall BLOCK until it is delivered or
   becomes an authorized `approved-change`.
10. **Do not merge the two evidence contracts.** Per-requirement acceptance and the
    durable-evidence stable-contract-or-expiry hold point are separately mandatory; neither is
    evidence for the other.
11. Append the reviewer block as a separate immutable record in the same single physical leaf
    Requirement Attempt Journal that contains the worker attempt. In the verdict body, link that
    exact journal anchor instead of copying the record into a second authority. Never edit the
    worker record or any earlier journal bytes. A pre-adjudication candidate change for this
    manifestation requires a successor worker attempt and new reviewer record; acceptance does not
    float to “latest,” but an unrelated later candidate also does not reopen accepted work.
    A malformed pre-handoff row never enters review: preserve its append-only
    `non-attempt-correction`/void reference and consume no attempt ID. A malformed handed-off row is
    a formal attempt; reject it independently without editing it, and require a successor only at
    the worker's next exact-candidate handoff.
12. Classify every rejection finding as exactly one of `implementation defect`, `evidence gap`,
    `requirement contradiction/overconstraint`, `test/tool defect`, or `external blocker`. A
    requirement problem is rejected for architect/developer-approved revision; a reviewer cannot
    rewrite it. Previously accepted attempts stay closed unless this reviewer proves direct
    regression and the owning manager (architect in a flat run) records bounded invalidation citing
    the accepted attempt, reviewer record, regressing candidate, and affected set, or an approved
    new requirement version affects that manifestation. This verdict cannot reopen work by itself.

## Mandatory Requirement Adjudication Block (repeat once per stable ID + version in every variant)

This block is the mandatory reviewer record. Append it once per stable ID + version to the single physical leaf Requirement Attempt
Journal. The independently authored verdict links the resulting journal anchor; it does not become
a second copy of the authoritative record.

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
  durable corpus ruling | mismatch/unapproved, therefore rejected>
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

Allowed failure classes are exactly `implementation defect`, `evidence gap`, `requirement
contradiction/overconstraint`, `test/tool defect`, and `external blocker`. A requirement problem
must point to a requested developer-approved revision; it cannot be rewritten in this record.
```

## Leaf Route-Review Variant (every code-changing leaf)

```md
# Independent Route-Review Verdict — <leaf id>

| Field           | Value                                              |
| --------------- | -------------------------------------------------- |
| scope           | <leaf code candidate; the plane binds its tree>    |
| reviewer seat   | <leaf task_doc path> + reviewer (must differ from author seat) |
| author seat     | <implementer seat that authored the change set>    |
| recommendation  | PASS, PASS-WITH-NOTES, or BLOCK                   |
| decider         | owning manager (architect in flat/solo mode)       |
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

## Owner Recording Packet
- verdict: `<pass | pass-with-notes | block>`
- verdictRef: `notes/reports/<leaf-id>-route-review-verdict.md`
- routes: `<one {route, verdict, evidenceRef} row per table row>`

The owner passes only this packet to `task_doc.record_route_review`. Do not include a candidate hash:
the control plane computes and stamps the current Git candidate tree after proving every referenced
artifact exists. A repair changes the tree and therefore requires the same route reviewers to
delta-verify and the owner to record a fresh packet.
```

## Master-Exit Variant

```md
# Adversarial Verdict — master-exit · <master id>

| Field           | Value                                              |
| --------------- | -------------------------------------------------- |
| seam            | master-exit                                        |
| execution nature | <organizational or atomic>                        |
| scope           | <exact proposed organizational super candidate including final leaf; atomic branch ref> |
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

## Lens 2 — Code Quality
- targeted leaf acceptance: current | failing:<which>
- one full master gate: reserved for <proposed final organizational super candidate or atomic landing>; reviewer did not duplicate it
- regressions vs the past (route indexes · CGC · GrepAI): none | <finding>
- backing evidence file: <quality/impact report path>

## Lens 3 — Onboarding vs Code
- changed files' sidecars refreshed same pass: yes | <gaps>
- drift_check clean and memory_quality_check clean: yes | <finding>
- master-side route overviews current: yes | <finding>
- backing evidence file: <onboarding-coherency report path>

## Criteria Catalog Results (every bound criterion reported — see roles/reviewer.md binding table)
| Criterion (id · catalog) | Ran | Finding | Evidence |
| ------------------------ | --- | ------------- | -------- |
| <CS-1 · code-seam>       | yes | none | <what was walked/checked> |
- Proposed catalog amendments (promotion ratchet): <surviving novel finding-class → catalog + evidence> | none

## Findings (ranked; each refute-tested)
| # | Severity | Finding | Evidence file/ref | Refutation attempted | Survives? |
| - | -------- | ------- | ----------------- | -------------------- | --------- |

## If BLOCK — Manager Fix Leaves
1. <fix leaf under this master: scope · target files/docs · evidence refs · done-when>
   (A BLOCK with no fix leaves here is invalid — resolve to PASS-WITH-NOTES or name the leaves.)

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

## Lens 2 — Code Quality
- resolved system/tools.md suite for the accumulated super branch: green | failing:<which>
- branch-wide regressions vs the past and across masters (route indexes · CGC · GrepAI): none | <finding>
- backing evidence file: <quality/impact report path>

## Lens 3 — Onboarding vs Code
- changed files' sidecars refreshed same pass: yes | <gaps>
- drift_check clean and memory_quality_check clean: yes | <finding>
- route overviews describe the accumulated behavior: yes | <finding>
- C-11 carry-over and ledger mapping coherent for the final super branch: yes | <finding>
- backing evidence file: <onboarding-coherency report path>

## Criteria Catalog Results (every bound criterion reported — see roles/reviewer.md binding table)
| Criterion (id · catalog) | Ran | Finding | Evidence |
| ------------------------ | --- | ------------- | -------- |
| <CS-1 · code-seam>       | yes | none | <what was walked/checked> |
- Proposed catalog amendments (promotion ratchet): <surviving novel finding-class → catalog + evidence> | none

## Findings (ranked; each refute-tested)
| # | Severity | Finding | Evidence file/ref | Refutation attempted | Survives? |
| - | -------- | ------- | ----------------- | -------------------- | --------- |

## If BLOCK — Orchestrator Fix Leaves
1. <fix leaf routed by the orchestrator: owning or reopened leaf, or new scoped fix leaf · scope · target files/docs · evidence refs · done-when>
   (A BLOCK with no fix leaves here is invalid — resolve to PASS-WITH-NOTES or name the leaves.)

## Judge-Evidence Note
This verdict attaches to the super-exit handover gate as judge evidence. The decider decides; this
reviewer does not.
```

## Loop-Review Adaptation (leaf full-loop · plan review)

A three-party-loop review (a full-loop leaf round, or the plan review over an orchestration task)
uses the **master-exit variant's shape minus the gate machinery**: drop the `gate evidence` header
row and the Judge-Evidence Note (a loop review attaches to no gate), set `decider` to the **loop
owner** (the leaf's owning seat, or the architect for the plan review), scope to the round's
change set (or the orchestration-task draft), and keep everything else — recommendation, Criteria
Catalog Results (the loop's bound catalogs, e.g. `plan-review` + `report-verification` for a plan
review), ranked refute-tested findings, and fix decomposition. The verdict remains **evidence to
the loop owner, never a decision**. Keep the mandatory Requirement Adjudication section and one
block per stable ID + version. A delta-verify appends a dated delta section to this same artifact, rechecks
only rejected IDs, direct regressions, and newly developer-authorized changed deliveries, and
retains earlier accepted adjudications rather than opening a new full review. An approved new
requirement version cannot inherit the prior version's acceptance; append its own reviewer record
to the authoritative journal and link that anchor here.
