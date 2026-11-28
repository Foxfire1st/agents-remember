"""A record a managed memory sync stores names who wrote exactly that content.

``worktree_sync(memory_sync_choice="merge-memory")`` merges two converted lines' records without
their ``writer``. A record stored without a conflict then takes the stamp of the line whose record
it equals (none where that line's record has none), and a record that combines both lines' meaning
edits carries the product's stamp for the merge. That stamp is named in the syncing leaf's history
file, so the reader, the state and the writer table read it from the same tree. A difference in
``writer`` alone is no conflict; two edits of one meaning field still are.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from agents_remember.mcp.tools.knowledge import ReadToolRequest, knowledge_read_payload
from agents_remember.memory.conversion.crossing_writer import CROSSING_OPERATION, is_record_path
from agents_remember.memory.conversion.state_conversion import plan_state_conversion
from agents_remember.memory.conversion.task_review_facts import TaskOwners
from agents_remember.models.knowledge_files.canonical import canonical_text
from agents_remember.models.knowledge_files.converted_reviews import CONVERTED_REVIEWS_PATH
from agents_remember.models.knowledge_files.documents import history_path
from agents_remember.models.knowledge_files.history_product import record_content_digest
from agents_remember.models.knowledge_files.records import LEGACY_STATE_WORDS
from agents_remember.tasks.task_paths import ARCHIVE_DIR
from agents_remember.worktrees.knowledge_crossing import crossing_plan, merge_base
from agents_remember_test_support.testing import knowledge_crossing_standing as standing
from knowledge_conversion_test_support import git
from knowledge_review_fact_test_support import review_fact
from knowledge_review_fact_test_support import stamp as agent_stamp
from test_knowledge_state_conversion import _task_document, _write
from test_worktree_sync_knowledge_merge import RECORD, Lines, _converted_lines


def _stamp(lines: Lines, seat: str, at: str) -> dict[str, Any]:
    leaf = lines.fixture.contract.task_id
    return {
        "by": "agent",
        "role": "curator",
        "harness": "claude",
        "model": f"model-{seat}",
        "effort": "low",
        "seat": seat,
        "task": leaf.rsplit("-L", 1)[0],
        "leaf": leaf,
        "at": at,
    }


def _record(
    base: dict[str, Any], writer: dict[str, Any] | None, **fields: Any
) -> dict[str, bytes | None]:
    document = {key: value for key, value in base.items() if key != "writer"}
    document.update(fields)
    if writer is not None:
        if document.get("status") in LEGACY_STATE_WORDS:
            document.pop("status")
        document["writer"] = writer
    return {RECORD: canonical_text(document).encode("utf-8")}


def _other_record(lines: Lines) -> str:
    folder = lines.leaf / "knowledge" / "invariants"
    others = sorted(path for path in folder.glob("*.json") if path.name != Path(RECORD).name)
    return others[0].relative_to(lines.leaf).as_posix()


def _merge_time(lines: Lines) -> str:
    """The later committer time of the merge commit's two parents."""

    times = [git(lines.leaf, "show", "-s", "--format=%cI", f"HEAD^{n}") for n in (1, 2)]
    return max(times, key=datetime.fromisoformat)


def _read(lines: Lines, tmp_path: Path, **request: Any) -> dict[str, Any]:
    out = knowledge_read_payload(
        ReadToolRequest(memory_root=str(lines.leaf), **request),
        coordination_root=str(tmp_path / "coordination"),
    )
    assert out["state"] == "view", out
    return out


