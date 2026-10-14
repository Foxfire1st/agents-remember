"""MIK-R24 rules 7 and 8: the converted base and the crossing sync.

The item rules are exercised on the pure merge; the whole crossing -- markers first, conversion of
every unconverted side, structural merge, validation at commit -- runs through the real managed
sync transaction on real Git repositories.
"""

from __future__ import annotations

import gzip
import json
import shutil
import time
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from unittest import mock

import apsw
import pytest
from agents_remember.application.knowledge_worklist import base_cache
from agents_remember.application.knowledge_writer import (
    Owner,
    WriteReport,
    WriteRequest,
    base_side,
    write_knowledge,
)
from agents_remember.kernel.memory_attribution import render_memory_content_message
from agents_remember.memory.conversion import base as base_module
from agents_remember.memory.conversion.base import GitBaseConverter
from agents_remember.memory.conversion.code_objects import CodeObjects
from agents_remember.memory.conversion.convert import MemoryInput, convert_memory
from agents_remember.memory.conversion.crossing import (
    _ABSENT,
    ReferencePairing,
    _assign_numbers,
    _pair_with_base,
    merge_item,
    merge_trees,
)
from agents_remember.memory.conversion.crossing_sync import (
    MARKER_ROW_REASON,
    CrossingError,
    CrossingPlan,
    HistoryOwner,
    cross,
    marker_rows,
    with_markers,
)
from agents_remember.memory.conversion.inputs import memory_from_directory, write_changed
from agents_remember.memory_quality.knowledge_validator.commit_route import (
    GitKnowledgeValidation,
)
from agents_remember.memory_quality.knowledge_validator.trees import CodePathSet, KnowledgeTree
from agents_remember.memory_quality.knowledge_validator.validator import validate_tree
from agents_remember.models.knowledge_files.canonical import canonical_text
from agents_remember.models.knowledge_files.history import HistoryFile
from agents_remember.models.knowledge_files.ids import derived_record_id
from agents_remember.worktrees.knowledge_crossing import (
    apply_crossing,
    close_crossing_history,
    crossing_summary,
)
from agents_remember.worktrees.services import CrossingPlanView, bind_worktree_services
from agents_remember.worktrees.services import worktree_services as bound_worktree_services
from agents_remember.worktrees.sync_transaction_state import sync_operation_path
from knowledge_conversion_test_support import (
    APP,
    APP_CARD,
    APP_SIDECAR,
    APP_SOURCE,
    OTHER_CARD,
    code_repository,
    git,
    memory_repository,
    other_card,
)
from test_worktree_sync import SyncFixture, commit_file


def validate_plan(
    plan: CrossingPlan,
    bases: tuple[Mapping[str, bytes], ...],
    code_paths: frozenset[str],
    label: str,
) -> list[str]:
    """Rule 8 step 5 over a plan: its refusing violations against the converted sides.

    Production enforces step 5 at the merge commit (``memory_commit_refusal``); this runs the same
    validator over the pure plan. The bases are the merge's sides, so the route is a sync merge
    (MIK-R53 rule 10): ``merge_reports`` findings report instead of refusing.
    """

    files = {path: data for path, data in plan.files.items() if data is not None}
    report = validate_tree(
        KnowledgeTree(label=label, files=files),
        bases=tuple(KnowledgeTree(label=f"side {i}", files=base) for i, base in enumerate(bases)),
        code=CodePathSet(label="paired code", paths=code_paths),
    )
    return [violation.render() for violation in report.violations if not violation.report_only]


def _anchor(blob: str, content: str, start: int = 4) -> dict:
    return {
        "locator": {"kind": "line_range", "start": start, "end": start + 1},
        "blob": blob * 40,
        "content": "sha256:" + content * 64,
    }


def _reference(note: str, anchor: dict) -> dict:
    return {"note": note, "targets": [{"kind": "code", "anchor": anchor}]}


def test_items_merge_by_their_mechanical_and_authored_fields() -> None:
    base = _reference("Adds.", _anchor("a", "1"))
    moved = _reference("Adds.", _anchor("b", "1", start=9))  # blob and line numbers only
    rehashed = _reference("Adds.", _anchor("c", "2"))
    reworded = _reference("Adds one.", _anchor("a", "1"))
    retold = _reference("Adds two.", _anchor("a", "1"))

    assert merge_item(base, base, moved) == (moved, None)  # only one side changed it
    assert merge_item(base, reworded, moved) == (reworded, None)  # authored beats mechanical
    assert merge_item(base, moved, reworded) == (reworded, None)
    assert merge_item(base, moved, rehashed) == (rehashed, None)  # both mechanical: incoming
    assert merge_item(base, _ABSENT, moved) == (_ABSENT, None)  # deleted vs mechanical: deleted
    kept, reason = merge_item(base, _ABSENT, reworded)
    assert kept == reworded and reason == "deleted on one side and changed on the other"
    assert merge_item(base, reworded, retold)[1] == (
        "authored fields changed differently on both sides"
    )
    assert merge_item(base, reworded, reworded) == (reworded, None)


def _sidecar(references: dict) -> bytes:
    return canonical_text(
        {
            "schema": "ar-onboarding-file/v1",
            "path": APP,
            "references": references,
            "realizes": [],
        }
    ).encode("utf-8")


def test_a_marker_incomplete_card_merges_without_alignment_exactly_as_today(
    tmp_path: Path,
) -> None:
    """MIK-R66 rule 7: own would be renumbered but cites no [1], so no alignment happens."""

    git(tmp_path, "init", "-q")
    card = "onboarding/src/pkg/app.py.md"
    sidecar = "onboarding/src/pkg/app.py.json"
    one = _reference("Adds.", _anchor("a", "1"))
    base = {card: b"# app\n\nA.\n\nB.\n\nC.\n", sidecar: _sidecar({"1": one})}
    ours = {
        card: b"# app\n\nA, see [2].\n\nB.\n\nC.\n",
        sidecar: _sidecar({"1": one, "2": _reference("Ours.", _anchor("d", "4"))}),
    }
    theirs = {
        card: b"# app\n\nA.\n\nB.\n\nC, see [2].\n",
        sidecar: _sidecar(
            {
                "1": _reference("Adds.", _anchor("e", "5")),
                "2": _reference("Theirs.", _anchor("f", "6")),
            }
        ),
    }

    merged = merge_trees(base, ours, theirs, repository=tmp_path)

    assert merged.files[card] == b"# app\n\nA, see [2].\n\nB.\n\nC, see [2].\n"
    assert [(one.path, one.item) for one in merged.conflicts] == [(sidecar, "references.2")]
    assert "renumber one side" in merged.conflicts[0].reason
    assert merged.alignment.aligned_cards == 0
    assert merged.alignment.skipped_cards == [
        {
            "path": sidecar,
            "reason": "the own side would be renumbered but has a reference without a marker ([1])",
        }
    ]
    reference_one = json.loads(merged.files[sidecar] or b"{}")["references"]["1"]
    assert reference_one["targets"][0]["anchor"]["blob"] == "e" * 40  # mechanical-only: incoming


def test_a_reference_number_both_sides_added_aligns_when_the_card_is_complete(
    tmp_path: Path,
) -> None:
    """MIK-R66: the same new number for two different references is aligned, not a conflict."""

    git(tmp_path, "init", "-q")
    card = "onboarding/src/pkg/app.py.md"
    sidecar = "onboarding/src/pkg/app.py.json"
    one = _reference("Adds.", _anchor("a", "1"))
    base = {card: b"# app\n\nA [1].\n\nB.\n\nC.\n", sidecar: _sidecar({"1": one})}
    ours = {
        card: b"# app\n\nA [1], see [2].\n\nB.\n\nC.\n",
        sidecar: _sidecar({"1": one, "2": _reference("Ours.", _anchor("d", "4"))}),
    }
    theirs = {
        card: b"# app\n\nA [1].\n\nB.\n\nC, see [2].\n",
        sidecar: _sidecar(
            {
                "1": _reference("Adds.", _anchor("e", "5")),
                "2": _reference("Theirs.", _anchor("f", "6")),
            }
        ),
    }

    merged = merge_trees(base, ours, theirs, repository=tmp_path)

    assert merged.conflicts == []
    assert merged.alignment.aligned_cards == 1
    assert merged.alignment.renumbered_references == 1  # only own [2] moves to [3]
    assert merged.files[card] == b"# app\n\nA [1], see [3].\n\nB.\n\nC, see [2].\n"
    references = json.loads(merged.files[sidecar] or b"{}")["references"]
    assert sorted(references) == ["1", "2", "3"]
    assert references["3"]["note"] == "Ours."
    assert references["2"]["note"] == "Theirs."
    reference_one = references["1"]
    assert reference_one["targets"][0]["anchor"]["blob"] == "e" * 40  # mechanical-only: incoming


def _converted(memory: Path, code_root: Path, commit: str) -> MemoryInput:
    outcome = convert_memory(
        memory_from_directory(memory), CodeObjects(code_root), paired_commit=commit
    )
    return MemoryInput(label="converted", files=outcome.files, database=None)


