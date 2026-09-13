"""Focused contracts for newest-first external-memory ledger history.

The projection half of these contracts lives here too: ``memory.md`` is derived state, so the
shape of the table closeout computes from a source plus a branch's own mappings is a property
of the ledger module's world, not of any one caller.
"""

from pathlib import Path

from agents_remember.kernel.memory_attribution import (
    CODE_COMMIT_TRAILER_KEY,
    AttributedCommit,
    attributed_commits,
    ledger_rows_from_attribution,
    parse_code_commit_trailer,
)
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
from agents_remember.models.closeout.input import (
    EffectiveCloseoutInput,
    EnabledCloseoutLeg,
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
    require_git(repo, ["commit", "-q", "--allow-empty", "-m", message])
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

    first = (rows[0].code_commit, rows[0].memory_commit) if rows else ("", "")
    verified, content = header if header is not None else first
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


# --------------------------------------------------------------------------------------
# The attribution: the ledger is what the memory commits' own trailers say it is.
# --------------------------------------------------------------------------------------


def _attributed_commit(repo: Path, message: str, *, code_commit: str = "", path: str = "") -> str:
    """One commit, with ``Code-Commit:`` as a real trailing block when one is attributed."""

    if path:
        destination = repo / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(f"{message}\n", encoding="utf-8")
    require_git(repo, ["add", "-A"])
    body = f"{message}\n\nCode-Commit: {code_commit}" if code_commit else message
    require_git(repo, ["commit", "-q", "-m", body])
    return head_commit(repo)


# Object names, not labels: git only reads a trailing block as a trailer when its value looks
# like one, so a test that wants the real reader has to write what the real writer writes.
CODE_ONE = "1" * 40
CODE_TWO = "2" * 40
CODE_OWN = "a" * 40


class _AttributedWorld:
    """A memory line whose ledger is recorded the way closeout records one.

    Each mapping is two commits: the content commit, which carries the ``Code-Commit:`` trailer,
    and the ledger commit, which pins that mapping into ``memory.md``. The ledger commit carries
    none, because it names no code counterpart. One code commit is attributed twice, which is the
    superseding pair the ledger keeps both copies of, and one memory commit carries no trailer at
    all, which is the class the spec skips by rule.
    """

    def __init__(self, root: Path) -> None:
        self.repo = _init_repo(root)
        self.checkpoints: list[tuple[str, tuple[LedgerRow, ...], str]] = []
        self._rows: list[LedgerRow] = []
        self.source_commit = self._seal()
        self._memory("m-one", CODE_ONE, "onboarding/one.md")
        self._memory("m-two", CODE_TWO, "onboarding/two.md")
        self._memory("m-three", CODE_ONE, "onboarding/one.md")
        self._unattributed("m-skip", "notes/unrelated.md")

    def _seal(self, rows: tuple[LedgerRow, ...] | None = None) -> str:
        """Pin the rows this checkpoint carries, exactly as a ledger commit pins them.

        The first checkpoint carries no rows yet, and a memory line that has produced no mapping
        has no ledger to write -- that is the bootstrap state, not an empty table. It is therefore
        the very first commit of the line, and everything the line later attributes is what the
        projection reads. ``rows`` is the exact checkpoint snapshot and is never the live list,
        because a checkpoint that a later mapping could edit would not be a checkpoint.
        """

        snapshot = tuple(self._rows) if rows is None else rows
        if not snapshot:
            return _commit(self.repo, "memory line bootstrap")
        write_ledger(self.repo / "memory.md", _ledger(list(snapshot)))
        return _commit(self.repo, "ledger checkpoint")

    def _memory(self, label: str, code: str, path: str) -> str:
        self._rows = [LedgerRow(code, f"{label}-placeholder"), *self._rows]
        content = _attributed_commit(self.repo, f"memory {label}", code_commit=code, path=path)
        self._rows[0] = LedgerRow(code, content)
        rows = tuple(self._rows)
        self.checkpoints.append((self._seal(rows), rows, label))
        return content

    def _unattributed(self, label: str, path: str) -> str:
        content = _attributed_commit(self.repo, f"memory {label}", path=path)
        rows = tuple(self._rows)
        self.checkpoints.append((self._seal(rows), rows, label))
        return content

    @property
    def tip(self) -> str:
        return self.checkpoints[-1][0]

    @property
    def rows(self) -> list[LedgerRow]:
        return list(self._rows)


def test_projection_is_the_ledger_the_attributed_history_records(tmp_path: Path) -> None:
    """The projection from the trailers reproduces the table the tracked ledger carried.

    Every checkpoint is checked, not only the tip, because that is the closed loop the master
    asks for: the attributions the projection computes at each commit are the rows the ledger
    recorded at that same commit. The superseding pair survives as two rows -- a later closeout
    supersedes an earlier mapping without deleting it -- the unattributed commit contributes
    nothing, and the first row resolves each code commit to the memory commit the table did.
    """

    world = _AttributedWorld(tmp_path)
    for ledger_commit, rows, label in world.checkpoints:
        source = read_ledger_source(world.repo, ledger_commit)
        assert [(row.code_commit, row.memory_commit) for row in source.ledger.rows] == [
            (row.code_commit, row.memory_commit) for row in rows
        ], label

    source = read_ledger_source(world.repo, world.tip)
    assert [(row.code_commit, row.memory_commit) for row in source.ledger.rows] == [
        (row.code_commit, row.memory_commit) for row in world.rows
    ]
    # the superseding pair is intact, and the row the table resolves each code commit to is the
    # one the projection puts first
    assert len(world.rows) == 3
    assert [row.code_commit for row in world.rows] == [CODE_ONE, CODE_TWO, CODE_ONE]
    parsed = parse_ledger_text((world.repo / "memory.md").read_text(encoding="utf-8"))
    for code in (CODE_ONE, CODE_TWO):
        assert find_mapping(parsed, code) == find_mapping(source.ledger, code)


def test_projection_reads_the_trailer_and_never_the_live_table(tmp_path: Path) -> None:
    """A hand edit to the table does not move the projection, because the trailers decide.

    ``memory.md`` is fed a row that would sit at the top of the table, under a header agreeing
    with it, and the projection still reports the attributed rows without it: a reader that
    answers from the file cannot pass this, which is what "no hand-edit path survives" means for
    a reader.
    """

    world = _AttributedWorld(tmp_path)
    hand_row = LedgerRow("f" * 40, "e" * 40)
    (world.repo / "memory.md").write_text(
        ledger_to_text(_ledger([hand_row, *world.rows])), encoding="utf-8"
    )
    hand = _commit(world.repo, "a hand edit to the table")

    source = read_ledger_source(world.repo, hand)

    assert source.ledger.rows == world.rows
    assert find_mapping(source.ledger, "f" * 40) is None


def test_projection_reads_an_unattributed_commit_from_its_own_ledger(tmp_path: Path) -> None:
    """A history written before the trailer rule is read from the ledger that history carried.

    With no trailer anywhere above it, a commit's own table is the only record there is, and
    returning nothing instead would make a repository that predates the rule look empty. A reader
    with no fallback loses every row here.
    """

    empty = _init_repo(tmp_path / "unattributed")
    historical = LedgerRow("b" * 40, "c" * 40)
    latest = LedgerRow("d" * 40, "e" * 40)
    (empty / "memory.md").write_text(
        ledger_to_text(_ledger([historical, latest])), encoding="utf-8"
    )
    _commit(empty, "memory content with no trailer")
    head = _commit(empty, "the ledger commit that pinned it")

    source = read_ledger_source(empty, head)

    assert source.ledger.rows == [historical, latest]
    assert find_mapping(source.ledger, "b" * 40) == historical
    assert attributed_commits(empty, tip=head) == [
        AttributedCommit(head, None),
        AttributedCommit(require_git(empty, ["rev-parse", "HEAD~1"]).strip(), None),
    ]


def test_projection_contributes_nothing_for_a_source_that_says_nothing(tmp_path: Path) -> None:
    """A source with no trailer anywhere and no ledger blob is the bootstrap state, not a refusal."""

    empty = _init_repo(tmp_path / "bootstrap")
    bootstrap = _commit(empty, "first memory commit")

    assert read_ledger_source(empty, bootstrap).ledger.rows == []


def test_attribution_reads_only_the_commits_a_caller_asks_for(tmp_path: Path) -> None:
    """``exclude`` is how a branch asks for its own attributed commits and not the source's."""

    world = _AttributedWorld(tmp_path)
    own = _attributed_commit(
        world.repo, "memory m-own", code_commit=CODE_OWN, path="onboarding/own.md"
    )

    everything = attributed_commits(world.repo, tip=own)
    work_only = attributed_commits(world.repo, tip=own, exclude=world.tip)

    assert sorted(commit.memory_commit for commit in work_only) == [own]
    assert work_only[0].code_commit == CODE_OWN
    assert len(everything) > len(work_only)
    assert {commit.memory_commit for commit in work_only} < {
        commit.memory_commit for commit in everything
    }


def test_trailer_parse_takes_the_last_block_and_ignores_a_body_mention() -> None:
    """A message that merely mentions the key is not an attribution, and the last block wins."""

    assert parse_code_commit_trailer("docs: no attribution here") is None
    assert (
        parse_code_commit_trailer("docs: mention Code-Commit: " + "b" * 40 + "\n\nmore prose")
        is None
    )
    assert parse_code_commit_trailer("docs: one\n\nCode-Commit: " + "a" * 40) == "a" * 40
    assert (
        parse_code_commit_trailer(
            "\n".join(
                [
                    "docs: two",
                    "",
                    "Code-Commit: " + "a" * 40,
                    "",
                    "Code-Commit: " + "c" * 40,
                ]
            )
        )
        == "c" * 40
    )
    assert parse_code_commit_trailer("docs: not a sha\n\nCode-Commit: not-a-sha") is None


def test_attribution_reports_only_commits_the_code_repository_holds(tmp_path: Path) -> None:
    """A trailer naming a commit the code repository lacks is not a mapping."""

    world = _AttributedWorld(tmp_path)
    commit = _attributed_commit(
        world.repo, "memory m-ghost", code_commit="f" * 40, path="onboarding/ghost.md"
    )

    commits = attributed_commits(world.repo, tip=commit, exclude=world.tip)

    assert [entry.code_commit for entry in commits] == ["f" * 40]
    assert ledger_rows_from_attribution(commits) == [LedgerRow("f" * 40, commit)]
    assert ledger_rows_from_attribution(commits, code_repository=world.repo) == []


def test_attribution_reads_a_message_whose_final_block_carries_several_trailers(
    tmp_path: Path,
) -> None:
    """A git reader takes the key, not the last line: another trailer in the block is not it.

    The history's own commit bodies are the closeout message verbatim, so the last block is not
    guaranteed to contain only this attribution, and reading the last line instead of the key
    would silently mis-attribute the commit.
    """

    world = _AttributedWorld(tmp_path)
    destination = world.repo / "onboarding" / "mixed.md"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("mixed\n", encoding="utf-8")
    require_git(world.repo, ["add", "-A"])
    require_git(
        world.repo,
        [
            "commit",
            "-q",
            "-m",
            f"memory m-mixed\n\nCode-Commit: {CODE_TWO}\nReviewed-By: someone@example.invalid",
        ],
    )
    mixed = head_commit(world.repo)

    commits = attributed_commits(world.repo, tip=mixed, exclude=world.tip)

    assert [(entry.memory_commit, entry.code_commit) for entry in commits] == [(mixed, CODE_TWO)]
    assert "Reviewed-By" in require_git(world.repo, ["log", "-1", "--format=%B", mixed])


def test_the_rendered_trailer_is_the_one_the_reader_parses(tmp_path: Path) -> None:
    """The writer's key and the reader's key are one literal, proved through Git.

    The writer's own output is committed and read back by the real reader path, so this fails if
    either side changes its copy of the key: the derived row would simply disappear, which is the
    worst failure this system can have because it looks like "no attribution exists" rather than
    like a bug. Asserting the constant against itself would not catch that, so nothing here
    restates the key -- it goes through ``memory_content_message`` and the trailer walk.
    """

    world = _AttributedWorld(tmp_path)
    leg = EnabledCloseoutLeg(reason="test", message="Test closeout commit")
    rendered = EffectiveCloseoutInput(
        route="worktree",
        contractKind="leaf",
        memoryMode="external",
        code=leg,
        memory=leg,
        ledger=leg,
    ).memory_content_message(CODE_TWO)

    assert CODE_COMMIT_TRAILER_KEY in rendered
    destination = world.repo / "onboarding" / "round-trip.md"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("round trip\n", encoding="utf-8")
    require_git(world.repo, ["add", "-A"])
    require_git(world.repo, ["commit", "-q", "-m", rendered])
    rendered_commit = head_commit(world.repo)

    commits = attributed_commits(world.repo, tip=rendered_commit, exclude=world.tip)

    assert [(entry.memory_commit, entry.code_commit) for entry in commits] == [
        (rendered_commit, CODE_TWO)
    ]
    assert ledger_rows_from_attribution(commits, code_repository=world.repo) == []
    assert parse_code_commit_trailer(rendered) == CODE_TWO


def test_attribution_reads_a_mapping_that_arrived_through_a_merge(tmp_path: Path) -> None:
    """A memory line merges, and a merged-in mapping is still an ancestor of the tip.

    This is the shape the real memory history has. Measured by resolving every memory-commit
    cell of the tracked table at 5e4899ea with ``git rev-parse`` before comparing it to
    ``git rev-list`` output: 463 of its 476 rows name a commit that is an ancestor of the tip, and
    442 are on the tip's first-parent line, so 34 rows sit off that line. Reading only the line
    would make those mappings invisible, which is the partial coverage the trailer rule exists to
    prevent.
    """

    world = _AttributedWorld(tmp_path)
    tip = world.tip
    require_git(world.repo, ["switch", "-q", "-c", "merged-in", tip])
    merged = _attributed_commit(
        world.repo, "memory merged-in", code_commit=CODE_TWO, path="onboarding/merged.md"
    )
    require_git(world.repo, ["switch", "-q", "-"])
    require_git(world.repo, ["merge", "-q", "--no-ff", "-m", "merge the memory line", "merged-in"])
    merge_tip = head_commit(world.repo)

    commits = attributed_commits(world.repo, tip=merge_tip)

    assert merged in {commit.memory_commit for commit in commits}
    rows = ledger_rows_from_attribution(commits)
    assert LedgerRow(CODE_TWO, merged) in rows
    assert len(rows) == len(world.rows) + 1
    # ...and the second parent really is off the first-parent line, so a first-parent walk
    # could not have found it
    first_parent = require_git(world.repo, ["rev-list", "--first-parent", merge_tip]).split()
    assert merged not in first_parent
