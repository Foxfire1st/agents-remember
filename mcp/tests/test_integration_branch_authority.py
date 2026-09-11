"""Protected integration refs and their plane-owned mutation capability."""

from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.kernel.memory_ledger import (
    LedgerRow,
    MemoryLedger,
    load_ledger,
    parse_ledger_text,
    prepend_mapping,
    write_ledger,
)
from agents_remember.models.lifecycles.operation import (
    IntegrateOperationInput,
)
from agents_remember.worktrees.integration import (
    integration_ref_transaction,
)
from agents_remember.worktrees.integration.integration_branch_authority import (
    require_ordinary_worktree,
)
from agents_remember.worktrees.integration.integration_ref_transaction import (
    IntegratedCommits,
    IntegrationRefRace,
    merge_integrated_commits,
    prepare_integration_ref_move,
    require_integrated_ledger_mapping,
)
from agents_remember.worktrees.integration.lifecycle import lifecycle_operations
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_store import (
    LifecycleOperationStore,
    operation_record_path,
)
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.integrate import (
    IntegrationSources,
)
from integration_branch_authority_test_support import (
    _authority_fixture,
    _closed_external_leaf_worktrees,
)
from test_source_lineage import _commit_on, _git


def _ledger_with_rows(ledger: MemoryLedger, rows: list[LedgerRow]) -> MemoryLedger:
    """A ledger whose exact rows are rewritten; the metadata follows the newest row."""

    return replace(
        ledger,
        rows=rows,
        last_verified_code_commit=rows[0].code_commit,
        last_memory_content_commit=rows[0].memory_commit,
    )


def _commit_ledger(
    memory_worktree: Path, ledger_path: Path, ledger: MemoryLedger, message: str
) -> str:
    write_ledger(ledger_path, ledger)
    _git(memory_worktree, "add", "memory.md")
    _git(memory_worktree, "commit", "-m", message)
    return _git(memory_worktree, "rev-parse", "HEAD")


def _reclosed_leaf_memory_history(root: Path):
    """One closed leaf whose memory work branch then re-closed out after its parent moved.

    Returns ``(closed, memory_source, source_rows, second_memory, ledger_commit, ledger)``:
    the contract as the second closeout left it, the exact memory source commit it must stay
    based on, that source's ledger rows, and the ledger whose two added rows are both true.
    """

    fixture = _authority_fixture(root, external_memory=True)
    closed = _closed_external_leaf_worktrees(fixture, root, publish_closeout_evidence=False)
    memory_repo = closed.memory_repo_path
    memory_worktree = closed.memory_worktree
    assert memory_repo is not None and memory_worktree is not None
    assert closed.ledger_path is not None
    source = _git(memory_repo, "rev-parse", closed.memory_source_branch)
    source_rows = parse_ledger_text(_git(memory_repo, "show", f"{source}:memory.md")).rows
    # The second closeout's own memory content, on top of the first one's ledger.
    work_branch = _git(memory_worktree, "rev-parse", "--abbrev-ref", "HEAD")
    _commit_on(memory_worktree, work_branch, "second-content.md")
    second_memory = _git(memory_worktree, "rev-parse", "HEAD")
    accumulated = prepend_mapping(
        load_ledger(closed.ledger_path),
        closed.code_commit,
        second_memory,
    )
    ledger_commit = _commit_ledger(
        memory_worktree,
        closed.ledger_path,
        accumulated,
        "Record the re-closeout mapping",
    )
    return closed, source, source_rows, second_memory, ledger_commit, accumulated


