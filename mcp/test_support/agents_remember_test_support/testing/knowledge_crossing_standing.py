"""Generate standing cases and read their public record and writer evidence."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import Any, Protocol

import pytest
from agents_remember.mcp.tools.knowledge import ReadToolRequest, knowledge_read_payload
from agents_remember.models.knowledge_files.canonical import canonical_text
from agents_remember.models.knowledge_files.converted_reviews import CONVERTED_REVIEWS_PATH
from agents_remember.models.knowledge_files.documents import history_path
from agents_remember.models.knowledge_files.history_product import record_content_digest

from agents_remember_test_support.testing.fixture_git import fixture_git


class StampForFixture(Protocol):
    """The existing writer-stamp test fixture as used by the generated sides."""

    def __call__(self, leaf: str, *, model: str) -> dict[str, Any]: ...


_SHAPES = ("old", "converted", "stamped")
_FACTS = (
    "none",
    "exact",
    "other-write",
    "other-tree",
    "consolidator-write",
    "consolidator-keep",
    "consolidator-no-keep",
)
_SIDES = tuple(product(_FACTS, (False, True)))
PROFILE_PAIRS = tuple((selected, other) for selected, other in product(_SIDES, _SIDES))
WRITER_FIELDS = ("role", "harness", "model")
_ORDERS = (("first", True), ("last", False))
_DIRECTIONS = (("own", False), ("incoming", True))
_PRIOR_PATH_SHAPES = ("converted", "stamped")
_CONTROLS = (
    ("stamped", "tie-exact", _DIRECTIONS),
    ("converted", "unbound-review", _DIRECTIONS),
    ("stamped", "equal-meaning", _DIRECTIONS[:1]),
)


@dataclass(frozen=True)
class CrossingCase:
    selected_shape: str
    other_shape: str
    selected_incoming: bool
    selected_sorts_first: bool
    control: str


MATRIX_CASES = (
    [
        pytest.param(
            CrossingCase(selected, other, incoming, first, "matrix"),
            id=f"{order}-{direction}-{selected}-{other}",
        )
        for selected, other in product(_SHAPES, repeat=2)
        for direction, incoming in _DIRECTIONS
        for order, first in _ORDERS
    ]
    + [
        pytest.param(
            CrossingCase(shape, "stamped", incoming, first, control),
            id=f"{order}-{direction}-{control}",
        )
        for shape, control, directions in _CONTROLS
        for direction, incoming in directions
        for order, first in _ORDERS
    ]
    + [
        pytest.param(
            CrossingCase(shape, "stamped", incoming, first, "prior-path"),
            id=f"{order}-{direction}-{shape}-prior-path",
        )
        for shape, (direction, incoming), (order, first) in product(
            _PRIOR_PATH_SHAPES, _DIRECTIONS, _ORDERS
        )
    ]
)


def encoded(document: dict[str, Any]) -> bytes:
    return canonical_text(document).encode("utf-8")


def next_record(base: dict[str, Any]) -> dict[str, Any]:
    record = {**base}
    record.pop("status", None)
    return {
        **record,
        "revision": record["revision"] + 1,
        "statement": "Both sides separately wrote this revised statement.",
    }


def kept_row(record_id: str, revision: int) -> dict[str, Any]:
    return {
        "id": "ROW-3D5E7F",
        "subject": record_id,
        "disposition": "no_impact",
        "reason": "Read at this revision and left as it was.",
        "items": [],
        "covers": [],
        "revision": revision,
    }


def changed_row(record_id: str, revision: int) -> dict[str, Any]:
    """A write from before stamps: a ``changed`` row naming the revision, no ``written`` row."""

    return {**kept_row(record_id, revision), "disposition": "changed", "effect": "clarify"}


def matrix_base(number: int, seed: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    record = f"INV-{number:06X}"
    return f"knowledge/invariants/{record}-crossing-standing.json", {**seed, "id": record}


def matrix_records(
    seed: dict[str, Any], stamp: StampForFixture, *, selected: bool, shape: str, prefix: str
) -> tuple[dict[str, bytes | None], dict[str, bytes | None], dict[str, tuple[str, dict[str, Any]]]]:
    """Prepare one Git tree for review and its later tip, plus each owner's frozen history."""

    reviewed_files: dict[str, bytes | None] = {}
    current_files: dict[str, bytes | None] = {}
    histories: dict[str, tuple[str, dict[str, Any]]] = {}
    for number, pair in enumerate(PROFILE_PAIRS, start=1):
        fact, has_write = pair[0 if selected else 1]
        owner = f"{prefix}-{fact}-{int(has_write)}-L1"
        path, base = matrix_base(number, seed)
        current = {**base, "revision": 2, "statement": "Both lines share this new statement."}
        if selected:
            current["applicability"] = "The selected line also changed this field."
        if shape == "stamped":
            current.pop("status", None)
            current["writer"] = stamp(owner, model=f"model-{prefix}")
        reviewed = (
            {**current, "applicability": "The review heard a different revision at this number."}
            if fact in {"other-write", "other-tree"}
            else current
        )
        reviewed_files[path] = encoded(reviewed)
        current_files[path] = encoded(current)
        separate = fact in {"consolidator-keep", "consolidator-no-keep"}
        _, history = histories.setdefault(
            owner,
            ("none" if separate else fact, {"closed": True, "leaf": owner, "rows": []}),
        )
        if has_write and shape == "stamped":
            document = reviewed if fact == "other-write" else current
            history.setdefault("written", []).append(
                {
                    "record": base["id"],
                    "revision": 2,
                    "writer": current["writer"],
                    "contentDigest": record_content_digest(document),
                }
            )
        elif has_write:
            history["rows"].append({**changed_row(base["id"], 2), "id": f"ROW-{number:06X}"})
        if separate:
            keeper = f"{owner}-keep-L1"
            _, kept = histories.setdefault(
                keeper, (fact, {"closed": True, "leaf": keeper, "rows": []})
            )
            if fact == "consolidator-keep":
                kept["rows"].append({**kept_row(base["id"], 2), "id": f"ROW-{number:06X}"})
    combined_path, combined = matrix_base(0, seed)
    combined["revision"] = 2
    field = "statement" if selected else "applicability"
    combined[field] = "This field was changed by only one line."
    reviewed_files[combined_path] = current_files[combined_path] = encoded(combined)
    return reviewed_files, current_files, histories


