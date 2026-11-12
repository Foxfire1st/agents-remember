# Leaf task template

Planning roles use this scaffold to author one leaf through `task_doc`. When filling the task fields, follow [task prose fields](../reference/tasks/document-authoring.md#put-prose-in-the-fields-that-render-it) for what each field holds; this Markdown is an authoring example, not another stored task document.

# Task: <concrete change and outcome>

## Objective

<Describe the person or agent meeting the current problem and the result this leaf delivers. Explain why the change matters, with enough context to understand it without the planning conversation.>

## Non-goals

<Explain the nearby work this leaf leaves alone and the behavior and authority it preserves.>

## Requirements

<Approved stable identifier and version — exact canonical packet path. State which promise this leaf implements and which adjacent promises constrain it, without restating their contracts.>

## Design

<Explain how the change produces the outcome, which existing surfaces it uses and why this approach fits. Discuss relevant alternatives and the reasons for rejecting them. Describe the important state, failure and recovery paths.>

## Acceptance

<Describe the observable result, the deliverable evidence and the verification that distinguishes a correct change from the plausible regression. Identify any unresolved fact and the owner who can settle it.>

## Implementation Steps

### <step identifier> — <reviewable outcome>

<The outcome and the focused evidence that demonstrates it. Use the task's step records for progress.>

## Proposed Code Examples

Before writing the proposed code examples, read [proposed code changes](../reference/tasks/document-authoring.md#show-the-proposed-changes) for placement paths, skeleton contracts and examples.

<The planned placement of files and folders with paths, the skeleton of new or changed types, modules and signatures (one-line contracts, no bodies), and a representative example where a change is hard to judge from the skeleton; or the honest explanation of their absence.>

## Decision Log

<The decision and its reason in the task's dated decision record.>

## Open Questions

<The unresolved question, what it affects and who can answer it; or none.>

## References

<The approved packets and concrete sources needed to assess this leaf.>
