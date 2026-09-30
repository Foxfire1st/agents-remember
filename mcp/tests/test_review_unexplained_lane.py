"""MIK-R32: the unexplained-changes lane -- file buckets, hunk classes, destinations and the entry.

Each comparison here is four real Git trees: a code repository with a base and a candidate commit
and a converted memory repository with a base and a candidate commit, reopened as the reviewer
reopens a recorded comparison, so every knowledge side is read through the derived index of its
tree. The code change is the same throughout:

* ``pkg/a.py`` -- ``land``'s body edited (a replace inside ``land``) and a new function appended (an
  insertion after the last line); symbol entries for ``land`` and ``keep``;
* ``pkg/lines.txt`` -- line 2 and line 4 replaced (two adjacent runs) and line 6 deleted; one
  ``line_range`` entry over lines 2-3;
* ``pkg/whole.py`` -- a line replaced; one ``file`` entry;
* ``pkg/stale.py`` -- edited; its entry records a blob that is neither side's (stale);
* ``pkg/unbound.py`` -- edited; its entry names a symbol the file does not define (unresolved);
* ``tests/test_a.py`` -- the test's body edited; one proof entry;
* ``pkg/mode.py`` -- only its mode changed (made executable); a symbol entry;
* ``pkg/new.py`` -- added; no entry anywhere;
* ``pkg/data.bin`` -- a binary file rewritten; one ``file`` entry, which links it at the gate;
* ``pkg/inside.py`` -- a line inserted strictly inside ``f``, whose symbol entry the gate would
  count as hit on the before side.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import apsw
import pytest
from agents_remember.application.knowledge_worklist.code import CodeTrees
from agents_remember.application.review_intent_summary import read_review_intent_summary
from agents_remember.application.review_lane_classification import NAMED_ENTRIES
from agents_remember.application.review_tree_comparison import ReviewTrees, reopened_trees
from agents_remember.application.review_tree_knowledge import read_review_trees
from agents_remember.application.review_unexplained_lane import (
    classify_changed_path,
    lane_summary,
    unexplained_lane,
)
from agents_remember.memory.knowledge_index import KnowledgeIndex, text_uuid
from agents_remember.models.knowledge.base import REFERENCE_MAX_LENGTH
from agents_remember.models.knowledge.review_lane import (
    ReviewFileClassification,
    ReviewLaneDestination,
    ReviewLaneHunk,
    ReviewLanePath,
)
from agents_remember.models.knowledge.review_trees import (
    ReviewTreeComparisonRecord,
    ReviewTreeSide,
)
from agents_remember.models.knowledge_files import canonical_text
from agents_remember.serving.review_trees import ReviewTreesQuery, register_review_trees_route
from agents_remember.worktrees.services import reset_worktree_services
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_review_git_trees import LEAF, MASTER, REPO, World, world  # noqa: F401 - the fixture

A, B, C = "INV-AAAAAA", "INV-BBBBBB", "INV-CCCCCC"
ORIGIN = {"task": "260101-OLD", "leaf": "260101-OLD-L1"}
BASE: dict[str, str | bytes] = {
    "pkg/a.py": "def land(value):\n    return value\n\n\ndef keep():\n    return 1\n",
    "pkg/lines.txt": "1\n2\n3\n4\n5\n6\n",
    "pkg/whole.py": "a = 1\nb = 2\n",
    "pkg/stale.py": "x = 1\n",
    "pkg/unbound.py": "def here():\n    return 1\n",
    "tests/test_a.py": "def test_land():\n    assert True\n",
    "pkg/mode.py": "def run():\n    return 0\n",
    "pkg/data.bin": bytes(range(256)),
    "pkg/inside.py": "def f():\n    a = 1\n    return a\n",
}
CANDIDATE: dict[str, str | bytes] = {
    **BASE,
    "pkg/a.py": (
        "def land(value):\n    return value + 0\n\n\ndef keep():\n    return 1\n"
        "\n\ndef extra():\n    return 2\n"
    ),
    "pkg/lines.txt": "1\nX\n3\nY\n5\n",
    "pkg/whole.py": "a = 1\nb = 3\n",
    "pkg/stale.py": "x = 2\n",
    "pkg/unbound.py": "def here():\n    return 2\n",
    "tests/test_a.py": "def test_land():\n    assert 1 == 1\n",
    "pkg/new.py": "def fresh():\n    return 3\n",
    "pkg/data.bin": bytes(reversed(range(256))),
    "pkg/inside.py": "def f():\n    a = 1\n    b = 2\n    return a\n",
}
EXECUTABLE_IN_CANDIDATE = frozenset({"pkg/mode.py"})
STALE_BLOB = "0" * 40
STALE_CONTENT = "sha256:" + "0" * 64


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(root), "GIT_CONFIG_NOSYSTEM": "1"},
    )
    assert result.returncode == 0, f"fixture git {' '.join(args)} failed: {result.stderr.strip()}"
    return result.stdout.strip()


def _repository(root: Path) -> Path:
    root.mkdir(parents=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "fixture@example.invalid")
    _git(root, "config", "user.name", "lane fixture")
    _git(root, "config", "core.fileMode", "true")
    return root


def _commit(
    root: Path, files: Mapping[str, str | bytes], executable: frozenset[str] = frozenset()
) -> str:
    """Make ``files`` the whole tree of a new commit; return that commit's tree."""

    _git(root, "rm", "-q", "-r", "--ignore-unmatch", "--cached", ".")
    for child in root.iterdir():
        if child.name != ".git":
            subprocess.run(["rm", "-rf", str(child)], check=True)
    for relative, content in files.items():
        target = root / os.fsdecode(relative.encode("utf-8", "surrogateescape"))
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")
        target.chmod(0o755 if relative in executable else 0o644)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "--allow-empty", "-m", "fixture")
    return _git(root, "rev-parse", "HEAD^{tree}")


