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

from agents_remember.cli.__main__ import build_parser
from agents_remember.models.role_identity import canonical_role
from agents_remember.models.role_launcher import LauncherRole
from agents_remember.models.tools.public_roster import PUBLIC_TOOLS
from agents_remember_test_support.testing.capsule_wording import assert_git_comparison_scope

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
                "You may author requirements, tasks, plans, and rulings. Do not write onboarding. Flat mode here is only the developer-chosen direct-coordination exception. In flat mode, when no Manager or Orchestrator owns closeout, you may coordinate the existing c-09/c-12 paired transaction only under its already-delegated authority and after independent review and required curation evidence exist. In that case you call the paired closeout yourself and start no Manager for that step alone; it grants no other execution. Human-pinned approvals remain human; never substitute raw Git or self-approval. A high-impact design/security choice, requirement contradiction, missing authority, or scope change returns to the developer. One concise report preserves the decision, refs, evidence, open questions, and limits; no completion claim replaces inspection of the artifact.\n",
            ],
            "roles/orchestrator.md": [
                "You coordinate one selected sprint when two or more masters are worked on at the same time, or when the developer explicitly asks for an Orchestrator above a single master. You operate at Projects altitude. A launch supplies the canonical sprint reference and allowed workspace; the state of an agent in Paseo is not an AR acceptance ledger.",
                "For the accepted objective, reuse each existing Manager named in the Architect's handover and start one Manager for each other master with `role_start` on the `agents-remember-task` tool server, on a selection under your own sprint. Those Managers coordinate their distinct Workers, Reviewers, and Curators. You may start Manager, Worker, Reviewer, Curator, and Investigator. Follow the start and messaging rules in the selected Coordination operation. Your tool server's binding names you as the sender. Address each recipient explicitly by agent ID or by role and task references, and keep the returned agent IDs; do not act as an invisible proxy.",
                "Keep each work item tied to its canonical AR task and primary requirement. Start, reconcile, message and follow through as the selected Coordination operation states; a start is not completion. Handle the order of the day, operational problems, checks before and after a landing, and paired closeout within delegated authority. An Orchestrator started from the dashboard has no parent, needs none, and must not invent one: put developer decisions in your own chat.",
                "Decide ordinary requirement interpretation within the intended promise and operational rulings yourself, and keep the work going. Keep those rulings in one durable rulings record, a list of requirement sentences that should change, and one status file under the selected task's notes; name their paths in your report. When the Architect started you, it reads these files on its own and aligns the requirement texts with the rulings. These records do not authorize new or dropped scope or a changed promise.",
                "If you cannot start a needed role within your permissions, report that blocker to the agent that started you with `role_message` on `agents-remember-task`; without a parent, report it in your own chat and invent no Architect ID. Never start that role yourself. Assign a check or investigation that belongs to no leaf to an Investigator on your own sprint or on your sprint and one of its masters. Send the scoped concern as your first `role_message` on `agents-remember-task` to its returned agent ID; never select a leaf for this role.",
                "When the Architect started you, message it only for a developer decision: new or dropped scope or a change to a requirement's promise; something only the developer can do or approve; an override of a role default; or a blocker that none of your own decisions can remove. Send these with `role_message` on `agents-remember-task` to the parent agent ID in your handover. Forward only questions requiring the developer and permission notices as stated below; send no other role messages or landing notices. Ordinary requirement readings and operational rulings stay with you; do not ask the Architect to perform them. Send one further message with the path of your report when the whole assignment is finished or cannot be finished. Send no other messages to the Architect.",
                "If that message cannot be delivered, write the matter in your status file, continue every part of the work that does not depend on the developer's decision, and send the message again later. Do not decide the developer's question yourself.",
                "A turn ending proves only that it ended. Verify each Manager's delivery and aggregate evidence; the Manager owns the leaf-diff, repair and evidence loop. You may inspect supplied actual diffs for acceptance without taking over that loop. Request an independent Reviewer when the brief or risk requires it; a Reviewer never adjudicates its own work. Request a Curator for affected memory/onboarding when needed. Keep reports, findings, review, curation, and Git publication separately addressed. Use the existing AR task and paired Git owners for their semantic records; do not claim acceptance or landing from the status of an agent in Paseo.",
            ],
            "roles/manager.md": [
                "You coordinate one selected master at Projects altitude. You do not own the portfolio. For one master, the Architect delegates coordination to one Manager. An Orchestrator sits above the Managers when two or more masters are worked on at the same time, or when the developer asks for one above a single master. Direct coordination by the Architect is only the developer-chosen exception. When another agent started you, report to that agent's role with its task references, unless the Architect instructs the reporting-recipient change below.",
                "Read each leaf's reports and task records yourself. Direct leaf handover flow and relay limits follow `../operations/coordination.md`. Inspect the complete changed-file diff and both verdicts before deciding the gate. Keep closeout, integration, acceptance writes and the order of landings. Start, finalize and abandon publish task stage and mirror it into the master's row; do not follow them with a separate status write. Use `task_doc set_status` for an explicit correction and for organizational-master completion, whose completion act remains yours. Reviews are evidence, not gate decisions. Preserve stable requirement/finding IDs and distinguish implementation, review, curation and publication status. A Manager started from the dashboard has no parent and needs none: put a needed developer decision in your own chat and do not invent an Architect ID. Direct leaf repair-loop handovers run as `../operations/coordination.md` states.",
                "Decide the order of your leaves, agent starts and replacements, operational problems, ordinary requirement interpretation within the intended promise, checks before and after a landing, and paired closeout and integration within delegated authority. Review rounds, memory passes and relay limits follow `../operations/coordination.md`. Accepting a verdict for the gate stays yours. Assign a check or investigation that belongs to no leaf to an Investigator on your own sprint and master. Send the scoped concern as your first `role_message` on `agents-remember-task` to its returned agent ID; never select a leaf for this role.",
                "Keep your rulings in one durable rulings record, a list of requirement sentences that should change, and one status file under the selected master's notes; name their paths in your report. The Architect reads these files on its own and aligns the requirement texts with the rulings. These records do not authorize new or dropped scope or a changed promise.",
                "When another agent started you, message your parent or the instructed reporting recipient only for a developer decision: new or dropped scope or a change to a requirement's promise; something only the developer can do or approve; an override of a role default; or a blocker that none of your own decisions can remove. Send these with `role_message` on `agents-remember-task` to the parent agent ID in your handover, or to the instructed reporting-recipient ID recorded in your report. When the Orchestrator owns coordination, do not bypass it to the Architect. Forward only questions requiring the developer and permission notices as stated below; send no other role messages or landing notices. Ordinary requirement readings and operational rulings stay with you; keep the work going. Send one further message with the path of your report, addressed to the parent's role with its task references, when the whole assignment is finished or cannot be finished. Send no other messages to that recipient.",
                "If the Architect who started you names an Orchestrator's agent ID in a message when a second master starts, record that instruction and reporting-recipient ID in your durable report and send developer-needed matters to that Orchestrator from then on. This changes your reporting recipient, not your fixed launch parent; no other message changes whom you report to.",
                "If you cannot start a needed role within your permissions, report that blocker to your parent or instructed reporting recipient with `role_message` on `agents-remember-task`; without a parent, report it in your own chat and invent no Architect ID. Never start that role yourself.",
                "If an upward message cannot be delivered, write the matter in your status file, continue every part of the work that does not depend on the developer's decision, and send the message again later. Do not decide the developer's question yourself.",
            ],
            "operations/planning.md": [
                "The Architect's first delegation for one master is one Manager on that canonical master. For two or more masters worked on at the same time, it is one Orchestrator on the canonical sprint, which starts one Manager per master. This delegation rule applies only to the Architect; Designers and Strategists do not start a coordinating role. Direct coordination by the Architect is only the developer-chosen exception; in that case, the Architect directly coordinates distinct Workers, Reviewers, and Curators. The developer may also ask for an Orchestrator above a single master. Keep one explicit owner per task and preserve the agent IDs of started roles. Do not fabricate a sprint/master for a taskless project role or an agent identity to satisfy an old hierarchy."
            ],
            "operations/coordination.md": [
                "The Manager starts a leaf's Worker, Reviewer and Curator as a set in one `role_start_set` call on `agents-remember-task`: the Worker opens at once, and the Reviewer and the Curator are held until their first message opens them, unless the call carries the Reviewer's first work. The Reviewer first cold-reads the requirement before code exists. The Worker, Reviewer and Curator run the leaf repair loop directly with `role_message` on `agents-remember-task`: Worker freeze to Reviewer, verdict and sealed findings back to Worker, passed freeze and producer lists to Curator, memory change and verdict between Curator and Reviewer. A Curator's code concern goes to both Reviewer and Worker even after code PASS; the Reviewer answers it and the Curator records the answer. The Manager reads their reports, decides the gate and keeps closeout, integration, status, acceptance and landing order; it relays no freeze, finding, verdict or memory change. Keep paired code/memory/report paths and exact task references in each hand-over.",
                "The Architect delegates coordination first to one Manager for one master, or to one Orchestrator on the sprint when two or more masters are worked on at the same time. The Orchestrator starts one Manager per master; each Manager coordinates its distinct Workers, Reviewers, and Curators. Only the developer may choose direct coordination by the Architect. The developer may also ask for an Orchestrator above a single master. If the Architect's coordinating-agent start is refused, the Architect tells the developer the refusal and its reason; it does not fall back to direct coordination.",
                "Send one message to one agent with `role_message` on `agents-remember-task`, addressed by agent ID or by role plus task references. A leaf hand-over uses the role and exact task reference in `leafSeats.roleMessageArguments`; use a product-supplied agent ID only whole and unchanged. The current leaf resolver reaches a seat's single live agent; a seat with two live occupants is refused naming both, and the tool never picks one; an empty-seat refusal is reported once to the Manager with its text and no candidate is chosen. A role address never means you yourself. A recipient that is mid-turn takes the message up without its turn being cancelled; when the host cannot hand a message to the running turn, the message is held, and while anything is held for a recipient a further message is held behind it. A message to a recipient that waits for a permission decision, or whose start has not finished, is held the same way: the answer says `held` and why, and the product delivers what it holds in the order it took it once the recipient can take it. The host is never sent a message for a recipient that waits at a permission prompt, because it would answer the permission with a denial; only the developer answers the prompt, in that agent's chat. A closed session is resumed first; an archived or missing agent is refused and stays as it is. With `wait` the call returns when the turn that took your message has ended: with the recipient's final text, or saying that the turn failed or was cancelled; or it returns a pending permission, also for a wait on a held message, or a timeout with the message still delivered. It can also return `accepted` without a text, when the host cannot say which turn took the message; read the reply later then. The text of a message delivered during a turn can be that running turn's own; `detail` says so. Do not wait on an agent that may be waiting on you: two agents that wait on each other both stand still until one wait runs out. If your own tool calls are cut off earlier than the wait, pass a shorter `timeout_seconds`. An Orchestrator, Manager or developer-direct-coordinating Architect follows assignments until the work is complete, blocked, or a true developer decision is needed; it does not start a role and stop. The Architect releases execution coordination after the handover and continues the developer conversation and status reading; it holds no wait on an Orchestrator or Manager for execution completion. On a refusal or an uncertain result, act on the named reason; do not guess a parent or start duplicate work. A role started from the dashboard works directly with the developer and needs no parent.",
            ],
            "SKILL.md": [
                "Use the agent IDs these tools return and the sender line of each message you receive; never invent a sender, recipient, parent, or delivery result.",
                "AR remains authoritative for canonical tasks, requirements, knowledge, curation, and paired Git operations.",
                "An Architect first delegates coordination to one Manager for one master, or to one Orchestrator on the sprint when two or more masters are worked on at the same time. The Orchestrator starts one Manager per master. Only the developer may choose direct coordination by the Architect. The developer may also ask for an Orchestrator above a single master. A role started from the dashboard has no parent agent and needs none. Existing approvals and rulings remain durable across reconnects and compaction; ask again only for new or changed scope, a real requirement conflict, or an unresolved human-pinned decision.",
            ],
            "roles/curator.md": [
                "Intake/writer admission: `../operations/curation.md` §Required inputs and §Bound Paseo intake and capabilities.",
                "taskless `agents-remember knowledge-bootstrap` under `c-14-knowledge-bootstrap`, with **no enclosure in scope**, otherwise `enclosure_in_scope`, never writing onto a task's line.",
                "Never fabricate a leaf, worktree or enclosure for either route;",
                "or the mounted `knowledge_write`, which runs the same writer with the same arguments;",
                "No other route writes knowledge.",
                "`--commit` (`commit: true`) authorizes file writes on every entry, not Git or acceptance.",
                "Report one full-intake worklist of distinct outstanding memory actions:",
                "A Curator started from the dashboard has no parent and needs none.",
                "A Curator starts no role:",
                "A harness without sub-agents can do all the same work.",
            ],
            "operations/curation.md": [
                "Count a hand-over as taken and handle provider, seat, busy and unreachable-Manager refusals as `../operations/implementation.md` §Boundaries states.",
                "Reviewer compares Git bases/candidates; Curator records no separate comparison.",
            ],
        }
        for source, clauses in required.items():
            reading = " ".join(read(LIFECYCLE / source).split())
            for clause in clauses:
                with self.subTest(source=source, clause=clause):
                    self.assertIn(" ".join(clause.split()), reading)
        assert_git_comparison_scope(CANONICAL / LIFECYCLE)

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
        self.assertIn("agents-remember knowledge-ingest", details)
        self.assertIn("--authorization-ref <ref> --commit --json", details)
        self.assertIn("The CLI has no `--baseline`, `--publish` or `--publish-to` options", details)
        self.assertIn("its parser rejects them before the file writer is invoked", details)
        for text in (curation,):
            with self.subTest(told="operation"):
                self.assertIn("source-bound command", text)
                self.assertIn(
                    "Use only the selected runtime's admitted source-bound command; if this Paseo capsule exposes no writer and admits none, report required records not written.",
                    text,
                )
                self.assertIn(
                    "Never bypass refusal with another installation's command or an internal writer.",
                    text,
                )
                self.assertIn("Verify every output resolves inside the selected memory root.", text)
                self.assertIn("Paseo capsule exposes no", text)
                self.assertIn("report", text)
                self.assertIn("not written", text)
                self.assertIn("another installation's", text)
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
