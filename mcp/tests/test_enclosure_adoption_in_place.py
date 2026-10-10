"""MIK-R82 rule 4 as amended: adoption of operation records written in place.

Every case here fails without the amendment: the dry run refuses
``root-local operation bytes exist without an adoption receipt or legacy
originals`` instead of listing the in-place records, and no receipt marked
found-in-place can be written.
"""

from __future__ import annotations

import dataclasses
import hashlib
import subprocess
from pathlib import Path
from typing import Any

from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
    RepositoryScope,
)
from agents_remember.mcp.tools.enclosure_adoption import (
    EnclosureAdoptionRequest,
    worktree_enclosure_adopt_payload,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    inspect_lifecycle_operation_locator,
)
from agents_remember.worktrees.worktree_contract import write_contract
from lifecycle_enclosure_test_support import enclosure_contract

_RATIONALE = "L82 test: adopt the scratch enclosure with records written in place"
_RECORDS = {
    "closeout-operation.log": b"closeout written in place\n",
    "integrate-operation.log": b"integrate written in place\n",
}


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise AssertionError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def _in_place_world(base: Path) -> tuple[McpRuntimeConfig, Path, Path]:
    """A coordination root whose enclosure holds its records in its lifecycle folder."""

    root = base / "adopt-in-place-world"
    coordination = root / "coordination"
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

    contract, _text = enclosure_contract(root, kind="leaf")
    contract = dataclasses.replace(
        contract,
        code_repo_path=code_repo,
        code_source_branch="main",
        code_base_commit=head,
    )
    contract.contract_path.parent.mkdir(parents=True, exist_ok=True)
    write_contract(contract.contract_path, contract)

    lifecycle = contract.worktree_group / ".lifecycle"
    lifecycle.mkdir(parents=True, exist_ok=True)
    for name, payload in _RECORDS.items():
        (lifecycle / name).write_bytes(payload)

    config = McpRuntimeConfig(
        config_path=coordination / "settings.json",
        coordination_root=coordination,
        workspace_root=coordination,
        transcript_root=coordination / "logs" / "mcp",
        repositories={"repo": RepositoryScope(repo_id="repo", path=code_repo)},
    )
    assert contract.coordination_root.resolve(strict=False) == coordination.resolve(strict=False)
    return config, contract.contract_path, contract.worktree_group


def _request(
    contract_path: Path, group: Path, *, dry_run: bool = True, **overrides: Any
) -> EnclosureAdoptionRequest:
    arguments: dict[str, Any] = {
        "contract_path": contract_path.as_posix(),
        "expected_worktree_group": group.as_posix(),
        "rationale": _RATIONALE,
        "dry_run": dry_run,
    }
    arguments.update(overrides)
    return EnclosureAdoptionRequest(**arguments)


def _expected_artifact(name: str) -> dict[str, object]:
    payload = _RECORDS[name]
    return {
        "name": name,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
    }


def test_dry_run_lists_in_place_records_and_writes_nothing(tmp_path: Path) -> None:
    """The dry run names the in-place state and lists each record with hash and size."""

    config, contract_path, group = _in_place_world(tmp_path)

    payload = worktree_enclosure_adopt_payload(config, _request(contract_path, group, dry_run=True))

    assert payload["ok"] is True
    assert payload["state"] == "would-adopt-enclosure-found-in-place"
    assert payload["recordsFoundInPlace"] is True
    assert payload["artifacts"] == [
        _expected_artifact("closeout-operation.log"),
        _expected_artifact("integrate-operation.log"),
    ]
    assert payload["nextArgs"]["expected_publication_request_id"] == payload["publicationRequestId"]

    assert not (group / ".lifecycle" / "enclosure-adoption-receipt.json").exists()
    for name, raw in _RECORDS.items():
        assert (group / ".lifecycle" / name).read_bytes() == raw
    assert not (group / "reports").exists()
    assert (
        inspect_lifecycle_operation_locator(config.coordination_root, contract_path).state
        == "missing"
    )


def test_real_run_receipts_in_place_without_copy_or_move(tmp_path: Path) -> None:
    """Approval writes a found-in-place receipt; the records stay where they are."""

    config, contract_path, group = _in_place_world(tmp_path)
    preview = worktree_enclosure_adopt_payload(config, _request(contract_path, group, dry_run=True))
    request_id = preview["publicationRequestId"]

    applied = worktree_enclosure_adopt_payload(
        config,
        _request(
            contract_path,
            group,
            dry_run=False,
            approved=True,
            expected_publication_request_id=request_id,
        ),
    )

    assert applied["ok"] is True
    assert applied["state"] == "enclosure-adopted"
    assert applied["publicationRequestId"] == request_id
    assert applied["recordsFoundInPlace"] is True

    receipt_path = group / ".lifecycle" / "enclosure-adoption-receipt.json"
    assert receipt_path.is_file()
    for name, raw in _RECORDS.items():
        assert (group / ".lifecycle" / name).read_bytes() == raw
    assert not (group / "reports").exists()

    observation = inspect_lifecycle_operation_locator(config.coordination_root, contract_path)
    assert observation.state == "addressable"

    repeated = worktree_enclosure_adopt_payload(
        config,
        _request(
            contract_path,
            group,
            dry_run=False,
            approved=True,
            expected_publication_request_id=request_id,
        ),
    )
    assert repeated["ok"] is True
    assert repeated["publicationRequestId"] == request_id

    preview_again = worktree_enclosure_adopt_payload(
        config, _request(contract_path, group, dry_run=True)
    )
    assert preview_again["ok"] is True
    assert preview_again["state"] == "would-adopt-enclosure-found-in-place"
    assert preview_again["publicationRequestId"] == request_id


def test_contradicting_originals_still_refuse(tmp_path: Path) -> None:
    """Records in place together with differing originals are refused, not adopted."""

    config, contract_path, group = _in_place_world(tmp_path)
    reports = group / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "closeout-operation.log").write_bytes(b"a different original\n")

    payload = worktree_enclosure_adopt_payload(config, _request(contract_path, group, dry_run=True))

    assert payload["ok"] is False
    assert payload["state"] == "operation-location-adoption-conflict"
    assert not (group / ".lifecycle" / "enclosure-adoption-receipt.json").exists()
    assert (group / ".lifecycle" / "closeout-operation.log").read_bytes() == (
        _RECORDS["closeout-operation.log"]
    )