@pytest.mark.parametrize(
    ("leaf_stamped", "line_stamped"),
    [(False, False), (True, False), (True, True)],
    ids=["two-old-shaped", "stamped-and-old-shaped", "two-stamped"],
)
def test_a_record_combining_both_lines_edits_carries_the_products_stamp(
    tmp_path: Path, leaf_stamped: bool, line_stamped: bool
) -> None:
    """Both lines raised one record by another meaning field: no conflict, the product's stamp.

    Catches a combined record stored with no writer, with one line's agent stamp over content that
    agent did not write, and a writer-only conflict between two stamped lines.
    """

    lines = _converted_lines(tmp_path)
    base = lines.read(RECORD)
    assert "writer" not in base  # the fixture's lines are converted from the old shape
    revision = base["revision"] + 1
    leaf_writer = _stamp(lines, "seat-leaf", "2026-01-02T10:00:00+01:00") if leaf_stamped else None
    line_writer = _stamp(lines, "seat-line", "2026-01-03T10:00:00+01:00") if line_stamped else None
    lines.on_leaf(_record(base, leaf_writer, statement="Leaf statement.", revision=revision))
    lines.on_line(
        _record(base, line_writer, applicability="Line applicability.", revision=revision)
    )

    merged = lines.merge()

    assert merged["state"] == "synced", merged
    stored = lines.read(RECORD)
    assert (stored["statement"], stored["applicability"], stored["revision"]) == (
        "Leaf statement.",
        "Line applicability.",
        revision,
    )
    leaf_id = lines.fixture.contract.task_id
    assert stored["writer"] == {
        "by": "product",
        "role": "product",
        "harness": "product",
        "model": "none",
        "effort": "none",
        "seat": "product",
        "task": leaf_id.rsplit("-L", 1)[0],
        "leaf": leaf_id,
        "at": _merge_time(lines),
        "operation": CROSSING_OPERATION,
    }
    # The product's revision is named in the syncing leaf's history file of the same tree.
    history = lines.read(f"knowledge/history/{leaf_id}.json")
    assert {
        "record": stored["id"],
        "revision": revision,
        "writer": stored["writer"],
        "contentDigest": record_content_digest(stored),
    } in history["written"]
    assert git(lines.leaf, "status", "--porcelain") == ""
    # The reader names the product as the writer; the state is what the history files give: the
    # leaf that names the revision is not closed out, so it reads proposed.
    record = _read(lines, tmp_path, view="record", record_id=stored["id"])["record"]
    assert record["recordState"] == "proposed"
    assert (record["writer"]["role"], record["writer"]["model"]) == ("product", "none")
    # The writer table counts that revision under the product, as one written revision.
    writers = _read(lines, tmp_path, view="writers")["rows"]
    product = [row for row in writers if (row["role"], row["model"]) == ("product", "none")]
    assert [(row["harness"], row["written"]) for row in product] == [("product", 1)]


def test_a_record_only_one_line_changed_keeps_that_lines_stamp_or_none(tmp_path: Path) -> None:
    """The stored record is one line's record: that line's stamp, or no stamp where it had none.

    Catches the product's stamp put on content one agent wrote, and a stamp invented for an
    old-shaped record.
    """

    lines = _converted_lines(tmp_path)
    base = lines.read(RECORD)
    other = _other_record(lines)
    other_base = lines.read(other)
    writer = _stamp(lines, "seat-leaf", "2026-01-02T10:00:00+01:00")
    lines.on_leaf(_record(base, writer, statement="Leaf statement.", revision=base["revision"] + 1))
    lines.on_line(
        {
            other: canonical_text(
                {
                    **other_base,
                    "statement": "Line statement.",
                    "revision": other_base["revision"] + 1,
                }
            ).encode("utf-8")
        }
    )

    merged = lines.merge()

    assert merged["state"] == "synced", merged
    assert lines.read(RECORD)["writer"] == writer
    assert "writer" not in lines.read(other)  # the line's old-shaped record stays unstamped
    # No revision of the product's: the merge opens no history file for the leaf.
    leaf_id = lines.fixture.contract.task_id
    assert not (lines.leaf / f"knowledge/history/{leaf_id}.json").exists()


def _bind_review_tree(
    lines: Lines, files: dict[str, bytes | None], owner: str, incoming: bool
) -> None:
    commit = lines.on_line if incoming else lines.on_leaf
    source = lines.line if incoming else lines.leaf
    path = history_path(owner)
    history = json.loads(files[path] or b"{}")
    pending = standing.encoded({**history, "closed": False})
    commit({RECORD: files[RECORD], path: pending}, "store the exact text the review passed")
    history["review"] = {**review_fact(owner), "tree": git(source, "rev-parse", "HEAD^{tree}")}
    files[path] = standing.encoded(history)