def _record(kind: str, record_id: str, **fields: Any) -> tuple[str, str]:
    directory = "invariants" if kind == "invariant" else "families"
    body: dict[str, Any] = {
        "schema": f"ar-{kind}/v1",
        "id": record_id,
        "revision": 1,
        "status": "accepted",
        "admission": "legacy-unassessed",
        "origin": ORIGIN,
        **fields,
    }
    if kind == "invariant":
        body = {
            "statement": f"{record_id} holds.",
            "applicability": "Always.",
            "conditions": [],
            "exclusions": [],
            "supersedes": [],
            **body,
        }
    return f"knowledge/{directory}/{record_id}-{record_id.lower()}.json", canonical_text(body)


def _family(family_id: str, members: list[str]) -> tuple[str, str]:
    return _record(
        "family", family_id, title=family_id, guarantee="Together.", members=members, routes=[]
    )


@dataclass
class Code:
    """The code repository and the tree each side of the comparison names."""

    repository: Path
    base: str
    candidate: str

    def anchor(self, path: str, locator: dict[str, Any], *, at_candidate: bool = False) -> dict:
        tree = self.candidate if at_candidate else self.base
        trees = CodeTrees.open(self.repository, tree, tree)
        blob = trees.base()[path]
        resolved = trees.resolve(path, locator, blob, blob)
        content = STALE_CONTENT if resolved is None else resolved.content
        return {"locator": locator, "blob": blob, "content": content}


def _realization(entry_id: str, invariant: str, anchor: dict) -> dict[str, Any]:
    return {
        "id": entry_id,
        "invariant": invariant,
        "anchor": anchor,
        "role": "primary-authority",
        "rationale": "It is the rule.",
    }


def _sidecar(path: str, realizes: list[dict], proves: list[dict] | None = None) -> dict[str, str]:
    body: dict[str, Any] = {
        "schema": "ar-onboarding-file/v1",
        "path": path,
        "references": {},
        "realizes": realizes,
    }
    if proves is not None:
        body["proves"] = proves
    return {f"onboarding/{path}.md": f"# {path}\n", f"onboarding/{path}.json": canonical_text(body)}