def test_a_crossing_moves_the_leaf_markers_converts_each_side_and_merges(tmp_path: Path) -> None:
    code = code_repository(tmp_path / "code")
    base_root = tmp_path / "base"
    memory_repository(base_root, code)
    base = memory_from_directory(base_root)
    own_files = dict(base.files)
    own_files[OTHER_CARD] = (
        other_card(code.first) + "- 2026-09-03 — L9 review. No content impact: comments only.\n"
    ).encode()
    own_files[APP_CARD] = base.files[APP_CARD].replace(b"Adds and doubles", b"Adds, then doubles")
    own = MemoryInput(label="own", files=own_files, database=base.database)
    incoming = _converted(base_root, code.root, code.head)
    objects = CodeObjects(code.root)

    plan = cross(
        (base, own, incoming),
        objects,
        own_paired_commit=code.head,
        repository=base_root,
        owner=HistoryOwner(kind="leaf", id="260101-FIX-L9"),
    )

    assert plan.conflicts == []
    history = json.loads(plan.files["knowledge/history/260101-FIX-L9.json"] or b"{}")
    assert [(row["subject"], row["disposition"]) for row in history["rows"]] == [
        ("onboarding:src/pkg/other.py", "no_impact")
    ]
    assert history["rows"][0]["markers"] == [
        "- 2026-09-03 — L9 review. No content impact: comments only."
    ]
    assert history["rows"][0]["reason"] == MARKER_ROW_REASON
    assert plan.report["markersNotMoved"] == []
    # A marker whose subject already has a row is reported, never dropped silently.
    held = MemoryInput(label="own", files=dict(plan.files), database=None)  # type: ignore[arg-type]
    again = [{**history["rows"][0], "id": "ROW-AAAAAA", "markers": ["No content impact: again."]}]
    kept, not_moved = with_markers(held, HistoryOwner(kind="leaf", id="260101-FIX-L9"), again)
    assert not_moved == [
        {"subject": "onboarding:src/pkg/other.py", "markers": ["No content impact: again."]}
    ]
    assert (
        kept["knowledge/history/260101-FIX-L9.json"]
        == plan.files["knowledge/history/260101-FIX-L9.json"]
    )
    assert b"Adds, then doubles" in (plan.files[APP_CARD] or b"")
    assert plan.files["knowledge/layout.json"] == incoming.files["knowledge/layout.json"]
    assert plan.report["converted"] == ["base", "own"]
    assert plan.report["cards"]["fromOwn"] == 1  # the other card converts to the base bytes
    code_paths = frozenset(git(code.root, "ls-tree", "-r", "--name-only", code.head).split("\n"))
    # Step 5 runs over converted sides, as production's base converter supplies them (MIK-R24
    # rule 7): the raw own side is unconverted and would trip R22.6-base-converted.
    converted_own = convert_memory(own, objects, paired_commit=code.head)
    assert validate_plan(plan, (converted_own.files, incoming.files), code_paths, "merged") == []
    with pytest.raises(CrossingError, match="no tree of this merge is converted"):
        cross(
            (base, own, own), objects, own_paired_commit=code.head, repository=base_root, owner=None
        )


def test_many_long_markers_move_into_one_valid_history_row() -> None:
    """A card that gained more marker text than one text value holds still yields a valid row."""

    lines = [
        f"- 2026-09-{day:02d} — No content impact: {'mechanical repair ' * 20}".rstrip()
        for day in range(1, 29)
    ]
    long_line = "- 2026-09-29 — No content impact: " + "x" * 45_000
    history = "\n".join([*lines, long_line])
    card = "onboarding/src/pkg/app.py.md"
    base = MemoryInput(
        label="base", files={card: b"# app\n\n## Update History\n\n- created\n"}, database=None
    )
    own = MemoryInput(
        label="own",
        files={card: f"# app\n\n## Update History\n\n- created\n{history}\n".encode()},
        database=None,
    )

    rows = marker_rows(base, own, code_blobs={"src/pkg/app.py": "a" * 40})

    assert sum(len(one) for one in rows[0]["markers"]) > 20_000
    assert rows[0]["markers"][: len(lines)] == lines
    assert "".join(rows[0]["markers"][len(lines) :]) == long_line  # split, nothing lost
    assert len(rows[0]["markers"]) == len(lines) + 3  # 45,034 characters -> 3 pieces
    document = {"schema": "ar-history/v1", "leaf": "260101-FIX-L9", "closed": False, "rows": rows}
    HistoryFile.model_validate(document)  # every value within the text limit


def test_a_master_line_record_conflict_opens_a_crossing_history_file_closed_at_commit(
    tmp_path: Path,
) -> None:
    code = code_repository(tmp_path / "code")
    base_root = tmp_path / "base"
    memory_repository(base_root, code)
    base = memory_from_directory(base_root)
    own_database = tmp_path / "own.sqlite"
    shutil.copyfile(base_root / "knowledge.sqlite", own_database)
    connection = apsw.Connection(str(own_database))
    connection.execute("UPDATE invariant_revision SET statement = 'Own.' WHERE revision_id = 'r1b'")
    connection.close()
    own = MemoryInput(label="own", files=dict(base.files), database=own_database)
    incoming_files = dict(_converted(base_root, code.root, code.head).files)
    one = derived_record_id("invariant", "inv-one")
    record = f"knowledge/invariants/{one}-FIX-I-1.json"
    incoming_files[record] = incoming_files[record].replace(b"Alpha adds one.", b"Incoming.")
    incoming = MemoryInput(label="incoming", files=incoming_files, database=None)

    plan = cross(
        (base, own, incoming),
        CodeObjects(code.root),
        own_paired_commit=code.head,
        repository=base_root,
        owner=HistoryOwner(kind="master", id="260101-FIX"),
    )

    assert [(one.path, one.item) for one in plan.conflicts] == [(record, "statement")]
    crossing = "knowledge/history/260101-FIX-crossing-1.json"
    assert plan.report["recordConflictHistoryOwner"] == "260101-FIX-crossing-1"
    assert json.loads(plan.files[crossing] or b"{}") == {
        "schema": "ar-history/v1",
        "crossing": "260101-FIX-crossing-1",
        "closed": False,
        "rows": [],
    }

    # The sync closes the file it opened in the merge commit itself.
    parent = git(base_root, "rev-parse", "HEAD")
    (base_root / crossing).parent.mkdir(parents=True, exist_ok=True)
    (base_root / crossing).write_bytes(plan.files[crossing] or b"")
    git(base_root, "add", crossing)
    assert close_crossing_history(base_root, (parent, parent)) == (crossing,)
    staged = json.loads(git(base_root, "show", f":{crossing}"))
    assert staged["closed"] is True


@pytest.mark.parametrize("own_converted", [True, False], ids=["own-converted", "own-unconverted"])
def test_a_record_both_sides_changed_is_resolved_at_one_more_than_the_higher_side(
    tmp_path: Path, own_converted: bool
) -> None:
    """MIK-R24 rule 8 step 4 (L37 ruling 03:36:39): the crossing owner resolves the conflicted
    record through the writer, whose ``revision`` becomes one more than the higher side's.

    The incoming side changed the same statement and holds the higher revision; the base is the
    unconverted fork point. The own side is either a converted master line, or (ONT's case,
    ruling 03:56:27) an unconverted one: the writer then reads its converted base (MIK-R24 rule 7),
    so the carried entries are not refused for want of a base.
    """

    code = code_repository(tmp_path / "code")
    base_root = tmp_path / "base"
    memory_repository(base_root, code)
    fork = tmp_path / "fork.sqlite"
    shutil.copyfile(base_root / "knowledge.sqlite", fork)
    base = MemoryInput(
        label="base", files=dict(memory_from_directory(base_root).files), database=fork
    )
    connection = apsw.Connection(str(base_root / "knowledge.sqlite"))
    connection.execute("UPDATE invariant_revision SET statement = 'Own.' WHERE revision_id = 'r1b'")
    connection.close()
    own_conversion = convert_memory(
        memory_from_directory(base_root), CodeObjects(code.root), paired_commit=code.head
    )
    if own_converted:
        write_changed(base_root, own_conversion.changed)
        own = MemoryInput(label="own", files=dict(own_conversion.files), database=None)
    else:
        own = MemoryInput(
            label="own",
            files=dict(memory_from_directory(base_root).files),
            database=base_root / "knowledge.sqlite",
        )
    git(base_root, "add", "-A")
    git(base_root, "commit", "-q", "-m", render_memory_content_message("own", code.head))
    incoming_files = dict(own_conversion.files)
    one = derived_record_id("invariant", "inv-one")
    record = f"knowledge/invariants/{one}-FIX-I-1.json"
    incoming_record = json.loads(incoming_files[record])
    incoming_record.update(statement="Incoming.", revision=incoming_record["revision"] + 1)
    incoming_files[record] = canonical_text(incoming_record).encode("utf-8")
    plan = cross(
        (base, own, MemoryInput(label="incoming", files=incoming_files, database=None)),
        CodeObjects(code.root),
        own_paired_commit=code.head,
        repository=base_root,
        owner=HistoryOwner(kind="master", id="260101-FIX"),
    )
    assert [(one.path, one.item) for one in plan.conflicts] == [(record, "statement")]
    view = CrossingPlanView(
        files=plan.files,
        conflicts=tuple((one.path, one.item, one.reason) for one in plan.conflicts),
        conflict_versions=plan.conflict_versions,
        report=plan.report,
    )
    assert apply_crossing(base_root, view) == (record,)  # left unmerged: stages 1-3
    sides = [json.loads(git(base_root, "show", f":{stage}:{record}"))["revision"] for stage in "23"]
    assert sides[1] == sides[0] + 1  # the incoming side is the higher one

    crossing = "260101-FIX-crossing-1"
    owner = Owner(task="260101-FIX", kind="crossing", id=crossing)
    resolution = {
        "key": "R-1",
        "kind": "invariant",
        "id": one,
        "fields": {"statement": "Both: adds one."},
    }
    row = {
        "subject": one,
        "disposition": "changed",
        "effect": "clarify",
        "reason": "Both sides reworded it; the resolution keeps both points.",
        "covers": [],
    }

    def write(document: dict[str, object]) -> WriteReport:
        return write_knowledge(
            WriteRequest(
                memory_root=base_root,
                code_root=code.root,
                owner=owner,
                handoff_path="crossing-rows.json",
                document=document,
                commit=True,
                coordination_root=tmp_path / "coordination",
            )
        )

    new_record = {**resolution, "key": "R-2", "id": None}
    refused = write({"records": [{k: v for k, v in new_record.items() if v is not None}]})
    assert refused.state == "refused" and "no entry, ruling or new record" in refused.render()

    written = write({"records": [resolution], "history": [row]})
    assert written.state == "written", written.render()
    resolved = json.loads((base_root / record).read_text())
    assert (resolved["statement"], resolved["revision"]) == ("Both: adds one.", max(sides) + 1)
    history = json.loads((base_root / f"knowledge/history/{crossing}.json").read_text())
    assert [(one["subject"], one["revision"]) for one in history["rows"]] == [(one, max(sides) + 1)]