@pytest.mark.parametrize("selected_incoming", [False, True], ids=["own", "incoming"])
@pytest.mark.parametrize("selected_shape", ["unstamped", "stamped", "stamped-unreviewed"])
def test_a_selected_side_only_gets_credit_for_its_exact_content(
    tmp_path: Path, selected_incoming: bool, selected_shape: str
) -> None:
    """The merge keeps one side verbatim even though the opposing content shares the number."""

    lines = _converted_lines(tmp_path)
    base = lines.read(RECORD)
    revision = base["revision"] + 1
    record_id = base["id"]
    leaf = lines.fixture.contract.task_id
    other = "260101-ALT-L1"
    selected_writer = (
        None if selected_shape == "unstamped" else agent_stamp(other, model="selected-model")
    )
    other_writer = agent_stamp(leaf, model="other-model")
    selected = _record(
        base,
        selected_writer,
        revision=revision,
        statement="Same statement.",
        applicability="Selected side's exact additional text.",
    )
    opposite = _record(base, other_writer, revision=revision, statement="Same statement.")
    for files, owner, writer in (
        (opposite, leaf, other_writer),
        (selected, other, selected_writer),
    ):
        if writer is not None:
            record = json.loads(files[RECORD] or b"{}")
            files[history_path(owner)] = standing.encoded(
                {
                    "schema": "ar-history/v1",
                    "leaf": owner,
                    "closed": True,
                    "rows": [],
                    "written": [standing.written(record, writer)],
                }
            )
    _bind_review_tree(lines, opposite, leaf, not selected_incoming)
    if selected_shape == "stamped":
        _bind_review_tree(lines, selected, other, selected_incoming)
    if selected_incoming:
        lines.on_leaf(opposite)
        lines.on_line(selected)
    else:
        lines.on_leaf(selected)
        lines.on_line(opposite)

    result = lines.merge()
    assert result["state"] == "synced", result
    assert (lines.leaf / RECORD).read_bytes() == selected[RECORD]
    frozen = f"knowledge/history/{leaf}.json"
    assert (lines.leaf / frozen).read_bytes() == opposite[frozen]

    record = _read(lines, tmp_path, view="record", record_id=record_id)["record"]
    reviewed = selected_shape == "stamped"
    assert record["writer"]["model"] == (selected_writer["model"] if selected_writer else "unknown")
    assert record["recordState"] == ("reviewed" if reviewed else "proposed")
    writers = _read(lines, tmp_path, view="writers")["rows"]
    credits = {row["model"]: (row["written"], row["reviewed"]) for row in writers}
    assert "other-model" not in credits
    assert credits.get("selected-model") == ((1, int(reviewed)) if selected_writer else None)
    if selected_shape == "unstamped":
        files = {
            path.relative_to(lines.leaf).as_posix(): path.read_bytes()
            for path in (lines.leaf / "knowledge").rglob("*.json")
        }
        plan = plan_state_conversion(files, TaskOwners(), at="2026-11-19T21:00:00+01:00")
        converted = json.loads(plan.changes[RECORD])
        assert (converted["writer"]["by"], converted["writer"]["model"]) == (
            "conversion",
            "unknown",
        )
        assert "leaf" not in converted["writer"]


def test_two_stamped_lines_conflict_only_on_the_meaning_field_both_changed(tmp_path: Path) -> None:
    """Two edits of one meaning field still conflict, and their different stamps add no conflict."""

    lines = _converted_lines(tmp_path)
    base = lines.read(RECORD)
    revision = base["revision"] + 1
    lines.on_leaf(
        _record(
            base,
            _stamp(lines, "seat-leaf", "2026-01-02T10:00:00+01:00"),
            statement="Leaf statement.",
            revision=revision,
        )
    )
    lines.on_line(
        _record(
            base,
            _stamp(lines, "seat-line", "2026-01-03T10:00:00+01:00"),
            statement="Line statement.",
            revision=revision,
        )
    )

    conflicted = lines.merge()

    assert conflicted["state"] == "sync-resolution-required", conflicted
    items = {
        (one["path"], one["item"]) for one in conflicted["resolution"]["crossing"]["conflicts"]
    }
    assert items == {(RECORD, "statement")}


