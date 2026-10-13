# Verdict Template (adversarial reviewer)

The artifact an **adversarial reviewer** writes at a seam (`roles/reviewer.md`). It lands
under the series `notes/reports/` directory. Standalone/organizational leaf route review and
atomic-master integration review bind through the task document; other seam verdicts attach to
handover gates as **judge evidence**. Variants are **standalone/organizational leaf route review**,
**atomic-master integration review**, **master-exit** (before manager → orchestrator),
**super-exit** (before orchestrator → developer), and the loop-review adaptation below. Atomic
child leaves do not receive a separate route-review verdict.

**This template shapes the artifact; it does not author the rules.** The reviewer's duties and the
criteria-catalog binding live in `../roles/reviewer.md`, the mode contract in
`../operations/review.md`, and the adjudication and truth boundary in `../core/acceptance.md`. The
sections below are the fields a consumer reads; where wording differs, those files win.

## Rules

1. **A verdict is evidence, not a decision.** State an explicit pass/block **recommendation**; the gate's
   decider decides. Never write the verdict as if it were the gate outcome.
2. **Route the verdict to the seam's recipient** (leaf block directly to the Worker with the sealed findings report; master/super findings stay with their owning Manager/Orchestrator). The verdict travels as a file: the handover message names its path, never its pasted text.
3. **Refute-or-confirm:** every finding must survive an attempt to refute it. Rank findings and cite
   the backing evidence file.
4. Cover all three lenses (completion · scoped implementation evidence · onboarding-vs-code)
   explicitly, even to say a lens is clean. Full quality/test/memory evidence appears only when
   the developer explicitly requested that operation.
5. Record the gate evidence reference exactly: `kind=reviewer-verdict`, `ref=<artifact path>`, and
   `verdict=<pass | pass-with-notes | block>`.
6. **Report the phase-appropriate catalog result** in the Criteria Catalog Results table: a
   `baseline` reports every bound standing criterion; a `fix-verification` reports only the
   sealed-baseline criteria/evidence used to verify its listed IDs.
7. **The reviewer seat is distinct from the author seat, and every requirement verdict cites
   evidence of the requirement's class.** A self-review or a wrong-class
   verdict is a verdict-laundering finding.
8. **Report the durable-evidence promotion hold point:** `N/A`, or one row per introduced/retained
   artifact naming its stable executable contract or dated expiry/retirement event, owner,
   source-observed consumers, compatibility consequence, and public lifecycle-validator result.
9. **Adjudicate every requirement attempt separately** with the mandatory block below, once per
   exact stable ID + version, leaf manifestation, worker attempt, and exact candidate, choosing
   only `accepted` or `rejected`. PASS and
   PASS-WITH-NOTES are forbidden while any requirement is rejected.
10. **Do not merge the two evidence contracts.** Per-requirement acceptance and the
    durable-evidence stable-contract-or-expiry hold point are separately mandatory; neither is
    evidence for the other.
11. Append the reviewer block as a separate immutable record in the same single physical leaf
    Requirement Attempt Journal that contains the worker attempt, and link that
    exact journal anchor in the verdict body. Never edit the
    worker record or any earlier journal bytes. Under
    `reviewMode=fix-verification`, only the sealed outstanding IDs may be adjudicated.
12. Classify every first-review rejection finding as exactly one of `implementation defect`, `evidence gap`,
    `requirement contradiction/overconstraint`, `test/tool defect`, or `external blocker`.
    A successor disposition is valid only when its remaining IDs are a subset of the preceding
    remaining set and sealed baseline, every preceding ID has a fixed/unfixed disposition, and no
    unknown, duplicate, rewritten, reintroduced, or newly discovered finding is present.

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

## Standalone/Organizational Leaf Route-Review Variant

```md
# Independent Route-Review Verdict — <leaf id>

| Field           | Value                                              |
| --------------- | -------------------------------------------------- |
| scope           | <leaf code candidate; the plane binds its tree>    |
| reviewer seat   | <leaf task_doc path> + reviewer (must differ from author seat) |
| author seat     | <implementer seat that authored the change set>    |
| review mode     | <baseline | fix-verification>                |
| sealed baseline | <sealed first-review baseline/verdict ref>         |
| preceding result / outstanding IDs | <result ref + exact IDs | N/A for baseline> |
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

## Fix-Verification Dispositions (required only for `reviewMode=fix-verification`)
| Sealed issue ID | Preceding status | Worker fix/evidence | Fixed or unfixed | Remaining-set membership |
| --------------- | ---------------- | ------------------- | ---------------- | ----------------------- |
| <ID>             | <open>           | <exact refs>        | <fixed | unfixed> | <present | removed> |

## Reviewer Recording Packet — ordinary leaf CODE lane
- begin: `task_doc(operation="begin_review")` before reviewer work
- result: `task_doc(operation="record_review")` or the existing `task_doc(operation="record_route_review")`
- verdict: `<pass | pass-with-notes | block>`
- verdictRef: `notes/reports/<leaf-id>-route-review-verdict.md`
- routes: `<one {route, verdict, evidenceRef} row per table row>`

The Reviewer records its own ordinary CODE round and result as `../roles/reviewer.md` states; the Manager keeps gate acceptance and the architect's or the developer's recorded approval beyond the settings limit. The task document remains the authority.

Send the verdict directly to the Worker, including the sealed findings report on BLOCK; on PASS,
send the passed freeze and verdict directly to the Curator. Keep the Curator's MEMORY review sealed and counted apart. Return memory findings/pass directly to the Curator. Only when both
lanes pass, send the Manager one ready-for-closeout notice naming the freeze and both verdicts.
```

