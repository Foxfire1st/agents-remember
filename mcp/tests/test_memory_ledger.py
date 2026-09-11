"""Focused contracts for newest-first external-memory ledger history.

The projection half of these contracts lives here too: ``memory.md`` is derived state, so the
shape of the table closeout computes from a source plus a branch's own mappings is a property
of the ledger module's world, not of any one caller.
"""

from pathlib import Path

from agents_remember.kernel.memory_ledger import (
    LEDGER_SCHEMA,
    LedgerRow,
    MemoryLedger,
    contains_mapping,
    create_initial_ledger,
    find_mapping,
    ledger_to_text,
    parse_ledger_text,
    prepend_mapping,
    write_ledger,
)
from agents_remember.worktrees.ledger_projection import (
    LedgerProjectionRefusal,
    LedgerSource,
    LedgerWorld,
    project_ledger,
    read_ledger_source,
)
from agents_remember.worktrees.modules.git import head_commit, require_git


def test_roundtrip_preserves_newest_same_code_history() -> None:
    ledger = create_initial_ledger("repo-a", "c1", "m1")
    text = ledger_to_text(ledger)

    assert "# Memory Ledger" in text
    assert "trackedCodeBranch" not in text
    assert "memoryBranch" not in text
    parsed = parse_ledger_text(text)
    assert parsed.last_verified_code_commit == "c1"

    updated = prepend_mapping(parsed, "c1", "m2")
    reparsed = parse_ledger_text(ledger_to_text(updated))
    assert reparsed.rows[0] == find_mapping(reparsed, "c1")
    assert reparsed.rows[0].memory_commit == "m2"
    assert contains_mapping(reparsed, "c1", "m1")
    assert reparsed.rows[1].memory_commit == "m1"


# --------------------------------------------------------------------------------------
# Ledger projection fixtures: one real repository, because truth is asked of Git.
# --------------------------------------------------------------------------------------


def _init_repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir(parents=True)
    require_git(repo, ["init", "-q", "-b", "main"])
    require_git(repo, ["config", "user.email", "ledger@example.invalid"])
    require_git(repo, ["config", "user.name", "Ledger Projection"])
    return repo


def _commit(repo: Path, message: str) -> str:
    require_git(repo, ["add", "-A"])
    require_git(repo, ["commit", "-q", "-m", message])
    return head_commit(repo)


class _World:
    """One memory/code repository whose commits stand in for a leaf's real history."""

    def __init__(self, root: Path) -> None:
        self.repo = _init_repo(root)
        (self.repo / "code.txt").write_text("one\n", encoding="utf-8")
        self.code_one = _commit(self.repo, "code one")
        (self.repo / "content.txt").write_text("first content\n", encoding="utf-8")
        self.memory_one = _commit(self.repo, "memory content one")
        write_ledger(
            self.repo / "memory.md",
            _ledger([LedgerRow(self.code_one, self.memory_one)]),
        )
        self.source_commit = _commit(self.repo, "source ledger")
        (self.repo / "code.txt").write_text("two\n", encoding="utf-8")
        self.code_two = _commit(self.repo, "code two")
        (self.repo / "content.txt").write_text("second content\n", encoding="utf-8")
        self.memory_two = _commit(self.repo, "memory content two")
        # Memory content that is real but never landed on this branch: the shape a superseded
        # row takes after a hand resolution keeps a side branch's row.
        require_git(self.repo, ["switch", "-q", "-c", "side", self.source_commit])
        (self.repo / "side.txt").write_text("side\n", encoding="utf-8")
        self.unreachable_memory = _commit(self.repo, "side content")
        require_git(self.repo, ["switch", "-q", "main"])

    @property
    def source_rows(self) -> list[LedgerRow]:
        return [LedgerRow(self.code_one, self.memory_one)]

    def observe(self, rows: list[LedgerRow], *, header: tuple[str, str] | None = None):
        """A projection of ``rows`` against this world, with the text the caller sees."""

        consistent = _ledger(rows)
        ledger = _ledger(rows, header=header)
        text = (
            ledger_to_text(consistent) if header is None else _text_with_header(consistent, header)
        )
        return project_ledger(
            source=LedgerSource(
                self.source_commit,
                read_ledger_source(self.repo, self.source_commit).ledger,
            ),
            observed=ledger,
            observed_text=text,
            world=LedgerWorld(
                memory_repository=self.repo,
                memory_reachable_from=head_commit(self.repo),
                code_repository=self.repo,
            ),
        )


def _ledger(rows: list[LedgerRow], *, header: tuple[str, str] | None = None) -> MemoryLedger:
    """A ledger over ``rows`` whose metadata follows row 1 unless a header is forced."""

    verified, content = (
        header if header is not None else (rows[0].code_commit, rows[0].memory_commit)
    )
    return MemoryLedger(
        schema=LEDGER_SCHEMA,
        repo_name="repo",
        base_code_commit="base-code",
        base_memory_commit="base-memory",
        last_verified_code_commit=verified,
        last_memory_content_commit=content,
        sort_order="newest-first",
        rows=list(rows),
    )


def _text_with_header(ledger: MemoryLedger, header: tuple[str, str]) -> str:
    """A canonical rendering whose metadata deliberately disagrees with its first row.

    The metadata block precedes the table, so replacing the first occurrence of row 1's two
    values rewrites the header and leaves every table cell alone; that is exactly the third
    real error -- a header that disagreed with its own first row.
    """

    text = ledger_to_text(ledger)
    return text.replace(f'"{ledger.rows[0].code_commit}"', f'"{header[0]}"', 1).replace(
        f'"{ledger.rows[0].memory_commit}"', f'"{header[1]}"', 1
    )


