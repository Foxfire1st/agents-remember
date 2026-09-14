"""Protected integration refs and their plane-owned mutation capability."""

from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import pytest

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.kernel.memory_attribution import code_commit_exists
from agents_remember.kernel.memory_ledger import (
    LedgerRow,
    MemoryLedger,
    ledger_to_text,
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
    _require_true_rows,
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
from agents_remember.worktrees.modules.git import is_ancestor
from agents_remember.worktrees.modules.integrate import (
    IntegrationSources,
)
from agents_remember.worktrees.worktree_contract import (
    WorktreeContract,
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


def _ledger_text_with_header(ledger: MemoryLedger, header: tuple[str, str]) -> str:
    """The canonical rendering with a header that deliberately disagrees with its own first row.

    The metadata block precedes the table, so the first occurrence of row 1's two values is the
    header, and patching exactly those two leaves every table cell as rendered. This reproduces
    the third real closeout-merge error on demand.
    """

    text = ledger_to_text(ledger)
    return text.replace(f'"{ledger.rows[0].code_commit}"', f'"{header[0]}"', 1).replace(
        f'"{ledger.rows[0].memory_commit}"', f'"{header[1]}"', 1
    )


def _commit_ledger_text(memory_worktree: Path, ledger_path: Path, text: str, message: str) -> str:
    ledger_path.write_text(text, encoding="utf-8")
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

    def test_ledger_refuses_a_ledger_that_does_not_map_the_landed_code_commit(self) -> None:
        """The landed code commit must be mapped to the landed memory content, or nothing lands.

        This is the first promise the landing owes and the one the file rule never carried: a
        ledger whose table does not name the pair this landing created leaves a reader resolving
        that code commit to nothing at all. It is checked against the landed table's own first
        row for that code commit, so a table that names the commit with DIFFERENT memory content
        is refused too -- the entry exists but it is not this landing's.
        """

        with tempfile.TemporaryDirectory() as tmp:
            closed, source, _source_rows, second_memory, ledger_commit, _accumulated = (
                _reclosed_leaf_memory_history(Path(tmp))
            )
            memory_repo = closed.memory_repo_path
            assert memory_repo is not None
            for label, code_commit, memory_content in (
                ("code commit the table never names", "f" * 40, second_memory),
                ("memory content the table maps elsewhere", closed.code_commit, "e" * 40),
            ):
                with self.subTest(case=label):
                    with self.assertRaises(RuntimeError) as raised:
                        require_integrated_ledger_mapping(
                            closed,
                            IntegratedCommits(
                                code=code_commit,
                                memory_content=memory_content,
                                ledger=ledger_commit,
                            ),
                            memory_source_commit=source,
                        )
                    self.assertIn(
                        "does not map landed code commit to landed memory content",
                        str(raised.exception),
                    )

    def test_ledger_refuses_memory_content_that_does_not_descend_from_the_source(self) -> None:
        """Landed memory content that is not built on the exact source is refused.

        The promise is CONDITIONAL, and the condition is the interesting half: while the landing is
        still to happen the source is behind the ledger and the question is real; once a ref has
        moved the source branch IS the landed ledger and the same question answers itself, which is
        what keeps a retry converging. So the case builds the state the promise exists for -- a
        source line the landed memory content does not descend from -- and asserts the divergence
        as well as the refusal, so it cannot pass because the fixture drifted into the trivial
        case.
        """

        with tempfile.TemporaryDirectory() as tmp:
            closed, source, _source_rows, second_memory, ledger_commit, accumulated = (
                _reclosed_leaf_memory_history(Path(tmp))
            )
            memory_repo = closed.memory_repo_path
            memory_worktree = closed.memory_worktree
            assert memory_repo is not None and memory_worktree is not None
            assert closed.ledger_path is not None
            # A source line of its own, carrying a commit the ledger's line does not have.
            _git(memory_repo, "branch", "other-source", source)
            _commit_on(memory_repo, "other-source", "other-source-only.md")
            other_source = _git(memory_repo, "rev-parse", "other-source")
            _git(memory_repo, "switch", "super")
            self.assertFalse(
                is_ancestor(memory_repo, other_source, ledger_commit),
                "the fixture must keep the source OUT of the landed line for this clause to bite",
            )
            # The pair itself is exactly right, so the refusal below is the ancestry clause's.
            candidate = _commit_ledger(
                memory_worktree,
                closed.ledger_path,
                replace(
                    accumulated,
                    rows=[LedgerRow(closed.code_commit, second_memory), *accumulated.rows],
                    last_verified_code_commit=closed.code_commit,
                    last_memory_content_commit=second_memory,
                ),
                "Land the exact pair against a divergent source",
            )
            with self.assertRaises(RuntimeError) as raised:
                require_integrated_ledger_mapping(
                    closed,
                    IntegratedCommits(
                        code=closed.code_commit,
                        memory_content=second_memory,
                        ledger=candidate,
                    ),
                    memory_source_commit=other_source,
                )
            self.assertIn("is not based on the exact memory source", str(raised.exception))

    def test_ledger_refuses_untrue_rows_and_accepts_a_rebuilt_source_region(self) -> None:
        """The landed table is judged on what the world says, not on what the file said.

        The rule this case used to witness -- "no source row may be dropped, reordered or
        replaced" -- protected the tracked ``memory.md``, and that file is derived state now: a
        rebuild drops a row whose memory commit the line cannot prove and normalises the rest.
        What the landing still owes is that every row it publishes is TRUE, and every row below
        is refused for exactly that, so the case keeps its teeth while the file rule goes.

        The two cases that are now ACCEPTED are asserted as accepted rather than deleted: a
        dropped source row and a duplicated one are precisely the shapes the removed rule refused
        and a rebuild produces, so leaving them untested would hide the change this leaf makes.
        """

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
                    "memory commit that never landed",
                    _ledger_with_rows(
                        accumulated,
                        [LedgerRow(code_source, orphan), *accumulated.rows],
                    ),
                    code_source,
                    orphan,
                    "does not name memory content the landed ledger commit carries",
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
                    "which the code repository does not hold",
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

            # ACCEPTED, and deliberately asserted as accepted: the two shapes the removed rule
            # refused and a rebuild produces. Each candidate keeps the landed pair true, so what
            # the acceptance measures is the file rule's absence rather than a weakened check.
            accepted = (
                (
                    "dropped source row",
                    _ledger_with_rows(accumulated, accumulated.rows[:-1]),
                ),
                (
                    "duplicated source row",
                    _ledger_with_rows(
                        accumulated,
                        [*accumulated.rows, accumulated.rows[-1]],
                    ),
                ),
            )
            for name, ledger in accepted:
                with self.subTest(accepted=name):
                    candidate = _commit_ledger(
                        memory_worktree,
                        closed.ledger_path,
                        ledger,
                        f"Ledger variant accepted: {name}",
                    )
                    require_integrated_ledger_mapping(
                        closed,
                        IntegratedCommits(
                            code=closed.code_commit,
                            memory_content=second_memory,
                            ledger=candidate,
                        ),
                        memory_source_commit=source,
                    )
            # A hand edit that leaves every row alone and moves only the header is still a
            # malformed ledger, and it is the shape this check newly carries a remedy for: the
            # integrated reader rejected it as an opaque "invalid" before the projection check.
            with self.subTest(case="header disagreeing with its own first row"):
                headered = _commit_ledger_text(
                    memory_worktree,
                    closed.ledger_path,
                    _ledger_text_with_header(
                        accumulated,
                        (source_rows[0].code_commit, source_rows[0].memory_commit),
                    ),
                    "Ledger variant: header disagrees",
                )
                with self.assertRaises(RuntimeError) as raised:
                    require_integrated_ledger_mapping(
                        closed,
                        IntegratedCommits(
                            code=closed.code_commit,
                            memory_content=second_memory,
                            ledger=headered,
                        ),
                        memory_source_commit=source,
                    )
                message = str(raised.exception)
                self.assertIn("the ledger header disagrees with its own first row", message)
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


def _init_repo(root: Path) -> Path:
    """One real repository, because every clause here is asked of Git rather than of a stub."""

    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "landing@example.invalid")
    _git(root, "config", "user.name", "Landing Clauses")
    return root


def _commit_text(repo: Path, name: str, body: str) -> str:
    """One real commit of this repository, so a cell names something git can resolve."""

    (repo / name).write_text(body + "\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", name)
    return _git(repo, "rev-parse", "HEAD")


def _integration_contract(repo: Path) -> WorktreeContract:
    """A leaf contract over one repository, which is all the row-truth clauses read.

    Built directly rather than through ``worktree_start``: these clauses read exactly three cells
    -- the two repositories and the kind -- and standing up a whole enclosure to ask a question
    about a row would make the case measure the fixture instead of the rule.
    """

    return WorktreeContract(
        task_id="TASK",
        task_name="task",
        repo_name="repo-a",
        workflow_kind="light-task",
        memory_mode="external",
        coordination_root=repo,
        task_root=repo,
        contract_path=repo / "series-contract.md",
        task_artifact=repo / "task.md",
        worktree_group=repo,
        code_repo_path=repo,
        code_source_branch="main",
        code_work_branch="work",
        code_base_commit="",
        code_worktree=repo,
        memory_repo_path=repo,
        memory_source_branch="main",
        memory_work_branch="memory-work",
        memory_base_commit="",
        ledger_path=repo / "memory.md",
    )


def test_the_landed_ledger_commit_must_carry_the_memory_content_it_maps(tmp_path: Path) -> None:
    """A row naming memory content the landed ledger commit does not carry is refused.

    This is the promise the file rule never carried: a landed table may resolve nothing to memory
    that never reached the branch, because such a row is a false entry whether or not a reader
    ever looks it up -- ``find_mapping`` returns the FIRST row naming a code commit, and a stale
    duplicate below a current one is exactly how a false entry hides. The case writes the table
    onto a commit that does not descend from the content it names, so the mapping clause is
    satisfied and this one refuses.
    """

    repo = _init_repo(tmp_path / "reachability")
    early = _commit_text(repo, "early.md", "a commit the ledger will sit on")
    code_commit = _commit_text(repo, "code.md", "the code commit the row names")
    content = "b" * 40
    ledger = MemoryLedger(
        schema="ar-memory-ledger/v1",
        repo_name="repo-a",
        base_code_commit=code_commit,
        base_memory_commit=content,
        last_verified_code_commit=code_commit,
        last_memory_content_commit=content,
        sort_order="newest-first",
        rows=[LedgerRow(code_commit, content)],
    )
    contract = replace(
        _integration_contract(repo),
        memory_base_commit=early,
    )
    assert not is_ancestor(repo, content, early)

    with pytest.raises(RuntimeError) as raised:
        _require_true_rows(contract, ledger, early)
    assert "does not name memory content the landed ledger commit carries" in str(raised.value)


def test_the_landed_ledger_must_name_a_code_commit_the_repository_holds(tmp_path: Path) -> None:
    """A row naming a code commit the code repository does not hold is refused.

    The other half of the same promise, and the shape a hand edit takes: the memory side resolves
    and the row parses, but no such code commit exists, so the mapping it claims is not a mapping
    anything can use.
    """

    repo = _init_repo(tmp_path / "code-side")
    head = _commit_text(repo, "content.md", "content the ledger may name")
    ledger = MemoryLedger(
        schema="ar-memory-ledger/v1",
        repo_name="repo-a",
        base_code_commit="c" * 40,
        base_memory_commit=head,
        last_verified_code_commit="c" * 40,
        last_memory_content_commit=head,
        sort_order="newest-first",
        rows=[LedgerRow("c" * 40, head)],
    )
    contract = _integration_contract(repo)
    assert not code_commit_exists(contract.code_repo_path, "c" * 40)

    with pytest.raises(RuntimeError) as raised:
        _require_true_rows(contract, ledger, head)
    assert "which the code repository does not hold" in str(raised.value)
