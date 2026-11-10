# Reviewer baseline review

The Reviewer uses this file when partitioning routes, checking evidence and sealing the first review's findings. It also states how to select focused tests.

## Partition the routes

Organise the route work yourself, using sub-agents if your harness offers them. No particular harness arrangement is required.

## Check baseline evidence

Run the bound catalogs, required routes and exploratory mandate. Cover all three lenses explicitly, even to say a lens is clean: completion against task documents, scoped implementation evidence, and onboarding against code. Try to refute each claim before confirming it.

Match evidence to the requirement's class:

- Rendering or visibility needs proof from the mounted user interface.
- Scheduling or ordering needs operation-level proof.
- A persisted shape needs the parsed artifact.
- Doctrine needs the file and the mechanism enforcing the rule.

Evidence of the wrong class is a finding and cannot support PASS.

## Distinguish recorded knowledge from findings

A weakness record is never a finding, and a document's staleness is never a finding. A weakness found outside the task's scope and a document standing on code the task changed are recorded knowledge. They refuse no review, gate or closeout.

An unrepaired weakness inside the task's scope is a finding. So is a document the task changed whose dependent record is not confirmed. A weakness record never substitutes for such a finding.

## Seal the baseline

An empty first-pass issue list is a valid terminating baseline.

## Select focused tests

Choose focused tests across every changed surface and relevant gate. For full-suite authority, follow [Inspect and verify the candidate](../roles/reviewer.md#inspect-and-verify-the-candidate).
