"""The abandonment notice is published at the native boundary, once (MIK-R200 rule 5).

A successful abandon publishes one ``task-abandoned`` occurrence; a publication that refuses or
raises publishes none, and leaves no pending intent behind. The landing counterpart lives in
``test_record_landing.py`` (its own route's fixture), so this file covers the abandon boundary.

Both enclosure shapes are covered, because the subject differs between them and the queue must
name the right one (F05): on a series contract the master document *is* the task, and on a leaf
enclosure the notice names the leaf's own document -- never the master it is filed under.
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.controlplane.attention_notices import read_notice_store
from agents_remember.models.lifecycles.enclosure import (
    TerminalEnclosureArchive,
    TerminalWorktreeAbandonArguments,
)
from agents_remember.worktrees.abandon_task_publication import publish_abandoned_task
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    inspect_lifecycle_operation_locator,
)
from agents_remember.worktrees.integration.terminal_abandon_commit import (
    terminal_abandon_commit_record,
)
from agents_remember.worktrees.integration.terminal_enclosure_archive import (
    terminal_archive_required_result,
)
from agents_remember.worktrees.worktree_contract import (
    ContractCells,
    amend_contract,
    contract_publication_text,
    load_contract,
)
from lifecycle_enclosure_test_support import publish_test_enclosure
from test_record_landing import _fixture
from test_terminal_notice_subject import two_leaf_world


class _Guard:
    """A terminal guard that publishes exactly what it is handed, or raises."""

    def __init__(self, *, refuse: bool = False) -> None:
        self._refuse = refuse

    def preview(self) -> dict[str, object]:
        return {}

    def complete(self, *, outcome, publish, rollback_publish, commit=None):
        publish()
        if self._refuse:
            rollback_publish()
            raise RuntimeError("the abandonment publication was refused")
        if commit is not None:
            commit.publish()
        return {"outcome": outcome}


def _abandoned(contract):
    return amend_contract(contract, ContractCells(cleanup="abandoned"))


def _archived_enclosure(contract):
    """The terminal archive as the abandon route publishes it: before the guard, pre-success."""

    publish_test_enclosure(contract, contract_publication_text(contract.contract_path, contract))
    result = terminal_archive_required_result(
        load_contract(contract.contract_path),
        operation="worktree_abandon",
        arguments=TerminalWorktreeAbandonArguments(force=False),
        dry_run=False,
    )
    assert result.returncode == 0, result.payload
    locator = inspect_lifecycle_operation_locator(
        contract.coordination_root, contract.contract_path
    ).locator
    assert locator is not None and locator.terminalArchivePath is not None
    archive = TerminalEnclosureArchive.model_validate_json(
        Path(locator.terminalArchivePath).read_text(encoding="utf-8")
    )
    return locator, archive


class AbandonmentNoticeTests(unittest.TestCase):
    def _case_test_one_successful_abandon_publishes_one_notice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _fixture(Path(tmp))
            _archived_enclosure(contract)
            updated = _abandoned(contract)

            publish_abandoned_task(contract, updated, _Guard())

            store = read_notice_store(contract.coordination_root)
            assert store is not None
            self.assertEqual(len(store.notices), 1)
            self.assertEqual(store.notices[0].kind, "task-abandoned")
            # A series contract publishes the master document: here the master IS the task.
            self.assertEqual(store.notices[0].taskKey, "repo/master/task.json")
            self.assertEqual(store.pending, [])

    def _case_test_one_successful_abandon_of_a_leaf_names_that_leaf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = two_leaf_world(Path(tmp))["L2"]
            _archived_enclosure(contract)
            updated = _abandoned(contract)

            publish_abandoned_task(contract, updated, _Guard())

            store = read_notice_store(contract.coordination_root)
            assert store is not None
            self.assertEqual(len(store.notices), 1)
            self.assertEqual(store.notices[0].kind, "task-abandoned")
            self.assertEqual(store.notices[0].taskKey, "repo/master/2_leaf-two.json")
            self.assertEqual(store.notices[0].taskReference.path, "master/2_leaf-two.json")
            self.assertEqual(
                store.notices[0].occurrenceId, "task-abandoned:repo/master/2_leaf-two.json"
            )
            self.assertEqual(store.pending, [])

    def _case_test_a_failed_publication_leaves_no_notice_and_no_intent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _fixture(Path(tmp))
            _archived_enclosure(contract)
            updated = _abandoned(contract)

            with self.assertRaises(RuntimeError):
                publish_abandoned_task(contract, updated, _Guard(refuse=True))

            store = read_notice_store(contract.coordination_root)
            assert store is not None
            self.assertEqual((store.notices, store.pending), ([], []))

    def _case_test_a_failed_leaf_publication_leaves_no_notice_and_no_intent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = two_leaf_world(Path(tmp))["L2"]
            _archived_enclosure(contract)
            updated = _abandoned(contract)

            with self.assertRaises(RuntimeError):
                publish_abandoned_task(contract, updated, _Guard(refuse=True))

            store = read_notice_store(contract.coordination_root)
            assert store is not None
            self.assertEqual((store.notices, store.pending), ([], []))

    def _case_test_a_committed_abandonment_states_its_outcome_beside_the_archive(self) -> None:
        """L190-R1-F06: the archive is pre-success, so the outcome is stated after the commit."""

        with tempfile.TemporaryDirectory() as tmp:
            contract = _fixture(Path(tmp))
            locator, archive = _archived_enclosure(contract)
            updated = _abandoned(contract)
            # The archive cannot carry the outcome: it was published before the guard ran.
            self.assertNotEqual(
                archive.contractText,
                contract_publication_text(updated.contract_path, updated),
            )

            publish_abandoned_task(contract, updated, _Guard())

            record = terminal_abandon_commit_record(
                contract.coordination_root, locator.publicationRequestId
            )
            assert record is not None
            self.assertEqual(record.cleanupRequestId, archive.cleanupRequestId)
            self.assertEqual(
                record.committedContractSha256,
                hashlib.sha256(
                    contract_publication_text(updated.contract_path, updated).encode("utf-8")
                ).hexdigest(),
            )
            store = read_notice_store(contract.coordination_root)
            assert store is not None
            self.assertEqual([notice.kind for notice in store.notices], ["task-abandoned"])
            self.assertEqual(store.pending, [])

    def _case_test_a_refused_abandonment_states_no_outcome(self) -> None:
        """The state is written only after the guard committed, never by a refused publication."""

        with tempfile.TemporaryDirectory() as tmp:
            contract = _fixture(Path(tmp))
            locator, _ = _archived_enclosure(contract)

            with self.assertRaises(RuntimeError):
                publish_abandoned_task(contract, _abandoned(contract), _Guard(refuse=True))

            self.assertIsNone(
                terminal_abandon_commit_record(
                    contract.coordination_root, locator.publicationRequestId
                )
            )
            store = read_notice_store(contract.coordination_root)
            assert store is not None
            self.assertEqual((store.notices, store.pending), ([], []))

    def test_an_abandon_publication_writes_one_notice_or_none(self) -> None:
        """Rule 5: a successful abandonment publishes exactly one notice naming its own subject,
        and a refused or raised publication publishes none and leaves no intent behind."""

        for message, case in (
            (
                "one successful abandon publishes one notice",
                self._case_test_one_successful_abandon_publishes_one_notice,
            ),
            (
                "one successful abandon of a leaf names that leaf",
                self._case_test_one_successful_abandon_of_a_leaf_names_that_leaf,
            ),
            (
                "a failed publication leaves no notice and no intent",
                self._case_test_a_failed_publication_leaves_no_notice_and_no_intent,
            ),
            (
                "a failed leaf publication leaves no notice and no intent",
                self._case_test_a_failed_leaf_publication_leaves_no_notice_and_no_intent,
            ),
            (
                "a committed abandonment states its outcome beside the archive",
                self._case_test_a_committed_abandonment_states_its_outcome_beside_the_archive,
            ),
            (
                "a refused abandonment states no outcome",
                self._case_test_a_refused_abandonment_states_no_outcome,
            ),
        ):
            with self.subTest(msg=message):
                case()
