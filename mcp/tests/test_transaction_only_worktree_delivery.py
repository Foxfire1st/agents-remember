"""Focused CCR-R12 transaction-only closeout and integration regressions."""

from __future__ import annotations

import ast
import json
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from unittest import mock

import pytest
from agents_remember.application import worktree_tools
from agents_remember.application.worktree_tool_requests import (
    CloseoutApproval,
)
from agents_remember.kernel.memory_cache import derive_memory_ledger
from agents_remember.kernel.memory_ledger import (
    load_ledger,
)
from agents_remember.models.lifecycles.operation import LifecycleOperationRecoveryCommits
from agents_remember.worktrees import git_worktree_manager
from agents_remember.worktrees.integration.lifecycle import lifecycle_operations
from agents_remember.worktrees.modules import closeout_external
from agents_remember.worktrees.modules import git as git_owner
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.closeout_external import _commit_memory_content
from agents_remember.worktrees.modules.git import is_ancestor
from agents_remember.worktrees.worktree_contract import load_contract
from closeout_input_test_support import (
    ensure_fixture_waiting_door,
)
from integration_branch_authority_test_support import (
    _authority_fixture,
    _closed_external_leaf_worktrees,
)
from test_knowledge_closeout_gate import CODE_A, LEAF, _edit, build_gated
from test_knowledge_reopen import _public_closeout, _rows_for_the_edit, _write
from test_source_lineage import _commit_on, _fixture, _git
from transaction_only_worktree_delivery_test_support import (
    MESSAGES,
    SEAMS,
    UNADMITTED,
    _apply,
    _assert_landed_subject,
    _assert_memory_attribution,
    _assert_no_profile_or_review,
    _bind_task_without_review,
    _branch_race,
    _closeout_through_failing_then_passing_hooks,
    _code_and_memory_state,
    _converted_closeout_fixture,
    _disturbed,
    _effective_closeout_input,
    _forbid_acceptance_tools,
    _install_failing_pre_commit_hooks,
    _memory_preparations,
    _public_config,
    _seed_leaf_trace_row,
)


@pytest.mark.parametrize("late", ("worktree", "index"))
def test_ordinary_code_commit_is_the_admitted_tree_and_keeps_a_later_edit(
    tmp_path, worktree_services, late
):
    """R5-3 for the code side: a file edited while the commit object is written is not committed
    and does not refuse the closeout; it stays an uncommitted change."""

    contract, config = _converted_closeout_fixture(tmp_path)
    native = git_owner.run_git
    seen: list[str] = []

    def edited_at_commit(repository, args, *extra):
        if args[0] == "commit-tree" and repository == contract.code_worktree:
            seen.append(args[1])
            path = repository / "feature.py"
            path.write_text("late user bytes\n", encoding="utf-8")
            if late == "index":
                _git(repository, "add", "--", "feature.py")
                path.write_text("VALUE = 1\n", encoding="utf-8")
        return native(repository, args, *extra)

    with mock.patch.object(git_owner, "run_git", edited_at_commit):
        applied = _apply(config, contract)
    assert applied["ok"] is True, applied
    code = contract.code_worktree
    assert _git(code, "rev-parse", "HEAD^{tree}") == seen[0]
    assert _git(code, "show", "HEAD:feature.py") == "VALUE = 1"
    assert _git(code, "diff", "--name-only") == "feature.py"  # the index and worktree differ
    assert _git(code, "diff", "--cached", "--name-only") == (
        "feature.py" if late == "index" else ""
    )


@pytest.mark.parametrize("seam", SEAMS)
def test_a_moved_code_branch_refuses_the_closeout_and_a_retry_completes_it(
    tmp_path, worktree_services, seam
):
    contract, config = _converted_closeout_fixture(tmp_path)
    before = _code_and_memory_state(contract)
    with (
        _branch_race(contract.code_worktree, seam),
        pytest.raises(RuntimeError, match=SEAMS[seam]),
    ):
        _apply(config, contract)
    code = contract.code_worktree
    assert _git(code, "rev-list", "--all", "--count") == _git(code, "rev-list", "HEAD", "--count")
    after = _code_and_memory_state(contract)
    assert after[0] != before[0] and after[1:3] == before[1:3] and after[3:] == before[3:]
    assert _git(code, "log", "-1", "--format=%s") == "foreign"

    applied = _apply(config, contract)  # the cause is gone: a plain retry completes
    assert applied["ok"] is True, applied
    assert _git(code, "show", "HEAD:feature.py") == "VALUE = 1"
    _assert_landed_subject(code, MESSAGES.code)


