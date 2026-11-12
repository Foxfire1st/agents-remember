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
                    "Check packet dependencies before starting a leaf and before integrating it; the product does not perform that check.",
                ),
            ),
            (
                "roles/manager.md",
                "Decide gates and keep the master current",
                (
                    "Before responding to gate findings from closeout preview or any landing route, read [Repair a gate refusal through its owner](../operations/closeout.md#repair-a-gate-refusal-through-its-owner)",
                ),
            ),
            (
                "roles/manager.md",
                "Make decisions within your authority",
                (
                    "sandbox or scratch instruments that change no product source",
                    "After a harness, loader or capture fault, you may order one rerun on the same operands",
                    "A second failure of one cause goes upward with the attempts already made",
                ),
            ),
            (
                "roles/manager.md",
                "Report through the assigned recipient",
                (
                    "a small item that has cost about an hour, or a second repair round or freeze, with the hours and a proposal",
                ),
            ),
            (
                "roles/orchestrator.md",
                "Communicate with the Architect",
                (
                    "a small item that has cost about an hour, or a second repair round or freeze; include its hours and a proposal",
                ),
            ),
            (
                "roles/worker.md",
                "Repair findings and failed checks",
                (
                    "When the same check or finding still fails after three of your repairs, stop repairing",
                    "When one small item costs about an hour, a second repair round or a second freeze, follow [Report a costly small repair](../operations/implementation.md#report-a-costly-small-repair)",
                ),
            ),
            (
                "roles/worker.md",
                "Stop conditions and stage completion",
                (
                    "Work step to step and wave to wave in one turn, collecting helper results",
                    "End only at your handover, a named wait or a nearly full context",
                    "handover checks named in `system/coding-guidelines.md` under `workspace.memoryRoot` pass on a clean copy of the final bytes",
                ),
            ),
            (
                "roles/reviewer.md",
                "Organise and continue the assigned work",
                (
                    "Work step to step and wave to wave in one turn, collecting helpers' results",
                    "End only at the verdict, a named wait or nearly full context",
                ),
            ),
            (
                "roles/curator.md",
                "Repair and finish the stage",
                (
                    "Work from step to step and wave to wave in one turn, collecting helpers' results",
                    "End only at the handover, a named wait or a nearly full context",
                ),
            ),
            (
                "roles/investigator.md",
                "Read and investigate",
                (
                    "Work step to step, wave to wave, in one turn",
                    "While a developer answer is pending, continue independent work. End the turn at your report, when no independent work remains, or at nearly full context",
                ),
            ),
            (
                "roles/investigator.md",
                "Organise and continue the work",
                (
                    "Collect helpers and continue ordinary work",
                    "For helper collection and continuation, follow [Parallel work and waiting](../core/every-role.md#parallel-work-and-waiting).",
                ),
            ),
        )
        for source, heading, clauses in (*checks, *RELOCATED_WORKING_STEPS):
            with self.subTest(source=source, heading=heading):
                _step_controls(source, heading, clauses)
        for role in ("worker", "reviewer", "curator", "investigator"):
            self.assertNotIn("Hermes: their files", role_text(role))

        for role, heading, trigger in (
            (
                "architect",
                "Questions received from children",
                "Before answering a child's question or returning the developer's answer",
            ),
            (
                "manager",
                "Report through the assigned recipient",
                "Before handling a child's question or returning the developer's answer",
            ),
            (
                "orchestrator",
                "Questions received from children",
                "Before handling a child's question or the developer's answer to it",
            ),
        ):
            _step_controls(
                f"roles/{role}.md",
                heading,
                (
                    trigger,
                    "{{detail:reference/coordination/shared/child-questions.md}}",
                ),
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
            "Verify and hand over",
            (
                "A review of the sprint plan (plan review) or of the completed sprint\N{RIGHT SINGLE QUOTATION MARK}s handover (super-exit review) has no startable Reviewer seat.",
                "Bring either review to the Architect, who takes it to the developer",
            ),
        )

    def test_freeze_report_and_receiver_keep_candidate_and_check_identity(self) -> None:
        checks = (
            (
                "operations/implementation.md",
                "Outputs",
                (
                    "relevant checks not run and why",
                    "files it exercises are byte-identical",
                    "For each rerun, name the changed file that required it",
                    "Before each freeze",
                    "handover checks on a clean copy of the final candidate",
                    "the copy's tree and its result",
                    "Repair failures before freezing",
                    "distinct from full code-quality operations",
                ),
            ),
            (
                "roles/worker.md",
                "Report and freeze the candidate",
                (
                    "Carry the leaf's journal records and their attempt identifiers in the handover",
                    "Before writing the freeze report",
                    "{{detail:templates/turn-report.md}}",
                    "run `handoff-check` against that freeze with the serving build's interpreter, identified through `server_info` on `agents-remember-task`",
                    "Name the result in the message",
                ),
            ),
            (
                "roles/reviewer.md",
                "Confirm the assignment before inspection",
                ("Require passing coding-guidelines handover results bound to that tree",),
            ),
            (
                "roles/reviewer.md",
                "Bind the seam and catalogs",
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

    def test_design_guidelines_are_obligations_at_each_ui_input_step(self) -> None:
        checks = (
            (
                "roles/worker.md",
                "Establish the assignment",
                (
                    "`system/design-guidelines.md` under `workspace.memoryRoot`",
                    "where it exists",
                    "before the first interface edit and follow it",
                ),
            ),
            (
                "roles/reviewer.md",
                "Confirm the assignment before inspection",
                (
                    "`system/design-guidelines.md`",
                    "under `workspace.memoryRoot`",
                    "where it exists. Review the interface as it directs",
                ),
            ),
        )
        for source, heading, clauses in checks:
            with self.subTest(source=source):
                _step_controls(source, heading, clauses)
        worker = read(LIFECYCLE / "roles/worker.md")
        self.assertLess(
            worker.index("system/design-guidelines.md"), worker.index("## Report and freeze")
        )
        for role in ("worker", "reviewer"):
            for copied_method in ("DESIGN.md", "TREATMENT.md", "TECHNIQUES.md"):
                self.assertNotIn(copied_method, role_text(role))

    def test_investigator_scope_report_and_authority_are_explicit(self) -> None:
        investigator = role_text("investigator")
        for clause in (
            "Investigate one concern of any kind",
            "without a task reference, on a sprint, or on a sprint and master",
            "its first `role_message` on `agents-remember-task` supplies the concern",
            "ask that parent once and wait; do not invent either",
            "Without a parent, use the developer's request",
            "Run checks in your scratch folder or in a sandbox built with the repository's sandbox tooling.",
            "Write one concise report at the path in your handover.",
            "tell it that the report is written and name its path",
            "Write the report before changing system state, and follow the Boundaries below before remediation.",
            "You may change system state only on an explicit order from the developer or from the parent named in your assignment.",
            "Edit no file in a code checkout or a leaf's enclosure.",
            "Make no commit and run no closeout or integration.",
            "Change nothing in the memory repository: no onboarding card, knowledge record or history row.",
            "Change no task document or task status.",
            "If a starter orders code or memory changes, decline",
            "Start no role.",
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
        self.assertNotIn("system-specialist", router)
        orientation = read(LIFECYCLE / "operations/orientation.md")
        self.assertIn(
            "An Investigator uses its parent's first message for the concern",
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
            "When no Manager or Orchestrator owns closeout, you may coordinate the "
            "existing paired closeout (`worktree_closeout_preview` and "
            "`worktree_closeout_apply` on `agents-remember-task`) only under the "
            "already-delegated authority of [Authorized "
            "Closeout](../operations/closeout.md), after independent review and "
            "required curation evidence exist."
        )
        self.assertIn(kept, architect)
        self.assertIn(
            "Flat mode here means only the developer-chosen direct-coordination exception.",
            architect,
        )
        closeout = " ".join(read(LIFECYCLE / "operations/closeout.md").split())
        self.assertIn(
            "In a flat run with neither Manager nor Orchestrator, the owning Architect may coordinate the same transaction only with the developer's explicit commit approval for that closeout, or with authority the developer delegated for the series and recorded in the task document.",
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
                trigger = {
                    "worker": "Before sending a handover",
                    "reviewer": "Before handing over work",
                    "curator": "Before sending a message",
                }[role]
                self.assertIn(trigger, original_role)
                original_operation = operation_path.read_text()
                selected_operation_route = "Before responding to a full-store refusal, read [Handle a full-store refusal](../reference/message-handover.md#handle-a-full-store-refusal) for the pending handover, single retry and second-refusal report."
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
                                manifest["references"]["message-handover"]["step"] = ""
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
                    "Use a product-supplied agent identifier only whole and unchanged",
                    "Use only the whole, unchanged agent ID supplied by the product",
                )
                with self.subTest(obligation=name):
                    if (
                        "Use a product-supplied agent identifier only whole and unchanged"
                        in original_worker
                    ):
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
            "A parent agent is optional. A role started from the dashboard has none and needs none; the developer reads and answers in its own chat.",
            "Call Agents Remember (AR) tools only on the tool server named `agents-remember-task`.",
            "Find its tools by that server name, spelled `agents-remember-task` or `agents_remember_task`, in either place your harness provides them:",
            "declared tools with that prefix",
            "the harness's own instructions for its tool search or script tool",
            "A tool that lists or proxies tool servers is not the way to AR tools unless it lists `agents-remember-task` itself.",
            "Do not search for or describe AR tools through it otherwise.",
            'Its "server not found" answer speaks only for that tool.',
            "Do not connect to, list, describe or call a server named `agents-remember` or any of its tools: it belongs to another installation.",
            "A message from another agent begins with a `From` line.",
            "Your reply in that turn reaches the sender only when it waits on its own call; otherwise send the reply through `role_message` on `agents-remember-task`.",
        ):
            with self.subTest(sentence=sentence):
                self.assertIn(sentence, orientation)

        # Startup timing belongs to the real generated role launch. Taskless
        # sessions retain their separate SKILL audience; orientation does not repeat it.
        self.assertNotIn(
            "Wait a few seconds and look once more before reporting it missing.", orientation
        )
        self.assertIn(
            "take some seconds to appear: look once more before reporting agents-remember-task missing",
            _AR_TOOL_SERVER_USAGE_TAIL,
        )

    def test_each_role_is_told_how_to_reach_agents_and_the_developer(self) -> None:
        orientation = " ".join(read(LIFECYCLE / "operations" / "orientation.md").split())
        pointer = "Developer questions and relayed answers follow `operations/orientation.md`"
        referral_pattern = r"(?:`(?:\.\./)?operations/orientation\.md`|\]\(\.\./operations/orientation\.md(?:#ask-for-a-developer-decision)?\))"
        manifest = json.loads(read(LIFECYCLE / "composition-manifest.json"))
        for role in LAUNCHER_ROLES:
            with self.subTest(role=role):
                text = role_text(role)
                if pointer in text or re.search(referral_pattern, text):
                    # One-home referral: the tokens live in the referenced question home.
                    if "#ask-for-a-developer-decision" in text:
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
                    self.assertIn("`role_message`", text)
                    self.assertIn("in your own chat", text)
                if role in STARTERS:
                    self.assertIn("`role_start`", text)
                else:
                    self.assertNotIn("role_start", manifest["roles"][role]["tools"])
                    assert_nonstarter_role_boundary(role, text, _step_controls)
        for role in set(LAUNCHER_ROLES) - {"architect"}:
            with self.subTest(role=role, told="a role without a parent needs none"):
                text = role_text(role)
                original = (
                    r"started from the dashboard,? (?:it )?"
                    r"(has no parent|has none|needs no parent|have no parent|need none)"
                )
                equivalent = {
                    "reviewer": "A dashboard-started Reviewer has and needs no parent.",
                    "curator": "A dashboard-started Curator needs no parent.",
                    "bootstrap": "a setup role started from the dashboard, with no task document, brief or parent.",
                }
                self.assertTrue(
                    re.search(original, text)
                    or equivalent.get(role, "NO MATCHING ROLE CLAUSE") in text
                )
        coordination = " ".join(read(LIFECYCLE / "operations" / "coordination.md").split())
        for sentence in (
            "| Architect | Investigator, Orchestrator, Manager, Worker, Reviewer and Curator |",
            "| Orchestrator | Manager, Worker, Reviewer, Curator and Investigator under its own sprint |",
            "| Manager | Worker, Reviewer, Curator and Investigator under its own master |",
            "for same-identifier reconciliation and refusal reporting",
            "A message to a recipient already in a turn may be held until that turn ends. If the host delivers it into the running turn, that turn may be cancelled. If the host has not delivered it, the answer names `held` and its reason.",
            "A dashboard-started role works directly with the developer and needs no parent.",
            # What the message tool answers, as its own description says it.
            "A role address never means you yourself.",
            "If the host has not delivered it, the answer names `held` and its reason.",
            "A closed session is resumed first. An archived or missing agent is refused and left as it is.",
            (
                "The host is never sent a message for a recipient at a permission prompt: "
                "doing so would answer that permission with a denial."
            ),
            (
                "Before acting on a denied or unanswered harness request, read "
                "[Honor harness permission decisions](orientation.md#honor-harness-permission-decisions) "
                "for the waiting, denial and non-bypass rules."
            ),
            "a pending permission, including for a wait on a held message",
            "a timeout while the message remains delivered",
            "notice that its turn failed or was cancelled",
            "`accepted` without text when the host cannot identify which turn took the message; read the reply later.",
            "A message delivered during a running turn may receive that turn's own text; `detail` identifies this case.",
            "Do not wait on an agent that may be waiting on you. Two mutually waiting agents remain stopped until a wait expires.",
        ):
            with self.subTest(sentence=sentence):
                self.assertIn(sentence, coordination)
        assert_message_handover_delivery(ROLES)
        architect = " ".join(role_text("architect").split())
        for sentence in (
            (
                "Before handing work to a waiting seat, read [message handovers and "
                "receipts]({{detail:reference/message-handover.md}}) for the sender "
                "and payload rules of `role_message` on `agents-remember-task`, "
                "full-store refusals, and where filed messages and receipts are kept."
            ),
            "its final text, a failed or cancelled turn, a pending permission, or a timeout. An `accepted` result without text means that the reply must be read later.",
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
                "When no path changed on both sides, run the focused tests once more",
                "Send no new freeze to the Reviewer and begin no round.",
                # Rule 12: the repair goes ahead of any plan or approval.
                "no plan, account, census or approval goes ahead of the repair",
                "the Manager gives no go-ahead for it",
                # Rule 13: the repair is visible in the freeze.
                "For every repaired finding, record its identifier",
                "the test that fails before the repair and passes after it",
            ],
            ("manager", "coordination"): [
                # Rule 11: a precedent is settled where it falls.
                "Settle a matter covered by an earlier ruling of the same kind and name that ruling in your record. Send only an uncovered question upward.",
                # Rule 9: the Manager decides from the closeout preview, not a new round.
                (
                    "Before deciding closeout after a leaf sync, read [Synchronize the "
                    "leaf](../operations/implementation.md#synchronize-the-leaf) for the "
                    "Worker's focused rerun, your `worktree_closeout_preview` on "
                    "`agents-remember-task`, the no-new-freeze condition and the "
                    "same-Reviewer repair path."
                ),
                # Rule 12: the Manager gives no go-ahead for a repair.
                "needs no plan, account or go-ahead from you",
            ],
            ("reviewer", "review"): [
                # Rule 13: each finding is checked against its entry, with no round counted.
                "compare every finding with its entry in the Worker's freeze report",
                "without counting a round",
                # Rule 9: a cleanly synced leaf is not sent back for a new freeze.
                "For a cleanly synced leaf, expect no new freeze and open no round",
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
        operation = " ".join(read(LIFECYCLE / "operations/implementation.md").split())
        _step_controls(
            "operations/implementation.md",
            "Boundaries",
            (
                "When no path changed on both sides, run the focused tests once more and tell the Manager",
                "The tools derive the mechanical checks again, and the Manager's closeout preview decides",
                "Send no new freeze to the Reviewer and begin no round",
                "Request no Reviewer confirmation or receipt",
                "When a path changed on both sides or a focused test fails, repair exactly that and send the same Reviewer one look at the repair",
            ),
        )
        self.assertIn("Send no new freeze to the Reviewer and begin no round", operation)
        self.assertNotIn("hand the new freeze to the Reviewer", operation)

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
                "send that parent what you await and from whom, using `role_message`"
            ),
            ("manager", "coordination"): (
                "send what you await, and from whom, to that parent through `role_message`"
            ),
            (
                "reviewer",
                "review",
            ): "tell that parent what and whom you await with `role_message`",
            (
                "curator",
                "curation",
            ): "report what you await and from whom to that parent with `role_message`",
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
        # and the sender hands it to the waiting agent (R62 rule44).
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
            text = capsule(role, operation)
            with self.subTest(role=role, told="an unfinished turn sends what it waits for"):
                self.assertIn(clause, text)
                self.assertIn("agents-remember-task", text)
            with self.subTest(role=role, told="a chat reply reaches a sender only on its wait"):
                self.assertIn(
                    "Your reply in that turn reaches the sender only when it waits on its own call",
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
        # The sender identity belongs to the implementation report, including the no-message case.
        implementation = " ".join(read(LIFECYCLE / "operations" / "implementation.md").split())
        for clause in (
            "exact checks and results",
            "relevant checks not run and why",
            'your agent identifier as the `senderLine` of `role_message` on `agents-remember-task` names it, or "agent ID not known" if you sent no message',
            "open limitations",
        ):
            with self.subTest(clause=clause):
                self.assertIn(clause, implementation)


if __name__ == "__main__":
    unittest.main()