def test_the_writer_compares_an_unconverted_head_through_its_converted_base(
    tmp_path: Path,
) -> None:
    """L37 ruling 03:56:27: ``HEAD`` unconverted, the working tree converted (the converting leaf's
    curation before its closeout commits the conversion): the writer's base is ``HEAD``'s
    conversion (MIK-R24 rule 7), cached as the worklist and the gate cache it."""

    code = code_repository(tmp_path / "code")
    memory = tmp_path / "memory"
    memory_repository(memory, code)
    write_changed(
        memory,
        convert_memory(
            memory_from_directory(memory), CodeObjects(code.root), paired_commit=code.head
        ).changed,
    )
    one = derived_record_id("invariant", "inv-one")
    record = memory / f"knowledge/invariants/{one}-FIX-I-1.json"
    exported = json.loads(record.read_text())
    entry = next(
        one_entry
        for one_entry in json.loads((memory / APP_SIDECAR).read_text())["realizes"]
        if one_entry["invariant"] == one and one_entry["anchor"]["locator"]["kind"] == "symbol"
    )
    coordination = tmp_path / "coordination"

    def change(statement: str, *, commit: bool = True, code_root: Path = code.root) -> WriteReport:
        return write_knowledge(
            WriteRequest(
                memory_root=memory,
                code_root=code_root,
                owner=Owner(task="260101-FIX", kind="leaf", id="260101-FIX-L7"),
                handoff_path="handoff.json",
                document={
                    "records": [
                        {
                            "key": "R",
                            "kind": "invariant",
                            "id": one,
                            "fields": {"statement": statement},
                        }
                    ],
                    "history": [
                        {
                            "subject": one,
                            "disposition": "changed",
                            "effect": "clarify",
                            "reason": "The statement is sharper.",
                            "covers": [entry["id"]],
                        }
                    ],
                },
                commit=commit,
                coordination_root=coordination,
            )
        )

    unbuildable = change("Alpha adds exactly one.", code_root=tmp_path / "no-code")
    assert unbuildable.state == "refused"  # never compared against nothing
    assert "converted base (MIK-R24 rule 7)" in unbuildable.render()
    written = change("Alpha adds exactly one.")
    assert written.state == "written", (
        written.render()
    )  # no carried anchor refused for want of a base
    assert json.loads(record.read_text())["revision"] == exported["revision"] + 1
    history = json.loads((memory / "knowledge/history/260101-FIX-L7.json").read_text())
    (row,) = history["rows"]
    assert row["revision"] == exported["revision"] + 1
    assert row["covers"][0]["before"] == {**entry["anchor"], "path": APP}  # read from the base
    cached = list((coordination / "runtime" / "knowledge-worklist-bases").glob("*.json.gz"))
    assert len(cached) == 1  # converted once; the next operation reads the cache
    never = mock.Mock(side_effect=AssertionError("converted again"))
    with mock.patch.object(base_cache, "converted_base", never):
        assert change("Alpha adds one, exactly.", commit=False).state == "planned"

    git(memory, "add", "-A")
    git(memory, "commit", "-q", "-m", render_memory_content_message("converted", code.head))
    with mock.patch.object(
        base_side, "converted_base_files", never
    ):  # a converted HEAD is the base
        again = change("Alpha adds one, always.")
    assert again.state == "written", again.render()
    assert json.loads(record.read_text())["revision"] == exported["revision"] + 2


def test_a_trailerless_head_converts_at_the_gates_code_base_and_an_unreadable_one_refuses(
    tmp_path: Path,
) -> None:
    """L37 review R1 (F10, F3 X14, F6). A ``HEAD`` without a ``Code-Commit`` trailer is converted at
    the request's code base B -- the gate's and the worklist's fallback -- so the three share one
    cache key. A code root with no commits, or a ``HEAD`` whose tree Git cannot read, is a named
    refusal, never a comparison against nothing."""

    code = code_repository(tmp_path / "code")
    memory = tmp_path / "memory"
    head = memory_repository(memory, code)  # committed as "legacy memory": no trailer
    write_changed(
        memory,
        convert_memory(
            memory_from_directory(memory), CodeObjects(code.root), paired_commit=code.head
        ).changed,
    )
    coordination = tmp_path / "coordination"
    cache = coordination / "runtime" / "knowledge-worklist-bases"

    def plan(code_root: Path, code_base: str | None) -> WriteReport:
        return write_knowledge(
            WriteRequest(
                memory_root=memory,
                code_root=code_root,
                owner=Owner(task="260101-FIX", kind="leaf", id="260101-FIX-L7"),
                handoff_path="handoff.json",
                document={"history": []},
                coordination_root=coordination,
                code_base=code_base,
            )
        )

    unborn = tmp_path / "unborn-code"
    unborn.mkdir()
    git(unborn, "init", "-q")
    refused = plan(unborn, None)  # X14: no code commit to convert the base at
    assert refused.state == "refused"
    assert "converted base (MIK-R24 rule 7)" in refused.render()
    assert "'HEAD' is unknown" in refused.render()

    planned = plan(code.root, code.first)  # B, not the code tree's HEAD
    assert planned.state == "planned", planned.render()
    (cached,) = cache.glob("*.json.gz")
    key = json.loads(gzip.decompress(cached.read_bytes()))["key"]
    assert (key[0], key[2]) == (head, code.first) and code.first != code.head
    never = mock.Mock(side_effect=AssertionError("converted again"))
    with mock.patch.object(base_cache, "converted_base", never):  # the gate's own call: a hit
        base_cache.converted_base_files(
            memory, head, code=(code.root, code.first), version=key[1], cache_directory=cache
        )

    blob = git(memory, "rev-parse", f"HEAD:{APP_CARD}")
    (memory / ".git" / "objects" / blob[:2] / blob[2:]).unlink()  # F6: HEAD's tree unreadable
    unreadable = plan(code.root, code.first)
    assert unreadable.state == "refused"
    assert "the memory worktree's HEAD, cannot be read" in unreadable.render()


