"""Curator and coordination instruction wording, moved out of the role wording module.

Verifies foundation boundaries, producer and review truth criteria, card sentence decisions,
Curator write-stop qualification, and current coordination/Curator rules across the canonical
three-tier lifecycle tree. Compiled capsule delivery, manifest contracts, and writer admission
live in ``test_curator_instruction_wording_delivery.py``.
"""

from __future__ import annotations

import unittest

from test_curator_instruction_wording_delivery import (
    CANONICAL,
    LAUNCHER_ROLES,
    LIFECYCLE,
    assert_coordination_routes,
    assert_git_comparison_scope,
    assert_message_handover_delivery,
    read,
    role_text,
)


class CuratorInstructionWordingTests(unittest.TestCase):
    def test_foundation_intake_and_correction_keep_their_task_and_truth_boundaries(self) -> None:
        role = read(LIFECYCLE / "roles/curator.md")
        role_flat = " ".join(role.split())
        for clause in (
            "Leaf task doc only.",
            "require passing `handoff-check`",
            "Withdraw falsified claims",
            "[foundation]({{detail:reference/curator/repository-foundation.md}})",
        ):
            with self.subTest(clause=clause):
                self.assertIn(clause, role_flat)
                with self.assertRaises(AssertionError):
                    self.assertIn(clause, role_flat.replace(clause, "", 1))
        onboarding = " ".join(read(LIFECYCLE / "criteria/onboarding-memory.md").split())
        self.assertIn(
            "Read the whole affected body. A true paragraph added beside a contradictory claim does not repair it.",
            onboarding,
        )
        foundation = " ".join(
            read(LIFECYCLE / "reference/curator/repository-foundation.md").split()
        )
        for clause in (
            "The user-started Architect opens the real foundation task",
            "real foundation task under a master",
            "The Curator uses its actual enclosure contract.",
            "No incidental code delta is required",
            "follow [the curation operation](../../operations/curation.md)",
            "Onboarding is optional input",
            "This foundation procedure authors no onboarding",
            "For setup\N{RIGHT SINGLE QUOTATION MARK}s state report and next step, follow [Name the next step]",
            "An instructed session cannot replace task admission",
            "never fabricate a task, worktree or enclosure",
            "Report partial foundation scope as partial and name the areas not reached",
        ):
            self.assertIn(clause, foundation)
        self.assertNotIn("**Repository foundation is regular task work.**", role)

    def test_producer_checks_and_review_truth_criteria_keep_their_own_homes(self) -> None:
        operation = " ".join(read(LIFECYCLE / "operations/review.md").split())
        self.assertIn(
            "Once Curator exists, run `handoff-check` (`server_info`) and send Curator freeze, code-PASS verdict, list + result directly.",
            operation,
        )
        commands = " ".join(read(LIFECYCLE / "reference/knowledge/commands.md").split())
        for clause in (
            "Before sending a freeze's producer list, run `handoff-check` with the serving interpreter and exact frozen tree.",
            "Name the result, list and freeze in the handover.",
        ):
            self.assertIn(clause, commands)
        onboarding = " ".join(read(LIFECYCLE / "criteria/onboarding-memory.md").split())
        self.assertIn(
            "Read the whole affected body. A true paragraph added beside a contradictory claim does not repair it.",
            onboarding,
        )
        report = " ".join(read(LIFECYCLE / "criteria/report-verification.md").split())
        for clause in (
            "RV-6",
            "directly required and standing",
            "promotion/demotion process cannot demote it",
            "what ran and what was left out",
            "same cause, identified by message and subtest",
            "rather than merely sharing a test name",
            "files it exercises are byte-identical",
            "Changed exercised files make the carried run an evidence gap.",
        ):
            self.assertIn(clause, report)

    def test_cards_are_decided_sentence_by_sentence_before_curation(self) -> None:
        """Pin the instructed guidance; this neither inspects nor grades actor card edits."""
        detail = read(LIFECYCLE / "reference/curator/card-sentence-decisions.md")
        item = detail.split("# Decide card sentences before editing", 1)[1]
        text = " ".join(item.split())
        for clause in (
            "Decide each sentence of every card and its paired notes, including reference captions, one by one, whatever "
            "its tense or location, dated sections included.",
            "Edit individual sentences; never replace a section or regenerate a card wholesale.",
            "When a sentence is true now, keep its wording, also when it states something "
            "true about a retired thing.",
            "Before keeping a sentence, verify it in the supplied source. Before narrowing "
            "or dropping one, search the supplied source and memory for what supports it. "
            "A sentence about what happens is true only if that code runs today; code that "
            "exists but never runs does not make it true.",
            "Repair or withdraw every false claim in every current carrier found by a bounded "
            "search: the card body, paired notes and sibling cards.",
            "Repair the class, not just one example.",
            "Remove past accounts, including dated change notes, “former … was removed” and "
            "“The fix: …”, from the card and name them in that card's history row, written "
            "after the card's last edit.",
        ):
            with self.subTest(clause=clause):
                self.assertIn(clause, text)
        self.assertNotIn("code does not contradict", text)

    def test_correct_write_recovery_qualifies_only_the_curator_write_stop(self) -> None:
        role = role_text("curator")
        self.assertNotIn("{{detail:reference/refused-write.md}}", role)
        self.assertIn("missing writer != correct-input refusal", role)
        self.assertIn("No Git writes, code/gate/lifecycle/contract/closeout edits", role)
        self.assertNotIn("No other route writes knowledge from this seat.", role)
        self.assertNotIn("never hand-write records, sidecar entries or history", role)
        shared = " ".join(read(LIFECYCLE / "core/every-role.md").split())
        self.assertIn("(same tool and input class) before applying a stop.", shared)
        self.assertIn("Other refusals keep stops.", shared)
        detail = " ".join(read(LIFECYCLE / "reference/refused-write.md").split())
        self.assertIn(
            "This exception covers only the same tool and input class that refused; every other write still uses its tool.",
            detail,
        )

    def test_current_coordination_and_curator_rules(self) -> None:
        # One obligation group per original case, relocated to its current Tier-0/1/2 home.
        required = (
            (
                "roles/architect.md:0",
                (
                    (
                        "roles/architect.md",
                        "Answer developer messages first with an answer, never a status line. `From <role> · <task> · agent <id>` is a role agent, not developer approval;",
                    ),
                ),
            ),
            (
                "roles/architect.md:1",
                (
                    (
                        "roles/architect.md",
                        "Delegate 1 master to 1 Manager; 2+ concurrent masters to 1 sprint Orchestrator (1 Manager/master).",
                    ),
                    (
                        "roles/architect.md",
                        "Direct coordination needs explicit developer instruction (report refused starts, no fallback).",
                    ),
                    (
                        "operations/planning.md",
                        "keep 1 owner/task and real IDs",
                    ),
                ),
            ),
            (
                "roles/architect.md:2",
                (
                    (
                        "roles/architect.md",
                        "Direct coordination needs explicit developer instruction (report refused starts, no fallback).",
                    ),
                    (
                        "operations/coordination.md",
                        "Direct Architect coordination or single-master Orchestrator needs developer instruction; report refused starts without fallback.",
                    ),
                ),
            ),
            (
                "roles/architect.md:3",
                (
                    (
                        "roles/architect.md",
                        "When a 2nd master starts under a direct Manager, start 1 sprint Orchestrator with that Manager's exact ID, master ref, and state (reuses it, no duplicate) and name the Orchestrator's ID to the Manager in 1 message (reporting recipient switches; launch parent stays fixed).",
                    ),
                ),
            ),
            (
                "roles/architect.md:4",
                (
                    (
                        "roles/architect.md",
                        "Start all 6 roles via `role_start` with exact refs/request ID; keep returned IDs/paths.",
                    ),
                    (
                        "operations/coordination.md",
                        "Call `role_start` with role, refs, and request ID (Architect: all 6; Orchestrator: all except Orchestrator, sprint; Manager: Worker/Reviewer/Curator/Investigator, master). Keep AR-paired enclosure (no Paseo worktree/global memory); save IDs/paths; reconcile uncertain starts via same request ID.",
                    ),
                    (
                        "core/authority.md",
                        "Start another role with `role_start` once, naming the role, a chosen request identifier and its exact required task references. To reconcile an uncertain result, use the same identifier and selection for the same start.",
                    ),
                    (
                        "roles/architect.md",
                        "Under direct coordination, send repairs to the same Worker via `role_message` (review full diff).",
                    ),
                ),
            ),
            (
                "roles/architect.md:5",
                (
                    (
                        "roles/architect.md",
                        "Delegate 1 master to 1 Manager; 2+ concurrent masters to 1 sprint Orchestrator (1 Manager/master).",
                    ),
                    (
                        "roles/architect.md",
                        "case rulings name only the outcome needing Architect and leave Manager decisions with Manager.",
                    ),
                ),
            ),
            (
                "roles/architect.md:6",
                (
                    (
                        "roles/architect.md",
                        "read Manager/Orchestrator rulings, requirement-change list, and status file yourself (no status-only notices). Align requirement text within promise; case rulings name only the outcome needing Architect and leave Manager decisions with Manager.",
                    ),
                ),
            ),
            (
                "roles/architect.md:7",
                (
                    (
                        "roles/architect.md",
                        "Release coordination after handover (no completion `wait`); read Manager/Orchestrator rulings, requirement-change list, and status file yourself (no status-only notices).",
                    ),
                ),
            ),
            (
                "roles/architect.md:8",
                (
                    (
                        "roles/architect.md",
                        "Relay the developer's actual answer/ruling via `role_message` by recorded agent ID (repeating the question is not the developer's decision).",
                    ),
                ),
            ),
            (
                "roles/architect.md:9",
                (
                    (
                        "roles/architect.md",
                        "Start Investigator via `role_start` without task ref (sprint/standalone master/both; Manager/Orchestrator may start within scope) with concern as first `role_message`;",
                    ),
                ),
            ),
            (
                "roles/architect.md:10",
                (
                    (
                        "roles/architect.md",
                        "ask developer in own chat; never mutually wait.",
                    ),
                    (
                        "roles/architect.md",
                        "Under direct coordination, send repairs to the same Worker via `role_message` (review full diff).",
                    ),
                    (
                        "core/every-role.md",
                        "Never review/approve your own work; human approvals stay with the developer.",
                    ),
                    (
                        "core/every-role.md",
                        "Completion != acceptance: write artifact, name object/file, message recipient (own chat if parentless); no model completion row.",
                    ),
                ),
            ),
            (
                "roles/architect.md:11",
                (
                    (
                        "roles/architect.md",
                        "Check, measure, test/build, read code, and sandbox-test (on a copy, never a candidate worktree), including pre-landing checks—never substituting for leaf build, review, curation, or gate (no leaf code/memory edits, no self-grading, no onboarding).",
                    ),
                    (
                        "roles/architect.md",
                        "Perform no landing step except flat-mode direct paired closeout (`worktree_closeout_preview`, `worktree_closeout_apply`) under developer-chosen direct coordination with independent review and curation evidence.",
                    ),
                ),
            ),
            (
                "roles/architect.md:12",
                (
                    (
                        "roles/architect.md",
                        "Own developer chat, semantic plans, requirements, and delegation in Agents Remember (AR).",
                    ),
                    (
                        "reference/coordination/shared/closeout-recovery.md",
                        "Flat mode means only the developer-chosen direct-coordination exception where neither a Manager nor an Orchestrator owns closeout.",
                    ),
                    (
                        "reference/coordination/shared/closeout-recovery.md",
                        "Call the paired closeout yourself and start no Manager for that step alone. This grants no other execution authority. Human-pinned approvals remain human; never substitute raw Git or self-approval.",
                    ),
                ),
            ),
            (
                "roles/architect.md:13",
                (
                    (
                        "roles/architect.md",
                        "Reviews, cold reads, re-checks, sweeps and Investigator reports are advice, not requirements. A finding that only corrects a wrong fact may be applied. A finding that adds, narrows or overrides a rule beyond the developer's words goes to the developer first, quoted beside the developer's earlier words.",
                    ),
                ),
            ),
            (
                "roles/orchestrator.md:0",
                (
                    (
                        "roles/orchestrator.md",
                        "Coordinate one selected sprint at Projects altitude for 2+ concurrent masters (or developer-requested single-master sprint). Paseo state does not record Agents Remember (AR) acceptance.",
                    ),
                ),
            ),
            (
                "roles/orchestrator.md:1",
                (
                    (
                        "roles/orchestrator.md",
                        "Reuse every existing Manager named in Architect's handover and start 1 Manager per other master via `role_start` under sprint (permitted: Manager, Worker, Reviewer, Curator, Investigator). Keep returned IDs, address explicitly, never proxy invisibly.",
                    ),
                ),
            ),
            (
                "roles/orchestrator.md:2",
                (
                    (
                        "roles/orchestrator.md",
                        "Handle operational order, pre/post-landing checks, and delegated paired closeout. Dashboard Orchestrators have no parent (ask developer in own chat).",
                    ),
                ),
            ),
            (
                "roles/orchestrator.md:3",
                (
                    (
                        "roles/orchestrator.md",
                        "Decide ordinary readings within promise and keep 3 durable task notes (name paths in report; no scope/promise authority): rulings record, requirement-change list, status file (Architect reads independently).",
                    ),
                ),
            ),
            (
                "roles/orchestrator.md:4",
                (
                    (
                        "roles/orchestrator.md",
                        "Report permission-blocked starts upward (or own chat if parentless); never start the blocked role or invent an ID.",
                    ),
                    (
                        "roles/orchestrator.md",
                        "Start Investigator via `role_start` on sprint (or sprint + 1 master; never leaf) with concern as first `role_message`.",
                    ),
                ),
            ),
            (
                "roles/orchestrator.md:5",
                (
                    (
                        "roles/orchestrator.md",
                        "Message handover parent ID via `role_message` for developer decisions: new/dropped scope or promise changes; developer-only approvals; role-default overrides; small item costing ~1 hour or 2nd repair round/freeze (with hours & proposal); unremovable blockers. Forward Manager's Architect matters unchanged, plus developer questions and permission notices. Send report path when finished or unfinishable; no status-only messages or extra landing notices.",
                    ),
                ),
            ),
            (
                "roles/orchestrator.md:6",
                (
                    (
                        "roles/orchestrator.md",
                        "On refusals other than `held` or full-store, log matter & refusal in status file and continue independent work without deciding developer questions.",
                    ),
                    (
                        "core/every-role.md",
                        "`held` = queued; never resend, withdraw via `withdraw_held_message_id`, open supplied artifacts first.",
                    ),
                    (
                        "core/authority.md",
                        "A held receipt means queued eventual delivery, possibly many turns later. Never resend it. Count a handover as taken only when the recipient's reply, report or intake names the exact object and shows the requested work or its start. Open every supplied recipient artifact before you call one absent. Withdraw a superseded held message with `withdraw_held_message_id`. A receiver does not poll.",
                    ),
                ),
            ),
            (
                "roles/orchestrator.md:7",
                (
                    (
                        "roles/orchestrator.md",
                        "Managers keep leaf finding-trail judgment, leaf-diff/repair/evidence loops, and planned-gap skeleton answers",
                    ),
                    (
                        "roles/orchestrator.md",
                        "Keep reports, findings, review, curation, and publication separate; never self-review. Plan-review and super-exit have no startable Reviewer seat -> bring to Architect for developer with sprint-level Reviewer idea.",
                    ),
                    (
                        "core/acceptance.md",
                        "It does not establish that the required artifact exists, is current or satisfies its requirement.",
                    ),
                ),
            ),
            (
                "roles/manager.md:0",
                (
                    (
                        "roles/manager.md",
                        "Coordinate one canonical master in Agents Remember (AR), not the portfolio. Report to launching parent unless Architect instructs an Orchestrator recipient.",
                    ),
                ),
            ),
            (
                "roles/manager.md:1",
                (
                    (
                        "roles/manager.md",
                        "Inspect full diff, verdicts, and listed final checks before gates; keep closeout/integration/status/acceptance/landing order without relaying freeze/finding/verdict/memory changes.",
                    ),
                    (
                        "roles/manager.md",
                        "Start/finalize/abandon mirror stage automatically (`task_doc set_status` only for correction/org-master completion; you set no step for another agent).",
                    ),
                    (
                        "roles/manager.md",
                        "(dashboard Managers have no parent; invent no ID)",
                    ),
                ),
            ),
            (
                "roles/manager.md:2",
                (
                    (
                        "roles/manager.md",
                        "**You decide:** leaf order, seat starts/replacements, operational issues, ordinary promise readings, pre/post-landing checks, delegated integration",
                    ),
                    (
                        "roles/manager.md",
                        "1 infra rerun on same operands",
                    ),
                    (
                        "roles/manager.md",
                        "Start Investigator via `role_start` on master/sprint (never leaf) with concern as first `role_message`.",
                    ),
                ),
            ),
            (
                "roles/manager.md:3",
                (
                    (
                        "roles/manager.md",
                        "Keep 3 master notes (name paths in report; no scope/promise authority): rulings, requirement-change list, status file.",
                    ),
                ),
            ),
            (
                "roles/manager.md:4",
                (
                    (
                        "roles/manager.md",
                        "**Send to developer:** scope/promise changes, developer-only approvals, role-default overrides, small item costing ~1h or 2nd repair round/freeze (hours + proposal), unremovable blockers.",
                    ),
                    (
                        "roles/manager.md",
                        "Message recorded recipient, else parent's role with its task references (or parent ID for developer questions), else own chat via `role_message` (dashboard Managers have no parent; invent no ID); route Architect matters via Orchestrator except direct skeletons; send closeout notices & final report path (no status-only notices).",
                    ),
                ),
            ),
            (
                "roles/manager.md:5",
                (
                    (
                        "roles/manager.md",
                        "When launching Architect names an Orchestrator ID on a 2nd master, record it and switch recipient (launch parent stays fixed).",
                    ),
                    (
                        "reference/coordination/shared/closeout-recovery.md",
                        "The Manager records that instruction and the Orchestrator's agent identifier in its report and uses it as its reporting recipient. The Manager's launch parent stays fixed; only that Architect instruction changes its reporting recipient.",
                    ),
                ),
            ),
            (
                "roles/manager.md:6",
                (
                    (
                        "roles/manager.md",
                        "Report blocked starts upward; log undelivered messages in status file.",
                    ),
                ),
            ),
            (
                "roles/manager.md:7",
                (
                    (
                        "roles/manager.md",
                        "log undelivered messages in status file.",
                    ),
                ),
            ),
            (
                "operations/planning.md:0",
                (
                    (
                        "operations/planning.md",
                        "Follow [owner](coordination.md#choose-the-coordination-owner), keep 1 owner/task and real IDs",
                    ),
                    (
                        "operations/coordination.md",
                        "1 master -> 1 Manager; 2+ concurrent masters -> 1 sprint Orchestrator (1 Manager/master). Direct Architect coordination or single-master Orchestrator needs developer instruction; report refused starts without fallback.",
                    ),
                ),
            ),
            (
                "operations/coordination.md:0",
                (
                    (
                        "operations/coordination.md",
                        "The Manager calls `role_start` once per stage: Worker first, Reviewer at the freeze, and Curator when curation begins.",
                    ),
                ),
            ),
            (
                "operations/coordination.md:1",
                (
                    (
                        "operations/coordination.md",
                        "The Worker's freeze notice and the Reviewer's code-`PASS` notice go to the Manager first.",
                    ),
                ),
            ),
            (
                "operations/coordination.md:2",
                (("operations/coordination.md", "Start only the Worker at build."),),
            ),
            (
                "operations/coordination.md:3",
                (
                    (
                        "operations/coordination.md",
                        "Ordinary curation follows code `PASS` under Manager's master;",
                    ),
                ),
            ),
            (
                "operations/coordination.md:4",
                (
                    (
                        "operations/coordination.md",
                        "once Reviewer exists, Worker sends freeze directly.",
                    ),
                ),
            ),
            (
                "operations/coordination.md:5",
                (
                    (
                        "operations/coordination.md",
                        "assigned packet cold read without an early normal leaf Reviewer;",
                    ),
                ),
            ),
            (
                "operations/coordination.md:6",
                (
                    (
                        "operations/coordination.md",
                        "ordinary delivery curation and knowledge check still follow code `PASS`",
                    ),
                ),
            ),
            (
                "operations/coordination.md:7",
                (
                    (
                        "operations/coordination.md",
                        "Leaf seats message directly via `role_message` (freeze->Reviewer->Worker; passed freeze & lists->Curator<->Reviewer; post-`PASS` final checks->Manager & Reviewer; Curator code concerns: [exceptions](../reference/curator/exceptions-and-recovery.md#memory-tool-failures-code-concerns-and-sync-conflicts)).",
                    ),
                    (
                        "reference/curator/exceptions-and-recovery.md",
                        "Send evidenced code concerns to both Worker and Reviewer, including after passing code review, showing the requirement or report mismatch.",
                    ),
                    (
                        "reference/curator/exceptions-and-recovery.md",
                        "The Reviewer records a finding and reopens the point, or explains its rejection; the Curator records the answer, reports both the finding and the answer, and sends unresolved disagreement to the Manager.",
                    ),
                    (
                        "operations/coordination.md",
                        "<!-- ar:roles architect,orchestrator --> Manager decides gates and retains closeout/integration/status/acceptance/landing order without relaying freeze/finding/verdict/memory changes.",
                    ),
                ),
            ),
            (
                "operations/coordination.md:8",
                (
                    (
                        "operations/coordination.md",
                        "1 master -> 1 Manager; 2+ concurrent masters -> 1 sprint Orchestrator (1 Manager/master). Direct Architect coordination or single-master Orchestrator needs developer instruction; report refused starts without fallback.",
                    ),
                ),
            ),
            (
                "operations/coordination.md:9",
                (
                    (
                        "operations/coordination.md",
                        "Send via `role_message` by ID or role+task refs (role != self; invent no IDs).",
                    ),
                ),
            ),
            (
                "operations/coordination.md:10",
                (
                    (
                        "operations/curation.md",
                        "Send memory candidate to Reviewer via `role_message` (sibling role, leaf refs, whole IDs);",
                    ),
                ),
            ),
            (
                "operations/coordination.md:11",
                (
                    (
                        "operations/coordination.md",
                        "Empty seats report upward; 2 occupants refuse naming both;",
                    ),
                ),
            ),
            (
                "operations/coordination.md:12",
                (("operations/coordination.md", "role != self"),),
            ),
            (
                "operations/coordination.md:13",
                (
                    (
                        "operations/coordination.md",
                        "active turns may `held` or cancel (never into permission prompts);",
                    ),
                ),
            ),
            (
                "operations/coordination.md:14",
                (
                    (
                        "operations/coordination.md",
                        "active turns may `held` or cancel",
                    ),
                ),
            ),
            (
                "operations/coordination.md:15",
                (
                    (
                        "operations/coordination.md",
                        "(never into permission prompts);",
                    ),
                    (
                        "reference/coordination/shared/closeout-recovery.md",
                        "Before deciding a formal permission, follow [Formal permissions](../../../operations/orientation.md#formal-permissions) for the Architect's own-seat answers and the prompts the developer decides.",
                    ),
                    (
                        "reference/coordination/shared/closeout-recovery.md",
                        "Before acting on a denied or unanswered harness request, read [Honor harness permission decisions](../../../operations/orientation.md#honor-harness-permission-decisions) for the waiting, denial and non-bypass rules.",
                    ),
                ),
            ),
            (
                "operations/coordination.md:16",
                (
                    (
                        "operations/coordination.md",
                        "archived seats refuse (use successor).",
                    ),
                ),
            ),
            (
                "operations/coordination.md:17",
                (
                    (
                        "operations/coordination.md",
                        "`wait` returns text, failure/cancel, `permission-pending`, timeout, or `accepted`; never mutually wait.",
                    ),
                ),
            ),
            (
                "operations/coordination.md:18",
                (
                    (
                        "operations/coordination.md",
                        "timeout, or `accepted`;",
                    ),
                ),
            ),
            (
                "operations/coordination.md:19",
                (
                    (
                        "operations/coordination.md",
                        "active turns may `held` or cancel",
                    ),
                ),
            ),
            (
                "operations/coordination.md:20",
                (("operations/coordination.md", "never mutually wait."),),
            ),
            (
                "operations/coordination.md:22",
                (
                    (
                        "core/every-role.md",
                        "Continue ended child turns with unfinished work and no valid wait/stop.",
                    ),
                ),
            ),
            (
                "operations/coordination.md:23",
                (
                    (
                        "roles/architect.md",
                        "Release coordination after handover (no completion `wait`);",
                    ),
                ),
            ),
            (
                "operations/coordination.md:24",
                (
                    (
                        "operations/coordination.md",
                        "reconcile uncertain starts via same request ID.",
                    ),
                    (
                        "roles/orchestrator.md",
                        "never start the blocked role or invent an ID.",
                    ),
                ),
            ),
            (
                "operations/coordination.md:25",
                (
                    (
                        "operations/orientation.md",
                        "dashboard roles need no parent.",
                    ),
                ),
            ),
            (
                "SKILL.md:0",
                (
                    (
                        "SKILL.md",
                        "Use the agent IDs these tools return and the sender line of each message you receive; never invent a sender, recipient, parent, or delivery result.",
                    ),
                ),
            ),
            (
                "SKILL.md:1",
                (
                    (
                        "SKILL.md",
                        "AR remains authoritative for canonical tasks, requirements, knowledge, curation, and paired Git operations.",
                    ),
                ),
            ),
            (
                "SKILL.md:2",
                (
                    (
                        "SKILL.md",
                        "An Architect first delegates coordination to one Manager for one master, or to one Orchestrator on the sprint when two or more masters are worked on at the same time. The Orchestrator starts one Manager per master. Only the developer may choose direct coordination by the Architect. The developer may also ask for an Orchestrator above a single master. A role started from the dashboard has no parent agent and needs none. Existing approvals and rulings remain durable across reconnects and compaction; ask again only for new or changed scope, a real requirement conflict, or an unresolved human-pinned decision.",
                    ),
                ),
            ),
            (
                "roles/curator.md:0",
                (
                    (
                        "roles/curator.md",
                        "Open briefs (`read_ar_files`/`context_packet`: `task_context`; `knowledge_read`: `workspace.memoryRoot`). Scope MCP calls with contract",
                    ),
                    (
                        "operations/curation.md",
                        "Verify writer outputs resolve in `workspace.memoryRoot`.",
                    ),
                ),
            ),
            (
                "roles/curator.md:1",
                (
                    (
                        "roles/curator.md",
                        "Write `onboarding/`+`knowledge/` via one writer (`knowledge_write` or serving-build CLI `--commit`/`commit: true`; missing writer != correct-input refusal); never edit derived indexes.",
                    ),
                ),
            ),
            (
                "roles/curator.md:2",
                (
                    (
                        "reference/curator/repository-foundation.md",
                        "Report a missing task; never fabricate a task, worktree or enclosure.",
                    ),
                ),
            ),
            ("roles/curator.md:3", (("roles/curator.md", "`knowledge_write`"),)),
            (
                "roles/curator.md:4",
                (
                    (
                        "core/every-role.md",
                        "For correct-input refused covered writes, follow [refused-write](skill://agents-remember/skills/l-01-agent-lifecycles/reference/refused-write.md) (same tool and input class) before applying a stop.",
                    ),
                    (
                        "reference/refused-write.md",
                        "This exception covers only the same tool and input class that refused; every other write still uses its tool.",
                    ),
                ),
            ),
            (
                "roles/curator.md:5",
                (
                    (
                        "roles/curator.md",
                        "`knowledge_write` `commit: true` writes files, not Git",
                    ),
                    (
                        "reference/curator/publication-report.md",
                        "`--commit` authorizes file writes, never a Git commit or acceptance.",
                    ),
                ),
            ),
            (
                "roles/curator.md:6",
                (
                    (
                        "operations/curation.md",
                        "require readable worklist before ingest;",
                    ),
                    (
                        "reference/curator/exceptions-and-recovery.md",
                        "worklist count",
                    ),
                ),
            ),
            (
                "roles/curator.md:7",
                (
                    ("operations/orientation.md", "dashboard roles need no parent."),
                    (
                        "SKILL.md",
                        "A role started from the dashboard has no parent agent and needs none.",
                    ),
                ),
            ),
            ("roles/curator.md:8", (("roles/curator.md", "role starts"),)),
            (
                "roles/curator.md:9",
                (
                    (
                        "core/invariants.md",
                        "A harness without sub-agents can still start a role agent through the framework.",
                    ),
                    (
                        "reference/curator/exceptions-and-recovery.md",
                        "host/helper support (delegated work and how it was verified; state explicitly when none was used).",
                    ),
                ),
            ),
            (
                "operations/curation.md:0",
                (
                    (
                        "operations/curation.md",
                        "1. Reconcile intent/code; verify card sentences vs code.",
                    ),
                ),
            ),
            (
                "operations/curation.md:1",
                (
                    (
                        "operations/curation.md",
                        "Manager notices: delivery/seat, authority, beyond-limit passes, disputed findings, reserved sync steps;",
                    ),
                    (
                        "reference/worker/sync-and-repairs.md",
                        "When a required Manager notice fails delivery for a Worker, Curator or Reviewer, record the notice as pending, continue independent work, and retry the notice through `role_message`. Never turn a delivery failure into a question to the developer in your own chat.",
                    ),
                ),
            ),
            (
                "operations/curation.md:2",
                (
                    (
                        "roles/curator.md",
                        "[handover]({{detail:reference/curator/memory-handover.md}})",
                    ),
                    (
                        "reference/curator/memory-handover.md",
                        "The Reviewer compares the leaf's base and candidate in Git: the code trees and the knowledge files in both memory trees. The Curator records no separate comparison.",
                    ),
                ),
            ),
            (
                "roles/investigator.md:0",
                (
                    (
                        "roles/investigator.md",
                        "Report what is and what is not, with evidence for each. State no requirement, ruling or duty of your own. Name a needed change as an open question for the parent, not a fix to apply.",
                    ),
                ),
            ),
        )
        for case, expectations in required:
            for source, clause in expectations:
                with self.subTest(case=case, source=source, clause=clause):
                    reading = " ".join(read(LIFECYCLE / source).split())
                    expected = " ".join(clause.split())
                    self.assertIn(expected, reading)
                    if case in {
                        "roles/architect.md:4",
                        "roles/orchestrator.md:6",
                        "operations/coordination.md:7",
                        "operations/curation.md:2",
                    }:
                        mutant = reading.replace(expected, "", 1)
                        self.assertNotEqual(mutant, reading)
                        with self.assertRaises(AssertionError):
                            self.assertIn(expected, mutant)

        assert_coordination_routes(self, CANONICAL / LIFECYCLE)
        assert_git_comparison_scope(self)
        assert_message_handover_delivery(self, LAUNCHER_ROLES)
