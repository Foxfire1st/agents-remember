"""A page open names the records it read, and none of the leaf's other comparison records (D570).

A leaf records one comparison per change, so its history grows with every edit. The opening
answers are kept on the records they read -- the comparison's own -- and on the names the
comparison directory lists: a new record moves the names and is another answer, while an edit of a
record no answer read changes nothing any of them read. So the record bytes an open reads do not
grow with the history. (The review's external-movement check still selects its comparison only
when every retained record is readable, so its answer names each record; re-observing an unchanged
one is a stat, never a read.) Real Git repositories and the page's own routes throughout.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from agents_remember.application.review_candidate_resolution import resolve_review_candidate
from agents_remember.application.review_comparison_records import comparison_directory
from agents_remember.kernel import recorded_reads
from agents_remember.kernel.primitives.runtime_config import RepositoryScope
from agents_remember.serving.review_open_observation import OPEN_OBSERVATIONS
from agents_remember.worktrees.services import reset_worktree_services
from agents_remember_test_support.testing.review_git_trees import (
    LEAF,
    MASTER,
    REPO,
    build_world,
)
from test_review_opening_answers import _Opened

pytestmark = pytest.mark.evidence_unit


def _opened_with_history(tmp_path: Path, earlier: int) -> _Opened:
    """The leaf's own comparison as its latest record, after ``earlier`` records of other trees."""

    world = build_world(tmp_path)
    world.edit()
    resolve_review_candidate(world.config, REPO, MASTER, LEAF)  # the leaf's own record, #1
    directory = comparison_directory(world.task_root, LEAF)
    own = json.loads((directory / "1.json").read_text())
    (directory / "1.json").unlink()
    (directory / f"{earlier + 1}.json").write_text(json.dumps({**own, "number": earlier + 1}))
    for number in range(1, earlier + 1):
        side = {**own["code_candidate"], "tree": f"{number:040x}"}
        (directory / f"{number}.json").write_text(
            json.dumps({**own, "number": number, "code_candidate": side})
        )
    # The explainer's environment is admitted only for a repository the settings name.
    scope = RepositoryScope(repo_id=REPO, path=world.code, memory_root=world.memory)
    world.config = replace(world.config, repositories={REPO: scope})
    return _Opened(world)


@pytest.fixture
def history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[int], _Opened]]:
    # The records are written just before the opens: outside Git's racy window, a record's identity
    # is held against its stat as an old record's is, rather than read again on each observation.
    monkeypatch.setattr(recorded_reads, "RACY_NS", 0)
    made: list[_Opened] = []

    def make(earlier: int) -> _Opened:
        reset_worktree_services()
        OPEN_OBSERVATIONS.clear()
        made.append(_opened_with_history(tmp_path / f"history-{earlier}", earlier))
        # Only the page's own reads: an open's first read would otherwise start the background
        # capture, whose reads (and any record it mints) would land amid the ones counted here.
        OPEN_OBSERVATIONS.bind_opened(None)
        return made[-1]

    yield make
    for opened in made:
        shutdown = opened.collaborators.review_trees_shutdown
        assert shutdown is not None
        shutdown()
    OPEN_OBSERVATIONS.clear()
    reset_worktree_services()


def _history_read(
    monkeypatch: pytest.MonkeyPatch, opened: _Opened, open_id: str
) -> tuple[int, list[tuple[str, str]]]:
    """How many times one open reads a comparison record's bytes, and how it had its answers."""

    directory = comparison_directory(opened.world.task_root, LEAF).absolute()
    read = Path.read_bytes
    count = [0]

    def counted(path: Path) -> bytes:
        if path.absolute().parent == directory:
            count[0] += 1
        return read(path)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_bytes", counted)
        answers = opened.page(open_id)
    return count[0], answers


def test_the_record_bytes_an_open_reads_do_not_grow_with_the_leafs_history(
    history, monkeypatch: pytest.MonkeyPatch
) -> None:
    read: dict[int, int] = {}
    for earlier in (5, 200):
        opened = history(earlier)
        opened.build()
        opened.page(f"warm-{earlier}")
        count, answers = _history_read(monkeypatch, opened, f"kept-{earlier}")
        assert all(how == "read" for _name, how in answers), answers
        read[earlier] = count
    assert read[200] == read[5], f"the open read the history: {read} record reads"


def _new_record(opened: _Opened) -> None:
    """A successor record: the leaf's next comparison, as a later change records it."""

    directory = comparison_directory(opened.world.task_root, LEAF)
    own = json.loads((directory / f"{opened.record.number}.json").read_text())
    number = opened.record.number + 1
    side = {**own["code_candidate"], "tree": "f" * 40}
    (directory / f"{number}.json").write_text(
        json.dumps({**own, "number": number, "code_candidate": side})
    )


def _edit_an_unread_record(opened: _Opened) -> None:
    """An earlier record's bytes rewritten under its own name: no opening answer read it."""

    path = comparison_directory(opened.world.task_root, LEAF) / "1.json"
    record = json.loads(path.read_text())
    path.write_text(json.dumps({**record, "recorded_at": "2026-10-26T00:00:00+00:00"}))


@pytest.mark.parametrize(
    ("move", "kept"),
    [(_new_record, False), (_edit_an_unread_record, True)],
    ids=["a-new-record-moves-the-names", "an-edit-of-a-record-no-answer-read"],
)
def test_a_new_record_is_another_answer_and_an_unread_records_edit_changes_none(
    history, move: Callable[[_Opened], None], kept: bool
) -> None:
    opened = history(3)
    opened.build()
    assert all(how == "read" for _name, how in opened.page("before")), "not kept to begin with"
    move(opened)
    answers = dict(opened.page("after"))
    for name in ("overview", "files"):
        assert (answers[name] == "read") is kept, answers
