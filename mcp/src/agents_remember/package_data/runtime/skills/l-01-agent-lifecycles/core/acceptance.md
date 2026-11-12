# Core — Completion and handoff acceptance

This authoring reference defines the difference between a completed turn, a delivered artifact and acceptance by its owner. Authors and reviewers use it to preserve that distinction in each role's instructions.

## Separate completed execution from accepted work

A canonical terminal or finalizer outcome of `completed` means only that the provider turn ended normally. It does not establish that the required artifact exists, is current or satisfies its requirement. An agent can violate its artifact duty while its turn still ends as `completed`. An `interrupted` outcome remains an interruption, not a completed handoff.

Role files state their own seat's part of this boundary without restating the whole rule.
This file changes no approval of requirements, plans, commits or integration.

## Keep relay and gate-owner duties separate

The host reads that a turn ended and derives the mechanical seat-state signal. It delivers one line to the structurally current owner waiting for that agent, carrying the ended agent's last line. The relay never opens, parses or evaluates reports, verdicts, coherence records or acceptance envelopes. It never nudges a seat based on its own judgment of an artifact.

A silent leaf seat is first sent back under the host's turn-end rules. A line sent to the parent is not a report inspection. The relay neither validates artifact content nor invents a gate decision.

On wake, the current gate owner opens and validates the required artifact, candidate identity, evidence and acceptance envelope before advancing lifecycle state. The Manager owns a leaf gate. Worker, Reviewer and Curator handovers go directly between those peers: the recipient checks the exact object and requested work, while the Manager retains gate acceptance. Take closeout trees by filename from the highest-numbered memory PASS and landing-check verdict, never from a message.

The owner detects a missing, malformed or stale handoff artifact after wake. Under the existing rules, it nudges, rejects, replaces or escalates instead of waiting for the relay to check the artifact.

## Write and send the handoff artifact

Worker, Reviewer and Curator write their durable handoff artifact before ending a successful handoff turn. They then send it with `role_message` to the waiting agent. A filed artifact wakes nobody by itself. Name the finished object and its evidence file. A finished turn or a filed report alone does not notify it.

| Seat | Durable handoff artifact | Validated by |
| --- | --- | --- |
| Worker | Turn report and leaf Requirement Attempt Journal records | Reviewer for code; Manager for the gate |
| Reviewer | Verdict artifact | Decision-maker for the gate to which the verdict belongs |
| Curator | Structured coherence record and its generated projection | Reviewer for memory; Manager for the gate |
| Investigator | Investigation report | Parent that started it, or the developer |
| Manager | Master-handover packet and owned records | Named coordination recipient; without a parent, the developer |
| Orchestrator | Super-exit packet and demo notes | Architect who owns the developer conversation |

Every seat writes its artifact before ending a successful handoff turn, then ends. It authors no second, model-written completion row and carries neither the decision-maker's nor the owner's runtime identity.

## Bind acceptance to one requirement revision

Before dispatch, the owner puts the leaf's one owned primary revision in the builder brief: stable identifier, version, approved packet and durable approval record citation: the requirement manifest's entry with its `state` set to `approved`, its `approval` and `sha256` content hash, and the manifest's hash for the whole requirement corpus. It lists dependency and preservation context separately. Adjacent context is checked as a constraint; the leaf cannot claim to close it.

The builder's handoff contains one acceptance block for that primary revision:

1. Status: exactly `satisfied`, `blocked` or `approved-change`.
2. Delivery or implementation rationale: what was delivered and why it satisfies the requirement.
3. Delivery or implementation citations. For code, use the file path and symbol. For other work, use the deliverable path and an appropriate section or anchor.
4. Verification rationale: the behavior demonstrated by the evidence and the failure it would catch.
5. Verification citations: file path and test symbol, executable node, report section or another exact anchor appropriate to the evidence.
6. Exact command and result, or a durable evidence reference containing both.

A `blocked` or `approved-change` block also explains why the original requirement cannot be delivered unchanged. It describes the changed delivery, when one exists, and cites the durable developer approval or ruling. A new blocker may be reported while approval is pending; that block is explicitly incomplete and cannot pass review.

A `satisfied` claim is invalid when any required rationale, citation or exact evidence is missing. General prose or an aggregate “requirements addressed” claim is not an acceptance envelope.

## Independently judge the exact manifestation

