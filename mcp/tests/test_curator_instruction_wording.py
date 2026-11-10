"""Curator and coordination instruction wording, moved out of the role wording module.

MIK-R194 rule 10: the Curator's own pins moved here when its authoring routes changed, together with
enough whole test functions that `test_role_instruction_wording.py` stays under the size rail. The
moved tests read the same canonical skill tree and the same built parser, and decide the same
questions they decided there.
"""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from pathlib import Path
from typing import get_args

from agents_remember.application.skill_resources.provider import shipped_composition_corpus
from agents_remember.cli.__main__ import build_parser
from agents_remember.models.role_identity import canonical_role
from agents_remember.models.role_launcher import LauncherRole
from agents_remember.models.tools.public_roster import PUBLIC_TOOLS
from agents_remember_test_support.testing.capsule_wording import (
    assert_git_comparison_scope,
)
from agents_remember_test_support.testing.curator_contracts import (
    require_coordination_routes,
)
from agents_remember_test_support.testing.curator_instruction_delivery import (
    assert_coordination_steps,
    assert_correct_write_ladder,
    assert_foundation_boundaries,
    assert_reviewer_initial_intake_delivery,
)
from agents_remember_test_support.testing.leaf_instruction_wording import (
    SERVED_REFUSED_WRITE,
)
from role_instruction_wording_support import assert_message_handover_delivery
from test_l84_capsule_delivery_proofs import _compiled_outcome

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
LIFECYCLE = Path("l-01-agent-lifecycles")
CANONICAL = REPOSITORY_ROOT / "skills"
LAUNCHER_ROLES: tuple[str, ...] = tuple(
    dict.fromkeys(canonical_role(role) for role in get_args(LauncherRole))
)
STARTERS = ("architect", "orchestrator", "manager")


def read(relative: Path) -> str:
    return (CANONICAL / relative).read_text(encoding="utf-8")


def role_text(role: str) -> str:
    return " ".join(read(LIFECYCLE / "roles" / f"{role}.md").split())


