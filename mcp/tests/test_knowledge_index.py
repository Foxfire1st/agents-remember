"""The derived knowledge index: sources, key, answers, partial state and cache (MIK-R23 rules 1-5).

Every case builds a real memory tree in a real Git repository and indexes it through the public
cache, so the key, the capture and the Git-object reads are the production ones.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from agents_remember.cli.__main__ import build_parser
from agents_remember.memory.knowledge_index import (
    INDEX_FORMAT,
    KnowledgeIndex,
    KnowledgeIndexCache,
    MemoryTreeError,
    directory_key,
    is_indexed_path,
)
from agents_remember.models.knowledge_files import canonical_text
from knowledge_index_test_support import (
    DECISION,
    FAMILY,
    INCIDENT,
    LEAF,
    OTHER_LEAF,
    OUTSIDER_INVARIANT,
    REVIEW_INVARIANT,
    REVIEW_PATH,
    SIBLING_INVARIANT,
    SIBLING_PATHS,
    TEST_PATH,
    commit_all,
    git,
    init_repository,
    write_review_tree,
)


@pytest.fixture
def memory(tmp_path: Path) -> Path:
    root = tmp_path / "memory"
    init_repository(root)
    (root / ".gitignore").write_text("onboarding/**/overview.index.json\n", encoding="utf-8")
    write_review_tree(root)
    commit_all(root)
    return root


@pytest.fixture
def cache(tmp_path: Path) -> KnowledgeIndexCache:
    return KnowledgeIndexCache(tmp_path / "coordination" / "runtime" / "knowledge-index")


def _statement_file(memory: Path) -> Path:
    return next((memory / "knowledge" / "invariants").glob(f"{REVIEW_INVARIANT}-*.json"))


# --- rule 1: both sources ---------------------------------------------------------------------


def test_a_working_tree_and_its_git_tree_index_to_the_same_key_and_answers(
    memory: Path, cache: KnowledgeIndexCache
) -> None:
    with cache.for_directory(memory) as from_directory:
        directory_answer = from_directory.invariant(REVIEW_INVARIANT).value
        directory_key_value = from_directory.state.key
    with cache.for_git_tree(memory, "HEAD") as from_git:
        git_answer = from_git.invariant(REVIEW_INVARIANT).value
        assert from_git.state.key == git(memory, "rev-parse", "HEAD^{tree}")
    assert directory_key_value == git(memory, "rev-parse", "HEAD^{tree}")
    assert git_answer == directory_answer
    assert {entry.id for entry in git_answer.realizations} == {"RLZ-RVW001"}


def test_a_historical_git_tree_is_read_through_objects_without_a_checkout(
    memory: Path, cache: KnowledgeIndexCache
) -> None:
    first = git(memory, "rev-parse", "HEAD")
    _statement_file(memory).unlink()
    commit_all(memory, "retire the review invariant file")
    head_before = git(memory, "rev-parse", "HEAD")
    status_before = git(memory, "status", "--porcelain")
    with cache.for_git_tree(memory, first) as historical:
        assert historical.record(REVIEW_INVARIANT).value is not None
    with cache.for_git_tree(memory, "HEAD") as current:
        assert current.record(REVIEW_INVARIANT).value is None
    assert git(memory, "rev-parse", "HEAD") == head_before
    assert git(memory, "status", "--porcelain") == status_before


# --- rule 2: the key --------------------------------------------------------------------------


def test_the_key_is_the_tree_id_of_the_captured_state_and_depends_on_content_only(
    memory: Path, tmp_path: Path
) -> None:
    head_tree = git(memory, "rev-parse", "HEAD^{tree}")
    assert directory_key(memory) == head_tree

    target = _statement_file(memory)
    original = target.read_bytes()
    target.write_bytes(original.replace(b"beside changed ones", b"beside edited ones"))
    edited = directory_key(memory)
    assert edited != head_tree
    # The key of an uncommitted state is exactly what `git write-tree` over that state records.
    expected = subprocess.run(
        ["git", "-C", str(memory), "stash", "create"], check=True, capture_output=True, text=True
    ).stdout.strip()
    assert edited == git(memory, "rev-parse", f"{expected}^{{tree}}")

    target.write_bytes(original)
    assert directory_key(memory) == head_tree

    # Identical content elsewhere is the identical key; an ignored cache is not content.
    copy = tmp_path / "copy"
    shutil.copytree(memory, copy)
    (copy / "onboarding" / "dashboard" / "src" / "overview.index.json").write_text("{}", "utf-8")
    assert directory_key(copy) == head_tree


def test_capturing_a_key_writes_nothing_into_the_repository(memory: Path) -> None:
    _statement_file(memory).write_text(_statement_file(memory).read_text("utf-8") + " ", "utf-8")
    (memory / "untracked.md").write_text("new\n", encoding="utf-8")
    git_dir = memory / ".git"
    index_before = (git_dir / "index").read_bytes()
    objects_before = sorted(
        path.name for path in (git_dir / "objects").rglob("*") if path.is_file()
    )
    status_before = git(memory, "status", "--porcelain")

    directory_key(memory)

    assert (git_dir / "index").read_bytes() == index_before
    assert sorted(path.name for path in (git_dir / "objects").rglob("*") if path.is_file()) == (
        objects_before
    )
    assert git(memory, "status", "--porcelain") == status_before


def test_a_directory_outside_git_has_no_key(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(MemoryTreeError, match="not inside a Git working tree"):
        directory_key(plain)


# --- rule 3: the answers ----------------------------------------------------------------------


def test_path_lookups_return_realizations_and_proofs(
    memory: Path, cache: KnowledgeIndexCache
) -> None:
    with cache.for_directory(memory) as index:
        at_review = index.entries_at_path(REVIEW_PATH)
        at_test = index.entries_at_path(TEST_PATH)
        nowhere = index.entries_at_path("dashboard/src/unknown.ts")
    assert [entry.id for entry in at_review.value.realizations] == ["RLZ-RVW001", "RLZ-RVW002"]
    assert at_review.value.realizations[0].document["anchor"]["path"] == REVIEW_PATH
    assert [entry.id for entry in at_test.value.proofs] == ["PRF-T3ST0K"]
    assert nowhere.value.realizations == () and nowhere.value.proofs == ()
    assert at_review.index.complete


def test_an_invariant_answers_its_code_tests_families_links_and_history(
    memory: Path, cache: KnowledgeIndexCache
) -> None:
    with cache.for_directory(memory) as index:
        answer = index.invariant(REVIEW_INVARIANT).value
        sibling = index.invariant(SIBLING_INVARIANT).value
        outsider = index.invariant(OUTSIDER_INVARIANT).value
    assert answer.record is not None and answer.record.revision == 1
    assert [(entry.id, entry.path) for entry in answer.realizations] == [
        ("RLZ-RVW001", REVIEW_PATH)
    ]
    assert [entry.id for entry in answer.proofs] == ["PRF-T3ST0K"]
    assert answer.families == (FAMILY,)
    assert {(link.source, link.source_kind, link.relation) for link in answer.linked_from} == {
        (DECISION, "decision", "constrains"),
        (DECISION, "decision", "reconsider_on"),
        (INCIDENT, "incident", "violated"),
        ("dashboard/src", "route", "cites"),
    }
    reconsider = next(link for link in answer.linked_from if link.relation == "reconsider_on")
    assert reconsider.detail["alternative"] == 1
    assert {row.id for row in answer.history} == {"ROW-AAAAA1", "ROW-BBBBB1"}
    # The sibling realizations across four files, in the reverse direction no file records.
    assert {entry.path for entry in sibling.realizations} == {REVIEW_PATH, *SIBLING_PATHS}
    assert outsider.families == () and outsider.realizations == ()


def test_a_family_answers_members_and_routes_and_routes_answer_their_families(
    memory: Path, cache: KnowledgeIndexCache
) -> None:
    with cache.for_directory(memory) as index:
        family = index.family(FAMILY).value
        under_route = index.families_governing(REVIEW_PATH).value
        the_route = index.families_governing("mcp/src/agents_remember/application").value
        deeper = index.families_governing(
            "mcp/src/agents_remember/application/review_family_context.py"
        ).value
        outside = index.families_governing("mcp/src/agents_remember/models/x.py").value
        prefix_only = index.families_governing("dashboard/srcx/file.ts").value
    assert family.members == (REVIEW_INVARIANT, SIBLING_INVARIANT)
    assert family.routes == ("dashboard/src", "mcp/src/agents_remember/application")
    assert under_route == ((FAMILY, "dashboard/src"),)
    assert the_route == deeper == ((FAMILY, "mcp/src/agents_remember/application"),)
    assert outside == () and prefix_only == ()


def test_a_family_routed_at_the_root_governs_every_path(
    memory: Path, cache: KnowledgeIndexCache
) -> None:
    """MIK-R04: the root route ``.`` covers every path, as the validator's ``route_covers`` does."""

    family_file = next((memory / "knowledge" / "families").glob(f"{FAMILY}-*.json"))
    document = json.loads(family_file.read_text(encoding="utf-8"))
    document["routes"] = [".", *document["routes"]]
    family_file.write_text(canonical_text(document), encoding="utf-8")
    with cache.for_directory(memory) as index:
        root_file = index.families_governing("README.md").value
        deep_file = index.families_governing(
            "mcp/src/agents_remember/application/review_family_context.py"
        ).value
    assert root_file == ((FAMILY, "."),)
    assert deep_file == ((FAMILY, "."), (FAMILY, "mcp/src/agents_remember/application"))


