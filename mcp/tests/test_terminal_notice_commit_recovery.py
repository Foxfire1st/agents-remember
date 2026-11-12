"""Native terminal commit cuts preserve exact archive-backed occurrences."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from agents_remember.controlplane.attention_notices import read_notice_store
from agents_remember.kernel.atomic_write import atomic_write_text
from agents_remember.memory_quality.style.citations.source_index_cache import (
    TerminalNamespaceGuard,
    contract_cache_authority,
)
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.worktrees import abandon_task_publication
from agents_remember.worktrees.abandon_task_publication import publish_abandoned_task
from agents_remember.worktrees.attention_terminal_notices import begin_abandoned_notice
from agents_remember.worktrees.integration.direct_landing.direct_landing_operation import (
    DirectLandingRuntime,
    direct_landing_store,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    inspect_lifecycle_operation_locator,
)
from agents_remember.worktrees.integration.terminal_abandon_commit import (
    terminal_abandon_commit_record,
    terminal_abandon_commit_record_path,
)
from agents_remember.worktrees.modules.terminal_inventory import terminal_without_remains_reason
from agents_remember.worktrees.terminal_notice_recovery import NoticeVerdict, resolve_pending_notice
from agents_remember.worktrees.worktree_contract import (
    ContractCells,
    amend_contract,
    contract_publication_text,
    load_contract,
    write_contract,
)
from test_attention_notice_publication import _archived_enclosure as archive_before_the_guard
from test_enclosure_without_remains import _leafless_enclosure
from test_terminal_notice_recovery import (
    pending,
    published,
    settle,
)
from test_terminal_notice_subject import (
    CODE_COMMIT,
    finish_direct_generation,
    running_direct_generation,
    series_world,
    two_leaf_world,
)


class _ProcessCut(BaseException):
    """The process disappears after the guard's durable commit and before notice confirmation."""


class _CutGuard(TerminalNamespaceGuard):
    @staticmethod
    def _cleanup_retired_namespace(retired, result):
        del retired, result
        raise _ProcessCut


def _without_remains_world(root: Path):
    contract = load_contract(_leafless_enclosure(root, integrated=False))
    for fields in (
        {
            "id": contract.task_name,
            "slug": "task",
            "kind": "master",
            "subTasks": [
                {
                    "number": contract.leaf_id,
                    "name": "Leaf",
                    "file": "leaf.md",
                    "status": "inProgress",
                }
            ],
        },
        {"id": contract.leaf_id, "slug": "leaf", "kind": "subTask", "master": "task.md"},
    ):
        write_task_doc(
            contract.task_root,
            TaskDocument.model_validate(
                {
                    "repo": contract.repo_name,
                    "title": "Fixture",
                    "createdAt": "2026-10-15T00:00:00Z",
                    "status": "inProgress",
                    **fields,
                }
            ),
        )
    assert terminal_without_remains_reason(contract, operation="worktree_abandon") is not None
    assert (
        inspect_lifecycle_operation_locator(
            contract.coordination_root, contract.contract_path
        ).state
        == "missing"
    )
    return contract


