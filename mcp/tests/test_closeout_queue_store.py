"""L3 forcing for the two-state disposable projection store."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agents_remember.controlplane.closeout_queue_records import CloseoutProjectionBuild
from agents_remember.controlplane.closeout_queue_store import (
    CloseoutQueueStore,
    CloseoutQueueStoreError,
    ProjectionSourceIdentity,
    queue_store_paths,
)
from agents_remember.models.closeout.projection import (
    MAX_CLOSEOUT_CANDIDATES,
    MAX_CLOSEOUT_SOURCE_PROBLEMS,
    CloseoutProjectionMember,
    CloseoutQueueState,
    ProjectionRebuildResult,
    ProjectionSourceProblem,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from pydantic import ValidationError

NOW = "2026-08-24T00:00:00+00:00"
SPRINT = TaskDocumentRef(repository="repo-a", path="sprint/task.json")
LEAF = TaskDocumentRef(repository="repo-a", path="master/leaf.json")
MASTER = TaskDocumentRef(repository="repo-a", path="master/task.json")
HEX40 = "a" * 40
HEX64 = "b" * 64


def _member(*, generation: str = HEX64) -> CloseoutProjectionMember:
    return CloseoutProjectionMember(
        generationId=generation,
        taskDocumentRef=LEAF,
        owningMaster=MASTER,
        contractPath="/coord/tasks/repo-a/master/enclosures/leaf/contract.md",
        candidateTree=HEX40,
        sourceDoorFingerprint="c" * 64,
        classification="ready",
        priority="normal",
        order=0,
    )


def _build(*, fingerprint: str = HEX64) -> CloseoutProjectionBuild:
    return CloseoutProjectionBuild(
        sprintTaskDocumentRef=SPRINT,
        sourceFingerprint=fingerprint,
        sourceClassification="active",
        members=[_member()],
        builtAt=NOW,
    )


def _active_source(*, fingerprint: str = HEX64) -> ProjectionSourceIdentity:
    return ProjectionSourceIdentity(
        fingerprint,
        classification="active",
        members=(_member(),),
    )


class CloseoutProjectionStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = CloseoutQueueStore(self.root, SPRINT)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write(self, text: str) -> None:
        self.store.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.store.state_path.write_text(text, encoding="utf-8")

    def test_paths_are_confined_and_persisted_conditions_are_exactly_two(self) -> None:
        escaped = TaskDocumentRef.model_construct(repository="repo-a", path="../../task.json")
        with self.assertRaisesRegex(CloseoutQueueStoreError, "escapes"):
            queue_store_paths(self.root, escaped)
        with self.assertRaises(ValidationError):
            CloseoutQueueState.model_validate(
                {
                    "sprintTaskDocumentRef": SPRINT.model_dump(),
                    "revision": 0,
                    "serviceCondition": "rebuilding",
                    "updatedAt": NOW,
                }
            )

    def test_absent_invalidation_publishes_invalid_empty_and_reports_not_created(self) -> None:
        state, effect = self.store.invalidate(timestamp=NOW)
        self.assertEqual(effect.outcome, "not-created")
        self.assertEqual(state.serviceCondition, "invalid-empty")
        self.assertEqual(state.members, [])
        self.assertTrue(self.store.state_path.is_file())

    def test_existing_invalid_empty_is_idempotent(self) -> None:
        self.store.invalidate(timestamp=NOW)
        state, effect = self.store.invalidate(timestamp=NOW)
        self.assertEqual(effect.outcome, "already-empty")
        self.assertEqual(state.revision, 0)

    def test_malformed_artifact_is_recoverably_overwritten_without_legacy_parse(self) -> None:
        self._write("not-json")
        raw = self.store.read_raw(timestamp=NOW)
        self.assertEqual(raw.serviceCondition, "invalid-empty")
        self.assertEqual(raw.sourceProblems[0].kind, "projection")
        recovered, effect = self.store.invalidate(timestamp=NOW)
        self.assertEqual(effect.outcome, "recovered-malformed")
        self.assertEqual(recovered.sourceProblems, [])
        self.assertEqual(
            CloseoutQueueState.model_validate_json(
                self.store.state_path.read_text(encoding="utf-8")
            ),
            recovered,
        )

    def test_present_nonregular_artifact_is_not_reported_absent_and_is_replaced(self) -> None:
        self.store.state_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_target = self.root / "legacy-projection"
        legacy_target.write_text("not-json", encoding="utf-8")
        self.store.state_path.symlink_to(legacy_target)

        self.assertTrue(self.store.exists())
        raw = self.store.read_raw(timestamp=NOW)
        self.assertEqual(raw.serviceCondition, "invalid-empty")
        self.assertEqual(raw.sourceProblems[0].state, "unreadable")
        recovered, effect = self.store.invalidate(timestamp=NOW)

        self.assertEqual(effect.outcome, "recovered-malformed")
        self.assertFalse(self.store.state_path.is_symlink())
        self.assertEqual(recovered.serviceCondition, "invalid-empty")
        self.assertEqual(legacy_target.read_text(encoding="utf-8"), "not-json")

    def test_effective_read_preserves_artifact_diagnostic_and_refuses_source_mismatch(self) -> None:
        self._write("not-json")
        effective = self.store.read_effective(
            timestamp=NOW,
            source=_active_source(),
        )
        self.assertEqual(effective.serviceCondition, "invalid-empty")
        self.assertEqual(effective.sourceProblems[0].kind, "projection")

        self.store.invalidate(timestamp=NOW)
        published, outcome = self.store.publish_build(
            _build(),
            current_source=_active_source,
        )
        self.assertEqual(outcome.outcome, "published")
        mismatched = self.store.read_effective(
            timestamp=NOW,
            source=ProjectionSourceIdentity("d" * 64),
        )
        self.assertEqual(mismatched.serviceCondition, "invalid-empty")
        self.assertEqual(mismatched.members, [])
        self.assertEqual(mismatched.revision, published.revision)
        self.assertEqual(mismatched.sourceProblems[0].errorType, "source-fingerprint-mismatch")

    def test_stale_off_side_builder_never_publishes(self) -> None:
        self.store.invalidate(timestamp=NOW)
        state, outcome = self.store.publish_build(
            _build(),
            current_source=lambda: _active_source(fingerprint="d" * 64),
        )
        self.assertEqual(outcome.outcome, "source-changed")
        self.assertEqual(state.serviceCondition, "invalid-empty")
        self.assertFalse(self.store.build_path.exists())

    def test_source_unreadability_keeps_canonical_state_non_admitting(self) -> None:
        self.store.invalidate(timestamp=NOW)
        problem = ProjectionSourceProblem(
            kind="door",
            address="/door",
            state="unreadable",
            errorType="contract-unreadable",
            repairAction="repair the exact door",
        )
        state, outcome = self.store.publish_build(
            _build(),
            current_source=lambda: ProjectionSourceIdentity(None, (problem,)),
        )
        self.assertEqual(outcome.outcome, "source-unreadable")
        self.assertEqual(outcome.sourceProblems, [problem])
        self.assertEqual(state.serviceCondition, "invalid-empty")

    def test_terminal_empty_is_valid_built_not_a_third_condition(self) -> None:
        self.store.invalidate(timestamp=NOW)
        terminal = CloseoutProjectionBuild(
            sprintTaskDocumentRef=SPRINT,
            sourceFingerprint=HEX64,
            sourceClassification="terminal",
            members=[],
            builtAt=NOW,
        )
        state, outcome = self.store.publish_build(
            terminal,
            current_source=lambda: ProjectionSourceIdentity(
                HEX64,
                classification="terminal",
            ),
        )
        self.assertEqual(
            (state.serviceCondition, state.sourceClassification),
            ("valid-built", "terminal"),
        )
        self.assertEqual(state.members, [])
        self.assertEqual(outcome.memberCount, 0)

    def test_every_persisted_and_wire_collection_is_capped(self) -> None:
        build = _build().model_dump(mode="json")
        build["members"] = [_member().model_dump(mode="json")] * (MAX_CLOSEOUT_CANDIDATES + 1)
        with self.assertRaises(ValidationError):
            CloseoutProjectionBuild.model_validate(build)
        problems = [
            ProjectionSourceProblem(
                kind="task",
                address=f"task-{index}",
                state="missing",
                errorType="missing",
                repairAction="repair",
            )
            for index in range(MAX_CLOSEOUT_SOURCE_PROBLEMS)
        ]
        self.assertEqual(
            len(
                ProjectionRebuildResult(
                    outcome="source-unreadable",
                    sourceProblems=problems,
                ).sourceProblems
            ),
            MAX_CLOSEOUT_SOURCE_PROBLEMS,
        )
        with self.assertRaises(ValidationError):
            ProjectionRebuildResult(
                outcome="source-unreadable",
                sourceProblems=[*problems, problems[0]],
            )
