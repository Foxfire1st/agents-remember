# Requirement packet template

Requirement authors use this scaffold for one version-addressed canonical promise.

Before authoring a packet, read [task prose fields](../reference/tasks/document-authoring.md#put-prose-in-the-fields-that-render-it) for prose conventions.

Before seeking packet approval, read [Prepare requirements and recover approval](../operations/planning.md#prepare-requirements-and-recover-approval) for approval context, recovery and the requirement’s owning task.

# <stable identifier> @ <version> — <observable outcome>

| Field | Value |
| --- | --- |
| Stable ID | <stable identifier> |
| Version | <version> |
| State at packet freeze | <draft or approved> |
| Developer approval | <durable ruling citation or pending> |
| Supersedes | <earlier identifier and version or none> |

## Normative Requirement

<Write the independently falsifiable promise. State who acts, when it applies and what must be observably true.>

## Problem

<Explain the concrete failure, ambiguity or missing ability and who encounters it. Give the context a cold reader needs.>

## Required Behavior

<Describe the behavior and relevant state transitions in connected prose. Make the result observable and distinguish it from a plausible incorrect result.>

## Rationale

<Explain why this behavior is needed, why the boundary fits the problem and which relevant alternatives were rejected.>

## Scope

<Name the actors, situations and surfaces covered by the promise.>

## Preservation Boundaries

<Explain the behavior, authority and evidence that this change must leave intact.>

## Forbidden Overreach

<Describe adjacent work and convenient shortcuts that fall outside or undermine this promise.>

## Failure And Recovery Behavior

<Describe each important failure, its observable answer, any admitted retry or recovery and the state that must survive.>

## Planned Structure

Before writing the planned structure, read [proposed code changes](../reference/tasks/document-authoring.md#show-the-proposed-changes) for placement paths, skeleton contracts and examples.

<For code work, where the change lives and its shape, before any code is written. Placement: the files and folders added, moved, split or removed, with their paths, following the repository's coding guidelines. Skeleton: new or changed types, modules and function signatures in the repository's language, each with a one-line contract and no body; say what calls what where that matters. Keep contracts short; a skeleton is not an implementation. For work that changes no code, say so in one line.>

## Examples

<Give a conforming case, a violating case and a boundary case. Explain why each belongs to that category. Add a short code example where a change is hard to judge from the skeleton. Add a diagram when it helps the reader follow the interaction.>

## Expected Evidence

<Describe the deliverable and verification evidence classes, the exact anchors the builder supplies and the omission or regression the verification must catch.>

## Authority And Provenance

<Name the actual intent source, durable approval and evidence used to compile this packet. Distinguish an approved promise from a draft proposal.>

## Dependencies

<Name any versioned promises this requirement needs or constrains.>

## Open Truth Gaps

<Name unresolved facts, their owner and the evidence that would settle them; or none.>

## Revision History

<Record this revision, its reason and approval, what it supersedes and the acceptance or brief that must be renewed.>
