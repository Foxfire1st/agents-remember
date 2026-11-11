"""Parked authoring a sync resolution drops is reported and kept.

A resolution that omits lines or files the parked candidate held stops the sync with an omission
report instead of settling; the parked stash stays until each omission is restored or reconciled
with evidence the sync re-measures. Split out of ``test_sync_parked_candidate`` to keep that
file from growing past the size rail.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.application.review_sync_rebinding import rebinding_result_block
from agents_remember.application.worktree_services import (
    bind_worktree_services,
    build_default_worktree_services,
)
from agents_remember.models.worktree import WorktreeSyncResponse
from agents_remember.worktrees.services import reset_worktree_services
from agents_remember.worktrees.sync_parked_omissions import measure
from agents_remember.worktrees.sync_transaction_git import _read as sync_git_read
from agents_remember.worktrees.sync_transaction_state import (
    SyncOperationStore,
)
from agents_remember.worktrees.worktree_contract import load_contract
from test_sync_reapply_merge import sync_record
from test_worktree_sync import SyncFixture, commit_file, git
from test_worktree_sync_knowledge_merge import Lines

CARD = {
    "id": "RLZ-D5Z9J7",
    "invariant": "INV-G841F9",
    "entries": [{"key": "one", "note": "parked prose"}],
    "history": [{"at": "2026-10-01", "note": "a parked history row"}],
}
PARKED_README = "work version\nparked alpha\nparked beta\n"


def _parked_card(worktree: Path) -> Path:
    card = worktree / "docs" / "card.json"
    card.parent.mkdir(parents=True, exist_ok=True)
    card.write_text(json.dumps(CARD, indent=2) + "\n", encoding="utf-8")
    return card


def _parked_conflict(tmp: str) -> tuple[SyncFixture, Path, Path]:
    fixture = SyncFixture(Path(tmp))
    worktree = fixture.contract.code_worktree
    (worktree / "README.md").write_text(PARKED_README, encoding="utf-8")
    card = _parked_card(worktree)
    commit_file(fixture.code_repo, "README.md", "official version")
    tip = git(fixture.code_repo, "rev-parse", "main")
    fixture.map_official_memory(tip)
    retained = fixture.sync()
    assert retained.payload["state"] == "sync-resolution-required", retained.payload
    return fixture, worktree, card


def _dropped_resolution(tmp: str) -> tuple[SyncFixture, Path, Path]:
    """A parked conflict whose resolution took the official side; the report is recorded."""

    fixture, worktree, card = _parked_conflict(tmp)
    (worktree / "README.md").write_text("official version\n", encoding="utf-8")
    git(worktree, "add", "README.md")
    card.unlink()
    stopped = fixture.sync(resolution_action="continue")
    assert stopped.payload["state"] == "sync-resolution-incomplete", stopped.payload
    return fixture, worktree, Path(_omissions(stopped.payload)["report"])


def _record_reconciliations(report: Path, path: str, records: list[dict[str, object]]) -> None:
    document = json.loads(report.read_text(encoding="utf-8"))
    for entry in document["entries"]:
        if entry["path"] == path:
            entry["reconciliations"] = records
    report.write_text(json.dumps(document, indent=2), encoding="utf-8")


def _omissions(payload: dict[str, object]) -> dict[str, Any]:
    """The answer's omission block, typed for the assertions below."""

    return cast("dict[str, Any]", payload["omissions"])


def _resolution(payload: dict[str, object]) -> dict[str, Any]:
    return cast("dict[str, Any]", payload["resolution"])


