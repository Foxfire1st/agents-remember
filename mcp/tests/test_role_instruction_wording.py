"""Role instruction wording: delegation, developer channels, and host tool names.

The surfaces are the ones a launched role agent reads: the router, the role file of each of the
seven launcher roles, every operation file that applies to one of them, the routing manifest, the
handover text built in code, and the descriptions of the two role tools. None of them may name
the previous host or its transport, every passage that tells an agent to call an AR tool names
the tool server ``agents-remember-task``, and the handover says that a server named
``agents-remember``, and an AR tool server under any other name, belongs to another installation.
"""

from __future__ import annotations

import asyncio
import json
import re
import tempfile
import unittest
import uuid
from pathlib import Path
from typing import Any, get_args
from unittest.mock import patch

from agents_remember.application.role_capsules.compilation import compile_admitted_capsule
from agents_remember.application.role_capsules.launch import _capsule_launch
from agents_remember.application.role_launch_context import (
    RoleLaunchContext,
    resolve_role_launch_context,
)
from agents_remember.application.skill_resources.capsule import (
    CapsuleSeatAddress,
    routed_admission_for,
)
from agents_remember.cli import role_launch_preparation
from agents_remember.cli.paseo_launch import StartingAgent
from agents_remember.cli.role_launch_preparation import (
    RoleHandoverRequest,
    _ar_mcp_context,
    _compile_handover,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.mcp.registration.role_agents import register_role_agent_tools
from agents_remember.models.role_capsules.types import (
    CapsuleAdmittedFacts,
    CapsuleBinding,
    CapsuleOperation,
    CapsuleRoleSeat,
    CapsuleToolPolicy,
    compute_content_digest,
)
from agents_remember.models.role_launcher import LauncherRole, RoleSelection
from agents_remember.models.tools.public_roster import PUBLIC_TOOLS
from agents_remember.tasks import TaskDocument
from agents_remember_test_support.testing.curation_doctrine import (
    RETIRED_CURATION_STATEMENTS,
)
from agents_remember_test_support.testing.leaf_instruction_wording import (
    LEAF_CLAUSES,
    ROLES,
    autonomy_clauses,
    forbidden_autonomy_clauses,
    missing_clauses,
    normalized,
)
from mcp.server.fastmcp import FastMCP

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
LIFECYCLE = Path("l-01-agent-lifecycles")
CANONICAL = REPOSITORY_ROOT / "skills"
# The copy the capsule compiler reads; a rewrite is not live until the sync has run.
PACKAGED = (
    REPOSITORY_ROOT / "mcp" / "src" / "agents_remember" / "package_data" / "runtime" / "skills"
)

# PNT-R06 item 12, searched case-insensitively.
FORBIDDEN = (
    "orca",
    "native Run",
    "Run/Task/Dispatch",
    "Dispatch worker",
    "worker-start",
    "run-create",
    "worker_done",
    "dispatch_agent",
    "message_parent",
    "message_child",
)
# The report directory of a role, as a segment of the report and artifact paths in a handover. It
# carried the previous host's name until the rename leaf (PNT-R08); the search below now reads
# the whole text, this segment included.
REPORT_DIRECTORY = "/role-launch/"

TOOL_SERVER = "agents-remember-task"
OTHER_INSTALLATION = "agents-remember"
LAUNCHER_ROLES: tuple[str, ...] = get_args(LauncherRole)
STARTERS = ("architect", "orchestrator", "manager")
_NAMED_TOOL = re.compile("`(" + "|".join(sorted(map(re.escape, PUBLIC_TOOLS))) + ")`")


def forbidden_in(text: str) -> list[str]:
    """The forbidden strings a text contains, in the packet's spelling."""

    folded = text.casefold()
    return [word for word in FORBIDDEN if word.casefold() in folded]


def instruction_files() -> list[Path]:
    """The router, the launcher roles' files and their operations, relative to a skill tree."""

    manifest = json.loads((CANONICAL / LIFECYCLE / "composition-manifest.json").read_text("utf-8"))
    roles = [manifest["roles"][role]["file"] for role in LAUNCHER_ROLES]
    operations = [
        entry["source"]
        for entry in manifest["operations"].values()
        if set(entry["applies_to_roles"]) & set(LAUNCHER_ROLES)
    ]
    return [LIFECYCLE / name for name in ("SKILL.md", *roles, *sorted(operations))]


def read(relative: Path) -> str:
    return (CANONICAL / relative).read_text(encoding="utf-8")


def role_text(role: str) -> str:
    return " ".join(read(LIFECYCLE / "roles" / f"{role}.md").split())


class InstructionFileWordingTests(unittest.TestCase):
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
                "When a System Specialist is needed, start it with `role_start` on `agents-remember-task`; you may start that role. Give the returned agent ID, report path, handover artifact path and status to the Manager or Orchestrator coordinating the work; neither gains permission to start that role.",
                "Put questions for the developer, and requests for a ruling, in your own chat; the developer answers there. Under developer-chosen direct coordination, review the full candidate diff, including unattributed changes; under delegation, the Manager owns the leaf-diff and repair loop and supplies aggregate evidence through the Orchestrator where one exists. Treat invariant-family attribution as an additional review dimension, not a filter on changed files. Keep a finished turn, semantic review, curation, and paired Git publication as separate facts. Do not accept your own work or convert green checks into requirement acceptance. Record decisions and task truth through the existing AR task/data owners.",
                "You run no test suite, no build and no investigation of a failure in your own chat. You perform no step of a landing except the paired closeout in the developer-chosen direct-coordination case below. Under delegation, give a needed check or investigation to the Manager or Orchestrator coordinating the work; under developer-chosen direct coordination, assign it to a leaf's Worker or Reviewer, including a job that belongs to no leaf.",
                "You may author requirements, tasks, plans, and rulings. Do not write onboarding. Flat mode here is only the developer-chosen direct-coordination exception. In flat mode, when no Manager or Orchestrator owns closeout, you may coordinate the existing c-09/c-12 paired transaction only under its already-delegated authority and after independent review and required curation evidence exist. In that case you call the paired closeout yourself and start no Manager for that step alone; it grants no other execution. Human-pinned approvals remain human; never substitute raw Git or self-approval. A high-impact design/security choice, requirement contradiction, missing authority, or scope change returns to the developer. One concise report preserves the decision, refs, evidence, open questions, and limits; no completion claim replaces inspection of the artifact.\n",
            ],
            "roles/orchestrator.md": [
                "You coordinate one selected sprint when two or more masters are worked on at the same time, or when the developer explicitly asks for an Orchestrator above a single master. You operate at Projects altitude. A launch supplies the canonical sprint reference and allowed workspace; the state of an agent in Paseo is not an AR acceptance ledger.",
                "For the accepted objective, reuse each existing Manager named in the Architect's handover and start one Manager for each other master with `role_start` on the `agents-remember-task` tool server, on a selection under your own sprint. Those Managers coordinate their distinct Workers, Reviewers, and Curators. You may start Manager, Worker, Reviewer, and Curator. Follow the start and messaging rules in the selected Coordination operation. Your tool server's binding names you as the sender. Address each recipient explicitly by agent ID or by role and task references, and keep the returned agent IDs; do not act as an invisible proxy.",
                "Keep each work item tied to its canonical AR task and primary requirement. Do not start duplicates while a start is `unknown` or an execution is open; repeat the same request ID to reconcile. Follow each assignment with `role_message` on `agents-remember-task` and its `wait`, handling questions and results until the assigned work is done, blocked, or needs a real developer decision; a start is not completion. Do not add a separate background poller. Handle the order of the day, operational problems, checks before and after a landing, and paired closeout within delegated authority. An Orchestrator started from the dashboard has no parent, needs none, and must not invent one: put developer decisions in your own chat.",
                "Decide ordinary requirement interpretation within the intended promise and operational rulings yourself, and keep the work going. Keep those rulings in one durable rulings record, a list of requirement sentences that should change, and one status file under the selected task's notes; name their paths in your report. When the Architect started you, it reads these files on its own and aligns the requirement texts with the rulings. These records do not authorize new or dropped scope or a changed promise.",
                "If you cannot start a needed role within your permissions, report that blocker to the agent that started you with `role_message` on `agents-remember-task`; without a parent, report it in your own chat and invent no Architect ID. Never start that role yourself. The Architect may start a needed System Specialist and supply its returned agent ID, report path, handover artifact path and status so you can coordinate its work.",
                "When the Architect started you, message it only for a developer decision: new or dropped scope or a change to a requirement's promise; something only the developer can do or approve; an override of a role default; or a blocker that none of your own decisions can remove. Send these with `role_message` on `agents-remember-task` to the parent agent ID in your handover. Forward only questions requiring the developer and permission notices as stated below; send no other role messages or landing notices. Ordinary requirement readings and operational rulings stay with you; do not ask the Architect to perform them. Send one further message with the path of your report when the whole assignment is finished or cannot be finished. Send no other messages to the Architect.",
                "If that message cannot be delivered, write the matter in your status file, continue every part of the work that does not depend on the developer's decision, and send the message again later. Do not decide the developer's question yourself.",
                "A turn ending proves only that it ended. Verify each Manager's delivery and aggregate evidence; the Manager owns the leaf-diff, repair and evidence loop. You may inspect supplied actual diffs for acceptance without taking over that loop. Request an independent Reviewer when the brief or risk requires it; a Reviewer never adjudicates its own work. Request a Curator for affected memory/onboarding when needed. Keep reports, findings, review, curation, and Git publication separately addressed. Use the existing AR task and paired Git owners for their semantic records; do not claim acceptance or landing from the status of an agent in Paseo.",
            ],
            "roles/manager.md": [
                "You coordinate one selected master at Projects altitude. You do not own the portfolio. For one master, the Architect delegates coordination to one Manager. An Orchestrator sits above the Managers when two or more masters are worked on at the same time, or when the developer asks for one above a single master. Direct coordination by the Architect is only the developer-chosen exception. When another agent started you, report to that agent using its parent agent ID in your handover, unless the Architect instructs the reporting-recipient change below.",
                "Read each leaf's reports and task records yourself. Worker, Reviewer and Curator hand freezes, findings, verdicts and memory changes directly to each other; you relay none of those contents. Inspect the complete changed-file diff and both verdicts before deciding the gate. Keep closeout, integration, task status and acceptance writes, and the order of landings. Reviews are evidence, not gate decisions. Preserve stable requirement/finding IDs and distinguish implementation, review, curation and publication status. A Manager started from the dashboard has no parent and needs none: put a needed developer decision in your own chat and do not invent an Architect ID.",
                "Decide the order of your leaves, agent starts and replacements, operational problems, ordinary requirement interpretation within the intended promise, checks before and after a landing, and paired closeout and integration within delegated authority. The Reviewer opens and records ordinary code review rounds itself; memory review keeps its own sealed reports, IDs and pass count and opens no code round. Keep the ordinary limits and sealed finding IDs. Only the developer grants an extra round or memory pass; record that approval in the leaf's decisions. Accepting a verdict for the gate stays yours. Assign a check or investigation that belongs to no leaf to the Worker or Reviewer of the nearest leaf.",
                "Keep your rulings in one durable rulings record, a list of requirement sentences that should change, and one status file under the selected master's notes; name their paths in your report. The Architect reads these files on its own and aligns the requirement texts with the rulings. These records do not authorize new or dropped scope or a changed promise.",
                "When another agent started you, message your parent or the instructed reporting recipient only for a developer decision: new or dropped scope or a change to a requirement's promise; something only the developer can do or approve; an override of a role default; or a blocker that none of your own decisions can remove. Send these with `role_message` on `agents-remember-task` to the parent agent ID in your handover, or to the instructed reporting-recipient ID recorded in your report. When the Orchestrator owns coordination, do not bypass it to the Architect. Forward only questions requiring the developer and permission notices as stated below; send no other role messages or landing notices. Ordinary requirement readings and operational rulings stay with you; keep the work going. Send one further message with the path of your report when the whole assignment is finished or cannot be finished. Send no other messages to that recipient.",
                "If the Architect who started you names an Orchestrator's agent ID in a message when a second master starts, record that instruction and reporting-recipient ID in your durable report and send developer-needed matters to that Orchestrator from then on. This changes your reporting recipient, not your fixed launch parent; no other message changes whom you report to.",
                "If you cannot start a needed role within your permissions, report that blocker to your parent or instructed reporting recipient with `role_message` on `agents-remember-task`; without a parent, report it in your own chat and invent no Architect ID. Never start that role yourself. The Architect may start a needed System Specialist and supply its returned agent ID, report path, handover artifact path and status to you or the Orchestrator above you.",
                "If an upward message cannot be delivered, write the matter in your status file, continue every part of the work that does not depend on the developer's decision, and send the message again later. Do not decide the developer's question yourself.",
            ],
            "operations/planning.md": [
                "The Architect's first delegation for one master is one Manager on that canonical master. For two or more masters worked on at the same time, it is one Orchestrator on the canonical sprint, which starts one Manager per master. This delegation rule applies only to the Architect; Designers and Strategists do not start a coordinating role. Direct coordination by the Architect is only the developer-chosen exception; in that case, the Architect directly coordinates distinct Workers, Reviewers, and Curators. The developer may also ask for an Orchestrator above a single master. Keep one explicit owner per task and preserve the agent IDs of started roles. Do not fabricate a sprint/master for a taskless project role or an agent identity to satisfy an old hierarchy."
            ],
            "operations/coordination.md": [
                "The Manager starts a leaf's Worker and Reviewer together and its Curator at the first freeze. The Reviewer first cold-reads the requirement before code exists. The Worker, Reviewer and Curator run the leaf repair loop directly with `role_message` on `agents-remember-task`: Worker freeze to Reviewer, verdict and sealed findings back to Worker, passed freeze and producer lists to Curator, memory change and verdict between Curator and Reviewer. A Curator's code concern goes to both Reviewer and Worker even after code PASS; the Reviewer answers it and the Curator records the answer. The Manager reads their reports, decides the gate and keeps closeout, integration, status, acceptance and landing order; it relays no freeze, finding, verdict or memory change. Keep paired code/memory/report paths and exact task references in each hand-over.",
                "The Architect delegates coordination first to one Manager for one master, or to one Orchestrator on the sprint when two or more masters are worked on at the same time. The Orchestrator starts one Manager per master; each Manager coordinates its distinct Workers, Reviewers, and Curators. Only the developer may choose direct coordination by the Architect. The developer may also ask for an Orchestrator above a single master. If the Architect's coordinating-agent start is refused, the Architect tells the developer the refusal and its reason; it does not fall back to direct coordination.",
                "Send one message to one agent with `role_message` on `agents-remember-task`, addressed by agent ID or by role plus task references. A leaf hand-over uses the role and exact task reference in `leafSeats.roleMessageArguments`; use a product-supplied agent ID only whole and unchanged. The current leaf resolver reaches the newest live agent; an empty or doubled-seat refusal is reported once to the Manager with its text and no candidate is chosen. A role address never means you yourself. A recipient that is mid-turn takes the message up without its turn being cancelled, or the call is refused as busy. A recipient that waits for a permission decision is refused as busy as well, because a message would answer the permission with a denial: the developer answers it in that agent's chat, then send again. So is a recipient whose start has not finished. A closed session is resumed first; an archived or missing agent is refused and stays as it is. With `wait` the call returns when the turn that took your message has ended: with the recipient's final text, or saying that the turn failed or was cancelled; or it returns a pending permission, or a timeout with the message still delivered. It can also return `accepted` without a text, when the host cannot say which turn took the message; read the reply later then. The text of a message delivered during a turn can be that running turn's own; `detail` says so. Do not wait on an agent that may be waiting on you: two agents that wait on each other both stand still until one wait runs out. If your own tool calls are cut off earlier than the wait, pass a shorter `timeout_seconds`. An Orchestrator, Manager or developer-direct-coordinating Architect follows assignments until the work is complete, blocked, or a true developer decision is needed; it does not start a role and stop. The Architect releases execution coordination after the handover and continues the developer conversation and status reading; it holds no wait on an Orchestrator or Manager for execution completion. On a refusal or an uncertain result, act on the named reason; do not guess a parent or start duplicate work. A role started from the dashboard works directly with the developer and needs no parent.",
            ],
            "SKILL.md": [
                "Use the agent IDs these tools return and the sender line of each message you receive; never invent a sender, recipient, parent, or delivery result.",
                "AR remains authoritative for canonical tasks, requirements, knowledge, curation, and paired Git operations.",
                "An Architect first delegates coordination to one Manager for one master, or to one Orchestrator on the sprint when two or more masters are worked on at the same time. The Orchestrator starts one Manager per master. Only the developer may choose direct coordination by the Architect. The developer may also ask for an Orchestrator above a single master. A role started from the dashboard has no parent agent and needs none. Existing approvals and rulings remain durable across reconnects and compaction; ask again only for new or changed scope, a real requirement conflict, or an unresolved human-pinned decision.",
            ],
            "roles/curator.md": [
                "hand-off list, the resolved baseline and `--publish --commit`; and the taskless `agents-remember",
                "knowledge-bootstrap` entry the `c-14-knowledge-bootstrap` skill states, which belongs to a session with **no",
                "enclosure in scope** — it refuses one (`enclosure_in_scope`) rather than publishing onto a task's line. Those",
                "two are the write plane's reachable entry points; the mounted `knowledge_change` tool refuses every kind and",
                "exists only to name the route.",
                "`../operations/curation.md` § Record the task comparison; it retains the comparison and authors no knowledge.",
                "Enumerate one full-intake worklist of distinct outstanding memory actions in your report:",
                "Use `role_message` on `agents-remember-task`, addressed by role and this leaf's exact task references",
                "A Curator started from the dashboard has no parent and needs none.",
                "A Curator starts no role:",
                "A harness without sub-agents can do all the same work.",
            ],
        }
        for source, clauses in required.items():
            reading = " ".join(read(LIFECYCLE / source).split())
            for clause in clauses:
                with self.subTest(source=source, clause=clause):
                    self.assertIn(" ".join(clause.split()), reading)

    def test_direct_closeout_keeps_the_architect_and_its_existing_gates(self) -> None:
        architect = role_text("architect")
        kept = "In flat mode, when no Manager or Orchestrator owns closeout, you may coordinate the existing c-09/c-12 paired transaction only under its already-delegated authority and after independent review and required curation evidence exist."
        self.assertIn(kept, architect)
        self.assertIn(
            "Flat mode here is only the developer-chosen direct-coordination exception.", architect
        )
        closeout = " ".join(read(LIFECYCLE / "operations/closeout.md").split())
        self.assertIn(
            "In a flat run with neither role, the owning Architect may coordinate the same transaction only when existing delegated c-09/c-12 authority permits it.",
            closeout,
        )
        self.assertNotIn(kept, [row.statement for row in RETIRED_CURATION_STATEMENTS])

    def test_every_role_organises_its_harness_work_with_seat_boundaries(self) -> None:
        self.assertEqual(
            sorted(path.stem for path in (CANONICAL / LIFECYCLE / "roles").glob("*.md")),
            sorted(ROLES),
        )
        for role in ROLES:
            reading = role_text(role)
            with self.subTest(role=role):
                self.assertEqual(missing_clauses(reading, autonomy_clauses(role)), [])
                self.assertEqual(forbidden_autonomy_clauses(role, reading), [])
                self.assertNotIn("one level deep", reading)
                self.assertNotIn("must fan out", reading)
                self.assertNotIn("Sub-agents for read/search only", reading)
        self.assertIn("A Worker starts no role", role_text("worker"))
        self.assertIn("Keep exactly one Curator writer", role_text("curator"))

    def test_leaf_roles_and_operations_deliver_their_own_positive_obligations(self) -> None:
        for source, clauses in LEAF_CLAUSES.items():
            with self.subTest(source=source):
                self.assertEqual(missing_clauses(read(LIFECYCLE / source), clauses), [])

    def test_removing_each_affirmative_clause_is_detected_on_its_surface(self) -> None:
        surfaces = dict(LEAF_CLAUSES)
        surfaces.update(
            {
                f"roles/{role}.md": {
                    **LEAF_CLAUSES.get(f"roles/{role}.md", {}),
                    **autonomy_clauses(role),
                }
                for role in ROLES
            }
        )
        for source, clauses in surfaces.items():
            reading = normalized(read(LIFECYCLE / source))
            self.assertEqual(missing_clauses(reading, clauses), [], source)
            for name, alternatives in clauses.items():
                with self.subTest(source=source, obligation=name):
                    mutant = reading
                    for clause in alternatives:
                        mutant = mutant.replace(normalized(clause), "")
                    self.assertNotEqual(mutant, reading, "control must remove real instructions")
                    self.assertIn(name, missing_clauses(mutant, clauses))

    def test_equivalent_preserved_identity_and_no_role_boundaries_are_allowed(self) -> None:
        for role in ROLES:
            reading = role_text(role).replace("starts no role", "does not start a role")
            with self.subTest(role=role):
                self.assertEqual(missing_clauses(reading, autonomy_clauses(role)), [])
        worker = role_text("worker").replace(
            "Use an agent ID only whole and unchanged as the product supplied it",
            "Use only the whole, unchanged agent ID supplied by the product",
        )
        self.assertEqual(missing_clauses(worker, LEAF_CLAUSES["roles/worker.md"]), [])

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

    def test_the_surfaces_are_the_router_seven_roles_and_their_eight_operations(self) -> None:
        names = [path.relative_to(LIFECYCLE).as_posix() for path in instruction_files()]
        self.assertEqual(names[0], "SKILL.md")
        self.assertEqual(names[1:8], [f"roles/{role}.md" for role in LAUNCHER_ROLES])
        self.assertEqual(
            names[8:],
            [
                f"operations/{name}.md"
                for name in (
                    "closeout",
                    "coordination",
                    "curation",
                    "implementation",
                    "orientation",
                    "planning",
                    "recovery",
                    "review",
                )
            ],
        )

    def test_no_forbidden_host_string_is_left_and_the_packaged_copy_is_the_same_text(self) -> None:
        for relative in (*instruction_files(), LIFECYCLE / "composition-manifest.json"):
            with self.subTest(file=relative.as_posix()):
                self.assertEqual(forbidden_in(read(relative)), [])
                self.assertEqual(
                    (PACKAGED / relative).read_bytes(),
                    (CANONICAL / relative).read_bytes(),
                    "run python3 scripts/sync-skills.py",
                )

    def test_every_passage_that_names_an_ar_tool_names_the_task_tool_server(self) -> None:
        naming = 0
        for relative in instruction_files():
            for passage in re.split(r"\n\s*\n", read(relative)):
                tools = sorted(set(_NAMED_TOOL.findall(passage)))
                if not tools:
                    continue
                naming += 1
                with self.subTest(file=relative.as_posix(), tools=tools):
                    self.assertIn(f"`{TOOL_SERVER}`", passage)
        # The rule is checked on real passages, not satisfied by finding none.
        self.assertGreaterEqual(naming, 20)

    def test_the_router_names_the_host_the_two_tools_and_the_only_tool_server(self) -> None:
        router = " ".join(read(LIFECYCLE / "SKILL.md").split())
        for sentence in (
            "Paseo is the host",
            "Call every AR tool on the tool server named `agents-remember-task`",
            # Where the tools are: found by the server's name in either spelling, not by a
            # tool's own name, which another installation's server carries too.
            "its tools are in your session",
            "find them by the server's name in either spelling, `agents-remember-task` or "
            "`agents_remember_task`: as tools declared under a prefixed name (for example "
            "`mcp__agents_remember_task__server_info`), or, when your harness's own "
            "instructions list a server in that spelling behind its tool search or script "
            "tool, the way those instructions say.",
            "A tool that lists or proxies tool servers holds only the servers it was configured "
            "with: unless it lists `agents-remember-task` itself, do not use it for AR tools at "
            "all, not even to search for or describe a tool by its name, and take its answer "
            '"server not found" as speaking only for that tool.',
            "Do not connect to, list, describe or call a tool server named `agents-remember` or "
            "any of its tools: it belongs to another installation, and its tools carry the same "
            "tool names.",
            "wait a few seconds and look once more before you report it missing",
            # What a recipient does with another agent's message.
            "A message another agent sent you begins with a line `From <role> · <task> · agent "
            "<agent ID>`: your reply in that turn, in your own chat, is what the sender "
            "receives, so answer the message there.",
            "With a parent named in `host.parent`, send every question requiring the developer's decision to that parent with `role_message` on `agents-remember-task`, addressed to its agent ID.",
            "A role started from the dashboard has no parent agent and needs none.",
            "an agent created outside `role_start` on `agents-remember-task` has no capsule and no binding",
        ):
            with self.subTest(sentence=sentence):
                self.assertIn(sentence, router)
        for tool in ("role_start", "role_message"):
            self.assertIn(tool, PUBLIC_TOOLS)
            self.assertIn(tool, router)

    def test_the_orientation_says_where_the_tools_are_and_what_a_reply_is(self) -> None:
        orientation = " ".join(read(LIFECYCLE / "operations" / "orientation.md").split())
        for sentence in (
            "Call AR tools only on the tool server named `agents-remember-task`.",
            "Find its tools by the server's name, spelled `agents-remember-task` or "
            "`agents_remember_task`: declared under a prefixed name, or listed in your harness's "
            "own instructions behind its tool search or script tool.",
            "A tool that lists or proxies tool servers is not the way to AR tools unless it "
            "lists `agents-remember-task` itself: do not search or describe AR tools through "
            'it, and take its "server not found" as that tool\'s answer only.',
            "Do not connect to, list, describe or call a server named `agents-remember` or any "
            "of its tools: it belongs to another installation.",
            "wait a few seconds and look once more before you report it missing",
            "A message another agent sent you begins with a `From` line: your reply in that "
            "turn is what the sender receives, so answer it there.",
        ):
            with self.subTest(sentence=sentence):
                self.assertIn(sentence, orientation)

    def test_each_role_is_told_how_to_reach_agents_and_the_developer(self) -> None:
        for role in LAUNCHER_ROLES:
            with self.subTest(role=role):
                text = role_text(role)
                self.assertIn("`role_message`", text)
                self.assertIn("in your own chat", text)
                if role in STARTERS:
                    self.assertIn("`role_start`", text)
                else:
                    self.assertNotIn("`role_start`", text)
                    self.assertRegex(
                        text, r"A (Worker|Reviewer|Curator|System Specialist) starts no role"
                    )
        for role in set(LAUNCHER_ROLES) - {"architect"}:
            with self.subTest(role=role, told="a role without a parent needs none"):
                self.assertRegex(
                    role_text(role),
                    r"started from the dashboard,? (has no parent|has none|needs no parent)",
                )
        coordination = " ".join(read(LIFECYCLE / "operations" / "coordination.md").split())
        for sentence in (
            "an Architect every role except Architect",
            "an Orchestrator Manager, Worker, Reviewer, and Curator under its own sprint",
            "a Manager Worker, Reviewer, and Curator under its own master",
            "call it again with the same request ID",
            "takes the message up without its turn being cancelled",
            "A role started from the dashboard works directly with the developer and needs no parent.",
            # What the message tool answers, as its own description says it.
            "A role address never means you yourself.",
            "A recipient that waits for a permission decision is refused as busy as well, "
            "because a message would answer the permission with a denial: the developer answers "
            "it in that agent's chat, then send again.",
            "So is a recipient whose start has not finished.",
            "saying that the turn failed or was cancelled",
            "It can also return `accepted` without a text, when the host cannot say which turn "
            "took the message; read the reply later then.",
            "The text of a message delivered during a turn can be that running turn's own; "
            "`detail` says so.",
            "Do not wait on an agent that may be waiting on you: two agents that wait on each "
            "other both stand still until one wait runs out.",
        ):
            with self.subTest(sentence=sentence):
                self.assertIn(sentence, coordination)
        architect = " ".join(role_text("architect").split())
        for sentence in (
            "A recipient that waits for a permission decision, or whose start has not finished, "
            "is refused as busy",
            "its final text, or that the turn failed or was cancelled, a pending permission, or "
            "a timeout; `accepted` without a text means that the reply is to be read later.",
            "Do not wait on an agent that may be waiting on you.",
        ):
            with self.subTest(role="architect", sentence=sentence[:40]):
                self.assertIn(sentence, architect)

    def test_the_workers_report_names_its_agent_id(self) -> None:
        # Where the report of the previous host named the execution, it names the agent.
        implementation = " ".join(read(LIFECYCLE / "operations" / "implementation.md").split())
        self.assertIn(
            "exact checks and results, your agent ID as the `senderLine` of `role_message` on "
            '`agents-remember-task` names it (write "agent ID not known" when you sent no '
            "message), and open limitations.",
            implementation,
        )

    def test_the_curator_writer_is_admitted_for_the_selected_runtime(self) -> None:
        # MIK owns the converted file writer; a capsule without its capability reports the gap.
        curation = " ".join(read(LIFECYCLE / "operations" / "curation.md").split())
        for label, text in (("role", role_text("curator")), ("operation", curation)):
            with self.subTest(told=label):
                self.assertIn("agents-remember knowledge-ingest", text)
                self.assertIn("--authorization-ref <ref> --commit --json", text)
                self.assertIn("publishes no dataset", text)
                self.assertIn(
                    "`--baseline`, `--publish` and `--publish-to` are refused there", text
                )
                self.assertIn("source-bound command", text)
                self.assertIn("Paseo capsule exposes no", text)
                self.assertIn("report", text)
                self.assertIn("not written", text)
                self.assertIn("another installation's", text)
                self.assertIn("`agents-remember-task`", text)
                self.assertIn("`arMcpContext.readerArguments`", text)
                self.assertIn("selected memory root", text)
                self.assertIn("memory_quality_check", text)
                self.assertIn("prepare", text)
                self.assertIn("publish", text)
                self.assertIn("validate", text)
                self.assertNotIn("the writer is not available to you on this line", text)

    def test_the_manifest_grants_the_role_tools_to_the_roles_that_may_use_them(self) -> None:
        manifest = json.loads(read(LIFECYCLE / "composition-manifest.json"))
        for role in LAUNCHER_ROLES:
            with self.subTest(role=role):
                tools = manifest["roles"][role]["tools"]
                self.assertIn("role_message", tools)
                self.assertEqual("role_start" in tools, role in STARTERS)
                self.assertEqual(set(tools) - set(PUBLIC_TOOLS), set())