class IntegrationBranchAuthorityTests(unittest.TestCase):
    def test_branch_alias_nested_checkout_and_memory_name_cannot_bypass_refusal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _authority_fixture(Path(tmp), external_memory=True)
            code = fixture.code_repo
            memory = fixture.leaf_contract.memory_repo_path
            assert memory is not None
            _git(code, "symbolic-ref", "refs/heads/alias-master", "refs/heads/ar/master")
            nested = code / "nested"
            nested.mkdir()
            cases = (
                replace(fixture.leaf_contract, code_work_branch="refs/heads/super"),
                replace(fixture.leaf_contract, code_work_branch="alias-master"),
                replace(
                    fixture.leaf_contract,
                    code_repo_path=nested,
                    code_work_branch="ar/atomic-two",
                ),
                replace(fixture.leaf_contract, memory_work_branch="main"),
            )
            for candidate in cases:
                with (
                    self.subTest(candidate=candidate),
                    self.assertRaisesRegex(RuntimeError, "integration-branch-is-not-a-workbench"),
                ):
                    require_ordinary_worktree(candidate, operation="test")

    def test_external_pair_cas_retains_torn_pair_without_clobbering_memory_race(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _authority_fixture(root, external_memory=True)
            closed = _closed_external_leaf_worktrees(fixture, root)
            memory_repo = closed.memory_repo_path
            assert memory_repo is not None
            operation_input = IntegrateOperationInput(
                configPath=fixture.config_path.as_posix(),
                contractPath=closed.contract_path.as_posix(),
            )
            lifecycle_operations.start_or_observe_operation(
                operation_input,
                closed,
                launcher=lambda *_: None,
            )
            store = LifecycleOperationStore(
                operation_record_path(closed.worktree_group, "integrate")
            )
            running = store.read()
            assert running is not None
            authority = running.integrationAuthority
            assert authority is not None
            _git(memory_repo, "branch", "memory-race", "ar/master")
            _commit_on(memory_repo, "memory-race", "parallel-memory.txt")
            raced_memory = _git(memory_repo, "rev-parse", "memory-race")
            original_cas = integration_ref_transaction._compare_and_swap_ref

            def race_memory(
                repo: Path,
                branch: str,
                expected: str,
                target: str,
                *,
                authority: object | None = None,
            ) -> bool:
                if repo == memory_repo and branch == "ar/master":
                    _git(
                        memory_repo,
                        "update-ref",
                        "refs/heads/ar/master",
                        raced_memory,
                        expected,
                    )
                return original_cas(
                    repo,
                    branch,
                    expected,
                    target,
                    authority=authority,
                )

            with (
                mock.patch.object(
                    integration_ref_transaction,
                    "_compare_and_swap_ref",
                    side_effect=race_memory,
                ),
                self.assertRaises(IntegrationRefRace) as raised,
            ):
                commits = IntegratedCommits(
                    code=closed.code_commit,
                    memory_content=closed.memory_content_commit,
                    ledger=closed.ledger_commit,
                )
                snapshot = prepare_integration_ref_move(
                    closed,
                    commits,
                    WorktreeArgs(operation_key=running.operationKey),
                    IntegrationSources(
                        current_code_source=authority.codeSourceCommit,
                        current_memory_source=authority.memorySourceCommit,
                        code_replay_required=False,
                        memory_replay_required=False,
                    ),
                )
                merge_integrated_commits(closed, commits, snapshot)
            expected = raised.exception.expected
            self.assertEqual(
                expected["before"],
                {"codeRef": authority.codeSourceCommit, "memoryRef": authority.memorySourceCommit},
            )
            self.assertEqual(
                expected["intended"],
                {"codeRef": closed.code_commit, "memoryRef": closed.ledger_commit},
            )
            self.assertEqual(raised.exception.observed, {})
            self.assertEqual(
                _git(fixture.code_repo, "rev-parse", "ar/master"),
                closed.code_commit,
            )
            self.assertEqual(_git(memory_repo, "rev-parse", "ar/master"), raced_memory)

    def test_ledger_keeps_every_true_mapping_a_reclosed_leaf_accumulated(self) -> None:
        """A leaf that closed out, synced, and closed out again lands both real mappings.

        Both closeouts really happened and both commits exist in their repositories, so the
        landed ledger carries two rows ahead of the source history. The row count is not the
        safeguard; each added row is verified instead.
        """

        with tempfile.TemporaryDirectory() as tmp:
            closed, source, source_rows, second_memory, ledger_commit, _ = (
                _reclosed_leaf_memory_history(Path(tmp))
            )
            assert closed.memory_repo_path is not None

            require_integrated_ledger_mapping(
                closed,
                IntegratedCommits(
                    code=closed.code_commit,
                    memory_content=second_memory,
                    ledger=ledger_commit,
                ),
                memory_source_commit=source,
            )

            landed = parse_ledger_text(
                _git(closed.memory_repo_path, "show", f"{ledger_commit}:memory.md")
            )
            self.assertEqual(
                landed.rows,
                [
                    LedgerRow(closed.code_commit, second_memory),
                    LedgerRow(closed.code_commit, closed.memory_content_commit),
                    *source_rows,
                ],
            )

    def test_ledger_refuses_unpreserved_source_rows_and_untrue_added_mappings(self) -> None:
        """Every added mapping is proven, and the source history is never traded away."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            closed, source, source_rows, second_memory, ledger_commit, accumulated = (
                _reclosed_leaf_memory_history(root)
            )
            memory_repo = closed.memory_repo_path
            memory_worktree = closed.memory_worktree
            assert memory_repo is not None and memory_worktree is not None
            assert closed.ledger_path is not None
            code_source = _git(closed.code_repo_path, "rev-parse", closed.code_source_branch)
            # A memory commit on a sibling branch: real, but never part of this leaf's history.
            _git(memory_repo, "branch", "orphan-content", source)
            _commit_on(memory_repo, "orphan-content", "orphan-content.md")
            orphan = _git(memory_repo, "rev-parse", "orphan-content")
            _git(memory_repo, "switch", "super")
            cases = (
                (
                    "dropped source row",
                    _ledger_with_rows(accumulated, accumulated.rows[:-1]),
                    closed.code_commit,
                    second_memory,
                    "does not preserve the complete source ledger history",
                    (f"missing 1 source row(s): {source_rows[0].code_commit}",),
                ),
                (
                    "memory commit that never landed",
                    _ledger_with_rows(
                        accumulated,
                        [LedgerRow(code_source, orphan), *accumulated.rows],
                    ),
                    code_source,
                    orphan,
                    "is not an ancestor of the landed ledger commit",
                    (orphan,),
                ),
                (
                    "code commit this repository does not hold",
                    _ledger_with_rows(
                        accumulated,
                        [LedgerRow(source, second_memory), *accumulated.rows],
                    ),
                    source,
                    second_memory,
                    "does not exist in the code repository",
                    (source,),
                ),
            )
            for name, ledger, code_commit, memory_content, refusal, evidence in cases:
                with self.subTest(case=name):
                    candidate = _commit_ledger(
                        memory_worktree,
                        closed.ledger_path,
                        ledger,
                        f"Ledger variant: {name}",
                    )
                    with self.assertRaises(RuntimeError) as raised:
                        require_integrated_ledger_mapping(
                            closed,
                            IntegratedCommits(
                                code=code_commit,
                                memory_content=memory_content,
                                ledger=candidate,
                            ),
                            memory_source_commit=source,
                        )
                    message = str(raised.exception)
                    self.assertIn(refusal, message)
                    for fragment in evidence:
                        self.assertIn(fragment, message)
                    self.assertIn("Remedy:", message)
                    self.assertIn("worktree_closeout_apply", message)
            self.assertEqual(
                parse_ledger_text(_git(memory_repo, "show", f"{ledger_commit}:memory.md")).rows,
                [
                    LedgerRow(closed.code_commit, second_memory),
                    LedgerRow(closed.code_commit, closed.memory_content_commit),
                    *source_rows,
                ],
            )
