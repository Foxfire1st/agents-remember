# Core — Completion Truth And Handoff Acceptance (one home — this file owns the truth boundary)

**Terminal truth is mechanical; acceptance is the owner's.** A canonical terminal/finalizer outcome
of `completed` means only that the provider turn ended normally. It does **not** attest that the
required artifact exists, is current, or satisfies its requirement — a subordinate can violate its
artifact obligation and still produce mechanical `completed` truth. A terminal `interrupted` outcome
remains an interruption, not a completed handoff. Role files state their own seat's side of this
boundary; they do not restate the whole of it.

- **The relay derives and delivers the state signal.** The lifecycle-owned relay reads turn truth,
  derives that mechanical seat-state fact, and delivers it to the structurally current owner. It
  never opens, parses, or evaluates a report, verdict, coherence record, expectation row, or
  acceptance envelope, and it never nudges a seat on its own judgment about an artifact.
- **The owner alone validates.** On wake, the current owner opens the required artifact, candidate
  identity, evidence, and acceptance envelope and validates them **before advancing lifecycle
  state**. For a leaf handoff that owner is the manager.
- **An artifact defect is owner-detected.** A missing, malformed, or stale artifact is a handoff
  defect the owner finds after wake; it nudges, rejects, replaces, or escalates under existing
  doctrine instead of waiting for an imaginary notifier artifact check. If terminal truth is
  unavailable, observer health identifies that runtime failure.
- **The artifact obligation is unchanged.** Worker, reviewer, and curator still write their durable
  handoff artifact before intentionally ending a successful handoff turn. Once that artifact exists
  and the turn ends, the subordinate needs no second model-authored completion post.
- **Nothing here moves authority.** This file states the boundary that already holds; it changes no
  approval of requirements, plans, commits, or integration.

## Which artifact each seat hands over, and who validates

| Seat | Durable handoff artifact | Validated by |
| --- | --- | --- |
| worker | the turn report + the leaf Requirement Attempt Journal records | the owning manager |
| reviewer | the verdict artifact | the decider of the gate the verdict attaches to |
| curator | the structured coherence record and its generated projection | the owning manager |
| strategist | the orchestration-task draft | the architect (who rules it) |
| designer | the task_doc + the designer-limits note | the architect |
| system-specialist | the investigation report | the orchestrator |
| manager | the master-handover packet | the orchestrator |
| orchestrator | the super-exit packet and demo notes | the architect |

A seat writes its artifact **before** ending a successful handoff turn, then ends. It does not
author a second, model-written completion row, and it does not carry the decider's or the owner's
runtime identity.

## Acceptance is per stable ID and version, never aggregate

Before dispatch, the owner projects the leaf's one owned primary revision — with its stable ID +
version, approved packet, and durable corpus-ruling citation — plus separately labelled
dependency/preservation context into the builder brief. Adjacent context is verified as a constraint
and cannot be claimed closed by this leaf.

The builder's handoff contains **one** acceptance block for the owned primary revision:

1. status: exactly `satisfied`, `blocked`, or `approved-change`;
2. delivery/implementation rationale explaining what was delivered and why it satisfies the
   requirement;
3. delivery/implementation citations — code uses file path + symbol; non-code work uses the
   deliverable path + section/anchor appropriate to that artifact;
4. verification rationale explaining what behavior the evidence demonstrates and **which failure it
   would catch**;
5. test/verification citations using file path + test symbol, executable node, report section, or
   other exact verification anchor appropriate to the evidence;
6. the exact command and result, or a durable evidence reference that contains them.

A `blocked` or `approved-change` block also explains why the original requirement cannot be
delivered unchanged, describes the changed delivery when one exists, and cites the durable developer
approval/ruling. A new blocker may be reported while approval is pending, but that block is
explicitly incomplete and cannot pass review. **`satisfied` is invalid when any required rationale,
citation, or exact evidence is absent.** General prose or an aggregate "requirements addressed"
claim is not an acceptance envelope.