def _matrix_reader(memory: Path, scratch: Path, **arguments: Any) -> dict[str, Any]:
    result = knowledge_read_payload(
        ReadToolRequest(memory_root=str(memory), **arguments),
        coordination_root=str(scratch / "coordination"),
    )
    assert result["state"] == "view", result
    return result


def matrix_credits(memory: Path, scratch: Path) -> dict[str, tuple[tuple[str, ...], bool]]:
    payload = _matrix_reader(memory, scratch, view="writers")
    report = json.loads(Path(payload["report"]).read_bytes())
    credits = {}
    for writer in report["writers"]:
        key = tuple(writer[field] for field in WRITER_FIELDS)
        reviewed = set(writer["revisions"]["reviewed"])
        for revision in writer["revisions"]["written"]:
            assert revision not in credits, (revision, credits, writer)
            credits[revision] = (key, revision in reviewed)
    return credits


def matrix_standing(memory: Path, scratch: Path, record: str) -> tuple[str, tuple[str, ...]]:
    row = _matrix_reader(memory, scratch, view="record", record_id=record)["record"]
    return row["recordState"], tuple(row["writer"][field] for field in WRITER_FIELDS)


def written(record: dict[str, Any], writer: dict[str, Any]) -> dict[str, Any]:
    return {
        "record": record["id"],
        "revision": record["revision"],
        "writer": writer,
        "contentDigest": record_content_digest(record),
    }


def assert_public_standing(
    memory: Path, scratch: Path, record: dict[str, Any], state: str, writer: tuple[str, ...]
) -> None:
    record_id = record["id"]
    label = f"{record_id}@{record['revision']}"
    assert matrix_standing(memory, scratch, record_id) == (state, writer)
    assert matrix_credits(memory, scratch)[label] == (writer, state != "proposed")


class CrossingLines(Protocol):
    @property
    def leaf(self) -> Path: ...

    @property
    def line(self) -> Path: ...

    def on_leaf(self, files: dict[str, bytes | None], message: str) -> str: ...
    def on_line(self, files: dict[str, bytes | None], message: str) -> str: ...


@dataclass(frozen=True)
class CrossingProfile:
    """One input line, including who wrote it, where it belongs and which byte shape it keeps."""

    shape: str
    name: str
    incoming: bool

    def root(self, lines: CrossingLines) -> Path:
        return lines.line if self.incoming else lines.leaf

    def commit(self, lines: CrossingLines, files: dict[str, bytes | None], message: str) -> str:
        commit = lines.on_line if self.incoming else lines.on_leaf
        return commit(files, message)