class HandoverTextWordingTests(unittest.TestCase):
    """The handover text as the launch code builds it, with the reader context of the launch.

    The role resolver and role-specific capsule compiler run. Only task-reader/enclosure plumbing
    is isolated; no launch, model or lifecycle operation runs in this fixture.
    """

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        self.config = McpRuntimeConfig(
            config_path=root / "settings" / "mcp.json",
            coordination_root=root / "coordination",
            workspace_root=root / "projects",
            transcript_root=root / "coordination" / "logs" / "mcp",
            repositories={
                "repo": RepositoryScope(
                    repo_id="repo", path=root / "projects" / "repo", memory_root=root / "memory"
                )
            },
        )
        for name, stub in (
            ("compile_launch_capsule", self.role_capsule),
            ("build_context_packet", lambda *_args, **_kwargs: {"repo": {"id": "repo"}}),
            ("_read_task_doc", lambda _config, resolved: {"canonicalTaskPath": resolved.ref.key}),
            # A leaf's readers are confined to its enclosure, which this test does not create.
            ("task_scoped_mcp_config_for_reader", lambda config, **_scope: config),
        ):
            patcher = patch.object(role_launch_preparation, name, stub)
            patcher.start()
            self.addCleanup(patcher.stop)

    def role_capsule(
        self, _config: McpRuntimeConfig, request: Any, *, operation: CapsuleOperation
    ) -> Any:

        corpus = PACKAGED / LIFECYCLE
        manifest_bytes = (corpus / "composition-manifest.json").read_bytes()
        manifest = json.loads(manifest_bytes)
        role = request.role
        binding = CapsuleBinding(
            operation=operation,
            admitted=CapsuleAdmittedFacts(
                task_reference=request.task_document_ref.key
                if request.task_document_ref
                else "fixture:Projects",
                task_document_digest=compute_content_digest(b"fixed handover comparison inputs"),
                seat=CapsuleRoleSeat(role=role, altitude=manifest["roles"][role]["altitude"]),
                repository_id="repo",
                work_branch="fixture",
                tool_policy=CapsuleToolPolicy(granted=frozenset(PUBLIC_TOOLS)),
            ),
        )
        outcome = compile_admitted_capsule(
            binding,
            routed_admission_for(
                corpus,
                "composition-manifest.json",
                manifest_bytes,
                CapsuleSeatAddress(role=role, operation=operation),
            ),
        )
        self.assertTrue(outcome.ok, outcome.render_explanation())
        assert outcome.result is not None
        return _capsule_launch(role, outcome.result)

    def launch_of(self, role: str) -> tuple[RoleLaunchContext, dict[str, str]]:
        """Taskless, sprint, master and leaf selections admitted by the production resolver."""

        root = self.config.coordination_root
        documents = {
            "sprint/task.json": {"id": "SPRINT", "kind": "master", "orchestrates": ["master"]},
            "master/task.json": {
                "id": "MASTER",
                "kind": "master",
                "subTasks": [
                    {"number": "01", "name": "Leaf", "file": "01_leaf.md", "status": "inProgress"}
                ],
            },
            "master/01_leaf.json": {
                "id": "01_LEAF",
                "kind": "subTask",
                "master": "task.md",
                "status": "inProgress",
            },
        }
        for relative, fields in documents.items():
            document = TaskDocument.model_validate(
                {
                    "slug": Path(relative).stem,
                    "title": fields["id"],
                    "repo": "repo",
                    "createdAt": "2026-10-02T00:00:00+00:00",
                    **fields,
                }
            )
            path = root / "tasks" / "repo" / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(document.model_dump_json(by_alias=True), encoding="utf-8")
        refs = {}
        if role not in {"architect", "system-specialist"}:
            refs["sprintDocumentRef"] = {"repository": "repo", "path": "sprint/task.json"}
        if role in {"manager", "worker", "reviewer", "curator"}:
            refs["masterDocumentRef"] = {"repository": "repo", "path": "master/task.json"}
        if role in {"worker", "reviewer", "curator"}:
            refs["taskDocumentRef"] = {"repository": "repo", "path": "master/01_leaf.json"}
        context = resolve_role_launch_context(
            self.config, RoleSelection.model_validate({"role": role, **refs})
        )
        workspace = {"path": self.config.workspace_root.as_posix()}
        if context.task is not None:
            enclosure = self.config.workspace_root / "enclosure"
            workspace = {
                "path": (enclosure / "code").as_posix(),
                "contractPath": (enclosure / "contract.json").as_posix(),
                "taskReportAccessRoot": (context.task.path.parent / "notes" / "reports").as_posix(),
            }
        return context, workspace

    def compiled(self, role: str, started_by: StartingAgent | None) -> tuple[str, dict[str, Any]]:
        context, workspace = self.launch_of(role)
        prepared = _compile_handover(
            RoleHandoverRequest(
                config=self.config,
                context=context,
                workspace=workspace,
                agent_id="some-provider",
                ar_mcp_context=_ar_mcp_context(self.config, context, workspace),
                request_id=uuid.uuid4(),
                started_by=started_by,
            )
        )
        return prepared["prompt"], prepared["handover"]

    def test_developer_question_channel_matches_role_and_parent(self) -> None:
        ordinary = (
            "Put every question for the developer in your own chat: write it as your reply "
            "in this session and end your turn. The developer reads this chat in the "
            "dashboard and answers in it. Never send a developer question to another agent."
        )
        architect = StartingAgent("1f3c2f0e-6a57-4f0b-9d4e-0c8f1a2b3c4d", "architect", "Projects")
        orchestrator = StartingAgent(
            "2f3c2f0e-6a57-4f0b-9d4e-0c8f1a2b3c4d", "orchestrator", "SPRINT"
        )
        for role, parent, routed in (
            ("orchestrator", architect, True),
            ("orchestrator", None, False),
            ("manager", architect, True),
            ("manager", orchestrator, True),
            ("manager", None, False),
            ("worker", architect, True),
            ("worker", orchestrator, True),
            (
                "worker",
                StartingAgent("3f3c2f0e-6a57-4f0b-9d4e-0c8f1a2b3c4d", "manager", "MASTER"),
                True,
            ),
            ("reviewer", architect, True),
            ("curator", architect, True),
            ("system-specialist", architect, True),
        ):
            with self.subTest(role=role, parent=parent.role if parent else None):
                prompt, handover = self.compiled(role, parent)
                question = handover["host"]["developerQuestions"]
                self.assertIn(question, prompt)
                if routed:
                    assert parent is not None
                    self.assertIn(
                        "send what needs the developer's decision to that parent", question
                    )
                    self.assertIn(f"addressed to agent {parent.agent_id}", question)
                    self.assertIn("role_message on agents-remember-task", question)
                    self.assertIn("takes precedence over generic own-chat guidance", question)
                    self.assertIn("do not end your turn on the question", question)
                else:
                    self.assertEqual(question.encode(), ordinary.encode())
        for role in LAUNCHER_ROLES:
            with self.subTest(dashboard_role=role):
                _prompt, handover = self.compiled(role, None)
                self.assertEqual(handover["host"]["developerQuestions"].encode(), ordinary.encode())

    def test_owner_relation_routes_developer_questions_and_preserves_coordinators(self) -> None:
        parents = (
            StartingAgent("1f3c2f0e-6a57-4f0b-9d4e-0c8f1a2b3c4d", "architect", "Projects"),
            StartingAgent("2f3c2f0e-6a57-4f0b-9d4e-0c8f1a2b3c4d", "orchestrator", "SPRINT"),
            StartingAgent("3f3c2f0e-6a57-4f0b-9d4e-0c8f1a2b3c4d", "manager", "MASTER"),
        )
        categories = "new or dropped scope or a change to a requirement's promise; something only the developer can do or approve; an override of a role default; or a blocker that none of your own decisions can remove."
        final = "Send one further message with the path of your report when the whole assignment is finished or cannot be finished."
        for role in LAUNCHER_ROLES:
            for parent in (None, *parents):
                with self.subTest(role=role, parent=parent.role if parent else None):
                    prompt, handover = self.compiled(role, parent)
                    relation = handover["host"]["ownerRelation"]
                    self.assertIn(relation, prompt)
                    if parent is None:
                        expected = "This role was started from the dashboard launcher. It has no parent agent and needs none: the developer who reads this chat owns its decisions. The selected AR sprint/master/leaf is work scope, not a parent."
                        self.assertEqual(relation.encode(), expected.encode())
                    elif role in {"orchestrator", "manager"}:
                        self.assertIn(
                            f"Agent {parent.agent_id} ({parent.role} · {parent.subject}) started this role and is its parent in Paseo.",
                            relation,
                        )
                        self.assertIn(categories, relation)
                        self.assertIn(final, relation)
                        self.assertNotIn("questions about the assignment and your result", prompt)
                    elif role in {"worker", "reviewer", "curator"}:
                        self.assertIn("Send no routine result to the parent.", relation)
                        self.assertIn("leafSeats.roleMessageArguments", relation)
                    else:
                        expected = f"Agent {parent.agent_id} ({parent.role} · {parent.subject}) started this role and is its parent in Paseo. Send that agent your questions about the assignment, questions requiring the developer's decision, and your result with role_message, addressed to its agent id. The selected AR sprint/master/leaf is work scope; the parent is the agent named here and no other."
                        self.assertEqual(relation.encode(), expected.encode())

    def test_the_first_message_names_no_forbidden_host_string(self) -> None:
        parent = StartingAgent("1f3c2f0e-6a57-4f0b-9d4e-0c8f1a2b3c4d", "architect", "Projects")
        for label, role, started_by in (
            ("a taskless role started from the dashboard", "architect", None),
            ("a sprint-bound role started by an agent", "orchestrator", parent),
            ("a leaf role started by an agent", "worker", parent),
        ):
            with self.subTest(label):
                prompt, handover = self.compiled(role, started_by)
                self.assertIn(REPORT_DIRECTORY, prompt)
                self.assertEqual(forbidden_in(prompt), [])
                self.assertEqual(forbidden_in(json.dumps(handover, ensure_ascii=False)), [])
                # The first message carries the reader context the launch compiled.
                self.assertIn(handover["arMcpContext"]["missingCapabilityAction"], prompt)

    def test_the_reader_context_names_the_readers_of_the_task_tool_server(self) -> None:
        _prompt, leaf = self.compiled("worker", None)
        _prompt, taskless = self.compiled("architect", None)
        self.assertEqual(
            (leaf["arMcpContext"]["scopeKind"], taskless["arMcpContext"]["scopeKind"]),
            ("canonical-leaf", "configured-projects"),
        )
        self.assertEqual(
            leaf["arMcpContext"]["missingCapabilityAction"],
            f"If the context_packet or the read_ar_files schema of {TOOL_SERVER} lacks "
            "task_context with both task_document_ref and contract_path, stop and report the "
            "missing ar-task-scoped-readers/v1 capability. Do not call a task reader without "
            "task_context or substitute caller-selected roots.",
        )
        self.assertEqual(
            taskless["arMcpContext"]["missingCapabilityAction"],
            f"If the context_packet or the read_ar_files schema of {TOOL_SERVER} is unavailable, "
            "report that; do not invent a repository id or pass caller-selected roots.",
        )
        # "The installed AR" is the developer's other installation: no reader text points there.
        for handover in (leaf, taskless):
            told = json.dumps([handover["arMcpContext"], handover["host"]], ensure_ascii=False)
            self.assertNotIn("installed", told)
            self.assertNotIn("AR MCP tool", told)

    def test_the_handover_names_the_tool_server_the_two_tools_and_the_host(self) -> None:
        prompt, handover = self.compiled("architect", None)
        host = handover["host"]
        self.assertEqual((host["name"], host["arToolServer"]), ("Paseo", TOOL_SERVER))
        self.assertIn(
            f"Call every Agents Remember tool on the tool server named {TOOL_SERVER}",
            host["arMcpUsage"],
        )
        for said in (
            "and its tools are in this session.",
            # A lookup by the server's name as it is spelled here can find nothing, and one by a
            # tool's own name finds another installation's tool: the server's name, in either
            # spelling, is what to look for.
            "A harness shows them in its own way, so find them by the server's name in either "
            f"spelling, {TOOL_SERVER} or agents_remember_task.",
            "Either a tool is declared to you under a name that contains that spelling and ends "
            "in the tool's own name, for example mcp__agents_remember_task__server_info: call "
            "it.",
            "Or your harness's own instructions to you list a server or namespace in that "
            "spelling, for example mcp__agents_remember_task, and say how its tools are "
            "reached, for example through the harness's tool search or its script tool: reach "
            "them that way, and learn a tool's arguments there too.",
            # A tool that proxies other servers answers for those servers only.
            "A tool that lists, describes, connects or proxies tool servers holds only the "
            f"servers it was configured with. When it does not list {TOOL_SERVER} itself, do "
            "not use it for AR tools at all, not even to search for or describe a tool by its "
            "name: what it answers then comes from another installation, and its answer "
            '"server not found" speaks only for that tool.',
            # The developer's own installation, which every harness carries, by its name; and
            # any other AR tool server, whatever its name: not connected to, listed or called.
            f"Do not connect to, list, describe or call a tool server named {OTHER_INSTALLATION} "
            "or any of its tools, nor an AR tool server under any other name: it belongs to "
            "another installation, its tools carry the same tool names, and nothing found there "
            "serves this assignment.",
            f"look once more before reporting {TOOL_SERVER} missing, and report that instead of "
            "substituting another.",
            "add the requested files to read_ar_files as a list of objects such as "
            '{"path": "<path in the repository>", "source": "full"}.',
        ):
            with self.subTest(said=said):
                self.assertIn(said, host["arMcpUsage"])
        # The paragraph tells an agent where to look and names no harness and no harness's tool.
        self.assertNotRegex(host["arMcpUsage"], r"(?i)codemode|tool_search|\bmcp\(")
        # The first message says it in plain text before the capsule, which names AR tools, and
        # again as a field of the handover.
        self.assertEqual(prompt.count(host["arMcpUsage"]), 1)
        self.assertLess(
            prompt.index(f"AR tools, before your first tool call: {host['arMcpUsage']}"),
            prompt.index("AR owner assignment and canonical task handover:"),
        )
        self.assertEqual(
            {key: host["roleTools"][key] for key in ("toolServer", "start", "message")},
            {"toolServer": TOOL_SERVER, "start": "role_start", "message": "role_message"},
        )
        usage = host["roleTools"]["usage"]
        for said in (
            f"role_start on {TOOL_SERVER} starts one role agent",
            f"role_message on {TOOL_SERVER} sends one message to one role agent",
            "It never interrupts a running turn.",
            "repeat the same id to reconcile an uncertain start",
            "an agent created another way has no capsule and no binding",
            "A recipient that waits for a permission decision is refused as busy, because a "
            "message would answer the permission with a denial: the developer answers it in "
            "that agent's chat.",
            "A recipient whose start has not finished is refused as busy as well, and a role "
            "address never means the caller itself.",
            "the recipient's final text, or that the turn failed or was cancelled",
            "It can answer accepted without a text when the host cannot say which turn took "
            "the message, and the text of a message delivered during a turn can be the running "
            "turn's own; detail says which.",
            "Do not wait on an agent that may be waiting on you: two agents that wait on each "
            "other both stand still until one wait runs out.",
            # What a recipient does with a message: its chat reply is the answer.
            'A message another agent sent you begins with a line "From <role> · <task> · agent '
            '<agent id>": your reply in that turn, in your own chat, is what the sender '
            "receives, so answer the message there; send role_message to that agent id only "
            "for a message of your own.",
        ):
            with self.subTest(said=said):
                self.assertIn(said, usage)
        self.assertIn(
            "Put every question for the developer in your own chat", host["developerQuestions"]
        )
        self.assertIn(f"calling task_doc on {TOOL_SERVER}", handover["ownerHandover"])
        self.assertTrue(
            prompt.startswith("AR ROLE BRIEF: the dashboard launcher started this role")
        )
        self.assertEqual(handover["schema"], "ar-role-handover/v1")
        self.assertEqual(handover["agent"], "some-provider")

    def test_a_role_without_a_parent_is_told_it_needs_none_and_a_started_role_who_started_it(
        self,
    ) -> None:
        _prompt, manual = self.compiled("architect", None)
        self.assertEqual(manual["host"]["entryMode"], "dashboard-role-start")
        self.assertIsNone(manual["host"]["parent"])
        self.assertIn("It has no parent agent and needs none", manual["host"]["ownerRelation"])
        parent = StartingAgent("1f3c2f0e-6a57-4f0b-9d4e-0c8f1a2b3c4d", "architect", "Projects")
        prompt, started = self.compiled("orchestrator", parent)
        self.assertEqual(started["host"]["entryMode"], "agent-role-start")
        self.assertEqual(
            started["host"]["parent"],
            {"agentId": parent.agent_id, "role": "architect", "task": "Projects"},
        )
        self.assertIn(
            f"Agent {parent.agent_id} (architect · Projects) started this role and is its parent",
            started["host"]["ownerRelation"],
        )
        self.assertIn("role_message, addressed to its agent id", started["host"]["ownerRelation"])
        self.assertTrue(
            prompt.startswith(
                f"AR ROLE BRIEF: agent {parent.agent_id} (architect · Projects) started this role"
            )
        )


