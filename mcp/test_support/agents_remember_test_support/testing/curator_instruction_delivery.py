"""Complete current instruction-delivery assertions owned by registered Curator tests.

Coordination timing, Reviewer intake, refused-write stops and foundation admission are
kept here; original unittest methods remain wrappers with their exact case identities.
"""

from __future__ import annotations

import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path

from agents_remember.application.role_capsules.compilation import CapsuleCompilationOutcome
from agents_remember.application.skill_resources.provider import shipped_composition_corpus
from agents_remember.cli.role_brief_message import shared_block
from agents_remember.models.role_capsules.types import (
    CapsuleRole,
)

from agents_remember_test_support.testing.capsule_wording import assert_reviewer_initial_intake
from agents_remember_test_support.testing.curator_contracts import (
    PERMISSION_ACTION,
    require_permission_delivery,
)
from agents_remember_test_support.testing.leaf_instruction_wording import (
    REFUSED_WRITE_ACTIONS,
    SERVED_REFUSED_WRITE,
    delivered_step,
)


def assert_coordination_steps(
    case: unittest.TestCase,
    root: Path,
    compile_capsule: Callable[[str, str], CapsuleCompilationOutcome],
) -> None:
    """A detail existing at a later replacement step cannot answer an earlier case."""
    cases: dict[CapsuleRole, tuple[tuple[str, str, str], ...]] = {
        "architect": (
            ("0:0", "selecting closeout trees", "highest-numbered memory PASS"),
            ("20:2", "collecting an idle leaf's helpers", "collect its results and finish"),
            ("21:0", "updating the master after a start, handover or landing", "In work now"),
            (
                "26:8",
                "checking a developer-stopped or archived seat",
                "developer's explicit word",
            ),
            (
                "40:3",
                "continuing unfinished work after a report",
                "send a continuation at once",
            ),
        ),
        "orchestrator": (
            ("0:0", "selecting closeout trees", "highest-numbered memory PASS"),
            ("20:2", "collecting an idle leaf's helpers", "collect its results and finish"),
            ("21:0", "updating the master after a start, handover or landing", "In work now"),
        ),
    }
    homes = {
        "20:2": (
            "coordination/shared/seat-recovery.md",
            "Before recovering idle, unfinished, stopped or unavailable-provider work, read",
        ),
        "21:0": (
            "coordination/shared/master-state.md",
            "Before updating the master after a start, handover or landing, read",
        ),
        "26:8": (
            "coordination/shared/seat-recovery.md",
            "Before recovering idle, unfinished, stopped or unavailable-provider work, read",
        ),
        "40:3": (
            "coordination/shared/seat-recovery.md",
            "Before recovering idle, unfinished, stopped or unavailable-provider work, read",
        ),
    }
    for role, scenarios in cases.items():
        operation = "planning" if role == "architect" else "coordination"
        for case_id, _old_trigger, answer in scenarios:
            with (
                case.subTest(role=role, case=case_id),
                tempfile.TemporaryDirectory() as directory,
            ):
                if case_id == "0:0":
                    clause = "Take closeout trees by filename from the highest-numbered memory PASS and landing-check verdict, never from a message."
                    case.assertIn(
                        clause, (root / "core" / "acceptance.md").read_text(encoding="utf-8")
                    )
                    outcome = compile_capsule(role, operation)
                    case.assertTrue(outcome.ok, outcome.render_explanation())
                    case.assertIsNotNone(outcome.result)
                    case.assertEqual(
                        outcome.shared_block,
                        (root / "core" / "every-role.md").read_text(encoding="utf-8"),
                    )
                    shared = shared_block(outcome.shared_block)
                    case.assertEqual(shared.count(clause), 1)
                    case.assertIn(answer, shared)
                    for mutant in (shared.replace(clause, "", 1), shared + "\n" + clause):
                        with case.assertRaises(AssertionError):
                            case.assertEqual(mutant.count(clause), 1)
                    continue
                if case_id == "40:3":
                    outcome = compile_capsule(role, operation)
                    case.assertTrue(outcome.ok, outcome.render_explanation())
                    case.assertIn(answer, shared_block(outcome.shared_block))
                    continue
                source, action = homes[case_id]
                instructions, detail = delivered_step(
                    root,
                    (role, operation),
                    "reference/" + source,
                    action,
                    Path(directory) / "report.md",
                )
                case.assertIn(action, instructions)
                case.assertIn(answer, detail)