The independent reviewer inspects the owned primary packet revision and the cited artifacts itself
and adjudicates that exact manifestation as `accepted` or `rejected`, with its own rationale.
Missing rationale, an unapproved packet revision, missing or wrong-class evidence, or invalid
citations forces rejection of that requirement; the overall verdict cannot pass while any
requirement is rejected. An accurately reported `blocked` row may be accepted as a truthful
handoff, but it still requires a BLOCK recommendation until the requirement is delivered or becomes
an approved change.

## Requirement revisions and delivery attempts are separate axes

The canonical `ID@version` states semantic intent and changes only through explicit developer
approval. A leaf-local attempt ID states what one exact candidate was handed to independent review
for one leaf manifestation of that revision.

- The builder advances that ID **only** when handing a candidate to review, or when a reviewer
  rejection requires a successor handoff.
- Internal implementation, test, and evidence reruns do **not** mint attempts. Preserve them
  separately as experimental protocol events with the candidate identity, command, result, failure
  cause, repair, and expected proof for the next run.
- Before review handoff, the builder appends an immutable worker attempt record to the leaf's
  detailed journal. It binds the revision, manifestation, predecessor and carried findings when
  present, exact candidate tree/commit or appropriate non-code digest/anchors, and its own
  requirement-specific status, rationale, citations, findings, failure class, and a
  content-addressed reference to immutable expanded evidence. The frozen expanded artifact carries
  shared definitions and complete command results; do not duplicate the complete master acceptance
  envelope or experimental-run body inside every attempt.
- Validate the complete record before append. Append plus exact-candidate review handoff is one
  logical formal-attempt boundary. A malformed pre-handoff row is preserved, receives an
  append-only `non-attempt-correction`/void reference, and consumes no attempt ID; the corrected row
  uses that same next ID at handoff. A malformed handed-off row is already a formal attempt: the
  independent reviewer rejects it, and the worker may append a successor only at the next review
  handoff. The worker never self-rejects or silently replaces either row.
- The independent reviewer appends a separate reviewer record against that exact attempt and exact
  candidate after inspecting the artifacts itself. It chooses `accepted` or `rejected`, supplies its
  own rationale/citations, and classifies every rejection finding as exactly one of
  `implementation defect`, `evidence gap`, `requirement contradiction/overconstraint`, `test/tool
  defect`, or `external blocker`. It never modifies the worker record, and acceptance never floats
  to a later candidate.
- Rejection closes that attempt; a repair appends a successor citing the predecessor and listed
  findings. Accepted attempts stay closed. During successor verification, an outside-list
  regression, changed route, or changed requirement goes directly to the developer; it does not add
  a finding, reopen a resolved item, or reset the review. Same-reviewer verification and shrinking
  findings stay in force; an architect takeover continues the same attempt lineage.
- A requirement contradiction/overconstraint is rejected and routed through the architect for
  developer-approved revision; builders and reviewers may propose a revision but never rewrite or
  approve one.

## The master-level summary is disposable

The detailed per-leaf worker and reviewer records are authority. A master maintains a rebuildable
summary linking those records and showing attempts, rejection history, current state, and dominant
open failure class per requirement manifestation. The summary is a disposable observation only: it
is never a requirement contract, lifecycle/closeout gate, queue authority, or task-authoring lock.
Missing or stale summary state is rebuilt from leaf journals and cannot block work.

## The durable-evidence promotion hold point (a separate concern)

For each newly created or retained fixture, recording, generator, shared support file, or migration
proof, the handoff records either:

1. the registered stable contract identity, real owner, executable evidence node, and exact
   consumers; or
2. the expiry date, executable replacement/removal event, owner, and compatibility consequence.

For example, a retained provider frame may graduate to
`contract:codex-agent-wire-version-matrix`; a migration comparison expiring on `2026-09-30` must
name an exact `node:...::test_replacement` and removal event. "Useful later" is neither option. A
missing or contradictory catalog row is implementation work, never a review note or tool blocker.

This hold point is **independent of requirement acceptance**: a valid stable-contract-or-expiry
disposition cannot fill a missing requirement rationale or verification proof, and a satisfied
requirement cannot waive missing lifecycle metadata for durable evidence.