def test_a_master_lines_combined_record_is_named_in_its_crossing_file(tmp_path: Path) -> None:
    """A master line has no leaf: the product's revision is named in ``<task>-crossing-<n>``."""

    lines = _converted_lines(tmp_path)
    base = lines.read(RECORD)
    revision = base["revision"] + 1
    own = lines.on_leaf(_record(base, None, statement="Own statement.", revision=revision))
    incoming = lines.on_line(_record(base, None, applicability="Incoming.", revision=revision))
    git(lines.leaf, "fetch", "-q", str(lines.line), "main")

    plan = crossing_plan(
        lines.leaf,
        (merge_base(lines.leaf, own, incoming), own, incoming),
        paired_code=None,
        owner=("master", "260101-FIX"),
    )

    assert plan.conflicts == ()
    stored = json.loads(plan.files[RECORD] or b"{}")
    assert stored["writer"]["crossing"] == "260101-FIX-crossing-1"
    assert stored["writer"]["task"] == "260101-FIX"
    crossing = json.loads(plan.files["knowledge/history/260101-FIX-crossing-1.json"] or b"{}")
    assert crossing["written"] == [
        {
            "record": stored["id"],
            "revision": revision,
            "writer": stored["writer"],
            "contentDigest": record_content_digest(stored),
        }
    ]


def _other_line(
    owner: str, record: dict[str, Any], kind: str, shape: str
) -> dict[str, bytes | None]:
    """The other line's own content of the record at its revision, and its closed history file;
    for ``kept``, also another closed Consolidator file that kept that number."""

    record_id, revision = record["id"], record["revision"]
    stamped = shape == "stamped"
    document: dict[str, Any] = {
        "schema": "ar-history/v1",
        "leaf": owner,
        "closed": True,
        "rows": [] if stamped else [standing.changed_row(record_id, revision)],
    }
    if stamped:
        document["written"] = [
            {"record": record_id, "revision": revision, "writer": agent_stamp(owner)}
        ]
    if kind != "none":
        handed = "curator" if kind == "review" else "consolidator"
        document["review"] = review_fact(owner, handed_over_by=handed)
    files = {
        **_record(record, agent_stamp(owner) if stamped else None, applicability="Line text."),
        f"knowledge/history/{owner}.json": canonical_text(document).encode("utf-8"),
    }
    if kind == "kept":
        keeper = owner.replace("-L1", "-L2")
        kept = {
            "schema": "ar-history/v1",
            "leaf": keeper,
            "closed": True,
            "rows": [standing.kept_row(record_id, revision)],
            "review": review_fact(keeper, handed_over_by="consolidator"),
        }
        files[f"knowledge/history/{keeper}.json"] = canonical_text(kept).encode("utf-8")
    return files


_SYNCING_CLOSE = {"open": None, "closed-no-pass": False, "reviewed": "curator"}
_SYNCING_CLOSE["consolidated"] = "consolidator"


@pytest.mark.parametrize("syncing", list(_SYNCING_CLOSE))
@pytest.mark.parametrize("other_fact", ["review", "consolidator", "kept", "none"])
@pytest.mark.parametrize("shape", ["stamped", "legacy"])
@pytest.mark.parametrize("other", ["AAA-L1", "ZZZ-L1"], ids=["sorts-before", "sorts-after"])
def test_a_combined_revision_reads_the_state_its_own_writer_gives_in_either_file_order(
    tmp_path: Path, syncing: str, other_fact: str, shape: str, other: str
) -> None:
    """Another line wrote its own content under the combined revision's number, stamped or from
    before stamps (a ``changed`` row only), and closed it with a curator's fact, a Consolidator's
    fact, a Consolidator's fact while another Consolidator file kept that number, or none.

    The combined revision reads only what the syncing leaf's own file gives it: proposed while
    that file is open or closed without a pass, reviewed or consolidated once it is closed with
    such a fact. Catches the other line's fact or a kept row deciding it, a write from before
    stamps not counting as a second content, and the order of the files' names deciding it, in
    the record view and in the writers table.
    """

    lines = _converted_lines(tmp_path)
    leaf_id = lines.fixture.contract.task_id
    assert other < leaf_id if other == "AAA-L1" else other > leaf_id
    base = lines.read(RECORD)
    revision = base["revision"] + 1
    lines.on_leaf(
        _record(
            base,
            _stamp(lines, "seat-leaf", "2026-01-02T10:00:00+01:00"),
            statement="Leaf statement.",
            revision=revision,
        )
    )
    lines.on_line(_other_line(other, {**base, "revision": revision}, other_fact, shape))
    assert lines.merge()["state"] == "synced"
    stored = lines.read(RECORD)
    assert stored["writer"]["by"] == "product"
    handed = _SYNCING_CLOSE[syncing]
    if handed is not None:
        own = f"knowledge/history/{leaf_id}.json"
        closed = {**lines.read(own), "closed": True}
        if isinstance(handed, str):
            closed["review"] = review_fact(leaf_id, handed_over_by=handed)
        (lines.leaf / own).write_text(canonical_text(closed), encoding="utf-8")
        git(lines.leaf, "add", "-A")
        git(lines.leaf, "commit", "-qm", "close the syncing leaf")

    expected = {"reviewed": "reviewed", "consolidated": "consolidated"}.get(syncing, "proposed")
    record = _read(lines, tmp_path, view="record", record_id=stored["id"])["record"]
    assert record["recordState"] == expected
    writers = _read(lines, tmp_path, view="writers")["rows"]
    product = [row for row in writers if (row["role"], row["model"]) == ("product", "none")]
    assert [(row["written"], row["reviewed"]) for row in product] == [
        (1, 0 if expected == "proposed" else 1)
    ]