class TerminalCommitCutTests(unittest.TestCase):
    def _no_remains_success_and_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _without_remains_world(Path(tmp))
            updated = amend_contract(
                contract,
                ContractCells(
                    cleanup="abandoned",
                    terminal_note=terminal_without_remains_reason(
                        contract, operation="worktree_abandon"
                    ),
                ),
            )
            publish_abandoned_task(contract, updated, TerminalNamespaceGuard(None, None, None))
            path = contract.contract_path.with_suffix(".abandon-committed.json")
            before = path.read_bytes()
            record = json.loads(before)
            self.assertEqual(
                set(record),
                {
                    "schemaVersion",
                    "authorityKind",
                    "contractPath",
                    "lifecycleId",
                    "committedContractSha256",
                    "terminalNote",
                },
            )
            self.assertEqual(record["authorityKind"], "no-remains")
            self.assertEqual(record["contractPath"], contract.contract_path.as_posix())
            self.assertEqual(record["lifecycleId"], contract.lifecycle_id)
            self.assertEqual(record["terminalNote"], updated.terminal_note)
            self.assertEqual(
                record["committedContractSha256"],
                hashlib.sha256(
                    contract_publication_text(updated.contract_path, updated).encode()
                ).hexdigest(),
            )
            self.assertLessEqual(len(before), 16 * 1024)
            self.assertEqual(len(published(contract)), 1)
            publish_abandoned_task(updated, updated, TerminalNamespaceGuard(None, None, None))
            settle(updated)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(len(list(path.parent.glob("*.abandon-committed.json"))), 1)
            self.assertEqual(len(published(contract)), 1)
            self.assertEqual(pending(contract), [])
            self.assertFalse(contract.worktree_group.exists())
            self.assertEqual(
                inspect_lifecycle_operation_locator(
                    contract.coordination_root, contract.contract_path
                ).state,
                "missing",
            )

    def _no_remains_failure_rolls_back(self) -> None:
        for failure in ("contract", "receipt"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                contract = _without_remains_world(Path(tmp))
                updated = amend_contract(contract, ContractCells(cleanup="abandoned"))
                paths = [
                    contract.contract_path,
                    *(
                        contract.task_root / name
                        for name in ("task.json", "task.md", "leaf.json", "leaf.md")
                    ),
                ]
                before = {path: path.read_bytes() for path in paths}
                native_write = abandon_task_publication.write_contract

                def failed_contract(path, candidate, _native_write=native_write):
                    if candidate.cleanup == "abandoned":
                        raise OSError("injected contract publication failure")
                    _native_write(path, candidate)

                def failed_receipt(path, text, _contract=contract):
                    atomic_write_text(path, text)
                    verdict = resolve_pending_notice(
                        _contract.coordination_root, pending(_contract)[0]
                    )
                    self.assertIs(verdict.verdict, NoticeVerdict.UNDECIDED)
                    raise OSError("injected receipt publication failure")

                target = (
                    "agents_remember.worktrees.abandon_task_publication.write_contract"
                    if failure == "contract"
                    else "agents_remember.worktrees.integration.terminal_abandon_commit.atomic_write_text"
                )
                with (
                    patch(
                        target,
                        side_effect=failed_contract if failure == "contract" else failed_receipt,
                    ),
                    self.assertRaises(OSError),
                ):
                    publish_abandoned_task(
                        contract, updated, TerminalNamespaceGuard(None, None, None)
                    )
                self.assertEqual({path: path.read_bytes() for path in paths}, before)
                self.assertFalse(
                    contract.contract_path.with_suffix(".abandon-committed.json").exists()
                )
                self.assertEqual((pending(contract), published(contract)), ([], []))
                publish_abandoned_task(contract, updated, TerminalNamespaceGuard(None, None, None))
                self.assertEqual(len(published(contract)), 1)

    def _no_remains_commit_before_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _without_remains_world(Path(tmp))
            updated = amend_contract(contract, ContractCells(cleanup="abandoned"))
            guard = TerminalNamespaceGuard(None, None, None)
            with (
                patch(
                    "agents_remember.worktrees.abandon_task_publication.confirm_notice",
                    side_effect=_ProcessCut,
                ),
                self.assertRaises(_ProcessCut),
            ):
                publish_abandoned_task(contract, updated, guard)
            self.assertTrue(guard.completed)
            self.assertEqual(load_contract(contract.contract_path), updated)
            self.assertEqual(len(pending(contract)), 1)
            self.assertEqual(published(contract), [])
            notice = pending(contract)[0]
            path = contract.contract_path.with_suffix(".abandon-committed.json")
            before = path.read_bytes()
            record = json.loads(before)
            for field, value in (
                ("contractPath", str(contract.contract_path.with_name("another.md"))),
                ("lifecycleId", "another-lifecycle"),
                ("committedContractSha256", "f" * 64),
                ("terminalNote", "another note"),
                ("authorityKind", "archive"),
            ):
                with self.subTest(field=field):
                    path.write_text(json.dumps({**record, field: value}))
                    self.assertIs(
                        resolve_pending_notice(contract.coordination_root, notice).verdict,
                        NoticeVerdict.UNDECIDED,
                    )
                    settle(contract)
                    self.assertEqual((pending(contract), published(contract)), ([notice], []))
            path.write_text(
                json.dumps({key: value for key, value in record.items() if key != "authorityKind"})
            )
            self.assertIs(
                resolve_pending_notice(contract.coordination_root, notice).verdict,
                NoticeVerdict.UNDECIDED,
            )
            path.write_bytes(before + b" " * (16 * 1024))
            self.assertIs(
                resolve_pending_notice(contract.coordination_root, notice).verdict,
                NoticeVerdict.UNDECIDED,
            )
            path.unlink()
            self.assertIs(
                resolve_pending_notice(contract.coordination_root, notice).verdict,
                NoticeVerdict.UNDECIDED,
            )
            path.write_bytes(before)
            settle(contract)
            settle(contract)
            self.assertEqual((pending(contract), published(contract)), ([], [notice.occurrenceId]))
            self.assertFalse(contract.worktree_group.exists())
            self.assertEqual(
                inspect_lifecycle_operation_locator(
                    contract.coordination_root, contract.contract_path
                ).state,
                "missing",
            )

    def test_unreadable_current_generation_keeps_its_pending_notice(self) -> None:
        """R5 property B: a retained predecessor cannot refute an unreadable exact owner."""

        with tempfile.TemporaryDirectory() as tmp:
            contract, leaves = series_world(Path(tmp))
            store = direct_landing_store(contract)
            seed, _created = store.create(running_direct_generation(contract, leaves["L1"]))
            DirectLandingRuntime(contract, seed).cancel_unpublished(
                status="refused", detail="retry"
            )
            current = store.replace_terminal(seed)
            self.assertEqual(current.generation, 2)
            predecessor = store.path.with_name(
                "direct-landing-operation.legacy-missing-intent-generation-1.json"
            )
            self.assertTrue(predecessor.is_file())
            with (
                patch(
                    "agents_remember.worktrees.integration.direct_landing.direct_landing_operation.confirm_notice",
                    side_effect=_ProcessCut,
                ),
                self.assertRaises(_ProcessCut),
            ):
                DirectLandingRuntime(contract, current).finish(
                    {"codeCommit": CODE_COMMIT, "memoryContentCommit": "b" * 40}
                )
            notice = pending(contract)[0]
            read_text = Path.read_text

            def selective_read(path: Path, *args, **kwargs):
                if path == store.path:
                    raise PermissionError("current generation temporarily unreadable")
                return read_text(path, *args, **kwargs)

            with patch.object(Path, "read_text", selective_read):
                self.assertTrue(predecessor.read_text(encoding="utf-8"))
                for row in (notice, notice.model_copy(update={"root": None})):
                    verdict = resolve_pending_notice(contract.coordination_root, row)
                    self.assertEqual(
                        (verdict.verdict, verdict.proved, verdict.record),
                        (NoticeVerdict.UNDECIDED, None, "direct-landing journal"),
                    )
                settle(contract)
                self.assertEqual(pending(contract), [notice])
                self.assertEqual(published(contract), [])

            settle(contract)
            settle(contract)
            self.assertEqual(pending(contract), [])
            self.assertEqual(published(contract), [notice.occurrenceId])

    def _abandonment_after_native_commit(self, terminal_note: str | None = None) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = two_leaf_world(Path(tmp))["L2"]
            contract = replace(contract, lifecycle_id="native-terminal-fixture")
            write_contract(contract.contract_path, contract)
            archive_before_the_guard(contract)
            contract = load_contract(contract.contract_path)
            updated = amend_contract(
                contract, ContractCells(cleanup="abandoned", terminal_note=terminal_note)
            )
            guard = _CutGuard(contract_cache_authority(contract), None, None)

            with self.assertRaises(_ProcessCut):
                publish_abandoned_task(contract, updated, guard)

            self.assertTrue(guard.completed)
            self.assertEqual(load_contract(contract.contract_path), updated)
            self.assertEqual(len(pending(contract)), 1)
            occurrence = pending(contract)[0].occurrenceId
            shutil.rmtree(contract.worktree_group)
            wrong_root = pending(contract)[0].model_copy(
                update={"root": contract.worktree_group.with_name("another-enclosure").as_posix()}
            )
            self.assertIs(
                resolve_pending_notice(contract.coordination_root, wrong_root).verdict,
                NoticeVerdict.REFUTED,
            )
            settle(contract)
            settle(contract)
            self.assertEqual(published(contract), [occurrence])
            self.assertEqual(pending(contract), [])

    def _native_failure_rolls_back(self, *, receipt_failure: bool) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = replace(
                two_leaf_world(Path(tmp))["L2"], lifecycle_id="native-terminal-fixture"
            )
            write_contract(contract.contract_path, contract)
            locator, _ = archive_before_the_guard(contract)
            authority = contract_cache_authority(contract)
            assert authority is not None
            authority.namespace.mkdir(parents=True)
            (authority.namespace / "retained").write_text("cache")
            guard = TerminalNamespaceGuard(authority, None, None)

            def failed_receipt(path, text):
                # The rename happened, but a later write failure must remain rollbackable and
                # invisible to an interleaved observer until this transaction exits.
                atomic_write_text(path, text)
                verdict = resolve_pending_notice(contract.coordination_root, pending(contract)[0])
                self.assertIs(verdict.verdict, NoticeVerdict.UNDECIDED)
                raise OSError("receipt directory flush failed")

            target = (
                "agents_remember.worktrees.integration.terminal_abandon_commit.atomic_write_text"
                if receipt_failure
                else "agents_remember.memory_quality.style.citations.source_index_cache._write_control_state"
            )
            failure: Callable[..., None] | OSError = (
                failed_receipt if receipt_failure else OSError("native guard refused before commit")
            )
            with patch(target, side_effect=failure), self.assertRaises(OSError):
                publish_abandoned_task(
                    contract, amend_contract(contract, ContractCells(cleanup="abandoned")), guard
                )

            self.assertFalse(guard.completed)
            self.assertEqual(load_contract(contract.contract_path), contract)
            self.assertEqual((authority.namespace / "retained").read_text(), "cache")
            self.assertIsNone(
                terminal_abandon_commit_record(
                    contract.coordination_root, locator.publicationRequestId
                )
            )
            self.assertEqual(published(contract), [])
            self.assertEqual(pending(contract), [])

    def _notice_failure_does_not_govern_native_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = two_leaf_world(Path(tmp))["L2"]
            locator, _ = archive_before_the_guard(contract)
            guard = TerminalNamespaceGuard(None, None, None)
            updated = amend_contract(contract, ContractCells(cleanup="abandoned"))
            with patch(
                "agents_remember.controlplane.attention_notices.atomic_write_text",
                side_effect=OSError("notice store is unavailable"),
            ):
                publish_abandoned_task(contract, updated, guard)

            self.assertTrue(guard.completed)
            self.assertEqual(load_contract(contract.contract_path), updated)
            self.assertIsNotNone(
                terminal_abandon_commit_record(
                    contract.coordination_root, locator.publicationRequestId
                )
            )
            store = read_notice_store(contract.coordination_root)
            assert store is not None
            self.assertEqual((store.notices, store.pending), ([], []))

    def _pre_success_archive_keeps_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = two_leaf_world(Path(tmp))["L2"]
            occurrence = begin_abandoned_notice(contract)
            archive_before_the_guard(contract)
            write_contract(
                contract.contract_path, amend_contract(contract, ContractCells(cleanup="abandoned"))
            )
            settle(contract)
            self.assertEqual(published(contract), [])
            self.assertEqual([row.occurrenceId for row in pending(contract)], [occurrence])

    def _only_the_exact_outcome_receipt_proves_native_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = two_leaf_world(Path(tmp))["L2"]
            locator, _ = archive_before_the_guard(contract)
            with (
                patch(
                    "agents_remember.worktrees.abandon_task_publication.confirm_notice",
                    side_effect=_ProcessCut,
                ),
                self.assertRaises(_ProcessCut),
            ):
                publish_abandoned_task(
                    contract,
                    amend_contract(contract, ContractCells(cleanup="abandoned")),
                    TerminalNamespaceGuard(None, None, None),
                )
            record = terminal_abandon_commit_record(
                contract.coordination_root, locator.publicationRequestId
            )
            assert record is not None
            path = terminal_abandon_commit_record_path(
                contract.coordination_root, locator.publicationRequestId
            )
            for mismatch in (
                {"cleanupRequestId": "f" * 64},
                {"contractPath": contract.contract_path.with_name("another.md").as_posix()},
                {"committedContractSha256": "f" * 64},
                {"terminalNote": "another terminal note"},
            ):
                atomic_write_text(path, record.model_copy(update=mismatch).model_dump_json())
                verdict = resolve_pending_notice(contract.coordination_root, pending(contract)[0])
                self.assertIs(verdict.verdict, NoticeVerdict.UNDECIDED)
                settle(contract)
                self.assertEqual(published(contract), [])
                self.assertEqual(len(pending(contract)), 1)
            atomic_write_text(path, record.model_dump_json())
            settle(contract)
            self.assertEqual(len(published(contract)), 1)

    def test_native_outcome_and_best_effort_notice_boundaries(self) -> None:
        for message, case in (
            (
                "a true no-remains publication or receipt failure restores task, master and contract",
                self._no_remains_failure_rolls_back,
            ),
            (
                "a native precommit failure restores contract, cache and pending notice",
                lambda: self._native_failure_rolls_back(receipt_failure=False),
            ),
            (
                "a receipt failure after rename remains excluded and restores precommit state",
                lambda: self._native_failure_rolls_back(receipt_failure=True),
            ),
            (
                "a notice write failure does not roll back native success",
                self._notice_failure_does_not_govern_native_success,
            ),
            (
                "an archive written before native success retains the pending intent",
                self._pre_success_archive_keeps_pending,
            ),
            (
                "a receipt must name the exact archive generation, contract, digest and note",
                self._only_the_exact_outcome_receipt_proves_native_success,
            ),
        ):
            with self.subTest(msg=message):
                case()

    def _series_generation_after_collection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract, leaves = series_world(Path(tmp))
            with (
                patch(
                    "agents_remember.worktrees.integration.direct_landing.direct_landing_operation.confirm_notice",
                    side_effect=_ProcessCut,
                ),
                self.assertRaises(_ProcessCut),
            ):
                finish_direct_generation(contract, leaves["L1"])
            notice = pending(contract)[0]
            archive_before_the_guard(contract)
            shutil.rmtree(contract.worktree_group)

            verdict = resolve_pending_notice(contract.coordination_root, notice)

            self.assertIs(verdict.verdict, NoticeVerdict.PUBLISHED)
            assert notice.commit is not None and notice.operationKey is not None
            wrong_commit = "f" * 40
            for updates, expected in (
                (
                    {
                        "commit": wrong_commit,
                        "occurrenceId": notice.occurrenceId.replace(notice.commit, wrong_commit),
                    },
                    NoticeVerdict.REFUTED,
                ),
                (
                    {
                        "operationKey": notice.operationKey.removesuffix(":g1") + ":g2",
                        "operationGeneration": 2,
                        "occurrenceId": notice.occurrenceId.removesuffix(":g1") + ":g2",
                    },
                    NoticeVerdict.REFUTED,
                ),
                ({"repository": "another-repository"}, NoticeVerdict.REFUTED),
                (
                    {"root": contract.worktree_group.with_name("another-enclosure").as_posix()},
                    NoticeVerdict.UNDECIDED,
                ),
            ):
                self.assertIs(
                    resolve_pending_notice(
                        contract.coordination_root, notice.model_copy(update=updates)
                    ).verdict,
                    expected,
                )
            settle(contract)
            settle(contract)
            self.assertEqual(published(contract), [notice.occurrenceId])
            self.assertEqual(pending(contract), [])

    def test_exact_native_commit_crash_matrix(self) -> None:
        for message, case in (
            (
                "a true no-remains commit and retry publish exactly one occurrence",
                self._no_remains_success_and_retry,
            ),
            (
                "a true no-remains commit before confirmation recovers only its exact typed receipt",
                self._no_remains_commit_before_confirmation,
            ),
            (
                "abandonment survives a native commit before confirmation",
                self._abandonment_after_native_commit,
            ),
            (
                "the committed terminal note is included in exact recovery bytes",
                lambda: self._abandonment_after_native_commit("terminal-without-remains"),
            ),
            (
                "the series journal survives collection even when the leaf owns no enclosure",
                self._series_generation_after_collection,
            ),
        ):
            with self.subTest(msg=message):
                case()
