"""Role instruction wording: delegation, developer channels, and host tool names.

The surfaces are the ones a launched role agent reads: the router, the role file of each of the
eight launcher roles, every operation file that applies to one of them, the routing manifest, the
handover text built in code, and the descriptions of the two role tools. None of them may name
the previous host or its transport, every passage that tells an agent to call an AR tool names
the tool server ``agents-remember-task``, and the handover says that a server named
``agents-remember``, and an AR tool server under any other name, belongs to another installation.
"""

from __future__ import annotations

import json
import re
import shutil
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
from agents_remember.cli.role_launch_preparation import _AR_TOOL_SERVER_USAGE_TAIL
from agents_remember.errors import CapsuleManifestError
from agents_remember.models.role_capsules.types import (
    CapsuleAdmittedFacts,
    CapsuleBinding,
    CapsuleRoleSeat,
    CapsuleToolPolicy,
    compute_content_digest,
)
from agents_remember.models.tools.public_roster import PUBLIC_TOOLS
from agents_remember_test_support.testing.capsule_wording import (
    MESSAGE_OPERATION_ROUTES,
    assert_git_comparison_scope,
    autonomy_surface,
    clause_mutations,
    clause_surface,
    surface_clauses,
)
from agents_remember_test_support.testing.coordination_wording import CURRENT_COORDINATION_CLAUSES
from agents_remember_test_support.testing.curation_doctrine import (
    RETIRED_CURATION_STATEMENTS,
    normalize_statement,
    retired_statement_findings,
)
from agents_remember_test_support.testing.instruction_renewal import RENEW_COMMAND
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
    COORDINATION_ACTION_STEPS,
    LAUNCHER_ROLES,
    LIFECYCLE,
    PACKAGED,
    RELOCATED_WORKING_STEPS,
    REPOSITORY_ROOT,
    STARTERS,
    TOOL_SERVER,
    assert_detail_link_provenance_controls,
    assert_memory_review_owner_link_controls,
    assert_message_handover_delivery,
    assert_nonstarter_role_boundary,
    forbidden_in,
    instruction_files,
    read,
    role_text,
)


def _required_step(text: str, heading: str, clauses: tuple[str, ...]) -> None:
    """Read the named working step, never accept a matching clause elsewhere."""
    match = re.search(r"(?ms)^## " + re.escape(heading) + r"\n(.*?)(?=^## |\Z)", text)
    assert match is not None, heading
    step = " ".join(match[1].split())
    for clause in clauses:
        assert clause in step, (heading, clause)


def _step_controls(source: str, heading: str, clauses: tuple[str, ...]) -> None:
    text = read(LIFECYCLE / source)
    _required_step(text, heading, clauses)
    for clause in clauses:
        # Delete an actual duty, rather than pass a fabricated empty input to the checker.
        normalized = " ".join(text.split())
        assert clause in normalized, (source, clause)
        mutant = re.sub(re.escape(clause).replace(r"\ ", r"\s+"), "", text)
        assert mutant != text, (source, clause)
        with unittest.TestCase().assertRaises(AssertionError):
            _required_step(mutant, heading, clauses)
    # A duty at another step is still missing where it is acted on.
    moved = text.replace("## " + heading + "\n", "## Wrong working step\n")
    with unittest.TestCase().assertRaises(AssertionError):
        _required_step(moved, heading, clauses)