def _matrix_facts(
    histories: dict[str, tuple[str, dict[str, Any]]], shape: str, tree: str, tasks: Path
) -> dict[str, bytes | None]:
    """Name a real reviewed tree in the modern fact or in the archive consumed by conversion."""

    files: dict[str, bytes | None] = {}
    _write(tasks, "matrix/task.json", _task_document("261112-FIX", "master", []))
    for owner, (fact, history) in histories.items():
        if fact in {"consolidator-write", "consolidator-keep", "consolidator-no-keep"}:
            history["review"] = {**review_fact(owner, handed_over_by="consolidator"), "tree": tree}
        elif fact in {"exact", "other-write", "other-tree"}:
            if shape == "converted":
                task = _task_document(owner, "subTask", [(1, True)])
                review = task["memoryReviewState"]
                review["candidateTree"] = tree
                review["recordedRounds"][0]["memoryTree"] = tree
                _write(tasks, f"matrix/{owner}.json", task)
            else:
                history["review"] = {**review_fact(owner), "tree": tree}
        files[history_path(owner)] = standing.encoded({"schema": "ar-history/v1", **history})
    return files


def _matrix_prepare(
    profile: standing.CrossingProfile,
    lines: Lines,
    scratch: Path,
    seed: dict[str, Any],
    *,
    selected: bool,
) -> None:
    reviewed, current, histories = standing.matrix_records(
        seed, agent_stamp, selected=selected, shape=profile.shape, prefix=profile.name
    )
    profile.commit(lines, reviewed, "record the exact text available to review")
    tree = git(profile.root(lines), "rev-parse", "HEAD^{tree}")
    tasks = scratch / f"{profile.name}-tasks"
    facts = _matrix_facts(histories, profile.shape, tree, tasks)
    profile.commit(lines, {**current, **facts}, "retain the live text and close its history")
    if profile.shape == "converted":
        standing.convert(profile, lines, scratch, tasks)


def _assert_closeout_tie(
    lines: Lines, scratch: Path, selected: standing.CrossingProfile, other: standing.CrossingProfile
) -> None:
    """One common write gains review on one branch when its history closes."""

    owner = f"{selected.name}-L1"
    writer = agent_stamp(owner, model=f"model-{selected.name}")
    record = {**standing.next_record(lines.read(RECORD)), "writer": writer}
    payload = standing.encoded(record)
    path = history_path(owner)
    body = {"written": [standing.written(record, writer)]}
    pending = {"schema": "ar-history/v1", "leaf": owner, "closed": False, "rows": [], **body}
    lines.on_line({RECORD: payload, path: standing.encoded(pending)}, "write the common revision")
    assert lines.merge()["state"] == "synced"
    source = selected.root(lines)
    review = {**review_fact(owner), "tree": git(source, "rev-parse", "HEAD^{tree}")}
    _, frozen = standing.write_closed_history(lines, selected, owner, {**body, "review": review})
    standing.write_closed_history(lines, other, f"{other.name}-L1", {})
    writer_key = tuple(writer[field] for field in standing.WRITER_FIELDS)
    standing.assert_public_standing(source, scratch, record, "reviewed", writer_key)
    standing.assert_public_standing(other.root(lines), scratch, record, "proposed", writer_key)
    assert (lines.leaf / RECORD).read_bytes() == (lines.line / RECORD).read_bytes() == payload

    assert lines.merge()["state"] == "synced"
    assert (lines.leaf / RECORD).read_bytes() == payload
    assert (lines.leaf / path).read_bytes() == frozen
    standing.assert_public_standing(lines.leaf, scratch, record, "reviewed", writer_key)


