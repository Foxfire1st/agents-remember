# Core — Lifecycle frame

This authoring reference helps maintainers locate assignment and scope duties in the delivered role instructions.

## Record the task's location and outcome

The public `worktree_start` reference (`docs/reference/mcp-tools.md#worktree-lifecycle--closeout`) describes worktree creation and task-contract publication.

The public `worktree_attach` reference (`docs/reference/mcp-tools.md#worktree-lifecycle--closeout`) describes existing-task attachment and contract resolution.

The public `terminal lifecycle` reference (`docs/reference/worktrees-c09.md#terminal-cleanup`) describes completion or abandonment, the terminal operation and surviving contract truth.

## Bind each role to its scope

A task-bound seat is its canonical document plus role. Architect and Bootstrap start without documents. An Investigator uses its supplied taskless, sprint or master scope. Managers bind masters; Orchestrators bind sprints. Every Reviewer binds a leaf, including a Reviewer of an integration seam.

Each owning seat controls only the machinery named by its role file. No other agent calls `task_doc`, `role_start` or closeout on its behalf.

## Check the supplied assignment before acting

Orientation uses the role, exact canonical assignment and workspace bindings in the handover. Do not invent a trust packet.

1. Read the supplied rendered task and requirement files with native file tools. Change canonical task documents only through `task_doc`.
2. Confirm that the role, task, requirement version, roots, contract and report path agree. For a missing binding, name the owning source and exact field.
3. Before repository planning, the Architect reads the relevant knowledge and resolves the memory layer. Onboarding changes belong to the Curator, not to an invented refresh by a started role.