class ParkedOmissionCustodyTests(unittest.TestCase):
    """A resolution that drops parked authoring is reported and kept."""

    def setUp(self) -> None:
        bind_worktree_services(build_default_worktree_services())
        self.addCleanup(reset_worktree_services)

    def test_a_resolution_that_drops_parked_authoring_is_reported_and_kept(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture, worktree, card = _parked_conflict(tmp)
            tip = git(fixture.code_repo, "rev-parse", "main")
            (worktree / "README.md").write_text("official version\n", encoding="utf-8")
            git(worktree, "add", "README.md")
            card.unlink()

            stopped = fixture.sync(resolution_action="continue")

            self.assertEqual(stopped.returncode, 2)
            self.assertEqual(stopped.payload["state"], "sync-resolution-incomplete")
            omissions = _omissions(stopped.payload)
            self.assertEqual(omissions["side"], "code")
            self.assertEqual(omissions["parkedPaths"], 2)
            self.assertEqual(omissions["omittedPaths"], 2)
            self.assertEqual(omissions["omittedItems"], 4)
            report = Path(omissions["report"])
            self.assertTrue(report.is_file())
            document = json.loads(report.read_text(encoding="utf-8"))
            paths = {entry["path"] for entry in document["entries"]}
            self.assertEqual(paths, {"README.md", "docs/card.json"})
            readme = next(e for e in document["entries"] if e["path"] == "README.md")
            lines = {item["line"] for item in readme["omitted"]}
            self.assertEqual(lines, {"work version", "parked alpha", "parked beta"})
            summary = str(stopped.payload["summary"])
            self.assertIn("README.md", summary)
            self.assertIn("docs/card.json", summary)
            self.assertIn("only saved copy", summary)
            self.assertIn("resolution_action='continue'", summary)
            self.assertIn("resolution_action='cancel'", summary)

            record = sync_record(fixture)
            self.assertEqual(record.phase, "code-resolution-required")
            assert record.code is not None
            self.assertEqual(record.code.wipState, "restore-conflict")
            self.assertEqual(record.code.omissionReport, report.as_posix())
            self.assertNotEqual(git(worktree, "stash", "list"), "")
            self.assertNotEqual(load_contract(fixture.contract.contract_path).code_base_commit, tip)
            self.assertEqual(
                (worktree / "README.md").read_text(encoding="utf-8"), "official version\n"
            )
            self.assertFalse(card.exists())

            repeated = fixture.sync(resolution_action="continue")

            self.assertEqual(repeated.payload["state"], "sync-resolution-incomplete")
            self.assertEqual(_omissions(repeated.payload)["report"], report.as_posix())
            self.assertNotEqual(git(worktree, "stash", "list"), "")

            (worktree / "README.md").write_text(PARKED_README, encoding="utf-8")
            card.write_text(json.dumps(CARD, indent=2) + "\n", encoding="utf-8")
            landed = fixture.sync(resolution_action="continue")

            self.assertEqual(landed.payload["state"], "synced", landed.payload)
            self.assertEqual(git(worktree, "stash", "list"), "")
            self.assertEqual(load_contract(fixture.contract.contract_path).code_base_commit, tip)

    def test_recorded_reconciliation_evidence_is_remeasured_before_a_settle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture, worktree, card = _parked_conflict(tmp)
            (worktree / "README.md").write_text(
                "work version\nrenamed alpha\nrenamed beta\n", encoding="utf-8"
            )
            git(worktree, "add", "README.md")
            card.unlink()
            stopped = fixture.sync(resolution_action="continue")
            self.assertEqual(stopped.payload["state"], "sync-resolution-incomplete")
            report = Path(_omissions(stopped.payload)["report"])
            document = json.loads(report.read_text(encoding="utf-8"))
            for entry in document["entries"]:
                if entry["path"] == "README.md":
                    entry["reconciliations"] = [
                        {
                            "line": "parked alpha",
                            "replacement": {"path": "README.md", "line": "renamed alpha"},
                        },
                        {
                            "line": "parked beta",
                            "replacement": {"path": "README.md", "line": "renamed beta"},
                        },
                    ]
                else:
                    entry["reconciliations"] = [
                        {"line": "docs/card.json", "replacement": {"path": "docs/card.json"}}
                    ]
            card.write_text(json.dumps(CARD, indent=2) + "\n", encoding="utf-8")
            report.write_text(json.dumps(document, indent=2), encoding="utf-8")

            landed = fixture.sync(resolution_action="continue")

            self.assertEqual(landed.payload["state"], "synced", landed.payload)
            self.assertEqual(git(worktree, "stash", "list"), "")

    def test_a_bare_reconciled_label_is_no_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture, worktree, card = _parked_conflict(tmp)
            (worktree / "README.md").write_text("official version\n", encoding="utf-8")
            git(worktree, "add", "README.md")
            card.unlink()
            stopped = fixture.sync(resolution_action="continue")
            report = Path(_omissions(stopped.payload)["report"])
            document = json.loads(report.read_text(encoding="utf-8"))
            document["entries"][0]["reconciliations"] = [
                {"line": document["entries"][0]["omitted"][0]["line"], "reconciled": True}
            ]
            report.write_text(json.dumps(document, indent=2), encoding="utf-8")

            refused = fixture.sync(resolution_action="continue")

            self.assertEqual(refused.payload["state"], "sync-resolution-incomplete")
            self.assertEqual(_omissions(refused.payload)["reconciledItems"], 0)

    def test_preview_reports_omissions_without_writing_or_moving(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture, worktree, card = _parked_conflict(tmp)
            (worktree / "README.md").write_text("official version\n", encoding="utf-8")
            git(worktree, "add", "README.md")
            card.unlink()
            journal = SyncOperationStore(fixture.contract.worktree_group).path.read_bytes()

            preview = fixture.sync(resolution_action="continue", dry_run=True)

            self.assertEqual(preview.payload["state"], "sync-resolution-incomplete")
            omissions = _omissions(preview.payload)
            self.assertEqual(omissions["omittedPaths"], 2)
            self.assertFalse(Path(omissions["report"]).exists())
            self.assertEqual(
                SyncOperationStore(fixture.contract.worktree_group).path.read_bytes(), journal
            )
            self.assertNotEqual(git(worktree, "stash", "list"), "")

    def test_memory_omissions_are_reported_and_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            memory = fixture.contract.memory_worktree
            assert memory is not None
            lines = Lines(fixture=fixture, leaf=memory, line=fixture.memory_repo)
            lines.on_leaf({"onboarding/note.md": b"base prose\n"})
            lines.on_line({"onboarding/note.md": b"official prose\n"})
            (memory / "onboarding" / "note.md").write_text(
                "base prose\nparked memory alpha\n", encoding="utf-8"
            )
            first = fixture.sync(memory_sync_choice="merge-memory")
            self.assertEqual(first.payload["state"], "sync-resolution-required", first.payload)
            (memory / "onboarding" / "note.md").write_text("official prose\n", encoding="utf-8")
            git(memory, "add", "onboarding/note.md")
            merged = fixture.sync(resolution_action="continue")
            self.assertEqual(merged.payload["state"], "sync-resolution-required")
            self.assertTrue(_resolution(merged.payload)["wipRestore"])
            (memory / "onboarding" / "note.md").write_text("official prose\n", encoding="utf-8")
            git(memory, "add", "onboarding/note.md")

            stopped = fixture.sync(resolution_action="continue")

            self.assertEqual(stopped.payload["state"], "sync-resolution-incomplete")
            omissions = _omissions(stopped.payload)
            self.assertEqual(omissions["side"], "memory")
            self.assertEqual(omissions["omittedPaths"], 1)
            document = json.loads(Path(omissions["report"]).read_text(encoding="utf-8"))
            self.assertEqual(document["entries"][0]["path"], "onboarding/note.md")
            record = sync_record(fixture)
            self.assertEqual(record.phase, "memory-resolution-required")
            self.assertNotEqual(git(memory, "stash", "list"), "")

            (memory / "onboarding" / "note.md").write_text(
                "base prose\nparked memory alpha\n", encoding="utf-8"
            )
            settled = fixture.sync(resolution_action="continue")

            self.assertEqual(settled.payload["state"], "synced", settled.payload)
            self.assertEqual(git(memory, "stash", "list"), "")


class OmissionReportBoundaryTests(unittest.TestCase):
    """The owner-edited report is evidence input: it is never lost and never escapes the tree."""

    def setUp(self) -> None:
        bind_worktree_services(build_default_worktree_services())
        self.addCleanup(reset_worktree_services)

    def test_a_malformed_report_refuses_and_keeps_every_byte(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture, worktree, report = _dropped_resolution(tmp)
            _record_reconciliations(
                report,
                "README.md",
                [{"line": "parked alpha", "replacement": {"path": "README.md"}}],
            )
            broken = report.read_bytes()[:-3] + b",,}"
            report.write_bytes(broken)
            journal = SyncOperationStore(fixture.contract.worktree_group).path.read_bytes()

            for dry_run in (True, False):
                with self.subTest(dry_run=dry_run):
                    refused = fixture.sync(resolution_action="continue", dry_run=dry_run)

                    self.assertEqual(refused.returncode, 2)
                    self.assertEqual(refused.payload["state"], "sync-resolution-incomplete")
                    summary = str(refused.payload["summary"])
                    self.assertIn(report.as_posix(), summary)
                    self.assertIn("is not valid JSON", summary)
                    self.assertIn("fix the JSON in it, then run worktree_sync", summary)
                    self.assertIn("resolution_action='continue'", summary)
                    self.assertNotIn("omissions", refused.payload)
                    self.assertEqual(report.read_bytes(), broken)
                    self.assertEqual(
                        SyncOperationStore(fixture.contract.worktree_group).path.read_bytes(),
                        journal,
                    )
                    self.assertNotEqual(git(worktree, "stash", "list"), "")

    def test_a_replacement_outside_the_worktree_is_no_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture, worktree, report = _dropped_resolution(tmp)
            outside = worktree.parent / "outside.md"
            outside.write_text(PARKED_README, encoding="utf-8")
            relative = os.path.relpath(outside, worktree)
            self.assertTrue(relative.startswith(".."), relative)
            _record_reconciliations(
                report,
                "README.md",
                [
                    {"line": "work version", "replacement": {"path": relative}},
                    {
                        "line": "parked alpha",
                        "replacement": {"path": relative, "line": "parked alpha"},
                    },
                    {
                        "line": "parked beta",
                        "replacement": {"path": outside.as_posix(), "line": "parked beta"},
                    },
                ],
            )

            refused = fixture.sync(resolution_action="continue")

            self.assertEqual(refused.payload["state"], "sync-resolution-incomplete")
            self.assertEqual(_omissions(refused.payload)["reconciledItems"], 0)
            document = json.loads(report.read_text(encoding="utf-8"))
            readme = next(e for e in document["entries"] if e["path"] == "README.md")
            self.assertEqual(len(readme["remaining"]), 3)
            self.assertEqual(len(readme["reconciliations"]), 3)

    def test_blob_reads_do_not_grow_with_the_items_one_path_omits(self) -> None:
        reads: dict[int, int] = {}
        for size in (3, 12):
            with tempfile.TemporaryDirectory() as tmp:
                fixture = SyncFixture(Path(tmp))
                worktree = fixture.contract.code_worktree
                parked = [f"parked line {index}" for index in range(size)]
                (worktree / "README.md").write_text("\n".join(parked) + "\n", encoding="utf-8")
                commit_file(fixture.code_repo, "README.md", "official version")
                fixture.map_official_memory(git(fixture.code_repo, "rev-parse", "main"))
                retained = fixture.sync()
                self.assertEqual(retained.payload["state"], "sync-resolution-required")
                (worktree / "README.md").write_text("official version\n", encoding="utf-8")
                git(worktree, "add", "README.md")
                stopped = fixture.sync(resolution_action="continue")
                report = Path(_omissions(stopped.payload)["report"])
                _record_reconciliations(
                    report,
                    "README.md",
                    [{"line": line, "replacement": {"path": "README.md"}} for line in parked],
                )
                record = sync_record(fixture)
                assert record.code is not None
                counted = mock.Mock(wraps=sync_git_read.read_git_blob_bytes)

                with mock.patch.object(sync_git_read, "read_git_blob_bytes", counted):
                    measured = measure(record.code, fixture.contract, record)

                self.assertGreaterEqual(measured.omittedItems, size)
                reads[size] = counted.call_count
        self.assertGreater(reads[3], 0)
        self.assertEqual(reads[3], reads[12], reads)


class ReviewAdviceResolutionTests(unittest.TestCase):
    """Review advice names the resolution kind, not one blanket state."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        bind_worktree_services(build_default_worktree_services())
        self.addCleanup(reset_worktree_services)
        self.fixture = SyncFixture(Path(self._tmp.name))

    def _block(self, state: str, **extra: object) -> dict[str, object]:
        payload: dict[str, object] = {"operation": "worktree_sync", "state": state, **extra}
        block = rebinding_result_block(self.fixture.contract, payload)
        return dict(block["review_rebinding"])  # type: ignore[arg-type]

    def test_parked_retained_and_omissions_have_their_own_advice(self) -> None:
        parked = self._block("sync-resolution-required", resolution={"wipRestore": True})
        retained = self._block("sync-resolution-required", resolution={"files": ["README.md"]})
        omissions = self._block("sync-resolution-incomplete")

        self.assertEqual(parked["state"], "parked-reapplication")
        self.assertEqual(retained["state"], "retained-merge")
        self.assertEqual(omissions["state"], "omissions-awaiting-reconciliation")
        self.assertIn("parked authoring", str(omissions["detail"]))

    def test_a_resumed_reapply_with_a_recorded_report_awaits_reconciliation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture, _, report = _dropped_resolution(tmp)

            resumed = fixture.sync()
            block = rebinding_result_block(
                fixture.contract, {"operation": "worktree_sync", **resumed.payload}
            )

        self.assertEqual(resumed.payload["state"], "sync-resolution-required", resumed.payload)
        self.assertTrue(_resolution(resumed.payload)["wipRestore"])
        self.assertEqual(
            resumed.payload["omissions"],
            {"side": "code", "report": report.as_posix(), "omittedItems": 4},
        )
        answer = WorktreeSyncResponse.model_validate(
            {"operation": "worktree_sync", "ok": False, **resumed.payload}
        )
        assert answer.omissions is not None
        self.assertEqual(answer.omissions.report, report.as_posix())
        advice = cast("dict[str, Any]", block["review_rebinding"])

        self.assertEqual(advice["state"], "omissions-awaiting-reconciliation")
        self.assertIn("4 authored item(s)", str(advice["detail"]))
        self.assertIn(report.as_posix(), str(advice["detail"]))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
