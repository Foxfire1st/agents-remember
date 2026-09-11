"""Focused CCR-R12 transaction-only closeout and integration regressions."""

from __future__ import annotations

import json
import shlex
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from unittest import mock

from agents_remember.application import worktree_tools
from agents_remember.application.worktree_tool_requests import (
    CloseoutApproval,
    CloseoutCommitMessages,
)
from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.memory_ledger import (
    LedgerRow,
    create_initial_ledger,
    ledger_to_text,
    load_ledger,
    parse_ledger_text,
    write_ledger,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, load_config
from agents_remember.models.closeout.input import (
    EffectiveCloseoutInput,
    EnabledCloseoutLeg,
)
from agents_remember.tasks import TaskEnclosureRef, read_task_doc, write_task_doc
from agents_remember.worktrees.integration.closeout import curator_coherence as coherence
from agents_remember.worktrees.integration.closeout.certification import execution as selected
from agents_remember.worktrees.integration.lifecycle import lifecycle_operations
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.closeout_external import (
    _commit_ledger_mapping,
    _commit_memory_content,
    _LedgerCommitFacts,
)
from agents_remember.worktrees.modules.git import is_ancestor
from agents_remember.worktrees.modules.quality import closeout_memory as memory_quality
from agents_remember.worktrees.modules.quality import gate as quality_gate
from agents_remember.worktrees.worktree_contract import load_contract
from closeout_input_test_support import (
    ensure_fixture_waiting_door,
)
from integration_branch_authority_test_support import (
    _authority_fixture,
    _closed_external_leaf_worktrees,
)
from test_source_lineage import _commit_on, _fixture, _git

MESSAGES = CloseoutCommitMessages(
    code="Add transaction feature",
    memory="Document transaction feature",
    ledger="Record transaction pair",
)


def _public_config(root: Path, contract) -> McpRuntimeConfig:
    """Bind an existing temp Git fixture to MCP authority without a profile."""

    code_link = root / contract.repo_name
    if not code_link.exists():
        code_link.symlink_to(contract.code_repo_path, target_is_directory=True)
    if contract.memory_repo_path is not None:
        memory_link = contract.coordination_root / "memory-repos" / f"ar-{contract.repo_name}"
        memory_link.parent.mkdir(parents=True, exist_ok=True)
        if not memory_link.exists():
            memory_link.symlink_to(contract.memory_repo_path, target_is_directory=True)
    config_path = root / "settings.json"
    config_path.write_text(
        json.dumps(
            {
                "version": 1,
                "coordinationRoot": contract.coordination_root.as_posix(),
                "workspaceRoot": root.as_posix(),
                "retirement": {"autoLandOnIntegration": False},
                "repositories": {contract.repo_name: {}},
            }
        ),
        encoding="utf-8",
    )
    return load_config(config_path)


def _bind_task_without_review(contract) -> None:
    """Add only the canonical enclosure binding; leave review evidence absent."""

    task_path = contract.task_root / f"{contract.leaf_id.lower()}.json"
    document = read_task_doc(task_path)
    write_task_doc(
        task_path.parent,
        document.model_copy(
            update={
                "enclosures": [
                    TaskEnclosureRef(
                        leafId=contract.leaf_id,
                        enclosurePath=contract.contract_path.as_posix(),
                    )
                ],
            }
        ),
    )


def _assert_no_profile_or_review(config, contract) -> None:
    """Make the transaction test's absent acceptance authorities explicit."""

    assert config.repositories[contract.repo_name].certification_profile is None
    document = read_task_doc(contract.task_root / f"{contract.leaf_id.lower()}.json")
    assert document.routeReview is None


def _forbid_acceptance_tools():
    """Patch historical acceptance entry points so an accidental call fails loudly."""

    return (
        mock.patch.multiple(
            quality_gate,
            run_strict_code_quality_gate=mock.Mock(
                side_effect=AssertionError("transaction called strict code quality")
            ),
            create=True,
        ),
        mock.patch.object(
            memory_quality,
            "run_memory_quality_phase",
            side_effect=AssertionError("transaction called memory quality"),
        ),
        mock.patch.object(
            selected,
            "execute_selected_closeout",
            side_effect=AssertionError("transaction called selected certification"),
        ),
        mock.patch.object(
            coherence,
            "require_current_curator_coherence",
            side_effect=AssertionError("transaction called curator certification"),
        ),
    )


def _install_failing_pre_commit_hooks(contract, root: Path) -> tuple[Path, Path]:
    """Install real failing hooks in both repositories and return their logs."""

    logs = (root / "code-pre-commit.log", root / "memory-pre-commit.log")
    repositories = (contract.code_repo_path, contract.memory_repo_path)
    for repository, log in zip(repositories, logs, strict=True):
        assert repository is not None
        hook_directory = Path(_git(repository, "rev-parse", "--git-path", "hooks"))
        if not hook_directory.is_absolute():
            hook_directory = repository / hook_directory
        hook_directory.mkdir(parents=True, exist_ok=True)
        hook = hook_directory / "pre-commit"
        hook.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' transaction-hook-invoked >> {shlex.quote(log.as_posix())}\n"
            "exit 97\n",
            encoding="utf-8",
        )
        hook.chmod(0o755)
        assert hook.is_file() and hook.stat().st_mode & 0o111
        probe = run_git(repository, ["hook", "run", "pre-commit"])
        assert probe.returncode == 97, probe
        assert log.read_text(encoding="utf-8").splitlines() == ["transaction-hook-invoked"]
        log.unlink()
    return logs