def test_incoming_links_reach_any_record(memory: Path, cache: KnowledgeIndexCache) -> None:
    with cache.for_directory(memory) as index:
        to_decision = index.incoming_links(DECISION).value
        to_sibling = index.incoming_links(SIBLING_INVARIANT).value
    assert [(link.source, link.relation) for link in to_decision] == [("ROW-BBBBB1", "because")]
    assert [(link.source, link.relation) for link in to_sibling] == [(FAMILY, "member")]


def test_history_rows_are_found_by_subject_and_by_leaf(
    memory: Path, cache: KnowledgeIndexCache
) -> None:
    """MIK-R07's "rows about a subject" and "a leaf's rows", answered by the index."""

    with cache.for_directory(memory) as index:
        about_invariant = index.history_rows_about(REVIEW_INVARIANT).value
        about_family = index.history_rows_about(FAMILY).value
        of_leaf = index.history_rows_of(LEAF).value
        of_other = index.history_rows_of(OTHER_LEAF).value
    assert [(row.owner, row.id, row.closed) for row in about_invariant] == [
        (OTHER_LEAF, "ROW-AAAAA1", True),
        (LEAF, "ROW-BBBBB1", False),
    ]
    assert [row.document["examined"][0]["id"] for row in about_family] == [REVIEW_INVARIANT]
    assert [row.subject for row in of_leaf] == [REVIEW_INVARIANT]
    assert {row.subject for row in of_other} == {REVIEW_INVARIANT, FAMILY}


