# Bootstrap failure states and responses

Bootstrap reads this detail when a setup step returns a state it cannot name. Each section identifies the observation and the response; [the role's stop rules](../../roles/bootstrap.md#stop-and-report-to-the-developer) remain in force.

## Memory root missing

Observation: `memory_baseline_status` reports nonexistent memory, or the resolver reports missing memory with the exact path it checked.

Run the memory-root step. No downstream step can succeed first.

## Unsupported topology

Observation: the coordination resolver or a worktree entry point returns `memory-mode-unsupported`, naming the artifact and the supported set.

Report the refusal by name. Do not substitute a mode. Report the exact path of the existing memory root; never migrate, rewrite or delete it. Bootstrap performs no change.

## Dirty memory repository before adoption

Observation: `git status --porcelain` in the memory root before adoption.

Adoption commits the content it finds. Uncommitted work under `onboarding/`, `docs/` or `system/` becomes the baseline. Read the tree and get explicit agreement before adopting. A clean drift report does not establish a clean tree.

## Coordination root cannot resolve

Observation: `server_info` reports no `coordinationRoot`, or `context_packet` refuses for the named repository.

The coordinator runtime scaffold is missing or the repository is not configured. For the recovery procedure, read the install and onboarding guide (`docs/getting-started.md` and `docs/install/README.md`) in the source repository; if the scaffold is absent, `runtime_install` creates it. Do not build a coordination root by hand.

## Memory root is not a Git repository

Observation: the adoption step refuses and names the memory root.

Run the memory-root step again with Git initialization enabled. Adoption needs a Git repository, not just directories.

## Baseline already adopted

Observation: `memory_baseline_status` reports attributed memory.

The baseline is already adopted. Adoption is valid only before attributed memory exists, so skip adoption. Before finishing, follow [foundation reporting](foundation-state.md) for the read-only state report and transition to ordinary use.

## Knowledge foundation not recorded

Observation: `memory_init`'s `knowledge` block reports `not-recorded`.

No knowledge is recorded at the location selected by the ordinary read route. This is not a failure. Onboarding and the baseline may both be complete; neither substitutes for the foundation. Before reporting this state and naming the next step, follow [foundation reporting](foundation-state.md) for the reported state and the Architect's next step.

## Knowledge foundation already recorded

Observation: `memory_init`'s `knowledge` block reports `recorded` with its identity.

Before reporting the identity or naming the authoring owner, follow [foundation reporting](foundation-state.md) for the reported identity and the Architect as the foundation's owner. Do not replace the recorded foundation with a scaffold.

## Knowledge foundation unusable or bound elsewhere

Observation: `unusable`, with the shipped refusal code. `selected_input_unavailable` means there is no file to open; `snapshot_unavailable` means the bytes are not the expected dataset.

Report the exact state, path and code, then stop. The developer decides what to do with that object. Never delete, overwrite or migrate it, and never report it as `not-recorded`.

## Knowledge admission refused

Observation: `memory_init` returns `knowledge.state = context-not-admitted`, with `code`, `detail` and `nextAction`.

This is an admission failure, not a knowledge state. Report the refusal's own route. Its code can be `repository_not_allowed`, `coordination_root_unavailable`, `code_checkout_unavailable`, `code_checkout_is_not_a_git_checkout`, `code_revision_unavailable`, `memory_revision_unavailable` or `memory_layer_not_resolved`.

Do not work around the refusal by supplying a path or revision yourself.

## Settings file already exists

Observation: `user_settings_init` returns `already-configured` or `already-initialized`.

Before responding to this observation, follow [Write the initial user settings](../../operations/bootstrap.md#4-write-the-initial-user-settings) for the existing-file report, preservation of choices and harness-default behavior.

## Settings are not writable

Observation: `user_settings_init` refuses with `settings-not-writable` or `destination-is-symlink`.

Creating or replacing the destination would change what the developer owns. Report the exact path and failure. The developer changes the mode; Bootstrap does not. Do not follow a symlink as the destination.
