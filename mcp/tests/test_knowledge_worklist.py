"""MIK-R08@v2: the change-to-knowledge worklist over real Git code and converted memory repositories.

Every case builds two real repositories under ``tmp_path``: **code** (a small package, a test module,
a text file and a binary file) and a converted **memory** whose base commit records realization and
proof entries anchored at the code base, two families and their invariants. A case commits a code
candidate C and, where it matters, a memory candidate K_C, then computes the worklist over the four
named sides exactly as the leaf route does.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application.knowledge_worklist import (
    ITEM_KINDS,
    ExplicitSides,
    ItemKind,
    item_id,
    register_item_kind,
    satisfying_row,
    worklist_for_sides,
)
from agents_remember.application.knowledge_worklist.code import (
    CodeTrees,
    Hunk,
    hits_new,
    hits_old,
    map_range,
    parse_hunks,
)
from agents_remember.application.knowledge_worklist.registry import subject_row
from agents_remember.models.knowledge_files import canonical_text
from agents_remember.models.knowledge_files.history import HistoryFile

ORIGIN = {"task": "260101-OLD", "leaf": "260101-OLD-L1"}
REVIEW = "pkg/review.py"
OTHER = "pkg/other.py"
TESTS = "tests/test_review.py"
LINES = "pkg/lines.txt"
DATA = "pkg/data.bin"

REVIEW_V1 = '''"""Review."""


def _not_listed(path):
    """Refuse a path not listed."""
    return path not in LISTED


LISTED = {"a"}


def other(value):
    return value + 1


def third(value):
    return value * 2


def fourth():
    return 4
'''
OTHER_V1 = "def helper():\n    return 1\n"
TESTS_V1 = """from pkg.review import _not_listed


def test_refuses_unlisted():
    assert _not_listed("b")


def test_accepts_listed():
    assert not _not_listed("a")
