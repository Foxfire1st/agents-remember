# Turn-Report Template

The **mandatory** artifact a worker writes at **every** hand-off (`roles/worker.md`). It is how the
leaf's work survives the session's death and how a respawned successor onboards from **state, not the
transcript**. A missing turn report is nudged by the HFX2-L2 agent-notifier sweep, never by a
manager watching for it (uniform-mechanism ruling 2026-07-07).

## Rules

1. Write it in the **main loop**, from your own work plus any sub-agent summaries — never delegate it to
   a sub-agent (it is the leaf's single artifact of record).
2. State facts, not a narrative: what changed, what broke, what is proven green, what remains.
3. The **Respawn State** section must let a fresh successor continue **without reading any transcript**.
4. Keep it durable and in the series notes; reference it from the leaf `task_doc`.
5. Default path: `notes/reports/<leaf-id>-worker-report.md`. The control-plane helper exposes this
   convention so manager nudges and respawn onboarding can point at the same artifact.
6. Include one complete **Requirement Acceptance Envelope** block for the leaf-owned primary stable
   requirement ID + version in the brief. List dependency/preservation checks separately; they are
   not closure claims. General prose or an aggregate completion statement is invalid.
7. Include the explicit **Checks** section even when no check ran. The header row is only a summary;
   it never replaces exact commands, results, and durable evidence references.
8. Keep the **Durable-Evidence Promotion Hold Point** separate from requirement acceptance. Neither
   can substitute for the other.
9. The report and the detailed **Requirement Attempt Journal** are distinct artifacts. The report
   links the records appended at a review handoff; the journal at
   `notes/reports/<leaf-id>-requirement-attempt-journal.md` is the single physical append-only
   authority shared as an ordered record stream. Advance an attempt only when an exact candidate is
   handed to independent review, or after reviewer rejection. Independent reviewers append separate
   adjudication records to that same journal without changing earlier bytes. Never edit or delete
   an earlier attempt; a rejected attempt's repair is a successor attempt.
10. Bind every attempt to one exact candidate. Use a Git tree/commit for code delivery and an
    artifact digest plus durable anchors for non-code-only delivery. Branch names or “latest” are
    not candidate identities.
11. Internal implementation, test, and evidence reruns are experimental protocol events, not
    delivery attempts. Preserve them separately with candidate identity, command, result, failure
    cause, repair made, and expected proof for the next run.

## Shape

```md
# Turn Report — <leaf id> · <short leaf title>

| Field        | Value                                   |
| ------------ | --------------------------------------- |
| leaf         | <leaf id / task_doc path>               |
| master       | <master id>                             |
| worker seat  | <leaf task_doc path> + worker           |
| worktree     | <branch / worktree>                     |
| status       | in-progress | leaf-complete | blocked    |
| checks       | green | failing:<which> | not-yet-run    |
| written      | <YYYY-MM-DDTHH:MM>                       |
| attempt journal | <path + exact worker attempt anchors appended for this handoff> |

## What Was Done
- <concrete change> (files: `<path>`, …)

## Requirement Attempt Journal Records Appended For This Handoff

At a review handoff, append this lightweight block to the single physical leaf journal once per
exact requirement revision and leaf manifestation. In this turn report, link each resulting journal
anchor instead of copying the record and creating a second authority. A non-review handoff records
`none — no candidate handed to review`. Prior blocks are immutable.

Validate the complete record before append. Append plus exact-candidate review handoff is one
logical formal-attempt boundary. A malformed pre-handoff row remains preserved with an append-only
`non-attempt-correction`/void reference, consumes no attempt ID, and the corrected record uses the
same next ID at handoff. A malformed handed-off row requires independent reviewer rejection before
the next candidate may appear as a successor attempt.

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

Allowed classes are exactly `implementation defect`, `evidence gap`, `requirement
contradiction/overconstraint`, `test/tool defect`, and `external blocker`. Resolution state is
`fixed`, `still-open`, or `revision-requested`.

The referenced frozen expanded-evidence artifact carries shared definitions, the complete
acceptance-envelope corpus, and complete command results. Do not duplicate that corpus or the
experimental-protocol log inside every attempt record.

## Requirement Acceptance Envelope (exactly once for the owned primary stable ID + version)

This complete envelope belongs in the expanded handoff evidence and is frozen once with a content
digest. Each lightweight worker attempt cites its exact requirement anchor in that artifact.

### <stable requirement ID> @ <version> — <exact requirement label>

- Canonical packet inspected: <version-addressed path + matching ID/version + approved state +
  durable corpus-ruling citation>

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

Use one row for each internal implementation, test, or evidence run that materially informs the
next run. These rows never consume a worker-attempt ID and never appear as reviewer adjudications.

| Event | Candidate identity | Exact command | Result | Failure cause | Repair made | Expected proof next run |
| ----- | ------------------ | ------------- | ------ | ------------- | ----------- | ----------------------- |
| <event id> | <commit/tree/digest> | `<verbatim command>` | <exit/result> | <cause or none> | <repair or none> | <falsifiable expectation> |

## Checks

| Check | Exact command | Result | Durable evidence reference |
| ----- | ------------- | ------ | -------------------------- |
| <name or none> | `<verbatim command or N/A>` | <exit/result or not-run reason> | <path + section, artifact ref, or N/A> |

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

## Curator Handoff
- Changed paths: <exact list>
- Material routes and surrounding owners: <exact list>
- Onboarding observations: <evidence/candidate for curator reconciliation | none>

## Retrieval Evidence

| Strategy | Calls | Files/anchors inspected | Remaining gap |
| -------- | ----- | ----------------------- | ------------- |
| Intent (`read_ar_files`) | <count> | <paths> | <none or gap> |
| Semantics (`grepai_search`) | <count> | <results> | <none, gap, or unavailable> |
| Relationship (`cgc_*`) | <count> | <symbols/routes> | <none, gap, or unavailable> |

## Escalations
- <plan delta beyond blank-filling raised to the manager> | none

## Respawn State (onboard a successor from this — no transcript needed)
- Current position in the leaf plan:
- Files touched so far:
- The one thing a successor must know before editing:
- Uncommitted vs committed state:
```