## Atomic-Master Integration Route-Review Variant

```md
# Independent Route-Review Verdict — atomic master integration · <master id>

| Field           | Value                                              |
| --------------- | -------------------------------------------------- |
| scope           | <exact accumulated atomic master candidate; the plane binds its tree> |
| reviewer seat   | <canonical master task_doc path> + reviewer (must differ from author seat) |
| author seat     | <manager/owner that assembled the accumulated candidate> |
| membership      | <canonical ordered child refs and normative intent identities> |
| review mode     | <baseline | fix-verification>                |
| sealed baseline | <sealed first-review baseline/verdict ref>         |
| preceding result / outstanding IDs | <result ref + exact IDs | N/A for baseline> |
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
- candidate tree: <plane-stamped accumulated master tree; never caller-supplied>
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
| <ID>             | <open>           | <exact refs>        | <fixed | unfixed> | <present | removed> |

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
| review mode     | <baseline | fix-verification>                |
| sealed baseline | <sealed first-review baseline/verdict ref>         |
| preceding result / outstanding IDs | <result ref + exact IDs | N/A for baseline> |
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
- regressions vs the past (route indexes · CGC · GrepAI): none | <finding>
- backing evidence file: <quality/impact report path>

## Lens 3 — Onboarding vs Code
- changed files' sidecars refreshed same pass: yes | <gaps>
- affected c-05 onboarding checks + full `memory_quality_check` curation result: pass | failed | blocked | not-run — <finding/returned codes>
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
| <ID>             | <open>           | <exact refs>        | <fixed | unfixed> | <present | removed> |

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
| review mode     | <baseline | fix-verification>                |
| sealed baseline | <sealed first-review baseline/verdict ref>         |
| preceding result / outstanding IDs | <result ref + exact IDs | N/A for baseline> |
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
- branch-wide regressions vs the past and across masters (route indexes · CGC · GrepAI): none | <finding>
- backing evidence file: <quality/impact report path>

## Lens 3 — Onboarding vs Code
- changed files' sidecars refreshed same pass: yes | <gaps>
- affected c-05 onboarding checks + full `memory_quality_check` curation result: pass | failed | blocked | not-run — <finding/returned codes>
- drift reporting when the workflow requests it: pass | failed | blocked | not-run — <request/result if any>
- route overviews describe the accumulated behavior: yes | <finding>
- C-11 carry-over and ledger mapping coherent for the final super branch: yes | <finding>
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
| <ID>             | <open>           | <exact refs>        | <fixed | unfixed> | <present | removed> |

## If BLOCK — Orchestrator Fix Leaves
1. <baseline: fix leaf routed by the orchestrator, including an owning/reopened/new scoped fix leaf · scope · target files/docs · evidence refs · done-when; fix-verification: the existing owner and sealed outstanding issue IDs only>
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
change set (or the orchestration-task draft), and for a leaf use the direct Worker repair section
instead of a Manager fix-leaf request. Keep everything else — recommendation, Criteria
Catalog Results (the loop's bound catalogs, e.g. `plan-review` + `report-verification` for a plan
review), ranked refute-tested findings, and fix decomposition. The verdict remains **evidence to
the loop owner, never a decision**. Keep the mandatory Requirement Adjudication section and one
block per stable ID + version. A delta-verify appends a dated delta section to this same artifact, rechecks
only the sealed outstanding IDs and their worker fixes, records a fixed/unfixed disposition for
every preceding ID, and retains earlier accepted adjudications rather than opening a new full
review. Outside-list observations, changed deliveries, new routes, and newly discovered criteria
return to the developer rather than becoming successor findings. A successor never adds, reopens,
or resets an issue. At the settings limit, request authorization as `../operations/review.md` states before any extra review. Verifying repaired items is an ordinary round; no broader extra-round authority exists beyond the recorded approval.

## Handover

Messages wake; files carry. A message says what is asked or what is handed over, and names the files: evidence, verdicts, lists and logs are files, never pasted into a message. The full text goes in `body` and its files in `attach`; the tool files every message in the `messages` folder of the narrowest task the two agents share, and a leaf workspace links that folder as `messages`. The recipient reads the message and, where a file belongs to it, one last line naming the file and its SHA-256. A text holds at most the limit of the settings. A return goes to the parent's role with its task references, never to an id read from an earlier message.