def _knowledge(code: Code, *, curated: bool, families: dict[str, list[str]]) -> dict[str, str]:
    """A converted memory tree over ``code``; ``curated`` re-records ``pkg/a.py`` at C."""

    symbol = lambda name: {"kind": "symbol", "name": name}  # noqa: E731 - a local spelling
    a = lambda name: code.anchor("pkg/a.py", symbol(name), at_candidate=curated)  # noqa: E731
    files: dict[str, str] = {
        "knowledge/layout.json": canonical_text(
            {"schema": "ar-memory-layout/v2", "conversion": "1"}
        ),
        "onboarding/overview.md": "# root\n",
        **dict(_record("invariant", one) for one in (A, B, C)),
        **dict(_family(family, members) for family, members in families.items()),
        **_sidecar(
            "pkg/a.py",
            [_realization("RLZ-A00001", A, a("land")), _realization("RLZ-A00002", A, a("keep"))],
        ),
        **_sidecar(
            "pkg/lines.txt",
            [
                _realization(
                    "RLZ-B00001",
                    B,
                    code.anchor("pkg/lines.txt", {"kind": "line_range", "start": 2, "end": 3}),
                )
            ],
        ),
        **_sidecar(
            "pkg/whole.py",
            [_realization("RLZ-C00001", C, code.anchor("pkg/whole.py", {"kind": "file"}))],
        ),
        **_sidecar(
            "pkg/stale.py",
            [
                _realization(
                    "RLZ-C00002",
                    C,
                    {"locator": {"kind": "file"}, "blob": STALE_BLOB, "content": STALE_CONTENT},
                )
            ],
        ),
        **_sidecar(
            "pkg/unbound.py",
            [_realization("RLZ-C00003", C, code.anchor("pkg/unbound.py", symbol("missing")))],
        ),
        **_sidecar(
            "pkg/mode.py",
            [_realization("RLZ-A00003", A, code.anchor("pkg/mode.py", symbol("run")))],
        ),
        **_sidecar(
            "pkg/inside.py",
            [_realization("RLZ-B00002", B, code.anchor("pkg/inside.py", symbol("f")))],
        ),
        **_sidecar(
            "pkg/data.bin",
            [_realization("RLZ-C00004", C, code.anchor("pkg/data.bin", {"kind": "file"}))],
        ),
        **_sidecar(
            "tests/test_a.py",
            [],
            [
                {
                    "id": "PRF-A00004",
                    "invariant": A,
                    "anchor": code.anchor("tests/test_a.py", symbol("test_land")),
                    "facet": "land returns its value.",
                }
            ],
        ),
    }
    return files


BEFORE_FAMILIES = {"FAM-F00001": [A], "FAM-F00002": [A], "FAM-F00003": [A]}
AFTER_FAMILIES = {"FAM-F00001": [A], "FAM-F00002": [B]}


@dataclass
class Lane:
    """One fixture: the two repositories and a way to open a comparison of them."""

    root: Path
    code: Code
    memory: Path

    def trees(
        self, before: dict[str, str] | None, after: dict[str, str], *, code_candidate: str = ""
    ) -> ReviewTrees:
        """Reopen a comparison of the code trees with ``before``/``after`` as the memory trees.

        ``before=None`` names a memory base tree Git cannot produce: that side is unreadable.
        """

        base_tree = STALE_BLOB if before is None else _commit(self.memory, before)
        after_tree = _commit(self.memory, after)
        record = ReviewTreeComparisonRecord(
            task_id=MASTER,
            leaf_id=LEAF,
            number=1,
            code_base=ReviewTreeSide(repository=str(self.code.repository), tree=self.code.base),
            code_candidate=ReviewTreeSide(
                repository=str(self.code.repository),
                tree=code_candidate or self.code.candidate,
            ),
            memory_base=ReviewTreeSide(repository=str(self.memory), tree=base_tree),
            memory_candidate=ReviewTreeSide(repository=str(self.memory), tree=after_tree),
            recorded_at="2026-09-30T00:00:00+00:00",
        )
        return reopened_trees(self.root / "coordination", record, None)

    def knowledge(self, *, curated: bool = False, families: dict | None = None) -> dict[str, str]:
        return _knowledge(self.code, curated=curated, families=families or BEFORE_FAMILIES)

    def precuration(self) -> ReviewTrees:
        """K_C equals K_B's entries (the curator has not run) with the after families."""

        return self.trees(self.knowledge(), self.knowledge(families=AFTER_FAMILIES))


