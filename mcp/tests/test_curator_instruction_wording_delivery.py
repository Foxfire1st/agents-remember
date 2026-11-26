"""Compiled delivery, refused-write ladder, manifest, and Curator writer admission wording.

Split from ``test_curator_instruction_wording.py``. Covers compiled capsule detail delivery,
Reviewer intake delivery, refused-write recovery delivery, composition manifest descriptions
and tool grants, and Curator writer runtime admission.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import tempfile
import unittest
from pathlib import Path
from typing import get_args
from urllib.parse import unquote, urlsplit

from agents_remember.application.role_capsules.shared_block import filter_shared_block
from agents_remember.application.skill_resources.provider import shipped_composition_corpus
from agents_remember.cli.__main__ import build_parser
from agents_remember.cli.role_brief_message import shared_block
from agents_remember.cli.role_instruction_details import compose_launch_instructions
from agents_remember.models.role_capsules.vocabulary import capsule_role_or_none
from agents_remember.models.role_identity import canonical_role
from agents_remember.models.role_launcher import LauncherRole
from agents_remember.models.tools.public_roster import PUBLIC_TOOLS
from agents_remember.serving.capsule_delivery import capsule_delivery_from
from agents_remember_test_support.testing.capsule_wording import instruction_owner
from agents_remember_test_support.testing.curator_contracts import (
    PERMISSION_ACTION,
    SectionLink,
    compile_matrix,
    linked_section,
    require_clauses,
)
from agents_remember_test_support.testing.leaf_instruction_wording import (
    MESSAGE_HANDOVER_SOURCE,
    SERVED_REFUSED_WRITE,
    assert_emitted_detail_links,
    normalized,
)
from test_l84_capsule_delivery_proofs import _compiled_outcome

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
LIFECYCLE = Path("l-01-agent-lifecycles")
CANONICAL = REPOSITORY_ROOT / "skills"
LAUNCHER_ROLES: tuple[str, ...] = tuple(
    dict.fromkeys(canonical_role(role) for role in get_args(LauncherRole))
)
STARTERS = ("architect", "orchestrator", "manager")

PERMISSION_CLAUSES = (
    "Never bypass an open/denied harness permission; wait while open, else continue without it and report.",
)

LADDER_CLAUSES = (
    "Before applying a stop, classify the refused operation.",
    "It covers only the refused tool and input class",
    "`task_doc` retirement or folder movement",
    "it grants no new role or target authority",
    "Tell the owner of the record or machine state",
    "claim no tool receipt for manual work",
    "Read, check, validation and sync refusals",
    "starts, product guards and commit",
    PERMISSION_ACTION,
    "Other tools and input classes retain their",
)


def read(relative: Path) -> str:
    return (CANONICAL / relative).read_text(encoding="utf-8")


def role_text(role: str) -> str:
    return " ".join(read(LIFECYCLE / "roles" / f"{role}.md").split())


def _deliver_indexed_detail(
    role_operation: tuple[str, str],
    source_path: str,
    report_path: Path,
    label: str,
    fragment: str = "",
) -> tuple[str, str]:
    """Compile a capsule, verify its Reference-index detail pointer, and read the emitted detail."""
    role, operation = role_operation
    outcome = _compiled_outcome(role, operation)
    assert outcome.ok, outcome.render_explanation()
    assert outcome.result is not None
    compiled = outcome.result.render_instructions()
    marker = "{{detail:" + source_path + "}}" + fragment
    assert f"[{label}]({marker})" in compiled, (role, operation, label, marker)
    references = [
        item for item in outcome.result.capsule.detail_references if item.source_path == source_path
    ]
    assert len(references) == 1, (role, operation, source_path)
    assert references[0].content == (CANONICAL / LIFECYCLE / source_path).read_bytes()
    instructions, record = compose_launch_instructions(
        capsule_delivery_from(outcome.result), str(report_path), {}
    )
    entries = [entry for entry in record["detailReferences"] if entry["sourcePath"] == source_path]
    assert len(entries) == 1
    path = Path(entries[0]["path"])
    assert_emitted_detail_links(instructions, compiled, source_path, path)
    assert "{{detail:" not in instructions
    return instructions, path.read_text(encoding="utf-8")


def _require_permission_delivery(detail: str, root: Path) -> None:
    with shipped_composition_corpus() as (origin, _):
        target = origin / "operations/orientation.md"
        assert target.read_bytes() == (root / "operations/orientation.md").read_bytes()
        boundary = linked_section(
            detail,
            root / "reference/refused-write.md",
            SectionLink(
                target,
                "Honor harness permission decisions",
                PERMISSION_ACTION,
                "## Honor harness permission decisions",
            ),
        )
        require_clauses(boundary, PERMISSION_CLAUSES, "permission owner")
        reading = normalized(boundary)
        mutant = reading.replace(normalized(PERMISSION_CLAUSES[0]), "", 1)
        assert mutant != reading
        with unittest.TestCase().assertRaises(AssertionError):
            require_clauses(mutant, PERMISSION_CLAUSES, "deleted permission condition")
        for mutated_detail in (
            detail.replace("[Honor harness permission decisions]", "[missing rule]", 1),
            detail.replace("#honor-harness-permission-decisions", "#wrong-section", 1),
        ):
            assert mutated_detail != detail
            with unittest.TestCase().assertRaises(AssertionError):
                linked_section(
                    mutated_detail,
                    root / "reference/refused-write.md",
                    SectionLink(
                        target,
                        "Honor harness permission decisions",
                        PERMISSION_ACTION,
                        "## Honor harness permission decisions",
                    ),
                )


def assert_coordination_routes(case: unittest.TestCase, root: Path) -> None:
    held_ref = (
        "A held receipt means queued eventual delivery, possibly many turns later. Never resend it. "
        "Count a handover as taken only when the recipient's reply, report or intake names the exact "
        "object and shows the requested work or its start. Open every supplied recipient artifact "
        "before you call one absent. Withdraw a superseded held message with "
        "`withdraw_held_message_id`. A receiver does not poll."
    )
    held_kernel = (
        "`held` = queued; never resend, withdraw via `withdraw_held_message_id`, "
        "open supplied artifacts first."
    )
    require_clauses((root / "core/authority.md").read_text(), (held_ref,), "held core owner")
    for delivery in compile_matrix(root):
        if delivery.role != "orchestrator":
            continue
        shared = delivery.shared_block
        case.assertIsNotNone(shared)
        assert shared is not None
        case.assertEqual(
            shared,
            filter_shared_block((root / "core/every-role.md").read_text(), delivery.role),
        )
        case.assertEqual(shared.count(held_kernel), 1)
        for mutant in (shared.replace(held_kernel, "", 1), shared + "\n" + held_kernel):
            with case.assertRaises(AssertionError):
                case.assertEqual(mutant.count(held_kernel), 1)

    coord_text = (root / "operations/coordination.md").read_text(encoding="utf-8")
    concern_links = re.findall(r"\[exceptions\]\(([^)]+)\)", coord_text)
    case.assertEqual(len(concern_links), 1)
    parts = urlsplit(concern_links[0])
    case.assertEqual(parts.fragment, "memory-tool-failures-code-concerns-and-sync-conflicts")
    target = (root / "operations" / unquote(parts.path)).resolve()
    case.assertEqual(target, (root / "reference/curator/exceptions-and-recovery.md").resolve())
    case.assertIn(
        "## Memory-tool failures, code concerns, and sync conflicts\n",
        target.read_text(encoding="utf-8"),
    )


def assert_git_comparison_scope(case: unittest.TestCase) -> None:
    with tempfile.TemporaryDirectory() as directory:
        _, handoff = _deliver_indexed_detail(
            ("curator", "curation"),
            "reference/curator/memory-handover.md",
            Path(directory) / "report.md",
            "handover",
        )
    comparison = (
        "The Reviewer compares the leaf's base and candidate in Git: the code trees "
        "and the knowledge files in both memory trees. The Curator records no separate comparison."
    )
    case.assertIn(normalized(comparison), normalized(handoff))
    for component in (
        "leaf's base and candidate",
        "the code trees",
        "the knowledge files in both memory trees",
        "The Curator records no separate comparison.",
    ):
        mutant = normalized(handoff).replace(normalized(component), "")
        case.assertNotEqual(mutant, normalized(handoff))
        case.assertNotIn(normalized(comparison), mutant)


def assert_message_handover_delivery(case: unittest.TestCase, roles: tuple[str, ...]) -> None:
    root = CANONICAL / LIFECYCLE
    shared_by_role = {
        delivery.role: delivery.shared_block
        for delivery in compile_matrix(root)
        if delivery.operation == "orientation"
    }
    held_kernel = (
        "`held` = queued; never resend, withdraw via `withdraw_held_message_id`, "
        "open supplied artifacts first."
    )
    held_ref = (
        "A held receipt means queued eventual delivery, possibly many turns later. Never resend it. "
        "Count a handover as taken only when the recipient's reply, report or intake names the exact "
        "object and shows the requested work or its start. Open every supplied recipient artifact "
        "before you call one absent. Withdraw a superseded held message with "
        "`withdraw_held_message_id`. A receiver does not poll."
    )
    case.assertIn(held_ref, (root / "core/authority.md").read_text(encoding="utf-8"))
    message_labels = {
        "architect": ("msgs", ""),
        "orchestrator": ("msgs", ""),
        "manager": ("msgs", ""),
        "worker": ("msg", ""),
        "reviewer": ("msg", ""),
        "curator": ("msg", ""),
        "investigator": ("msgs", "#send-a-message-that-names-the-files"),
    }
    for role_name in roles:
        owner = instruction_owner(root, role_name)
        role = capsule_role_or_none(owner)
        case.assertIsNotNone(role, role_name)
        assert role is not None
        pointer = "{{detail:" + MESSAGE_HANDOVER_SOURCE + "}}"
        if role == "bootstrap":
            case.assertNotIn(pointer, role_text(role))
            continue
        case.assertIn(pointer, role_text(role))
        shared = shared_by_role[role]
        case.assertIsNotNone(shared)
        assert shared is not None
        case.assertEqual(
            shared,
            filter_shared_block((root / "core/every-role.md").read_text(), role),
        )
        case.assertEqual(shared.count(held_kernel), 1)
        for mutant in (shared.replace(held_kernel, "", 1), shared + "\n" + held_kernel):
            with case.assertRaises(AssertionError):
                case.assertEqual(mutant.count(held_kernel), 1)
        label, fragment = message_labels[role]
        with tempfile.TemporaryDirectory() as directory:
            _, held = _deliver_indexed_detail(
                (role, "orientation"),
                MESSAGE_HANDOVER_SOURCE,
                Path(directory) / "report.md",
                label,
                fragment,
            )
        for clause in (
            "Full-store recipient-busy is a refusal:",
            "1. Record the handover as pending and continue independent work.",
            "2. Resend once before the turn ends.",
            "3. If it is refused again, report the second refusal and leave it pending.",
            "Report the second refusal through the route named by your role, using its "
            "recipient and required notice fields.",
            "Never address the notice to yourself.",
            "A receipt or turn-end does not establish semantic acceptance.",
            "Messages are filed in the narrowest task shared by sender and recipient:",
            "- same-leaf messages: `notes/messages/<leaf-slug>/`;",
            "- shared-master or shared-sprint messages: that task's `notes/messages/`.",
            "The active enclosure holds its own receipts. Otherwise, receipts use the configured `transcriptRoot`.",
            "Before claiming that no message was sent, search the complete applicable message folder and receipt scope.",
        ):
            case.assertIn(clause, held, (role, clause))


class CuratorInstructionWordingDeliveryTests(unittest.TestCase):
    def test_coordination_detail_is_offered_at_all_eight_failed_scenario_steps(self) -> None:
        """A detail existing at a later replacement step cannot answer an earlier case."""
        root = CANONICAL / LIFECYCLE
        digest_map = json.loads((root / "core/digest-map.json").read_text(encoding="utf-8"))
        units_by_id = {entry["id"]: entry for entry in digest_map["units"]}

        cases: dict[str, tuple[tuple[str, str], ...]] = {
            "architect": (
                ("0:0", "highest-numbered memory PASS"),
                ("20:2", "collect its results and finish"),
                ("21:0", "In work now"),
                ("26:8", "developer's explicit word"),
                ("40:3", "Continue ended child turns with unfinished work and no valid wait/stop."),
            ),
            "orchestrator": (
                ("0:0", "highest-numbered memory PASS"),
                ("20:2", "collect its results and finish"),
                ("21:0", "In work now"),
            ),
        }
        homes = {
            "20:2": ("reference/coordination/shared/seat-recovery.md", "seats"),
            "21:0": ("reference/coordination/shared/master-state.md", "master"),
            "26:8": ("reference/coordination/shared/seat-recovery.md", "seats"),
        }
        for role, scenarios in cases.items():
            operation = "planning" if role == "architect" else "coordination"
            for case_id, answer in scenarios:
                with (
                    self.subTest(role=role, case=case_id),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    if case_id == "0:0":
                        clause = "Take closeout trees by filename from the highest-numbered memory PASS and landing-check verdict, never from a message."
                        acceptance = (root / "core/acceptance.md").read_text(encoding="utf-8")
                        self.assertIn(clause, acceptance)
                        self.assertEqual(units_by_id["AC0.4"]["disposition"], "reference")
                        self.assertEqual(units_by_id["AC0.4"]["file"], "core/acceptance.md")
                        self.assertIn(answer, acceptance)
                        mutant = acceptance.replace(clause, "", 1)
                        self.assertNotIn(clause, mutant)
                        continue
                    if case_id == "40:3":
                        authority = (root / "core/authority.md").read_text(encoding="utf-8")
                        self.assertIn(
                            "Send continuations immediately for ended turns with unfinished authorized work",
                            authority,
                        )
                        outcome = _compiled_outcome(role, operation)
                        self.assertTrue(outcome.ok, outcome.render_explanation())
                        self.assertIsNotNone(outcome.shared_block)
                        emitted_shared = shared_block(outcome.shared_block)
                        self.assertEqual(emitted_shared.count(answer), 1)
                        for mutant in (
                            emitted_shared.replace(answer, "", 1),
                            emitted_shared + "\n" + answer,
                        ):
                            with self.assertRaises(AssertionError):
                                self.assertEqual(mutant.count(answer), 1)
                        continue
                    source_path, label = homes[case_id]
                    _instructions, detail = _deliver_indexed_detail(
                        (role, operation),
                        source_path,
                        Path(directory) / "report.md",
                        label,
                    )
                    self.assertIn(answer, detail)

    def test_reviewer_waits_for_initial_mode_before_enforcing_complete_intake(self) -> None:
        """Follow the delivered intake owner before judging a received intake."""
        with tempfile.TemporaryDirectory() as directory:
            instructions, _ = _deliver_indexed_detail(
                ("reviewer", "review"),
                "reference/review-baseline.md",
                Path(directory) / "report.md",
                "baseline",
            )
        for clause in (
            "If mode is absent, ask starter via `role_message` (developer if parentless) and wait.",
            'Require before inspecting: mode (`baseline`/`fix-verification`; no whole-review in `fix-verification`), seam, req IDs/versions/packets/approval, exact candidate (no branch/"latest") + clean-copy checks (withhold PASS until passing), Worker attempts, `system/tools.md` + `system/design-guidelines.md` + guidelines.',
        ):
            self.assertIn(clause, instructions)
        coordination = " ".join(read(LIFECYCLE / "operations/coordination.md").split())
        self.assertIn(
            "After `role_start`, send Reviewer's intake via `role_message` (mode, purpose, `system/tools.md`, req IDs/versions/packets, approval record); it inspects nothing earlier.",
            coordination,
        )

    def test_correct_write_ladder_distinguishes_named_writes_from_other_stops(self) -> None:
        """All roles receive the named ladder without losing hard stops."""
        root = CANONICAL / LIFECYCLE
        shared = (root / "core/every-role.md").read_text(encoding="utf-8")
        [paragraph] = [
            line for line in shared.splitlines() if "correct-input refused covered writes" in line
        ]
        self.assertTrue(
            paragraph.startswith("For correct-input refused covered writes"),
            "reaches every role",
        )
        self.assertEqual(paragraph.count("](" + SERVED_REFUSED_WRITE + ")"), 1)
        self.assertIn("(same tool and input class) before applying a stop.", paragraph)
        self.assertIn("Other refusals keep stops.", paragraph)
        with shipped_composition_corpus() as (origin, _manifest):
            served = (origin / "reference/refused-write.md").read_text(encoding="utf-8")
            for clause in LADDER_CLAUSES:
                self.assertIn(clause, served)
            _require_permission_delivery(served, origin)
        for role in ("architect", "orchestrator"):
            self.assertNotIn("{{detail:reference/refused-write.md}}", role_text(role))
        with self.subTest(role="manager"), tempfile.TemporaryDirectory() as directory:
            _, detail = _deliver_indexed_detail(
                ("manager", "coordination"),
                "reference/refused-write.md",
                Path(directory) / "report.md",
                "retire",
                "#apply-the-procedure-to-a-task-document-or-folder",
            )
            for clause in LADDER_CLAUSES:
                self.assertIn(clause, detail)
            _require_permission_delivery(detail, root)
        worker_outcome = _compiled_outcome("worker", "implementation")
        self.assertTrue(worker_outcome.ok, worker_outcome.render_explanation())
        self.assertIsNotNone(worker_outcome.shared_block)
        assert worker_outcome.shared_block is not None
        self.assertIn("Other refusals keep stops.", worker_outcome.shared_block)
        self.assertIn(
            "fix faulty input on same tool; stop after 3 failed repairs on one item and tell Manager.",
            role_text("worker"),
        )

    def test_correct_write_procedure_is_delivered_at_the_refusal_step(self) -> None:
        shared = (CANONICAL / LIFECYCLE / "core/every-role.md").read_text(encoding="utf-8")
        [paragraph] = [
            line for line in shared.splitlines() if "correct-input refused covered writes" in line
        ]
        self.assertIn("follow [refused-write](" + SERVED_REFUSED_WRITE + ")", paragraph)
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
        curation = " ".join(read(LIFECYCLE / "operations" / "curation.md").split())
        exceptions = " ".join(
            read(LIFECYCLE / "reference" / "curator" / "exceptions-and-recovery.md").split()
        )
        refused = " ".join(read(LIFECYCLE / "reference" / "refused-write.md").split())
        self.assertIn(
            "{{detail:reference/knowledge/serving-build.md}}",
            read(LIFECYCLE / "roles" / "curator.md"),
        )
        details = " ".join(read(LIFECYCLE / "reference" / "knowledge/serving-build.md").split())
        intake = role_text("curator")
        self.assertIn(
            "`read_ar_files`/`context_packet`: `task_context`; `knowledge_read`: `workspace.memoryRoot`",
            intake,
        )
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
        with self.subTest(told="operation"):
            self.assertNotIn("correct-write recovery", curation)
            self.assertIn("Verify writer outputs resolve in `workspace.memoryRoot`.", curation)
            self.assertIn("memory_quality_check", curation)
            self.assertIn("Closeout runs stale `dependent` check (see exceptions).", curation)
            self.assertIn("prepare", curation)
            self.assertIn("publish", curation)
            self.assertIn("validate", curation)
            self.assertNotIn("the writer is not available to you on this line", curation)
        with self.subTest(told="exceptions_and_refused"):
            self.assertIn(
                'knowledge_read(memoryRoot=<leaf memory>, repositoryRoot=<leaf code>, codeTreeId="working-tree", view="dependent", staleOnly=true)',
                exceptions,
            )
            self.assertIn("match candidates before and after", exceptions)
            self.assertIn("`memoryTree.treeKey`, final `counts.total` and receipt", exceptions)
            self.assertIn(
                "A missing tool or unadmitted capability is not a refused correct write. Report the missing capability and what remains unwritten.",
                refused,
            )
        with self.subTest(told="role"):
            self.assertIn(
                "Write `onboarding/`+`knowledge/` via one writer (`knowledge_write` or serving-build CLI `--commit`/`commit: true`; missing writer != correct-input refusal);",
                intake,
            )
            self.assertIn("`agents-remember-task`", read(LIFECYCLE / "core/every-role.md"))
            self.assertNotIn("the writer is not available to you on this line", intake)

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
                # The seven lifecycle roles message each other; the setup seat speaks with the
                # developer and messages no role agent.
                self.assertEqual("role_message" in tools, role != "bootstrap")
                self.assertEqual("role_start" in tools, role in STARTERS)
                self.assertEqual(set(tools) - set(PUBLIC_TOOLS), set())