class RoleToolDescriptionWordingTests(unittest.TestCase):
    def test_the_two_tool_descriptions_name_no_forbidden_host_string(self) -> None:
        async def descriptions() -> dict[str, str]:
            server = FastMCP("role-tool-descriptions")
            register_role_agent_tools(server, None)  # type: ignore[arg-type]
            return {tool.name: tool.description or "" for tool in await server.list_tools()}

        described = asyncio.run(descriptions())
        self.assertEqual(sorted(described), ["role_message", "role_start"])
        for name, text in described.items():
            with self.subTest(tool=name):
                self.assertEqual(forbidden_in(text), [])
                self.assertIn("Only a role agent that AR launched can call this", text)
        self.assertIn("in Paseo", described["role_start"])
        told = {name: " ".join(text.split()) for name, text in described.items()}
        for name, sentence in (
            ("role_start", "Only the agent that started an execution repeats its request_id."),
            ("role_start", "Starts run one at a time."),
            (
                "role_message",
                "When the host cannot say which turn consumed the message, the call returns "
                "accepted without a text and detail says that the reply must be read later.",
            ),
            (
                "role_message",
                "A reply to a message delivered during a turn can be the running turn's text; "
                "detail says so then.",
            ),
            (
                "role_message",
                "A recipient that waits for a permission decision is refused as recipient-busy, "
                "because a message would answer the permission with a denial: the developer "
                "answers it in the recipient's chat, then send again.",
            ),
            (
                "role_message",
                "A recipient whose start has not finished is refused as recipient-busy as well.",
            ),
            ("role_message", "An address by role never means the caller itself."),
            ("role_start", "rejected (the launch is closed; no usable agent exists)"),
        ):
            with self.subTest(tool=name, sentence=sentence[:40]):
                self.assertIn(sentence, told[name])
        self.assertIn(
            '"From <role> · <task id or Projects> · agent <sender agent id>"',
            described["role_message"],
        )


if __name__ == "__main__":
    unittest.main()