def _assert_unbound_review(
    lines: Lines, scratch: Path, selected: standing.CrossingProfile, other: standing.CrossingProfile
) -> None:
    """A recovered legacy pass with no tree stays unproven after a same-number crossing."""

    owner = f"{selected.name}-L1"
    alternate = standing.next_record(lines.read(RECORD))
    retained = {**alternate, "applicability": "The selected meaning."}
    selected.commit(lines, {RECORD: standing.encoded(retained)}, "write the legacy revision")
    path, frozen = standing.write_closed_history(
        lines,
        selected,
        owner,
        {"rows": [standing.changed_row(retained["id"], int(retained["revision"]))]},
    )
    tasks = scratch / "archived-review"
    _store_archived_review(tasks, owner, None)
    standing.convert(selected, lines, scratch, tasks)
    source = selected.root(lines)
    retained_bytes = (source / RECORD).read_bytes()
    alternate_owner = f"{other.name}-L1"
    alternate_writer = agent_stamp(alternate_owner, model=f"model-{other.name}")
    alternate["writer"] = alternate_writer
    other.commit(lines, {RECORD: standing.encoded(alternate)}, "write the other meaning")
    other_path, other_history = standing.write_closed_history(
        lines, other, alternate_owner, {"written": [standing.written(alternate, alternate_writer)]}
    )
    unknown = ("unknown",) * 3
    standing.assert_public_standing(source, scratch, retained, "reviewed", unknown)

    assert lines.merge()["state"] == "synced"
    assert (lines.leaf / RECORD).read_bytes() == retained_bytes
    assert (lines.leaf / path).read_bytes() == frozen
    assert (lines.leaf / other_path).read_bytes() == other_history
    standing.assert_public_standing(lines.leaf, scratch, retained, "proposed", unknown)


def _assert_equal_meaning(
    lines: Lines, scratch: Path, selected: standing.CrossingProfile, other: standing.CrossingProfile
) -> None:
    """Independent writers of equal meaning have distinct stored stamps."""

    record = standing.next_record(lines.read(RECORD))
    expected = {}
    for profile in (selected, other):
        owner = f"{profile.name}-L1"
        writer = agent_stamp(owner, model=f"model-{profile.name}")
        authored = {**record, "writer": writer}
        reviewed = standing.write_review_tree(lines, profile, authored, RECORD)
        body: dict[str, Any] = {"written": [standing.written(authored, writer)]}
        state = "proposed"
        if profile == other:
            body["review"] = {**review_fact(owner), "tree": reviewed.tree}
            state = "reviewed"
        path, payload = standing.write_closed_history(lines, profile, owner, body)
        expected[path] = payload
        key = tuple(writer[field] for field in standing.WRITER_FIELDS)
        standing.assert_public_standing(profile.root(lines), scratch, authored, state, key)
    retained = (lines.leaf / RECORD).read_bytes()
    assert retained != (lines.line / RECORD).read_bytes()

    assert lines.merge()["state"] == "synced"
    assert (lines.leaf / RECORD).read_bytes() == retained
    assert {path: (lines.leaf / path).read_bytes() for path in expected} == expected
    own_writer = json.loads(retained)["writer"]
    own_key = tuple(own_writer[field] for field in standing.WRITER_FIELDS)
    standing.assert_public_standing(lines.leaf, scratch, record, "proposed", own_key)


_MATRIX_CONTROLS = {
    "tie-exact": _assert_closeout_tie,
    "unbound-review": _assert_unbound_review,
    "equal-meaning": _assert_equal_meaning,
}


def _store_archived_review(tasks: Path, owner: str, tree: str | None) -> None:
    folder = f"{ARCHIVE_DIR}/fixture"
    _write(tasks, f"{folder}/task.json", _task_document("261112-FIX", "master", []))
    document = _task_document(owner, "subTask", [(1, True)])
    review = document["memoryReviewState"]
    review["candidateTree"] = tree
    verdict = tasks / folder / "notes/reports/memory-verdict.md"
    verdict.parent.mkdir(parents=True)
    passed = f"PASS on {tree}" if tree is not None else "PASS"
    verdict.write_text(f"# Memory review\n\n{passed}\n", encoding="utf-8")
    review["recordedRounds"][0].update(memoryTree=tree, verdictRef=str(verdict))
    _write(tasks, f"{folder}/01_leaf.json", document)