@pytest.mark.parametrize("seam", SEAMS)
def test_a_moved_memory_branch_leaves_the_pair_to_the_recovery_a_retry_runs(
    tmp_path, worktree_services, seam
):
    """The code commit landed, the memory publication was refused, nothing of the memory side
    stayed behind, and the rerun finishes the same pair on the code commit that landed."""

    contract, config = _converted_closeout_fixture(tmp_path)
    memory = contract.memory_worktree
    assert memory is not None
    memory_before = tuple(
        _git(memory, *arguments)
        for arguments in (("ls-files", "--stage"), ("status", "--porcelain=v1", "-uall"))
    )
    with _branch_race(memory, seam), pytest.raises(RuntimeError, match=SEAMS[seam]):
        _apply(config, contract)
    landed_code = _git(contract.code_worktree, "rev-parse", "HEAD")
    _assert_landed_subject(contract.code_worktree, MESSAGES.code)
    assert _git(memory, "rev-list", "--all", "--count") == _git(
        memory, "rev-list", "HEAD", "--count"
    )
    assert (
        _git(memory, "ls-files", "--stage"),
        _git(memory, "status", "--porcelain=v1", "-uall"),
    ) == memory_before
    assert load_contract(contract.contract_path).closeout_status != "completed"

    applied = _apply(config, contract)
    assert applied["ok"] is True, applied
    closed = load_contract(contract.contract_path)
    assert closed.closeout_status == "completed" and closed.code_commit == landed_code
    assert _git(memory, "show", "HEAD:feature.md") == "# Feature"
    assert _git(memory, "rev-parse", "HEAD") == closed.memory_content_commit


@pytest.mark.parametrize("refusal", (*SEAMS, "switched"))
def test_a_refused_converted_memory_publication_gives_back_every_preparation(
    tmp_path, worktree_services, refusal
):
    """On converted memory the closeout closes the leaf's history and may add an ignore rule. A
    branch that moved (before the tip check or after the commit object was written), or a HEAD
    switched to another branch, refuses at publication: the history file is open again byte for
    byte, the ignore rule is gone, the index is as it was, and the rerun after the cause is gone
    completes."""

    world = build_gated(tmp_path)
    _edit(world, CODE_A.replace("return value", "return -value"))
    _write(world, _rows_for_the_edit())
    history = world.memory / f"knowledge/history/{LEAF}.json"
    ignore = world.memory / ".gitignore"
    opened = history.read_bytes(), ignore.exists()
    index = _git(world.memory, "ls-files", "--stage")
    status = _git(world.memory, "status", "--porcelain=v1", "-uall")
    _git(world.memory, "branch", "elsewhere")
    published = closeout_external.publish_tree_commit

    def switched(repository, message, **kwargs):
        _git(repository, "symbolic-ref", "HEAD", "refs/heads/elsewhere")
        try:
            return published(repository, message, **kwargs)
        finally:
            _git(repository, "symbolic-ref", "HEAD", kwargs["before"].headRef)

    if refusal == "switched":
        race = mock.patch.object(closeout_external, "publish_tree_commit", switched)
        match = "HEAD names refs/heads/elsewhere.*nothing was published; "
    else:
        race = _branch_race(world.memory, refusal)
        match = SEAMS[refusal]
    with race, pytest.raises(RuntimeError, match=match):
        _public_closeout(world)
    assert (history.read_bytes(), ignore.exists()) == opened
    assert json.loads(history.read_text())["closed"] is False
    assert _git(world.memory, "ls-files", "--stage") == index
    # The closeout's metadata refresh (``overview.index.json``) is not a preparation it takes back.
    assert [
        row
        for row in _git(world.memory, "status", "--porcelain=v1", "-uall").splitlines()
        if not row.endswith("overview.index.json")
    ] == status.splitlines()
    # No ref reaches a commit the closeout made: everything reachable is on the current branch.
    assert _git(world.memory, "rev-list", "--all", "--count") == _git(
        world.memory, "rev-list", "HEAD", "--count"
    )

    closed = _public_closeout(world)
    assert closed.payload["state"] == "closed"
    memory = closed.payload["memory_content_commit"]
    assert json.loads(_git(world.memory, "show", f"{memory}:knowledge/history/{LEAF}.json"))[
        "closed"
    ]