# --- rule 5: freshness ------------------------------------------------------------------------


def test_an_edited_working_tree_is_never_answered_from_the_previous_content(
    memory: Path, cache: KnowledgeIndexCache
) -> None:
    with cache.for_directory(memory) as before:
        old_key = before.state.key
        assert "beside changed ones" in before.record(REVIEW_INVARIANT).value.document["statement"]  # type: ignore[union-attr]
    target = _statement_file(memory)
    target.write_text(target.read_text("utf-8").replace("changed ones", "edited ones"), "utf-8")
    with cache.for_directory(memory) as after:
        assert after.state.key != old_key
        document = after.record(REVIEW_INVARIANT).value.document  # type: ignore[union-attr]
        assert "beside edited ones" in document["statement"]
    # The old file is still cached, but it is bound to its own key and refuses another.
    with pytest.raises(ValueError, match="was built for tree"):
        KnowledgeIndex(cache.path_for(old_key), expected_key=after.state.key)


# --- failure: partial index -------------------------------------------------------------------


def test_a_file_failing_its_schema_marks_the_index_partial_and_is_named(
    memory: Path, cache: KnowledgeIndexCache
) -> None:
    broken = memory / "knowledge" / "invariants" / "INV-BRKN01-broken.json"
    broken.write_text(json.dumps({"schema": "ar-invariant/v1", "id": "INV-BRKN01"}), "utf-8")
    misplaced = memory / "onboarding" / "elsewhere.py.json"
    misplaced.write_text(
        (memory / "onboarding" / f"{REVIEW_PATH}.json").read_text("utf-8"), encoding="utf-8"
    )
    duplicate = memory / "knowledge" / "invariants" / f"{OUTSIDER_INVARIANT}-again.json"
    shutil.copyfile(
        next((memory / "knowledge" / "invariants").glob(f"{OUTSIDER_INVARIANT}-outsider.json")),
        duplicate,
    )
    with cache.for_directory(memory) as index:
        state = index.state
        answer = index.invariant(REVIEW_INVARIANT)
    assert state.state == "partial" and not state.complete
    named = {path for path, _detail in state.problems}
    assert named == {
        "knowledge/invariants/INV-BRKN01-broken.json",
        "onboarding/elsewhere.py.json",
        f"knowledge/invariants/{OUTSIDER_INVARIANT}-outsider.json",
    }
    # The rest of the tree is still answered, and every answer says it is partial.
    assert answer.index.state == "partial"
    assert [entry.id for entry in answer.value.realizations] == ["RLZ-RVW001"]
    assert cache.last_outcome is not None and cache.last_outcome.report is not None
    assert cache.last_outcome.report.state == "partial"