@pytest.fixture
def lane(tmp_path: Path) -> Lane:
    code = _repository(tmp_path / "code")
    base = _commit(code, BASE)
    candidate = _commit(code, CANDIDATE, EXECUTABLE_IN_CANDIDATE)
    return Lane(tmp_path, Code(code, base, candidate), _repository(tmp_path / "memory"))


def _file(trees: ReviewTrees, path: str) -> ReviewFileClassification:
    classified = classify_changed_path(trees, path)
    assert isinstance(classified, ReviewFileClassification), classified
    return classified


def _spans(hunk: ReviewLaneHunk) -> tuple[tuple[int, int], tuple[int, int], str]:
    return (
        (hunk.before.start, hunk.before.count),
        (hunk.after.start, hunk.after.count),
        hunk.classification,
    )


def _listed(destination: ReviewLaneDestination | None) -> list[tuple[str, str, int]]:
    assert destination is not None
    return [(one.path, one.bucket, one.counts.hunks) for one in destination.files]


# -- buckets and reconciliation --------------------------------------------------------------------


def test_every_changed_file_takes_one_bucket_and_the_destinations_reconcile(lane: Lane) -> None:
    trees = lane.precuration()
    result = unexplained_lane(trees)
    assert result.state == "measured"
    assert (result.changed_total, result.attributed, result.unexplained) == (10, 7, 1)
    assert result.attribution_unknown == 2
    _unexplained_destination(result.unexplained_changes)
    _unknown_destination(result.unknown_attribution)
    _every_path_bucket(trees, result.paths)
    # The entry count reads the same buckets, without reading one hunk.
    summary = lane_summary(trees)
    assert (summary.state, summary.changed_total, summary.unexplained) == ("counted", 10, 1)
    assert (summary.attributed, summary.attribution_unknown) == (7, 2)
    # A non-text change a file-locator entry covers is linked at the gate and listed nowhere.
    data = _file(trees, "pkg/data.bin")
    assert (data.bucket, data.hunks) == ("attributed", ())
    assert data.non_text is not None
    assert data.non_text.gate == "linked"


def _every_path_bucket(trees: ReviewTrees, paths: tuple[ReviewLanePath, ...]) -> None:
    """Every changed path once, with the bucket its own classification gives it: the labels the
    source explorer shows -- the stale file is unknown, the proof-linked test is attributed."""

    buckets = {one.path: one.bucket for one in paths}
    assert buckets == {
        "pkg/a.py": "attributed",
        "pkg/data.bin": "attributed",
        "pkg/inside.py": "attributed",
        "pkg/lines.txt": "attributed",
        "pkg/mode.py": "attributed",
        "pkg/new.py": "unexplained",
        "pkg/stale.py": "attribution_unknown",
        "pkg/unbound.py": "attribution_unknown",
        "pkg/whole.py": "attributed",
        "tests/test_a.py": "attributed",
    }
    for path in ("pkg/stale.py", "tests/test_a.py"):
        assert _file(trees, path).bucket == buckets[path]


