"""Role instruction wording: delegation, developer channels, and host tool names.

The surfaces are the ones a launched role agent reads: the router, the role file of each of the
seven launcher roles, every operation file that applies to one of them, the routing manifest, the
handover text built in code, and the descriptions of the two role tools. None of them may name
the previous host or its transport, every passage that tells an agent to call an AR tool names
the tool server ``agents-remember-task``, and the handover says that a server named
``agents-remember``, and an AR tool server under any other name, belongs to another installation.
"""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from agents_remember.application.role_capsules.compilation import (
    compile_admitted_capsule,
)
from agents_remember.application.skill_resources.capsule import (
    CapsuleSeatAddress,
    routed_admission_for,
)
from agents_remember.models.role_capsules.types import (
    CapsuleAdmittedFacts,
    CapsuleBinding,
    CapsuleRoleSeat,
    CapsuleToolPolicy,
    compute_content_digest,
)
from agents_remember.models.tools.public_roster import PUBLIC_TOOLS
from agents_remember_test_support.testing.capsule_wording import (
    assert_git_comparison_scope,
    clause_mutations,
    clause_surface,
    surface_clauses,
)
from agents_remember_test_support.testing.curation_doctrine import (
    RETIRED_CURATION_STATEMENTS,
    normalize_statement,
    retired_statement_findings,
)
from agents_remember_test_support.testing.leaf_instruction_wording import (
    LEAF_CLAUSES,
    ROLES,
    autonomy_clauses,
    forbidden_autonomy_clauses,
    missing_clauses,
)
from role_instruction_wording_support import (
    _NAMED_TOOL,
    CANONICAL,
    LAUNCHER_ROLES,
    LIFECYCLE,
    PACKAGED,
    REPOSITORY_ROOT,
    STARTERS,
    TOOL_SERVER,
    forbidden_in,
    instruction_files,
    read,
    role_text,
)


