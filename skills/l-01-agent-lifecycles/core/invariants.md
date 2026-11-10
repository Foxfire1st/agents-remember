# Core — Shared invariants

This file is an authoring reference for the shared rules on which roles depend. A role file names the invariant it depends on without restating the whole set; started roles receive the shared projection in `core/every-role.md`.

## Keep continuity in durable state

A seat's state is the task tree and its own written artifacts. The tree includes masters, leaves, statuses, decision logs, `openQuestions`, contracts and worktree contracts. This durable state lets short-lived Workers and Reviewers recover when a session dies, compacts or resumes. Every seat writes its artifact of record. A finding held only in chat is a defect.

A claim about all members requires checking the complete population named in the claim. Read the producing call before counting. A claim of absence requires searching the entire relevant tree; claim absence only from an unfiltered list. Record the covered scope and any truncation.

For a check, report what ran and what was left out. Call a failure pre-existing only when the base fails for the same cause, identified by message and subtest; the same test name is not enough.

For each recorded time, read the clock in the same step. Append a correction instead of rewriting a wrong time.

Before reporting mismatched behavior as a defect, compare the serving tool build with the code.

Name the graphics setup behind a browser measurement. Do not claim it represents the developer's browser without evidence for that claim.

## Record decisions on their durable surface

Put a decision needing the developer on the visible task decision surface. A taskless seat uses its own chat as its role states.
Keep the supporting analysis in `notes/`.

Record a ruling that changes task, branch or orchestration state in the owning task's decision log.
A seat with a parent sends developer questions through that parent; without one, it asks in its own chat.

## Let dependencies determine the work order

Run independent work in parallel by default. Preserve serial landing order.
Name a reason when work must be sequential: a gate, one writer needed for a shared file, an explicit ruling, or a harness without sub-agents. Do not invent a concurrency cap; only the developer can authorize one.

Concurrent builds do not grant a different landing order.

A guard is a check that refuses, holds back or switches off an operation. Do not add a new guard or any other restriction (a refusal, a limit or a narrowing) on your own. A new one needs an issue seen to repeat, and the developer accepts it before anyone builds it. A one-time incident or an imagined case does not justify it. Existing guards stay unless a requirement changes them. When an existing check blocks a legal operation, change only what blocks it. Architect, Investigator: Before a requirement goes to a Worker or a proposal leaves an Investigator, list every refusal, limit or narrowing added beyond the developer's words and get the developer's agreement. Do not invent count limits on full inputs or pages.

## Keep the work observable

Coordination seats keep their selected task documents and attached chats. Taskless Architect and Bootstrap seats need no invented document.

The Architect owns the developer conversation for delegated work. A parentless seat uses its own chat; backend seats keep their named parent relay.

## Connect task, branch and worktree

```text
task document (approved) → branch (intent) → worktree (where something is built)
```

Planning and design create no branch, worktree or commit.

An intent creates its branch at the appropriate step:

- The super branch is created when portfolio execution begins.
- An intermediate master branch is created only for an `atomic` master.
- A leaf branch and its leaf worktree are created together. Leaf work is the one place where branch and worktree legitimately appear at once.
- An `organizational` master creates no branch.

Worktrees exist for each build or integration step and are reclaimed afterward.

Every code change belongs to an approved task document. For small code work, use the [leaf task template](../templates/leaf-task.md). Chat remains the place for research and design conversation; it is not a build route.

## Fill small gaps without changing the plan

Fulfil the task and fill the small, unambiguous gaps a competent implementer would fill, and no more. Add no special instruction granting or withholding creative liberty.

A plan change beyond those gaps goes to the parent rather than silently reshaping the task. Without a parent, ask in your own chat.

The spirit test belongs only to the Orchestrator; its definition is in the [Orchestrator role](../roles/orchestrator.md#changes-to-the-plan). Managers and Workers keep their assigned promise and escalate real deltas.

## Resolve settings and capabilities

Role files are Markdown interpreted by models. They are not executors or deployment-setting blocks. The admitted launch settings and host supply the harness, model, effort and capabilities.

Launch settings resolve global and repository layers, followed by applicable role and level overrides. An explicitly admitted call overrides those values. The default harness comes from `orchestration.spawn.harness`. Unknown orchestration keys fail loudly, except that `designer` and `strategist` role keys are accepted and reported as ignored. docs/reference/settings-json.md documents this in full.

Role settings, `orchestration.roles.<role>` and `orchestration.rolesPerLevel.<level>.<role>`, admit `harness`, `model`, `effort`, `serviceTier`, `mode`, `featureValues`, `notes`, `extraArgs` and `stepUp`. The terms `dispatch` and `tools` describe structural authority and capability; they are never settings keys. docs/reference/settings-json.md documents this in full.

There are no per-harness role files. Portable role files describe harness-specific abilities, such as sub-agents, as conditions on available capability. Harness preference belongs to deployment configuration, not doctrine. Hard-coding a vendor would create different doctrine for each harness.

The `role_start` tool starts a role agent on a canonical document-and-role seat independently of the harness. A harness without sub-agents can still start a role agent through the framework.

## Keep each instruction on its owning surface

Before scoping work, read the [collaboration method](../reference/tasks/collaboration-method.md). Apply it as written rather than paraphrase it.

Before authoring a leaf, master or requirement packet, read its [leaf](../templates/leaf-task.md), [master](../templates/master-task.md) or [requirement-packet](../templates/requirement-packet.md) scaffold for its fields and sections, and [Put prose in the fields that render it](../reference/tasks/document-authoring.md#put-prose-in-the-fields-that-render-it) for the task-format conventions.

The memory layer's skills and resolved `system/*` files own repository-specific commands, coding guidelines and the landing procedure. Read the resolved `system/tools.md`, `system/coding-guidelines.md` and `system/task-publication.md` rather than inventing a runner. Before writing instruction texts, follow `system/skill-writing-guidelines.md` when present.

Design rules belong in the repository's `system/design-guidelines.md`. Role text and code defaults state no design rule. Shipped design files are examples only; never impose Agents Remember's appearance on the user's pages.

Roles own duties, operations own step procedures, and reference files own delivered details and supporting reasons. A started role's first message carries its role text, operation text and the `core/every-role.md` digest. It loads no other `core/` file: those files are references for the people and agents writing and reviewing instructions.

Instructions and requirements point to the repository's own guideline files and never restate their content. This keeps Agents Remember minimally opinionated.

Run Git commands only against the resolved code repository root or memory root, and only when that root is a Git repository. Never run Git against the coordination root as a whole.

Architect, Orchestrator, Manager, Worker, Curator, Reviewer, Investigator, Bootstrap: Before relying on storage or path eligibility, cross-repository allowances, or serving-build metadata, read [settings authority](../reference/knowledge/use.md#read-the-source-that-owns-a-setting) for the sources that govern those facts.