@dataclass(frozen=True)
class ReviewedTree:
    commit: str
    tree: str
    path: str
    record: dict[str, Any]

    def prove(self, root: Path) -> None:
        fixture_git(root, ["merge-base", "--is-ancestor", self.commit, "HEAD"])
        reviewed = json.loads(fixture_git(root, ["show", f"{self.tree}:{self.path}"]))
        assert (reviewed["id"], reviewed["revision"]) == (
            self.record["id"],
            self.record["revision"],
        )
        assert record_content_digest(reviewed) == record_content_digest(self.record)


def write_review_tree(
    lines: CrossingLines,
    profile: CrossingProfile,
    record: dict[str, Any],
    current_path: str,
    *,
    prior_path: str | None = None,
) -> ReviewedTree:
    reviewed_path = prior_path or current_path
    files: dict[str, bytes | None] = {reviewed_path: encoded(record)}
    if reviewed_path != current_path:
        files[current_path] = None
    head = profile.commit(lines, files, "store the record whose meaning is reviewed")
    root = profile.root(lines)
    tree = fixture_git(root, ["rev-parse", "HEAD^{tree}"])
    evidence = ReviewedTree(head, tree, reviewed_path, record)
    evidence.prove(root)
    return evidence


def write_closed_history(
    lines: CrossingLines, profile: CrossingProfile, owner: str, body: dict[str, Any]
) -> tuple[str, bytes]:
    path = history_path(owner, body.get("attempt", 1))
    payload = encoded(
        {"schema": "ar-history/v1", "leaf": owner, "closed": True, "rows": [], **body}
    )
    profile.commit(lines, {path: payload}, "close the source history")
    return path, payload


@dataclass(frozen=True)
class SelectedRevision:
    path: str
    record: str
    evidence: tuple[tuple[str, bool], tuple[str, bool]]
    payload: bytes
    standing: tuple[str, tuple[str, ...]]
    credit: tuple[tuple[str, ...], bool]


def capture_selected(
    root: Path, scratch: Path, bases: list[tuple[str, dict[str, Any]]]
) -> list[SelectedRevision]:
    credits = matrix_credits(root, scratch)
    return [
        SelectedRevision(
            path,
            base["id"],
            pair,
            (root / path).read_bytes(),
            matrix_standing(root, scratch, base["id"]),
            credits[f"{base['id']}@2"],
        )
        for (path, base), pair in zip(bases, PROFILE_PAIRS, strict=True)
    ]


def assert_source_controls(expected: list[SelectedRevision], *, old: bool) -> None:
    standalone = {
        item.evidence[0]: item.standing[0]
        for item in expected
        if item.evidence[1] == ("none", False)
    }
    assert standalone[("exact", True)] == ("proposed" if old else "reviewed")
    assert standalone[("consolidator-keep", True)] == ("proposed" if old else "consolidated")
    assert standalone[("consolidator-no-keep", True)] == "proposed"


def assert_retained(
    root: Path,
    scratch: Path,
    expected: list[SelectedRevision],
    credits: dict[str, tuple[tuple[str, ...], bool]],
) -> None:
    differences = []
    for item in expected:
        assert (root / item.path).read_bytes() == item.payload, (item.path, item.evidence)
        wanted = (item.standing, item.credit)
        actual = (matrix_standing(root, scratch, item.record), credits[f"{item.record}@2"])
        if actual != wanted:
            differences.append((item.evidence, wanted, actual))
    assert not differences, differences


def convert(profile: CrossingProfile, lines: CrossingLines, scratch: Path, tasks: Path) -> None:
    report_path = scratch / f"{profile.name}-conversion.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "agents_remember.cli",
            "knowledge-convert-states",
            "--memory-root",
            str(profile.root(lines)),
            "--task-root",
            str(tasks),
            "--apply",
            "--report",
            str(report_path),
        ],
        cwd=scratch,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    report = json.loads(report_path.read_bytes())
    assert report["applied"] and report["counts"]["reviewFactsAdded"] > 0, report
    changes = {name: (profile.root(lines) / name).read_bytes() for name in report["changedFiles"]}
    profile.commit(lines, changes, "apply the one-time state conversion")


def share_conversion(
    profiles: tuple[CrossingProfile, CrossingProfile], lines: CrossingLines
) -> None:
    """Both converted lines can start with the same independently produced archival facts."""

    if any(profile.shape != "converted" for profile in profiles):
        return
    entries = {}
    for profile in profiles:
        ledger = json.loads((profile.root(lines) / CONVERTED_REVIEWS_PATH).read_bytes())
        entries.update({row["owner"]: row for row in ledger["reviews"]})
    ledger["reviews"] = [entries[owner] for owner in sorted(entries)]
    for profile in profiles:
        profile.commit(
            lines,
            {CONVERTED_REVIEWS_PATH: encoded(ledger)},
            "share the recovered archive facts",
        )