def _rows(projection) -> list[tuple[str, str]]:
    return [(row.code_commit, row.memory_commit) for row in projection.projected_rows]


def test_projection_drops_a_row_whose_memory_content_never_landed(tmp_path: Path) -> None:
    """A superseded row kept ahead of the source tail is removed and reported."""

    world = _World(tmp_path)
    projection = world.observe(
        [
            LedgerRow(world.code_two, world.memory_two),
            LedgerRow(world.code_one, world.unreachable_memory),
            *world.source_rows,
        ]
    )

    assert _rows(projection) == [
        (world.code_two, world.memory_two),
        *[(row.code_commit, row.memory_commit) for row in world.source_rows],
    ]
    assert projection.removed_rows == (LedgerRow(world.code_one, world.unreachable_memory),)
    assert [removal.reason for removal in projection.removals] == ["memory-commit-unreachable"]
    payload = projection.operator_payload()
    assert payload["state"] == "repaired"
    assert payload["rowsRemoved"] == [f"{world.code_one} -> {world.unreachable_memory}"]
    assert payload["removedReasons"] == [
        {
            "row": f"{world.code_one} -> {world.unreachable_memory}",
            "reason": "memory-commit-unreachable",
        }
    ]


def test_projection_moves_the_source_ledger_back_to_the_tail(tmp_path: Path) -> None:
    """A table whose source row sits ahead of the branch's own mapping is reordered."""

    world = _World(tmp_path)
    source_row = world.source_rows[0]
    projection = world.observe([source_row, LedgerRow(world.code_two, world.memory_two)])

    assert _rows(projection) == [
        (world.code_two, world.memory_two),
        (source_row.code_commit, source_row.memory_commit),
    ]
    assert set(projection.reordered_rows) == {
        source_row,
        LedgerRow(world.code_two, world.memory_two),
    }
    assert projection.header_after == (world.code_two, world.memory_two)
    payload = projection.operator_payload()
    assert payload["state"] == "repaired"
    assert payload["rowsReordered"]
    assert payload["headerChanged"] is True
    assert payload["headerAfter"] == {
        "lastVerifiedCodeCommit": world.code_two,
        "lastMemoryContentCommit": world.memory_two,
    }


def test_projection_recomputes_a_header_that_disagrees_with_its_first_row(tmp_path: Path) -> None:
    """A correct table under a wrong header is repaired without moving a single row."""

    world = _World(tmp_path)
    source_row = world.source_rows[0]
    rows = [LedgerRow(world.code_two, world.memory_two), source_row]
    projection = world.observe(rows, header=(source_row.code_commit, source_row.memory_commit))

    assert _rows(projection) == [
        (world.code_two, world.memory_two),
        (source_row.code_commit, source_row.memory_commit),
    ]
    assert projection.added_rows == ()
    assert projection.removed_rows == ()
    assert projection.reordered_rows == ()
    assert projection.header_changed is True
    payload = projection.operator_payload()
    assert payload["state"] == "repaired"
    assert str(payload["summary"]).endswith("header updated")
    assert payload["headerBefore"] == {
        "lastVerifiedCodeCommit": source_row.code_commit,
        "lastMemoryContentCommit": source_row.memory_commit,
    }


def test_projection_leaves_an_already_correct_ledger_byte_identical(tmp_path: Path) -> None:
    """The projection of a correct ledger is the ledger, and the payload says exactly that."""

    world = _World(tmp_path)
    source_row = world.source_rows[0]
    projection = world.observe([LedgerRow(world.code_two, world.memory_two), source_row])

    assert projection.needs_write is False
    assert projection.is_fixed_point is True
    assert projection.intended_text == projection.observed_text
    payload = projection.operator_payload()
    assert payload["state"] == "already-correct"
    assert payload["summary"] == (
        f"the memory ledger already equals the projection for source {world.source_commit}: "
        "added 0 row(s), removed 0 row(s), reordered 0 row(s), header unchanged"
    )


def test_projection_keeps_the_metadata_it_did_not_compute(tmp_path: Path) -> None:
    """Only the mapping table and its header are recomputed; the rest of the ledger is carried."""

    world = _World(tmp_path)
    observed = _ledger([LedgerRow(world.code_two, world.memory_two), *world.source_rows])
    projection = world.observe(observed.rows)

    assert projection.projected.repo_name == observed.repo_name
    assert projection.projected.base_code_commit == observed.base_code_commit
    assert projection.projected.base_memory_commit == observed.base_memory_commit
    assert projection.projected.sort_order == observed.sort_order
    assert projection.projected.schema == observed.schema


def test_projection_refuses_an_unreadable_source_ledger_with_a_remedy(tmp_path: Path) -> None:
    """An unparseable source, and a source that does not resolve, both refuse with the remedy."""

    world = _World(tmp_path)
    unparseable = _World(tmp_path / "second")
    (unparseable.repo / "memory.md").write_text("no ledger here\n", encoding="utf-8")
    broken = _commit(unparseable.repo, "break the source ledger")

    for label, commit in (("unparseable", broken), ("unresolvable", "0" * 40)):
        try:
            read_ledger_source(world.repo, commit)
        except LedgerProjectionRefusal as refusal:
            assert "Remedy:" in str(refusal), label
            assert "worktree_closeout_apply" in str(refusal), label
        else:
            raise AssertionError(f"a {label} memory source ledger was accepted")