def test_the_commit_route_validates_against_the_conversion_of_an_unconverted_base(
    tmp_path: Path,
) -> None:
    code = code_repository(tmp_path / "code")
    memory = tmp_path / "memory"
    memory_repository(memory, code)
    # The legacy commit names its paired code commit; rule 7 converts it at that tree.
    git(
        memory, "commit", "-q", "--amend", "-m", render_memory_content_message("legacy", code.first)
    )
    unconverted = git(memory, "rev-parse", "HEAD")
    write_changed(
        memory,
        convert_memory(
            memory_from_directory(memory), CodeObjects(code.root), paired_commit=code.head
        ).changed,
    )
    git(memory, "add", "-A")
    git(memory, "commit", "-q", "-m", "convert")
    converted_tree = git(memory, "rev-parse", "HEAD^{tree}")
    arguments = {
        "memory_repository": memory,
        "candidate_tree": converted_tree,
        "bases": [unconverted],
        "code_repository": code.root,
        "code_commit": code.head,
    }

    refused = GitKnowledgeValidation().refusal(**arguments)
    assert refused is not None and "R22.6-base-converted" in refused
    seen: list[str] = []
    real = base_module.converted_base

    def spy(*args: object, **kwargs: object) -> object:
        seen.append(str(kwargs["code_commit"]))
        return real(*args, **kwargs)  # type: ignore[arg-type]

    base_module.converted_base = spy  # type: ignore[assignment]
    try:
        validation = GitKnowledgeValidation(base_converter=GitBaseConverter())
        converted_refusal = validation.refusal(**arguments)
        # The base converter ran, so R22.6-base-converted is gone; the fixture's stale anchor
        # is refused at the commit route (MIK-R53 rule 10: merge_reports refuses everywhere
        # but a sync merge).
        assert converted_refusal is not None and "R22.6-base-converted" not in converted_refusal
        assert "carried-stale" in converted_refusal
    finally:
        base_module.converted_base = real
    assert seen == [code.first]  # the base's own Code-Commit, not the candidate's code.head


def _crossing_fixture(tmp_path: Path) -> tuple[SyncFixture, Path, dict[str, str], str]:
    """A leaf whose memory is unconverted, refused once from its line; returns the verified commit."""

    fixture = SyncFixture(tmp_path, converted=False)
    worktree = fixture.contract.memory_worktree
    assert worktree is not None
    commit_file(fixture.code_repo, APP, APP_SOURCE.rstrip("\n"))
    verified = git(fixture.code_repo, "rev-parse", "main")
    metadata = f"| Field | Value |\n| --- | --- |\n| lastVerifiedCommitHash | `{verified}` |\n\n"
    cards = {
        APP_CARD: (
            f"# {APP}\n\n{metadata}## Purpose\n\nAdds.\n\n## Repo-Internal References\n\n"
            "| Finding | Anchor | Source |\n| --- | --- | --- |\n"
            f"| alpha adds. | `alpha` | {APP}:4-5 |\n\n## Update History\n\n- created\n"
        ),
        OTHER_CARD: "# other\n\n## Update History\n\n- created\n",
    }
    for relative, text in cards.items():
        (fixture.memory_repo / relative).parent.mkdir(parents=True, exist_ok=True)
        (fixture.memory_repo / relative).write_text(text, encoding="utf-8")
    git(fixture.memory_repo, "add", "-A")
    git(fixture.memory_repo, "commit", "-q", "-m", render_memory_content_message("cards", verified))
    line_before = git(fixture.memory_repo, "rev-parse", "main")
    leaf_before = git(worktree, "rev-parse", "HEAD")
    first = fixture.sync(memory_sync_choice="merge-memory").payload
    # MIK-R46 rule 3: a merge whose base, own and incoming sides are all unconverted refuses
    # with legacy-format before anything moves; the plain Git merge of memory is gone.
    assert first["state"] == "sync-unconverted-memory-locked", first
    assert "code legacy-format" in str(first["detail"]), first
    assert "knowledge-convert" in str(first["detail"]), first
    assert git(fixture.memory_repo, "rev-parse", "main") == line_before
    assert git(worktree, "rev-parse", "HEAD") == leaf_before
    assert not sync_operation_path(fixture.contract.worktree_group).exists()
    # Setup for the crossing below (not product behavior): carry the line's cards into the
    # still-unconverted leaf worktree, as the removed plain merge once did as a side effect.
    git(worktree, "merge", "-q", "main")
    return fixture, worktree, cards, verified


def _convert_line(
    fixture: SyncFixture, verified: str, edit: str | None = None, finding: str | None = None
) -> str:
    """Convert the official line, optionally editing the app card's purpose and first finding."""

    outcome = convert_memory(
        memory_from_directory(fixture.memory_repo),
        CodeObjects(fixture.code_repo),
        paired_commit=verified,
    )
    write_changed(fixture.memory_repo, outcome.changed)
    if edit is not None:
        card = fixture.memory_repo / APP_CARD
        card.write_text(card.read_text(encoding="utf-8").replace("Adds.", edit), encoding="utf-8")
    if finding is not None:
        card = fixture.memory_repo / APP_CARD
        card.write_text(
            card.read_text(encoding="utf-8").replace("alpha adds.", finding), encoding="utf-8"
        )
        sidecar = fixture.memory_repo / APP_SIDECAR
        document = json.loads(sidecar.read_text(encoding="utf-8"))
        document["references"]["1"]["note"] = finding
        sidecar.write_text(canonical_text(document), encoding="utf-8")
    code_tip = fixture.move_official_code()
    git(fixture.memory_repo, "add", "-A")
    git(
        fixture.memory_repo,
        "commit",
        "-q",
        "-m",
        render_memory_content_message("convert", code_tip),
    )
    return git(fixture.memory_repo, "rev-parse", "HEAD")


def test_the_managed_sync_crosses_an_unconverted_leaf_into_a_converted_line(
    tmp_path: Path, worktree_services: None
) -> None:
    fixture, worktree, cards, verified = _crossing_fixture(tmp_path)
    (worktree / OTHER_CARD).write_text(
        cards[OTHER_CARD] + "- L9: No content impact: renamed a local.\n", encoding="utf-8"
    )
    git(worktree, "commit", "-q", "-am", "leaf attests no impact")
    leaf_head = git(worktree, "rev-parse", "HEAD")
    line_head = _convert_line(fixture, verified)

    result = fixture.sync(memory_sync_choice="merge-memory")

    assert result.payload["state"] == "synced", result.payload
    assert git(worktree, "rev-list", "--parents", "-n", "1", "HEAD").split()[1:] == [
        leaf_head,
        line_head,
    ]
    assert (worktree / "knowledge/layout.json").is_file()
    assert (worktree / APP_CARD).read_bytes() == (fixture.memory_repo / APP_CARD).read_bytes()
    history = json.loads(
        (worktree / f"knowledge/history/{fixture.contract.task_id}.json").read_text()
    )
    assert history["leaf"] == fixture.contract.task_id
    assert history["rows"][0]["subject"] == "onboarding:src/pkg/other.py"
    assert "## Update History" not in (worktree / OTHER_CARD).read_text(encoding="utf-8")
    journal = json.loads(sync_operation_path(fixture.contract.worktree_group).read_text())
    report = Path(journal["memory"]["crossingReport"])
    assert report.parent == fixture.contract.worktree_group / "reports" and report.is_file()
    # MIK-R08 rule 8: the completed sync recomputed the now-converted leaf's worklist at its new
    # base pair and persisted it beside the contract.
    worklist = result.payload["knowledgeWorklist"]
    assert isinstance(worklist, dict) and worklist["state"] == "complete", worklist
    persisted = fixture.contract.contract_path.parent / "knowledge-worklist.json"
    assert worklist["path"] == persisted.as_posix()
    document = json.loads(persisted.read_text(encoding="utf-8"))
    assert document["pairing"]["base"]["commit"] == git(fixture.code_repo, "rev-parse", "main")
    assert document["pairing"]["memoryBase"]["commit"] == line_head


def _cancel_and_cross_again(fixture: SyncFixture, worktree: Path, leaf_head: str) -> None:
    """L37 (P2 task 1): a conflicted crossing cancels cleanly -- the leaf's head, no merge, no
    converted file left behind -- and the same sync then crosses again."""

    cancelled = fixture.sync(resolution_action="cancel")
    assert cancelled.payload["state"] == "sync-cancelled", cancelled.payload
    assert git(worktree, "rev-parse", "HEAD") == leaf_head
    with pytest.raises(AssertionError):
        git(worktree, "rev-parse", "-q", "--verify", "MERGE_HEAD")
    assert git(worktree, "status", "--porcelain") == ""
    assert not (worktree / "knowledge/layout.json").exists()
    again = fixture.sync(memory_sync_choice="merge-memory")
    assert again.payload["state"] == "sync-resolution-required", again.payload