def test_public_closeout_commits_code_memory_and_ledger_without_acceptance_tools(
    tmp_path, worktree_services
):
    """Closeout is a Git transaction even when quality/review authorities are absent."""

    fixture = _fixture(tmp_path, external_memory=True, selected_profile=False)
    contract = fixture.leaf_contract
    assert contract.memory_repo_path is not None and contract.memory_worktree is not None
    contract.code_worktree.parent.mkdir(parents=True, exist_ok=True)
    contract.memory_worktree.parent.mkdir(parents=True, exist_ok=True)
    _git(
        fixture.code_repo,
        "worktree",
        "add",
        contract.code_worktree.as_posix(),
        contract.code_work_branch,
    )
    _git(
        contract.memory_repo_path,
        "worktree",
        "add",
        contract.memory_worktree.as_posix(),
        contract.memory_work_branch,
    )
    memory_seed = _git(contract.memory_worktree, "rev-parse", "HEAD")
    write_ledger(
        contract.memory_worktree / "memory.md",
        create_initial_ledger("repo", contract.code_base_commit, memory_seed),
    )
    _git(contract.memory_worktree, "add", "memory.md")
    _git(contract.memory_worktree, "commit", "-m", "Seed transaction ledger")
    (contract.code_worktree / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
    (contract.memory_worktree / "onboarding").mkdir()
    (contract.memory_worktree / "feature.md").write_text("# Feature\n", encoding="utf-8")
    ensure_fixture_waiting_door(contract, force_synthetic=True)
    contract = load_contract(contract.contract_path)
    _bind_task_without_review(contract)
    config = _public_config(tmp_path, contract)
    _assert_no_profile_or_review(config, contract)
    code_hook_log, memory_hook_log = _install_failing_pre_commit_hooks(contract, tmp_path)

    with ExitStack() as stack:
        for patcher in _forbid_acceptance_tools():
            stack.enter_context(patcher)
        # The existing helper publishes a deliberately non-applicable waiting door.  Its
        # disposable sprint has no queue projection, so bypass only that fixture fence while
        # retaining the public apply admission.
        stack.enter_context(
            mock.patch.object(lifecycle_operations, "require_first_ready_generation")
        )
        preview = worktree_tools.worktree_closeout_preview_tool(
            config, contract.contract_path.as_posix(), MESSAGES
        )
        assert preview["ok"] is True, preview
        assert preview["state"] == "would-closeout"
        applied = worktree_tools.worktree_closeout_apply_tool(
            config,
            contract.contract_path.as_posix(),
            MESSAGES,
            CloseoutApproval(intent_note="developer approved transaction"),
        )

    # Closeout runs in this process: no detached worker, no operation record. The
    # three commits and their ancestry are the whole record of what it did.
    assert applied["ok"] is True, applied
    assert applied["state"] == "closed", applied
    closed = load_contract(contract.contract_path)
    # The reload above rebinds `contract`, so the optional memory worktree and ledger
    # path must be narrowed again before they are read.
    assert contract.memory_worktree is not None and contract.ledger_path is not None
    assert closed.closeout_status == "completed"
    assert closed.code_commit and closed.memory_content_commit and closed.ledger_commit
    assert _git(contract.code_worktree, "rev-parse", "HEAD") == closed.code_commit
    assert _git(contract.memory_worktree, "rev-parse", "HEAD") == closed.ledger_commit
    mapping = load_ledger(contract.ledger_path).rows[0]
    assert mapping.code_commit == closed.code_commit
    assert mapping.memory_commit == closed.memory_content_commit
    # The validated messages are the ones that actually landed in the commit objects.
    assert _git(contract.code_worktree, "log", "-1", "--format=%s") == MESSAGES.code
    assert _git(contract.memory_worktree, "log", "-1", "--format=%s") == MESSAGES.ledger
    assert (
        _git(contract.memory_worktree, "log", "-1", "--format=%s", closed.memory_content_commit)
        == MESSAGES.memory
    )
    assert not code_hook_log.exists()
    assert not memory_hook_log.exists()


def test_public_integration_merges_prepared_pair_without_acceptance_tools(
    tmp_path, worktree_services
):
    """Integration merges the prepared code and memory refs without rerunning acceptance."""

    fixture = _authority_fixture(tmp_path, external_memory=True)
    # Narrow the fixture's optional repositories once, where the test establishes them.
    code_repo = fixture.code_repo
    assert isinstance(code_repo, Path)
    closed = _closed_external_leaf_worktrees(fixture, tmp_path, publish_closeout_evidence=False)
    memory_repo = closed.memory_repo_path
    assert isinstance(memory_repo, Path)
    config = _public_config(tmp_path, closed)
    _assert_no_profile_or_review(config, closed)
    code_hook_log, memory_hook_log = _install_failing_pre_commit_hooks(closed, tmp_path)

    with ExitStack() as stack:
        for patcher in _forbid_acceptance_tools():
            stack.enter_context(patcher)
        preview = worktree_tools.worktree_integrate_tool(
            config,
            contract_path=closed.contract_path.as_posix(),
            strategy="ff-only",
            dry_run=True,
        )
        assert preview["ok"] is True, preview
        # Seat retirement must still fire on the SYNCHRONOUS path: its other call
        # site was the detached worker, so a deletion that removed this trigger
        # would stop retiring seats silently and a green suite would not catch it.
        # This fixture disables auto-landing, so enable retirement for the pin.
        retiring = replace(
            config, retirement=replace(config.retirement, auto_land_on_integration=True)
        )
        with mock.patch.object(worktree_tools, "auto_complete_seats", return_value={}) as retire:
            applied = worktree_tools.worktree_integrate_tool(
                retiring,
                contract_path=closed.contract_path.as_posix(),
                strategy="ff-only",
                dry_run=False,
            )

    # Integration runs in this process: the refs themselves are the record.
    assert applied["ok"] is True, applied
    assert retire.call_args is not None, "auto_complete_seats is no longer reachable"
    assert retire.call_args.kwargs["edge"] == "leaf-integration"
    integrated = load_contract(closed.contract_path)
    assert integrated.integration_status == "completed"
    assert integrated.integrated_code_commit == integrated.code_commit
    assert integrated.integrated_memory_content_commit == integrated.memory_content_commit
    assert integrated.integrated_ledger_commit == integrated.ledger_commit
    assert _git(code_repo, "rev-parse", "ar/master") == integrated.code_commit
    assert _git(memory_repo, "rev-parse", "ar/master") == integrated.ledger_commit
    assert not code_hook_log.exists()
    assert not memory_hook_log.exists()


def test_public_integration_ref_movement_refuses_before_pair_merge(tmp_path, worktree_services):
    """A source-tip race remains a concrete refusal and cannot publish a torn pair."""

    fixture = _authority_fixture(tmp_path, external_memory=True)
    # Narrow the fixture's optional repositories once, where the test establishes them.
    code_repo = fixture.code_repo
    assert isinstance(code_repo, Path)
    closed = _closed_external_leaf_worktrees(fixture, tmp_path, publish_closeout_evidence=False)
    memory_repo = closed.memory_repo_path
    assert isinstance(memory_repo, Path)
    config = _public_config(tmp_path, closed)
    _assert_no_profile_or_review(config, closed)
    source_before = _git(code_repo, "rev-parse", "ar/master")
    memory_before = _git(memory_repo, "rev-parse", "ar/master")

    with ExitStack() as stack:
        for patcher in _forbid_acceptance_tools():
            stack.enter_context(patcher)
        preview = worktree_tools.worktree_integrate_tool(
            config,
            contract_path=closed.contract_path.as_posix(),
            strategy="ff-only",
            dry_run=True,
        )
        assert preview["ok"] is True, preview

        _git(code_repo, "branch", "race", source_before)
        _git(code_repo, "switch", "race")
        (fixture.code_repo / "parallel.txt").write_text("parallel\n", encoding="utf-8")
        _git(code_repo, "add", "parallel.txt")
        _git(code_repo, "commit", "-m", "Parallel source change")
        raced = _git(code_repo, "rev-parse", "HEAD")
        _git(code_repo, "update-ref", "refs/heads/ar/master", raced, source_before)
        code_hook_log, memory_hook_log = _install_failing_pre_commit_hooks(closed, tmp_path)

        # The parent moved past the candidate, so the git replay requirement is
        # true and the integration refuses before any ref moves. The refusal is
        # a return value, not an exception.
        refused = worktree_tools.worktree_integrate_tool(
            config,
            contract_path=closed.contract_path.as_posix(),
            strategy="ff-only",
            dry_run=False,
        )

    assert refused["ok"] is False, refused
    assert refused["state"] == "blocked-non-ff", refused
    assert "source branch moved" in repr(refused)
    assert _git(code_repo, "rev-parse", "ar/master") == raced
    assert _git(memory_repo, "rev-parse", "ar/master") == memory_before
    assert not code_hook_log.exists()
    assert not memory_hook_log.exists()
    current = load_contract(closed.contract_path)
    assert current.integration_status != "completed"


def _effective_closeout_input() -> EffectiveCloseoutInput:
    leg = EnabledCloseoutLeg(reason="test", message="Test closeout commit")
    return EffectiveCloseoutInput(
        route="worktree",
        contractKind="leaf",
        memoryMode="external",
        code=leg,
        memory=leg,
        ledger=leg,
    )


def test_recloseout_after_a_sync_records_the_memory_head_as_content_commit(tmp_path):
    """Closeout -> sync -> closeout records the live memory head, not the stale record.

    The recorded ``memory_content_commit`` predates the sync merge, and integration refuses
    exactly that with "integrated memory content commit is not based on the exact memory
    source". Since sync -> re-closeout -> retry is the published remedy for a moved parent,
    the re-closeout must re-derive the content commit from the worktree it is committing.
    """

    fixture = _authority_fixture(tmp_path, external_memory=True)
    closed = _closed_external_leaf_worktrees(fixture, tmp_path, publish_closeout_evidence=False)
    memory_repo = closed.memory_repo_path
    memory_worktree = closed.memory_worktree
    assert memory_repo is not None and memory_worktree is not None
    recorded = closed.memory_content_commit
    # The parent moved after that closeout, and the sync merged the move into the work branch.
    _commit_on(memory_repo, closed.memory_source_branch, "parent-moved.md")
    moved_source = _git(memory_repo, "rev-parse", closed.memory_source_branch)
    _git(memory_worktree, "merge", "--no-edit", moved_source)
    memory_head = _git(memory_worktree, "rev-parse", "HEAD")
    # The integration predicate this fix removes: the recorded commit is behind the source.
    assert not is_ancestor(memory_repo, moved_source, recorded)

    memory_commit, created = _commit_memory_content(
        closed,
        WorktreeArgs(contract_path=closed.contract_path),
        _effective_closeout_input(),
        existing_mapping=None,
    )

    assert created is False
    assert memory_commit == memory_head
    assert memory_commit != recorded
    assert is_ancestor(memory_repo, moved_source, memory_commit)


# --------------------------------------------------------------------------------------
# The ledger is derived state: closeout recomputes it, and a re-run repairs it.
# --------------------------------------------------------------------------------------


class _ClosedLedgerLeaf:
    """One closed external leaf, its canonical mapping rows, and the ledger file they live in."""

    def __init__(self, root: Path) -> None:
        fixture = _authority_fixture(root, external_memory=True)
        closed = _closed_external_leaf_worktrees(fixture, root, publish_closeout_evidence=False)
        memory_repo = closed.memory_repo_path
        memory_worktree = closed.memory_worktree
        ledger_path = closed.ledger_path
        assert memory_repo is not None and memory_worktree is not None
        assert ledger_path is not None
        self.contract = closed
        self.memory_repo = memory_repo
        self.memory_worktree = memory_worktree
        self.ledger_path = ledger_path
        source_commit = _git(memory_repo, "rev-parse", closed.memory_source_branch)
        self.source_rows = parse_ledger_text(
            _git(memory_repo, "show", f"{source_commit}:memory.md")
        ).rows
        self.own_row = LedgerRow(closed.code_commit, closed.memory_content_commit)
        self.canonical_rows = [self.own_row, *self.source_rows]
        self.content_path = memory_worktree / "candidate.md"
        self.content_bytes = self.content_path.read_bytes()

    def render(self, rows: list[LedgerRow], *, header: tuple[str, str] | None = None) -> str:
        """A ledger whose rows are ``rows`` and whose header may deliberately disagree."""

        consistent = replace(
            load_ledger(self.ledger_path),
            rows=list(rows),
            last_verified_code_commit=rows[0].code_commit,
            last_memory_content_commit=rows[0].memory_commit,
        )
        text = ledger_to_text(consistent)
        if header is None or header == (rows[0].code_commit, rows[0].memory_commit):
            return text
        # The metadata block precedes the table, so the first occurrence of row 1's two values
        # is the header; the table cells are left exactly as rendered.
        return text.replace(f'"{rows[0].code_commit}"', f'"{header[0]}"', 1).replace(
            f'"{rows[0].memory_commit}"', f'"{header[1]}"', 1
        )

    def commit_ledger_text(self, text: str, message: str) -> str:
        self.ledger_path.write_text(text, encoding="utf-8")
        _git(self.memory_worktree, "add", "memory.md")
        _git(self.memory_worktree, "commit", "-m", message)
        return _git(self.memory_worktree, "rev-parse", "HEAD")

    def rerun_closeout(self):
        """The closeout ledger step, re-run over whatever the file now says."""

        return _commit_ledger_mapping(
            self.contract,
            WorktreeArgs(
                contract_path=self.contract.contract_path,
                closeout_input=_effective_closeout_input(),
            ),
            _effective_closeout_input(),
            _LedgerCommitFacts(
                self.contract.code_commit,
                self.contract.memory_content_commit,
            ),
        )

    def unreachable_memory_commit(self) -> str:
        """Real memory content on a sibling branch, never reachable from this work head."""

        source = _git(self.memory_repo, "rev-parse", self.contract.memory_source_branch)
        _git(self.memory_repo, "branch", "superseded-content", source)
        _commit_on(self.memory_repo, "superseded-content", "superseded-content.md")
        orphan = _git(self.memory_repo, "rev-parse", "superseded-content")
        _git(self.memory_repo, "switch", self.contract.memory_source_branch)
        return orphan


def test_closeout_rerun_leaves_an_already_correct_ledger_byte_identical(tmp_path):
    """Re-running closeout over a correct ledger writes nothing and says exactly that."""

    leaf = _ClosedLedgerLeaf(tmp_path)
    before_bytes = leaf.ledger_path.read_bytes()
    head_before = _git(leaf.memory_worktree, "rev-parse", "HEAD")

    ledger_commit, created, payload = leaf.rerun_closeout()

    assert created is False
    assert ledger_commit == head_before
    assert leaf.ledger_path.read_bytes() == before_bytes
    assert load_ledger(leaf.ledger_path).rows == leaf.canonical_rows
    assert _git(leaf.memory_worktree, "status", "--porcelain") == ""
    assert payload["state"] == "already-correct"
    assert payload["rowsAdded"] == []
    assert payload["rowsRemoved"] == []
    assert payload["rowsReordered"] == []
    assert payload["headerChanged"] is False
    assert str(payload["summary"]).startswith(
        "the memory ledger already equals the projection for source "
    )


def test_closeout_rerun_repairs_every_malformed_ledger_shape_and_reports_it(tmp_path):
    """The three real closeout-merge errors are repaired by re-running closeout.

    Each shape was landed as real damage once: a superseded row kept, a table whose source
    rows are not its tail, and a header that disagreed with its own first row. Re-running
    closeout recomputes the ledger from the source plus the branch's own true mappings, and
    the payload names what it changed rather than rewriting the record silently.
    """

    leaf = _ClosedLedgerLeaf(tmp_path)
    own_row = leaf.own_row
    orphan = leaf.unreachable_memory_commit()
    source_row = leaf.source_rows[0]
    malformations = (
        (
            "superseded row kept",
            [own_row, LedgerRow(own_row.code_commit, orphan), *leaf.source_rows],
            None,
            "removed",
        ),
        (
            "source row ahead of the branch's own mapping",
            [source_row, own_row],
            None,
            "reordered",
        ),
        (
            "header disagreeing with its own first row",
            [own_row, *leaf.source_rows],
            (source_row.code_commit, source_row.memory_commit),
            "header",
        ),
    )

    for name, rows, header, expectation in malformations:
        leaf.commit_ledger_text(leaf.render(rows, header=header), f"Malform: {name}")
        ledger_commit, created, payload = leaf.rerun_closeout()

        assert created is True, name
        assert load_ledger(leaf.ledger_path).rows == leaf.canonical_rows, name
        # Content is never touched: the repair commit carries the mapping table and nothing else.
        assert _git(
            leaf.memory_worktree,
            "show",
            "--name-only",
            "--format=",
            ledger_commit,
        ).split() == ["memory.md"], name
        assert leaf.content_path.read_bytes() == leaf.content_bytes, name
        assert payload["state"] == "repaired", name
        assert str(payload["summary"]).startswith("repaired the memory ledger for source "), name
        if expectation == "removed":
            assert payload["rowsRemoved"], name
            assert payload["removedReasons"], name
        elif expectation == "reordered":
            assert payload["rowsReordered"], name
        else:
            # A wrong header is repaired without moving a single row, so the payload has to
            # report the header change rather than claim a table change that never happened.
            assert payload["headerChanged"] is True, name
            assert payload["rowsAdded"] == [], name
            assert payload["rowsRemoved"] == [], name
            assert payload["rowsReordered"] == [], name
