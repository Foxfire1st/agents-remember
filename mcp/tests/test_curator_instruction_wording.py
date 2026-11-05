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
import tempfile
import unittest
from pathlib import Path
from typing import get_args

from agents_remember.cli.__main__ import build_parser
from agents_remember.models.role_identity import canonical_role
from agents_remember.models.role_launcher import LauncherRole
from agents_remember.models.tools.public_roster import PUBLIC_TOOLS
from agents_remember_test_support.testing.capsule_wording import assert_git_comparison_scope
from agents_remember_test_support.testing.leaf_instruction_wording import delivered_step
from role_instruction_wording_support import assert_message_handover_delivery

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
    def test_foundation_intake_and_correction_keep_their_task_and_truth_boundaries(self) -> None:
        role = read(LIFECYCLE / "roles/curator.md")
        inputs = role.split("## Inputs\n", 1)[1].split("\n## ", 1)[0]
        outputs = role.split("## Outputs\n", 1)[1].split("\n## ", 1)[0]
        required = {
            "Inputs": (
                inputs,
                (
                    "only on a leaf task document for the leaf pass, never taskless",
                    "{{detail:reference/curator-repository-foundation.md}}",
                    "when you reach a repository's first or resumed knowledge foundation, not before",
                    "Refuse a producer's list sent without a passing `handoff-check` result for that freeze",
                ),
            ),
            "Outputs": (
                outputs,
                (
                    "A correction withdraws or qualifies every claim in the affected body that it makes false",
                    "a true added paragraph does not answer a contradicting one",
                ),
            ),
        }
        for heading, (text, clauses) in required.items():
            normalized = " ".join(text.split())
            for clause in clauses:
                with self.subTest(step=heading, clause=clause):
                    self.assertIn(clause, normalized)
                    with self.assertRaises(AssertionError):
                        self.assertIn(clause, normalized.replace(clause, "", 1))
        foundation = " ".join(
            read(LIFECYCLE / "reference/curator-repository-foundation.md").split()
        )
        for clause in (
            "the user starts the Architect",
            "real foundation task under a master",
            "use its actual enclosure contract",
            "no incidental code delta is required",
            "Run `c-14-knowledge-bootstrap`",
            "Onboarding is optional input",
            "this foundation procedure authors no onboarding",
            "Without a task, Bootstrap reads and reports state",
            "No instructed session substitutes for task admission",
            "never fabricate a task, worktree or enclosure",
            "Report partial foundation scope as partial; name unreached areas",
        ):
            self.assertIn(clause, foundation)
        self.assertNotIn("**Repository foundation is regular task work.**", role)

    def test_producer_checks_and_review_truth_criteria_keep_their_own_homes(self) -> None:
        operation = " ".join(read(LIFECYCLE / "operations/review.md").split())
        for clause in (
            "Before you send your hand-off list, run `handoff-check`",
            "with the serving build's interpreter (`server_info` on `agents-remember-task`)",
            "against that freeze, and name its result",
        ):
            self.assertIn(clause, operation)
        onboarding = " ".join(read(LIFECYCLE / "criteria/onboarding-memory.md").split())
        self.assertIn("read the whole affected body, not only the added text", onboarding)
        report = " ".join(read(LIFECYCLE / "criteria/report-verification.md").split())
        for clause in (
            "RV-6",
            "standing by requirement",
            "ratchet does not demote it",
            "what it ran and what it left out",
            "same cause (message and subtest)",
            "not only under the same test name",
            "files it exercises are byte-identical",
            "a carried run whose files changed is an evidence gap",
        ):
            self.assertIn(clause, report)

    def test_cards_are_decided_sentence_by_sentence_before_curation(self) -> None:
        """Pin the instructed guidance; this neither inspects nor grades actor card edits."""
        detail = read(LIFECYCLE / "reference/curator-authoring-d301.md")
        item = detail.split("**Cards say what is true now.**", 1)[1].split(
            "## Before curation step 2", 1
        )[0]
        text = " ".join(item.split())
        for clause in (
            "Decide each sentence of every card and its paired notes, including reference captions one by one, whatever "
            "its tense or location, dated sections included.",
            "Edit individual sentences; never replace a section or regenerate a card wholesale.",
            "When a sentence is true now, keep its wording, including true statements about "
            "retired things.",
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
        self.assertIn("{{detail:reference/refused-write.md}}", role)
        self.assertIn("Only that tool and input class", role)
        self.assertIn("missing writer is not a refused correct write", role)
        self.assertIn("code, gates, lifecycle state, worktree contracts or closeout state", role)
        self.assertNotIn("No other route writes knowledge from this seat.", role)
        self.assertNotIn("never hand-write records, sidecar entries or history", role)

    def test_correct_write_procedure_is_delivered_at_the_refusal_step(self) -> None:
        root = CANONICAL / LIFECYCLE
        with tempfile.TemporaryDirectory() as directory:
            instructions, detail = delivered_step(
                root,
                ("curator", "curation"),
                "reference/refused-write.md",
                "when a covered writer or record tool refuses a correct write, not before",
                Path(directory) / "report.md",
            )
        self.assertIn("Read [correct-write recovery]", instructions)
        for clause in (
            "Check the build and route.",
            "Take an honest route the tool admits.",
            "Otherwise make that write openly by hand.",
            "before writing",
            "every other write still uses its tool",
            "check that still works",
            "issue row",
            "full report",
            "rendered copies are never parsed back",
            "Repair validator and gate findings under D229",
            "Read, check, validation and sync refusals",
        ):
            with self.subTest(clause=clause):
                self.assertIn(clause, detail)

    def test_current_coordination_and_curator_rules(self) -> None:
        required = {
            "roles/architect.md": [
                "Answer a developer message before any other work continues, with an answer, not a status line. A message that begins with `From <role> · <task> · agent <id>` is a role agent's message, not the developer's answer or approval.",
                "For work inside one master, first start one Manager on that canonical master and hand it the coordination. When two or more masters are worked on at the same time, first start one Orchestrator on the selected canonical sprint; it starts one Manager for each master. Direct coordination is the exception: only when the developer tells you to coordinate the role agents yourself, directly coordinate distinct Worker, Reviewer, and Curator agents. If the developer asks for an Orchestrator above a single master, start one. Keep one owner per task and one explicitly addressed recipient per message.",
                "If the coordinating agent's start is refused, tell the developer the refusal and its reason. Do not fall back to direct coordination without the developer's instruction.",
                "If a second master starts while a Manager works directly under you, start one Orchestrator on the sprint and give it the existing Manager's exact agent ID, canonical master reference and current state; it reuses that Manager and starts no duplicate. Name the Orchestrator's agent ID to the existing Manager in one message. From then on the Manager sends developer-needed matters to that Orchestrator; this changes its reporting recipient, not its fixed launch parent.",
                "For each role assignment, call `role_start` on the `agents-remember-task` tool server with the role, the exact canonical sprint, master, and leaf references its class requires, and a request ID you choose. It resolves or creates the AR paired enclosure, compiles the role's capsule and handover, and starts the agent in Paseo with you as its parent; the agent begins its assignment at once. Keep the returned agent ID, report path, and handover artifact path. You may start every role except another Architect. A start that answers `unknown` is reconciled by the same request ID, never by a second start. After review, under developer-chosen direct coordination send a finding-specific repair to the same Worker with `role_message` instead of starting another Worker; under delegation, send it to the Manager or Orchestrator coordinating the work.",
                "Keep the conversation with the developer, the plan and its order, the requirements and their text, and the decision on what needs the developer. Hand the Manager or Orchestrator the role starts and messages, the order of the day, operational problems, ordinary requirement interpretation within the intended promise and operational rulings, checks before and after a landing, and paired closeout within its delegated authority.",
                "Read the coordinating agent's rulings record, list of requirement sentences to change, and status file on your own; it sends no routine ruling or landing notice. Bring the requirement texts in line with its rulings within the intended promise. New or dropped scope and changes to that promise still need the developer's decision.",
                "After the handover, release execution coordination to the Orchestrator or Manager and continue the developer conversation and status reading. Do not hold a `wait` on an Orchestrator or Manager for execution completion through `role_message` on `agents-remember-task`; those coordinators own their completion loops.",
                "When the developer answers or gives a ruling, send that actual answer or ruling to the requesting Manager or Orchestrator with `role_message` on `agents-remember-task`, addressed by its recorded agent ID. A reply to a coordinating agent's wait that repeats its question is not the developer's decision; relay the decision only once you have obtained the developer's actual answer or ruling.",
                "When an Investigator is needed, start it with `role_start` on `agents-remember-task` without a task reference, on a sprint, or on a sprint and master. Send the scoped concern as your first `role_message` on `agents-remember-task` to the returned agent ID. A Manager or Orchestrator may also start an Investigator within its own scope.",
                "Put questions for the developer, and requests for a ruling, in your own chat; the developer answers there. Under developer-chosen direct coordination, review the full candidate diff, including unattributed changes; under delegation, the Manager owns the leaf-diff and repair loop and supplies aggregate evidence through the Orchestrator where one exists. Treat invariant-family attribution as an additional review dimension, not a filter on changed files. Keep a finished turn, semantic review, curation, and paired Git publication as separate facts. Do not accept your own work or convert green checks into requirement acceptance. Record decisions and task truth through the existing AR task/data owners.",
                "You may check, measure, run tests and builds, read the code and sources of the product, the host and the harnesses, and try things out in a sandbox yourself, as the developer would; that includes a check before a landing. Such work informs your rulings, your requirements and the developer. It never takes the place of a leaf's build, review, curation or gate, and you change no leaf's code or memory: those go through its Worker, Reviewer and Curator. You grade nothing you wrote. Nothing of yours runs in a candidate's worktree; work on a copy. Reading code and sources for a check is not loading another task's documents or another role's instructions; the read-only rules of line 42 and of `operations/orientation.md` stay as they are. You perform no step of a landing except the paired closeout in the developer-chosen direct-coordination case below; a check before a landing is not a step of it. For a check or investigation you do not do yourself, give the needed check or investigation to the Manager or Orchestrator coordinating the work; under developer-chosen direct coordination, assign a leaf's check to its Worker or Reviewer and a check or investigation that belongs to no leaf to an Investigator.",
                "You may author requirements, tasks, plans, and rulings. Do not write onboarding. Flat mode here is only the developer-chosen direct-coordination exception. In flat mode, when no Manager or Orchestrator owns closeout, you may coordinate the existing paired closeout only under the already-delegated authority of the kept closeout procedure (`../operations/closeout.md` with its `../../w-02-light-task-workflow/workflows/closeout.md` workflow) and after independent review and required curation evidence exist. In that case you call the paired closeout yourself and start no Manager for that step alone; it grants no other execution. Human-pinned approvals remain human; never substitute raw Git or self-approval. A high-impact design/security choice, requirement contradiction, missing authority, or scope change returns to the developer. One concise report preserves the decision, refs, evidence, open questions, and limits; no completion claim replaces inspection of the artifact.\n",
            ],
            "roles/orchestrator.md": [
                "You coordinate one selected sprint when two or more masters are worked on at the same time, or when the developer explicitly asks for an Orchestrator above a single master. You operate at Projects altitude. A launch supplies the canonical sprint reference and allowed workspace; the state of an agent in Paseo is not an AR acceptance ledger.",
                "For the accepted objective, reuse each existing Manager named in the Architect's handover and start one Manager for each other master with `role_start` on the `agents-remember-task` tool server, on a selection under your own sprint. Those Managers coordinate their distinct Workers, Reviewers, and Curators. You may start Manager, Worker, Reviewer, Curator, and Investigator. Follow the start and messaging rules in the selected Coordination operation. Your tool server's binding names you as the sender. Address each recipient explicitly by agent ID or by role and task references, and keep the returned agent IDs; do not act as an invisible proxy.",
                "Keep each work item tied to its canonical AR task and primary requirement. Start, reconcile, message and follow through as the selected Coordination operation states; a start is not completion. Handle the order of the day, operational problems, checks before and after a landing, and paired closeout within delegated authority. An Orchestrator started from the dashboard has no parent, needs none, and must not invent one: put developer decisions in your own chat.",
                "Decide ordinary requirement interpretation within the intended promise and operational rulings yourself, and keep the work going. Keep those rulings in one durable rulings record, a list of requirement sentences that should change, and one status file under the selected task's notes; name their paths in your report. When the Architect started you, it reads these files on its own and aligns the requirement texts with the rulings. These records do not authorize new or dropped scope or a changed promise.",
                "If you cannot start a needed role within your permissions, report that blocker to the agent that started you with `role_message` on `agents-remember-task`; without a parent, report it in your own chat and invent no Architect ID. Never start that role yourself. Assign a check or investigation that belongs to no leaf to an Investigator on your own sprint or on your sprint and one of its masters. Send the scoped concern as your first `role_message` on `agents-remember-task` to its returned agent ID; never select a leaf for this role.",
                "When the Architect started you, message it only for a developer decision: new or dropped scope or a change to a requirement's promise; something only the developer can do or approve; an override of a role default; a small item that has cost about an hour, or a second repair round or freeze, with its hours and a proposal; or a blocker that none of your own decisions can remove. Send these with `role_message` on `agents-remember-task` to the parent agent ID in your handover. Forward only questions requiring the developer and permission notices as stated below; send no other role messages or landing notices. Ordinary requirement readings and operational rulings stay with you; do not ask the Architect to perform them. Send one further message with the path of your report when the whole assignment is finished or cannot be finished. Send no other messages to the Architect.",
                "If that message cannot be delivered, write the matter in your status file, continue every part of the work that does not depend on the developer's decision, and send the message again later. Do not decide the developer's question yourself.",
                "A turn ending proves only that it ended. Verify each Manager's delivery and aggregate evidence; the Manager owns the leaf-diff, repair and evidence loop. You may inspect supplied actual diffs for acceptance without taking over that loop. Request an independent Reviewer when the brief or risk requires it; a Reviewer never adjudicates its own work. Request a Curator for affected memory/onboarding when needed. Keep reports, findings, review, curation, and Git publication separately addressed. Use the existing AR task and paired Git owners for their semantic records; do not claim acceptance or landing from the status of an agent in Paseo.\n\nPlan-review and super-exit have no startable Reviewer seat. Bring either review to the architect, who takes it to the developer with the sprint-level Reviewer idea.",
            ],
            "roles/manager.md": [
                "You coordinate one selected master at Projects altitude. You do not own the portfolio. When another agent started you, report to that agent's role with its task references, unless the Architect instructs the reporting-recipient change below.",
                "Read each leaf's reports and task records yourself. Direct leaf hand-overs, repair loops, review rounds, memory passes and relay limits follow `../operations/coordination.md`. Inspect the complete changed-file diff and both verdicts before deciding the gate. Keep closeout, integration, acceptance writes and the order of landings. Start, finalize and abandon publish task stage and mirror it into the master's row; do not follow them with a separate status write. Use `task_doc set_status` for an explicit correction and for organizational-master completion, whose completion act remains yours. Reviews are evidence, not gate decisions. Preserve stable requirement/finding IDs and distinguish implementation, review, curation and publication status. A Manager started from the dashboard has no parent and needs none: put a needed developer decision in your own chat and do not invent an Architect ID.",
                "Decide the order of your leaves, agent starts and replacements, operational problems, ordinary requirement interpretation within the intended promise, checks before and after a landing, and paired closeout and integration within delegated authority. Accepting a verdict for the gate stays yours. The task's construction is yours: step order, evidence form, which existing test or command shows a thing, one rerun on the same operands after a harness, loader or capture fault, and sandbox- or scratch-only instruments that change no product source; a second failure of one cause goes up with what was tried. Assign a check or investigation that belongs to no leaf to an Investigator on your own sprint and master. Send the scoped concern as your first `role_message` on `agents-remember-task` to its returned agent ID; never select a leaf for this role.",
                "Keep your rulings in one durable rulings record, a list of requirement sentences that should change, and one status file under the selected master's notes; name their paths in your report. The Architect reads these files on its own and aligns the requirement texts with the rulings. These records do not authorize new or dropped scope or a changed promise.",
                "When another agent started you, message your parent or the instructed reporting recipient only for a developer decision: new or dropped scope or a change to a requirement's promise; something only the developer can do or approve; an override of a role default; a small item that has cost about an hour, or a second repair round or freeze, with its hours and a proposal; or a blocker that none of your own decisions can remove. Send these with `role_message` on `agents-remember-task` to the parent agent ID in your handover, or to the instructed reporting-recipient ID recorded in your report. When the Orchestrator owns coordination, do not bypass it to the Architect. Forward only questions requiring the developer and permission notices as stated below; send no other role messages or landing notices. Ordinary requirement readings and operational rulings stay with you; keep the work going. Send one further message with the path of your report, addressed to the parent's role with its task references, when the whole assignment is finished or cannot be finished. Send no other messages to that recipient.",
                "If the Architect who started you names an Orchestrator's agent ID in a message when a second master starts, record that instruction and reporting-recipient ID in your durable report and send developer-needed matters to that Orchestrator from then on. This changes your reporting recipient, not your fixed launch parent; no other message changes whom you report to.",
                "If you cannot start a needed role within your permissions, report that blocker to your parent or instructed reporting recipient with `role_message` on `agents-remember-task`; without a parent, report it in your own chat and invent no Architect ID. Never start that role yourself.",
                "If an upward message cannot be delivered, write the matter in your status file, continue every part of the work that does not depend on the developer's decision, and send the message again later. Do not decide the developer's question yourself.",
            ],
            "operations/planning.md": [
                "The Architect's first delegation for one master is one Manager on that canonical master. For two or more masters worked on at the same time, it is one Orchestrator on the canonical sprint, which starts one Manager per master. This delegation rule applies only to the Architect; Designers and Strategists do not start a coordinating role. Direct coordination by the Architect is only the developer-chosen exception; in that case, the Architect directly coordinates distinct Workers, Reviewers, and Curators. The developer may also ask for an Orchestrator above a single master. Keep one explicit owner per task and preserve the agent IDs of started roles. Do not fabricate a sprint/master for a taskless project role or an agent identity to satisfy an old hierarchy."
            ],
            "operations/coordination.md": [
                "The Manager calls role_start once per stage: Worker first, Reviewer at the freeze, Curator when curation begins.",
                "The Worker\u2019s freeze notice and Reviewer\u2019s code-PASS notice go to the Manager first.",
                "At build, start the Worker alone.",
                "For ordinary delivery, curation begins after code PASS.",
                "The Worker hands its freeze directly to the Reviewer once that seat exists.",
                "A separately assigned packet cold read remains possible without an early normal leaf Reviewer.",
                "Ordinary delivery curation and the knowledge check still follow code PASS.",
                "The Worker, Reviewer and Curator run the leaf repair loop directly with `role_message` on `agents-remember-task`: Worker freeze to Reviewer, verdict and sealed findings back to Worker, passed freeze and producer lists to Curator, memory change and verdict between Curator and Reviewer. A Curator's code concern goes to both Reviewer and Worker even after code PASS; the Reviewer answers it and the Curator records the answer. The Manager reads their reports, decides the gate and keeps closeout, integration, status, acceptance and landing order; it relays no freeze, finding, verdict or memory change. Keep paired code/memory/report paths and exact task references in each hand-over.",
                "The Architect delegates coordination first to one Manager for one master, or to one Orchestrator on the sprint when two or more masters are worked on at the same time. The Orchestrator starts one Manager per master; each Manager coordinates its distinct Workers, Reviewers, and Curators. Only the developer may choose direct coordination by the Architect. The developer may also ask for an Orchestrator above a single master. If the Architect's coordinating-agent start is refused, the Architect tells the developer the refusal and its reason; it does not fall back to direct coordination.",
                "Send one message to one agent with `role_message` on `agents-remember-task`, addressed by agent ID or by role plus task references.",
                "A leaf hand-over uses the role and exact task reference in `leafSeats.roleMessageArguments`; use a product-supplied agent ID only whole and unchanged.",
                "The current leaf resolver reaches a seat's single live agent; a seat with two live occupants is refused naming both, and the tool never picks one; an empty-seat refusal is reported once through the role\u2019s recovery route with its text and no candidate is chosen; a Manager uses its parent route, never itself.",
                "A role address never means you yourself.",
                "A recipient that is mid-turn takes the message up without its turn being cancelled.",
                "The answer names `held` and its reason when the host has not delivered the message.",
                "The host is never sent a message for a recipient that waits at a permission prompt, because it would answer the permission with a denial; permission decisions follow `operations/orientation.md`.",
                "A closed session is resumed first; an archived or missing agent is refused and stays as it is.",
                "With `wait` the call returns when the turn that took your message has ended: with the recipient's final text, or saying that the turn failed or was cancelled; or it returns a pending permission, also for a wait on a held message, or a timeout with the message still delivered.",
                "It can also return `accepted` without a text, when the host cannot say which turn took the message; read the reply later then.",
                "The text of a message delivered during a turn can be that running turn's own; `detail` says so.",
                "Do not wait on an agent that may be waiting on you: two agents that wait on each other both stand still until one wait runs out.",
                "If your own tool calls are cut off earlier than the wait, pass a shorter `timeout_seconds`.",
                "An Orchestrator, Manager or developer-direct-coordinating Architect follows assignments until the work is complete, blocked, or a true developer decision is needed; it does not start a role and stop.",
                "The Architect releases execution coordination after the handover and continues the developer conversation and status reading; it holds no wait on an Orchestrator or Manager for execution completion.",
                "On a refusal or an uncertain result, act on the named reason; do not guess a parent or start duplicate work.",
                "A role started from the dashboard works directly with the developer and needs no parent.",
            ],
            "SKILL.md": [
                "Use the agent IDs these tools return and the sender line of each message you receive; never invent a sender, recipient, parent, or delivery result.",
                "AR remains authoritative for canonical tasks, requirements, knowledge, curation, and paired Git operations.",
                "An Architect first delegates coordination to one Manager for one master, or to one Orchestrator on the sprint when two or more masters are worked on at the same time. The Orchestrator starts one Manager per master. Only the developer may choose direct coordination by the Architect. The developer may also ask for an Orchestrator above a single master. A role started from the dashboard has no parent agent and needs none. Existing approvals and rulings remain durable across reconnects and compaction; ask again only for new or changed scope, a real requirement conflict, or an unresolved human-pinned decision.",
            ],
            "roles/curator.md": [
                "Intake/writer admission: `../operations/curation.md` §Required inputs and §Bound Paseo intake and capabilities.",
                "Use mounted `knowledge_write` or the admitted serving-build CLI with this task's contract, hand-off list and `--commit` (`commit: true`).",
                "Never fabricate a leaf, worktree or enclosure",
                "Use mounted `knowledge_write`",
                "Only that tool and input class admits open manual recovery; other writes keep their tools.",
                "`--commit` (`commit: true`) authorizes file writes, not Git or acceptance.",
                "Report one full-intake worklist of distinct outstanding memory actions:",
                "A Curator started from the dashboard has no parent and needs none.",
                "A Curator starts no role:",
                "A harness without sub-agents can do all the same work.",
            ],
            "operations/curation.md": [
                "1. **Reconcile** current intent, ruled intent and implemented reality; surface "
                "every material divergence. Verify each card sentence against the code yourself; "
                "a memory line is a claim, not proof.",
                "Handle non-work replies, empty or doubled seats and unreachable-Manager notices as `../operations/implementation.md` \u00a7Boundaries states.",
                "Reviewer compares Git bases/candidates; Curator records no separate comparison.",
            ],
        }
        required["roles/architect.md"].append(
            "Reviews, cold reads, re-checks, sweeps and Investigator reports are advice, not requirements. "
            "A finding that only corrects a wrong fact may be applied. A finding that adds, narrows or "
            "overrides a rule beyond the developer's words goes to the developer first, quoted beside "
            "the developer's earlier words."
        )
        required["roles/investigator.md"] = [
            "Report what is and what is not, with the evidence for each. State no requirement, ruling "
            "or duty of your own. Where a change seems needed, name it as an open question for the "
            "parent, not as a fix to apply."
        ]
        for source, clauses in required.items():
            reading = " ".join(read(LIFECYCLE / source).split())
            for clause in clauses:
                with self.subTest(source=source, clause=clause):
                    self.assertIn(" ".join(clause.split()), reading)
        assert_git_comparison_scope(CANONICAL / LIFECYCLE)
        # Rule44/BA3: prove the moved held meaning through real audience/step delivery.
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
            "{{detail:reference/curator-writer-details.md}}",
            read(LIFECYCLE / "roles" / "curator.md"),
        )
        details = " ".join(read(LIFECYCLE / "reference" / "curator-writer-details.md").split())
        intake = " ".join(read(LIFECYCLE / "reference" / "curator-intake-details.md").split())
        self.assertIn("`arMcpContext.readerArguments`", intake)
        self.assertIn("-P -m agents_remember.cli", details)
        self.assertIn("knowledge-ingest --contract", details)
        self.assertIn("--authorization-ref '<the admitted authorization>' --commit --json", details)
        self.assertIn(
            "The parser has no `--baseline`, `--publish` or `--publish-to` options", details
        )
        for text in (curation,):
            with self.subTest(told="operation"):
                self.assertIn("admitted CLI bound to the selected serving build", text)
                self.assertIn(
                    "If this capsule exposes no writer and admits none, report the missing capability "
                    "and required records not written: a missing capability is not a correct-input refusal.",
                    text,
                )
                self.assertIn("refused correct write", text)
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
            self.assertIn("There is no dataset to publish.", role)
            self.assertIn(
                "Intake/writer admission: `../operations/curation.md` "
                "§Required inputs and §Bound Paseo intake and capabilities.",
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