def test_a_crossing_leaves_overlapping_edits_to_the_curator_and_a_failed_step_changes_nothing(
    tmp_path: Path, worktree_services: None
) -> None:
    fixture, worktree, _, verified = _crossing_fixture(tmp_path)
    card = worktree / APP_CARD
    card.write_text(
        card.read_text(encoding="utf-8")
        .replace("Adds.", "Adds one.")
        .replace("| alpha adds. |", "| alpha adds one. |"),
        encoding="utf-8",
    )
    git(worktree, "commit", "-q", "-am", "leaf rewords")
    leaf_head = git(worktree, "rev-parse", "HEAD")
    _convert_line(fixture, verified, edit="Adds exactly one.", finding="alpha adds exactly one.")
    bound = bound_worktree_services()

    try:
        bind_worktree_services(replace(bound, knowledge_crossing=None))
        failed = fixture.sync(memory_sync_choice="merge-memory")
    finally:
        bind_worktree_services(bound)
    assert failed.payload["state"] == "sync-git-proof-failed", failed.payload
    assert "crossing sync step 'convert' failed" in str(failed.payload["summary"])
    assert git(worktree, "rev-parse", "HEAD") == leaf_head  # the line is unchanged
    with pytest.raises(AssertionError):
        git(worktree, "rev-parse", "-q", "--verify", "MERGE_HEAD")  # nothing was merged
    fixture.sync(resolution_action="cancel")

    conflicted = fixture.sync(memory_sync_choice="merge-memory")

    # The curator is told which items conflict, with every side's value, in the payload and in a
    # durable report beside the worktree; the conflicted sidecar item is an explicit marker.
    assert conflicted.payload["state"] == "sync-resolution-required", conflicted.payload
    resolution = conflicted.payload["resolution"]
    assert isinstance(resolution, dict)
    crossing = resolution["crossing"]
    assert {(one["path"], one["item"]) for one in crossing["conflicts"]} == {
        (APP_CARD, "(lines)"),
        (APP_SIDECAR, "references.1"),
    }
    assert "crossing sync" in str(conflicted.payload["summary"])
    report = json.loads(Path(crossing["reportPath"]).read_text(encoding="utf-8"))
    item = next(one for one in report["conflicts"] if one["item"] == "references.1")
    assert (item["own"]["note"], item["incoming"]["note"], item["base"]["note"]) == (
        "alpha adds one.",
        "alpha adds exactly one.",
        "alpha adds.",
    )
    sidecar = worktree / APP_SIDECAR
    assert json.loads(sidecar.read_text())["references"]["1"] == {
        "crossing-conflict": {k: v for k, v in item.items() if k != "path"}
    }
    assert "<<<<<<< ours" in card.read_text(encoding="utf-8")

    _cancel_and_cross_again(fixture, worktree, leaf_head)

    # Staging the Markdown fix and the untouched marker is refused at commit: never a silent ours.
    card.write_text(git(worktree, "show", f"MERGE_HEAD:{APP_CARD}") + "\n", encoding="utf-8")
    git(worktree, "add", APP_CARD, APP_SIDECAR)
    refused = fixture.sync(resolution_action="continue")
    assert refused.payload["state"] == "sync-knowledge-validation-refused", refused.payload
    assert APP_SIDECAR in str(refused.payload["summary"])

    sidecar.write_text(git(worktree, "show", f"MERGE_HEAD:{APP_SIDECAR}") + "\n", encoding="utf-8")
    git(worktree, "add", APP_SIDECAR)
    resolved = fixture.sync(resolution_action="continue")
    assert resolved.payload["state"] == "synced", resolved.payload
    assert (worktree / "knowledge/layout.json").is_file()


# MIK-R66: the structural merge aligns reference numbers. Fixture cards below are complete:
# every side's markers name its own references, so rule 7 never fires unless named.


def _aligned_card(note: str, blob: str) -> dict:
    return _reference(note, _anchor(blob, note))


def _converted_sides(
    label_files: tuple[dict[str, bytes], dict[str, bytes], dict[str, bytes]],
) -> tuple[MemoryInput, MemoryInput, MemoryInput]:
    """Three already-converted trees sharing one layout marker version."""

    layout = canonical_text({"schema": "ar-memory-layout/v2", "conversion": "1"}).encode()
    sides = tuple(
        MemoryInput(label=label, files={**files, "knowledge/layout.json": layout}, database=None)
        for label, files in zip(("base", "own", "incoming"), label_files, strict=True)
    )
    base_in, own_in, incoming_in = sides
    return base_in, own_in, incoming_in


def test_fresh_numbers_follow_own_order_then_base_order() -> None:
    """MIK-R66 rules 2 and 3: own groups in own order, then base-only groups."""

    base = {
        "1": _aligned_card("A.", "a"),
        "2": _aligned_card("B.", "b"),
        "3": _aligned_card("C.", "c"),
    }
    own = {"1": _aligned_card("A.", "a"), "4": _aligned_card("X.", "x")}
    incoming = {"3": _aligned_card("C.", "c")}
    base_to_own = _pair_with_base(base, own)
    base_to_incoming = _pair_with_base(base, incoming)
    assert base_to_own == {"1": "1"}  # content only; the same-number test adds nothing
    assert base_to_incoming == {"3": "3"}
    assigned = _assign_numbers(
        base, own, incoming, ReferencePairing(base_to_own, base_to_incoming, {})
    )
    assert assigned is not None
    base_map, own_map = assigned
    assert base_map == {"1": "5", "2": "7", "3": "3"}
    assert own_map == {"1": "5", "4": "6"}
    assert incoming.keys() == {"3"}  # incoming is never renumbered


def test_alignment_pairs_content_and_freshens_only_what_has_no_partner(
    tmp_path: Path,
) -> None:
    """MIK-R66 rules 2a and 3: shifted references take the incoming numbers; X survives fresh."""

    git(tmp_path, "init", "-q")
    card = "onboarding/src/pkg/app.py.md"
    sidecar = "onboarding/src/pkg/app.py.json"
    base = {
        card: b"# app\n\nA [1].\n\nB [2].\n\nC [3].\n",
        sidecar: _sidecar(
            {
                "1": _aligned_card("A.", "a"),
                "2": _aligned_card("B.", "b"),
                "3": _aligned_card("C.", "c"),
            }
        ),
    }
    ours = {
        card: b"# app\n\nA [1].\n\nX [4].\n",
        sidecar: _sidecar({"1": _aligned_card("A.", "a"), "4": _aligned_card("X.", "x")}),
    }
    theirs = {
        card: b"# app\n\nC [3].\n",
        sidecar: _sidecar({"3": _aligned_card("C.", "c")}),
    }

    merged = merge_trees(base, ours, theirs, repository=tmp_path)

    assert merged.alignment.aligned_cards == 1
    assert merged.alignment.renumbered_references == 4
    assert [(one.path, one.item) for one in merged.conflicts] == [(card, "(lines)")]
    references = json.loads(merged.files[sidecar] or b"{}")["references"]
    assert sorted(references) == ["6"]  # A and B deleted on a side, C deleted by own
    assert references["6"]["note"] == "X."
    markdown = (merged.files[card] or b"").decode()
    assert "[5]" in markdown and "[6]" in markdown  # own's markers moved with the map


def test_alignment_pairs_same_numbers_when_content_moved(tmp_path: Path) -> None:
    """MIK-R66 rule 2b: a replaced note under the same number pairs by number, not content."""

    git(tmp_path, "init", "-q")
    card = "onboarding/src/pkg/app.py.md"
    sidecar = "onboarding/src/pkg/app.py.json"
    base = {card: b"# app\n\nA [1].\n", sidecar: _sidecar({"1": _aligned_card("A.", "a")})}
    ours = {card: b"# app\n\nB [1].\n", sidecar: _sidecar({"1": _aligned_card("B.", "b")})}
    theirs = {card: b"# app\n\nA [1].\n", sidecar: _sidecar({"1": _aligned_card("A.", "a")})}

    merged = merge_trees(base, ours, theirs, repository=tmp_path)

    assert merged.conflicts == []
    assert merged.alignment.aligned_cards == 0  # identity maps: nothing moved
    references = json.loads(merged.files[sidecar] or b"{}")["references"]
    assert sorted(references) == ["1"] and references["1"]["note"] == "B."


def test_alignment_pairs_the_singleton_between_paired_neighbours(tmp_path: Path) -> None:
    """MIK-R66 rule 2c: the changed row between two paired neighbours follows them."""

    git(tmp_path, "init", "-q")
    card = "onboarding/src/pkg/app.py.md"
    sidecar = "onboarding/src/pkg/app.py.json"
    base = {
        card: b"# app\n\nA [1].\n\nB [2].\n\nC [5].\n",
        sidecar: _sidecar(
            {
                "1": _aligned_card("A.", "a"),
                "2": _aligned_card("B.", "b"),
                "5": _aligned_card("C.", "c"),
            }
        ),
    }
    ours = {
        card: b"# app\n\nA [1].\n\nB, twice [3].\n\nC [5].\n",
        sidecar: _sidecar(
            {
                "1": _aligned_card("A.", "a"),
                "3": _aligned_card("B, twice.", "b"),
                "5": _aligned_card("C.", "c"),
            }
        ),
    }
    theirs = {
        card: b"# app\n\nA [1].\n\nB [2].\n\nC, thrice [5].\n",
        sidecar: _sidecar(
            {
                "1": _aligned_card("A.", "a"),
                "2": _aligned_card("B.", "b"),
                "5": _aligned_card("C, thrice.", "c"),
            }
        ),
    }

    merged = merge_trees(base, ours, theirs, repository=tmp_path)

    assert merged.conflicts == []
    assert merged.alignment.aligned_cards == 1
    assert merged.alignment.renumbered_references == 1  # only own [3] takes incoming [2]
    references = json.loads(merged.files[sidecar] or b"{}")["references"]
    assert {number: reference["note"] for number, reference in references.items()} == {
        "1": "A.",
        "2": "B, twice.",
        "5": "C, thrice.",
    }
    assert merged.files[card] == b"# app\n\nA [1].\n\nB, twice [2].\n\nC, thrice [5].\n"