The independent Reviewer inspects the primary packet revision and cited artifacts itself. It judges that exact manifestation as `accepted` or `rejected` and supplies its own rationale.

Missing rationale, an unapproved packet revision, missing evidence, evidence of the wrong class or invalid citations requires rejection. The overall verdict cannot pass while a requirement is rejected.

An accurately reported `blocked` row may be accepted as a truthful handoff. It still requires a `BLOCK` recommendation until the requirement is delivered or becomes an approved change.

## Keep requirement revisions and attempts distinct

The canonical `ID@version` identifies semantic intent and changes only through explicit developer approval. A leaf-local attempt identifier records the exact candidate handed to independent review for one manifestation of that revision.

The builder advances the attempt identifier only when handing a candidate to review, or when Reviewer rejection requires a successor handoff.
Internal implementation, test and evidence reruns do not create attempts. Record those runs separately as experimental protocol events: candidate identity, command, result, failure cause, repair and expected proof for the next run.

### Append the builder's immutable record

Before review handoff, the builder appends an immutable Worker attempt record to the leaf's detailed journal. It binds:

- The revision and manifestation.
- The predecessor and carried findings, when present.
- The exact candidate tree or commit, or suitable non-code digest and anchors.
- Its requirement-specific status, rationale, citations, findings and failure class.
- A content-addressed reference to immutable expanded evidence.

The frozen expanded artifact carries shared definitions and complete command results. Do not duplicate the complete master acceptance envelope or experimental-run body in every attempt.

Validate the complete record before appending it. Append and exact-candidate review handoff form one logical formal-attempt boundary.

Preserve a malformed record that has not been handed over, and append a `non-attempt-correction`/void reference. It consumes no attempt identifier: the corrected record uses the same next identifier at handoff.

A malformed record already handed over is a formal attempt. The independent Reviewer rejects it; the Worker may append a successor only at the next review handoff. The Worker never rejects its own record or silently replaces either kind of record.

### Append the independent review record

After inspecting the artifacts itself, the independent Reviewer appends its own record against the exact attempt and candidate. It chooses `accepted` or `rejected`, gives its own rationale and citations, and assigns each rejection finding exactly one class:

- `implementation defect`
- `evidence gap`
- `requirement contradiction/overconstraint`
- `test/tool defect`
- `external blocker`

The Reviewer never modifies the Worker record. Acceptance never carries forward to a later candidate.

### Preserve closed attempts and sealed findings

Rejection closes the attempt. A repair appends a successor citing its predecessor and sealed findings. Accepted attempts also remain closed.

Use the two sanctioned routes for adding a finding after the list is sealed, defined in the [review operation's Boundaries](../operations/review.md#boundaries). Send all other observations outside the findings list and changed promises through the parent to the developer; do not add or reopen findings or reset review. The same-Reviewer and shrinking-list rules remain, subject to those defined routes.

Reject a requirement contradiction or overconstraint and route it through the Architect for a developer-approved revision. Builders and Reviewers may propose a revision; they never rewrite or approve one themselves.

## Keep the master summary rebuildable

The detailed Worker and Reviewer records for each leaf are authoritative. The master keeps a summary linking them and showing attempts, rejection history, current state and the dominant open failure class for each requirement manifestation.

This summary is a disposable observation, not a requirement contract, lifecycle or closeout gate, queue authority or task-authoring lock. Rebuild it from leaf journals when missing or stale; it never blocks work.

## Record how retained evidence will be maintained

For each new or retained fixture, recording, generator, shared support file or migration proof, the handoff records one of these sets:

1. Registered stable contract identity, real owner, executable evidence node and exact consumers.
2. Expiry date, executable replacement or removal event, owner and compatibility consequence.

For example, a retained provider frame may become part of a named wire-version-matrix contract. A migration comparison with an expiry date must name an exact `node:...::test_replacement` and removal event. “Useful later” satisfies neither option.

Missing or contradictory catalog metadata requires implementation work. It is never just a review note or tool blocker.

This obligation is independent of requirement acceptance. Valid contract or expiry information cannot replace requirement rationale or verification proof. A satisfied requirement cannot waive missing lifecycle metadata for durable evidence.

Worker, Reviewer, Curator: At the end of each round, pack that round's bulk evidence (captures, logs, generated packages) into one archive in your report folder, with a manifest of its files and hashes, and keep loose only the archive, its manifest and the files the next step reads.
