"""MIK-R212 v3: reported source-file cases through the public synchronous quality route.

The uncited size/count cases already passed on the MIK line before this leaf. Readable ignored,
excluded and binary citations preserve content verification; only ignored and binary citations
carry source-policy notes. Oversized files and links remain unavailable with named reasons.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application.memory_quality.controller import run_memory_quality_request
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.memory_quality.reference_state import git_blob_id
from agents_remember.models.knowledge_files.anchor_content import content_identity
from agents_remember.models.knowledge_files.canonical import canonical_text
from agents_remember.models.memory import MemoryQualitySyncRequest

_FIVE_MIB = 5 * 1024 * 1024
_TEXT = b"# a source fixture\n"


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _init(root: Path) -> None:
    root.mkdir(parents=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "fixture@example.invalid")
    _git(root, "config", "user.name", "Fixture")


def _write(root: Path, relative: str, data: bytes) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@dataclass(frozen=True)
class _World:
    root: Path
    code: Path
    memory: Path
    config: McpRuntimeConfig

    def check(self) -> dict[str, Any]:
        return run_memory_quality_request(
            self.config, MemoryQualitySyncRequest(repo_id="probe", mode="sync")
        )

    def cite(self, relative: str, data: bytes) -> None:
        # The containing card stays visible even when the referenced path starts with a dot.
        # Knowledge readers intentionally exclude hidden memory directories.
        sidecar = {
            "schema": "ar-onboarding-file/v1",
            "path": "baseline.py",
            "realizes": [],
            "references": {
                "1": {
                    "targets": [
                        {
                            "kind": "code",
                            "anchor": {
                                "path": relative,
                                "blob": git_blob_id(data),
                                "locator": {"kind": "file"},
                                "content": content_identity(data),
                            },
                        }
                    ]
                }
            },
        }
        _write(self.memory, "onboarding/baseline.py.json", canonical_text(sidecar).encode())
        _write(
            self.memory,
            "onboarding/baseline.py.md",
            b"# baseline.py\n\nThe source fixture is present. [1]\n",
        )
        _git(self.memory, "add", "-A")
        _git(self.memory, "commit", "-qm", "fixture reference")


def _make_world(tmp_path: Path) -> _World:
    code = tmp_path / "probe"
    coordination = tmp_path / "coordination"
    memory = coordination / "memory-repos/ar-probe"
    _init(code)
    _init(memory)
    _write(code, "baseline.py", _TEXT)
    _git(code, "add", "-A")
    _git(code, "commit", "-qm", "fixture code")
    _write(
        memory,
        "knowledge/layout.json",
        canonical_text({"schema": "ar-memory-layout/v2", "conversion": "1"}).encode(),
    )
    (memory / "onboarding").mkdir()
    _git(memory, "add", "-A")
    _git(memory, "commit", "-qm", "fixture converted memory")
    config = McpRuntimeConfig(
        config_path=coordination / "settings.json",
        coordination_root=coordination,
        workspace_root=tmp_path,
        transcript_root=coordination / "logs",
        repositories={"probe": RepositoryScope(repo_id="probe", path=code, memory_root=memory)},
    )
    return _World(tmp_path, code, memory, config)


@pytest.fixture
def world(tmp_path: Path) -> _World:
    return _make_world(tmp_path)


def _excluded(world: _World) -> None:
    settings = {
        "onboarding": {"pathRules": {"exclude": {"paths": [".build/**", "DerivedData/**"]}}}
    }
    _write(world.memory, "system/settings.json", json.dumps(settings).encode())


def _case(world: _World, case: str) -> tuple[str, bytes]:
    relative, data = "subject.py", _TEXT
    if case == "ignored":
        relative, data = ".build/subject.py", b"#" * _FIVE_MIB
        _write(world.code, ".gitignore", b".build/\n")
    elif case == "large_binary":
        relative, data = "Framework.framework/Framework", b"\x00\xff" * (_FIVE_MIB // 2)
    elif case == "binary":
        relative, data = "subject.bin", b"\x00\xffbinary fixture\n"
    elif case == "oversized":
        data = b"#" * _FIVE_MIB
    elif case == "symlink":
        data = b"#" * _FIVE_MIB
        outside = _write(world.root, "outside/subject.py", data)
        (world.code / relative).symlink_to(outside)
    elif case == "directory_symlink":
        relative = "linkdir/mod.py"
        outside = _write(world.root, "outside/pkg/mod.py", data)
        (world.code / "linkdir").symlink_to(outside.parent, target_is_directory=True)
    elif case == "gitlink":
        relative = "embedded/mod.py"
        embedded = world.code / "embedded"
        _init(embedded)
        _write(embedded, "mod.py", data)
        _git(embedded, "add", "-A")
        _git(embedded, "commit", "-qm", "fixture embedded repository")
        _git(world.code, "add", "embedded")
        assert _git(world.code, "ls-files", "--stage", "--", "embedded").startswith("160000 ")
    elif case.startswith("excluded"):
        _excluded(world)
        relative = ".build/subject.py" if case == "excluded_build" else "DerivedData/subject.py"
    else:
        raise AssertionError(case)
    if case not in {"symlink", "directory_symlink", "gitlink"}:
        _write(world.code, relative, data)
    _git(world.code, "add", "-A")
    _git(world.code, "commit", "-qm", "fixture source case")
    return relative, data


def _assert_uncited_reported_size_and_symlink_cases(world: _World, case: str) -> None:
    """R212.1: these reported failures already pass on the converted MIK line."""
    relative, _ = _case(world, case)
    result = world.check()
    assert result["ok"] is True, result
    assert result["status"] == "passed", result
    if case == "ignored":
        source_files = result["checks"]["knowledge.converted"]["sourceFiles"]
        assert all(row["path"] != relative for row in source_files["skippedFiles"])
        assert source_files["skippedCount"] == 0, source_files


def _assert_uncited_skipped_files_are_reported_by_the_listing(
    world: _World, case: str, reason_words: tuple[str, ...]
) -> None:
    """R212.2: skipped files are visible without any reference into them."""
    relative, _ = _case(world, case)
    result = world.check()
    assert result["ok"] is True, result
    assert result["status"] == "passed", result
    source_files = result["checks"]["knowledge.converted"]["sourceFiles"]
    rows = source_files["skippedFiles"]
    assert source_files["skippedCount"] == len(rows) == 1, source_files
    assert rows[0]["path"] == relative, rows
    reason = rows[0]["reason"].lower()
    assert "not indexed" in reason, rows
    assert all(word in reason for word in reason_words), rows


def _assert_uncited_path_rules_case(world: _World) -> None:
    """R212.3: pathRules do not remove either build directory from the citation listing."""
    _case(world, "excluded_build")
    _case(world, "excluded_derived")
    result = world.check()
    assert result["ok"] is True, result
    assert result["status"] == "passed", result
    source_files = result["checks"]["knowledge.converted"]["sourceFiles"]
    assert source_files["indexedCount"] == 3, source_files
    assert source_files["skippedCount"] == 0, source_files


def _assert_uncited_file_count_above_old_limit(world: _World) -> None:
    """R212.1: a full sync accepts 100 files and then more than 100,000 ordinary files."""
    for index in range(100):
        _write(world.code, f"many/{index // 1000}/{index}.py", _TEXT)
    first = world.check()
    assert first["ok"] is True, first
    assert first["checks"]["knowledge.converted"]["sourceFiles"]["indexedCount"] == 101
    for index in range(100, 100_001):
        _write(world.code, f"many/{index // 1000}/{index}.py", _TEXT)
    second = world.check()
    assert second["ok"] is True, second
    assert second["status"] == "passed", second
    assert second["checks"]["knowledge.converted"]["sourceFiles"]["indexedCount"] == 100_002


def test_uncited_repository_shapes(tmp_path: Path, subtests: pytest.Subtests) -> None:
    """One fixture family preserves all reported uncited and listing assertions."""
    for case in ("ignored", "large_binary", "symlink"):
        with subtests.test(case="reported-" + case):
            _assert_uncited_reported_size_and_symlink_cases(
                _make_world(tmp_path / ("reported-" + case)), case
            )
    for case, reason in (("oversized", "4 mib"), ("binary", "binary"), ("large_binary", "4 mib")):
        with subtests.test(case="skipped-" + case):
            _assert_uncited_skipped_files_are_reported_by_the_listing(
                _make_world(tmp_path / ("skipped-" + case)), case, (reason,)
            )
    with subtests.test(case="path-rules"):
        _assert_uncited_path_rules_case(_make_world(tmp_path / "path-rules"))
    with subtests.test(case="file-count"):
        _assert_uncited_file_count_above_old_limit(_make_world(tmp_path / "file-count"))


def _assert_source_note(
    result: dict[str, Any], relative: str, reason_words: tuple[str, ...]
) -> None:
    notes = result["checks"]["knowledge.converted"]["referenceSourceNotes"]
    if not reason_words:
        assert notes == [], notes
        return
    assert len(notes) == 1, notes
    note = notes[0]
    assert note["path"] == "onboarding/baseline.py.json", note
    assert note["field"] == "references.1.targets.0", note
    assert note["target"] == relative, note
    assert all(word in note["reason"].lower() for word in reason_words), note


def _assert_readable_citation(
    world: _World,
    relative: str,
    data: bytes,
    reason_words: tuple[str, ...],
    result: dict[str, Any],
) -> None:
    assert result["ok"] is True, result
    assert result["status"] == "passed", result
    assert result["checks"]["knowledge.converted"]["referenceStates"] == {
        "current": 1,
        "stale": 0,
    }
    _assert_source_note(result, relative, reason_words)
    _write(world.code, relative, data + b"# changed\n")
    changed = world.check()
    assert changed["ok"] is False, changed
    assert changed["checks"]["knowledge.converted"]["referenceStates"] == {
        "current": 0,
        "stale": 1,
    }
    drift = changed["checks"]["knowledge.converted"]["referenceDriftRows"]
    assert len(drift) == 1, drift
    assert drift[0]["target"] == relative, drift
    assert drift[0]["detail"] == "stale:content", drift
    _assert_source_note(changed, relative, reason_words)


def _assert_cited_source_policy(
    world: _World, monkeypatch: pytest.MonkeyPatch, case: str, reason_words: tuple[str, ...]
) -> None:
    """R1/R2: source-policy reasons preserve readable anchors and contain link failures."""
    relative, data = _case(world, case)
    if case == "ignored":
        data = _TEXT
        _write(world.code, relative, data)
    world.cite(relative, data)
    reads: list[str] = []
    opened: list[str] = []
    original = Path.read_bytes
    original_path_open = Path.open
    original_os_open = os.open
    target = world.code / relative
    no_inspection = case in {"symlink", "directory_symlink", "gitlink"}
    protected = {target, target.resolve()}

    def read_bytes(path: Path) -> bytes:
        if path == target:
            reads.append(relative)
        return original(path)

    def path_open(path: Path, *args: Any, **kwargs: Any) -> Any:
        if no_inspection and path in protected:
            opened.append(str(path))
        return original_path_open(path, *args, **kwargs)

    def os_open(path: Any, *args: Any, **kwargs: Any) -> int:
        if (
            no_inspection
            and isinstance(path, str | bytes | os.PathLike)
            and Path(os.fsdecode(path)) in protected
        ):
            opened.append(os.fsdecode(path))
        return original_os_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(Path, "open", path_open)
    monkeypatch.setattr(os, "open", os_open)
    result = world.check()
    if case in {"ignored", "excluded_build", "excluded_derived", "binary"}:
        _assert_readable_citation(world, relative, data, reason_words, result)
        return
    assert result["ok"] is False, result
    assert result["status"] == "failed", result
    rows = result["checks"]["knowledge.converted"]["referenceDriftRows"]
    row = next((item for item in rows if item["target"] == relative), None)
    assert row is not None, result
    reason = json.dumps(row).lower()
    assert all(word in reason for word in reason_words), row
    assert "path-absent" not in reason and "missing" not in reason, row
    assert reads == [], reads
    assert opened == [], opened


def test_cited_source_policy(
    tmp_path: Path, subtests: pytest.Subtests, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Readable citations verify; oversized, linked and Git-unclassifiable paths refuse."""
    for case, words in (
        ("ignored", ("ignored", "git")),
        ("excluded_build", ()),
        ("excluded_derived", ()),
        ("binary", ("binary",)),
        ("oversized", ("4 mib",)),
        ("symlink", ("symbolic link",)),
        ("directory_symlink", ("symbolic link",)),
        ("gitlink", ("git cannot classify",)),
    ):
        with subtests.test(case=case), monkeypatch.context() as context:
            _assert_cited_source_policy(_make_world(tmp_path / case), context, case, words)


def test_small_untracked_source_remains_citable(world: _World) -> None:
    """R212.2: Git's ordinary untracked files remain in the source view."""
    relative = "untracked.py"
    _write(world.code, relative, _TEXT)
    world.cite(relative, _TEXT)
    result = world.check()
    assert result["ok"] is True, result
    assert result["checks"]["knowledge.converted"]["referenceStates"] == {
        "current": 1,
        "stale": 0,
    }


def test_absent_source_keeps_the_missing_reason(world: _World) -> None:
    """R212.4: an actually missing target still differs from policy exclusions."""
    world.cite("absent.py", _TEXT)
    result = world.check()
    rows = result["checks"]["knowledge.converted"]["referenceDriftRows"]
    assert len(rows) == 1, result
    assert rows[0]["target"] == "absent.py", rows
    assert rows[0]["detail"] == "stale:path-absent", rows
