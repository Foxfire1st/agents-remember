"""A sync from the repository's default branch keeps the contract's base pair.

The leaf's own source line is ``sprint``; the repositories' default is ``main``. A sync with
``sync_source="repository-default"`` pulls ``main`` into the leaf, journals the choice and its
pins, appends one sync-log entry and leaves the base pair where it was. Resume and cancel read
the journal and its pins only; the live default is read again only on continue.
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import pytest
from agents_remember.application.landing_answer_shapes import project_landing_answer
from agents_remember.application.worktree_services import (
    bind_worktree_services,
    build_default_worktree_services,
)
from agents_remember.kernel.memory_attribution import render_memory_content_message
from agents_remember.models.worktree import SyncSource
from agents_remember.worktrees import sync_source_strategy
from agents_remember.worktrees.activation import (
    atomic_series_activation_transaction as series_transaction,
)
from agents_remember.worktrees.modules import sync as sync_module
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.services import reset_worktree_services
from agents_remember.worktrees.sync_transaction_authority import (
    require_pinned_authority,
    require_record_contract,
)
from agents_remember.worktrees.sync_transaction_completion import completed_sync_result
from agents_remember.worktrees.sync_transaction_git import SyncGitProofError
from agents_remember.worktrees.sync_transaction_state import (
    SyncOperationRecord,
    SyncOperationStore,
    sync_side_default_source_prefix,
)
from agents_remember.worktrees.worktree_contract import load_contract
from test_closeout_queue import QueueFixture
from test_sync_reapply_merge import sync_record
from test_worktree_sync import SyncFixture, commit_file, git

DEFAULT = "repository-default"


def _on_main(repo: Path, name: str, content: str) -> str:
    """Commit on ``main`` in the main checkout, then return to ``sprint``."""

    git(repo, "switch", "-q", "main")
    commit_file(repo, name, content)
    tip = git(repo, "rev-parse", "main")
    git(repo, "switch", "-q", "sprint")
    return tip


def _memory_on_main(repo: Path, code_tip: str) -> str:
    """Land attributed memory on ``main`` after it caught up with the converted ``sprint``."""

    git(repo, "branch", "-f", "main", "sprint")
    git(repo, "switch", "-q", "main")
    target = repo / "onboarding/src/main_only.py.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# main_only.py onboarding\n", encoding="utf-8")
    git(repo, "add", "onboarding/src/main_only.py.md")
    git(repo, "commit", "-m", render_memory_content_message("Onboard main_only.py", code_tip))
    tip = git(repo, "rev-parse", "main")
    git(repo, "switch", "-q", "sprint")
    return tip


def _default_refs(fixture: SyncFixture, side: str) -> list[str]:
    repo = fixture.code_repo if side == "code" else fixture.memory_repo
    prefix = sync_side_default_source_prefix(fixture.contract.contract_path, side)  # type: ignore[arg-type]
    listed = git(repo, "for-each-ref", "--format=%(refname)", prefix)
    return [line.removeprefix(prefix) for line in listed.splitlines() if line]


class RepositoryDefaultSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        bind_worktree_services(build_default_worktree_services())
        self.addCleanup(reset_worktree_services)

    def test_selector_accepts_only_the_repository_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp), source_branch="sprint")
            refused = fixture.sync(sync_source="sprint")  # type: ignore[arg-type]
            self.assertEqual(refused.payload["state"], "sync-input-invalid")
            self.assertEqual(refused.payload["invalidField"], "sync_source")

    def test_default_sync_pulls_main_keeps_the_pair_and_logs_the_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp), source_branch="sprint")
            before = load_contract(fixture.contract.contract_path)
            code_tip = _on_main(fixture.code_repo, "src/main_only.py", "VALUE = 'main'")
            memory_tip = _memory_on_main(fixture.memory_repo, code_tip)
            sprint = (
                git(fixture.code_repo, "rev-parse", "sprint"),
                git(fixture.memory_repo, "rev-parse", "sprint"),
            )

            preview = fixture.sync(sync_source=DEFAULT, dry_run=True)
            self.assertEqual(preview.payload["state"], "would-sync", preview.payload)
            self.assertEqual(preview.payload["syncSource"], DEFAULT)
            self.assertEqual(
                preview.payload["sources"],
                {
                    "code": {"branch": "main", "commit": code_tip},
                    "memory": {"branch": "main", "commit": memory_tip},
                },
            )

            # The public answer keeps the choice, the resolved sources and the parked-work omissions.
            answer = project_landing_answer(
                "worktree_sync", {**preview.payload, "omissions": {"omittedItems": 0}}
            )
            assert answer is not None
            self.assertEqual(
                (answer["syncSource"], answer["sources"], answer["omissions"]),
                (DEFAULT, preview.payload["sources"], {"omittedItems": 0}),
            )

            synced = fixture.sync(sync_source=DEFAULT, memory_sync_choice="merge-memory")

            self.assertEqual(synced.payload["state"], "synced", synced.payload)
            self.assertEqual(synced.payload["syncSource"], DEFAULT)
            worktree = fixture.contract.code_worktree
            git(worktree, "merge-base", "--is-ancestor", code_tip, "HEAD")
            assert fixture.contract.memory_worktree is not None
            git(fixture.contract.memory_worktree, "merge-base", "--is-ancestor", memory_tip, "HEAD")
            after = load_contract(fixture.contract.contract_path)
            self.assertEqual(
                (after.code_base_commit, after.memory_base_commit),
                (before.code_base_commit, before.memory_base_commit),
            )
            self.assertEqual(len(after.sync_log), 1)
            entry = after.sync_log[0]
            self.assertEqual(entry["syncSource"], DEFAULT)
            self.assertEqual(entry["codeBaseTo"], before.code_base_commit)
            self.assertEqual(
                (entry["codeSourceBranch"], entry["codeSourceCommit"]), ("main", code_tip)
            )
            self.assertEqual(
                (entry["memorySourceBranch"], entry["memorySourceCommit"]), ("main", memory_tip)
            )
            self.assertEqual(entry["codeHeadTo"], git(worktree, "rev-parse", "HEAD"))
            # The default is read, never written; the contract's own line is untouched.
            self.assertEqual(git(fixture.code_repo, "rev-parse", "main"), code_tip)
            self.assertEqual(git(fixture.memory_repo, "rev-parse", "main"), memory_tip)
            self.assertEqual(
                (
                    git(fixture.code_repo, "rev-parse", "sprint"),
                    git(fixture.memory_repo, "rev-parse", "sprint"),
                ),
                sprint,
            )
            self.assertEqual(_default_refs(fixture, "code"), [])

            # "Moved again" reads the recorded source: main moving is news, sprint moving is not.
            record = sync_record(fixture)
            commit_file(fixture.code_repo, "sprint_only.txt", "sprint moves")
            self.assertEqual(completed_sync_result(after, record, {}).payload["state"], "synced")
            _on_main(fixture.code_repo, "src/later.py", "VALUE = 'later'")
            moved = completed_sync_result(after, record, {}).payload
            self.assertEqual(moved["state"], "sync-pass-completed-source-moved-again")
            next_args = moved["nextArgs"]
            assert isinstance(next_args, dict)
            self.assertEqual(next_args["sync_source"], DEFAULT)

    def test_resume_and_cancel_read_the_journal_and_continue_reads_the_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp), source_branch="sprint")
            before = load_contract(fixture.contract.contract_path)
            worktree = fixture.contract.code_worktree
            commit_file(worktree, "README.md", "leaf version")
            pre_sync = git(worktree, "rev-parse", "HEAD")
            main_tip = _on_main(fixture.code_repo, "README.md", "main version")

            stopped = fixture.sync(sync_source=DEFAULT, memory_sync_choice="merge-memory")
            self.assertEqual(stopped.payload["state"], "sync-resolution-required", stopped.payload)
            record = sync_record(fixture)
            self.assertEqual(record.syncSource, DEFAULT)
            self.assertEqual((record.codeSourceFrom, record.memorySourceFrom), ("sprint", "sprint"))
            self.assertEqual(
                (record.code.sourceBranch, record.code.sourceCommit), ("main", main_tip)
            )
            self.assertEqual(_default_refs(fixture, "code"), ["main"])

            ordinary = record.model_copy(
                update={"syncSource": "", "codeSourceFrom": "", "memorySourceFrom": ""}
            )
            with self.assertRaises(SyncGitProofError):
                require_record_contract(load_contract(fixture.contract.contract_path), ordinary)

            # Identity: a retargeted contract refuses; a hand-edited branch has no pin.
            retargeted = replace(before, code_source_branch="elsewhere")
            with self.assertRaisesRegex(
                SyncGitProofError, "beside .* sprint, .* now names elsewhere"
            ):
                require_record_contract(retargeted, record)
            edited = record.model_copy(
                update={"code": record.code.model_copy(update={"sourceBranch": "other"})}
            )
            with self.assertRaises(SyncGitProofError):
                require_pinned_authority(edited)

            # Resume needs no default authority at all; continue reads it live.
            git(fixture.code_repo, "update-ref", "refs/remotes/origin/other", main_tip)
            git(
                fixture.code_repo,
                "symbolic-ref",
                "refs/remotes/origin/HEAD",
                "refs/remotes/origin/other",
            )
            git(fixture.code_repo, "branch", "other", main_tip)
            observed = fixture.sync(dry_run=True)
            self.assertEqual(observed.payload["state"], "sync-active", observed.payload)
            (worktree / "README.md").write_text("resolved\n", encoding="utf-8")
            git(worktree, "add", "README.md")
            moved = fixture.sync(resolution_action="continue")
            self.assertEqual(moved.payload["state"], "sync-source-moved", moved.payload)
            self.assertIn("now other", str(moved.payload["summary"]))
            # An unrecorded default is answered as a moved source, never raised. (Every sync
            # entry already requires the code default for its worktree check, so this is read
            # on the continue check itself.)
            git(fixture.code_repo, "symbolic-ref", "--delete", "refs/remotes/origin/HEAD")
            unreadable = sync_source_strategy.REPOSITORY_DEFAULT.moved_default(sync_record(fixture))
            self.assertIsNotNone(unreadable)
            self.assertIn("cannot be read", str(unreadable))
            git(
                fixture.code_repo,
                "symbolic-ref",
                "refs/remotes/origin/HEAD",
                "refs/remotes/origin/other",
            )

            # Cancel reads the journal and its pins: the moved default does not stop it.
            cancelled = fixture.sync(resolution_action="cancel")
            self.assertEqual(cancelled.payload["state"], "sync-cancelled", cancelled.payload)
            self.assertEqual(git(worktree, "rev-parse", "HEAD"), pre_sync)
            after = load_contract(fixture.contract.contract_path)
            self.assertEqual(after.sync_log, before.sync_log)
            self.assertEqual(after.code_base_commit, before.code_base_commit)
            self.assertEqual(_default_refs(fixture, "code"), [])
            self.assertEqual(git(fixture.code_repo, "rev-parse", "main"), main_tip)

    def test_an_admitted_ordinary_sync_cannot_be_continued_as_a_default_sync(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            commit_file(fixture.contract.code_worktree, "README.md", "leaf version")
            commit_file(fixture.code_repo, "README.md", "official version")
            stopped = fixture.sync(memory_sync_choice="merge-memory")
            self.assertEqual(stopped.payload["state"], "sync-resolution-required", stopped.payload)
            mismatch = fixture.sync(resolution_action="continue", sync_source=DEFAULT)
            self.assertEqual(mismatch.payload["state"], "sync-input-mismatch", mismatch.payload)

    def test_ref_recovery_rebuilds_the_recorded_default_branch_for_cancel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp), source_branch="sprint")
            worktree = fixture.contract.code_worktree
            commit_file(worktree, "README.md", "leaf version")
            pre_sync = git(worktree, "rev-parse", "HEAD")
            _on_main(fixture.code_repo, "README.md", "main version")
            stopped = fixture.sync(sync_source=DEFAULT, memory_sync_choice="merge-memory")
            self.assertEqual(stopped.payload["state"], "sync-resolution-required", stopped.payload)
            store = SyncOperationStore(fixture.contract.worktree_group)
            store.path.unlink()

            cancelled = fixture.sync(resolution_action="cancel")

            self.assertEqual(cancelled.payload["state"], "sync-cancelled", cancelled.payload)
            self.assertEqual(git(worktree, "rev-parse", "HEAD"), pre_sync)
            self.assertEqual(_default_refs(fixture, "code"), [])
            recovered = store.read()
            assert isinstance(recovered, SyncOperationRecord)
            self.assertEqual(recovered.syncSource, DEFAULT)
            self.assertEqual(recovered.code.sourceBranch, "main")


def test_series_early_check_judges_the_default_branch_on_a_default_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A series sync from the default branch judges the default branch's memory tip.

    The series' own source line moved ahead with unconverted memory, so an ordinary sync is
    refused before any write. The default branch already sits at the work tip; a default sync
    takes that branch, so the early check must not refuse it for the own line's movement.
    """

    fixture = QueueFixture(
        tmp_path / "world", atomic_b=True, memory_mode="external", converted=False
    )
    series = load_contract(fixture.tasks / "master-b" / "series-contract.md")
    assert series.memory_repo_path is not None
    memory = series.memory_repo_path
    scratch = memory.parent / "scratch-official"
    git(memory, "worktree", "add", "-q", scratch.as_posix(), series.memory_source_branch)
    (scratch / "official-note.md").write_text("# official\n", encoding="utf-8")
    git(scratch, "add", "official-note.md")
    git(scratch, "commit", "-q", "-m", "Advance the series' own source line")
    git(memory, "worktree", "remove", "--force", scratch.as_posix())
    git(memory, "branch", "default-line", series.memory_work_branch)
    monkeypatch.setattr(
        sync_source_strategy, "memory_repository_default_branch", lambda _repo: "default-line"
    )
    args = WorktreeArgs(contract_path=series.contract_path, memory_sync_choice="merge-memory")

    own_line = series_transaction._legacy_series_memory_refusal(series, args, {})
    assert own_line is not None and own_line.payload["state"] == "sync-unconverted-memory-locked"
    default_line = series_transaction._legacy_series_memory_refusal(
        series, replace(args, sync_source=DEFAULT), {}
    )
    assert default_line is None