def test_pairing_prefers_content_over_the_same_number(tmp_path: Path) -> None:
    """MIK-R66 rule 2 order: test a wins over test b for the same base reference."""

    git(tmp_path, "init", "-q")
    card = "onboarding/src/pkg/app.py.md"
    sidecar = "onboarding/src/pkg/app.py.json"
    base = {card: b"# app\n\nA [1].\n", sidecar: _sidecar({"1": _aligned_card("A.", "a")})}
    ours = {
        card: b"# app\n\nB [1].\n\nA [2].\n",
        sidecar: _sidecar({"1": _aligned_card("B.", "b"), "2": _aligned_card("A.", "a")}),
    }
    theirs = {
        card: b"# app\n\nA [1].\n\nC [3].\n",
        sidecar: _sidecar({"1": _aligned_card("A.", "a"), "3": _aligned_card("C.", "c")}),
    }

    merged = merge_trees(base, ours, theirs, repository=tmp_path)

    assert merged.conflicts == []
    references = json.loads(merged.files[sidecar] or b"{}")["references"]
    assert {number: reference["note"] for number, reference in references.items()} == {
        "1": "A.",
        "3": "C.",
        "4": "B.",
    }
    assert b"B [4]." in (merged.files[card] or b"")


def test_alignment_keeps_a_reference_incoming_deleted_deleted(tmp_path: Path) -> None:
    """MIK-R66 rule 3: a base reference without an incoming partner is numbered, then merged."""

    git(tmp_path, "init", "-q")
    card = "onboarding/src/pkg/app.py.md"
    sidecar = "onboarding/src/pkg/app.py.json"
    base = {
        card: b"# app\n\nA [1].\n\nB [2].\n",
        sidecar: _sidecar({"1": _aligned_card("A.", "a"), "2": _aligned_card("B.", "b")}),
    }
    ours = {
        card: b"# app\n\nA, edited [1].\n\nB [2].\n",
        sidecar: _sidecar({"1": _aligned_card("A, edited.", "a"), "2": _aligned_card("B.", "b")}),
    }
    theirs = {
        card: b"# app\n\nA [1].\n",
        sidecar: _sidecar({"1": _aligned_card("A.", "a")}),
    }

    merged = merge_trees(base, ours, theirs, repository=tmp_path)

    references = json.loads(merged.files[sidecar] or b"{}")["references"]
    assert {number: reference["note"] for number, reference in references.items()} == {
        "1": "A, edited."
    }
    assert merged.alignment.aligned_cards == 1
    assert merged.alignment.renumbered_references == 2  # B takes a fresh number, then merges away
    assert [(one.path, one.item) for one in merged.conflicts] == [(card, "(lines)")]


def test_markers_follow_references_but_code_and_escapes_do_not(tmp_path: Path) -> None:
    """MIK-R66 rule 4: prose markers move with the map; code spans and escapes do not."""

    git(tmp_path, "init", "-q")
    card = "onboarding/src/pkg/app.py.md"
    sidecar = "onboarding/src/pkg/app.py.json"
    tail = b"\n\n`[7]` and \\[6].\n"
    base = {card: b"# app\n\nA [1]." + tail, sidecar: _sidecar({"1": _aligned_card("A.", "a")})}
    ours = {
        card: b"# app\n\nA [1].\n\n- X [2]." + tail,
        sidecar: _sidecar({"1": _aligned_card("A.", "a"), "2": _aligned_card("X.", "x")}),
    }
    theirs = {
        card: b"# app\n\nA, edited [1]." + tail,
        sidecar: _sidecar({"1": _aligned_card("A, edited.", "a")}),
    }

    merged = merge_trees(base, ours, theirs, repository=tmp_path)

    assert merged.conflicts == []
    assert merged.files[card] == b"# app\n\nA, edited [1].\n\n- X [3]." + tail
    references = json.loads(merged.files[sidecar] or b"{}")["references"]
    assert {number: reference["note"] for number, reference in references.items()} == {
        "1": "A, edited.",
        "3": "X.",
    }


def test_a_true_conflict_stays_one_item_at_the_incoming_number(tmp_path: Path) -> None:
    """MIK-R66 rule 5: what both lines really changed differently is still one conflict item."""

    git(tmp_path, "init", "-q")
    card = "onboarding/src/pkg/app.py.md"
    sidecar = "onboarding/src/pkg/app.py.json"
    base = {
        card: b"# app\n\nA [1].\n\nB [2].\n",
        sidecar: _sidecar({"1": _aligned_card("A.", "a"), "2": _aligned_card("B.", "b")}),
    }
    ours = {
        card: b"# app\n\nA, ours [1].\n\nB [2].\n",
        sidecar: _sidecar({"1": _aligned_card("A, ours.", "a"), "2": _aligned_card("B.", "b")}),
    }
    theirs = {
        card: b"# app\n\nA, theirs [1].\n\nB [2].\n",
        sidecar: _sidecar({"1": _aligned_card("A, theirs.", "a"), "2": _aligned_card("B.", "b")}),
    }

    merged = merge_trees(base, ours, theirs, repository=tmp_path)

    sidecar_conflicts = [one for one in merged.conflicts if one.path == sidecar]
    assert [(one.item, one.reason) for one in sidecar_conflicts] == [
        ("references.1", "authored fields changed differently on both sides")
    ]


def test_evidence_lines_both_sides_added_are_kept_in_order(tmp_path: Path) -> None:
    """MIK-R66 rule 6: an empty-base region of evidence lines only is incoming first, then own."""

    git(tmp_path, "init", "-q")
    card = "onboarding/src/pkg/app.py.md"
    sidecar = "onboarding/src/pkg/app.py.json"
    base = {
        card: b"# app\n\nA [1].\n\n## Evidence\n",
        sidecar: _sidecar({"1": _aligned_card("A.", "a")}),
    }
    ours = {
        card: b"# app\n\nA [1].\n\n## Evidence\n\n- Own finding. [2]\n",
        sidecar: _sidecar({"1": _aligned_card("A.", "a"), "2": _aligned_card("Own finding.", "x")}),
    }
    theirs = {
        card: b"# app\n\nA [1].\n\n## Evidence\n\n- Incoming finding. [2]\n",
        sidecar: _sidecar(
            {"1": _aligned_card("A.", "a"), "2": _aligned_card("Incoming finding.", "y")}
        ),
    }

    merged = merge_trees(base, ours, theirs, repository=tmp_path)

    assert merged.conflicts == []
    assert merged.alignment.evidence_lines_kept == 2
    assert merged.files[card] == (
        b"# app\n\nA [1].\n\n## Evidence\n\n- Incoming finding. [2]\n- Own finding. [3]\n"
    )
    references = json.loads(merged.files[sidecar] or b"{}")["references"]
    assert {number: reference["note"] for number, reference in references.items()} == {
        "1": "A.",
        "2": "Incoming finding.",
        "3": "Own finding.",
    }


def test_a_repeated_number_refuses_alignment() -> None:
    """MIK-R66 rule 7: the pairing must never give one number to two references of a side.

    The pairing keeps every side injective, so this guard is defensive: it fails
    closed on a colliding map instead of writing two references under one number.
    """

    base = {
        "1": _aligned_card("A.", "a"),
        "2": _aligned_card("B.", "b"),
    }
    assert (
        _assign_numbers(
            base,
            {},
            {"5": _aligned_card("C.", "c")},
            ReferencePairing({}, {"1": "5", "2": "5"}, {}),
        )
        is None
    )


def test_the_crossing_report_counts_alignment(tmp_path: Path) -> None:
    """MIK-R66 rule 8: the report counts aligned cards, moved references and rule 7 skips."""

    git(tmp_path, "init", "-q")
    app = "onboarding/src/pkg/app.py"
    other = "onboarding/src/pkg/other.py"
    base = {
        f"{app}.md": b"# app\n\nA [1].\n",
        f"{app}.json": _sidecar({"1": _aligned_card("A.", "a")}),
        f"{other}.md": b"# other\n\nOther.\n",
        f"{other}.json": _sidecar({"1": _aligned_card("Other.", "o")}),
    }
    ours = {
        f"{app}.md": b"# app\n\nA [1].\n\n- Own finding. [2]\n",
        f"{app}.json": _sidecar(
            {"1": _aligned_card("A.", "a"), "2": _aligned_card("Own finding.", "x")}
        ),
        f"{other}.md": b"# other\n\nOther, with an uncited addition.\n",
        f"{other}.json": _sidecar(
            {"1": _aligned_card("Other.", "o"), "2": _aligned_card("Silent.", "s")}
        ),
    }
    theirs = {
        f"{app}.md": b"# app\n\nA, edited [1].\n\n- Incoming finding. [2]\n",
        f"{app}.json": _sidecar(
            {"1": _aligned_card("A, edited.", "a"), "2": _aligned_card("Incoming finding.", "y")}
        ),
        f"{other}.md": b"# other\n\nOther, edited.\n",
        f"{other}.json": _sidecar({"1": _aligned_card("Other, edited.", "o")}),
    }
    base_in, own_in, incoming_in = _converted_sides((base, ours, theirs))

    plan = cross(
        (base_in, own_in, incoming_in),
        CodeObjects(tmp_path),
        own_paired_commit="0" * 40,
        repository=tmp_path,
        owner=HistoryOwner(kind="leaf", id="260928-MIK-L66"),
    )

    assert plan.report["converted"] == []
    assert plan.report["alignment"] == {
        "alignedCards": 1,
        "renumberedReferences": 1,
        "evidenceLinesKept": 0,
        "skippedCards": [
            {
                "path": f"{other}.json",
                "reason": "the own side would be renumbered but has a reference without a marker ([1], [2])",
            }
        ],
    }