def _unexplained_destination(destination: ReviewLaneDestination | None) -> None:
    """The unexplained file first, then attributed files carrying the class -- here the delete-only
    run of lines.txt and the mode-only change the gate holds unexplained."""

    assert _listed(destination) == [
        ("pkg/new.py", "unexplained", 1),
        ("pkg/lines.txt", "attributed", 3),
        ("pkg/mode.py", "attributed", 0),
    ]
    assert destination is not None
    assert (destination.bucket_files, destination.attributed_files, destination.hunks) == (1, 2, 2)
    mode = destination.files[2].non_text
    assert mode is not None
    assert (mode.mode_change, mode.gate) == (True, "unexplained")


def _unknown_destination(destination: ReviewLaneDestination | None) -> None:
    """The stale and the unresolved file with their reasons, then the attributed files whose
    after-side entries have not been re-recorded at the edited blob yet."""

    assert _listed(destination) == [
        ("pkg/stale.py", "attribution_unknown", 1),
        ("pkg/unbound.py", "attribution_unknown", 1),
        ("pkg/a.py", "attributed", 2),
        ("pkg/inside.py", "attributed", 1),
        ("pkg/lines.txt", "attributed", 3),
    ]
    assert destination is not None
    assert destination.hunks == 5
    assert "RLZ-C00002 (recorded_blob_mismatch)" in destination.files[0].reason
    assert "RLZ-C00003 (unresolved)" in destination.files[1].reason


def test_the_entry_count_reads_file_buckets_only(
    lane: Lane, monkeypatch: pytest.MonkeyPatch
) -> None:
    trees = lane.precuration()

    def no_hunks(*_: object) -> None:
        raise AssertionError("the entry count read a hunk")

    monkeypatch.setattr(CodeTrees, "hunks", no_hunks)
    assert lane_summary(trees).unexplained == 1
    with pytest.raises(AssertionError, match="read a hunk"):
        unexplained_lane(trees)


# -- hunks -----------------------------------------------------------------------------------------


def test_hunks_are_classified_on_their_changed_lines_at_each_sides_recorded_blob(
    lane: Lane,
) -> None:
    trees = lane.precuration()
    _precuration_edit_and_insertion(_file(trees, "pkg/a.py"))
    # A line range: the replace inside it links; the adjacent replace is its own hunk and unknown on
    # the after side; the delete-only run is matched on the before side only and is unexplained.
    lines = _file(trees, "pkg/lines.txt")
    assert [_spans(hunk) for hunk in lines.hunks] == [
        ((2, 1), (2, 1), "linked"),
        ((4, 1), (4, 1), "attribution_unknown"),
        ((6, 1), (5, 0), "unexplained"),
    ]
    # An insertion strictly inside f changes no line on the before side, so f's before-side range
    # cannot link it (the gate would call it hit); on the after side no entry supplies a range yet.
    inside = _file(trees, "pkg/inside.py")
    assert [_spans(hunk) for hunk in inside.hunks] == [((2, 0), (3, 1), "attribution_unknown")]
    # A resolving file locator covers every line: no unexplained hunk.
    whole = _file(trees, "pkg/whole.py")
    assert [hunk.classification for hunk in whole.hunks] == ["linked"]
    # After curation (the after side re-recorded at the candidate blob) the insertion is unexplained.
    curated = lane.trees(lane.knowledge(), lane.knowledge(curated=True, families=AFTER_FAMILIES))
    a = _file(curated, "pkg/a.py")
    assert [_spans(hunk) for hunk in a.hunks] == [
        ((2, 1), (2, 1), "linked"),
        ((6, 0), (7, 4), "unexplained"),
    ]
    assert {one.side for one in a.hunks[0].links} == {"before", "after"}


