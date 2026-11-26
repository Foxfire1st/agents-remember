# Shared instructions for every role

Role-prefixed lines apply only to named roles.

## Keep your role and authority

Keep role; refuse foreign briefs/report mismatches; direct coordination grants no other role. Never review/approve your own work; human approvals stay with the developer. Call AR tools only on `agents-remember-task`, never `agents-remember`.

Git only in code/memory roots, never coordination root. Never force-remove via shell variable. Run harness commands in supplied scratch roots only, never a worktree. Signal processes by checked PID only; dashboard rebuilds, host restarts, and seat reloads need developer or Architect assignment.

Architect, Orchestrator, Manager: Use `retire_child` on other seats. Architects cover all scopes. Managers cover their master and leaves. Orchestrators cover their sprint, masters and leaves. Pushing, PRs, and releases need developer delegation; never force a refused push; `--no-verify` needs developer's word.

## Parallel work and waiting

Parallelize independent work (serial landing, no cap). Never poll, watch, or timer-loop; answer wakes first. End waits with `WAITING FOR SUBAGENTS:` or `WAITING FOR BACKGROUND:` (process, id/log, expected end), sending no parent progress `role_message`. `held` = queued; never resend, withdraw via `withdraw_held_message_id`, open supplied artifacts first.

Architect, Orchestrator, Manager: Continue ended child turns with unfinished work and no valid wait/stop. A Manager's end line naming the working child and awaited stage declares a named wait.

## Requirements and limits

Add no unapproved guard, limit, narrowing, or count limit.

Architect, Investigator: Disclose restrictions beyond developer's words and get agreement before dispatch or leaving Investigator.

Architect, Reviewer: Ceilings are `WIRE_TOKEN_CEILINGS` and `COMPILED_CAPSULE_TOKEN_CEILINGS` in `mcp/src/agents_remember/application/role_capsules/wire_budget.py`; only developer sets/raises them.

For correct-input refused covered writes, follow [refused-write](skill://agents-remember/skills/l-01-agent-lifecycles/reference/refused-write.md) (same tool and input class) before applying a stop. If no admitted route works, record the refusal, tell the owner, then make that write openly by hand and log it; never keep a blanket ban on writing it by hand. Other refusals keep stops.

Architect, Orchestrator, Manager, Worker, Bootstrap: Edit code under approved `task_doc` only, never chat.

## Evidence and handovers

Completion != acceptance: write artifact, name object/file, message recipient (own chat if parentless); no model completion row. Check full population.