"""
LINES_V1 = "".join(f"line {number}\n" for number in range(1, 11))
DATA_V1 = b"\x00\x01binary\x00payload"

# entry id -> (invariant, path, locator, list)
ENTRIES: dict[str, tuple[str, str, dict[str, Any], str]] = {
    "RLZ-A00001": ("INV-AAAAAA", REVIEW, {"kind": "symbol", "name": "_not_listed"}, "realizes"),
    "RLZ-B00001": ("INV-BBBBBB", REVIEW, {"kind": "symbol", "name": "other"}, "realizes"),
    "RLZ-B00002": ("INV-BBBBBB", OTHER, {"kind": "symbol", "name": "helper"}, "realizes"),
    "RLZ-C00001": ("INV-CCCCCC", REVIEW, {"kind": "symbol", "name": "third"}, "realizes"),
    "RLZ-D00001": ("INV-DDDDDD", REVIEW, {"kind": "symbol", "name": "fourth"}, "realizes"),
    "RLZ-E00001": ("INV-EEEEEE", LINES, {"kind": "line_range", "start": 3, "end": 5}, "realizes"),
    "RLZ-F00001": ("INV-FFFFFF", DATA, {"kind": "file"}, "realizes"),
    "PRF-P00001": (
        "INV-AAAAAA",
        TESTS,
        {"kind": "symbol", "name": "test_refuses_unlisted"},
        "proves",
    ),
}
FAMILIES = {
    "FAM-F00001": ["INV-AAAAAA", "INV-BBBBBB"],
    "FAM-F00002": ["INV-CCCCCC"],
}


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(root), "GIT_CONFIG_NOSYSTEM": "1"},
    )
    if result.returncode != 0:
        raise AssertionError(f"fixture git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _init(root: Path) -> None:
    root.mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "user.name", "worklist fixture")


def _write(root: Path, files: dict[str, str | bytes | None]) -> None:
    for relative, content in files.items():
        target = root / relative
        if content is None:
            target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")


def invariant(invariant_id: str, revision: int = 1, statement: str = "It holds.") -> str:
    return canonical_text(
        {
            "schema": "ar-invariant/v1",
            "id": invariant_id,
            "revision": revision,
            "status": "accepted",
            "statement": statement,
            "applicability": "Always.",
            "conditions": [],
            "exclusions": [],
            "supersedes": [],
            "admission": "legacy-unassessed",
            "origin": ORIGIN,
        }
    )


def family(family_id: str, members: list[str], revision: int = 1) -> str:
    return canonical_text(
        {
            "schema": "ar-family/v1",
            "id": family_id,
            "revision": revision,
            "status": "accepted",
            "title": family_id,
            "guarantee": "They hold together.",
            "members": members,
            "routes": ["pkg"],
            "admission": "legacy-unassessed",
            "origin": ORIGIN,
        }
    )


@dataclass
class World:
    root: Path
    code: Path
    memory: Path
    code_base: str = ""
    memory_base: str = ""

    def code_commit(self, files: dict[str, str | bytes | None], message: str = "code") -> str:
        _write(self.code, files)
        git(self.code, "add", "-A")
        git(self.code, "commit", "-q", "--allow-empty", "-m", message)
        return git(self.code, "rev-parse", "HEAD")

    def memory_commit(self, files: dict[str, str | bytes | None], code_commit: str) -> str:
        _write(self.memory, files)
        git(self.memory, "add", "-A")
        git(
            self.memory,
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            f"memory\n\nCode-Commit: {code_commit}",
        )
        return git(self.memory, "rev-parse", "HEAD")

    def anchor(self, commit: str, path: str, locator: dict[str, Any]) -> dict[str, Any]:
        tree = git(self.code, "rev-parse", f"{commit}^{{tree}}")
        trees = CodeTrees.open(self.code, tree, tree)
        blob = trees.base()[path]
        resolved = trees.resolve(path, locator, blob, blob)
        assert resolved is not None, (path, locator)
        return {"locator": locator, "blob": blob, "content": resolved.content}

    def sidecars(
        self, commit: str, entries: dict[str, tuple[str, str, dict[str, Any], str]]
    ) -> dict[str, str | bytes | None]:
        by_path: dict[str, dict[str, Any]] = {}
        for entry_id, (invariant_id, path, locator, key) in sorted(entries.items()):
            sidecar = by_path.setdefault(
                path,
                {"schema": "ar-onboarding-file/v1", "path": path, "references": {}, "realizes": []},
            )
            entry: dict[str, Any] = {
                "id": entry_id,
                "invariant": invariant_id,
                "anchor": self.anchor(commit, path, locator),
            }
            if key == "proves":
                entry["facet"] = "It refuses an unlisted path."
                sidecar.setdefault("proves", []).append(entry)
            else:
                entry |= {"role": "primary-authority", "rationale": "It is the rule."}
                sidecar["realizes"].append(entry)
        return {f"onboarding/{path}.json": canonical_text(doc) for path, doc in by_path.items()}

    def sidecar_of(self, path: str) -> dict[str, Any]:
        return json.loads((self.memory / f"onboarding/{path}.json").read_text(encoding="utf-8"))

    def worklist(
        self, candidate: str, *, memory_candidate: str | Path | None = None, **options: Any
    ) -> dict[str, Any]:
        document = worklist_for_sides(
            ExplicitSides(
                code_repository=self.code,
                base=options.pop("base", self.code_base),
                memory_repository=self.memory,
                memory_base=options.pop("memory_base", self.memory_base),
                memory_candidate=memory_candidate or "HEAD",
                code_candidate=candidate,
                owner="260928-MIK-L99",
                **options,
            )
        )
        assert document is not None
        return document


def base_memory(world: World, commit: str) -> dict[str, str | bytes | None]:
    invariants = sorted({one[0] for one in ENTRIES.values()})
    files: dict[str, str | bytes | None] = {
        "knowledge/layout.json": canonical_text(
            {"schema": "ar-memory-layout/v2", "conversion": "1"}
        ),
        **{f"knowledge/invariants/{one}-rule.json": invariant(one) for one in invariants},
        **{
            f"knowledge/families/{one}-group.json": family(one, members)
            for one, members in FAMILIES.items()
        },
    }
    files.update(world.sidecars(commit, ENTRIES))
    return files


@pytest.fixture
def world(tmp_path: Path) -> World:
    world = World(root=tmp_path, code=tmp_path / "code", memory=tmp_path / "memory")
    _init(world.code)
    world.code_base = world.code_commit(
        {REVIEW: REVIEW_V1, OTHER: OTHER_V1, TESTS: TESTS_V1, LINES: LINES_V1, DATA: DATA_V1}
    )
    _init(world.memory)
    world.memory_base = world.memory_commit(base_memory(world, world.code_base), world.code_base)
    return world


def items(document: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    assert document["state"] == "complete", document.get("incomplete")
    return {(item["kind"], item["subject"]): item for item in document["items"]}


def classes(document: dict[str, Any]) -> dict[str, str]:
    return {entry["id"]: entry["class"] for entry in document["entries"]}


def entry_facts(item: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["id"]: entry for entry in item["facts"]["entries"]}


# --------------------------------------------------------------------------------------------------
# Hunks and line-range mapping
# --------------------------------------------------------------------------------------------------


def test_hunks_parse_and_line_ranges_map_through_the_zero_context_diff() -> None:
    output = "@@ -2,0 +3,2 @@\n+x\n+y\n@@ -6 +8 @@\n-a\n+b\n@@ -9,2 +10,0 @@\n-c\n-d\n"
    hunks = parse_hunks(output)
    assert hunks is not None
    assert hunks == (Hunk(2, 0, 3, 2), Hunk(6, 1, 8, 1), Hunk(9, 2, 10, 0))
    assert parse_hunks("Binary files a and b differ\n") is None
    insert, change, delete = hunks
    # Unchanged lines shift past an insertion; a changed line maps onto its replacement.
    assert map_range(hunks, (3, 5)) == (5, 7)
    assert map_range(hunks, (5, 7)) == (7, 9)
    # A range deleted whole, with nothing written in its place, has no image.
    assert map_range(hunks, (9, 10)) is None
    # A pure insertion hits a range on the B side only when it sits strictly inside it.
    assert (
        hits_old(insert, (1, 3)) and not hits_old(insert, (1, 2)) and not hits_old(insert, (3, 5))
    )
    assert hits_new(insert, (3, 3)) and not hits_new(insert, (5, 6))
    assert hits_old(change, (6, 6)) and hits_new(change, (8, 8))
    # A pure deletion hits on the C side only strictly inside the range.
    assert hits_new(delete, (9, 11)) and not hits_new(delete, (11, 12))


# --------------------------------------------------------------------------------------------------
# Classes, precedence and the one-pass scope
# --------------------------------------------------------------------------------------------------


def test_a_body_edit_raises_its_invariant_and_family_and_carries_the_rest_of_the_file(
    world: World,
) -> None:
    edited = REVIEW_V1.replace(
        "    return path not in LISTED\n",
        "    listed = LISTED\n    return path not in listed\n",
    )
    document = world.worklist(world.code_commit({REVIEW: edited}))
    raised = items(document)
    assert set(raised) == {("touched_invariant", "INV-AAAAAA"), ("reached_family", "FAM-F00001")}
    found = classes(document)
    assert found["RLZ-A00001"] == "touched"
    # The other three entries in the file are carried: no item, the writer re-records them.
    assert {found[one] for one in ("RLZ-B00001", "RLZ-C00001", "RLZ-D00001")} == {"carried"}
    # Step 4: every entry of the reached family's members is classified, here at unchanged paths.
    assert found["RLZ-B00002"] == "untouched" and found["PRF-P00001"] == "untouched"
    assert "RLZ-E00001" not in found and "RLZ-F00001" not in found
    touched = entry_facts(raised[("touched_invariant", "INV-AAAAAA")])["RLZ-A00001"]
    assert touched["hunks"] and touched["base"]["resolved"] and touched["candidate"]["resolved"]
    family_facts = raised[("reached_family", "FAM-F00001")]["facts"]
    assert family_facts["members"] == [
        {"id": "INV-AAAAAA", "base": 1, "candidate": 1},
        {"id": "INV-BBBBBB", "base": 1, "candidate": 1},
    ]
    assert family_facts["reachedBy"] == ["touched:INV-AAAAAA"]
    assert document["pairing"]["base"]["commit"] == world.code_base


def test_a_comment_between_functions_raises_nothing_and_one_inside_raises(world: World) -> None:
    between = REVIEW_V1.replace('LISTED = {"a"}\n', '# the listed paths\nLISTED = {"a"}\n')
    outside = world.worklist(world.code_commit({REVIEW: between}))
    assert outside["items"] == []
    assert classes(outside)["RLZ-B00001"] == "carried"
    inside = REVIEW_V1.replace(
        '    """Refuse a path not listed."""\n',
        '    """Refuse a path not listed."""\n    # membership is exact\n',
    )
    document = world.worklist(world.code_commit({REVIEW: inside}))
    assert set(items(document)) == {
        ("touched_invariant", "INV-AAAAAA"),
        ("reached_family", "FAM-F00001"),
    }


def test_stale_at_base_takes_precedence_and_reaches_its_families_without_widening(
    world: World,
) -> None:
    # The code base moves past the memory base: `third` changes, so its entry's blob and content
    # are stale at B. A leaf then edits `third` again and nothing else.
    moved_base = world.code_commit({REVIEW: REVIEW_V1.replace("value * 2", "value * 3")})
    candidate = world.code_commit({REVIEW: REVIEW_V1.replace("value * 2", "value * 4")})
    document = world.worklist(candidate, base=moved_base)
    raised = items(document)
    assert classes(document)["RLZ-C00001"] == "stale_at_base"
    stale = raised[("stale_invariant", "INV-CCCCCC")]["facts"]["entries"][0]
    assert stale["recordedContent"] != stale["baseContent"] != "absent"
    assert ("reached_family", "FAM-F00002") in raised
    assert raised[("reached_family", "FAM-F00002")]["facts"]["reachedBy"] == ["stale:INV-CCCCCC"]
    # A stale item is not a touched one, and it classified no further member.
    assert ("touched_invariant", "INV-CCCCCC") not in raised
    assert "RLZ-B00002" not in classes(document)


def test_moved_or_absent_covers_deletion_rename_ambiguity_and_a_deleted_range(
    world: World,
) -> None:
    git(world.code, "mv", OTHER, "pkg/moved.py")
    ambiguous = REVIEW_V1 + "\n\nclass Holder:\n    def third(self):\n        return 3\n"
    lines = "".join(f"line {number}\n" for number in (1, 2, 6, 7, 8, 9, 10))
    candidate = world.code_commit({REVIEW: ambiguous, LINES: lines, DATA: None})
    document = world.worklist(candidate)
    found = classes(document)
    assert found["RLZ-B00002"] == "moved_or_absent"  # renamed at C
    assert found["RLZ-C00001"] == "moved_or_absent"  # `third` is now bound twice
    assert found["RLZ-E00001"] == "moved_or_absent"  # every line of 3-5 was deleted
    assert found["RLZ-F00001"] == "moved_or_absent"  # the path is absent at C
    raised = items(document)
    helper = entry_facts(raised[("touched_invariant", "INV-BBBBBB")])["RLZ-B00002"]
    assert helper["renamedTo"] == "pkg/moved.py"
    assert helper["uniqueMatch"] == {"path": "pkg/moved.py", "label": "mechanical"}
    third = entry_facts(raised[("touched_invariant", "INV-CCCCCC")])["RLZ-C00001"]
    assert "uniqueMatch" not in third  # ambiguous: no suggestion


def test_line_ranges_map_carry_and_touch(world: World) -> None:
    shifted = "header\n" + LINES_V1
    document = world.worklist(world.code_commit({LINES: shifted}))
    assert classes(document)["RLZ-E00001"] == "carried" and document["items"] == []
    inside = LINES_V1.replace("line 4\n", "line 4\nline 4b\n")
    document = world.worklist(world.code_commit({LINES: inside}))
    assert classes(document)["RLZ-E00001"] == "touched"
    edge = LINES_V1.replace("line 6\n", "line six\n")
    document = world.worklist(world.code_commit({LINES: edge}))
    assert classes(document)["RLZ-E00001"] == "carried"


def test_proof_entries_take_part_in_change_detection(world: World) -> None:
    """Carried from L28 (MIK-R28 rule 3): a changed test body or a deleted test raises an item."""

    changed = TESTS_V1.replace('assert _not_listed("b")', 'assert _not_listed("zz")')
    document = world.worklist(world.code_commit({TESTS: changed}))
    proof = entry_facts(items(document)[("touched_invariant", "INV-AAAAAA")])["PRF-P00001"]
    assert proof["class"] == "touched" and proof["kind"] == "proof"
    deleted = (
        TESTS_V1.split("\n\ndef test_refuses_unlisted", maxsplit=1)[0]
        + "\n\n\ndef test_accepts_listed():\n"
    )
    deleted += '    assert not _not_listed("a")\n'
    document = world.worklist(world.code_commit({TESTS: deleted}))
    proof = entry_facts(items(document)[("touched_invariant", "INV-AAAAAA")])["PRF-P00001"]
    assert proof["class"] == "moved_or_absent"


def test_a_binary_change_touches_a_file_anchor_and_links_at_file_level(world: World) -> None:
    candidate = world.code_commit(
        {DATA: DATA_V1 + b"\x00more", "pkg/blob.bin": b"\x00\x02\x03", OTHER: OTHER_V1 + "# x\n"}
    )
    git(world.code, "update-index", "--chmod=+x", LINES)
    git(world.code, "commit", "-q", "-m", "mode")
    mode_commit = git(world.code, "rev-parse", "HEAD")
    document = world.worklist(mode_commit)
    assert classes(document)["RLZ-F00001"] == "touched"
    changes = {change["path"]: change for change in document["changes"]}
    assert changes[DATA]["fileLevel"] == {"linked": True}  # a file-locator entry covers it
    assert changes["pkg/blob.bin"]["fileLevel"] == {"linked": False}
    assert changes[LINES]["modeChange"] and changes[LINES]["fileLevel"] == {"linked": False}
    # A text hunk no entry range intersects is unexplained; the comment line lies past `helper`.
    assert [hunk["linked"] for hunk in changes[OTHER]["hunks"]] == [False]
    assert candidate != mode_commit


def test_text_hunks_are_linked_by_either_sides_ranges(world: World) -> None:
    edited = REVIEW_V1.replace("value + 1", "value + 2").replace('"""Review."""', '"""Rev."""')
    document = world.worklist(world.code_commit({REVIEW: edited}))
    hunks = {change["path"]: change["hunks"] for change in document["changes"]}[REVIEW]
    assert [hunk["linked"] for hunk in hunks] == [False, True]


def test_knowledge_maintenance_scope_classifies_every_base_entry(world: World) -> None:
    document = world.worklist(world.code_base, maintenance_scope=True)
    assert set(classes(document)) == set(ENTRIES)
    assert set(classes(document).values()) == {"untouched"} and document["items"] == []
    assert document["scope"]["knowledgeMaintenanceScope"] is True


# --------------------------------------------------------------------------------------------------
# Knowledge-side changes
# --------------------------------------------------------------------------------------------------


def test_added_retired_and_reanchored_entries_raise_and_new_records_do_not(world: World) -> None:
    code = world.code_commit({})  # no code change at all
    entries = dict(ENTRIES)
    entries["RLZ-C00002"] = ("INV-CCCCCC", OTHER, {"kind": "symbol", "name": "helper"}, "realizes")
    del entries["RLZ-D00001"]
    entries["RLZ-E00001"] = (
        "INV-EEEEEE",
        LINES,
        {"kind": "line_range", "start": 7, "end": 8},
        "realizes",
    )
    entries["RLZ-N00001"] = ("INV-NNNNNN", OTHER, {"kind": "symbol", "name": "helper"}, "realizes")
    files: dict[str, str | bytes | None] = {
        **world.sidecars(code, entries),
        "knowledge/invariants/INV-NNNNNN-new.json": invariant("INV-NNNNNN"),
        "knowledge/families/FAM-F00001-group.json": family(
            "FAM-F00001", ["INV-AAAAAA", "INV-BBBBBB", "INV-NNNNNN"], revision=2
        ),
        "knowledge/families/FAM-NEW001-group.json": family("FAM-NEW001", ["INV-DDDDDD"]),
    }
    world.memory_commit(files, code)
    raised = items(world.worklist(code))
    assert raised[("touched_invariant", "INV-CCCCCC")]["facts"]["added"][0]["id"] == "RLZ-C00002"
    assert raised[("touched_invariant", "INV-DDDDDD")]["facts"]["retired"][0]["id"] == "RLZ-D00001"
    reanchored = raised[("touched_invariant", "INV-EEEEEE")]["facts"]["reanchored"][0]
    assert reanchored["id"] == "RLZ-E00001" and reanchored["baseClass"] == "untouched"
    # A new invariant raises nothing of its own; the family it joined changed, so it is reached.
    assert ("touched_invariant", "INV-NNNNNN") not in raised
    family_item = raised[("reached_family", "FAM-F00001")]["facts"]
    assert {"id": "INV-NNNNNN", "base": None, "candidate": 1} in family_item["members"]
    assert "record-changed" in family_item["reachedBy"]
    # A family present only in K_C raises nothing.
    assert ("reached_family", "FAM-NEW001") not in raised


def test_a_mechanical_carry_forward_in_k_c_is_not_a_change(world: World) -> None:
    shifted = REVIEW_V1.replace('"""Review."""\n', '"""Review."""\n# header\n')
    code = world.code_commit({REVIEW: shifted})
    world.memory_commit(world.sidecars(code, ENTRIES), code)  # every blob re-recorded at C
    document = world.worklist(code)
    assert document["items"] == []
    assert classes(document)["RLZ-A00001"] == "carried"


def test_a_re_anchor_of_a_stale_entry_raises_the_stale_item_not_a_re_anchor(world: World) -> None:
    # `third` changed after the memory base; B is that commit and the leaf changes no code. The
    # curator refreshes the stale entry at an unchanged path: its K_B class covers the re-anchor.
    moved_base = world.code_commit({REVIEW: REVIEW_V1.replace("value * 2", "value * 3")})
    world.memory_commit(world.sidecars(moved_base, ENTRIES), moved_base)
    raised = items(world.worklist(moved_base, base=moved_base))
    assert ("stale_invariant", "INV-CCCCCC") in raised
    assert ("touched_invariant", "INV-CCCCCC") not in raised
    # Every other re-recorded entry only moved mechanically (carried): no item of its own.
    assert set(raised) == {("stale_invariant", "INV-CCCCCC"), ("reached_family", "FAM-F00002")}


def test_a_record_change_raises_its_invariant_with_both_revisions(world: World) -> None:
    code = world.code_commit({})
    world.memory_commit(
        {"knowledge/invariants/INV-EEEEEE-rule.json": invariant("INV-EEEEEE", 2, "It holds more.")},
        code,
    )
    record = items(world.worklist(code))[("touched_invariant", "INV-EEEEEE")]["facts"]["record"]
    assert record == {"changed": True, "baseRevision": 1, "candidateRevision": 2}


# --------------------------------------------------------------------------------------------------
# Items: registry, identity, determinism, incomplete
# --------------------------------------------------------------------------------------------------


def test_the_registry_declares_four_things_per_kind_and_refuses_a_second_registration() -> None:
    for name in ("touched_invariant", "stale_invariant", "reached_family"):
        kind = ITEM_KINDS[name]
        assert kind.subject and kind.facts and kind.satisfying_row and kind.owner == "MIK-R08"
    with pytest.raises(ValueError, match="already registered"):
        register_item_kind(ITEM_KINDS["reached_family"])
    history = HistoryFile.model_validate(
        {
            "schema": "ar-history/v1",
            "leaf": "260928-MIK-L99",
            "closed": False,
            "rows": [
                {
                    "id": "ROW-AAAAAA",
                    "subject": "FAM-F00001",
                    "disposition": "no_impact",
                    "reason": "Examined.",
                    "items": [],
                    "examined": [{"id": "INV-AAAAAA", "revision": 1}],
                }
            ],
        }
    )
    assert satisfying_row("reached_family", "FAM-F00001", history) is not None
    assert satisfying_row("touched_invariant", "INV-AAAAAA", history) is None
    later = ItemKind(
        name="later_kind_probe",
        subject="family ID",
        subject_pattern=r"^FAM-",
        facts=("x",),
        satisfying_row="the family row",
        row_lookup=subject_row("family"),
        owner="MIK-R06",
    )
    try:
        register_item_kind(later)
        assert satisfying_row("later_kind_probe", "FAM-F00001", history) is not None
    finally:
        ITEM_KINDS.pop("later_kind_probe")


def test_item_ids_are_stable_deterministic_and_bound_to_range_content(world: World) -> None:
    edited = REVIEW_V1.replace("return path not in LISTED", "return path not in set(LISTED)")
    first = world.code_commit({REVIEW: edited})
    one, two = world.worklist(first), world.worklist(first)
    assert one == two and one["digest"] == two["digest"]
    touched = items(one)[("touched_invariant", "INV-AAAAAA")]["id"]
    assert touched.startswith("sha256:") and len(touched) == 71
    # A change elsewhere in the same file leaves the item's range content, and so its ID, unchanged.
    elsewhere = world.code_commit({REVIEW: edited.replace("return 4", "return 4  # four")})
    moved = world.worklist(elsewhere)
    assert items(moved)[("touched_invariant", "INV-AAAAAA")]["id"] == touched
    assert moved["digest"] != one["digest"]  # the D entry is touched now: another item
    different = world.code_commit({REVIEW: REVIEW_V1.replace("not in LISTED", "not in {}")})
    assert items(world.worklist(different))[("touched_invariant", "INV-AAAAAA")]["id"] != touched
    assert item_id("k", "s", [1]) == item_id("k", "s", [1]) != item_id("k", "s", [2])


def test_unreadable_inputs_make_the_run_incomplete_and_name_them(world: World) -> None:
    candidate = world.code_commit({REVIEW: REVIEW_V1 + "\n"})
    unknown_base = world.worklist(candidate, base="0" * 40)
    assert unknown_base["state"] == "incomplete" and unknown_base["items"] == []
    assert unknown_base["incomplete"][0]["input"] == "B"
    world.memory_commit({"knowledge/invariants/INV-AAAAAA-rule.json": "{not json"}, candidate)
    broken = world.worklist(candidate)
    assert broken["state"] == "incomplete"
    assert broken["incomplete"][0]["input"] == "K_C"
    assert "INV-AAAAAA-rule.json" in broken["incomplete"][0]["detail"]


def test_two_unconverted_memory_sides_get_no_worklist(tmp_path: Path) -> None:
    world = World(root=tmp_path, code=tmp_path / "code", memory=tmp_path / "memory")
    _init(world.code)
    base = world.code_commit({REVIEW: REVIEW_V1})
    _init(world.memory)
    memory = world.memory_commit({"onboarding/overview.md": "# root\n"}, base)
    sides = ExplicitSides(
        code_repository=world.code,
        base=base,
        memory_repository=world.memory,
        memory_base=memory,
        memory_candidate="HEAD",
        code_candidate=base,
    )
    assert worklist_for_sides(sides) is None


# --------------------------------------------------------------------------------------------------
# Fix round 1 (review R1)
# --------------------------------------------------------------------------------------------------


def test_an_unchanged_blob_is_untouched_whatever_its_recorded_content_says(world: World) -> None:
    """F3 (ruling): definition 4 wins; the content fallback needs a changed blob."""

    sidecar = world.sidecar_of(REVIEW)
    for entry in sidecar["realizes"]:
        if entry["id"] == "RLZ-D00001":
            entry["anchor"]["content"] = "sha256:" + "0" * 64
    world.memory_base = world.memory_commit(
        {f"onboarding/{REVIEW}.json": canonical_text(sidecar)}, world.code_base
    )
    unchanged = world.worklist(world.code_base, maintenance_scope=True)
    assert classes(unchanged)["RLZ-D00001"] == "untouched" and unchanged["items"] == []
    # With the blob changed elsewhere in the file, the mismatch is the curator's to look at.
    elsewhere = world.code_commit({REVIEW: REVIEW_V1.replace('"""Review."""', '"""Rev."""')})
    changed = world.worklist(elsewhere)
    assert classes(changed)["RLZ-D00001"] == "touched"
    assert (
        entry_facts(items(changed)[("touched_invariant", "INV-DDDDDD")])["RLZ-D00001"]["hunks"]
        == []
    )


def test_a_partial_change_inventory_makes_the_run_incomplete_naming_the_paths(
    world: World,
) -> None:
    """F4: an unrepresentable path is named in an ``incomplete`` run, never silently skipped."""

    raw = os.fsencode(str(world.code)) + b"/pkg/\xffname.txt"
    with open(raw, "wb") as handle:
        handle.write(b"text\n")
    candidate = world.code_commit({})
    document = world.worklist(candidate)
    assert document["state"] == "incomplete" and document["items"] == []
    missing = document["incomplete"][0]
    assert missing["input"] == "B/C change inventory"
    assert "not valid text" in missing["detail"] and "name.txt" in missing["detail"]


def test_a_text_change_with_a_mode_change_links_its_hunks_and_the_mode_fact(world: World) -> None:
    """F5: the mode fact is file-level, and the text hunks are linked as for any text change."""

    world.code_commit({OTHER: OTHER_V1.replace("return 1", "return 2")})
    git(world.code, "update-index", "--chmod=+x", OTHER)
    git(world.code, "commit", "-q", "-m", "mode and text")
    document = world.worklist(git(world.code, "rev-parse", "HEAD"))
    change = {one["path"]: one for one in document["changes"]}[OTHER]
    assert change["modeChange"] is True
    assert [hunk["linked"] for hunk in change["hunks"]] == [True]  # inside `helper`
    assert change["fileLevel"] == {"linked": False}  # no file-locator entry covers the path