def _assert_prior_review_path(
    lines: Lines, scratch: Path, selected: standing.CrossingProfile, other: standing.CrossingProfile
) -> None:
    alternate = standing.next_record(lines.read(RECORD))
    owner = f"{selected.name}-L1"
    writer = agent_stamp(owner, model=f"model-{selected.name}")
    retained = {**alternate, "applicability": "The exact applicability the review read."}
    converted = selected.shape == "converted"
    if not converted:
        retained["writer"] = writer
    prior_path = RECORD.removesuffix(".json") + "-prior-name.json"
    source_tree = standing.write_review_tree(
        lines, selected, retained, RECORD, prior_path=prior_path
    )
    selected.commit(
        lines,
        {prior_path: None, RECORD: standing.encoded(retained)},
        "move the record to its current name",
    )
    source = selected.root(lines)
    body: dict[str, Any] = {
        "rows": [standing.changed_row(retained["id"], int(retained["revision"]))]
    }
    key: tuple[str, ...] = ("unknown",) * 3
    if not converted:
        body = {
            "written": [standing.written(retained, writer)],
            "review": {**review_fact(owner), "tree": source_tree.tree},
        }
        key = tuple(writer[field] for field in standing.WRITER_FIELDS)
    path, history = standing.write_closed_history(lines, selected, owner, body)
    frozen = {path: history}
    if converted:
        tasks = scratch / "archived-review"
        _store_archived_review(tasks, owner, source_tree.tree)
        standing.convert(selected, lines, scratch, tasks)
        frozen[CONVERTED_REVIEWS_PATH] = (source / CONVERTED_REVIEWS_PATH).read_bytes()
        stored = json.loads((source / RECORD).read_bytes())
        assert stored["writer"]["operation"] == "state-conversion"
    alternate_owner = f"{other.name}-L1"
    alternate_writer = agent_stamp(alternate_owner, model=f"model-{other.name}")
    alternate["writer"] = alternate_writer
    alternate_tree = standing.write_review_tree(lines, other, alternate, RECORD)
    alternate_body = {
        "written": [standing.written(alternate, alternate_writer)],
        "review": {**review_fact(alternate_owner), "tree": alternate_tree.tree},
    }
    alternate_path, alternate_history = standing.write_closed_history(
        lines, other, alternate_owner, alternate_body
    )
    frozen[alternate_path] = alternate_history
    assert record_content_digest(alternate) != record_content_digest(retained)
    retained_bytes = (source / RECORD).read_bytes()
    standing.assert_public_standing(source, scratch, retained, "reviewed", key)

    outcome = lines.merge()
    assert outcome["state"] == "synced", outcome
    assert (lines.leaf / RECORD).read_bytes() == retained_bytes
    assert not (lines.leaf / prior_path).exists()
    assert {name: (lines.leaf / name).read_bytes() for name in frozen} == frozen
    source_tree.prove(lines.leaf)
    alternate_tree.prove(lines.leaf)
    standing.assert_public_standing(lines.leaf, scratch, retained, "reviewed", key)


def _assert_ordinary_conflict(lines: Lines) -> None:
    base = lines.read(RECORD)
    lines.on_leaf(_record(base, None, statement="The leaf changes this meaning."))
    lines.on_line(_record(base, None, statement="The source changes this meaning differently."))
    conflict = lines.merge()
    assert conflict["state"] == "sync-resolution-required", conflict
    items = {(one["path"], one["item"]) for one in conflict["resolution"]["crossing"]["conflicts"]}
    assert items == {(RECORD, "statement")}