def assert_reviewer_initial_intake_delivery(root: Path) -> None:
    """Follow the delivered intake owner before judging a received intake."""
    with tempfile.TemporaryDirectory() as directory:
        instructions, _ = delivered_step(
            root,
            ("reviewer", "review"),
            "reference/review-baseline.md",
            "Before preparing a baseline review, read",
            Path(directory) / "report.md",
        )
    assert_reviewer_initial_intake(instructions)


def _assert_ladder_clauses(case: unittest.TestCase, detail: str) -> None:
    for clause in (
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
    ):
        case.assertIn(clause, detail)


def assert_correct_write_ladder(
    case: unittest.TestCase, root: Path, read_role: Callable[[str], str]
) -> None:
    """The four failed roles receive the named ladder without losing hard stops."""
    shared = (root / "core/every-role.md").read_text(encoding="utf-8")
    [paragraph] = [
        line for line in shared.splitlines() if "correct-input refusal of a covered" in line
    ]
    case.assertTrue(paragraph.startswith("For a correct-input refusal"), "reaches every role")
    case.assertEqual(paragraph.count("](" + SERVED_REFUSED_WRITE + ")"), 1)
    with shipped_composition_corpus() as (origin, _manifest):
        served = (origin / "reference/refused-write.md").read_text(encoding="utf-8")
        _assert_ladder_clauses(case, served)
        require_permission_delivery(served, origin)
    for role in ("architect", "orchestrator"):
        case.assertNotIn("{{detail:reference/refused-write.md}}", read_role(role))
    with case.subTest(role="manager"), tempfile.TemporaryDirectory() as directory:
        _, detail = delivered_step(
            root,
            ("manager", "coordination"),
            "reference/refused-write.md",
            REFUSED_WRITE_ACTIONS["manager"],
            Path(directory) / "report.md",
        )
        _assert_ladder_clauses(case, detail)
        require_permission_delivery(detail, root)
    worker = read_role("worker")
    case.assertIn("a product-guard refusal", worker)
    case.assertIn(
        "except a covered same-tool/input-class write following the supplied recovery procedure",
        worker,
    )
    case.assertIn("a refusal naming a state you do not own", worker)


def assert_foundation_boundaries(
    case: unittest.TestCase, read_source: Callable[[Path], str], lifecycle: Path
) -> None:
    role = read_source(lifecycle / "roles/curator.md")
    inputs = role.split("## Establish the assignment\n", 1)[1].split(
        "## Reconcile, author and check", 1
    )[0]
    outputs = role.split("## Produce the handover\n", 1)[1].split(
        "## Use direct handovers within the leaf", 1
    )[0]
    required = {
        "Inputs": (
            inputs,
            (
                "Start the leaf pass only on a leaf task document, never taskless.",
                "{{detail:reference/curator/repository-foundation.md}}",
                "Before a repository's first or resumed knowledge foundation, read [repository foundation]({{detail:reference/curator/repository-foundation.md}}) for its task admission and distinct outputs.",
                "Refuse a producer list without a passing `handoff-check` result for that exact freeze.",
            ),
        ),
        "Outputs": (
            outputs,
            (
                "A correction withdraws or qualifies every affected-body claim it makes false",
                "adding a true paragraph does not answer a contradictory claim",
            ),
        ),
    }
    for heading, (text, clauses) in required.items():
        normalized = " ".join(text.split())
        for clause in clauses:
            with case.subTest(step=heading, clause=clause):
                case.assertIn(clause, normalized)
                with case.assertRaises(AssertionError):
                    case.assertIn(clause, normalized.replace(clause, "", 1))
    foundation = " ".join(
        read_source(lifecycle / "reference/curator/repository-foundation.md").split()
    )
    for clause in (
        "The user-started Architect opens the real foundation task",
        "real foundation task under a master",
        "The Curator uses its actual enclosure contract.",
        "No incidental code delta is required",
        "follow [the knowledge-bootstrap workflow]"
        "(skill://agents-remember/skills/c-14-knowledge-bootstrap/SKILL.md).",
        "Onboarding is optional input",
        "This foundation procedure authors no onboarding",
        "For setup\N{RIGHT SINGLE QUOTATION MARK}s state report and next step, follow [Name the next step]",
        "An instructed session cannot replace task admission",
        "never fabricate a task, worktree or enclosure",
        "Report partial foundation scope as partial and name the areas not reached",
    ):
        case.assertIn(clause, foundation)
    case.assertNotIn("**Repository foundation is regular task work.**", role)