class InstructionFileWordingTests(unittest.TestCase):
    def test_inherited_construction_cost_and_continuation_keep_their_bounds(self) -> None:
        checks = (
            (
                "roles/manager.md",
                "Start leaf agents and preserve their work",
                (
                    "Check packet deps before starting/integrating "
                    "(the product does not perform that check);",
                ),
            ),
            (
                "operations/closeout.md",
                "Repair a gate refusal through its owner",
                (
                    "Send `knowledge-gate-refused` (closeout/integration) or "
                    "`direct-landing-knowledge-gate-refused` (direct landing) findings to Curator "
                    "for repair, then retry the owning route;",
                    "bypass no gate.",
                ),
            ),
            (
                "roles/manager.md",
                "Make decisions within your authority",
                (
                    "scratch tools",
                    "1 infra rerun on same operands",
                    "2nd same-kind infra failure",
                ),
            ),
            (
                "roles/manager.md",
                "Make decisions within your authority",
                ("small item costing ~1h or 2nd repair round/freeze (hours + proposal)",),
            ),
            (
                "roles/orchestrator.md",
                "Communicate with the Architect",
                ("small item costing ~1 hour or 2nd repair round/freeze (with hours & proposal)",),
            ),
            (
                "reference/worker/sync-and-repairs.md",
                "Repair limits, costly small repairs, and record-only repairs",
                (
                    "When the same check or finding still fails after three of the Worker's repairs, stop repairing.",
                    "When one small item costs about an hour, a second repair round or a second freeze, tell the Manager immediately with the hours and a proposal",
                ),
            ),
            (
                "core/authority.md",
                "Hand over work and wait for messages",
                (
                    "Collect helpers and continue ordinary work.",
                    "end a turn that waits for your helpers with `WAITING FOR SUBAGENTS:`",
                    "a turn that waits for a process you started with `WAITING FOR BACKGROUND:`",
                ),
            ),
            (
                "core/every-role.md",
                "Parallel work and waiting",
                (
                    "End waits with `WAITING FOR SUBAGENTS:` or `WAITING FOR BACKGROUND:` (process, id/log, expected end), sending no parent progress `role_message`.",
                    "Continue ended child turns with unfinished work and no valid wait/stop.",
                ),
            ),
        )
        for source, heading, clauses in (*checks, *RELOCATED_WORKING_STEPS):
            with self.subTest(source=source, heading=heading):
                _step_controls(source, heading, clauses)
        self.assertIn(
            "stop after 3 failed repairs on one item and tell Manager",
            role_text("worker"),
        )
        self.assertIn(
            "Pass `system/coding-guidelines.md` handover checks on a clean freeze copy",
            " ".join(read(LIFECYCLE / "operations/implementation.md").split()),
        )
        for role in ("worker", "reviewer", "curator", "investigator"):
            self.assertNotIn("Hermes: their files", role_text(role))

        for role in ("architect", "manager", "orchestrator"):
            _step_controls(
                f"roles/{role}.md",
                "Reference index",
                ("[child]({{detail:reference/coordination/shared/child-questions.md}})",),
            )
        _step_controls(
            "reference/coordination/shared/child-questions.md",
            "Answer within your authority or relay the question",
            (
                "first check whether the decision falls within your authority",
                "If it does, answer and identify the answer as your own",
                "pass the question unchanged to your parent",
                "originating role, task and agent identifier",
                "add your recommendation separately",
                "Without a parent, ask the developer in your own chat",
                "task number, what the task does, agent identifier",
            ),
        )
        _step_controls(
            "reference/coordination/shared/child-questions.md",
            "Return the developer's words",
            (
                "Send the developer's answer to the agent that sent the question",
                "Before composing that return, read [Recognize a relayed developer answer](../../../operations/orientation.md#recognize-a-relayed-developer-answer) for the three required attribution items",
                "passes those three items unchanged",
                "Never present your own answer as the developer's answer or approval",
            ),
        )
        _step_controls(
            "roles/orchestrator.md",
            "Verify, boundaries, and result contract",
            (
                "Plan-review and super-exit have no startable Reviewer seat -> bring to Architect for developer with sprint-level Reviewer idea.",
            ),
        )

    def test_freeze_report_and_receiver_keep_candidate_and_check_identity(self) -> None:
        checks = (
            (
                "reference/worker/sync-and-repairs.md",
                "Focused test selection and freeze report details",
                (
                    "byte-identical carry-overs",
                    "rerun causes",
                    "per-finding repair entries (ID, files/lines, before/after test)",
                    "../../templates/turn-report.md",
                ),
            ),
            (
                "roles/worker.md",
                "Reference index",
                ("[report]({{detail:templates/turn-report.md}}#shape)",),
            ),
            (
                "reference/review/non-leaf-seams-and-catalogs.md",
                "Review seams and criteria catalogs",
                (
                    "never the hosting leaf's enclosure candidate",
                    "| atomic-master integration review | the canonical master | master's acceptance leaf |",
                    "| master-exit | the master | master's acceptance leaf |",
                    "| portfolio plan review | the sprint | none |",
                    "| super-exit | the sprint | none |",
                ),
            ),
        )
        for source, heading, clauses in checks:
            with self.subTest(source=source):
                _step_controls(source, heading, clauses)
        implementation = " ".join(read(LIFECYCLE / "operations/implementation.md").split())
        for clause in (
            "Pass `system/coding-guidelines.md` handover checks on a clean freeze copy (record check/tree/result; fix first).",
            "Regenerate Curator list per freeze + `handoff-check` (`server_info`).",
        ):
            self.assertIn(clause, implementation)
        worker = read(LIFECYCLE / "roles/worker.md")
        self.assertIn(
            "<!-- ar:gives freeze-handoff to reviewer: freeze-report, candidate-identity, attempt-records -->",
            worker,
        )
        reviewer = role_text("reviewer")
        self.assertIn(
            'exact candidate (no branch/"latest") + clean-copy checks (withhold PASS until passing)',
            reviewer,
        )
        turn_report = " ".join(read(LIFECYCLE / "templates/turn-report.md").split())
        self.assertIn("Explicitly list any relevant test or check not run and why.", turn_report)

    def test_design_guidelines_are_obligations_at_each_ui_input_step(self) -> None:
        _step_controls(
            "core/invariants.md",
            "Keep each instruction on its owning surface",
            ("Design rules belong in the repository's `system/design-guidelines.md`.",),
        )
        worker = read(LIFECYCLE / "roles/worker.md")
        self.assertIn(
            "Follow `workspace.memoryRoot` `system/{coding,design,skill-writing}-guidelines.md`",
            worker,
        )
        self.assertLess(
            worker.index("system/{coding,design,skill-writing}-guidelines.md"),
            worker.index("<!-- ar:gives freeze-handoff"),
        )
        reviewer = read(LIFECYCLE / "roles/reviewer.md")
        self.assertIn("`system/tools.md` + `system/design-guidelines.md` + guidelines.", reviewer)
        self.assertLess(
            reviewer.index("`system/tools.md` + `system/design-guidelines.md` + guidelines."),
            reviewer.index("## Bind the seam and catalogs"),
        )
        for role in ("worker", "reviewer"):
            for copied_method in ("DESIGN.md", "TREATMENT.md", "TECHNIQUES.md"):
                self.assertNotIn(copied_method, role_text(role))

    def test_investigator_scope_report_and_authority_are_explicit(self) -> None:
        investigator = role_text("investigator")
        for clause in (
            "Examine one scoped concern and write an evidence-based report.",
            "Start in Projects (taskless), sprint, standalone master, or both—never a leaf",
            "Parent's first `role_message` supplies concern (ask once if missing, wait); parentless asks developer in own chat.",
            "Run checks in scratch/repo sandbox;",
            "write one report at handover path",
            "send path to parent's role with its task references via `role_message` (parentless: own chat).",
            "Write the report before authorized system remediation.",
            "Change system state only on explicit developer/parent order outside code checkouts, enclosures, memory repos and task docs.",
            "Edit no code/leaf/memory/task file; no commit/closeout/integration/role start.",
            "Decline edit orders, cite boundaries, report to owner.",
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
                "server_info",
                "context_packet",
                "read_ar_files",
                "memory_quality_check",
                "worktree_status",
                "task_doc",
                "role_message",
                "knowledge_read",
                "knowledge_diff",
                "knowledge_integrity_check",
            ],
        )
        router = read(LIFECYCLE / "SKILL.md")
        self.assertNotIn("system-specialist", router)
        self.assertIn(
            "An Investigator takes any scoped concern from its parent's first message",
            router,
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
        for source, clauses in CURRENT_COORDINATION_CLAUSES.items():
            reading = " ".join(read(LIFECYCLE / source).split())
            for clause in clauses:
                with self.subTest(source=source, clause=clause):
                    self.assertIn(" ".join(clause.split()), reading)
        for source, heading, clauses in COORDINATION_ACTION_STEPS:
            with self.subTest(source=source, heading=heading):
                _step_controls(source, heading, clauses)
        assert_memory_review_owner_link_controls()
        assert_git_comparison_scope(CANONICAL / LIFECYCLE)

    def test_direct_closeout_keeps_the_architect_and_its_existing_gates(self) -> None:
        architect = role_text("architect")
        kept = (
            "Perform no landing step except flat-mode direct paired closeout "
            "(`worktree_closeout_preview`, `worktree_closeout_apply`) under "
            "developer-chosen direct coordination with independent review and "
            "curation evidence."
        )
        self.assertIn(kept, architect)
        self.assertIn(
            "[flat]({{detail:reference/coordination/shared/closeout-recovery.md}}#flat-mode-direct-paired-closeout)",
            architect,
        )
        flat_detail = " ".join(
            read(LIFECYCLE / "reference/coordination/shared/closeout-recovery.md").split()
        )
        self.assertIn(
            "Flat mode means only the developer-chosen direct-coordination exception where neither a Manager nor an Orchestrator owns closeout.",
            flat_detail,
        )
        self.assertIn(
            "the owning Architect may coordinate the paired closeout (`worktree_closeout_preview` and `worktree_closeout_apply`) only with the developer's explicit commit approval for that closeout, or with authority the developer delegated for the series and recorded in the task document, after independent review and required curation evidence exist.",
            flat_detail,
        )
        closeout = " ".join(read(LIFECYCLE / "operations/closeout.md").split())
        self.assertIn(
            "flat-mode Architect direct closeout follows [flat-mode closeout](../reference/coordination/shared/closeout-recovery.md#flat-mode-direct-paired-closeout).",
            closeout,
        )
        self.assertIn(
            "clean preview returns `would-closeout`, `commit-approval-pending`, `request_commit_approval`;",
            closeout,
        )
        self.assertIn(
            "pass standing leaf authority as `intent_note` to `worktree_closeout_apply` with previewed leg messages.",
            closeout,
        )
        self.assertNotIn(kept, [row.statement for row in RETIRED_CURATION_STATEMENTS])

    def test_every_role_organises_its_harness_work_with_seat_boundaries(self) -> None:
        self.assertEqual(
            sorted(path.stem for path in (CANONICAL / LIFECYCLE / "roles").glob("*.md")),
            sorted(ROLES),
        )
        self.assertEqual(set(LAUNCHER_ROLES), set(ROLES))
        for role in ROLES:
            reading = autonomy_surface(CANONICAL / LIFECYCLE, role)
            with self.subTest(role=role):
                self.assertEqual(missing_clauses(reading, autonomy_clauses(role)), [])
                for surface in (reading, role_text(role)):
                    self.assertEqual(forbidden_autonomy_clauses(role, surface), [])
                    self.assertNotIn("one level deep", surface)
                    self.assertNotIn("must fan out", surface)
                    self.assertNotIn("Sub-agents for read/search only", surface)
        self.assertIn("No role starts", role_text("worker"))
        self.assertIn("One Curator writer lane (no helper writers)", role_text("curator"))

    def test_leaf_roles_and_operations_deliver_their_own_positive_obligations(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            for source, clauses in LEAF_CLAUSES.items():
                for name, alternatives in surface_clauses(source, clauses).items():
                    with self.subTest(source=source, obligation=name):
                        text = clause_surface(
                            CANONICAL / LIFECYCLE,
                            source,
                            name,
                            Path(directory) / source / name / "report.md",
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

        self._assert_message_handover_route_controls()

    def _assert_message_handover_route_controls(self) -> None:
        # Resolve both operation routes and role Messages bindings; a detail file on
        # disk or a clause at an unrelated role step cannot repair a missing route.
        detail_source = "reference/message-handover.md"
        detail_pointer = "{{detail:" + detail_source + "}}"
        for role, operation in (
            ("worker", "implementation"),
            ("reviewer", "review"),
            ("curator", "curation"),
        ):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "corpus"
                shutil.copytree(CANONICAL / LIFECYCLE, root)
                report = Path(directory) / "report.md"
                role_path = root / f"roles/{role}.md"
                operation_path = root / f"operations/{operation}.md"
                detail_path = root / detail_source
                manifest_path = root / "composition-manifest.json"
                original_role = role_path.read_text()
                trigger = "[msg]("
                self.assertIn(trigger, original_role)
                original_operation = operation_path.read_text()
                selected_operation_route = MESSAGE_OPERATION_ROUTES[role]
                original_detail = detail_path.read_text()
                original_manifest = manifest_path.read_text()
                for source in (f"roles/{role}.md", f"operations/{operation}.md"):
                    for control, mutated_role in (
                        ("role Messages pointer absent", original_role.replace(detail_pointer, "")),
                        (
                            "role Messages trigger absent",
                            original_role.replace(
                                trigger,
                                "",
                            ),
                        ),
                        (
                            "pointer at wrong role step",
                            original_role.replace(detail_pointer, "")
                            + "\n## Unrelated step\n"
                            + detail_pointer
                            + "\n",
                        ),
                    ):
                        with self.subTest(source=source, control=control):
                            self.assertNotEqual(mutated_role, original_role)
                            role_path.write_text(mutated_role)
                            with self.assertRaises(AssertionError):
                                clause_surface(root, source, "busy retry", report)
                            role_path.write_text(original_role)
                    for control in ("required detail undeclared", "empty delivered detail step"):
                        with self.subTest(source=source, control=control):
                            manifest = json.loads(original_manifest)
                            if control == "required detail undeclared":
                                del manifest["references"]["message-handover"]
                            else:
                                manifest["derived"]["references"]["message-handover"]["step"] = ""
                            manifest_path.write_text(json.dumps(manifest))
                            expected_error = (
                                CapsuleManifestError
                                if control == "empty delivered detail step"
                                else AssertionError
                            )
                            with self.assertRaises(expected_error):
                                clause_surface(root, source, "busy retry", report)
                            manifest_path.write_text(original_manifest)
                    with self.subTest(source=source, control="actual busy-retry duty absent"):
                        alternatives = LEAF_CLAUSES[source]["busy retry"]
                        changed_detail = original_detail
                        for clause in alternatives:
                            changed_detail = changed_detail.replace(clause, "")
                        self.assertNotEqual(changed_detail, original_detail)
                        detail_path.write_text(changed_detail)
                        delivered = clause_surface(root, source, "busy retry", report)
                        self.assertIn(
                            "busy retry", missing_clauses(delivered, {"busy retry": alternatives})
                        )
                        detail_path.write_text(original_detail)
                with self.subTest(source=f"operations/{operation}.md", control="step route absent"):
                    self.assertIn(selected_operation_route, original_operation)
                    operation_path.write_text(
                        original_operation.replace(selected_operation_route, "")
                    )
                    with self.assertRaises(AssertionError):
                        clause_surface(root, f"operations/{operation}.md", "busy retry", report)

    def test_busy_retry_keeps_each_refusal_duty_on_the_delivered_surface(self) -> None:
        assert_detail_link_provenance_controls()
        facets = (
            "Full-store recipient-busy is a refusal",
            "Record the handover as pending",
            "independent work",
            "Resend once",
            "before the turn ends",
            "report the second refusal",
            "leave it pending",
        )
        with tempfile.TemporaryDirectory() as directory:
            for source, clauses in LEAF_CLAUSES.items():
                transport = {"busy retry": clauses["busy retry"]}
                reading = clause_surface(
                    CANONICAL / LIFECYCLE,
                    source,
                    "busy retry",
                    Path(directory) / source / "report.md",
                )
                self.assertEqual(missing_clauses(reading, transport), [], source)
                for facet in facets:
                    with self.subTest(source=source, deleted_facet=facet):
                        self.assertIn(facet, reading)
                        mutant = reading.replace(facet, "")
                        self.assertNotEqual(mutant, reading)
                        self.assertIn("busy retry", missing_clauses(mutant, transport))

    def test_equivalent_preserved_identity_and_no_role_boundaries_are_allowed(
        self,
    ) -> None:
        for role in ROLES:
            original_reading = autonomy_surface(CANONICAL / LIFECYCLE, role)
            reading = original_reading.replace("starts no role", "does not start a role").replace(
                "Start no role.", "Do not start a role."
            )
            with self.subTest(role=role):
                self.assertEqual(missing_clauses(reading, autonomy_clauses(role)), [])
        # BA3: equivalence changes wording, not the actual source/step of an obligation.
        source = "roles/worker.md"
        with tempfile.TemporaryDirectory() as directory:
            for name, alternatives in surface_clauses(source, LEAF_CLAUSES[source]).items():
                original_worker = clause_surface(
                    CANONICAL / LIFECYCLE, source, name, Path(directory) / "report.md"
                )
                worker = original_worker.replace(
                    "Use the agent IDs these tools return",
                    "Use only the agent IDs these tools return",
                )
                with self.subTest(obligation=name):
                    if "Use the agent IDs these tools return" in original_worker:
                        self.assertNotEqual(worker, original_worker)
                    self.assertEqual(missing_clauses(worker, {name: alternatives}), [])

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
                    "consolidation",
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
                    f"run {RENEW_COMMAND}",
                )

    def test_every_passage_that_names_an_ar_tool_names_the_task_tool_server(
        self,
    ) -> None:
        wrong_server = re.compile(r"\b(?:on|via)\s+`agents-remember`(?!-task)")
        naming = 0
        for relative in instruction_files():
            for passage in re.split(r"\n\s*\n", read(relative)):
                tools = sorted(set(_NAMED_TOOL.findall(passage)))
                if not tools:
                    continue
                naming += 1
                with self.subTest(file=relative.as_posix(), tools=tools):
                    self.assertIsNone(wrong_server.search(passage))
        # The rule is checked on real passages, not satisfied by finding none.
        self.assertGreaterEqual(naming, 20)
        self.assertIsNotNone(wrong_server.search("Call `role_message` on `agents-remember`."))
        for source, expected in (
            (
                LIFECYCLE / "core/every-role.md",
                f"Call AR tools only on `{TOOL_SERVER}`, never `agents-remember`.",
            ),
            (
                LIFECYCLE / "operations/orientation.md",
                f"Call Agents Remember (AR) tools only via `{TOOL_SERVER}` (`agents_remember_task`); ignore unlisted proxies, never call `agents-remember`.",
            ),
            (
                LIFECYCLE / "SKILL.md",
                f"Call every AR tool on the tool server named `{TOOL_SERVER}`",
            ),
        ):
            with self.subTest(source=source.as_posix()):
                self.assertIn(expected, " ".join(read(source).split()))

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
            "dashboard roles need no parent.",
            "Call Agents Remember (AR) tools only via `agents-remember-task` (`agents_remember_task`); ignore unlisted proxies, never call `agents-remember`.",
            "Peer messages start with `From`; turn replies reach waiting senders only, else `role_message`.",
        ):
            with self.subTest(sentence=sentence):
                self.assertIn(sentence, orientation)

        # Startup timing belongs to the real generated role launch. Taskless
        # sessions retain their separate SKILL audience; orientation does not repeat it.
        self.assertNotIn(
            "Wait a few seconds and look once more before reporting it missing.", orientation
        )
        self.assertIn(
            "Retry once if agents-remember-task is missing.",
            _AR_TOOL_SERVER_USAGE_TAIL,
        )

    def test_each_role_is_told_how_to_reach_agents_and_the_developer(self) -> None:
        orientation = " ".join(read(LIFECYCLE / "operations" / "orientation.md").split())
        referral_pattern = r"\]\(\.\./operations/orientation\.md#ask-for-a-developer-decision\)"
        manifest = json.loads(read(LIFECYCLE / "composition-manifest.json"))
        for role in LAUNCHER_ROLES:
            with self.subTest(role=role):
                text = role_text(role)
                if re.search(referral_pattern, text):
                    for wrong_step in (
                        "#formal-permissions",
                        "#honor-harness-permission-decisions",
                    ):
                        mutant = text.replace("#ask-for-a-developer-decision", wrong_step)
                        self.assertNotEqual(mutant, text)
                        self.assertNotRegex(mutant, referral_pattern)
                    self.assertIn("`role_message`", orientation)
                    self.assertIn("in your own chat", orientation)
                else:
                    if role != "bootstrap":
                        self.assertIn("`role_message`", text)
                    self.assertIn("own chat", text)
                if role in STARTERS:
                    self.assertIn("`role_start`", text)
                else:
                    self.assertNotIn("role_start", manifest["roles"][role]["tools"])
                    assert_nonstarter_role_boundary(role, text, _step_controls)
        self.assertIn("dashboard roles need no parent.", orientation)
        self.assertIn("Dashboard Orchestrators have no parent", role_text("orchestrator"))
        self.assertIn("dashboard Managers have no parent", role_text("manager"))
        self.assertIn("parentless asks developer in own chat", role_text("investigator"))
        self.assertIn(
            "dashboard-started free agent with no task doc, brief, or parent;",
            role_text("bootstrap"),
        )
        coordination = " ".join(read(LIFECYCLE / "operations" / "coordination.md").split())
        for sentence in (
            "Architect: all 7; Orchestrator: all except Orchestrator, sprint; Manager: Worker/Reviewer/Curator/Consolidator/Investigator, master",
            "reconcile uncertain starts via same request ID.",
            "role != self; invent no IDs",
            "active turns may `held` or cancel (never into permission prompts);",
            "archived seats refuse (use successor).",
            "`wait` returns text, failure/cancel, `permission-pending`, timeout, or `accepted`; never mutually wait.",
        ):
            with self.subTest(sentence=sentence):
                self.assertIn(sentence, coordination)
        closeout_recovery = " ".join(
            read(
                LIFECYCLE / "reference" / "coordination" / "shared" / "closeout-recovery.md"
            ).split()
        )
        self.assertIn(
            "Before acting on a denied or unanswered harness request, read "
            "[Honor harness permission decisions](../../../operations/orientation.md#honor-harness-permission-decisions) "
            "for the waiting, denial and non-bypass rules.",
            closeout_recovery,
        )
        assert_message_handover_delivery(ROLES)
        architect = " ".join(role_text("architect").split())
        for sentence in (
            "[msgs]({{detail:reference/message-handover.md}})",
            "ask developer in own chat; never mutually wait.",
        ):
            with self.subTest(role="architect", sentence=sentence[:40]):
                self.assertIn(sentence, architect)

    def test_a_clean_sync_keeps_the_review_and_a_repair_stays_visible(self) -> None:
        """Clean sync preserves review without a new round and repairs stay visible."""

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
                "[sync]({{detail:reference/worker/sync-and-repairs.md}})",
                "Repair sealed own-file findings directly; return freeze to same Reviewer.",
                "per-finding repairs",
            ],
            ("manager", "coordination"): [
                "precedent-covered matters (read & record rulings; case reservations cover named items only; Manager decisions stay yours)",
                "[sync]({{detail:reference/worker/sync-and-repairs.md}}#synchronize-the-leaf)",
                "Workers repair sealed findings immediately.",
            ],
            ("reviewer", "review"): [
                "return reports missing per-finding entries without counting a round.",
                "Clean syncs open no round; review overlap/test-failure repairs only.",
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
        _step_controls(
            "reference/worker/sync-and-repairs.md",
            "Synchronize the leaf",
            (
                "The Worker reruns focused tests and reports the updated code and memory pair to the Manager.",
                "Product tools derive mechanical checks again.",
                "When clean sync changes the code or memory pair, the Manager obtains the Curator's refreshed dependent-document read before routine closeout.",
                "Send no new freeze to the Reviewer, open no review round, and request no Reviewer confirmation or receipt.",
                "When a path changed on both sides or a focused test fails after sync, the Worker repairs exactly that conflict or failure and sends the same Reviewer one look at the repair.",
            ),
        )
        _step_controls(
            "reference/worker/sync-and-repairs.md",
            "Focused test selection and freeze report details",
            ("per-finding repair entries (ID, files/lines, before/after test)",),
        )
        sync_detail = " ".join(read(LIFECYCLE / "reference/worker/sync-and-repairs.md").split())
        self.assertNotIn("hand the new freeze to the Reviewer", sync_detail)

    def test_the_capsules_send_a_return_and_never_point_at_the_agent_chat(self) -> None:
        """What is handed over and awaited travels by `role_message`, never only chat."""

        manifest_bytes = (CANONICAL / LIFECYCLE / "composition-manifest.json").read_bytes()
        manifest = json.loads(manifest_bytes)

        def capsule(role: str, operation: str) -> tuple[str, str]:
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
            assert outcome.shared_block is not None
            return (
                " ".join(outcome.result.render_instructions().split()),
                " ".join(outcome.shared_block.split()),
            )

        stage_sentence = {
            (
                "worker",
                "implementation",
            ): "Send Reviewer report path (no pasted text), head/hash + next stretch via `role_message`.",
            (
                "manager",
                "coordination",
            ): "While a child works or awaiting an already-sent developer question, record wait locally in end line and status file without sending a second wait message.",
            (
                "reviewer",
                "review",
            ): "Send verdict path directly to Worker via `role_message`",
            (
                "curator",
                "curation",
            ): "Send memory candidate to Reviewer via `role_message`",
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
        acceptance = " ".join(
            (CANONICAL / LIFECYCLE / "core" / "acceptance.md").read_text(encoding="utf-8").split()
        )
        self.assertIn("A filed artifact wakes nobody by itself", acceptance)
        self.assertIn("They then send it with `role_message` to the waiting agent", acceptance)
        self.assertIn(
            "write their durable handoff artifact before ending a successful handoff turn",
            acceptance,
        )
        self.assertNotIn("needs no second model-authored completion post", acceptance)
        for (role, operation), clause in stage_sentence.items():
            text, shared = capsule(role, operation)
            with self.subTest(role=role, told="the stage uses its assigned reporting route"):
                self.assertIn(clause, text)
                self.assertIn("agents-remember-task", shared)
            with self.subTest(role=role, told="a chat reply reaches a sender only on its wait"):
                orient_text, _ = capsule(role, "orientation")
                self.assertIn(
                    "turn replies reach waiting senders only, else `role_message`.",
                    orient_text,
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
                text, _ = capsule(role, operation)
                for phrase in banned:
                    with self.subTest(role=role, operation=operation, phrase=phrase):
                        self.assertNotIn(phrase, text)

    def test_the_workers_report_names_its_agent_id(self) -> None:
        # The sender identity and gap reporting belong to implementation + freeze report details.
        implementation = " ".join(read(LIFECYCLE / "operations" / "implementation.md").split())
        self.assertIn("record check/tree/result", implementation)
        sync_detail = " ".join(
            read(LIFECYCLE / "reference" / "worker" / "sync-and-repairs.md").split()
        )
        for clause in (
            "record the `role_message` `senderLine` ID",
            "byte-identical carry-overs",
            "rerun causes",
            "name any gaps",
        ):
            with self.subTest(clause=clause):
                self.assertIn(clause, sync_detail)
        turn_report = " ".join(read(LIFECYCLE / "templates" / "turn-report.md").split())
        self.assertIn("Explicitly list any relevant test or check not run and why.", turn_report)


if __name__ == "__main__":
    unittest.main()
