from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from agents_remember.tasks.document import Section
from agents_remember.tasks.document_refs import TaskDocumentTopology
from agents_remember.worktrees.closeout_queue import _graph_context
from agents_remember.worktrees.closeout_queue_errors import CloseoutQueueError
from agents_remember.worktrees.closeout_queue_evidence import (
    JUDGMENT_REGISTER_HEADER,
    JUDGMENT_REGISTER_SECTION,
    PRIORITY_REGISTER_HEADER,
    PRIORITY_REGISTER_SECTION,
    GradeAuthority,
    JudgmentAuthority,
    PriorityAuthority,
    _append_judgments,
    _append_priorities,
    _coherence_dispositions,
    _CuratorAttestation,
    _CuratorSourceCandidate,
    _decision_values,
    _evidence_fact,
    _reference_cells,
    _split_markdown_row,
    _table_rows,
    _task_relative_evidence,
    canonical_blocker_abort,
    canonical_grade,
    curator_evidence,
    curator_evidence_blockers,
    planning_authorities,
)
from pydantic import ValidationError
from test_closeout_queue import (
    JUDGMENT_HEADER,
    JUDGMENT_HEADING,
    LEAF_A,
    MASTER_A,
    PRIORITY_HEADER,
    PRIORITY_HEADING,
    RATIONALE,
    SPRINT,
    QueueFixture,
    _grade,
    _judgment_table,
    _priority_table,
    _write_curator_evidence,
)

HEX64 = "a" * 64


class CloseoutQueueEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.fixture = QueueFixture(Path(self.temp.name))
        self.contract = self.fixture.contracts[MASTER_A]
        self.graph = _graph_context(TaskDocumentTopology(self.fixture.coord), SPRINT)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_curator_attestation_binds_exact_unique_source_candidates(self) -> None:
        candidate = {
            "sourceFile": "src/a.py",
            "onboardingFile": "onboarding/src/a.py.md",
            "classification": "changed",
        }
        base = {
            "schema": "ar-curator-memory-quality/v1",
            "checklistStatus": "ready-for-closeout",
            "curatorActionableCount": 0,
            "memoryRepairCount": 0,
            "missingOnboardingCount": 0,
            "staleRouteIndexCount": 0,
            "sourceChangeCandidateCount": 1,
            "sourceChangeCandidates": [candidate],
            "onboardingRoot": "onboarding",
            "reportPath": "report.md",
            "reportSha256": HEX64,
        }
        self.assertEqual(_CuratorAttestation.model_validate(base).sourceChangeCandidateCount, 1)
        with self.assertRaisesRegex(ValidationError, "does not match"):
            _CuratorAttestation.model_validate({**base, "sourceChangeCandidateCount": 0})
        with self.assertRaisesRegex(ValidationError, "must be unique"):
            _CuratorAttestation.model_validate(
                {
                    **base,
                    "sourceChangeCandidateCount": 2,
                    "sourceChangeCandidates": [candidate, candidate],
                }
            )
        for field in ("sourceFile", "onboardingFile", "classification"):
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(ValidationError, "must not be blank"),
            ):
                _CuratorSourceCandidate.model_validate({**candidate, field: " "})

    def test_curator_evidence_refuses_missing_invalid_not_ready_and_stale_bytes(self) -> None:
        report = self.contract.worktree_group / "reports" / "curator-memory-quality.md"
        attestation = report.with_suffix(".json")
        attestation.unlink()
        with self.assertRaisesRegex(CloseoutQueueError, "unavailable"):
            curator_evidence(self.contract)

        _write_curator_evidence(self.contract)
        payload = json.loads(attestation.read_text(encoding="utf-8"))
        payload["curatorActionableCount"] = 1
        attestation.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(CloseoutQueueError, "does not prove"):
            curator_evidence(self.contract)

        _write_curator_evidence(self.contract)
        report.write_text(report.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")
        with self.assertRaisesRegex(CloseoutQueueError, "bytes do not match"):
            curator_evidence(self.contract)

    def test_curator_evidence_status_line_is_exact_and_blockers_are_stable(self) -> None:
        ambiguous = "- Status: **action-required**\n- Status: **ready-for-closeout**\n"
        _write_curator_evidence(self.contract, ambiguous)
        with self.assertRaisesRegex(CloseoutQueueError, "does not prove"):
            curator_evidence(self.contract)
        self.assertEqual(
            curator_evidence_blockers(self.contract, [], required=True),
            ["memory-readiness-evidence-stale"],
        )
        self.assertEqual(curator_evidence_blockers(self.contract, [], required=False), [])

    def test_disposition_table_parser_is_structural_and_rectangular(self) -> None:
        valid = """# Report

## Source-change dispositions

| Source file | Onboarding file | Classification | Disposition | Evidence |
| --- | --- | --- | --- | --- |
| src/a.py | onboarding/a.md | changed | reconciled | claim-1 |

## Next
"""
        rows = _coherence_dispositions(valid)
        self.assertEqual(rows[0].disposition, "reconciled")
        invalid = (
            ("# none\n", "exactly one"),
            (valid + "\n## Source-change dispositions\n", "exactly one"),
            (
                valid.replace("Source file", "Source path"),
                "canonical five-column header",
            ),
            (valid.replace("| --- | --- | --- | --- | --- |", "| --- | bad |"), "separator"),
            (
                valid.replace(
                    "| --- | --- | --- | --- | --- |",
                    "| : | - | -- | ::: | :-: |",
                ),
                "separator",
            ),
            (
                valid.replace(
                    "| --- | --- | --- | --- | --- |",
                    "| - - - | --- | --- | --- | --- |",
                ),
                "separator",
            ),
            (
                valid.replace(
                    "| src/a.py | onboarding/a.md | changed | reconciled | claim-1 |",
                    "| a | b | c | d |",
                ),
                "five cells",
            ),
            (valid.replace("reconciled", "mentioned"), "disposition-invalid"),
        )
        for text, message in invalid:
            with self.subTest(message=message), self.assertRaisesRegex(CloseoutQueueError, message):
                _coherence_dispositions(text)

    def test_coherence_evidence_requires_exact_candidate_set(self) -> None:
        candidate = {
            "sourceFile": "src/a.py",
            "onboardingFile": "onboarding/a.md",
            "classification": "changed",
        }
        report = self.contract.task_root / "notes" / "reports" / "LEAF-A-curator-report.md"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(
            """## Source-change dispositions
| Source file | Onboarding file | Classification | Disposition | Evidence |
| --- | --- | --- | --- | --- |
| src/a.py | onboarding/a.md | changed | reconciled | claim-1 |
""",
            encoding="utf-8",
        )
        _write_curator_evidence(self.contract, source_candidates=[candidate])
        self.assertEqual(len(curator_evidence(self.contract)), 3)
        report.write_text(
            report.read_text(encoding="utf-8")
            + "| src/a.py | onboarding/a.md | changed | reconciled | duplicate |\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(CloseoutQueueError, "exactly match"):
            curator_evidence(self.contract)
        report.unlink()
        with self.assertRaisesRegex(CloseoutQueueError, "require the canonical"):
            curator_evidence(self.contract)

    def test_canonical_grade_refusal_matrix_and_exact_evidence(self) -> None:
        authority = self.graph.grade_authority
        with self.assertRaisesRegex(CloseoutQueueError, "requires grade"):
            canonical_grade(
                None,
                authority=authority,
                candidate_ref=LEAF_A,
                owning_master=MASTER_A,
            )
        with self.assertRaisesRegex(CloseoutQueueError, "grade-invalid"):
            canonical_grade(
                {"priority": "normal", "judgmentId": ""},
                authority=authority,
                candidate_ref=LEAF_A,
                owning_master=MASTER_A,
            )
        missing_priority = replace(authority, priorities={})
        with self.assertRaisesRegex(CloseoutQueueError, "priority-missing"):
            canonical_grade(
                _grade("normal", LEAF_A),
                authority=missing_priority,
                candidate_ref=LEAF_A,
                owning_master=MASTER_A,
            )
        mismatch = replace(
            authority,
            priorities={LEAF_A.key: replace(authority.priorities[LEAF_A.key], priority="low")},
        )
        with self.assertRaisesRegex(CloseoutQueueError, "priority-mismatch"):
            canonical_grade(
                _grade("normal", LEAF_A),
                authority=mismatch,
                candidate_ref=LEAF_A,
                owning_master=MASTER_A,
            )
        no_judgment = replace(authority, judgments={})
        with self.assertRaisesRegex(CloseoutQueueError, "judgment-missing"):
            canonical_grade(
                _grade("normal", LEAF_A),
                authority=no_judgment,
                candidate_ref=LEAF_A,
                owning_master=MASTER_A,
            )
        grade, digest, evidence = canonical_grade(
            _grade("normal", LEAF_A),
            authority=authority,
            candidate_ref=LEAF_A,
            owning_master=MASTER_A,
        )
        self.assertEqual(
            (grade.priority, len(digest), evidence[0].path),
            ("normal", 64, "grade.md"),
        )

    def test_matching_judgment_requires_kind_author_subject_and_values(self) -> None:
        authority = self.graph.grade_authority
        judgment_id = _grade("normal", LEAF_A)["judgmentId"]
        judgment = authority.judgments[str(judgment_id)]
        cases = (
            (replace(judgment, kind="blocker"), "wrong canonical kind"),
            (replace(judgment, author="manager"), "author-refused"),
            (replace(judgment, subject=LEAF_A.key + "-other"), "subject-mismatch"),
            (replace(judgment, decision={"priority": "low"}), "values-mismatch"),
            (
                replace(
                    judgment,
                    decision={
                        **judgment.decision,
                        "unruled": "value",
                    },
                ),
                "values-mismatch",
            ),
        )
        for changed, message in cases:
            changed_authority = replace(authority, judgments={str(judgment_id): changed})
            with self.subTest(message=message), self.assertRaisesRegex(CloseoutQueueError, message):
                canonical_grade(
                    _grade("normal", LEAF_A),
                    authority=changed_authority,
                    candidate_ref=LEAF_A,
                    owning_master=MASTER_A,
                )

        signal_judgment = replace(
            judgment,
            decision={"priority": "normal", "urgency": "now", "risk": "high"},
        )
        grade, _digest, _evidence = canonical_grade(
            {
                **_grade("normal", LEAF_A),
                "urgency": "now",
                "risk": "high",
            },
            authority=replace(
                authority,
                judgments={str(judgment_id): signal_judgment},
            ),
            candidate_ref=LEAF_A,
            owning_master=MASTER_A,
        )
        self.assertEqual((grade.urgency, grade.risk), ("now", "high"))

        invalid_provenance = replace(judgment, confidence="")
        with self.assertRaisesRegex(CloseoutQueueError, "grade-invalid"):
            canonical_grade(
                _grade("normal", LEAF_A),
                authority=replace(
                    authority,
                    judgments={str(judgment_id): invalid_provenance},
                ),
                candidate_ref=LEAF_A,
                owning_master=MASTER_A,
            )

    def test_blocker_abort_requires_exact_authority_and_existing_evidence(self) -> None:
        evidence = self.graph.sprint.path.parent / "abort.md"
        evidence.write_text("proof\n", encoding="utf-8")
        judgment = JudgmentAuthority(
            judgment_id="ABORT-1",
            kind="atomic-blocker-abort",
            subject=MASTER_A.key,
            decision={"blocker": "abort", "graphRevision": self.graph.revision},
            rationale=RATIONALE,
            evidence_refs=("abort.md",),
            author="orchestrator",
            confidence="high",
            supersedes="",
            source_row="row",
        )
        authority = GradeAuthority(
            sprint=self.graph.sprint,
            judgments={"ABORT-1": judgment},
            priorities={},
        )
        canonical_blocker_abort(
            "ABORT-1",
            authority=authority,
            master_ref=MASTER_A,
            graph_revision=self.graph.revision,
        )
        for changed in (
            replace(judgment, kind="priority"),
            replace(judgment, subject=LEAF_A.key),
            replace(judgment, author="manager"),
            replace(judgment, decision={"blocker": "abort"}),
        ):
            with self.assertRaisesRegex(CloseoutQueueError, "judgment-invalid"):
                canonical_blocker_abort(
                    "ABORT-1",
                    authority=replace(authority, judgments={"ABORT-1": changed}),
                    master_ref=MASTER_A,
                    graph_revision=self.graph.revision,
                )
        evidence.unlink()
        with self.assertRaisesRegex(CloseoutQueueError, "does not exist"):
            canonical_blocker_abort(
                "ABORT-1",
                authority=authority,
                master_ref=MASTER_A,
                graph_revision=self.graph.revision,
            )

    def test_register_parsers_refuse_duplicates_shapes_and_invalid_values(self) -> None:
        judgment_row = (
            f"| J | priority | {LEAF_A.key} | priority=normal | because | grade.md | "
            "orchestrator | high | |"
        )
        priority_row = f"| {LEAF_A.key} | normal | none | J |"
        judgments: dict[str, JudgmentAuthority] = {}
        priorities: dict[str, PriorityAuthority] = {}
        _append_judgments(judgments, _judgment_table([judgment_row]))
        _append_priorities(priorities, _priority_table([priority_row]))
        with self.assertRaisesRegex(CloseoutQueueError, "repeats"):
            _append_judgments(judgments, _judgment_table([judgment_row]))
        with self.assertRaisesRegex(CloseoutQueueError, "repeats"):
            _append_priorities(priorities, _priority_table([priority_row]))
        with self.assertRaisesRegex(CloseoutQueueError, "require identity"):
            _append_judgments({}, _judgment_table([judgment_row.replace("because", "")]))
        with self.assertRaisesRegex(CloseoutQueueError, "Priority Register rows"):
            _append_priorities({}, _priority_table([priority_row.replace("normal", "urgent")]))
        with self.assertRaisesRegex(CloseoutQueueError, "outer Markdown pipes"):
            _append_priorities({}, _priority_table([priority_row[1:-1]]))
        with self.assertRaisesRegex(CloseoutQueueError, "outer Markdown pipes"):
            _append_judgments({}, _judgment_table([judgment_row[1:-1]]))
        with self.assertRaisesRegex(CloseoutQueueError, "exact template header"):
            _table_rows("| a | b | c |", 4)
        with self.assertRaisesRegex(CloseoutQueueError, "no canonical scheduling register"):
            _table_rows("", 5)
        with self.assertRaisesRegex(CloseoutQueueError, "exact template header"):
            _table_rows("| a | b | c | d |\n| --- | --- | --- | --- |", 4)
        with self.assertRaisesRegex(CloseoutQueueError, "separator"):
            _table_rows(_priority_table([]).replace("---", ":"), 4)
        with self.assertRaisesRegex(CloseoutQueueError, "separator"):
            _table_rows(
                f"{PRIORITY_HEADER}\n| --- | --- | --- |",
                4,
            )
        with self.assertRaisesRegex(CloseoutQueueError, "separator"):
            _table_rows(
                f"{PRIORITY_HEADER}\n--- | --- | --- | ---",
                4,
            )
        with self.assertRaisesRegex(CloseoutQueueError, "expected 4"):
            _table_rows(_priority_table(["| a | b | c |"]), 4)
        self.assertEqual(_table_rows(_priority_table([]), 4), [])

        sprint = replace(
            self.graph.sprint,
            document=self.graph.sprint.document.model_copy(
                update={
                    "sections": [
                        *self.graph.sprint.document.sections,
                        Section(heading="Notes", body="Observed, but not scheduling authority."),
                    ]
                }
            ),
        )
        parsed_judgments, parsed_priorities = planning_authorities(sprint)
        self.assertEqual(
            (set(parsed_judgments), set(parsed_priorities)),
            (set(self.graph.grade_authority.judgments), set(self.graph.grade_authority.priorities)),
        )

    def test_register_schema_matches_the_canonical_orchestration_template(self) -> None:
        template = (
            Path(__file__).parents[2]
            / "skills"
            / "l-01-agent-lifecycles"
            / "templates"
            / "orchestration-task.md"
        ).read_text(encoding="utf-8")
        self.assertIn(f"## {JUDGMENT_HEADING}", template)
        self.assertIn(f"## {PRIORITY_HEADING}", template)
        self.assertEqual(JUDGMENT_REGISTER_SECTION, JUDGMENT_HEADING.casefold())
        self.assertEqual(PRIORITY_REGISTER_SECTION, PRIORITY_HEADING.casefold())
        self.assertEqual(tuple(_split_markdown_row(JUDGMENT_HEADER)), JUDGMENT_REGISTER_HEADER)
        self.assertEqual(tuple(_split_markdown_row(PRIORITY_HEADER)), PRIORITY_REGISTER_HEADER)

    def test_low_level_markdown_decision_and_evidence_helpers_are_fail_closed(self) -> None:
        self.assertEqual(_split_markdown_row(r"| a\|b | c\\ |"), ["a|b", "c\\"])
        self.assertEqual(_reference_cells("`a.md`, b.md, ,``"), ["a.md", "b.md"])
        self.assertEqual(
            _decision_values("priority=normal, risk=low"),
            {"priority": "normal", "risk": "low"},
        )
        for value in ("broken", "=value", "key=", "key=a;key=b"):
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(CloseoutQueueError, "unique nonblank"),
            ):
                _decision_values(value)

        root = self.graph.sprint.path.parent
        local = root / "local.md"
        local.write_text("proof", encoding="utf-8")
        self.assertEqual(_task_relative_evidence(root, "local.md", "test").path, "local.md")
        for value, message in (
            ("", "task-relative"),
            (local.as_posix(), "task-relative"),
            ("../outside", "escapes"),
            ("missing.md", "does not exist"),
        ):
            with self.subTest(value=value), self.assertRaisesRegex(CloseoutQueueError, message):
                _task_relative_evidence(root, value, "test")
        with (
            mock.patch.object(Path, "read_bytes", side_effect=OSError("unreadable")),
            self.assertRaisesRegex(CloseoutQueueError, "does not exist"),
        ):
            _evidence_fact(local)


if __name__ == "__main__":
    unittest.main()