def test_an_ordinary_converted_sync_aligns_through_the_same_merge(tmp_path: Path) -> None:
    """MIK-R66 rule 9: two converted leaves adding the same number merge without conflict."""

    git(tmp_path, "init", "-q")
    card = "onboarding/src/pkg/app.py.md"
    sidecar = "onboarding/src/pkg/app.py.json"
    base = {card: b"# app\n\nA [1].\n", sidecar: _sidecar({"1": _aligned_card("A.", "a")})}
    ours = {
        card: b"# app\n\nA [1].\n\n- Own finding. [2]\n",
        sidecar: _sidecar({"1": _aligned_card("A.", "a"), "2": _aligned_card("Own finding.", "x")}),
    }
    theirs = {
        card: b"# app\n\nA [1].\n\n- Incoming finding. [2]\n",
        sidecar: _sidecar(
            {"1": _aligned_card("A.", "a"), "2": _aligned_card("Incoming finding.", "y")}
        ),
    }
    base_in, own_in, incoming_in = _converted_sides((base, ours, theirs))

    plan = cross(
        (base_in, own_in, incoming_in),
        CodeObjects(tmp_path),
        own_paired_commit="0" * 40,
        repository=tmp_path,
        owner=HistoryOwner(kind="leaf", id="260928-MIK-L66"),
    )

    assert plan.report["converted"] == []  # nothing to convert: the ordinary route
    assert plan.conflicts == []
    assert plan.report["alignment"]["renumberedReferences"] == 1
    references = json.loads(plan.files[sidecar] or b"{}")["references"]
    assert {number: reference["note"] for number, reference in references.items()} == {
        "1": "A.",
        "2": "Incoming finding.",
        "3": "Own finding.",
    }


def test_the_crossing_summary_carries_the_alignment_counts(tmp_path: Path) -> None:
    """MIK-R66 rule 8: the sync result's summary surfaces what the report counted."""

    report = {
        "conflicts": [],
        "cards": {},
        "converted": [],
        "markerRows": 0,
        "alignment": {
            "alignedCards": 2,
            "renumberedReferences": 3,
            "evidenceLinesKept": 4,
            "skippedCards": [{"path": "onboarding/x.py.json", "reason": "rule 7"}],
        },
    }
    target = tmp_path / "report.json"
    target.write_text(json.dumps(report), encoding="utf-8")

    summary = crossing_summary(target.as_posix())

    assert summary["alignment"] == report["alignment"]


def test_the_same_three_trees_merge_to_identical_bytes(tmp_path: Path) -> None:
    """MIK-R66 preservation: the merge is a function of the three trees only."""

    git(tmp_path, "init", "-q")
    card = "onboarding/src/pkg/app.py.md"
    sidecar = "onboarding/src/pkg/app.py.json"
    base = {
        card: b"# app\n\nA [1].\n\nB [2].\n\nC [3].\n",
        sidecar: _sidecar(
            {
                "1": _aligned_card("A.", "a"),
                "2": _aligned_card("B.", "b"),
                "3": _aligned_card("C.", "c"),
            }
        ),
    }
    ours = {
        card: b"# app\n\nA [1].\n\nX [4].\n",
        sidecar: _sidecar({"1": _aligned_card("A.", "a"), "4": _aligned_card("X.", "x")}),
    }
    theirs = {
        card: b"# app\n\nC [3].\n",
        sidecar: _sidecar({"3": _aligned_card("C.", "c")}),
    }

    first = merge_trees(base, ours, theirs, repository=tmp_path)
    second = merge_trees(base, ours, theirs, repository=tmp_path)

    assert first.files == second.files
    assert [(one.path, one.item) for one in first.conflicts] == [
        (one.path, one.item) for one in second.conflicts
    ]


def _no_base_trees(
    own_refs: dict, incoming_refs: dict, own_lines: list[str], incoming_lines: list[str]
) -> tuple[dict, dict, dict]:
    """Three trees for a card both sides added: no base, different sides."""

    card = "onboarding/src/pkg/app.py.md"
    sidecar = "onboarding/src/pkg/app.py.json"
    head = "# app\n\n"
    return (
        {},
        {
            sidecar: _sidecar(own_refs),
            card: (head + "".join(f"{line}\n" for line in own_lines)).encode("utf-8"),
        },
        {
            sidecar: _sidecar(incoming_refs),
            card: (head + "".join(f"{line}\n" for line in incoming_lines)).encode("utf-8"),
        },
    )


def test_both_added_cards_align_distinct_references_without_collision(
    tmp_path: Path,
) -> None:
    """MIK-R66 rules 1-2 with no base: two distinct [1] become incoming [1] and own [2]."""

    git(tmp_path, "init", "-q")
    base, ours, theirs = _no_base_trees(
        {"1": _aligned_card("Own.", "o")},
        {"1": _aligned_card("Incoming.", "i")},
        ["- Own [1]"],
        ["- Incoming [1]"],
    )
    result = merge_trees(base, ours, theirs, repository=tmp_path)

    assert [(one.path, one.item) for one in result.conflicts] == []
    merged_bytes = result.files["onboarding/src/pkg/app.py.json"]
    assert merged_bytes is not None
    merged = json.loads(merged_bytes)
    assert {number: reference["note"] for number, reference in merged["references"].items()} == {
        "1": "Incoming.",
        "2": "Own.",
    }
    assert result.files["onboarding/src/pkg/app.py.md"] == (b"# app\n\n- Incoming [1]\n- Own [2]\n")
    assert result.alignment.aligned_cards == 1
    assert result.alignment.renumbered_references == 1
    assert result.alignment.evidence_lines_kept == 2


def test_both_added_identical_cards_merge_clean(tmp_path: Path) -> None:
    """MIK-R66 rule 1 with no base: the same addition on both sides stays one card."""

    git(tmp_path, "init", "-q")
    base, ours, theirs = _no_base_trees(
        {"1": _aligned_card("Same.", "s")},
        {"1": _aligned_card("Same.", "s")},
        ["- Same [1]"],
        ["- Same [1]"],
    )
    result = merge_trees(base, ours, theirs, repository=tmp_path)

    assert [(one.path, one.item) for one in result.conflicts] == []
    assert (
        result.files["onboarding/src/pkg/app.py.json"] == theirs["onboarding/src/pkg/app.py.json"]
    )


def test_partnerless_incoming_pairs_directly_with_partnerless_own(tmp_path: Path) -> None:
    """MIK-R66 rule 2a: incoming B1 with no own partner pairs with own B4, not freshened."""

    git(tmp_path, "init", "-q")
    card = "onboarding/src/pkg/app.py.md"
    sidecar = "onboarding/src/pkg/app.py.json"
    base = {
        card: b"# app\n\n- A [1]\n",
        sidecar: _sidecar({"1": _aligned_card("A.", "a")}),
    }
    ours = {
        card: b"# app\n\n- B [4]\n- X [5]\n",
        sidecar: _sidecar({"4": _aligned_card("B.", "b"), "5": _aligned_card("X.", "x")}),
    }
    theirs = {
        card: b"# app\n\n- B [1]\n- Y [2]\n",
        sidecar: _sidecar({"1": _aligned_card("B.", "b"), "2": _aligned_card("Y.", "y")}),
    }
    result = merge_trees(base, ours, theirs, repository=tmp_path)

    assert [(one.path, one.item) for one in result.conflicts if one.path.endswith(".json")] == []
    merged_bytes = result.files[sidecar]
    assert merged_bytes is not None
    merged = json.loads(merged_bytes)
    assert {number: reference["note"] for number, reference in merged["references"].items()} == {
        "1": "B.",
        "2": "Y.",
        "6": "X.",
    }


