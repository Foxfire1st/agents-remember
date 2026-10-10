"""MIK-R82 rule 3 (copies side): unmanaged citation source indexes are listed and deleted.

``agents-remember knowledge-copies`` lists every citation source index outside the managed root
(``temp/citation-source-index/managed``) with its slot directory, under the rule
``unmanaged-citation-source-index``. The reviewed apply deletes those slot directories whole;
the managed root is never entered, and any index Git tracks is listed and kept -- Git-tracked
files are never deleted. The dry run changes nothing; the apply is bound to the reviewed dry
run's ``reviewDigest`` like every other deletion this command performs.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from agents_remember.application.citation_cache_census import CITATION_CACHE_RULE
from agents_remember.application.legacy_dataset_copies import (
    plan_cleanup,
    run_cleanup,
)

INDEX = "temp/citation-source-index"
SLOT = f"{INDEX}/slot-0"
OTHER_SLOT = f"{INDEX}/slot-1"
MANAGED_SLOT = f"{INDEX}/managed/ns-9f2c"


def _slot(root: Path, relative: str, *, marker: str = "index") -> Path:
    """One citation source index slot: the database beside its manifest and readiness files.

    The manifest carries the citation publisher's own record shape, which is what the command
    identifies a slot by -- never a database file's name.
    """

    slot = root / relative
    slot.mkdir(parents=True, exist_ok=True)
    (slot / "index.sqlite3").write_bytes(b"citation-index-bytes:" + marker.encode())
    (slot / "manifest.json").write_text(
        json.dumps(
            {
                "schemaVersion": 8,
                "codeRoot": str(root / "code"),
                "memoryRoot": str(root / "memory"),
                "snapshotId": "0" * 64,
                "sourceBytes": len(marker),
                "directories": [],
                "files": [],
                "candidateTree": None,
            }
        ),
        encoding="utf-8",
    )
    (slot / "ready.json").write_text(json.dumps({"ready": True}), encoding="utf-8")
    return slot


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def coordination_root(tmp_path: Path) -> Path:
    root = tmp_path / "coordination"
    (root / "tasks" / "proj").mkdir(parents=True, exist_ok=True)
    _slot(root, SLOT)
    _slot(root, OTHER_SLOT, marker="second")
    _slot(root, MANAGED_SLOT, marker="managed")
    foreign = root / INDEX / "foreign"
    foreign.mkdir(parents=True, exist_ok=True)
    (foreign / "index.sqlite3").write_bytes(b"another tool's database")
    (foreign / "manifest.json").write_text(json.dumps({"note": "not a manifest"}), encoding="utf-8")
    return root


def test_the_dry_run_lists_each_unmanaged_index_with_its_slot_and_changes_nothing(
    tmp_path: Path,
) -> None:
    root = coordination_root(tmp_path)
    before = {path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}

    document = plan_cleanup(root)

    caches = {cache["path"]: cache for cache in document["citationCaches"]}
    assert set(caches) == {SLOT, OTHER_SLOT}
    assert caches[SLOT]["rule"] == CITATION_CACHE_RULE
    assert caches[SLOT]["bytes"] == sum(
        path.stat().st_size for path in (root / SLOT).rglob("*") if path.is_file()
    )
    assert set(document["wouldDeleteCaches"]) == {SLOT, OTHER_SLOT}
    assert document["wouldKeepCaches"] == []
    # A foreign database file is never mistaken for an index: the manifest decides.
    assert f"{INDEX}/foreign" not in caches
    assert document["cacheTotals"] == {
        "slots": 2,
        "bytes": sum(cache["bytes"] for cache in document["citationCaches"]),
    }
    assert document["deletedCaches"] == [] and document["mode"] == "dry-run"
    assert {path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()} == before


def test_the_reviewed_apply_deletes_unmanaged_slots_and_keeps_the_managed_one(
    tmp_path: Path,
) -> None:
    root = coordination_root(tmp_path)

    applied = run_cleanup(root, str(plan_cleanup(root)["reviewDigest"]))

    assert set(applied["deletedCaches"]) == {SLOT, OTHER_SLOT}
    assert applied["keptCaches"] == [] and applied["failures"] == []
    assert not (root / SLOT).exists() and not (root / OTHER_SLOT).exists()
    # The managed root is never entered: its index is not listed and stays as it was.
    assert (root / MANAGED_SLOT / "index.sqlite3").is_file()
    assert (root / MANAGED_SLOT / "manifest.json").is_file()
    assert (root / INDEX / "foreign" / "index.sqlite3").is_file()

    again = run_cleanup(root, str(plan_cleanup(root)["reviewDigest"]))
    assert again["deletedCaches"] == [] and again["citationCaches"] == []


def test_a_git_tracked_index_is_listed_and_kept(tmp_path: Path) -> None:
    root = coordination_root(tmp_path)
    repository = root / "temp"
    _git(repository, "init", "-q", "-b", "main")
    _git(repository, "config", "user.email", "fixture@example.invalid")
    _git(repository, "config", "user.name", "cache fixture")
    _git(repository, "add", "-f", "citation-source-index/slot-0/index.sqlite3")
    _git(repository, "commit", "-q", "-m", "track the index")

    document = plan_cleanup(root)

    caches = {cache["path"]: cache for cache in document["citationCaches"]}
    assert caches[SLOT]["rule"] == CITATION_CACHE_RULE
    assert "kept" in caches[SLOT]["ruleText"]
    assert set(document["wouldDeleteCaches"]) == {OTHER_SLOT}
    assert [entry["path"] for entry in document["wouldKeepCaches"]] == [SLOT]

    applied = run_cleanup(root, str(document["reviewDigest"]))

    assert set(applied["deletedCaches"]) == {OTHER_SLOT}
    assert [entry["path"] for entry in applied["keptCaches"]] == [SLOT]
    assert (root / SLOT / "index.sqlite3").is_file()
    assert (
        subprocess.run(
            ["git", "diff", "--name-only"],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == ""
    )


def test_a_cache_added_after_the_review_refuses_the_apply(tmp_path: Path) -> None:
    root = coordination_root(tmp_path)
    digest = str(plan_cleanup(root)["reviewDigest"])
    _slot(root, f"{INDEX}/slot-7", marker="late")

    with pytest.raises(ValueError, match="the scan differs from the reviewed dry run"):
        run_cleanup(root, digest)

    assert (root / SLOT).is_dir() and (root / f"{INDEX}/slot-7").is_dir()