def test_an_unconverted_tree_is_indexed_empty_and_says_so(
    tmp_path: Path, cache: KnowledgeIndexCache
) -> None:
    legacy = tmp_path / "legacy"
    init_repository(legacy)
    (legacy / "onboarding").mkdir()
    (legacy / "onboarding" / "overview.md").write_text("# legacy\n", encoding="utf-8")
    commit_all(legacy)
    with cache.for_directory(legacy) as index:
        assert index.state.converted is False
        assert index.state.complete
        assert index.entries_at_path("anything.py").value.realizations == ()


# --- rule 4: storage --------------------------------------------------------------------------


def test_the_cache_reuses_rebuilds_and_loses_nothing_when_deleted(
    memory: Path, cache: KnowledgeIndexCache
) -> None:
    with cache.for_directory(memory) as first:
        answer = first.invariant(REVIEW_INVARIANT).value
        path = first.database_path
    assert cache.last_outcome is not None and not cache.last_outcome.reused
    assert path == cache.path_for(git(memory, "rev-parse", "HEAD^{tree}"))
    with cache.for_directory(memory) as _second:
        pass
    assert cache.last_outcome.reused

    path.write_bytes(b"not an index")
    with cache.for_directory(memory) as rebuilt:
        assert rebuilt.invariant(REVIEW_INVARIANT).value == answer
    assert not cache.last_outcome.reused

    shutil.rmtree(cache.directory)
    cache.directory.mkdir(parents=True)
    with cache.for_directory(memory) as from_nothing:
        assert from_nothing.invariant(REVIEW_INVARIANT).value == answer
    assert sorted(p.name for p in cache.directory.iterdir()) == [path.name]


def test_old_and_excess_index_files_are_evicted(memory: Path, tmp_path: Path) -> None:
    cache = KnowledgeIndexCache(tmp_path / "evicting", max_age_seconds=3600, max_bytes=10**9)
    stale = cache.directory / f"{'a' * 40}.sqlite"
    stale.write_bytes(b"x" * 10)
    old = time.time() - 7200
    os.utime(stale, (old, old))
    with cache.for_directory(memory) as index:
        kept = index.database_path
    assert not stale.exists() and kept.exists()

    small = KnowledgeIndexCache(tmp_path / "small", max_bytes=1)
    extra = small.directory / f"{'b' * 40}.sqlite"
    extra.write_bytes(b"y" * 10)
    with small.for_directory(memory) as index:
        assert index.database_path.exists()
    assert not extra.exists()