@pytest.mark.parametrize(("event", "side", "moment"), UNADMITTED)
def test_the_publication_is_bound_to_the_branch_and_the_head_the_closeout_admitted(
    tmp_path, worktree_services, event, side, moment
):
    """Review R3, findings 2 and 3, through ``worktree_closeout_apply`` on converted memory. A
    checkout switched to another branch, detached, or moved by someone else's commit between the
    judgment and the publication refuses: no branch receives a commit of the refused side, the
    leaf's history file and the ignore rule are as before, and the plain rerun completes."""

    contract, config = _converted_closeout_fixture(tmp_path)
    assert contract.memory_worktree is not None
    repository = contract.code_worktree if side == "code" else contract.memory_worktree
    branch = _git(repository, "symbolic-ref", "HEAD")
    before = _memory_preparations(contract), _code_and_memory_state(contract)[:3]
    with _disturbed(contract, event, side, moment) as fired:
        with pytest.raises(RuntimeError, match=UNADMITTED[event, side, moment]):
            _apply(config, contract)
        assert fired
        # Nothing of the refused side is committed anywhere: every ref of the repository names
        # what it named, except the branch someone else moved.
        foreign = _git(repository, "rev-parse", "HEAD") if event == "moved" else None
        _git(repository, "symbolic-ref", "HEAD", branch)
        commits = set(_git(repository, "rev-list", "--all").split())
        assert _git(repository, "log", "-1", "--format=%s") in {"foreign", "base", "memory base"}
        assert _git(repository, "rev-parse", "elsewhere") in commits - {foreign}
        if side == "code":
            assert _code_and_memory_state(contract)[1:3] == before[1][1:3]
        else:  # the code commit of the pair landed; the rerun completes the pair on it
            _assert_landed_subject(contract.code_worktree, MESSAGES.code)
        assert _memory_preparations(contract) == before[0]

        applied = _apply(config, contract)  # the cause is gone: the plain rerun completes
    assert applied["ok"] is True and applied["state"] == "closed", applied
    closed = load_contract(contract.contract_path)
    memory = contract.memory_worktree
    assert _git(memory, "rev-parse", "HEAD") == closed.memory_content_commit
    assert _git(contract.code_worktree, "rev-parse", "HEAD") == closed.code_commit
    history = _git(memory, "show", f"HEAD:knowledge/history/{contract.leaf_id}.json")
    assert json.loads(history)["closed"] is True
    for repo in (contract.code_worktree, memory):
        assert _git(repo, "rev-parse", "elsewhere") != _git(repo, "rev-parse", "HEAD")


def test_public_closeout_commits_code_and_memory_without_acceptance_tools(
    tmp_path, worktree_services
):
    """Closeout is a Git transaction even when quality/review authorities are absent."""

    fixture = _fixture(
        tmp_path, external_memory=True, selected_profile=False, converted_memory=True
    )
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
    _seed_leaf_trace_row(contract, "leaf-1", "onboarding:feature.py")
    ensure_fixture_waiting_door(contract, force_synthetic=True)
    contract = load_contract(contract.contract_path)
    _bind_task_without_review(contract)
    config = _public_config(tmp_path, contract)
    _assert_no_profile_or_review(config, contract)
    # Closeout runs in this process: no detached worker, no operation record. The
    # two commits and their ancestry are the whole record of what it did.
    applied, code_hook_log, memory_hook_log = _closeout_through_failing_then_passing_hooks(
        config, contract, MESSAGES, tmp_path
    )
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
    # The one-line code message's `%s` carries the fact; the memory message's first
    # paragraph is bare and its trailers are pinned by `_assert_memory_attribution`.
    _assert_landed_subject(contract.code_worktree, MESSAGES.code, hook=".git/hooks/pre-commit")
    assert contract.memory_content_commit is not None
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
        hook=".git/hooks/pre-commit",
    )
    # MIK-R152: both hooks ran for the landing that published these commits.
    assert code_hook_log.read_text(encoding="utf-8").splitlines() == ["transaction-hook-invoked"]
    assert memory_hook_log.read_text(encoding="utf-8").splitlines() == ["transaction-hook-invoked"]


