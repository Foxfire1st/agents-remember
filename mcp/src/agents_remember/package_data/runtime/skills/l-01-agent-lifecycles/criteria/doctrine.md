# Criteria catalog: doctrine review

The Reviewer uses this catalog when the change contains instructions agents obey: skills, role lifecycles, templates, instruction surfaces or documentation. The [Reviewer binding table](../roles/reviewer.md#bind-the-seam-and-catalogs) determines when it applies.

Before starting a baseline or fix-verification, read the [shared catalog review rules](code-seam.md#shared-catalog-review-rules). They define standing checks, baseline exploration, fixed-list verification and criterion promotion, demotion and automation.

## Standing criteria

### D-1 — Doctrine-vs-code anchoring

Every instruction claiming enforcement, refusal or automatic behavior must identify the code file and mechanism that implements it. Describing enforcement that does not exist is a defect.

### D-2 — Cross-file contradiction sweep

Check sibling instructions for statements the change now contradicts. Examine the whole relevant instruction surface, including skills, roles, templates, documentation, hooks and canvas prose, rather than only edited files.

### D-3 — Stuck-state walk

Follow the instructions as the agent that must obey them. At every wait, gate and handover, identify who unblocks it and through which channel. Two seats waiting on one another, or waiting on a channel nobody serves, is a blocking finding.

### D-4 — Topology and authority sweep

Reject instructions that treat every master as a Git integration branch or an integration branch as a repair workbench. Organizational masters are logical owners whose leaves land directly on super. Only atomic masters own an intermediate branch and expose it once.

Check roles, briefs, templates, review packets and diagrams for contradictions of these topology and authority rules. Direct feature or fix commits on main, super or atomic integration branches are a blocking contradiction. The only integration-branch writes are narrowly scoped conflict resolution and reference movements owned by the execution system.

### D-5 — Detection is not judgment

Mechanical facts must not make policy decisions implicitly. Code may report membership, graph shape, derived waves, readiness, lineage and route or seam evidence. Execution nature, dependency meaning, priority and material reprioritization require an explicit role judgment with durable rationale. Label stable tie-breaking as tie-breaking, never as a priority decision.

### D-6 — Source-comment requirement identifiers

Before reviewing source comments, read `system/coding-guidelines.md`, section Source Comment Scope, in the repository's memory, when present, and check the comments against it, including its bounded requirement-identifier exception.