def _precuration_edit_and_insertion(a: ReviewFileClassification) -> None:
    """Before curation: land's body links through the before side; the appended function changes
    lines on the after side only, where no entry supplies a range yet -- attribution unknown."""

    assert [_spans(hunk) for hunk in a.hunks] == [
        ((2, 1), (2, 1), "linked"),
        ((6, 0), (7, 4), "attribution_unknown"),
    ]
    assert [(one.side, one.id, one.start_line, one.end_line) for one in a.hunks[0].links] == [
        ("before", "RLZ-A00001", 1, 2)
    ]
    assert {entry.reason for entry in a.sides[1].entries} == {"recorded_blob_mismatch"}
    unknown = a.hunks[1].unknown
    assert [(one.side, one.entries) for one in unknown] == [("after", ("RLZ-A00001", "RLZ-A00002"))]


def test_a_linked_hunk_names_its_entries_revisions_and_family_occurrences(lane: Lane) -> None:
    trees = lane.precuration()
    link = _file(trees, "pkg/a.py").hunks[0].links[0]
    assert (link.invariant, link.invariant_revision) == (A, 1)
    assert link.invariant_key == text_uuid("identity", A)
    assert link.invariant_revision_key == text_uuid("revision", f"{A}@1")
    assert [(one.family, one.state) for one in link.families] == [
        ("FAM-F00001", "member"),
        ("FAM-F00002", "removed_or_reassigned"),
        ("FAM-F00003", "before_only"),
    ]
    # A proof entry links like a realization entry and is carried as a test, with its facet.
    proof = _file(trees, "tests/test_a.py").hunks[0].links[0]
    assert (proof.kind, proof.id, proof.facet) == ("proof", "PRF-A00004", "land returns its value.")
    # An invariant no family lists, on a side whose family records were all read.
    lines = _file(trees, "pkg/lines.txt").hunks[0].links[0]
    assert [(one.family, one.state) for one in lines.families] == [(None, "confirmed_no_family")]


# -- failure and recovery --------------------------------------------------------------------------


def test_an_unreadable_side_is_never_unexplained_and_the_readable_side_still_links(
    lane: Lane,
) -> None:
    trees = lane.trees(None, lane.knowledge(curated=True, families=AFTER_FAMILIES))
    assert trees.before.database is None
    result = unexplained_lane(trees)
    assert result.unexplained == 0  # no file is unexplained while a side is unread
    new = _file(trees, "pkg/new.py")
    assert (new.bucket, new.sides[0].knowledge) == ("attribution_unknown", "unavailable")
    _readable_side_links(_file(trees, "pkg/a.py"))
    # Changed lines on the unread side are unknown, never unexplained.
    lines = _file(trees, "pkg/lines.txt")
    assert lines.hunks[2].classification == "attribution_unknown"
    assert lines.hunks[2].unknown[0].reason == "knowledge_unavailable"
    # The gate linkage of a non-text change on an unread side is unknown, never unexplained.
    mode = _file(trees, "pkg/mode.py").non_text
    assert mode is not None
    assert mode.gate == "unknown"


def _readable_side_links(a: ReviewFileClassification) -> None:
    """The after side's re-recorded ranges establish attribution; its unread mappings are named."""

    assert a.bucket == "attributed"
    assert "before knowledge was not read" in a.reason
    assert [hunk.classification for hunk in a.hunks] == ["linked", "unexplained"]
    link = a.hunks[0].links[0]
    assert [(one.family, one.state) for one in link.families] == [("FAM-F00001", "member")]


def test_a_partial_index_makes_only_its_unparsed_files_unknown(lane: Lane) -> None:
    unparsed_family = {"knowledge/families/FAM-F00009-broken.json": "{}\n"}
    after = {**lane.knowledge(families=AFTER_FAMILIES), "onboarding/pkg/new.py.json": "{}\n"}
    trees = lane.trees({**lane.knowledge(), **unparsed_family}, after)
    # The after sidecar of new.py did not parse: its after side is unknown, not "no entry".
    new = _file(trees, "pkg/new.py")
    assert new.bucket == "attribution_unknown"
    assert "onboarding/pkg/new.py.json does not parse" in (new.sides[1].detail or "")
    # Every other path is read as before: a.py's attribution is untouched by the unparsed files.
    assert _file(trees, "pkg/a.py").bucket == "attributed"
    # A family record of the before side did not parse: no family can be confirmed there.
    link = _file(trees, "pkg/lines.txt").hunks[0].links[0]
    assert link.side == "before"
    assert [(one.family, one.state) for one in link.families] == [(None, "membership_unknown")]
    # The gate computes nothing over a side it cannot read whole, so the mode-only change's gate
    # linkage is unknown -- listed under Unknown attribution, never as "gate unexplained".
    _gate_unknown_on_a_partial_side(trees)


