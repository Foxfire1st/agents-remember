# Operation — Recovery

Recover the same canonical AR task and the same role agents. Start with the exact saved task, request ID, agent ID, recipient, report, and candidate references, and reload your handover artifact when your assignment is no longer in context. Use AR task/worktree tools on the `agents-remember-task` tool server only for their canonical data and paired workspace facts.

An interrupted or uncertain start or message is not permission to create a new owner. Reconcile a start by calling `role_start` on `agents-remember-task` again with the same request ID; it returns the same agent. A message whose delivery is unknown is asked about with a new `role_message` to the same agent, not replaced by a new agent. If the outcome stays unknown, preserve it as unknown and report the IDs. For an existing AR leaf, restore the same admitted code/memory/contract pair; never ask Paseo to create a parallel Git worktree or fall back to a global memory root.

Do not create an alternate transport, background poller, private store, or second owner. A refusal names its real prerequisite. A missing semantic fact names the exact task/requirement field and returns to its owner. Resuming an agent restores execution, not AR acceptance or Git publication.
