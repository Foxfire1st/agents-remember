# Operation — Review

**What it covers:** the requested independent review seam and its exact mode contract — what a
baseline seals, what a successor may verify, and how the verdict is recorded and consumed.

**When it is selected:** the developer or the approved task/role brief requests review at a seam
(leaf route review, master-exit, super-exit, portfolio plan, or a three-party loop's reviewer seat).
**It is never selected by default**: closeout and integration neither require nor launch it.

## Who carries it, and their job

| Role | Its job in this operation |
| --- | --- |
| reviewer | the reviewing seat: scope the seam, run the phase-appropriate evidence work, adjudicate each requirement revision, write the verdict |
| manager | dispatches leaf/route and master-exit reviews, carries the mode in every brief, and calls `begin_review` / `record_review` |
| orchestrator | dispatches the super-exit review and decides the delegated master-handover gate the verdict attaches to |
| architect | owns plan review, rules its verdict, and carries the developer ruling at super-exit |

The reviewing seat may not be the author/implementer seat, and no seat reviews its own work as the
"independent" reviewer.

## Required inputs

The dispatch brief must name **exactly one** review mode plus the canonical task and review purpose.

| Mode | Inputs |
| --- | --- |
| `reviewMode=baseline` | the complete agreed scope; all applicable standing criteria (`../criteria/`); required routes/lenses; the evidence needed to inspect them. If no review state is present, the used-round count is zero and the next review is a baseline — do not create a pristine marker or refuse because legacy history is absent. |
| `reviewMode=fix-verification` | the sealed baseline; the immediately preceding result; the exact outstanding IDs; and the worker fixes/evidence. |

Every review also needs: the exact stable-ID + version requirement set dispatched to the builder,
with its canonical packets and durable corpus ruling; the candidate identity (tree/commit, or a
non-code digest + anchors); the worker attempt records; and the resolved `system/tools.md`.

## Normal workflow

1. **Confirm the mode and the claimed scope** before inspecting anything. A successor that receives
   a whole-review mandate refuses it.
2. **Baseline only: partition the agreed scope by material major route** (architecture/control-plane
   ownership boundary, informed by governing route overviews and the import/call graph), name one
   independent reviewer per affected major route, and require a **complete route-coverage table** so
   no route disappears inside a generic diff skim. One reviewer may not silently collapse several
   routes.
3. **Run the phase-appropriate evidence work.**
   - Baseline runs its type's standing catalog from `../criteria/` (the regression floor), all three
     lenses, the required routes, and the exploratory mandate, with durable reports. Every standing
     criterion is reported, even to say it found nothing.
   - Fix-verification uses the sealed baseline evidence and only the listed IDs. It has **no**
     exploratory mandate, whole-catalog rediscovery, new-lens duty, or catalog-promotion authority.
   - Posture is always **refute-or-confirm**: try to disprove the claim; a claim that cannot survive
     refutation is not accepted.
4. **Seal the baseline (baseline only).** Before a first verdict can pass, record stable issue IDs,
   precise problem statements, source/requirement evidence, and observable fix-acceptance criteria.
   An empty first-pass issue list is a valid terminating baseline; it does not create a reason for
   another review.
5. **Adjudicate every requirement revision separately** as exactly `accepted` or `rejected`, against
   one exact worker attempt and candidate, with the reviewer's own rationale and refutation attempt.
   An aggregate verdict or a sampled subset is invalid.
6. **Write a fixed/unfixed disposition for every preceding outstanding ID** (successor only). The
   remaining set must be a subset of both the preceding remaining set and the sealed baseline.
   Omission, unknown, duplicate, rewritten, reintroduced, or newly discovered IDs, a new criterion
   under an old ID, a full-review request, or a pass with unresolved IDs is a **blocking protocol
   refusal**.
7. **Append the reviewer record** to the same single physical leaf journal, against that exact
   attempt and candidate, without modifying the worker record or any earlier bytes; link that exact
   journal anchor from the verdict rather than copying the record.
8. **Write the verdict artifact** in the shape of `../templates/verdict.md` for the matching seam
   variant, including an explicit pass/block recommendation and the durable-evidence checklist
   output even when it is `N/A`.
9. **The owner records the result** with `task_doc(operation="record_review")` or the existing
   `task_doc(operation="record_route_review")` after the durable verdict and route evidence exist.
   The task document remains the review authority; prose, a chat claim, or an unbound evidence
   reference does not satisfy a requested review.

## Authority gates

- **Verdicts are evidence, not decisions.** The reviewer never decides a gate and never implements;
  the decider decides. The verdict attaches to the handover gate as judge evidence, and the policy
  may require that evidence when `requireReviewerVerdictAtSeams` is set.
- **`task_doc(operation="begin_review")` is called before** hosted reviewer dispatch or native
  reviewer work; the result is recorded after.
- **The verdict cannot pass** while any requirement is rejected. A truthful `blocked` row may be
  `accepted` as a handoff state, but the overall recommendation stays BLOCK until the requirement
  becomes `satisfied` or an authorized `approved-change`.
- **Evidence must match the requirement's class**: rendering/visibility needs mounted-UI proof;
  scheduling/ordering needs operation-level proof; data-model/persisted shape needs artifact-level
  proof; doctrine/enforcement needs a code anchor. Evidence of the wrong class is a finding, never a
  pass.
- **A requirement contradiction/overconstraint** is a rejection that requests architect/developer
  revision authority; the reviewer never rewrites the packet or accepts a workaround as changed
  semantics.
- **A baseline block must be decomposable into leaf-shaped fix leaves** — concrete scope, target
  files/docs, evidence, and done-when. A baseline block that cannot be named as fix leaves is not
  yet a first-review block. A fix-verification block returns only the sealed outstanding IDs to
  their existing owners and **cannot create a new fix leaf**.
- **The promotion ratchet duty belongs to the first review only**; a successor may not propose a
  catalog amendment.

## Failure handling

- **A malformed handed-off worker row** is a formal attempt: reject it independently as an evidence
  gap or the applicable exact failure class. Do not edit it, and require a successor only when the
  worker hands off the next exact candidate.
- **A missing, unapproved, or mismatched packet version** is an invalid citation and forces
  rejection. If the corpus shows a newer approved version, reject stale acceptance and require the
  affected leaf to be rebriefed.
- **A candidate that moved during review** makes the attempt stale: reject it and require a
  successor worker attempt plus reviewer record.
- **An un-reviewable change set** (missing diff, missing task docs) is itself a blocking finding in
  the verdict, routed to the decider — not an escalation up the ladder.
- **An outside-list regression, changed route, or changed requirement** during successor
  verification goes directly to the developer; it does not add, reopen, or reset a review.
- **At three rounds**, ask the developer directly and wait for explicit authorization; record the
  instruction before any extra round.

## Handoff / exit

The verdict artifact is the durable handoff. Terminal/finalizer truth then attests only that the
reviewer's turn ended and wakes the decider, who validates the verdict independently. The reviewer
does not author a second completion row and does not carry the decider's runtime identity.