class InstructionFileWordingTests(unittest.TestCase):
    def test_investigator_scope_report_and_authority_are_explicit(self) -> None:
        investigator = role_text("investigator")
        for clause in (
            "Investigate one scoped concern of any kind",
            "without a task reference, on a sprint, or on a sprint and master",
            "the concern arrives in that parent's first `role_message` on `agents-remember-task`",
            "ask that parent once and wait; do not invent a concern",
            "Without a parent, use the developer's request",
            "Run checks in your scratch folder or in a sandbox built with the repository's sandbox tooling.",
            "Write one concise report at the path named in your handover.",
            "also tell it the report is written, naming its path",
            "Write the report before changing provider or system state.",
            "Remediate only after an explicit authorized order from the developer or from the parent agent named in the assignment.",
            "Edit no file in a code checkout or a leaf's enclosure.",
            "Make no commit and run no closeout or integration.",
            "Change nothing in the memory repository: no onboarding card, knowledge record or history row.",
            "Change no task document or task status.",
            "If a starter orders code or memory changes, decline",
            "An Investigator starts no role",
        ):
            with self.subTest(clause=clause):
                self.assertIn(clause, investigator)
        self.assertFalse((CANONICAL / LIFECYCLE / "roles/system-specialist.md").exists())
        manifest = json.loads(read(LIFECYCLE / "composition-manifest.json"))
        self.assertNotIn("system-specialist", manifest["roles"])
        self.assertEqual(manifest["roles"]["investigator"]["file"], "roles/investigator.md")
        self.assertEqual(
            manifest["roles"]["investigator"]["tools"],
            [
                "context_packet",
                "read_ar_files",
                "role_message",
                "knowledge_diff",
                "knowledge_read",
                "task_doc",
                "worktree_status",
            ],
        )
        router = read(LIFECYCLE / "SKILL.md")
        self.assertEqual(router.count("system-specialist"), 1)
        self.assertIn(
            "`system-specialist` is the earlier ID of the same Investigator role.",
            router,
        )
        orientation = read(LIFECYCLE / "operations/orientation.md")
        self.assertIn(
            "an Investigator uses its parent's first message for the concern",
            orientation,
        )

    def test_retired_investigator_wording_is_detected_when_reinserted(self) -> None:
        retired = [
            row for row in RETIRED_CURATION_STATEMENTS if row.reason.startswith("MIK-R98@v1")
        ]
        self.assertTrue(retired)
        for row in retired:
            for source in row.sources:
                with self.subTest(source=source, statement=row.statement):
                    shipped = (REPOSITORY_ROOT / source).read_text(encoding="utf-8")
                    relative = source.removeprefix("skills/")
                    self.assertEqual(
                        retired_statement_findings(normalize_statement(shipped), relative),
                        [],
                    )
                    self.assertTrue(
                        retired_statement_findings(
                            normalize_statement(shipped + "\n" + row.statement),
                            relative,
                        )
                    )

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
                "**Repository foundation is regular task work.** After setup reports memory/baseline state, the user starts the Architect, which opens the real foundation task, standalone or under a master/sprint.",
                "Never fabricate a leaf, worktree or enclosure;",
                "or the mounted `knowledge_write`, which runs the same writer with the same arguments.",
                "No other route writes knowledge from this seat.",
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

    def test_direct_closeout_keeps_the_architect_and_its_existing_gates(self) -> None:
        architect = role_text("architect")
        kept = "In flat mode, when no Manager or Orchestrator owns closeout, you may coordinate the existing paired closeout only under the already-delegated authority of the kept closeout procedure (`../operations/closeout.md` with its `../../w-02-light-task-workflow/workflows/closeout.md` workflow) and after independent review and required curation evidence exist."
        self.assertIn(kept, architect)
        self.assertIn(
            "Flat mode here is only the developer-chosen direct-coordination exception.",
            architect,
        )
        closeout = " ".join(read(LIFECYCLE / "operations/closeout.md").split())
        self.assertIn(
            "In a flat run with neither role, the owning Architect may coordinate the same transaction only when the already-delegated authority of the kept closeout workflow (`../../w-02-light-task-workflow/workflows/closeout.md`) permits it.",
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

    def test_leaf_roles_and_operations_deliver_their_own_positive_obligations(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            for source, clauses in LEAF_CLAUSES.items():
                for name, alternatives in surface_clauses(source, clauses).items():
                    with self.subTest(source=source, obligation=name):
                        text = clause_surface(
                            CANONICAL / LIFECYCLE, source, name, Path(directory) / "report.md"
                        )
                        self.assertEqual(missing_clauses(text, {name: alternatives}), [])

    def test_removing_each_affirmative_clause_is_detected_on_its_surface(self) -> None:
        for source, name, clauses, reading, mutant in clause_mutations(
            CANONICAL / LIFECYCLE, ROLES
        ):
            with self.subTest(source=source, obligation=name):
                self.assertEqual(missing_clauses(reading, {name: clauses[name]}), [], source)
                self.assertNotEqual(mutant, reading, "control must remove real instructions")
                self.assertIn(name, missing_clauses(mutant, clauses))

    def test_equivalent_preserved_identity_and_no_role_boundaries_are_allowed(
        self,
    ) -> None:
        for role in ROLES:
            reading = role_text(role).replace("starts no role", "does not start a role")
            with self.subTest(role=role):
                self.assertEqual(missing_clauses(reading, autonomy_clauses(role)), [])
        worker = role_text("worker").replace(
            "Use an agent ID only whole and unchanged as the product supplied it",
            "Use only the whole, unchanged agent ID supplied by the product",
        )
        self.assertEqual(missing_clauses(worker, LEAF_CLAUSES["roles/worker.md"]), [])

    def test_the_surfaces_are_the_router_seven_roles_and_their_eight_operations(
        self,
    ) -> None:
        names = [path.relative_to(LIFECYCLE).as_posix() for path in instruction_files()]
        self.assertEqual(names[0], "SKILL.md")
        roles_end = 1 + len(LAUNCHER_ROLES)
        self.assertEqual(names[1:roles_end], [f"roles/{role}.md" for role in LAUNCHER_ROLES])
        self.assertEqual(
            names[roles_end:],
            [
                f"operations/{name}.md"
                for name in (
                    # MIK-R196: the free agent's operation is reachable from a launch now.
                    "bootstrap",
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

    def test_no_forbidden_host_string_is_left_and_the_packaged_copy_is_the_same_text(
        self,
    ) -> None:
        for relative in (*instruction_files(), LIFECYCLE / "composition-manifest.json"):
            with self.subTest(file=relative.as_posix()):
                self.assertEqual(forbidden_in(read(relative)), [])
                self.assertEqual(
                    (PACKAGED / relative).read_bytes(),
                    (CANONICAL / relative).read_bytes(),
                    "run python3 scripts/sync-skills.py",
                )

    def test_every_passage_that_names_an_ar_tool_names_the_task_tool_server(
        self,
    ) -> None:
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

    def test_the_router_names_the_host_the_two_tools_and_the_only_tool_server(
        self,
    ) -> None:
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
            "<agent ID>`: your reply in that turn reaches the sender only when it waits on "
            "its own call; otherwise send it with `role_message` on `agents-remember-task`.",
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
            "turn reaches the sender only when it waits on its own call; otherwise send it "
            "with `role_message` on `agents-remember-task`.",
        ):
            with self.subTest(sentence=sentence):
                self.assertIn(sentence, orientation)

    def test_each_role_is_told_how_to_reach_agents_and_the_developer(self) -> None:
        orientation = " ".join(read(LIFECYCLE / "operations" / "orientation.md").split())
        pointer = "Developer questions and relayed answers follow `operations/orientation.md`"
        for role in LAUNCHER_ROLES:
            with self.subTest(role=role):
                text = role_text(role)
                if pointer in text:
                    # One-home referral: the tokens live in the referenced home.
                    self.assertIn("`role_message`", orientation)
                    self.assertIn("in your own chat", orientation)
                else:
                    self.assertIn("`role_message`", text)
                    self.assertIn("in your own chat", text)
                if role in STARTERS:
                    self.assertIn("`role_start`", text)
                else:
                    self.assertNotIn("`role_start`", text)
                    self.assertRegex(
                        text,
                        r"(?:A (?:Worker|Reviewer|Curator)|An Investigator) starts no role"
                        r"|Bootstrap starts no role",
                    )
        for role in set(LAUNCHER_ROLES) - {"architect"}:
            with self.subTest(role=role, told="a role without a parent needs none"):
                text = role_text(role)
                original = (
                    r"started from the dashboard,? (?:it )?"
                    r"(has no parent|has none|needs no parent|have no parent|need none)"
                )
                if role == "reviewer":
                    self.assertTrue(
                        re.search(original, text)
                        or "A dashboard-started Reviewer has and needs no parent." in text
                    )
                else:
                    self.assertRegex(text, original)
        coordination = " ".join(read(LIFECYCLE / "operations" / "coordination.md").split())
        for sentence in (
            "an Architect every role except Architect",
            "an Orchestrator Manager, Worker, Reviewer, Curator, and Investigator under its own sprint",
            "a Manager Worker, Reviewer, Curator, and Investigator under its own master",
            "call it again with the same request ID",
            "takes the message up without its turn being cancelled",
            "A role started from the dashboard works directly with the developer and needs no parent.",
            # What the message tool answers, as its own description says it.
            "A role address never means you yourself.",
            "when the host cannot hand a message to the running turn, the message is held, and "
            "while anything is held for a recipient a further message is held behind it.",
            "A message to a recipient that waits for a permission decision, or whose start has "
            "not finished, is held the same way: the answer says `held` and why, and the product "
            "delivers what it holds in the order it took it once the recipient can take it.",
            "The host is never sent a message for a recipient that waits at a permission prompt, "
            "because it would answer the permission with a denial; only the developer answers "
            "the prompt, in that agent's chat.",
            "or it returns a pending permission, also for a wait on a held message, or a timeout "
            "with the message still delivered.",
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
            "A message to a recipient that waits for a permission decision, or whose start has "
            "not finished, is held by the product and delivered when the recipient can take it",
            "its final text, or that the turn failed or was cancelled, a pending permission, or "
            "a timeout; `accepted` without a text means that the reply is to be read later.",
            "Do not wait on an agent that may be waiting on you.",
        ):
            with self.subTest(role="architect", sentence=sentence[:40]):
                self.assertIn(sentence, architect)

    def test_a_clean_sync_keeps_the_review_and_a_repair_stays_visible(self) -> None:
        """MIK-R170 rules 9, 11, 12 and 13 reach the worker, manager and reviewer capsules."""

        manifest_bytes = (CANONICAL / LIFECYCLE / "composition-manifest.json").read_bytes()
        manifest = json.loads(manifest_bytes)

        def capsule(role: str, operation: str) -> str:
            binding = CapsuleBinding(
                operation=operation,  # type: ignore[arg-type]
                admitted=CapsuleAdmittedFacts(
                    task_reference="agents-remember/tasks/demo/task.json",
                    task_document_digest=compute_content_digest(b'{"id": "demo"}'),
                    seat=CapsuleRoleSeat(role=role, altitude=manifest["roles"][role]["altitude"]),  # type: ignore[arg-type]
                    repository_id="agents-remember",
                    work_branch="fixture",
                    tool_policy=CapsuleToolPolicy(granted=frozenset(PUBLIC_TOOLS)),
                ),
            )
            outcome = compile_admitted_capsule(
                binding,
                routed_admission_for(
                    CANONICAL / LIFECYCLE,
                    "composition-manifest.json",
                    manifest_bytes,
                    CapsuleSeatAddress(role=role, operation=operation),  # type: ignore[arg-type]
                ),
            )
            self.assertTrue(outcome.ok, outcome.render_explanation())
            assert outcome.result is not None
            return " ".join(outcome.result.render_instructions().split())

        required = {
            ("worker", "implementation"): [
                # Rule 9: a clean sync keeps the review and opens no round.
                "When no path was changed on both sides, your focused tests run once more",
                "no new freeze goes to the Reviewer and no round is begun",
                # Rule 12: the repair goes ahead of any plan or approval.
                "no plan, account, census or approval goes ahead of the repair",
                "the Manager gives no go-ahead for it",
                # Rule 13: the repair is visible in the freeze.
                "The freeze report holds one entry per repaired finding",
                "the test that fails before and passes after",
            ],
            ("manager", "coordination"): [
                # Rule 11: a precedent is settled where it falls.
                "A matter that an earlier ruling of the same kind settles, you settle by that "
                "ruling and name it in your record; only a question that no ruling covers goes up",
                # Rule 9: the Manager decides from the closeout preview, not a new round.
                "no new freeze goes to the Reviewer and no round is begun",
                # Rule 12: the Manager gives no go-ahead for a repair.
                "needs no plan, account or go-ahead from you",
            ],
            ("reviewer", "review"): [
                # Rule 13: each finding is checked against its entry, with no round counted.
                "check every finding against its entry in the Worker's freeze report",
                "with no round counted",
                # Rule 9: a cleanly synced leaf is not sent back for a new freeze.
                "A cleanly synced leaf sends no new freeze to you and opens no round",
            ],
        }
        for (role, operation), clauses in required.items():
            text = capsule(role, operation)
            for clause in clauses:
                with self.subTest(role=role, clause=clause):
                    self.assertIn(clause, text)
            with self.subTest(role=role, told="no cleanly synced freeze is re-sent"):
                self.assertNotIn("send the new freeze directly to the Reviewer", text)
                self.assertNotIn("hand the new freeze to the Reviewer", text)
        brief = " ".join(read(LIFECYCLE / "templates" / "worker-brief.md").split())
        self.assertIn("no new freeze goes to the Reviewer and no round is begun", brief)
        self.assertNotIn("hand the new freeze to the Reviewer", brief)

    def test_the_capsules_send_a_return_and_never_point_at_the_agent_chat(self) -> None:
        """MIK-R172 rule 1: what is handed over, and awaited, travels by `role_message`.

        The four stage-owning capsules carry the sending rule and the statement that a
        file hands itself over to nobody; no capsule of a launcher role still names the
        agent's own chat as the place of an answer or a report.
        """

        manifest_bytes = (CANONICAL / LIFECYCLE / "composition-manifest.json").read_bytes()
        manifest = json.loads(manifest_bytes)

        def capsule(role: str, operation: str) -> str:
            binding = CapsuleBinding(
                operation=operation,  # type: ignore[arg-type]
                admitted=CapsuleAdmittedFacts(
                    task_reference="agents-remember/tasks/demo/task.json",
                    task_document_digest=compute_content_digest(b'{"id": "demo"}'),
                    seat=CapsuleRoleSeat(role=role, altitude=manifest["roles"][role]["altitude"]),  # type: ignore[arg-type]
                    repository_id="agents-remember",
                    work_branch="fixture",
                    tool_policy=CapsuleToolPolicy(granted=frozenset(PUBLIC_TOOLS)),
                ),
            )
            outcome = compile_admitted_capsule(
                binding,
                routed_admission_for(
                    CANONICAL / LIFECYCLE,
                    "composition-manifest.json",
                    manifest_bytes,
                    CapsuleSeatAddress(role=role, operation=operation),  # type: ignore[arg-type]
                ),
            )
            self.assertTrue(outcome.ok, outcome.render_explanation())
            assert outcome.result is not None
            return " ".join(outcome.result.render_instructions().split())

        stage_sentence = {
            ("worker", "implementation"): (
                "sends what it waits for, and from whom, to its parent with `role_message`"
            ),
            ("manager", "coordination"): (
                "sends what it waits for, and from whom, to its parent with `role_message`"
            ),
            (
                "reviewer",
                "review",
            ): "send what and whom you await to your parent with `role_message`",
            (
                "curator",
                "curation",
            ): "send what you await and from whom to your parent with `role_message`",
        }
        banned = (
            "in your own chat, is what the sender receives",
            "so answer the message there",
            "answer the message there",
            "end your turn with the awaited thing",
            "state what and whom you await in your last line",
            "says in its last line what it waits for",
            "needs no second model-authored completion post",
        )
        # MIK-R172 rule 1 in the doctrine home: the artifact does not hand itself over,
        # and the relay's line carries the agent's last line.
        acceptance = " ".join(
            (CANONICAL / LIFECYCLE / "core" / "acceptance.md").read_text(encoding="utf-8").split()
        )
        self.assertIn("A file on record wakes no one", acceptance)
        self.assertIn("a file hands itself over to nobody", acceptance)
        self.assertIn(
            "one line to the agent that waits, carrying the ended agent's last line", acceptance
        )
        self.assertNotIn("needs no second model-authored completion post", acceptance)
        for (role, operation), clause in stage_sentence.items():
            text = capsule(role, operation)
            with self.subTest(role=role, told="an unfinished turn sends what it waits for"):
                self.assertIn(clause, text)
                self.assertIn("agents-remember-task", text)
            with self.subTest(role=role, told="a chat reply reaches a sender only on its wait"):
                self.assertIn(
                    "your reply in that turn reaches the sender only when it waits on its own call",
                    capsule(role, "orientation"),
                )
        for role in ("worker", "reviewer", "curator", "manager"):
            for operation in (
                "orientation",
                "implementation",
                "review",
                "curation",
                "coordination",
            ):
                if role not in manifest["operations"].get(operation, {}).get(
                    "applies_to_roles", []
                ):
                    continue
                text = capsule(role, operation)
                for phrase in banned:
                    with self.subTest(role=role, operation=operation, phrase=phrase):
                        self.assertNotIn(phrase, text)

    def test_the_workers_report_names_its_agent_id(self) -> None:
        # Where the report of the previous host named the execution, it names the agent.
        implementation = " ".join(read(LIFECYCLE / "operations" / "implementation.md").split())
        self.assertIn(
            "exact checks and results, your agent ID as the `senderLine` of `role_message` on "
            '`agents-remember-task` names it (write "agent ID not known" when you sent no '
            "message), and open limitations.",
            implementation,
        )


if __name__ == "__main__":
    unittest.main()
