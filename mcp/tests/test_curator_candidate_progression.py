"""Ordinary curation publishes actual pre-closeout source without losing an earlier candidate."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import agents_remember.application.knowledge_curator_ingest as ingest
import pytest
from agents_remember.application.curator_candidate_source import capture_curator_code
from agents_remember.application.knowledge_curator_ingest import (
    IngestSelection,
    ingest_curator_list,
)
from agents_remember.application.knowledge_write_admission import enclosure_admission
from agents_remember.cli.__main__ import main
from agents_remember.memory.knowledge.candidate_progression import (
    CandidateCodeProgression,
    progress_candidate_code,
    read_candidate_predecessor,
)
from agents_remember.memory.knowledge.candidate_receipt import (
    resolution_from_receipt,
    write_candidate_receipt,
)
from agents_remember.memory.knowledge.candidate_workspace import open_candidate
from agents_remember.memory.knowledge.logical import dataset_identity
from agents_remember.models.knowledge.repository import RepositoryIdentity
from agents_remember.models.knowledge.snapshot import (
    AdmittedCandidateDestination,
    build_candidate_receipt,
)
from agents_remember.worktrees.worktree_contract import write_contract
from diff_scope_test_support import _git
from read_scope_test_support import REPOSITORY_AUTHORITY_HOME
from test_knowledge_curator_ingest_list import entry, target
from test_review_unchanged_knowledge import _invocation as _endpoint_invocation

pytestmark = pytest.mark.evidence_unit


def _invocation(root: Path):
    """Give the endpoint fixture the authority home its authored database actually declares."""
    fixture, resolved, argv = _endpoint_invocation(root)
    old = fixture.contract.task_root.parent
    new = old.with_name(REPOSITORY_AUTHORITY_HOME)
    old.rename(new)
    memory = fixture.contract.memory_repo_path
    assert memory is not None and fixture.contract.memory_worktree is not None
    named_memory = memory.with_name(f"ar-{REPOSITORY_AUTHORITY_HOME}")
    memory.rename(named_memory)
    _git(named_memory, ["worktree", "repair", str(fixture.contract.memory_worktree)])
    contract = replace(
        fixture.contract,
        repo_name=REPOSITORY_AUTHORITY_HOME,
        task_root=new / fixture.contract.task_root.name,
        contract_path=new / fixture.contract.contract_path.relative_to(old),
        memory_repo_path=named_memory,
    )
    write_contract(contract.contract_path, contract)
    argv[argv.index("--contract") + 1] = str(contract.contract_path)
    return replace(fixture, contract=contract), resolved, argv


def _published(fixture) -> Path:
    assert fixture.contract.memory_worktree is not None
    return fixture.contract.memory_worktree / "knowledge.sqlite"


def _invoke(argv, capsys):
    capsys.readouterr()
    assert main(argv) == 0, capsys.readouterr().out
    return json.loads(capsys.readouterr().out)


def _hand_off(path: str, key: str):
    result = entry(key, targets=[target(path, locator={"kind": "file"})])
    result["scope"] = {
        "applicability": "The named integration source and rule.",
        "conditions": [],
        "exclusions": [],
    }
    return result


def _source_change(fixture, root: Path):
    modified = fixture.worktree / "src/integration.py"
    modified.write_text(modified.read_text() + "\n# uncommitted change for curation\n")
    added = fixture.worktree / "src/new_rule.py"
    added.write_text("def new_rule():\n    return True\n")
    listed = root / "handoff.json"
    listed.write_text(
        json.dumps(
            [_hand_off("src/integration.py", "modified"), _hand_off("src/new_rule.py", "added")]
        )
    )
    return listed


def _ingest_argv(fixture, listed: Path):
    return [
        "knowledge-ingest",
        "--contract",
        str(fixture.contract.contract_path),
        "--list",
        str(listed),
        "--authorization-ref",
        "test:ordinary-curation",
        "--baseline",
        str(_published(fixture)),
        "--publish",
        "--json",
    ]


def _revisions(database: Path):
    with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as connection:
        return set(connection.execute("SELECT revision_id, statement FROM invariant_revision"))


def test_live_source_curation_progresses_preserving_prior_knowledge_and_exact_retry(
    tmp_path: Path, capsys
):
    fixture, resolved, record = _invocation(tmp_path / "case")
    first = _invoke(record, capsys)
    manifest = Path(first["manifest_path"]).read_bytes()
    baseline = resolved.baseline_database.read_bytes()
    before = _revisions(resolved.candidate_database)
    pending = ingest_curator_list(
        fixture.contract,
        [_hand_off("src/integration.py", "pending")],
        IngestSelection(
            candidate_directory=resolved.candidate_database.parent,
            authorization_ref="test:pending",
            baseline=_published(fixture),
            dry_run=False,
        ),
    )
    assert pending.committed and not pending.refused
    unpublished = _revisions(resolved.candidate_database)
    assert unpublished > before
    listed = _source_change(fixture, tmp_path)
    code_head = _git(fixture.worktree, ["rev-parse", "HEAD"])
    index = Path(_git(fixture.worktree, ["rev-parse", "--git-path", "index"]))
    original_index = index.read_bytes()
    argv = _ingest_argv(fixture, listed)
    planned = _invoke(argv, capsys)
    assert len(planned["committed"]) == 2 and not planned["refused"]
    result = _invoke([*argv, "--commit"], capsys)
    assert len(result["committed"]) == 2 and not result["refused"], result
    assert result["codeTreeSource"] == "future-code-candidate"
    assert result["publication"]["state"] == "published"
    assert result["publishedIdentity"]["state"] == "confirmed", result["publishedIdentity"]
    published = _published(fixture)
    assert _revisions(published) > unpublished
    assert resolved.baseline_database.read_bytes() == baseline
    assert Path(first["manifest_path"]).read_bytes() == manifest
    allocated = (
        resolved.candidate_database.parent / "curator-allocation-journal.json"
    ).read_bytes()
    stored = _revisions(published)
    retry = _invoke([*argv, "--commit"], capsys)
    assert not retry["refused"] and _revisions(published) == stored
    assert (
        resolved.candidate_database.parent / "curator-allocation-journal.json"
    ).read_bytes() == allocated
    assert index.read_bytes() == original_index
    assert _git(fixture.worktree, ["rev-parse", "HEAD"]) == code_head


@pytest.mark.parametrize("phase", ["after-plan", "before-publication"])
def test_source_movement_refuses_before_affected_write_or_publication(
    tmp_path: Path, capsys, monkeypatch, phase
):
    fixture, resolved, record = _invocation(tmp_path / "case")
    _invoke(record, capsys)
    listed = _source_change(fixture, tmp_path)
    published = _published(fixture)
    destination_before = published.read_bytes()
    candidate_before = dataset_identity(resolved.candidate_database)
    receipt_before = (resolved.candidate_database.parent / "candidate-receipt.json").read_bytes()
    method = "_plan_entries" if phase == "after-plan" else "_run"
    original = getattr(ingest, method)
    source = fixture.worktree / "src/new_rule.py"

    def moved(*args, **kwargs):
        result = original(*args, **kwargs)
        source.write_text(source.read_text() + "# concurrent source change\n")
        return result

    monkeypatch.setattr(ingest, method, moved)
    result = _invoke([*_ingest_argv(fixture, listed), "--commit"], capsys)
    assert published.read_bytes() == destination_before
    if phase == "after-plan":
        assert not result["committed"] and len(result["refused"]) == 2
        assert "stale_precondition" in result["refused"][0]["refusal"]
        assert dataset_identity(resolved.candidate_database) == candidate_before
        assert (
            resolved.candidate_database.parent / "candidate-receipt.json"
        ).read_bytes() == receipt_before
    else:
        assert result["publication"]["state"] == "refused"
        assert result["publication"]["refusal"]["code"] == "stale_precondition"
        assert len(result["committed"]) == 2


@pytest.mark.parametrize(
    "invalid",
    ["task", "lane", "memory", "namespace", "schema", "corrupt", "moved-receipt", "moved-dataset"],
)
def test_progression_keeps_strict_open_and_refuses_foreign_or_moved_predecessors(
    tmp_path: Path, capsys, invalid
):
    fixture, resolved, record = _invocation(tmp_path / "case")
    _invoke(record, capsys)
    predecessor = read_candidate_predecessor(resolved.candidate_database.parent)
    assert predecessor.receipt is not None and predecessor.identity is not None
    _source_change(fixture, tmp_path)
    proof = capture_curator_code(enclosure_admission(fixture.contract))
    resolution = resolution_from_receipt(predecessor.receipt).model_copy(
        update={"code_tree_id": proof.identity.codeCandidateTree}
    )
    repository = RepositoryIdentity(
        repository_id=predecessor.identity.repository_id, authority_home=REPOSITORY_AUTHORITY_HOME
    )
    if invalid == "task":
        resolution = resolution.model_copy(update={"task_ref": "another-leaf"})
    if invalid == "lane":
        resolution = resolution.model_copy(update={"lane": "task-candidate"})
    if invalid == "memory":
        resolution = resolution.model_copy(update={"memory_tree_id": "b" * 40})
    if invalid == "namespace":
        repository = repository.model_copy(
            update={"repository_id": "33333333-3333-4333-8333-333333333333"}
        )
    receipt_path = resolved.candidate_database.parent / "candidate-receipt.json"
    if invalid == "corrupt":
        receipt_path.write_text("bad json")
    if invalid == "moved-receipt":
        moved = build_candidate_receipt(
            resolution=resolution,
            repository_id=predecessor.receipt.repository_id,
            schema_version=predecessor.receipt.schema_version,
            schema_fingerprint=predecessor.receipt.schema_fingerprint,
        )
        write_candidate_receipt(receipt_path, moved)
    if invalid == "schema":
        unsupported = build_candidate_receipt(
            resolution=resolution_from_receipt(predecessor.receipt),
            repository_id=predecessor.receipt.repository_id,
            schema_version=predecessor.receipt.schema_version,
            schema_fingerprint="0" * 64,
        )
        write_candidate_receipt(receipt_path, unsupported)
        predecessor = read_candidate_predecessor(resolved.candidate_database.parent)
    if invalid == "moved-dataset":
        pending = ingest_curator_list(
            fixture.contract,
            [_hand_off("src/integration.py", "concurrent")],
            IngestSelection(
                candidate_directory=resolved.candidate_database.parent,
                authorization_ref="test:concurrent",
                baseline=_published(fixture),
                dry_run=False,
            ),
        )
        assert pending.committed
        predecessor = predecessor.model_copy(
            update={
                "receipt": read_candidate_predecessor(resolved.candidate_database.parent).receipt
            }
        )
    destination = AdmittedCandidateDestination(
        directory=resolved.candidate_database.parent, repository=repository, resolution=resolution
    )
    if invalid not in {"moved-receipt", "moved-dataset"}:
        assert open_candidate(destination).state == "refused"
    original_receipt = receipt_path.read_bytes()
    original_data = dataset_identity(resolved.candidate_database)
    result = progress_candidate_code(
        destination, CandidateCodeProgression(predecessor, proof.currentness_refusal)
    )
    assert result.state == "refused", result
    assert receipt_path.read_bytes() == original_receipt
    assert dataset_identity(resolved.candidate_database) == original_data