class CuratorInstructionWordingTests(unittest.TestCase):
    def test_coordination_detail_is_offered_at_all_eight_failed_scenario_steps(self) -> None:
        "A detail existing at a later replacement step cannot answer an earlier case."
        assert_coordination_steps(self, CANONICAL / LIFECYCLE, _compiled_outcome)

    def test_reviewer_waits_for_initial_mode_before_enforcing_complete_intake(self) -> None:
        "Follow the delivered intake owner before judging a received intake."
        assert_reviewer_initial_intake_delivery(CANONICAL / LIFECYCLE)

    def test_correct_write_ladder_distinguishes_named_writes_from_other_stops(self) -> None:
        "The four failed roles receive the named ladder without losing hard stops."
        assert_correct_write_ladder(self, CANONICAL / LIFECYCLE, role_text)

    def test_foundation_intake_and_correction_keep_their_task_and_truth_boundaries(self) -> None:
        assert_foundation_boundaries(self, read, LIFECYCLE)

    def test_producer_checks_and_review_truth_criteria_keep_their_own_homes(self) -> None:
        operation = " ".join(read(LIFECYCLE / "operations/review.md").split())
        for clause in (
            "Before sending your hand-off list, run `handoff-check` against that freeze",
            "with the serving build's interpreter, identified by `server_info` on `agents-remember-task`",
            "Name the result.",
        ):
            self.assertIn(clause, operation)
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
        # P5-18: the shared every-role paragraph carries the one recovery pointer.
        self.assertNotIn("{{detail:reference/refused-write.md}}", role)
        self.assertIn("Only the tool and input class of a covered correct write", role)
        self.assertIn("A missing writer is not a correct-write refusal", role)
        self.assertIn(
            "Edit no code, gate, lifecycle state, worktree contract or closeout state.", role
        )
        self.assertNotIn("No other route writes knowledge from this seat.", role)
        self.assertNotIn("never hand-write records, sidecar entries or history", role)

    def test_correct_write_procedure_is_delivered_at_the_refusal_step(self) -> None:
        # P5-18: the Curator reaches the procedure through the shared paragraph's served
        # link at the refusal step, not through a capsule detail of its own.
        shared = (CANONICAL / LIFECYCLE / "core/every-role.md").read_text(encoding="utf-8")
        [paragraph] = [
            line for line in shared.splitlines() if "correct-input refusal of a covered" in line
        ]
        self.assertIn("follow [correct-write recovery](" + SERVED_REFUSED_WRITE + ")", paragraph)
        with shipped_composition_corpus() as (origin, _manifest):
            detail = (origin / "reference/refused-write.md").read_text(encoding="utf-8")
        for clause in (
            "Check the build and route.",
            "Take an honest route that the tool admits.",
            "Otherwise make that write openly by hand.",
            "before writing",
            "every other write still uses its tool",
            "check that still works",
            "issue row",
            "full report",
            "Rendered copies are never parsed back",
            "Repair every validator and gate finding at once.",
            "Read, check, validation and sync refusals",
        ):
            with self.subTest(clause=clause):
                self.assertIn(clause, detail)

    def test_current_coordination_and_curator_rules(self) -> None:
        # One obligation group per original case, including rules moved to their single home.
        required = (
            (
                "roles/architect.md:0",
                (
                    (
                        "roles/architect.md",
                        "Answer a developer message before any other work continues, with an answer, not a status line. A message that begins with `From <role> · <task> · agent <id>` is a role agent's message, not the developer's answer or approval.",
                    ),
                ),
            ),
            (
                "roles/architect.md:1",
                (
                    (
                        "roles/architect.md",
                        "For work inside one master, first start one Manager on that canonical master and hand it the coordination. When two or more masters are worked on at the same time, first start one Orchestrator on the selected canonical sprint; it starts one Manager for each master.",
                    ),
                    (
                        "roles/architect.md",
                        "Direct coordination is the exception. Only when the developer tells you to coordinate the role agents yourself, directly coordinate distinct Worker, Reviewer, and Curator agents. If the developer asks for an Orchestrator above a single master, start one.",
                    ),
                    (
                        "roles/architect.md",
                        "Keep one owner per task and one explicitly addressed recipient per message.",
                    ),
                ),
            ),
            (
                "roles/architect.md:2",
                (
                    (
                        "roles/architect.md",
                        "If the coordinating agent's start is refused, tell the developer the refusal and its reason. Do not fall back to direct coordination without the developer's instruction.",
                    ),
                ),
            ),
            (
                "roles/architect.md:3",
                (
                    (
                        "roles/architect.md",
                        "If a second master starts while a Manager works directly under you, start one Orchestrator on the sprint. Give it the existing Manager's exact agent ID, canonical master reference and current state; it reuses that Manager and starts no duplicate.",
                    ),
                    (
                        "roles/architect.md",
                        "Name the Orchestrator's agent ID to the existing Manager in one message. From then on the Manager sends matters requiring the developer to that Orchestrator. This changes its reporting recipient, not its fixed launch parent.",
                    ),
                ),
            ),
            (
                "roles/architect.md:4",
                (
                    (
                        "roles/architect.md",
                        "For each role assignment, call `role_start` on the `agents-remember-task` tool server with the role, the exact canonical sprint, master, and leaf references its class requires, and a request ID you choose.",
                    ),
                    (
                        "roles/architect.md",
                        "It resolves or creates the AR paired enclosure, compiles the role's capsule and handover, and starts the agent in Paseo with you as its parent. The agent begins its assignment at once.",
                    ),
                    (
                        "roles/architect.md",
                        "Keep the returned agent ID, report path, and handover artifact path. You may start Investigator, Orchestrator, Manager, Worker, Reviewer and Curator.",
                    ),
                    (
                        "operations/coordination.md",
                        "For an uncertain start, follow [Start and configure role agents](../core/every-role.md#start-and-configure-role-agents) for same-identifier reconciliation and refusal reporting.",
                    ),
                    (
                        "core/every-role.md",
                        "Architect, Orchestrator, Manager: Start each role once, with the exact task selection and request identifier. If the result is uncertain, reconcile it with that same identifier. Report a refusal without switching to another tool.",
                    ),
                    (
                        "roles/architect.md",
                        "After review, under developer-chosen direct coordination send a repair for the specific findings to the same Worker with `role_message` on `agents-remember-task` instead of starting another Worker. Under delegation, send it to the Manager or Orchestrator coordinating the work.",
                    ),
                ),
            ),
            (
                "roles/architect.md:5",
                (
                    (
                        "roles/architect.md",
                        "Keep the conversation with the developer, the plan and its order, the requirements and their text, and the decision on what needs the developer. Hand the Manager or Orchestrator the role starts and messages, the order of the day, operational problems, ordinary requirement interpretation within the intended promise and operational rulings, checks before and after a landing, and paired closeout within its delegated authority.",
                    ),
                ),
            ),
            (
                "roles/architect.md:6",
                (
                    (
                        "roles/architect.md",
                        "Read the coordinating agent's rulings record, list of requirement sentences to change, and status file on your own; it sends no routine ruling or landing notice. Bring the requirement texts in line with its rulings within the intended promise. New or dropped scope and changes to that promise still need the developer's decision.",
                    ),
                ),
            ),
            (
                "roles/architect.md:7",
                (
                    (
                        "roles/architect.md",
                        "After the handover, release execution coordination to the Orchestrator or Manager and continue the developer conversation and status reading. Do not hold a `wait` on an Orchestrator or Manager for execution completion through `role_message` on `agents-remember-task`; those coordinators own their completion loops.",
                    ),
                ),
            ),
            (
                "roles/architect.md:8",
                (
                    (
                        "roles/architect.md",
                        "When the developer answers or gives a ruling, send that actual answer or ruling to the requesting Manager or Orchestrator with `role_message` on `agents-remember-task`, addressed by its recorded agent ID. A reply to a coordinating agent's wait that repeats its question is not the developer's decision; relay the decision only once you have obtained the developer's actual answer or ruling.",
                    ),
                ),
            ),
            (
                "roles/architect.md:9",
                (
                    (
                        "roles/architect.md",
                        "When an Investigator is needed, start it with `role_start` on `agents-remember-task` without a task reference, on a sprint, or on a sprint and master. Send the scoped concern as your first `role_message` on `agents-remember-task` to the returned agent ID. A Manager or Orchestrator may also start an Investigator within its own scope.",
                    ),
                ),
            ),
            (
                "roles/architect.md:10",
                (
                    (
                        "roles/architect.md",
                        "You have no parent; ask the developer in your own chat. Put requests for a ruling there too; the developer answers there.",
                    ),
                    (
                        "roles/architect.md",
                        "Under developer-chosen direct coordination, review the full candidate diff, including unattributed changes. Under delegation, the Manager owns the leaf-diff and repair loop and supplies aggregate evidence through the Orchestrator where one exists. Treat invariant-family attribution as an additional review dimension, not a filter on changed files.",
                    ),
                    (
                        "roles/architect.md",
                        "Keep a finished turn, semantic review, curation, and paired Git publication as separate facts. Do not accept your own work or convert green checks into requirement acceptance. Record decisions and task truth through the existing AR task and data owners.",
                    ),
                ),
            ),
            (
                "roles/architect.md:11",
                (
                    (
                        "roles/architect.md",
                        "You may check, measure, run tests and builds, read the code and sources of the product, the host and the harnesses, and try things out in a sandbox yourself, as the developer would. This includes a check before a landing. Such work informs your rulings, your requirements and the developer.",
                    ),
                    (
                        "roles/architect.md",
                        "It never takes the place of a leaf's build, review, curation or gate. Change no leaf's code or memory: those go through its Worker, Reviewer and Curator. Grade nothing you wrote. Run nothing of yours in a candidate's worktree; work on a copy.",
                    ),
                    (
                        "roles/architect.md",
                        "Reading code and sources for a check is not loading another task's documents or another role's instructions. Preserve the read rules under [Read knowledge and code](#read-knowledge-and-code) and [Confirm the assignment](../operations/orientation.md#confirm-the-assignment) for task-document scope and supplied bindings.",
                    ),
                    (
                        "roles/architect.md",
                        "Perform no step of a landing except the paired closeout under developer-chosen direct coordination described below. A check before a landing is not a step of it.",
                    ),
                    (
                        "roles/architect.md",
                        "For a check or investigation you do not do yourself, give it to the Manager or Orchestrator coordinating the work. Under developer-chosen direct coordination, assign a leaf's check to its Worker or Reviewer and a check or investigation that belongs to no leaf to an Investigator.",
                    ),
                ),
            ),
            (
                "roles/architect.md:12",
                (
                    (
                        "roles/architect.md",
                        "You may author requirements, tasks, plans, and rulings. Do not write onboarding.",
                    ),
                    (
                        "roles/architect.md",
                        "Flat mode here means only the developer-chosen direct-coordination exception. When no Manager or Orchestrator owns closeout, you may coordinate the existing paired closeout only under the already-delegated authority of [Authorized Closeout](../operations/closeout.md), after independent review and required curation evidence exist.",
                    ),
                    (
                        "roles/architect.md",
                        "In that case, call the paired closeout yourself and start no Manager for that step alone. It grants no other execution. Human-pinned approvals remain human; never substitute raw Git or self-approval.",
                    ),
                    (
                        "roles/architect.md",
                        "A high-impact design or security choice, requirement contradiction, missing authority, or scope change returns to the developer. One concise report preserves the decision, references, evidence, open questions, and limits. No completion claim replaces inspection of the artifact.",
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
                        "You coordinate one selected sprint when two or more masters are worked on at the same time, or when the developer explicitly asks for an Orchestrator above a single master. You work at Projects altitude, the execution location supplied by your launch. The launch also supplies the canonical sprint reference and allowed workspace.",
                    ),
                    (
                        "roles/orchestrator.md",
                        "An agent's state in Paseo does not record Agents Remember (AR) acceptance.",
                    ),
                ),
            ),
            (
                "roles/orchestrator.md:1",
                (
                    (
                        "roles/orchestrator.md",
                        "For the accepted objective, reuse each existing Manager named in the Architect's handover. Start one Manager for each other master with `role_start` on the `agents-remember-task` tool server, using a selection under your own sprint. Those Managers coordinate their distinct Workers, Reviewers, and Curators.",
                    ),
                    (
                        "roles/orchestrator.md",
                        "You may start Manager, Worker, Reviewer, Curator, and Investigator. Before starting or messaging a role, read [role assignments](../operations/coordination.md#start-one-canonical-role-assignment) for selection and start rules, [the first intake](../operations/coordination.md#start-leaf-stages-and-deliver-the-first-intake) for stage timing and intake fields, and [message delivery](../operations/coordination.md#address-and-deliver-a-message) for addresses, seats and delivery state. Your tool server's binding names you as the sender. Address each recipient explicitly by agent ID or by role and task references, and keep the returned agent IDs; do not act as an invisible proxy.",
                    ),
                ),
            ),
            (
                "roles/orchestrator.md:2",
                (
                    (
                        "roles/orchestrator.md",
                        "Keep each work item tied to its canonical AR task and primary requirement; a start is not completion. Handle the order of the day, operational problems, checks before and after a landing, and paired closeout within delegated authority.",
                    ),
                    (
                        "roles/orchestrator.md",
                        "An Orchestrator started from the dashboard has no parent and needs none. Put developer decisions in your own chat; do not invent a parent.",
                    ),
                ),
            ),
            (
                "roles/orchestrator.md:3",
                (
                    (
                        "roles/orchestrator.md",
                        "Decide ordinary requirement interpretation within the intended promise and operational rulings yourself, and keep the work going. Under the selected task's notes, keep:",
                    ),
                    ("roles/orchestrator.md", "one durable rulings record;"),
                    (
                        "roles/orchestrator.md",
                        "a list of requirement sentences that should change;",
                    ),
                    ("roles/orchestrator.md", "one status file."),
                    (
                        "roles/orchestrator.md",
                        "Name their paths in your report. When the Architect started you, it reads these files itself and aligns the requirement texts with the rulings. These records do not authorize new or dropped scope or a changed promise.",
                    ),
                ),
            ),
            (
                "roles/orchestrator.md:4",
                (
                    (
                        "roles/orchestrator.md",
                        "If your permissions prevent a needed role start, report the blocker to the agent that started you with `role_message` on `agents-remember-task`. Without a parent, report it in your own chat. Do not invent an Architect ID or start the blocked role yourself.",
                    ),
                    (
                        "roles/orchestrator.md",
                        "Assign a check or investigation that belongs to no leaf to an Investigator on your own sprint, or on your sprint and one of its masters. Send the scoped concern as your first `role_message` on `agents-remember-task` to its returned agent ID. Never select a leaf for this role.",
                    ),
                ),
            ),
            (
                "roles/orchestrator.md:5",
                (
                    (
                        "roles/orchestrator.md",
                        "When the Architect started you, message it for a developer decision about:",
                    ),
                    (
                        "roles/orchestrator.md",
                        "new or dropped scope, or a change to a requirement's promise;",
                    ),
                    ("roles/orchestrator.md", "something only the developer can do or approve;"),
                    ("roles/orchestrator.md", "an override of a role default;"),
                    (
                        "roles/orchestrator.md",
                        "a small item that has cost about an hour, or a second repair round or freeze; include its hours and a proposal;",
                    ),
                    (
                        "roles/orchestrator.md",
                        "a blocker that none of your own decisions can remove.",
                    ),
                    (
                        "roles/orchestrator.md",
                        "Send these with `role_message` on `agents-remember-task` to the parent agent ID in your handover. Forward only questions requiring the developer and the permission notices described under [Questions received from children](#questions-received-from-children). Send no other role messages or landing notices to the Architect. Ordinary requirement readings and operational rulings stay with you; do not ask the Architect to perform them.",
                    ),
                    (
                        "roles/orchestrator.md",
                        "Send one further message with your report path when the whole assignment is finished or cannot be finished. Send no other messages to the Architect.",
                    ),
                ),
            ),
            (
                "roles/orchestrator.md:6",
                (
                    (
                        "roles/orchestrator.md",
                        "When the send answers held, follow [the held-delivery rule](../core/every-role.md#parallel-work-and-waiting) for queued delivery, consumption and supersession. For a refusal other than held or full-store, write the matter and the refusal in your status file. Either way, continue the work that does not depend on the developer's decision, and do not decide the developer's question yourself.",
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
                        "A turn ending proves only that it ended. Verify each Manager's delivery and aggregate evidence. The Manager owns the leaf-diff, repair and evidence loop; you may inspect supplied actual diffs for acceptance without taking over that loop.",
                    ),
                    (
                        "roles/orchestrator.md",
                        "Request an independent Reviewer when the brief or risk requires it; a Reviewer never adjudicates its own work. Request a Curator for affected memory and onboarding when needed. Keep reports, findings, review, curation, and Git publication separately addressed. Use the existing AR task and paired Git owners for their semantic records.",
                    ),
                    (
                        "roles/orchestrator.md",
                        "A review of the sprint plan (plan review) or of the completed sprint\N{RIGHT SINGLE QUOTATION MARK}s handover (super-exit review) has no startable Reviewer seat. Bring either review to the Architect, who takes it to the developer with the sprint-level Reviewer idea.",
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
                        "Your canonical assignment is the selected master; you do not own the portfolio.",
                    ),
                    (
                        "roles/manager.md",
                        "When another agent starts you, report to that agent's role with its task references, unless the Architect changes your reporting recipient as described below.",
                    ),
                ),
            ),
            (
                "roles/manager.md:1",
                (
                    (
                        "roles/manager.md",
                        "Read each leaf's reports and task records yourself. Before directing handovers, repair loops, review rounds, memory passes or relays, read [leaf coordination procedures](../operations/coordination.md) for stage timing, review records, message delivery and relay limits. Inspect the complete changed-file diff and both verdicts before deciding the gate. Reviews supply evidence; you decide the gate and retain closeout, integration, status, acceptance writes and landing order. Relay no freeze, finding, verdict or memory change. Preserve stable requirement and finding identifiers. Distinguish implementation, review, curation and publication status.",
                    ),
                    (
                        "roles/manager.md",
                        "Start, finalize and abandon already publish the task stage and mirror it into the master\N{RIGHT SINGLE QUOTATION MARK}s row; do not follow them with a separate status write. Use `task_doc set_status` for an explicit correction and for organizational-master completion. That completion act remains yours.",
                    ),
                    (
                        "roles/manager.md",
                        "A Manager started from the dashboard has no parent and needs none. Put a needed developer decision in your own chat; invent no Architect identifier.",
                    ),
                ),
            ),
            (
                "roles/manager.md:2",
                (
                    (
                        "roles/manager.md",
                        "Decide leaf order, agent starts and replacements, operational problems, ordinary requirement interpretation within the intended promise, and checks before and after landing. Decide paired closeout and integration within your delegated authority.",
                    ),
                    (
                        "roles/manager.md",
                        "You own the task's construction: step order, evidence form, which existing test or command demonstrates a property, and sandbox or scratch instruments that change no product source. After a harness, loader or capture fault, you may order one rerun on the same operands. A second failure of one cause goes upward with the attempts already made.",
                    ),
                    ("roles/manager.md", "you decide the gate"),
                    (
                        "roles/manager.md",
                        "Assign a check or investigation that belongs to no leaf to an Investigator on your own sprint and master. Never select a leaf for this role. Send the scoped concern as your first `role_message` on `agents-remember-task` to the returned agent identifier.",
                    ),
                ),
            ),
            (
                "roles/manager.md:3",
                (
                    (
                        "roles/manager.md",
                        "Keep one durable rulings record, one list of requirement sentences that should change, and one status file under the selected master's notes. Name their paths in your report. The Architect reads them independently and aligns requirement text with the rulings. These records authorize no new or dropped scope and no changed promise.",
                    ),
                ),
            ),
            (
                "roles/manager.md:4",
                (
                    (
                        "roles/manager.md",
                        "When another agent started you, send your parent or instructed reporting recipient only matters that require the developer:",
                    ),
                    (
                        "roles/manager.md",
                        "new or dropped scope, or a change to a requirement's promise;",
                    ),
                    ("roles/manager.md", "something only the developer can do or approve;"),
                    ("roles/manager.md", "an override of a role default;"),
                    (
                        "roles/manager.md",
                        "a small item that has cost about an hour, or a second repair round or freeze, with the hours and a proposal;",
                    ),
                    ("roles/manager.md", "a blocker none of your own decisions can remove."),
                    (
                        "roles/manager.md",
                        (
                            "Send these with `role_message` on `agents-remember-task` to the instructed "
                            "reporting-recipient identifier in your report when one is recorded; otherwise use the parent "
                            "agent identifier in your handover. If you have neither, put the developer question in your "
                            "own chat. When the Orchestrator owns coordination, do not bypass it to the Architect except "
                            "for the skeleton handover above. Ordinary requirement readings and operational rulings remain "
                            "yours; keep the work going. Apart from the skeleton handover above, forward only questions "
                            "requiring the developer and the permission notices below."
                        ),
                    ),
                    (
                        "roles/manager.md",
                        (
                            "Apart from that skeleton handover, send no other role messages or landing notices to that "
                            "recipient, except the closeout-result notice below and one further message naming your report "
                            "path when the whole assignment is finished or cannot be finished. Address that final message "
                            "to the parent's role with its task references."
                        ),
                    ),
                ),
            ),
            (
                "roles/manager.md:5",
                (
                    (
                        "roles/manager.md",
                        "If the Architect that started you names an Orchestrator's agent identifier in a message when a second master starts, record the instruction and recipient identifier in your durable report. Send developer-needed matters to that Orchestrator from then on. This changes your reporting recipient, not your fixed launch parent. No other message changes whom you report to.",
                    ),
                ),
            ),
            (
                "roles/manager.md:6",
                (
                    (
                        "roles/manager.md",
                        "If you cannot start a needed role within your permissions, report the blocker to your parent or instructed recipient through `role_message` on `agents-remember-task`. Without a parent, report it in your own chat. Never start that role yourself or invent an Architect identifier.",
                    ),
                ),
            ),
            (
                "roles/manager.md:7",
                (
                    (
                        "roles/manager.md",
                        "If an upward message cannot be delivered, record the matter in your status file and continue all work independent of the developer\N{RIGHT SINGLE QUOTATION MARK}s decision. Do not decide the developer\N{RIGHT SINGLE QUOTATION MARK}s question yourself.",
                    ),
                ),
            ),
            (
                "operations/planning.md:0",
                (
                    (
                        "operations/planning.md",
                        "Before delegating coordination, read [Choose the coordination owner](coordination.md#choose-the-coordination-owner)",
                    ),
                    (
                        "operations/planning.md",
                        "Keep one explicit owner per task and preserve started agents' identifiers. Do not fabricate a sprint, master or agent identity for a taskless project role.",
                    ),
                    (
                        "operations/coordination.md",
                        "The Architect first delegates one master to one Manager. For two or more masters worked on at the same time, it delegates to one Orchestrator on the sprint; that Orchestrator starts one Manager per master. Each Manager coordinates distinct Workers, Reviewers and Curators.",
                    ),
                    (
                        "operations/coordination.md",
                        "Only the developer may choose direct coordination by the Architect. The developer may also request an Orchestrator above a single master.",
                    ),
                ),
            ),
            (
                "operations/coordination.md:0",
                (
                    (
                        "operations/coordination.md",
                        "The Manager calls `role_start` on `agents-remember-task` once per stage: Worker first, Reviewer at the freeze, and Curator when curation begins.",
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
                        "For ordinary delivery, curation begins after code review passes.",
                    ),
                ),
            ),
            (
                "operations/coordination.md:4",
                (
                    (
                        "operations/coordination.md",
                        "Once the Reviewer exists, the Worker sends its freeze directly to it.",
                    ),
                ),
            ),
            (
                "operations/coordination.md:5",
                (
                    (
                        "operations/coordination.md",
                        "An assigned packet cold read may occur without an early normal leaf Reviewer.",
                    ),
                ),
            ),
            (
                "operations/coordination.md:6",
                (
                    (
                        "operations/coordination.md",
                        "Ordinary delivery curation and the knowledge check still follow code `PASS`.",
                    ),
                ),
            ),
            (
                "operations/coordination.md:7",
                (
                    (
                        "operations/coordination.md",
                        "The Worker, Reviewer and Curator run the repair loop directly through `role_message` on `agents-remember-task`: Worker freeze to Reviewer; verdict and sealed findings to Worker; passed freeze and producer lists to Curator; memory change and verdict between Curator and Reviewer.",
                    ),
                    (
                        "operations/coordination.md",
                        "Before sending or answering a Curator's code concern, read [Curation failure handling](curation.md#failure-handling) for both recipients, post-`PASS` handling, the finding and answer, and disagreement routing.",
                    ),
                    (
                        "operations/curation.md",
                        "Send evidenced code concerns to both Worker and Reviewer, including after passing code review.",
                    ),
                    (
                        "operations/curation.md",
                        "The Reviewer records a finding and reopens the point, or explains its rejection. The Curator records the answer. Report the finding and answer; send unresolved disagreement to the Manager.",
                    ),
                    (
                        "operations/coordination.md",
                        # P5-07 OP-113: scoped to the Architect and Orchestrator; the Manager's
                        # home is roles/manager.md:52.
                        "<!-- ar:roles architect,orchestrator --> The Manager reads their reports and decides the gate. It retains closeout, integration, status, acceptance and landing order; it relays no freeze, finding, verdict or memory change.",
                    ),
                    (
                        "operations/coordination.md",
                        "Every handover keeps the paired code, memory and report paths and exact task references.",
                    ),
                ),
            ),
            (
                "operations/coordination.md:8",
                (
                    (
                        "operations/coordination.md",
                        "The Architect first delegates one master to one Manager. For two or more masters worked on at the same time, it delegates to one Orchestrator on the sprint; that Orchestrator starts one Manager per master. Each Manager coordinates distinct Workers, Reviewers and Curators.",
                    ),
                    (
                        "operations/coordination.md",
                        "Only the developer may choose direct coordination by the Architect. The developer may also request an Orchestrator above a single master. If the Architect's coordinating-agent start is refused, tell the developer the refusal and its reason. Do not substitute direct coordination.",
                    ),
                ),
            ),
            (
                "operations/coordination.md:9",
                (
                    (
                        "operations/coordination.md",
                        "Send one message to one agent with `role_message` on `agents-remember-task`. Address it by agent identifier or by role and task references.",
                    ),
                ),
            ),
            (
                "operations/coordination.md:10",
                (
                    (
                        "operations/coordination.md",
                        "A leaf handover uses the role and exact task reference in `leafSeats.roleMessageArguments`. Use any product-supplied agent identifier whole and unchanged.",
                    ),
                ),
            ),
            (
                "operations/coordination.md:11",
                (
                    (
                        "operations/coordination.md",
                        "The leaf resolver reaches a seat's single live agent. Two live occupants cause a refusal naming both; the tool never selects one. Report an empty-seat refusal once, with its text, through the role's recovery route; choose no candidate. A Manager uses its parent route, never itself.",
                    ),
                ),
            ),
            (
                "operations/coordination.md:12",
                (("operations/coordination.md", "A role address never means you yourself."),),
            ),
            (
                "operations/coordination.md:13",
                (
                    (
                        "operations/coordination.md",
                        "A message to a recipient already in a turn may be held until that turn ends. If the host delivers it into the running turn, that turn may be cancelled. If the host has not delivered it, the answer names `held` and its reason.",
                    ),
                ),
            ),
            (
                "operations/coordination.md:14",
                (
                    (
                        "operations/coordination.md",
                        "If the host has not delivered it, the answer names `held` and its reason.",
                    ),
                ),
            ),
            (
                "operations/coordination.md:15",
                (
                    (
                        "operations/coordination.md",
                        "The host is never sent a message for a recipient at a permission prompt: doing so would answer that permission with a denial.",
                    ),
                    (
                        "operations/coordination.md",
                        "Before deciding a formal permission, follow [Formal permissions](orientation.md#formal-permissions) for the Architect's own-seat answers and the prompts the developer decides.",
                    ),
                    (
                        "operations/coordination.md",
                        "Before acting on a denied or unanswered harness request, read [Honor harness permission decisions](orientation.md#honor-harness-permission-decisions) for the waiting, denial and non-bypass rules.",
                    ),
                ),
            ),
            (
                "operations/coordination.md:16",
                (
                    (
                        "operations/coordination.md",
                        "A closed session is resumed first. An archived or missing agent is refused and left as it is.",
                    ),
                ),
            ),
            (
                "operations/coordination.md:17",
                (
                    (
                        "operations/coordination.md",
                        "With `wait`, the call returns when the turn that took the message ends. The result contains one of:",
                    ),
                    ("operations/coordination.md", "the recipient's final text;"),
                    ("operations/coordination.md", "notice that its turn failed or was cancelled;"),
                    (
                        "operations/coordination.md",
                        "a pending permission, including for a wait on a held message;",
                    ),
                    (
                        "operations/coordination.md",
                        "a timeout while the message remains delivered;",
                    ),
                ),
            ),
            (
                "operations/coordination.md:18",
                (
                    (
                        "operations/coordination.md",
                        "`accepted` without text when the host cannot identify which turn took the message; read the reply later.",
                    ),
                ),
            ),
            (
                "operations/coordination.md:19",
                (
                    (
                        "operations/coordination.md",
                        "A message delivered during a running turn may receive that turn's own text; `detail` identifies this case.",
                    ),
                ),
            ),
            (
                "operations/coordination.md:20",
                (
                    (
                        "operations/coordination.md",
                        "Do not wait on an agent that may be waiting on you. Two mutually waiting agents remain stopped until a wait expires.",
                    ),
                ),
            ),
            (
                "operations/coordination.md:21",
                (
                    (
                        "operations/coordination.md",
                        "If your own tool calls end sooner than the wait, pass a shorter `timeout_seconds`.",
                    ),
                ),
            ),
            (
                "operations/coordination.md:22",
                (
                    (
                        "operations/coordination.md",
                        "An Orchestrator, Manager or developer-direct-coordinating Architect follows assignments until complete, blocked or awaiting a real developer decision. Starting a role does not complete coordination.",
                    ),
                ),
            ),
            (
                "operations/coordination.md:23",
                (
                    (
                        "operations/coordination.md",
                        "The Architect releases execution coordination after the handover and continues the developer conversation and status reading. It does not wait on an Orchestrator or Manager for execution completion.",
                    ),
                ),
            ),
            (
                "operations/coordination.md:24",
                (
                    (
                        "operations/coordination.md",
                        "On refusal or uncertainty, act on the named reason. Do not guess a parent or start duplicate work.",
                    ),
                ),
            ),
            (
                "operations/coordination.md:25",
                (
                    (
                        "operations/coordination.md",
                        "A dashboard-started role works directly with the developer and needs no parent.",
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
                        "Before intake and writer admission, read [required curation inputs](../operations/curation.md#required-inputs) for the candidate, scope and lists, and [bound intake and capabilities](../operations/curation.md#bound-paseo-intake-and-capabilities) for the serving writer and capability checks.",
                    ),
                ),
            ),
            (
                "roles/curator.md:1",
                (
                    (
                        "roles/curator.md",
                        "Use mounted `knowledge_write` on `agents-remember-task` or the admitted serving-build command-line writer with the task contract, handoff list and `--commit` or `commit: true`.",
                    ),
                ),
            ),
            (
                "roles/curator.md:2",
                (
                    (
                        "roles/curator.md",
                        "Never fabricate a task, worktree or enclosure for either route.",
                    ),
                ),
            ),
            ("roles/curator.md:3", (("roles/curator.md", "Use mounted `knowledge_write`"),)),
            (
                "roles/curator.md:4",
                (
                    (
                        "roles/curator.md",
                        "Only the tool and input class of a covered correct write admit open manual recovery; every other write retains its tool.",
                    ),
                ),
            ),
            (
                "roles/curator.md:5",
                (
                    (
                        "roles/curator.md",
                        "The commit option authorizes file writes, not Git or acceptance.",
                    ),
                ),
            ),
            (
                "roles/curator.md:6",
                (
                    (
                        "roles/curator.md",
                        "Report one full-intake worklist of distinct outstanding memory actions.",
                    ),
                ),
            ),
            (
                "roles/curator.md:7",
                (
                    ("roles/curator.md", "A dashboard-started Curator needs no parent."),
                    (
                        "operations/orientation.md",
                        "A parent agent is optional. A role started from the dashboard has none and needs none; the developer reads and answers in its own chat.",
                    ),
                ),
            ),
            ("roles/curator.md:8", (("roles/curator.md", "A Curator starts no role."),)),
            (
                "roles/curator.md:9",
                (("roles/curator.md", "A harness without helpers can perform the same work."),),
            ),
            (
                "operations/curation.md:0",
                (
                    (
                        "operations/curation.md",
                        "1. Reconcile current intent, ruled intent and implemented reality. Surface every material divergence and verify every card sentence against code; a memory claim is not its proof.",
                    ),
                ),
            ),
            (
                "operations/curation.md:1",
                (
                    (
                        "operations/curation.md",
                        "Before responding to a non-work reply, an empty or doubled-seat refusal, an unreachable-Manager notice, or failed delivery of a required Manager notice, read [Report a refused handover](implementation.md#report-a-refused-handover) for the seat/problem report, pending state, independent continuation and notice retry. It transfers no Worker or transaction authority. The permitted Manager notice occasions below remain unchanged.",
                    ),
                ),
            ),
            (
                "operations/curation.md:2",
                (
                    (
                        "roles/curator.md",
                        "Before sending a memory candidate for review or returning a memory repair, read [memory handover]({{detail:reference/curator/memory-handover.md}}) for candidate identities, the reviewed trees, lane limits and the whole requested stretch.",
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
        require_coordination_routes(CANONICAL / LIFECYCLE)
        assert_git_comparison_scope(CANONICAL / LIFECYCLE)
        # Preserve real audience/step delivery, including held-message semantics.
        assert_message_handover_delivery(LAUNCHER_ROLES)

    def test_manifest_descriptions_follow_the_coordination_default(self) -> None:
        manifest = json.loads(read(LIFECYCLE / "composition-manifest.json"))
        self.assertEqual(
            manifest["operations"]["planning"]["purpose"],
            "Shape a bounded plan and requirement set; Architect delegation starts with one Manager for one master, one Orchestrator for concurrent masters, unless the developer chooses otherwise.",
        )
        self.assertEqual(
            manifest["roles"]["architect"]["seat"],
            "Projects-level semantic owner; may be launched taskless; delegates one master to one Manager and concurrent masters to one Orchestrator, unless the developer chooses otherwise.",
        )
        self.assertEqual(
            manifest["roles"]["orchestrator"]["seat"],
            "Sprint coordinator above one Manager per concurrently worked master; also available above a single master when the developer explicitly asks.",
        )
        self.assertEqual(
            manifest["roles"]["manager"]["seat"],
            "Selected-master coordinator and decision owner under the Architect for one master or the Orchestrator for concurrent masters.",
        )

    def test_the_curator_writer_is_admitted_for_the_selected_runtime(self) -> None:
        # MIK owns the converted file writer; a capsule without its capability reports the gap.
        curation = " ".join(read(LIFECYCLE / "operations" / "curation.md").split())
        self.assertIn(
            "{{detail:reference/knowledge/serving-build.md}}",
            read(LIFECYCLE / "roles" / "curator.md"),
        )
        details = " ".join(read(LIFECYCLE / "reference" / "knowledge/serving-build.md").split())
        intake = " ".join(read(LIFECYCLE / "roles" / "curator.md").split())
        self.assertIn("`arMcpContext.readerArguments`", intake)
        self.assertIn("-P -m agents_remember.cli", details)
        self.assertIn("knowledge-ingest --contract", details)
        self.assertIn("--authorization-ref '<the admitted authorization>' --commit --json", details)
        publication = " ".join(read(LIFECYCLE / "reference/curator/publication-report.md").split())
        for report_semantic in (
            "Without `--commit`, ingest plans and validates without writing files.",
            "`--commit` authorizes file writes, never a Git commit or acceptance",
            "Read the actual report state, written paths, record and entry identifiers, and refusals.",
            "Even `written` with no written paths establishes no publication.",
        ):
            with self.subTest(report_semantic=report_semantic):
                self.assertIn(report_semantic, publication)
        for text in (curation,):
            with self.subTest(told="operation"):
                self.assertIn(
                    "an admitted command-line interface (CLI) bound to the selected serving build",
                    text,
                )
                self.assertIn(
                    "If the capsule exposes no writer and admits none, report the missing capability "
                    "and the required records not written. A missing capability is not a correct-input refusal.",
                    text,
                )
                # P5-18: curation.md no longer repeats the recovery pointer; the shared
                # every-role paragraph holds the one pointer for every role.
                self.assertNotIn("correct-write recovery", text)
                self.assertIn("Verify every output resolves inside the selected memory root.", text)
                self.assertIn("capsule exposes no", text)
                self.assertIn("report", text)
                self.assertIn("not written", text)
                self.assertIn("`agents-remember-task`", text)
                self.assertIn("selected memory root", text)
                self.assertIn("memory_quality_check", text)
                self.assertIn("prepare", text)
                self.assertIn("publish", text)
                self.assertIn("validate", text)
                self.assertNotIn("the writer is not available to you on this line", text)
        # The role carries the same admission by reference to its operation home.
        role = role_text("curator")
        with self.subTest(told="role"):
            self.assertIn(
                "This leaf's memory: cards and sidecars under `onboarding/`, and records under `knowledge/`. These are memory-repository text committed with the leaf memory branch and read by the next planner from its base files.",
                role,
            )
            self.assertIn(
                "Before intake and writer admission, read [required curation inputs](../operations/curation.md#required-inputs) "
                "for the candidate, scope and lists, and [bound intake and capabilities](../operations/curation.md#bound-paseo-intake-and-capabilities) "
                "for the serving writer and capability checks.",
                role,
            )
            self.assertNotIn("the writer is not available to you on this line", role)

        parser = build_parser()
        arguments = [
            "knowledge-ingest",
            "--contract",
            "leaf-contract",
            "--list",
            "handoff.json",
            "--authorization-ref",
            "wording-proof",
            "--commit",
            "--json",
        ]
        self.assertTrue(callable(parser.parse_args(arguments).func))
        for option in ("--baseline", "--publish", "--publish-to"):
            with (
                self.subTest(unsupported_option=option),
                contextlib.redirect_stderr(io.StringIO()) as errors,
            ):
                with self.assertRaises(SystemExit) as caught:
                    parser.parse_args([*arguments, option])
                self.assertEqual(caught.exception.code, 2)
                self.assertIn("unrecognized arguments: " + option, errors.getvalue())

    def test_the_manifest_grants_the_role_tools_to_the_roles_that_may_use_them(
        self,
    ) -> None:
        manifest = json.loads(read(LIFECYCLE / "composition-manifest.json"))
        for role in LAUNCHER_ROLES:
            with self.subTest(role=role):
                tools = manifest["roles"][role]["tools"]
                self.assertIn("role_message", tools)
                self.assertEqual("role_start" in tools, role in STARTERS)
                self.assertEqual(set(tools) - set(PUBLIC_TOOLS), set())
