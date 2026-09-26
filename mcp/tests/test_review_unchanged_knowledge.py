"""Code-only curation uses the existing comparison producer without inventing knowledge writes."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from agents_remember.application.knowledge_review import (
    list_knowledge_review_entries,
    read_knowledge_review,
)
from agents_remember.application.review_comparison_generation import (
    read_generation_refs,
    read_manifest,
)
from agents_remember.cli.__main__ import main
from test_review_recorded_knowledge import _closed_endpoints

pytestmark = pytest.mark.evidence_unit


def _invocation(tmp_path: Path):
    fixture = _closed_endpoints(tmp_path)
    resolved = fixture.resolve()
    shutil.rmtree(resolved.baseline_database.parent)
    shutil.rmtree(resolved.candidate_database.parent)
    fixture.config.config_path.write_text(
        json.dumps(
            {
                "workspaceRoot": str(fixture.config.workspace_root),
                "coordinationRoot": str(fixture.config.coordination_root),
                "repositories": {},
            }
        )
    )
    argv = [
        "review-record-comparison",
        "--config",
        str(fixture.config.config_path),
        "--contract",
        str(fixture.contract.contract_path),
        "--unchanged-knowledge",
        "--json",
    ]
    return fixture, resolved, argv


def test_code_only_recorder_retains_unchanged_knowledge_and_reopens_after_cleanup(
    tmp_path: Path, capsys
):
    fixture, _, argv = _invocation(tmp_path)
    assert main(argv) == 0, capsys.readouterr().out
    report = json.loads(capsys.readouterr().out)
    assert report["state"] == "published"
    refs = read_generation_refs(fixture.contract.task_root, fixture.contract.leaf_id)
    assert len(refs) == 1
    manifest = read_manifest(refs[0].directory / "manifest.json")
    assert manifest.knowledge[0].identity == manifest.knowledge[1].identity
    assert all(binding.state == "retained" for binding in manifest.knowledge)
    live_entries = list_knowledge_review_entries(
        fixture.config, fixture.repository_id, fixture.master, fixture.contract.leaf_id
    )
    assert live_entries.state == "entries" and live_entries.family_total > 0
    live = read_knowledge_review(fixture.config, fixture.request(fixture.diff.sibling_invariant_id))
    assert live.state == "review" and live.payload is not None
    assert (
        live.payload.knowledge.before_statement.text == live.payload.knowledge.after_statement.text
    )
    assert live.payload.knowledge.before_statement.state == "present"
    assert fixture.resolve().closed_leaf is None
    shutil.rmtree(fixture.contract.worktree_group)
    reopened = fixture.resolve()
    assert reopened.closed_leaf is not None and reopened.closed_leaf.manifest == manifest


@pytest.mark.parametrize("changed", ["published", "candidate", "missing", "namespace"])
def test_unchanged_option_refuses_changed_missing_or_unpublished_knowledge(
    tmp_path: Path, capsys, changed
):
    fixture, resolved, argv = _invocation(tmp_path)
    memory = fixture.contract.memory_worktree
    assert memory is not None
    published = memory / "knowledge.sqlite"
    if changed == "missing":
        published.unlink()
    elif changed == "candidate":
        resolved.candidate_database.parent.mkdir(parents=True)
        shutil.copyfile(fixture.diff.after.database_path, resolved.candidate_database)
    elif changed == "namespace":
        other = _closed_endpoints(tmp_path / "other")
        shutil.copyfile(other.diff.before.database_path, published)
    else:
        shutil.copyfile(fixture.diff.after.database_path, published)
    assert main(argv) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["state"] == "refused", report
    assert not read_generation_refs(fixture.contract.task_root, fixture.contract.leaf_id)


@pytest.mark.parametrize("invalid", ["generation", "origin", "historical-absence"])
def test_known_invalid_pair_is_refused_before_any_placement(tmp_path: Path, capsys, invalid):
    fixture, resolved, argv = _invocation(tmp_path)
    if invalid == "historical-absence":
        argv += ["--historical-absence", "before"]
    else:
        resolved.baseline_database.parent.mkdir(parents=True)
        (resolved.baseline_database.parent / f"baseline-{invalid}.json").write_text("invalid json")
    root = resolved.baseline_database.parent.parent
    before = {
        path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }
    assert main(argv) == 2
    output = capsys.readouterr().out
    assert (
        "historical-absence" in output if invalid == "historical-absence" else "refused" in output
    )
    after = {
        path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }
    assert after == before
    assert not read_generation_refs(fixture.contract.task_root, fixture.contract.leaf_id)
