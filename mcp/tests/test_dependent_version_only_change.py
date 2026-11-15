"""A file link survives only substitution of the product's own old version with its new one.

Real code trees carry the product's metadata and installed-metadata resolver shape. The dependent
reader and landing report both derive their answers from converted-memory records at those trees.
Text cannot distinguish a dependency whose old and new spellings exactly equal the product's.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import pytest
from agents_remember.application.knowledge_currentness.observe import CodeTree
from agents_remember.application.knowledge_gate.landing import document_staleness
from agents_remember.application.knowledge_reader.marks import dependent_state
from agents_remember.application.knowledge_reader.selection import ReaderSelection
from agents_remember.kernel.git_command import run_git
from agents_remember.memory.knowledge_index import KnowledgeIndexCache
from agents_remember.models.knowledge_files.documents import (
    LAYOUT_MARKER_PATH,
    dependent_link_index,
    record_path,
)
from agents_remember.models.knowledge_files.records import DependentRecord
from agents_remember.worktrees.services import LandingGateRequest

VERSION_FILE = "mcp/src/agents_remember/kernel/primitives/version.py"
PYPROJECT = "mcp/pyproject.toml"
LOCK = "mcp/uv.lock"
DOC = "docs/release-checklist.md"
Consumer = Literal["reader", "landing"]


def _git(repo: Path, *args: str) -> str:
    result = run_git(repo, list(args))
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _write(repo: Path, path: str, text: str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _init(repo: Path) -> Path:
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "dependent@example.invalid")
    _git(repo, "config", "user.name", "Dependent Tests")
    return repo


def _commit(repo: Path) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "fixture state")
    return _git(repo, "rev-parse", "HEAD")


def _version_sources(repo: Path, version: str) -> None:
    _write(repo, PYPROJECT, f'[project]\nname = "agents-remember-mcp"\nversion = "{version}"\n')
    _write(
        repo,
        VERSION_FILE,
        "from importlib.metadata import PackageNotFoundError, version\n\n"
        'SERVER_NAME = "agents-remember"\n\n'
        "def _resolve_server_version() -> str:\n"
        "    try:\n"
        '        return version("agents-remember-mcp")\n'
        "    except PackageNotFoundError:\n"
        f'        return "{version}"\n\n'
        "SERVER_VERSION = _resolve_server_version()\n",
    )
    _write(
        repo,
        LOCK,
        f'[[package]]\nname = "agents-remember-mcp"\nversion = "{version}"\n'
        '[[package]]\nname = "dependency"\nversion = "4.1.0"\n',
    )


def _repo(tmp_path: Path) -> Path:
    repo = _init(tmp_path / "code")
    _version_sources(repo, "3.0.1")
    _write(repo, DOC, "Install agents-remember-mcp 3.0.1 and check the index.\n")
    _commit(repo)
    return repo


def _record(repo: Path, path: str) -> DependentRecord:
    return DependentRecord.model_validate(
        {
            "schema": "ar-dependent/v1",
            "id": "DPD-V3R510",
            "revision": 1,
            "document": DOC,
            "stands_on": [
                {"kind": "file", "path": path, "blob": _git(repo, "rev-parse", f"HEAD:{path}")}
            ],
            "confirmed": {
                "task": "261112-BUG-L3",
                "role": "curator",
                "statement": "The document still describes the code it stands on.",
                "commit": _git(repo, "rev-parse", "HEAD"),
                "codeTree": _git(repo, "rev-parse", "HEAD^{tree}"),
            },
            "origin": {"task": "261112-BUG", "leaf": "261112-BUG-L3"},
        }
    )


def _memory(repo: Path, record: DependentRecord) -> tuple[Path, str]:
    memory = _init(repo.parent / "memory")
    path = record_path("dependent", record.id, "release-checklist")
    _write(memory, path, json.dumps(record.to_document()))
    _write(
        memory,
        LAYOUT_MARKER_PATH,
        json.dumps(
            {
                "schema": "ar-memory-layout/v2",
                "conversion": "fixture",
                "dependent_links": dependent_link_index([(path, record)]),
            }
        ),
    )
    return memory, _commit(memory)


def _state(repo: Path, record: DependentRecord, consumer: Consumer) -> str:
    memory, revision = _memory(repo, record)
    selected = _git(repo, "rev-parse", "HEAD")
    if consumer == "landing":
        result = document_staleness(
            LandingGateRequest(
                memory_repository=memory,
                memory_commit=revision,
                memory_bases=(revision,),
                code_repository=repo,
                code_commit=selected,
                code_base=record.confirmed.commit,
            )
        )
        assert result.uncomputable is None, result
        assert not result.unverifiable, result
        assert not result.already_stale, result
        return "stale" if record.document in result.documents else "current"
    index = KnowledgeIndexCache(repo.parent / "index-cache").for_git_tree(memory, revision)
    with ReaderSelection(
        repository_id=index.repository_id,
        commit=revision,
        kind="commit",
        memory_repository=memory,
        memory_root=None,
        revision=revision,
        index=index,
        code_repository=repo,
        code_tree=CodeTree(repo, selected),
        code_note="fixture selected code tree",
    ) as selection:
        assert selection.index.state.complete, selection.index.state
        state, _moved = dependent_state(selection, record)
        return state


@pytest.mark.parametrize("consumer", ["reader", "landing"])
@pytest.mark.parametrize("path", [PYPROJECT, VERSION_FILE, LOCK, DOC])
def test_a_bump_of_the_products_own_version_does_not_move_a_file_link(
    tmp_path: Path, path: str, consumer: Consumer
) -> None:
    repo = _repo(tmp_path)
    record = _record(repo, path)
    _version_sources(repo, "3.0.2")
    _write(repo, DOC, "Install agents-remember-mcp 3.0.2 and check the index.\n")
    _commit(repo)

    assert _state(repo, record, consumer) == "current"


@pytest.mark.parametrize("consumer", ["reader", "landing"])
def test_an_unchanged_dependency_version_does_not_hide_a_product_only_change(
    tmp_path: Path, consumer: Consumer
) -> None:
    repo = _repo(tmp_path)
    _write(repo, DOC, "Product 3.0.1; dependency 4.1.0.\n")
    _commit(repo)
    record = _record(repo, DOC)
    _version_sources(repo, "3.0.2")
    _write(repo, DOC, "Product 3.0.2; dependency 4.1.0.\n")
    _commit(repo)

    assert _state(repo, record, consumer) == "current"


@pytest.mark.parametrize("consumer", ["reader", "landing"])
def test_an_old_version_mention_can_stay_beside_a_replaced_one(
    tmp_path: Path, consumer: Consumer
) -> None:
    repo = _repo(tmp_path)
    _write(repo, DOC, "Product 3.0.1; first introduced in 3.0.1.\n")
    _commit(repo)
    record = _record(repo, DOC)
    _version_sources(repo, "3.0.2")
    _write(repo, DOC, "Product 3.0.2; first introduced in 3.0.1.\n")
    _commit(repo)

    assert _state(repo, record, consumer) == "current"


@pytest.mark.parametrize("consumer", ["reader", "landing"])
def test_any_other_changed_byte_still_moves_the_link(tmp_path: Path, consumer: Consumer) -> None:
    repo = _repo(tmp_path)
    record = _record(repo, PYPROJECT)
    _version_sources(repo, "3.0.2")
    old = (repo / PYPROJECT).read_text(encoding="utf-8")
    _write(repo, PYPROJECT, old + 'license = "MIT"\n')
    _commit(repo)

    assert _state(repo, record, consumer) == "stale"


@pytest.mark.parametrize("consumer", ["reader", "landing"])
@pytest.mark.parametrize(
    ("old_dependency", "dependency"),
    [("4.1.0", "4.2.0"), ("4.1.0", "3.0.2"), ("3.0.10", "3.0.20")],
)
def test_a_changed_dependency_version_still_moves(
    tmp_path: Path, old_dependency: str, dependency: str, consumer: Consumer
) -> None:
    repo = _repo(tmp_path)
    _write(repo, DOC, f"Product 3.0.1; dependency {old_dependency}.\n")
    _commit(repo)
    record = _record(repo, DOC)
    _version_sources(repo, "3.0.2")
    _write(repo, DOC, f"Product 3.0.2; dependency {dependency}.\n")
    _commit(repo)

    assert _state(repo, record, consumer) == "stale"


@pytest.mark.parametrize("consumer", ["reader", "landing"])
@pytest.mark.parametrize(
    ("old_dependency", "dependency"),
    [
        pytest.param("1!3.0.1", "1!3.0.2", id="epoch"),
        pytest.param("v1!3.0.1", "v1!3.0.2", id="tagged-epoch"),
        pytest.param("3.0.1.1", "3.0.2.1", id="further-release"),
        pytest.param("3.0.1rc1", "3.0.2rc1", id="pre"),
        pytest.param("3.0.1.post1", "3.0.2.post1", id="post"),
        pytest.param("3.0.1.dev1", "3.0.2.dev1", id="dev"),
        pytest.param("3.0.1+local.1", "3.0.2+local.1", id="local"),
        pytest.param("3.0.1_post1", "3.0.2_post1", id="underscore-post"),
        pytest.param("3.0.1_dev1", "3.0.2_dev1", id="underscore-dev"),
        pytest.param("2.0+3.0.1", "2.0+3.0.2", id="local-fragment"),
    ],
)
def test_a_changed_complete_dependency_version_still_moves(
    tmp_path: Path, old_dependency: str, dependency: str, consumer: Consumer
) -> None:
    repo = _repo(tmp_path)
    _write(repo, DOC, f"Product 3.0.1; dependency {old_dependency}.\n")
    _commit(repo)
    record = _record(repo, DOC)
    _version_sources(repo, "3.0.2")
    _write(repo, DOC, f"Product 3.0.2; dependency {dependency}.\n")
    _commit(repo)

    assert _state(repo, record, consumer) == "stale"


@pytest.mark.parametrize("consumer", ["reader", "landing"])
def test_a_leading_v_tag_keeps_the_products_version_identity(
    tmp_path: Path, consumer: Consumer
) -> None:
    repo = _repo(tmp_path)
    _write(repo, DOC, "Product tag v3.0.1.\n")
    _commit(repo)
    record = _record(repo, DOC)
    _version_sources(repo, "3.0.2")
    _write(repo, DOC, "Product tag v3.0.2.\n")
    _commit(repo)

    assert _state(repo, record, consumer) == "current"


@pytest.mark.parametrize("consumer", ["reader", "landing"])
def test_an_equal_complete_dependency_version_is_textually_indistinguishable(
    tmp_path: Path, consumer: Consumer
) -> None:
    repo = _repo(tmp_path)
    _write(repo, DOC, "Product 3.0.1; dependency 3.0.1.\n")
    _commit(repo)
    record = _record(repo, DOC)
    _version_sources(repo, "3.0.2")
    _write(repo, DOC, "Product 3.0.2; dependency 3.0.2.\n")
    _commit(repo)

    assert _state(repo, record, consumer) == "current"


@pytest.mark.parametrize("consumer", ["reader", "landing"])
def test_a_whitespace_only_change_still_moves(tmp_path: Path, consumer: Consumer) -> None:
    repo = _repo(tmp_path)
    record = _record(repo, DOC)
    _write(repo, DOC, "Install agents-remember-mcp 3.0.1  and check the index.\n")
    _commit(repo)

    assert _state(repo, record, consumer) == "stale"


@pytest.mark.parametrize("consumer", ["reader", "landing"])
def test_version_identity_comes_from_the_selected_tree_not_working_files(
    tmp_path: Path, consumer: Consumer
) -> None:
    repo = _repo(tmp_path)
    record = _record(repo, DOC)
    _version_sources(repo, "3.0.2")
    _write(repo, DOC, "Install agents-remember-mcp 3.0.2 and check the index.\n")
    _commit(repo)
    _version_sources(repo, "9.9.9")

    assert _state(repo, record, consumer) == "current"


@pytest.mark.parametrize("consumer", ["reader", "landing"])
@pytest.mark.parametrize("unreadable", ["absent-revision", "no-version", "no-exact-tree"])
def test_an_unreadable_old_product_version_does_not_excuse_a_changed_file(
    tmp_path: Path, unreadable: str, consumer: Consumer
) -> None:
    repo = _repo(tmp_path)
    record = _record(repo, DOC)
    if unreadable == "absent-revision":
        confirmation = record.confirmed.model_copy(update={"codeTree": "f" * 40})
    elif unreadable == "no-version":
        _write(repo, PYPROJECT, '[project]\nname = "agents-remember-mcp"\n')
        _commit(repo)
        confirmation = record.confirmed.model_copy(
            update={"codeTree": _git(repo, "rev-parse", "HEAD^{tree}"), "uncommitted": True}
        )
    else:
        confirmation = record.confirmed.model_copy(update={"codeTree": None, "uncommitted": True})
    record = record.model_copy(update={"confirmed": confirmation})
    _version_sources(repo, "3.0.2")
    _write(repo, DOC, "Install agents-remember-mcp 3.0.2 and check the index.\n")
    _commit(repo)

    assert _state(repo, record, consumer) == "stale"
