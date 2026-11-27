"""Independent expected clauses for current coordination and curation wording."""

CURRENT_COORDINATION_CLAUSES: dict[str, list[str]] = {
    "roles/architect.md": [
        "Answer developer messages first with an answer, never a status line.",
        "`From <role> · <task> · agent <id>` is a role agent, not developer approval;",
        "Delegate 1 master to 1 Manager; 2+ concurrent masters to 1 sprint Orchestrator "
        "(1 Manager/master).",
        "Direct coordination needs explicit developer instruction (report refused starts, "
        "no fallback).",
        "When a 2nd master starts under a direct Manager, start 1 sprint Orchestrator "
        "with that Manager's exact ID, master ref, and state (reuses it, no duplicate) "
        "and name the Orchestrator's ID to the Manager in 1 message (reporting recipient "
        "switches; launch parent stays fixed).",
        "Start all 7 roles via `role_start` with exact refs/request ID; keep returned IDs/paths.",
        "Under direct coordination, send repairs to the same Worker via `role_message` "
        "(review full diff).",
        "Own developer chat, semantic plans, requirements, and delegation in Agents Remember (AR).",
        "Release coordination after handover (no completion `wait`); read Manager/Orchestrator "
        "rulings, requirement-change list, and status file yourself (no status-only notices).",
        "Align requirement text within promise; case rulings name only the outcome needing "
        "Architect and leave Manager decisions with Manager.",
        "Relay the developer's actual answer/ruling via `role_message` by recorded agent ID "
        "(repeating the question is not the developer's decision).",
        "Start Investigator via `role_start` without task ref (sprint/standalone master/both; "
        "Manager/Orchestrator may start within scope) with concern as first `role_message`;",
        "ask developer in own chat; never mutually wait.",
        "Check, measure, test/build, read code, and sandbox-test (on a copy, never a candidate "
        "worktree), including pre-landing checks—never substituting for leaf build, review, "
        "curation, or gate (no leaf code/memory edits, no self-grading, no onboarding).",
        "Perform no landing step except flat-mode direct paired closeout "
        "(`worktree_closeout_preview`, `worktree_closeout_apply`) under developer-chosen "
        "direct coordination with independent review and curation evidence.",
        "Reviews, cold reads, re-checks, sweeps and Investigator reports are advice, not "
        "requirements. A finding that only corrects a wrong fact may be applied. A finding "
        "that adds, narrows or overrides a rule beyond the developer's words goes to the "
        "developer first, quoted beside the developer's earlier words.",
        "Follow `system/task-publication.md` if present (registering a leaf creates its open "
        "linked issue; without that file, publish nothing);",
    ],
    "core/acceptance.md": [
        "It does not establish that the required artifact exists, is current or satisfies its requirement.",
        "On wake, the current gate owner opens and validates the required artifact, candidate identity, evidence and acceptance envelope before advancing lifecycle state.",
    ],
    "roles/orchestrator.md": [
        "Coordinate one selected sprint at Projects altitude for 2+ concurrent masters "
        "(or developer-requested single-master sprint). Paseo state does not record "
        "Agents Remember (AR) acceptance.",
        "Reuse every existing Manager named in Architect's handover and start 1 Manager "
        "per other master via `role_start` under sprint (permitted: Manager, Worker, "
        "Reviewer, Curator, Consolidator, Investigator). Keep returned IDs, address explicitly, "
        "never proxy invisibly.",
        "Managers keep leaf finding-trail judgment, leaf-diff/repair/evidence loops, "
        "and planned-gap skeleton answers",
        "inspect diffs without taking over.",
        "Handle operational order, pre/post-landing checks, and delegated paired closeout.",
        "Dashboard Orchestrators have no parent (ask developer in own chat).",
        "Decide ordinary readings within promise and keep 3 durable task notes (name paths "
        "in report; no scope/promise authority): rulings record, requirement-change list, "
        "status file (Architect reads independently).",
        "Start Investigator via `role_start` on sprint (or sprint + 1 master; never leaf) "
        "with concern as first `role_message`.",
        "Report permission-blocked starts upward (or own chat if parentless); never start "
        "the blocked role or invent an ID.",
        "Message handover parent ID via `role_message` for developer decisions: new/dropped "
        "scope or promise changes; developer-only approvals; role-default overrides; small "
        "item costing ~1 hour or 2nd repair round/freeze (with hours & proposal); "
        "unremovable blockers.",
        "Forward Manager's Architect matters unchanged, plus developer questions and "
        "permission notices.",
        "Send report path when finished or unfinishable; no status-only messages or extra "
        "landing notices.",
        "On refusals other than `held` or full-store, log matter & refusal in status file "
        "and continue independent work without deciding developer questions.",
        "Keep reports, findings, review, curation, and publication separate; never self-review.",
        "Plan-review and super-exit have no startable Reviewer seat -> bring to Architect "
        "for developer with sprint-level Reviewer idea.",
        "[stages](../operations/coordination.md#start-leaf-stages-and-deliver-the-first-intake)",
    ],
    "roles/manager.md": [
        "Coordinate one canonical master in Agents Remember (AR), not the portfolio.",
        "Report to launching parent unless Architect instructs an Orchestrator recipient.",
        "Check packet deps before starting/integrating (the product does not perform that "
        "check); follow [leaf stages](../operations/coordination.md#start-leaf-stages-and-deliver-the-first-intake).",
        "Permitted `role_start`: Worker, Reviewer, Curator, Consolidator, Investigator.",
        "Inspect full diff, verdicts, and listed final checks before gates; keep "
        "closeout/integration/status/acceptance/landing order without relaying "
        "freeze/finding/verdict/memory changes.",
        "Start/finalize/abandon mirror stage automatically (`task_doc set_status` only for "
        "correction/org-master completion; you set no step for another agent).",
        "Follow `system/task-publication.md` if present (claiming a leaf assigns its issue "
        "and sets In progress before start; after landing and both pushes, close the issue; "
        "without that file, publish nothing).",
        "**You decide:** leaf order, seat starts/replacements, operational issues, ordinary "
        "promise readings, pre/post-landing checks, delegated integration, proof commands, "
        "scratch tools",
        "1 infra rerun on same operands",
        "2nd same-kind infra failure",
        "planned-gap skeletons, and routine paired closeout",
        "**Send to developer:** scope/promise changes, developer-only approvals, "
        "role-default overrides, small item costing ~1h or 2nd repair round/freeze "
        "(hours + proposal), unremovable blockers.",
        "Start Investigator via `role_start` on master/sprint (never leaf) with concern "
        "as first `role_message`.",
        "Keep 3 master notes (name paths in report; no scope/promise authority): rulings, "
        "requirement-change list, status file.",
        "Message recorded recipient, else parent's role with its task references (or parent "
        "ID for developer questions), else own chat via `role_message` (dashboard Managers "
        "have no parent; invent no ID); route Architect matters via Orchestrator except "
        "direct skeletons; send closeout notices & final report path (no status-only notices).",
        "When launching Architect names an Orchestrator ID on a 2nd master, record it and "
        "switch recipient (launch parent stays fixed).",
        "Report blocked starts upward; log undelivered messages in status file.",
    ],
    "operations/planning.md": [
        "Follow [owner](coordination.md#choose-the-coordination-owner), keep 1 owner/task and real IDs",
    ],
    "operations/coordination.md": [
        "1 master -> 1 Manager; 2+ concurrent masters -> 1 sprint Orchestrator (1 Manager/master).",
        "Direct Architect coordination or single-master Orchestrator needs developer "
        "instruction; report refused starts without fallback.",
        "Call `role_start` with role, refs, and request ID (Architect: all 7; Orchestrator: "
        "all except Orchestrator, sprint; Manager: Worker/Reviewer/Curator/Consolidator/Investigator, "
        "master). Keep AR-paired enclosure (no Paseo worktree/global memory); save IDs/paths; "
        "reconcile uncertain starts via same request ID.",
        "The Manager calls `role_start` once per stage: Worker first, Reviewer at the freeze, "
        "and Curator when curation begins. Start only the Worker at build.",
        "The Worker's freeze notice and the Reviewer's code-`PASS` notice go to the Manager "
        "first. Ordinary curation follows code `PASS` under Manager's master; once Reviewer "
        "exists, Worker sends freeze directly.",
        "assigned packet cold read without an early normal leaf Reviewer",
        "ordinary delivery curation and knowledge check still follow code `PASS`",
        "Leaf seats message directly via `role_message` (freeze->Reviewer->Worker; passed "
        "freeze & lists->Curator<->Reviewer; post-`PASS` final checks->Manager & Reviewer; "
        "Curator code concerns: [exceptions](../reference/curator/exceptions-and-recovery.md"
        "#memory-tool-failures-code-concerns-and-sync-conflicts)).",
        "Manager decides gates and retains closeout/integration/status/acceptance/landing "
        "order without relaying freeze/finding/verdict/memory changes.",
        "Send via `role_message` by ID or role+task refs (role != self; invent no IDs). "
        "Empty seats report upward; 2 occupants refuse naming both; active turns may `held` "
        "or cancel (never into permission prompts); archived seats refuse (use successor). "
        "`wait` returns text, failure/cancel, `permission-pending`, timeout, or `accepted`; "
        "never mutually wait.",
    ],
    "SKILL.md": [
        "Use the agent IDs these tools return and the sender line of each message you receive; never "
        "invent a sender, recipient, parent, or delivery result.",
        "AR remains authoritative for canonical tasks, requirements, knowledge, curation, and paired "
        "Git operations.",
        "An Architect first delegates coordination to one Manager for one master, or to one "
        "Orchestrator on the sprint when two or more masters are worked on at the same time. The "
        "Orchestrator starts one Manager per master. Only the developer may choose direct coordination "
        "by the Architect. The developer may also ask for an Orchestrator above a single master. A "
        "role started from the dashboard has no parent agent and needs none. Existing approvals and "
        "rulings remain durable across reconnects and compaction; ask again only for new or changed "
        "scope, a real requirement conflict, or an unresolved human-pinned decision.",
    ],
    "roles/curator.md": [
        "Leaf task doc only.",
        "[foundation]({{detail:reference/curator/repository-foundation.md}})",
        "Write `onboarding/`+`knowledge/` via one writer (`knowledge_write` or serving-build "
        "CLI `--commit`/`commit: true`; missing writer != correct-input refusal);",
        "One Curator writer lane (no helper writers); own placement, admission, meaning, "
        "checks and handover.",
        "No Git writes, code/gate/lifecycle/contract/closeout edits, role starts",
    ],
    "operations/curation.md": [
        "Send evidenced code concerns to Worker+Reviewer; repair no code, author no false knowledge.",
        "scoped checks never substitute; rerun until `curatorActionableCount=0` + "
        "`qualityChecklistStatus=ready-for-closeout`",
    ],
    "core/every-role.md": [
        "For correct-input refused covered writes, follow [refused-write]"
        "(skill://agents-remember/skills/l-01-agent-lifecycles/reference/refused-write.md) "
        "(same tool and input class) before applying a stop.",
        "`held` = queued; never resend, withdraw via `withdraw_held_message_id`, "
        "open supplied artifacts first.",
        "Completion != acceptance: write artifact, name object/file, message recipient "
        "(own chat if parentless); no model completion row.",
    ],
    "core/authority.md": [
        "Start another role with `role_start` once, naming the role, a chosen request "
        "identifier and its exact required task references. To reconcile an uncertain "
        "result, use the same identifier and selection for the same start.",
    ],
    "reference/curator/memory-handover.md": [
        "The Reviewer compares the leaf's base and candidate in Git: the code trees and "
        "the knowledge files in both memory trees. The Curator records no separate "
        "comparison."
    ],
    "reference/curator/exceptions-and-recovery.md": [
        "Send evidenced code concerns to both Worker and Reviewer, including after passing "
        "code review, showing the requirement or report mismatch. Repair no code and author "
        "no knowledge claiming behavior the code lacks. The Reviewer records a finding and "
        "reopens the point, or explains its rejection; the Curator records the answer, "
        "reports both the finding and the answer, and sends unresolved disagreement to the "
        "Manager.",
    ],
    "reference/coordination/shared/closeout-recovery.md": [
        "Flat mode means only the developer-chosen direct-coordination exception where "
        "neither a Manager nor an Orchestrator owns closeout.",
        "Call the paired closeout yourself and start no Manager for that step alone. This "
        "grants no other execution authority. Human-pinned approvals remain human; never "
        "substitute raw Git or self-approval.",
        "The Manager records that instruction and the Orchestrator's agent identifier in "
        "its report and uses it as its reporting recipient. The Manager's launch parent "
        "stays fixed; only that Architect instruction changes its reporting recipient.",
        "Before deciding a formal permission, follow [Formal permissions]"
        "(../../../operations/orientation.md#formal-permissions) for the Architect's "
        "own-seat answers and the prompts the developer decides. Before acting on a denied "
        "or unanswered harness request, read [Honor harness permission decisions]"
        "(../../../operations/orientation.md#honor-harness-permission-decisions) for the "
        "waiting, denial and non-bypass rules.",
    ],
    "reference/worker/sync-and-repairs.md": [
        "Report non-work replies and empty-seat or doubled-seat refusals to the Manager "
        "once, naming the seat and quoting the refusal text. Never pick a candidate "
        "yourself. When the Manager gives the occasion, send the handover yourself; "
        "the Manager relays none of the contents.",
        "When a required Manager notice fails delivery for a Worker, Curator or Reviewer, "
        "record the notice as pending, continue independent work, and retry the notice "
        "through `role_message`. Never turn a delivery failure into a question to the "
        "developer in your own chat.",
    ],
}