def test_the_cache_is_never_placed_inside_a_git_working_tree(memory: Path) -> None:
    with pytest.raises(MemoryTreeError, match="inside a Git working tree"):
        KnowledgeIndexCache(memory / ".ar-index")
    assert "ar-index" not in git(memory, "status", "--porcelain", "--untracked-files=all")
    assert not (memory / ".ar-index").exists()
    with pytest.raises(MemoryTreeError, match="inside a Git working tree"):
        KnowledgeIndexCache(memory / "deep" / "er" / "cache")
    assert not (memory / "deep").exists()


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_an_index_flag_never_hides_an_edit_from_the_key(
    memory: Path, cache: KnowledgeIndexCache, flag: str
) -> None:
    target = _statement_file(memory)
    relative = target.relative_to(memory).as_posix()
    git(memory, "update-index", flag, relative)
    clean_key = directory_key(memory)
    target.write_text(target.read_text("utf-8").replace("changed ones", "edited ones"), "utf-8")
    assert directory_key(memory) != clean_key
    with cache.for_directory(memory) as index:
        document = index.record(REVIEW_INVARIANT).value.document  # type: ignore[union-attr]
    assert "beside edited ones" in document["statement"]
    # The repository's own index keeps its flag: only the scratch copy was cleared.
    tag = git(memory, "ls-files", "-v", relative)[0]
    assert tag.islower() or tag == "S"


def test_the_index_file_declares_its_format_and_key(
    memory: Path, cache: KnowledgeIndexCache
) -> None:
    import apsw  # noqa: PLC0415

    with cache.for_directory(memory) as index:
        connection = apsw.Connection(str(index.database_path), flags=apsw.SQLITE_OPEN_READONLY)
        meta = dict(connection.execute("SELECT name, value FROM ix_meta"))
        connection.close()
    assert meta["format"] == INDEX_FORMAT
    assert meta["key"] == git(memory, "rev-parse", "HEAD^{tree}")
    assert meta["source"] == "directory" and meta["state"] == "complete"


def test_the_command_reports_the_index_and_exits_by_state(
    memory: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    parser = build_parser()
    arguments = [
        "knowledge-index",
        "--memory-root",
        str(memory),
        "--cache-dir",
        str(tmp_path / "c"),
    ]
    args = parser.parse_args(arguments)
    assert args.func(args) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["state"] == "complete" and report["records"] == 6 and report["entries"] == 6

    (memory / "knowledge" / "families" / "FAM-BRKN01-x.json").write_text("{", encoding="utf-8")
    args = parser.parse_args(arguments)
    assert args.func(args) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["state"] == "partial"
    assert report["problems"][0]["path"] == "knowledge/families/FAM-BRKN01-x.json"

    git_args = parser.parse_args(
        [
            "knowledge-index",
            "--repository",
            str(memory),
            "--revision",
            "HEAD",
            "--cache-dir",
            str(tmp_path / "c"),
        ]
    )
    assert git_args.func(git_args) == 0
    assert json.loads(capsys.readouterr().out)["key"] == git(memory, "rev-parse", "HEAD^{tree}")


def test_the_index_reads_the_files_the_validator_reads() -> None:
    """The index shares MIK-R22's exclusion rule, less the census files MIK-R20 owns."""

    assert is_indexed_path("knowledge/invariants/INV-RVW001-x.json")
    assert is_indexed_path("onboarding/.github/workflows/ci.yml.json") is False
    assert is_indexed_path("onboarding/src/.hidden/a.py.json") is False
    assert is_indexed_path("onboarding/src/overview.index.json") is False
    assert is_indexed_path("knowledge/census/C1/inventory.json") is False
    assert is_indexed_path("onboarding/src/a.py.md") is False
    assert is_indexed_path("notes/x.json") is False
