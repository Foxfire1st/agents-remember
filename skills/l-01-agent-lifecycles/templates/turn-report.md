# Turn-Report Template

The Worker uses this template for the mandatory report at every handoff. The Manager, Reviewer, developer and a successor use it to inspect the leaf's work and continue from recorded state without the transcript.

In this template, AR means Agents Remember and ID means identifier.

This template supplies the report and journal authoring shapes. The [acceptance reference](../core/acceptance.md) owns acceptance, and [final-candidate verification](../operations/implementation.md#verify-the-final-candidate) owns targeted handover checks.

## Rules

1. Write it in the **main loop**, from your own work plus any sub-agent summaries — never delegate it to
   a sub-agent (it is the leaf's single artifact of record).
2. State facts, not a narrative: what changed, what broke, what is proven green, what remains.
3. The **Respawn State** section must let a fresh successor continue **without reading any transcript**.
4. Write the durable report at the local path the handover names, reference it from the leaf `task_doc`, and retain its canonical destination in the record.
5. Before writing the primary acceptance block, read [Bind acceptance to one requirement revision](../core/acceptance.md#bind-acceptance-to-one-requirement-revision) for its required fields; use the shape below.
6. Include the explicit **Checks** section even when no check ran. Its summary never replaces exact commands, results and durable evidence references.
7. Before recording retained evidence, read [Record how retained evidence will be maintained](../core/acceptance.md#record-how-retained-evidence-will-be-maintained) for the contract-or-expiry information, separate from acceptance.
8. Before appending the leaf journal, read [Keep requirement revisions and attempts distinct](../core/acceptance.md#keep-requirement-revisions-and-attempts-distinct) for advancement, internal-run and immutable-record rules. The report links its journal anchors.
9. Record harness sub-agent contributions and the seat's verification of them. The seat owns
    boundary messages and records; its helpers hold no AR seat and supply no independent review.
10. Record each handover's recipient seat, exact candidate head/change hash, report path, requested
    whole stretch, and the reply/report evidence that identifies it and shows work or its start.
    Mark busy/unresolved handovers pending; quote the refusal or non-work reply and its Manager
    notice. An `accepted` delivery alone does not mean the stretch started.
11. Before reporting successor dispositions, read [review Boundaries](../operations/review.md#boundaries) for the sealed-list and admission rules. Record the mode, baseline, preceding result, exact identifiers, Reviewer rulings and round authority in the fields below.
12. Hand the report over as a file. Before composing a message, read [Send a message that names the files](../reference/message-handover.md#send-a-message-that-names-the-files) for the sender and payload rules.

Before handling a full-store refusal, read [full-store receipt handling](../reference/message-handover.md#handle-a-full-store-refusal) for the pending record, one resend and second-refusal report.

## Shape

```md
# Turn Report — <leaf id> · <short leaf title>

| Field        | Value                                   |
| ------------ | --------------------------------------- |
| leaf         | <leaf id / task_doc path>               |
| master       | <master id>                             |
| worker seat  | <leaf task_doc path> + worker           |
| worktree     | <branch / worktree>                     |
| status       | in-progress \| leaf-complete \| blocked    |
| checks       | green \| failing:<which> \| not-yet-run    |
| review mode | baseline \| fix-verification |
| sealed baseline / preceding result | <sealed baseline ref> / <prior result ref or N/A> |
| outstanding IDs | <exact IDs and prior remaining-set digest \| none for baseline> |
| written      | <YYYY-MM-DDTHH:MM>                       |
| attempt journal | <path + exact worker attempt anchors appended for this handoff> |

## What Was Done
- <concrete change> (files: `<path>`, …)

## Requirement Attempt Journal Records Appended For This Handoff

Before appending the journal record, follow [requirement revisions and attempts](../core/acceptance.md#keep-requirement-revisions-and-attempts-distinct) for immutable records, advancement and internal-run distinctions. Link each resulting journal anchor rather than copying the authoritative record. A non-review handoff records `none — no candidate handed to review`.

| Requirement revision | Leaf manifestation | Worker attempt ID | Authoritative journal record |
| -------------------- | ------------------ | ----------------- | ---------------------------- |
| <ID@version> | <leaf-id>/<ID@version> | <attempt id> | <journal path + anchor> |

### Worker Delivery Record To Append To The Leaf Journal

This block is transient authoring input only. Append it to the authoritative leaf journal, then
remove this rendered scaffold from the completed turn report and retain only the exact journal
anchor in the table above. It must never remain as a duplicate `Worker Attempt Record` authority.

### Worker Attempt Record — <attempt id>

- Record kind: `worker-delivery-attempt`
- Requirement revision: `<stable requirement ID>@<version>`
- Leaf manifestation: `<leaf-id>/<stable requirement ID>@<version>`
- Attempt ID: `<leaf-local monotonically ordered id>`
- Predecessor attempt: `<attempt id | none>`
- Exact candidate: `<Git tree/commit | non-code artifact digest + durable anchors>`
- Requirement-specific status: `satisfied` | `blocked` | `approved-change`
- Delivery and verification rationale: `<concise requirement-specific rationale>`
- Deliverable and verification citations: `<exact symbols/anchors>`
- Findings and failure class: `<finding IDs + exact class | none>`
- Expanded evidence artifact: `<immutable path + content-addressed sha256 digest + exact requirement anchor>`
- Record appended at: `<YYYY-MM-DDTHH:MM>`
- Carried findings:

| Finding ID | Prior class | Resolution in this attempt | Evidence or still-open reason |
| ---------- | ----------- | -------------------------- | ----------------------------- |
| <id or none> | <one exact failure class> | <resolution state> | <exact anchor> |

For `fix-verification`, fill this table for every preceding outstanding identifier under [review boundaries](../operations/review.md#boundaries) for the sealed list and admitted successor findings.

Before assigning a failure class, read [Append the independent review record](../core/acceptance.md#append-the-independent-review-record) for the five classes. Resolution state is `fixed`, `still-open`, or `revision-requested`.

The referenced frozen expanded-evidence artifact carries shared definitions, the complete
acceptance-envelope corpus, and complete command results. Do not duplicate that corpus or the
experimental-protocol log inside every attempt record.

## Requirement Acceptance Envelope (exactly once for the owned primary stable ID + version)

This complete envelope belongs in the expanded handoff evidence and is frozen once with a content
digest. Each lightweight worker attempt cites its exact requirement anchor in that artifact. Before inspecting the canonical packet, read [the approval-record definition](../core/every-role.md#requirements-and-limits) for the approved manifest entry, its content hash and the corpus hash.

### <stable requirement ID> @ <version> — <exact requirement label>

- Canonical packet inspected: <version-addressed path + matching ID/version + approved state +
  durable approval record citation>

- Status: `satisfied` | `blocked` | `approved-change`
- Delivery/implementation rationale: <what was delivered and why it satisfies the requirement>
- Delivery/implementation citations:
  - <code: `path` — `symbol`; non-code: `path` — `section/anchor`; repeat as needed>
- Verification rationale:
  - Demonstrated behavior: <what the evidence proves>
  - Failure caught: <what regression, omission, or wrong behavior would make this evidence fail>
- Test/verification citations:
  - <`path` — `test symbol/node`, report section, scenario anchor, or other exact evidence>
- Exact evidence: `<command>` → `<result>` | <durable evidence reference containing both>
- Exception details (`blocked` or `approved-change` only):
  - Why unchanged delivery is impossible: <reason | N/A for satisfied>
  - Changed delivery: <exact change | none>
  - Developer approval/ruling: <durable citation | approval pending, therefore not review-passable>
- Blocked findings (`satisfied` may state `none`):

| Finding ID | Failure class (exactly one) | Evidence | Required next action |
| ---------- | --------------------------- | -------- | -------------------- |
| <id or none> | <one exact failure class> | <anchor> | <class-owned recovery> |

## Experimental Protocol Events (separate from delivery attempts)

Record internal implementation, test and evidence runs in the following fields under [requirement revisions and attempts](../core/acceptance.md#keep-requirement-revisions-and-attempts-distinct) for internal-run and immutable-record rules.

| Event | Candidate identity | Exact command | Result | Failure cause | Repair made | Expected proof next run |
| ----- | ------------------ | ------------- | ------ | ------------- | ----------- | ----------------------- |
| <event id> | <commit/tree/digest> | `<verbatim command>` | <exit/result> | <cause or none> | <repair or none> | <falsifiable expectation> |

## Checks

| Check | Exact command | Result | Durable evidence reference |
| ----- | ------------- | ------ | -------------------------- |
| <name or none> | `<verbatim command or N/A>` | <exit/result or not-run reason> | <path + section, artifact ref, or N/A> |

For a code handoff, this table records the relevant targeted tests selected by the worker and all
applicable repository-prescribed lint, formatting, typing, and structural checks, with their exact
scope, commands, and results. Explicitly list any relevant test or check not run and why.
These are diagnostic worker checks, separate from certification and review-round accounting.

## Durable-Evidence Promotion Hold Point (separate concern)

| Artifact | Decision | Owner + consumers | Executable stable contract or expiry/removal | Validator command + result |
| -------- | -------- | ----------------- | -------------------------------------------- | -------------------------- |
| <path or N/A> | <stable-contract, expiry, or N/A> | <owner + source-observed consumers> | <contract id + node, or date + replacement/removal> | <exact command + result> |

## Issues Hit
- <issue> → resolved: <how> | still open: <what it blocks>

## Solved On The Spot
- <blank filled / small decision made> (a plan delta beyond blank-filling is NOT here — it was
  escalated to the manager; see Escalations)

## What Is Left
- [ ] <remaining step from the leaf plan>

## Direct Handovers

| Recipient seat | Candidate head + change hash | Report/verdict path | Whole next stretch | Reply/report proves work or start | Pending/refusal/recovery |
| -------------- | ---------------------------- | ------------------- | ------------------ | ------------------------------- | ------------------------ |
| <reviewer or curator + canonical leaf refs> | <exact identity> | <path> | <requested work> | <exact identity and evidence, or pending> | <quoted refusal/reply and notice, or none> |

## Harness Contributions
- <work done by sub-agents, how the Worker verified it | none>

## Curator Handoff
- Changed paths: <exact list>
- Material routes and surrounding owners: <exact list>
- Onboarding observations: <evidence/candidate for curator reconciliation | none>

## Retrieval Evidence

| Retrieval action | Calls | Files/anchors inspected | Remaining gap |
| -------- | ----- | ----------------------- | ------------- |
| Name governing knowledge (`read_ar_files`) | <count> | <paths> | <none or gap> |
| Onboarding (native reads) | <count> | <results> | <none, gap, or unavailable> |
| Inspect route indexes | <count> | <symbols/routes> | <none, gap, or unavailable> |

## Escalations
- <plan delta beyond blank-filling raised to the manager> | none

## Respawn State (onboard a successor from this — no transcript needed)
- Current position in the leaf plan:
- Files touched so far:
- The one thing a successor must know before editing:
- Uncommitted vs committed state:
```