@pytest.mark.parametrize("case", standing.MATRIX_CASES)
def test_crossing_preserves_the_standing_of_its_selected_bytes(
    tmp_path: Path, case: standing.CrossingCase
) -> None:
    """All fact and write-row pairs share a sync for each byte shape, direction and order."""

    lines = _converted_lines(tmp_path)
    names = ("AAA", "ZZZ") if case.selected_sorts_first else ("ZZZ", "AAA")
    selected = standing.CrossingProfile(case.selected_shape, names[0], case.selected_incoming)
    other = standing.CrossingProfile(case.other_shape, names[1], not case.selected_incoming)
    if case.control == "prior-path":
        _assert_prior_review_path(lines, tmp_path, selected, other)
        return
    if case.control != "matrix":
        _MATRIX_CONTROLS[case.control](lines, tmp_path, selected, other)
        return
    seed = {**lines.read(RECORD), "revision": 1}
    combined_path, combined = standing.matrix_base(0, seed)
    bases = [
        standing.matrix_base(number, seed) for number in range(1, len(standing.PROFILE_PAIRS) + 1)
    ]
    baseline: dict[str, bytes | None] = {
        path: standing.encoded(base) for path, base in [(combined_path, combined), *bases]
    }
    lines.on_line(baseline, "seed the common revisions")
    assert lines.merge()["state"] == "synced"
    _matrix_prepare(selected, lines, tmp_path, seed, selected=True)
    _matrix_prepare(other, lines, tmp_path, seed, selected=False)
    standing.share_conversion((selected, other), lines)
    frozen = {
        path.relative_to(root): payload
        for root in (selected.root(lines), other.root(lines))
        for path in (root / "knowledge/history").glob("*.json")
        for payload in (path.read_bytes(),)
        if json.loads(payload).get("closed") is True
        or path.name == Path(CONVERTED_REVIEWS_PATH).name
    }
    source = selected.root(lines)

    expected = standing.capture_selected(source, tmp_path, bases)
    standing.assert_source_controls(expected, old=case.selected_shape == "old")
    combined_sides = [(side.root(lines) / combined_path).read_bytes() for side in (selected, other)]
    outcome = lines.merge()
    assert outcome["state"] == "synced", outcome
    assert {path: (lines.leaf / path).read_bytes() for path in frozen} == frozen
    after_credit = standing.matrix_credits(lines.leaf, tmp_path)
    assert (lines.leaf / combined_path).read_bytes() not in combined_sides
    combined_writer = lines.read(combined_path)["writer"]
    assert (combined_writer["by"], combined_writer["operation"]) == ("product", CROSSING_OPERATION)
    product_writer = ("product", "product", "none")
    assert standing.matrix_standing(lines.leaf, tmp_path, combined["id"]) == (
        "proposed",
        product_writer,
    )
    assert after_credit[f"{combined['id']}@2"] == (product_writer, False)

    standing.assert_retained(lines.leaf, tmp_path, expected, after_credit)
    _assert_ordinary_conflict(lines)


def test_unconverted_line_syncing_against_state_converted_main_keeps_conversion(
    tmp_path: Path,
) -> None:
    """An unconverted line merging main converted by plan_state_conversion keeps every conversion."""

    lines = _converted_lines(tmp_path)
    base_record = lines.read(RECORD)
    assert base_record.get("status") in LEGACY_STATE_WORDS
    assert "writer" not in base_record

    leaf_writer = _stamp(lines, "seat-leaf", "2026-01-02T10:00:00+01:00")
    lines.on_leaf(
        _record(
            base_record,
            leaf_writer,
            statement="Leaf changed this record.",
            revision=base_record["revision"] + 1,
        )
    )

    main_files = {
        path.relative_to(lines.line).as_posix(): path.read_bytes()
        for path in (lines.line / "knowledge").rglob("*.json")
    }
    plan = plan_state_conversion(main_files, TaskOwners(), at="2026-11-26T10:00:00+01:00")
    unchanged_records = [
        path for path in sorted(plan.changes) if is_record_path(path) and path != RECORD
    ]
    assert unchanged_records, "fixture main must convert records that the leaf did not change"
    lines.on_line(dict(plan.changes), "convert main states")

    merged = lines.merge()

    assert merged["state"] == "synced", merged
    for path in unchanged_records:
        assert (lines.leaf / path).read_bytes() == plan.changes[path]
        stored = lines.read(path)
        assert stored.get("status") not in LEGACY_STATE_WORDS
        assert "writer" in stored
    leaf_changed = lines.read(RECORD)
    assert leaf_changed["statement"] == "Leaf changed this record."
    assert leaf_changed["writer"] == leaf_writer