def test_closeout_recovery_attributes_the_memory_commit_it_still_owed(tmp_path, worktree_services):
    """Resume an interrupted public closeout with a missing cache and only its memory commit owed."""

    fixture = _fixture(
        tmp_path, external_memory=True, selected_profile=False, converted_memory=True
    )
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
    _seed_leaf_trace_row(contract, "leaf-1", "onboarding:feature.py")
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
        applied = worktree_tools.worktree_integrate_tool(
            config,
            contract_path=closed.contract_path.as_posix(),
            strategy="ff-only",
            dry_run=False,
        )

    # Integration runs in this process: the refs themselves are the record.
    assert applied["ok"] is True, applied
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


@pytest.mark.usefixtures("worktree_services")
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


@pytest.mark.parametrize("name", ("repository-certification-profile", "worktrees-c09"))
def test_l56_required_references_separate_requested_dagger_from_ordinary_closeout(name):
    """R56 rule 5: certification is requested, not an automatic closeout gate."""
    root = Path(__file__).resolve().parents[2]
    text = (root / f"docs/reference/{name}.md").read_text(encoding="utf-8")
    assert "Requested certification runs through Dagger and commits nothing" in text
    assert "Ordinary closeout runs no gate but the invariant gate" in text
    assert "closeout Gates 1\u20134" not in text
    assert "Closeout reconstructs the exact staged candidate" not in text


RETIRED_PUBLICATION_CLAIMS = (
    "prepared closeout",
    "Prepared code commits",
    "Prepared memory commits",
)


def test_l56_publication_code_pins_both_live_commit_hook_mechanisms():
    """Remove retired policy claims without weakening live hook guarantees (the module)."""
    root = Path(__file__).resolve().parents[2]
    text = (root / "mcp/src/agents_remember/worktrees/modules/git.py").read_text(encoding="utf-8")
    assert "commit-tree" in text and "reference-transaction" in text
    assert "pre-commit" in text
    for obsolete in RETIRED_PUBLICATION_CLAIMS:
        assert obsolete not in text


def test_l56_closeout_skill_names_the_live_hook_guarantee_without_prepared_policy():
    """Remove retired policy claims without weakening live hook guarantees (the skill text).

    The skill states the two guarantees a landing reader acts on -- the pre-commit check that
    refuses a failing landing and the reference-transaction hook on a branch move -- and names no
    retired prepared policy. It carries no copy of the module's plumbing names.
    """
    root = Path(__file__).resolve().parents[2]
    text = (root / "skills/w-02-light-task-workflow/workflows/closeout.md").read_text(
        encoding="utf-8"
    )
    assert "pre-commit" in text and "reference-transaction" in text
    for obsolete in RETIRED_PUBLICATION_CLAIMS:
        assert obsolete not in text


def test_l56_renderer_prose_names_only_surviving_content_routes_and_exact_backfill():
    root = Path(__file__).resolve().parents[2] / "mcp/src/agents_remember"
    tree = ast.parse((root / "kernel/memory_attribution.py").read_text(encoding="utf-8"))
    renderer = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "render_memory_content_message"
    )
    text = ast.get_docstring(renderer)
    assert text is not None
    assert "three memory-content routes" in text
    assert "direct landing" in text and "baseline adoption" in text
    assert "Carryover commits nothing" in text
    assert "kernel/memory_backfill.py" in text and "original commit's tree" in text
    assert "prepared memory-content leg" not in text
    assert "body is kept byte for byte" in text and "own final" in text


@pytest.mark.parametrize(
    "path,obsolete",
    (
        ("observer/projection.py", "histogram + a bounded leaderboard"),
        ("serving/projections/snapshots.py", "git-free sidecar staleness"),
        ("serving/change_watcher.py", "sidecar staleness / route coverage / ledgers"),
        ("serving/projections/projection_store.py", "Staleness/coverage/ledger panels"),
    ),
)
def test_l56_reader_prose_names_surviving_analytics_without_stamp_age_descriptors(path, obsolete):
    root = Path(__file__).resolve().parents[2] / "mcp/src/agents_remember"
    text = (root / path).read_text(encoding="utf-8")
    assert obsolete not in text
