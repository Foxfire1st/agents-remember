# Master task template

Planning roles use this scaffold for the shared outcome and reasoning of a master. When authoring its fields and ordered sections through `task_doc`, follow [task prose fields](../reference/tasks/document-authoring.md#put-prose-in-the-fields-that-render-it) for what each field and section holds.

# Task: <shared outcome>

## Objective

<Explain the current problem, who experiences it and the complete outcome the series delivers. Give the reader the context that makes the work worth doing.>

## Non-goals

<Explain the boundary of this series and the behavior and authority that it preserves.>

## Requirements

<Approved stable identifiers, versions and exact canonical packet paths. Explain the relationship among the promises without creating a second version of them.>

## Design

<Describe the shared approach and why its division into leaves is coherent. Explain the relevant alternatives, interactions, dependencies and risks. Keep leaf-specific implementation reasoning in its leaf.>

## Acceptance

<Explain what proves the complete outcome, including the evidence needed at the seams between leaves. State what remains unknown and who owns it.>

## Sub-tasks

<Use an ordered section with kind `subTasks` for the generated child list. Its introduction explains how the leaves contribute to the shared outcome.>

## Shared Decisions

<Use a section with kind `sharedDecisions` for the task's decision records. Its introduction explains any cross-cutting context the decisions need.>

## Open Questions

<Use a `freeform` section for cross-cutting unresolved questions, their effect and their owner; or none.>

## References

<Use a `freeform` section for the sources needed to understand and assess the shared plan.>
