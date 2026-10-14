"""MIK-R82 rule 5: an enclosure with nothing left ends without adoption.

A leaf with no locator whose worktree folder and work branches do not exist
finishes through ``worktree_abandon`` (integration never started) or
``worktree_cleanup`` (integration completed): the contract records its cleanup
state with the reason, and the result reports nothing to archive and nothing
to delete. A folder left on disk -- or a branch, or the wrong integration
state -- keeps the adoption-first refusal. Every case fails without the
change, where both verbs refuse with ``operation-location-missing``.
"""

from __future__ import annotations

import dataclasses
import shutil
import subprocess
from pathlib import Path

from agents_remember.models.tools.public_roster import PUBLIC_TOOLS
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    inspect_lifecycle_operation_locator,
)
from agents_remember.worktrees.modules import abandon as abandon_module
from agents_remember.worktrees.modules import cleanup as cleanup_module
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.terminal_inventory import (
    terminal_without_remains_reason,
)
from agents_remember.worktrees.worktree_contract import load_contract, write_contract
from lifecycle_enclosure_test_support import byte_tree, enclosure_contract
from read_boundary_oracle_test_support import _snapshot
from test_worktree_support import init_repo


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise AssertionError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def _leafless_enclosure(base: Path, *, integrated: bool) -> Path:
    """One leaf contract with no locator, no folder and no work branches."""

    root = base / ("integrated" if integrated else "never-integrated")
    code_repo = root / "repo"
    code_repo.mkdir(parents=True, exist_ok=True)
    _git(code_repo, "init", "-b", "main")
    _git(code_repo, "config", "user.email", "test@example.invalid")
    _git(code_repo, "config", "user.name", "Agents Remember Tests")
    (code_repo / "README.md").write_text("# Test Repo\n", encoding="utf-8")
    _git(code_repo, "add", "README.md")
    _git(code_repo, "commit", "-m", "base")
    head = _git(code_repo, "rev-parse", "HEAD")
    _git(code_repo, "update-ref", "refs/remotes/origin/main", head)
    _git(code_repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")

    # MIK-R50 (L50): the terminal verbs resolve the memory side's repository identity
    # whatever the mode, so the fixture carries a memory repository built the way L155
    # builds its own (test_worktree_support.init_repo), not a second inline bootstrap.
    # No work branch is created: the leafless enclosure has no remains by construction.
    memory_repo = root / "memory"
    init_repo(memory_repo)

    contract, _text = enclosure_contract(root, kind="leaf")
    contract = dataclasses.replace(
        contract,
        code_repo_path=code_repo,
        code_source_branch="main",
        code_base_commit=head,
        integration_status=("completed" if integrated else "not-started"),
        # The terminal verbs require a nonempty lifecycle id (as in
        # test_external_memory_branch_stays_outside_exception below).
        lifecycle_id="l159-no-remains-fixture",
    )
    contract.contract_path.parent.mkdir(parents=True, exist_ok=True)
    write_contract(contract.contract_path, contract)
    assert not contract.worktree_group.exists()
    return contract.contract_path


def test_abandon_dry_run_reports_nothing_to_archive(
    worktree_services: None, tmp_path: Path
) -> None:
    """A never-integrated leaf with no remains previews the finish, not adoption."""

    contract_path = _leafless_enclosure(tmp_path, integrated=False)

    result = abandon_module.abandon_result(WorktreeArgs(contract_path=contract_path, dry_run=True))

    assert result.returncode == 0, result.payload
    assert result.payload["state"] == "would-abandon"
    assert result.payload["nothingToArchive"] is True
    assert result.payload["nothingToDelete"] is True
    assert result.payload["locatorState"] == "missing"
    assert "nothing to archive" in str(result.payload["reason"])
    assert "nothing to delete" in str(result.payload["reason"])
    assert load_contract(contract_path).cleanup == "pending"


def test_abandon_real_run_finishes_and_records(worktree_services: None, tmp_path: Path) -> None:
    """The real run records ``cleanup=abandoned`` with why, and repeats cleanly."""

    contract_path = _leafless_enclosure(tmp_path, integrated=False)

    result = abandon_module.abandon_result(WorktreeArgs(contract_path=contract_path, approved=True))

    assert result.returncode == 0, result.payload
    assert result.payload["state"] == "abandoned"
    assert result.payload["nothingToArchive"] is True
    assert result.payload["nothingToDelete"] is True
    assert load_contract(contract_path).cleanup == "abandoned"

    repeated = abandon_module.abandon_result(
        WorktreeArgs(contract_path=contract_path, approved=True)
    )
    assert repeated.returncode == 0, repeated.payload
    assert repeated.payload["state"] == "abandoned"
    assert load_contract(contract_path).cleanup == "abandoned"


def test_cleanup_dry_run_then_real_run_finish_without_adoption(
    worktree_services: None, tmp_path: Path
) -> None:
    """An integrated leaf with no remains cleans up with nothing to delete."""

    contract_path = _leafless_enclosure(tmp_path, integrated=True)

    preview = cleanup_module.cleanup_result(WorktreeArgs(contract_path=contract_path, dry_run=True))
    assert preview.returncode == 0, preview.payload
    assert preview.payload["state"] == "would-cleanup"
    assert preview.payload["nothingToArchive"] is True
    assert preview.payload["nothingToDelete"] is True
    assert load_contract(contract_path).cleanup == "pending"

    result = cleanup_module.cleanup_result(WorktreeArgs(contract_path=contract_path, approved=True))
    assert result.returncode == 0, result.payload
    assert result.payload["nothingToArchive"] is True
    assert result.payload["nothingToDelete"] is True
    assert "nothing to archive" in str(result.payload["reason"])
    assert load_contract(contract_path).cleanup == "completed"


def test_folder_on_disk_still_requires_adoption_first(
    worktree_services: None, tmp_path: Path
) -> None:
    """A folder on disk needs the rule-4 adoption, and that tool is registered."""

    for integrated, module, operation in (
        (False, abandon_module, "worktree_abandon"),
        (True, cleanup_module, "worktree_cleanup"),
    ):
        contract_path = _leafless_enclosure(tmp_path / operation, integrated=integrated)
        contract = load_contract(contract_path)
        assert contract.memory_repo_path is not None, "the fixture carries a memory repo"
        if operation == "worktree_cleanup":
            # Cleanup's kept-line gate needs landed tips on both sides; these branches
            # satisfy that prerequisite, and the folder below keeps the adoption-first
            # refusal. Abandon keeps the original folder-only subject: no work branches
            # exist there, so only the folder can trigger the adoption-first refusal.
            _git(contract.code_repo_path, "branch", contract.code_work_branch, "main")
            _git(contract.memory_repo_path, "branch", contract.memory_work_branch, "main")
        (contract.worktree_group / "reports").mkdir(parents=True)
        (contract.worktree_group / "reports" / "integrate-operation.json").write_text(
            "{}\n", encoding="utf-8"
        )

        if operation == "worktree_abandon":
            result = module.abandon_result(WorktreeArgs(contract_path=contract_path, approved=True))
        else:
            result = module.cleanup_result(WorktreeArgs(contract_path=contract_path, dry_run=True))

        assert result.returncode == 2, result.payload
        assert result.payload["nextAction"] == "worktree_enclosure_adopt"
        assert "worktree_enclosure_adopt" in PUBLIC_TOOLS


def test_terminal_without_remains_reason_requires_the_folder_gone(tmp_path: Path) -> None:
    """The no-remains reason is folder-sensitive: a folder alone is remains (MIK-R82 rule 5)."""

    for integrated, operation in (
        (False, "worktree_abandon"),
        (True, "worktree_cleanup"),
    ):
        contract_path = _leafless_enclosure(tmp_path / operation, integrated=integrated)
        contract = load_contract(contract_path)
        (contract.worktree_group / "reports").mkdir(parents=True)
        assert terminal_without_remains_reason(contract, operation=operation) is None
        shutil.rmtree(contract.worktree_group)
        reason = terminal_without_remains_reason(contract, operation=operation)
        assert reason is not None and "MIK-R82" in reason


def test_present_work_branch_still_requires_adoption_first(
    worktree_services: None, tmp_path: Path
) -> None:
    """A surviving work branch is remains: no finish without adoption."""

    contract_path = _leafless_enclosure(tmp_path, integrated=False)
    contract = load_contract(contract_path)
    _git(contract.code_repo_path, "branch", contract.code_work_branch, "main")

    result = abandon_module.abandon_result(WorktreeArgs(contract_path=contract_path, dry_run=True))

    assert result.returncode == 2, result.payload
    assert result.payload["nextAction"] == "worktree_enclosure_adopt"
    # The named remedy must be a registered tool, not a bare string.
    assert "worktree_enclosure_adopt" in PUBLIC_TOOLS


def test_integrated_leaf_abandon_does_not_finish(worktree_services: None, tmp_path: Path) -> None:
    """Abandon is the never-integrated verb: an integrated leaf is refused."""

    contract_path = _leafless_enclosure(tmp_path, integrated=True)

    result = abandon_module.abandon_result(WorktreeArgs(contract_path=contract_path, dry_run=True))

    assert result.returncode == 2, result.payload
    assert result.payload["state"] == "operation-location-missing"
    assert result.payload["nextAction"] == "worktree_enclosure_adopt"
    assert "worktree_enclosure_adopt" in PUBLIC_TOOLS


def test_never_integrated_leaf_cleanup_raises(worktree_services: None, tmp_path: Path) -> None:
    """Cleanup is the integrated verb: a never-integrated leaf is refused."""

    contract_path = _leafless_enclosure(tmp_path, integrated=False)

    before = _snapshot({"world": tmp_path})
    refused = cleanup_module.cleanup_result(WorktreeArgs(contract_path=contract_path, dry_run=True))
    assert refused.returncode == 2
    assert refused.payload["code"] == "integration-incomplete"
    assert refused.payload["error"] == "cleanup requires integration.status completed"
    assert refused.payload["nextStep"] == {
        "nextTool": "worktree_integrate",
        "nextArgs": {"contract_path": contract_path.as_posix()},
        "summary": "Integrate the closeout, then clean up again.",
    }
    assert _snapshot({"world": tmp_path}) == before
    assert "worktree_enclosure_adopt" in PUBLIC_TOOLS


def test_abandon_real_run_persists_reason_in_contract(
    worktree_services: None, tmp_path: Path
) -> None:
    """Reviewer probe mirror: the abandon why survives in the contract bytes.

    Mirrors ``operation_probes.py#terminal-reason-False``: the returned reason
    must also read back from the durable contract, with no invented adoption
    or archive evidence, byte-identical dry runs, and an idempotent repeat.
    """

    contract_path = _leafless_enclosure(tmp_path, integrated=False)
    before = contract_path.read_bytes()

    preview = abandon_module.abandon_result(WorktreeArgs(contract_path=contract_path, dry_run=True))
    assert preview.returncode == 0, preview.payload
    assert contract_path.read_bytes() == before

    result = abandon_module.abandon_result(WorktreeArgs(contract_path=contract_path, approved=True))
    assert result.returncode == 0, result.payload
    reason = str(result.payload["reason"])
    contract_text = contract_path.read_text(encoding="utf-8")
    assert reason in contract_text
    assert load_contract(contract_path).terminal_note == reason
    assert load_contract(contract_path).cleanup == "abandoned"

    contract = load_contract(contract_path)
    assert (
        inspect_lifecycle_operation_locator(
            contract.coordination_root, contract.contract_path
        ).state
        == "missing"
    )

    settled = contract_path.read_bytes()
    repeated = abandon_module.abandon_result(
        WorktreeArgs(contract_path=contract_path, approved=True)
    )
    assert repeated.returncode == 0, repeated.payload
    assert str(repeated.payload["reason"]) == reason
    assert reason in contract_path.read_text(encoding="utf-8")
    assert contract_path.read_bytes() == settled
    assert load_contract(contract_path).cleanup == "abandoned"


def test_cleanup_real_run_persists_reason_in_contract(
    worktree_services: None, tmp_path: Path
) -> None:
    """Reviewer probe mirror: the cleanup why survives in the contract bytes.

    Mirrors ``operation_probes.py#terminal-reason-True`` with the same
    durability, dry-run preservation, and idempotent-repeat proof.
    """

    contract_path = _leafless_enclosure(tmp_path, integrated=True)
    before = contract_path.read_bytes()

    preview = cleanup_module.cleanup_result(WorktreeArgs(contract_path=contract_path, dry_run=True))
    assert preview.returncode == 0, preview.payload
    assert contract_path.read_bytes() == before

    result = cleanup_module.cleanup_result(WorktreeArgs(contract_path=contract_path, approved=True))
    assert result.returncode == 0, result.payload
    reason = str(result.payload["reason"])
    contract_text = contract_path.read_text(encoding="utf-8")
    assert reason in contract_text
    assert load_contract(contract_path).terminal_note == reason
    assert load_contract(contract_path).cleanup == "completed"

    contract = load_contract(contract_path)
    assert (
        inspect_lifecycle_operation_locator(
            contract.coordination_root, contract.contract_path
        ).state
        == "missing"
    )

    settled = contract_path.read_bytes()
    repeated = cleanup_module.cleanup_result(
        WorktreeArgs(contract_path=contract_path, approved=True)
    )
    assert repeated.returncode == 0, repeated.payload
    assert str(repeated.payload["reason"]) == reason
    assert reason in contract_path.read_text(encoding="utf-8")
    assert contract_path.read_bytes() == settled
    assert load_contract(contract_path).cleanup == "completed"


def test_external_memory_branch_stays_outside_exception(
    worktree_services: None, tmp_path: Path
) -> None:
    """A surviving external-memory work branch is remains: no silent finish."""

    contract_path = _leafless_enclosure(tmp_path, integrated=False)
    contract = load_contract(contract_path)
    memory_repo = tmp_path / "memory-repo"
    memory_repo.mkdir(parents=True)
    _git(memory_repo, "init", "-b", "main")
    _git(memory_repo, "config", "user.email", "test@example.invalid")
    _git(memory_repo, "config", "user.name", "Agents Remember Tests")
    (memory_repo / "README.md").write_text("# Memory Repo\n", encoding="utf-8")
    _git(memory_repo, "add", "README.md")
    _git(memory_repo, "commit", "-m", "base")
    head = _git(memory_repo, "rev-parse", "HEAD")
    _git(memory_repo, "update-ref", "refs/remotes/origin/main", head)
    _git(memory_repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    memory_branch = "ar/memory-leaf"
    _git(memory_repo, "branch", memory_branch, "main")
    memory_worktree = tmp_path / "memory-worktree"
    external = dataclasses.replace(
        contract,
        memory_mode="external",
        memory_repo_path=memory_repo,
        memory_source_branch="main",
        memory_work_branch=memory_branch,
        memory_base_commit=head,
        memory_worktree=memory_worktree,
        ledger_path=memory_worktree / "memory.md",
        lifecycle_id="l82-no-remains-probe",
    )
    write_contract(contract_path, external)
    assert not memory_worktree.exists()
    before = contract_path.read_bytes()

    result = abandon_module.abandon_result(WorktreeArgs(contract_path=contract_path, dry_run=True))

    assert result.returncode == 2, result.payload
    assert result.payload.get("nothingToArchive") is not True
    assert result.payload["nextAction"] == "worktree_enclosure_adopt"
    assert "worktree_enclosure_adopt" in PUBLIC_TOOLS
    assert contract_path.read_bytes() == before
    assert load_contract(contract_path).cleanup == "pending"


def test_series_abandon_preview_leaves_populated_logs_and_apply_removes_them(
    worktree_services: None, tmp_path: Path
) -> None:
    """A series enclosure with populated logs previews without touching disk, then removes them."""

    root = tmp_path / "series"
    contract, _text = enclosure_contract(root, kind="series")
    logs = contract.worktree_group / "logs" / "ar-tools-20261012"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "000000000001-task_doc-aaaaaaaa.json").write_text("{}\n", encoding="utf-8")
    (contract.worktree_group / "logs" / "ar-tools-20261012.jsonl").write_text(
        "{}\n", encoding="utf-8"
    )

    before = byte_tree(contract.worktree_group)
    preview = abandon_module._abandon_directories(contract, dry_run=True, force=False)
    assert set(preview) == {"reports", "lifecycle"}
    assert byte_tree(contract.worktree_group) == before

    applied = abandon_module._abandon_directories(contract, dry_run=False, force=False)
    assert set(applied) == {"reports", "lifecycle"}
    assert not (contract.worktree_group / "logs").exists()