def _series(tmp_path: Path):
    fixture = QueueFixture(tmp_path / "world", atomic_b=True, memory_mode="external")
    return load_contract(fixture.tasks / "master-b" / "series-contract.md")


@pytest.mark.parametrize("sync_source", [DEFAULT, None])
def test_a_contract_changed_retry_keeps_the_sync_source(
    tmp_path: Path, sync_source: SyncSource | None
) -> None:
    """Following the retry hint repeats the sync the caller asked for, from the same source.

    No journal exists yet when the contract changes during the source refresh, so the hint is
    the only carrier of the caller's choice; without it the retry would sync the own line.
    """

    series = _series(tmp_path)
    args = WorktreeArgs(contract_path=series.contract_path, sync_source=sync_source)
    stale = replace(series, memory_work_branch="a-branch-the-file-does-not-name")

    retry = sync_module._sync_live(stale, args, {})

    assert retry.payload["state"] == "sync-contract-changed-retry", retry.payload
    next_args = retry.payload["nextArgs"]
    assert isinstance(next_args, dict)
    assert next_args.get("sync_source") == sync_source


@pytest.mark.parametrize("sync_source", [DEFAULT, None])
def test_a_series_admission_refusal_retry_keeps_the_sync_source(
    tmp_path: Path, sync_source: SyncSource | None
) -> None:
    """A series admission refusal's retry arguments select the same source again."""

    series = _series(tmp_path)
    args = WorktreeArgs(contract_path=series.contract_path, sync_source=sync_source)
    request = series_transaction._AdmissionRefusalRequest(
        contract=series,
        args=args,
        operation="worktree_sync",
        status="atomic-series-contract-changed",
        detail="atomic-series contract changed while source evidence was refreshed",
    )

    refused = series_transaction._admission_refusal(request)

    assert refused.payload["nextTool"] == "worktree_sync"
    next_args = refused.payload["nextArgs"]
    assert isinstance(next_args, dict)
    assert next_args.get("sync_source") == sync_source


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