def test_rule_2b_moves_content_only_through_alignment(tmp_path: Path) -> None:
    """MIK-R66 rule 2b discriminator: all three sidecars differ, so only alignment merges clean.

    Without the pairing pass the both-added ``references.3`` (C versus D) stays a
    reference collision; with it, incoming keeps ``[3]`` and own takes fresh ``[4]``.
    """

    git(tmp_path, "init", "-q")
    card = "onboarding/src/pkg/app.py.md"
    sidecar = "onboarding/src/pkg/app.py.json"
    head = "# app\n\nA [1].\n\nB [2].\n"
    base = {
        card: head.encode("utf-8"),
        sidecar: _sidecar({"1": _aligned_card("A.", "a"), "2": _aligned_card("B.", "b")}),
    }
    ours = {
        card: (head + "\nC [3].\n").encode("utf-8"),
        sidecar: _sidecar(
            {
                "1": _aligned_card("A.", "a"),
                "2": _aligned_card("B.", "b"),
                "3": _aligned_card("C.", "c"),
            }
        ),
    }
    theirs = {
        card: (head + "\nD [3].\n").encode("utf-8"),
        sidecar: _sidecar(
            {
                "1": _aligned_card("A.", "a"),
                "2": _aligned_card("B.", "b"),
                "3": _aligned_card("D.", "d"),
            }
        ),
    }
    result = merge_trees(base, ours, theirs, repository=tmp_path)

    assert [(one.path, one.item) for one in result.conflicts if one.path.endswith(".json")] == []
    merged_bytes = result.files[sidecar]
    assert merged_bytes is not None
    merged = json.loads(merged_bytes)
    assert {number: reference["note"] for number, reference in merged["references"].items()} == {
        "1": "A.",
        "2": "B.",
        "3": "D.",
        "4": "C.",
    }
    assert result.alignment.renumbered_references == 1


def test_fenced_code_lines_never_resolve_evidence_conflicts(tmp_path: Path) -> None:
    """MIK-R66 rule 6: marker-shaped lines inside fenced blocks stay a conflict."""

    git(tmp_path, "init", "-q")
    card = "onboarding/src/pkg/app.py.md"
    head = "# app\n\n## Evidence\n"
    base = {card: head.encode("utf-8")}
    ours = {card: (head + "\n```\n- Own [1]\n```\n").encode("utf-8")}
    theirs = {card: (head + "\n```\n- Incoming [2]\n```\n").encode("utf-8")}
    result = merge_trees(base, ours, theirs, repository=tmp_path)

    assert [(one.path, one.item) for one in result.conflicts] == [(card, "(lines)")]
    assert result.alignment.evidence_lines_kept == 0


def test_escaped_trailing_brackets_never_resolve_evidence_conflicts(tmp_path: Path) -> None:
    """MIK-R66 rule 6: a line whose trailing bracket is escaped stays a conflict."""

    git(tmp_path, "init", "-q")
    card = "onboarding/src/pkg/app.py.md"
    head = "# app\n\n- Real [2]\n"
    base = {card: head.encode("utf-8")}
    ours = {card: (head + "\n- Own [3] \\[1]\n").encode("utf-8")}
    theirs = {card: (head + "\n- Incoming [2] \\[2]\n").encode("utf-8")}
    result = merge_trees(base, ours, theirs, repository=tmp_path)

    assert [(one.path, one.item) for one in result.conflicts] == [(card, "(lines)")]
    assert result.alignment.evidence_lines_kept == 0


def test_real_trailing_markers_still_resolve_with_shared_context(tmp_path: Path) -> None:
    """MIK-R66 rule 6 control: real evidence lines resolve around identical context."""

    git(tmp_path, "init", "-q")
    card = "onboarding/src/pkg/app.py.md"
    head = "# app\n\n## Evidence\n"
    base = {card: head.encode("utf-8")}
    ours = {card: (head + "\n- Own [1]\n").encode("utf-8")}
    theirs = {card: (head + "\n- Incoming [2]\n").encode("utf-8")}
    result = merge_trees(base, ours, theirs, repository=tmp_path)

    assert [(one.path, one.item) for one in result.conflicts] == []
    assert result.files[card] == b"# app\n\n## Evidence\n\n- Incoming [2]\n- Own [1]\n"
    assert result.alignment.evidence_lines_kept == 2


def test_gap_pairing_scales_near_linearly_on_fully_paired_inputs() -> None:
    """MIK-R66 pairing: two thousand fully paired references pair fast and exactly."""

    references = {
        str(number): _aligned_card(f"Note {number}.", f"blob-{number}") for number in range(1, 2001)
    }
    started = time.perf_counter()
    pairs = _pair_with_base(references, dict(references))
    elapsed = time.perf_counter() - started

    assert pairs == {str(number): str(number) for number in range(1, 2001)}
    # The 10s bound catches a complexity regression, not load: pairing 2000 references
    # runs in well under a second. load-independent: complexity bound.
    assert elapsed < 10, f"pairing 2000 references took {elapsed:.2f}s"


def test_gap_pairing_scales_on_crossed_fully_paired_inputs() -> None:
    """MIK-R66 pairing: reordered fully paired numbers stay sorting-dominated, not quadratic."""

    count = 2000
    base = {
        str(number): _aligned_card(f"Note {number}.", f"blob-{number}")
        for number in range(1, count + 1)
    }
    order = [1]
    low, high = 2, count
    while low <= high:
        order.append(high)
        if low != high:
            order.append(low)
        low += 1
        high -= 1
    side = {
        str(side_number): _aligned_card(f"Note {base_number}.", f"blob-{base_number}")
        for base_number, side_number in enumerate(order, start=1)
    }

    started = time.perf_counter()
    pairs = _pair_with_base(base, side)
    elapsed = time.perf_counter() - started

    assert pairs == {
        str(base_number): str(side_number) for base_number, side_number in enumerate(order, start=1)
    }
    # The 10s bound catches a complexity regression, not load: crossed pairing of 2000
    # references runs in well under a second. load-independent: complexity bound.
    assert elapsed < 10, f"pairing 2000 crossed references took {elapsed:.2f}s"


def test_gap_pairing_accounts_live_unpaired_numbers_per_interval() -> None:
    """MIK-R66 rule 2c: a side number paired by an earlier gap is gone for later gaps."""

    base = {
        number: {"note": note}
        for number, note in (
            ("1", "A"),
            ("2", "X"),
            ("4", "B"),
            ("7", "C"),
            ("8", "Y"),
            ("10", "D"),
        )
    }
    side = {
        number: {"note": note}
        for number, note in (
            ("1", "A"),
            ("2", "C"),
            ("3", "U"),
            ("4", "B"),
            ("5", "V"),
            ("6", "D"),
        )
    }

    assert _pair_with_base(base, side) == {
        "1": "1",
        "4": "4",
        "7": "2",
        "10": "6",
        "2": "3",
        "8": "5",
    }


def test_multiline_code_spans_never_resolve_evidence_conflicts(tmp_path: Path) -> None:
    """MIK-R66 rule 6: marker-shaped lines inside a multiline code span stay a conflict."""

    git(tmp_path, "init", "-q")
    card = "onboarding/src/pkg/app.py.md"
    head = "# app\n\n`open\n"
    tail = "closed`\n"
    base = {card: (head + tail).encode("utf-8")}
    ours = {card: (head + "- Own [1]\n" + tail).encode("utf-8")}
    theirs = {card: (head + "- Incoming [2]\n" + tail).encode("utf-8")}
    result = merge_trees(base, ours, theirs, repository=tmp_path)

    assert [(one.path, one.item) for one in result.conflicts] == [(card, "(lines)")]
    assert result.alignment.evidence_lines_kept == 0


def test_rule_2b_pairs_same_numbers_when_content_cannot(tmp_path: Path) -> None:
    """MIK-R66 rule 2b discriminator: same-number pairing alone produces the clean merge.

    The duplicated own content defeats unique-content test a, and the gap test c
    never sees a singleton on both sides; only the same-number test b pairs base
    2 with own 2 and incoming 2, so incoming keeps [2] and the duplicated own
    reference takes fresh [4]. Without test b the merge keeps both [4] and [5]
    and the Markdown stays conflicted.
    """

    git(tmp_path, "init", "-q")
    card = "onboarding/src/pkg/app.py.md"
    sidecar = "onboarding/src/pkg/app.py.json"
    head = "# app\n\nA [1].\n"
    base = {
        card: head.encode("utf-8"),
        sidecar: _sidecar({"1": _aligned_card("A.", "a"), "2": _aligned_card("B.", "b")}),
    }
    ours = {
        card: (head + "\n- X [2]\n\n- X [3]\n").encode("utf-8"),
        sidecar: _sidecar(
            {
                "1": _aligned_card("A.", "a"),
                "2": _aligned_card("X.", "x"),
                "3": _aligned_card("X.", "x"),
            }
        ),
    }
    theirs = {
        card: (head + "\n- X [2]\n").encode("utf-8"),
        sidecar: _sidecar(
            {
                "1": _aligned_card("A.", "a"),
                "2": _aligned_card("X.", "x"),
            }
        ),
    }
    result = merge_trees(base, ours, theirs, repository=tmp_path)

    assert [(one.path, one.item) for one in result.conflicts] == []
    merged_bytes = result.files[sidecar]
    assert merged_bytes is not None
    merged = json.loads(merged_bytes)
    assert {number: reference["note"] for number, reference in merged["references"].items()} == {
        "1": "A.",
        "2": "X.",
        "4": "X.",
    }
    assert result.files[card] == b"# app\n\nA [1].\n\n- X [2]\n\n- X [4]\n"
    assert result.alignment.renumbered_references == 1