def _gate_unknown_on_a_partial_side(trees: ReviewTrees) -> None:
    mode = _file(trees, "pkg/mode.py").non_text
    assert mode is not None
    assert mode.gate == "unknown"
    lane = unexplained_lane(trees)
    assert lane.unexplained_changes is not None and lane.unknown_attribution is not None
    assert "pkg/mode.py" not in [one.path for one in lane.unexplained_changes.files]
    assert "pkg/mode.py" in [one.path for one in lane.unknown_attribution.files]


def test_a_reason_names_a_bounded_number_of_entries(lane: Lane) -> None:
    # 300 entries of one path recorded at a blob neither side holds: every one supplies no range.
    stale = [
        _realization(
            f"RLZ-S{number:05d}",
            C,
            {"locator": {"kind": "file"}, "blob": STALE_BLOB, "content": STALE_CONTENT},
        )
        for number in range(300)
    ]
    many = {**lane.knowledge(), **_sidecar("pkg/stale.py", stale)}
    trees = lane.trees(many, many)
    stale_file = _file(trees, "pkg/stale.py")
    assert stale_file.bucket == "attribution_unknown"
    assert "300 entries recorded here supply no range" in stale_file.reason
    assert "and 290 more" in stale_file.reason
    assert stale_file.reason.count("RLZ-S") == 2 * NAMED_ENTRIES  # ten named on each side
    assert stale_file.hunks[0].unknown[0].entries == tuple(one["id"] for one in stale)
    # The lane read that lists the file answers instead of failing on an oversized reason.
    assert unexplained_lane(trees).state == "measured"


def test_an_unmeasured_change_set_has_no_count(lane: Lane, monkeypatch: pytest.MonkeyPatch) -> None:
    # A path whose name is not UTF-8 cannot be carried as text: the inventory is partial.
    undecodable = os.fsdecode(b"pkg/caf\xe9.txt")
    partial = _commit(lane.code.repository, {**CANDIDATE, undecodable: "x\n"})
    trees = lane.trees(lane.knowledge(), lane.knowledge(), code_candidate=partial)
    summary = lane_summary(trees)
    assert summary.state == "partial" and summary.unexplained is None
    assert summary.unmeasured == ("b'pkg/caf\\xe9.txt' (name is not text)",)
    listed = unexplained_lane(trees)
    assert listed.state == "partial" and listed.unmeasured == summary.unmeasured
    # A code tree Git cannot produce: nothing is counted and nothing is listed.
    gone = lane.trees(lane.knowledge(), lane.knowledge(), code_candidate="1" * 40)
    assert lane_summary(gone).state == "unavailable"
    assert unexplained_lane(gone).unexplained_changes is None
    assert classify_changed_path(gone, "pkg/a.py").code == "source_content_unresolved"  # type: ignore[union-attr]
    # An index that fails mid-read: the entry's count is unavailable, never a traceback or a zero.
    readable = lane.precuration()

    def failing(*_: object) -> None:
        raise apsw.IOError("disk I/O error")

    monkeypatch.setattr(KnowledgeIndex, "entries_at_path", failing)
    assert lane_summary(readable).state == "unavailable"


# -- transport and the entry -----------------------------------------------------------------------


