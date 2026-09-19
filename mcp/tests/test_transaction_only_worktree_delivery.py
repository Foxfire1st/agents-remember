"""Focused CCR-R12 transaction-only closeout and integration regressions."""

from __future__ import annotations

import json
import shlex
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from unittest import mock

import pytest
from agents_remember.application import worktree_tools
from agents_remember.application.worktree_tool_requests import (
    CloseoutApproval,
    CloseoutCommitMessages,
)
from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.memory_cache import derive_memory_ledger, refresh_memory_cache
from agents_remember.kernel.memory_ledger import (
    LedgerRow,
    ledger_to_text,
    load_ledger,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, load_config
from agents_remember.models.closeout.input import (
    EffectiveCloseoutInput,
    EnabledCloseoutLeg,
)
from agents_remember.models.lifecycles.operation import LifecycleOperationRecoveryCommits
from agents_remember.tasks import TaskEnclosureRef, read_task_doc, write_task_doc
from agents_remember.worktrees import git_worktree_manager
from agents_remember.worktrees.integration.closeout import curator_coherence as coherence
from agents_remember.worktrees.integration.closeout.certification import execution as selected
from agents_remember.worktrees.integration.lifecycle import lifecycle_operations
from agents_remember.worktrees.modules import closeout_external
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.closeout_external import _commit_memory_content
from agents_remember.worktrees.modules.git import is_ancestor
from agents_remember.worktrees.modules.quality import closeout_memory as memory_quality
from agents_remember.worktrees.modules.quality import gate as quality_gate
from agents_remember.worktrees.task_resolver import series_contract_path
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
    """Add only the canonical enclosure binding; leave review evidence absent.

    Both derived fields are bound, exactly as ``task_doc`` stamps them against a leaf
    contract: a leaf document missing its ``seriesContractPath`` is the damage a later
    start repairs, so it would model an unstarted-against document rather than this one.
    """

    task_path = contract.task_root / f"{contract.leaf_id.lower()}.json"
    document = read_task_doc(task_path)
    write_task_doc(
        task_path.parent,
        document.model_copy(
            update={
                "seriesContractPath": series_contract_path(contract.task_root).as_posix(),
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


def _assert_memory_attribution(
    memory_worktree: Path,
    *,
    code_commit: str,
    content_commit: str,
    scratch: Path,
) -> None:
    """The memory commit carries exactly one Code-Commit trailer and contains no cached ledger.

    The trailer is read back out of the committed object by both documented git readers, so
    this is the attribution being bound by the hash rather than recorded beside it.
    """

    body = _git(memory_worktree, "log", "-1", "--format=%B", content_commit)
    assert body == f"{MESSAGES.memory}\n\nCode-Commit: {code_commit}"
    assert body.count("Code-Commit:") == 1
    message_file = scratch / "memory-content-message.txt"
    message_file.write_text(f"{body}\n", encoding="utf-8")
    assert (
        _git(memory_worktree, "interpret-trailers", "--parse", message_file.as_posix())
        == f"Code-Commit: {code_commit}"
    )
    assert (
        _git(
            memory_worktree,
            "log",
            "-1",
            "--format=%(trailers:key=Code-Commit)",
            content_commit,
        )
        == f"Code-Commit: {code_commit}"
    )
    assert _git(memory_worktree, "ls-tree", "--name-only", content_commit, "--", "memory.md") == ""


def test_public_closeout_commits_code_and_memory_without_acceptance_tools(
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
    code_before = _git(contract.code_worktree, "rev-parse", "HEAD")
    memory_before = _git(contract.memory_worktree, "rev-parse", "HEAD")
    (contract.memory_worktree / "memory.md").write_text(
        "<<<<<<< malformed cache\n", encoding="utf-8"
    )
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
    # two commits and their ancestry are the whole record of what it did.
    assert applied["ok"] is True, applied
    assert applied["state"] == "closed", applied
    # The response's OWN address, in the envelope's spelling. `status_payload` emits the
    # snake_case `contract_path`; the guidance guard that a seat's next move depends on reads
    # `contractPath`/`enclosurePath` (`application/tool_response.py::bound_next_step`), so a
    # closed closeout that declares neither has `response_paths` empty and the guard withholds
    # the guidance ENTIRELY -- the seat silently loses "integrate the task branches". This is
    # the producer half of `260918-TSIP` `T54`; the guard half is pinned in
    # `test_response_address_binding.py`, and this is the only case that drives the real
    # producer end to end.
    assert applied["contractPath"] == contract.contract_path.as_posix(), applied.get("contractPath")
    closed = load_contract(contract.contract_path)
    # The reload above rebinds `contract`, so the optional memory worktree and ledger
    # path must be narrowed again before they are read.
    assert contract.memory_worktree is not None and contract.ledger_path is not None
    assert closed.closeout_status == "completed"
    assert closed.code_commit and closed.memory_content_commit
    assert _git(contract.code_worktree, "rev-parse", "HEAD") == closed.code_commit
    assert _git(contract.memory_worktree, "rev-parse", "HEAD") == closed.memory_content_commit
    assert (
        _git(contract.code_worktree, "rev-list", "--count", f"{code_before}..{closed.code_commit}")
        == "1"
    )
    assert (
        _git(
            contract.memory_worktree,
            "rev-list",
            "--count",
            f"{memory_before}..{closed.memory_content_commit}",
        )
        == "1"
    )
    assert "ledger_commit" not in applied and "ledgerCommit" not in applied
    assert contract.ledger_path.is_file()
    assert _git(contract.memory_worktree, "ls-files", "--", "memory.md") == ""
    mapping = load_ledger(contract.ledger_path).rows[0]
    assert mapping.code_commit == closed.code_commit
    assert mapping.memory_commit == closed.memory_content_commit
    # The validated messages are the ones that actually landed in the commit objects.
    assert _git(contract.code_worktree, "log", "-1", "--format=%s") == MESSAGES.code
    assert (
        _git(contract.memory_worktree, "log", "-1", "--format=%s", closed.memory_content_commit)
        == MESSAGES.memory
    )
    # The memory-content commit carries the attribution, inside the object: the closeout's
    # own body verbatim plus exactly one Code-Commit trailer naming the code commit this
    # same closeout landed. Both documented readers have to see it as data.
    _assert_memory_attribution(
        contract.memory_worktree,
        code_commit=closed.code_commit,
        content_commit=closed.memory_content_commit,
        scratch=tmp_path,
    )
    assert not code_hook_log.exists()
    assert not memory_hook_log.exists()


def test_closeout_recovery_attributes_the_memory_commit_it_still_owed(tmp_path, worktree_services):
    """Resume an interrupted public closeout with a missing cache and only its memory commit owed."""

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
    code_before = _git(contract.code_worktree, "rev-parse", "HEAD")
    memory_before = _git(contract.memory_worktree, "rev-parse", "HEAD")
    (contract.memory_worktree / "memory.md").unlink(missing_ok=True)
    (contract.code_worktree / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
    (contract.memory_worktree / "onboarding").mkdir()
    (contract.memory_worktree / "feature.md").write_text("# Feature\n", encoding="utf-8")
    ensure_fixture_waiting_door(contract, force_synthetic=True)
    contract = load_contract(contract.contract_path)
    _bind_task_without_review(contract)
    config = _public_config(tmp_path, contract)
    _assert_no_profile_or_review(config, contract)

    # First attempt: interrupt after the code commit, which is the point the recovery route's
    # journal records. The exception propagates out of the real public apply.
    with ExitStack() as stack:
        for patcher in _forbid_acceptance_tools():
            stack.enter_context(patcher)
        stack.enter_context(
            mock.patch.object(lifecycle_operations, "require_first_ready_generation")
        )
        stack.enter_context(
            mock.patch.object(
                closeout_external,
                "_refresh_external_memory",
                side_effect=InterruptedError("closeout interrupted after the code commit"),
            )
        )
        with pytest.raises(InterruptedError):
            worktree_tools.worktree_closeout_apply_tool(
                config,
                contract.contract_path.as_posix(),
                MESSAGES,
                CloseoutApproval(intent_note="developer approved transaction"),
            )
    code_commit = _git(contract.code_worktree, "rev-parse", "HEAD")
    assert not load_contract(contract.contract_path).memory_content_commit

    # Resume exactly as the recovery route does: the journalled code commit, and an empty
    # memory-content cell because the memory commit is the one still owed.
    with ExitStack() as stack:
        for patcher in _forbid_acceptance_tools():
            stack.enter_context(patcher)
        stack.enter_context(
            mock.patch.object(lifecycle_operations, "require_first_ready_generation")
        )
        resume_args = WorktreeArgs(
            contract_path=contract.contract_path,
            closeout_input=worktree_tools._normalize_worktree_closeout(
                contract,
                MESSAGES,
                tool_name="worktree_closeout_apply",
                corrected_arguments={},
            ),
            approval_note="developer approved transaction",
            approved=True,
            recovery_commits=LifecycleOperationRecoveryCommits(
                codeCommit=code_commit,
                memoryContentCommit="",
            ),
        )
        # The recovery cell is load-bearing rather than decoration: a cell naming another code
        # commit refuses before any mutation, which is what makes the resume below the recovery
        # route rather than a fresh closeout that happens to agree with it.
        with pytest.raises(RuntimeError, match="does not match task HEAD"):
            git_worktree_manager.closeout_result(
                replace(
                    resume_args,
                    recovery_commits=LifecycleOperationRecoveryCommits(codeCommit="0" * 40),
                ),
                load_contract(contract.contract_path),
            )
        resumed = git_worktree_manager.closeout_result(
            resume_args,
            load_contract(contract.contract_path),
        )

    assert resumed.returncode == 0, resumed.payload
    assert resumed.payload["state"] == "closed", resumed.payload
    closed = load_contract(contract.contract_path)
    assert contract.memory_worktree is not None and contract.ledger_path is not None
    assert closed.code_commit == code_commit
    assert (
        _git(contract.code_worktree, "rev-list", "--count", f"{code_before}..{code_commit}") == "1"
    )
    assert (
        _git(
            contract.memory_worktree,
            "rev-list",
            "--count",
            f"{memory_before}..{closed.memory_content_commit}",
        )
        == "1"
    )
    assert _git(contract.memory_worktree, "rev-parse", "HEAD") == closed.memory_content_commit
    assert "ledger_commit" not in resumed.payload and "ledgerCommit" not in resumed.payload
    assert contract.ledger_path.is_file()
    assert _git(contract.memory_worktree, "ls-files", "--", "memory.md") == ""
    assert load_ledger(contract.ledger_path) == derive_memory_ledger(
        contract.memory_worktree, closed.memory_content_commit, repo_name=contract.repo_name
    )
    _assert_memory_attribution(
        contract.memory_worktree,
        code_commit=code_commit,
        content_commit=closed.memory_content_commit,
        scratch=tmp_path,
    )


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
    assert _git(code_repo, "rev-parse", "ar/master") == integrated.code_commit
    assert _git(memory_repo, "rev-parse", "ar/master") == integrated.memory_content_commit
    assert "integrated_ledger_commit" not in applied and "ledger_commit" not in applied
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
        code_commit=closed.code_commit,
    )

    assert created is False
    assert memory_commit == memory_head
    assert memory_commit != recorded
    assert is_ancestor(memory_repo, moved_source, memory_commit)


class _ClosedCacheLeaf:
    """One closed external leaf and the disposable cache derived from its Git attribution."""

    def __init__(self, root: Path) -> None:
        fixture = _authority_fixture(root, external_memory=True)
        closed = _closed_external_leaf_worktrees(fixture, root, publish_closeout_evidence=False)
        assert closed.memory_repo_path is not None and closed.memory_worktree is not None
        assert closed.ledger_path is not None
        self.contract = closed
        self.memory_worktree = closed.memory_worktree
        self.cache_path = closed.ledger_path
        self.canonical = derive_memory_ledger(
            self.memory_worktree, closed.memory_content_commit, repo_name=closed.repo_name
        )
        self.own_row = LedgerRow(closed.code_commit, closed.memory_content_commit)
        self.source_rows = [row for row in self.canonical.rows if row != self.own_row]
        self.content_path = self.memory_worktree / "candidate.md"
        self.content_bytes = self.content_path.read_bytes()
        self.code_path = closed.code_worktree / "candidate.txt"
        self.code_bytes = self.code_path.read_bytes()

    def render(self, rows: list[LedgerRow], *, header: tuple[str, str] | None = None) -> str:
        consistent = replace(
            self.canonical,
            rows=rows,
            last_verified_code_commit=rows[0].code_commit,
            last_memory_content_commit=rows[0].memory_commit,
        )
        text = ledger_to_text(consistent)
        if header is None:
            return text
        return text.replace(f'"{rows[0].code_commit}"', f'"{header[0]}"', 1).replace(
            f'"{rows[0].memory_commit}"', f'"{header[1]}"', 1
        )

    def refresh(self):
        return refresh_memory_cache(self.memory_worktree, repo_name=self.contract.repo_name)

    def git_state(self):
        return tuple(
            (
                _git(repository, "rev-parse", "HEAD"),
                _git(
                    repository,
                    "for-each-ref",
                    "--sort=refname",
                    "--format=%(refname) %(objectname)",
                ),
                tuple(
                    sorted(
                        line
                        for line in _git(
                            repository,
                            "cat-file",
                            "--batch-all-objects",
                            "--batch-check=%(objectname) %(objecttype)",
                        ).splitlines()
                        if line.endswith(" commit")
                    )
                ),
            )
            for repository in (self.contract.code_worktree, self.memory_worktree)
        )

    def assert_content_unchanged(self) -> None:
        assert self.content_path.read_bytes() == self.content_bytes
        assert self.code_path.read_bytes() == self.code_bytes
        assert _git(self.memory_worktree, "ls-files", "--", "memory.md") == ""
        assert _git(self.memory_worktree, "status", "--porcelain") == ""
        assert _git(self.contract.code_worktree, "status", "--porcelain") == ""


def test_cache_refresh_preserves_current_bytes_and_materializes_a_missing_cache(tmp_path):
    """An existing correct cache is untouched, and a missing one is recreated without Git writes."""

    leaf = _ClosedCacheLeaf(tmp_path)
    before_bytes = leaf.cache_path.read_bytes()
    before_git = leaf.git_state()

    current = leaf.refresh()

    assert current["state"] == "current"
    assert leaf.cache_path.read_bytes() == before_bytes
    assert leaf.git_state() == before_git
    leaf.cache_path.unlink()

    rebuilt = leaf.refresh()

    assert rebuilt["state"] == "updated"
    assert leaf.cache_path.read_bytes() == before_bytes
    assert load_ledger(leaf.cache_path) == leaf.canonical
    assert leaf.git_state() == before_git
    leaf.assert_content_unchanged()


def test_cache_refresh_repairs_malformed_forged_and_reordered_data_from_git(tmp_path):
    """Cache corruption changes neither the reconstructed attribution nor any Git object/ref."""

    leaf = _ClosedCacheLeaf(tmp_path)
    own_row = leaf.own_row
    source_row = leaf.source_rows[0]
    malformations = (
        ("malformed text", "<<<<<<< not a ledger\n"),
        (
            "forged memory and code mappings",
            leaf.render(
                [
                    own_row,
                    LedgerRow(own_row.code_commit, "0" * 40),
                    LedgerRow("f" * 40, own_row.memory_commit),
                    *leaf.source_rows,
                ]
            ),
        ),
        ("reordered rows", leaf.render([source_row, own_row])),
        (
            "header disagrees with its first row",
            leaf.render(
                [own_row, *leaf.source_rows],
                header=(source_row.code_commit, source_row.memory_commit),
            ),
        ),
    )
    before_git = leaf.git_state()
    for name, text in malformations:
        leaf.cache_path.write_text(text, encoding="utf-8")

        refreshed = leaf.refresh()

        assert refreshed["state"] == "updated", name
        assert load_ledger(leaf.cache_path) == leaf.canonical, name
        assert leaf.cache_path.read_text(encoding="utf-8") == ledger_to_text(leaf.canonical), name
        assert leaf.git_state() == before_git, name
        leaf.assert_content_unchanged()