def test_the_route_asks_one_focused_question_at_a_time() -> None:
    app = FastAPI()
    seen: list[ReviewTreesQuery] = []

    def port(query: ReviewTreesQuery) -> Any:
        seen.append(query)
        return read_review_trees_stub(query)

    register_review_trees_route(app, port)
    client = TestClient(app)
    common = {"repo": REPO, "master": MASTER, "leaf": LEAF, "comparison": 1}
    assert client.get("/api/review/trees", params={**common, "lane": "files"}).status_code == 200
    assert (seen[-1].lane, seen[-1].file, seen[-1].focused) == (True, None, True)
    assert client.get("/api/review/trees", params={**common, "file": "pkg/a.py"}).status_code == 200
    assert (seen[-1].lane, seen[-1].file) == (False, "pkg/a.py")
    for wrong in (
        {"lane": "all"},
        {"lane": "files", "file": "a"},
        {"invariants": "k", "file": "a"},
    ):
        assert client.get("/api/review/trees", params={**common, **wrong}).status_code == 400
    assert len(seen) == 2


def read_review_trees_stub(query: ReviewTreesQuery) -> Any:
    from agents_remember.models.knowledge.review_trees import ReviewTreesResult  # noqa: PLC0415

    return ReviewTreesResult(
        state="not-converted",
        repository_id=query.repository_id,
        master=query.master,
        leaf_id=query.leaf_id,
    )


def test_a_live_tree_leaf_serves_the_lane_and_its_entry_count(world: World) -> None:  # noqa: F811
    world.edit()
    (world.code_worktree / "pkg" / "fresh.py").write_text("x = 1\n", encoding="utf-8")
    summary = read_review_intent_summary(world.config, REPO, MASTER, LEAF)
    assert summary.counts is not None and summary.attribution is not None
    assert summary.attribution.state == "counted"
    assert (summary.attribution.unexplained, summary.attribution.attributed) == (1, 1)
    query = ReviewTreesQuery(REPO, MASTER, LEAF, number=1, lane=True)
    view = read_review_trees(world.config, query)
    assert view.state == "trees" and view.worklist is None and view.lane is not None
    assert _listed(view.lane.unexplained_changes) == [("pkg/fresh.py", "unexplained", 1)]
    one = read_review_trees(
        world.config, ReviewTreesQuery(REPO, MASTER, LEAF, number=1, file="pkg/a.py")
    )
    assert one.file_classification is not None
    assert [hunk.classification for hunk in one.file_classification.hunks] == ["linked"]
    unchanged = read_review_trees(
        world.config, ReviewTreesQuery(REPO, MASTER, LEAF, number=1, file="pkg/none.py")
    )
    assert unchanged.state == "refused" and unchanged.refusal is not None
    _long_paths_are_typed_refusals(world)


def _long_paths_are_typed_refusals(world: World) -> None:  # noqa: F811
    """Every path the route admits (up to 4,096 characters) answers the typed 200 refusal, its
    offending input clipped to the refusal's own field."""

    app = FastAPI()
    register_review_trees_route(app, lambda query: read_review_trees(world.config, query))
    client = TestClient(app)
    common = {"repo": REPO, "master": MASTER, "leaf": LEAF, "comparison": 1}
    for length in (1025, 4096):
        answer = client.get("/api/review/trees", params={**common, "file": "p" * length})
        assert answer.status_code == 200, length
        body = answer.json()
        assert (body["state"], body["refusal"]["code"]) == ("refused", "source_content_unresolved")
        assert len(body["refusal"]["offending_input"]) == REFERENCE_MAX_LENGTH


def test_a_dataset_review_entry_carries_no_attribution(world: World) -> None:  # noqa: F811
    for repository in (world.memory, world.memory_worktree):
        _git(repository, "rm", "-q", "-r", "knowledge")
        _git(repository, "commit", "-q", "-m", "unconverted")
    world.edit()
    assert read_review_intent_summary(world.config, REPO, MASTER, LEAF).attribution is None


@pytest.fixture(autouse=True)
def _no_bound_services() -> Iterator[None]:
    yield
    reset_worktree_services()
