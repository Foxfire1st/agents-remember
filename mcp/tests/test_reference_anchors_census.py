"""MIK-R198 rules 1 and 2: the reference census, its classes and the repair worklist.

The census is the one reproducible measurement the leaf reports and the curator's work order, so the
tests pin its classification exactly: a stale blob whose bytes hold, a uniquely moved range, and the
three judgment shapes (ambiguous, no identical range, unresolved or absent) that the mechanical fixer
must refuse. Every case resolves through the product's own anchor resolver.
"""

from __future__ import annotations

from typing import Any

from agents_remember.memory_quality.knowledge_validator import CodePathSet, KnowledgeTree
from agents_remember.memory_quality.reference_anchors import (
    CURRENT,
    MOVED_RANGE,
    REPAIR_BLOB,
    REPAIR_JUDGMENT,
    REPAIR_NONE,
    REPAIR_RANGE,
    STALE_AMBIGUOUS,
    STALE_BLOB,
    STALE_CONTENT,
    STALE_PATH_ABSENT,
    STALE_UNRESOLVED,
    census,
    census_for_memory,
    observations,
)
from agents_remember.memory_quality.reference_state import git_blob_id, located, refreshed_anchor
from agents_remember.models.knowledge_files.anchor_content import content_identity
from knowledge_validator_test_support import encode, tree

SOURCE = "pkg/module.py"
OTHER = "pkg/other.py"
SOURCE_BYTES = (
    b"def alpha():\n"
    b"    return 1\n"
    b"\n"
    b"\n"
    b"def beta():\n"
    b"    return 2\n"
    b"\n"
    b"\n"
    b"marker = 'twice'\n"
    b"marker = 'twice'\n"
)


def _identity(locator: dict[str, Any], data: bytes = SOURCE_BYTES, path: str = SOURCE) -> str:
    found = located(path, data, locator)
    assert found is not None
    return content_identity(found)


def _anchor(
    locator: dict[str, Any],
    *,
    blob: str | None = None,
    content: str | None = None,
    data: bytes = SOURCE_BYTES,
    path: str = SOURCE,
) -> dict[str, Any]:
    anchor: dict[str, Any] = {
        "locator": locator,
        "blob": blob if blob is not None else git_blob_id(data),
        "content": content if content is not None else _identity(locator, data, path),
    }
    return anchor


def _memtree(anchor: dict[str, Any], *, own: str = SOURCE, kind: str = "code") -> KnowledgeTree:
    """One converted card whose reference 1 carries ``anchor`` (own file omits ``path``)."""

    document = {
        "schema": "ar-onboarding-file/v1",
        "path": own,
        "references": {"1": {"targets": [{"kind": kind, "anchor": anchor}]}},
        "realizes": [],
    }
    return tree(
        {
            f"onboarding/{own}.json": encode(document),
            f"onboarding/{own}.md": b"# module.py\n\nSee [1].\n",
        }
    )


def _code(**contents: bytes) -> CodePathSet:
    return CodePathSet(label="code pinned", paths=frozenset(contents), contents=dict(contents))


def _only(tree_: KnowledgeTree, code: CodePathSet):
    (observation,) = observations(tree_, code)
    return observation


def test_a_current_anchor_is_the_only_zero_debt_state() -> None:
    anchor = _anchor({"kind": "symbol", "name": "alpha"})

    observation = _only(_memtree(anchor), _code(**{SOURCE: SOURCE_BYTES}))

    assert (observation.state, observation.repair) == (CURRENT, REPAIR_NONE)
    result = census(_memtree(anchor), _code(**{SOURCE: SOURCE_BYTES}))
    assert result["ok"] and result["debtCount"] == 0 and result["denominator"] == 1


def test_a_stale_blob_with_the_recorded_bytes_is_class_a() -> None:
    anchor = _anchor({"kind": "symbol", "name": "alpha"}, blob="0" * 40)

    observation = _only(_memtree(anchor), _code(**{SOURCE: SOURCE_BYTES}))
    result = census(_memtree(anchor), _code(**{SOURCE: SOURCE_BYTES}))

    assert (observation.state, observation.repair) == (STALE_BLOB, REPAIR_BLOB)
    assert result["documents"] == ["pkg/module.py.md"]
    assert result["judgmentDocuments"] == [] and result["ok"] is False


def test_a_uniquely_moved_line_range_is_class_b() -> None:
    """A moved range is mechanical only where the shipped rule re-records it (MIK-R198 rule 2)."""

    anchor = _anchor(
        {"kind": "line_range", "start": 1, "end": 1},
        blob="0" * 40,
        content=_identity({"kind": "line_range", "start": 6, "end": 6}),
    )

    observation = _only(_memtree(anchor), _code(**{SOURCE: SOURCE_BYTES}))

    assert (observation.state, observation.repair) == (MOVED_RANGE, REPAIR_RANGE)
    assert "re-records it at line_range:6-6" in observation.reason


def test_a_same_blob_anchor_with_a_wrong_locator_is_judgment_because_the_fixer_refuses() -> None:
    """The fixer refreshes no anchor whose blob already matches, so the census cannot promise one."""

    anchor = _anchor(
        {"kind": "line_range", "start": 1, "end": 1},
        content=_identity({"kind": "line_range", "start": 6, "end": 6}),
    )
    assert anchor["blob"] == git_blob_id(SOURCE_BYTES), "this case needs the current blob"

    observation = _only(_memtree(anchor), _code(**{SOURCE: SOURCE_BYTES}))

    assert (observation.state, observation.repair) == (STALE_CONTENT, REPAIR_JUDGMENT)
    assert "blob already matches" in observation.reason
    assert refreshed_anchor(SOURCE, anchor, SOURCE_BYTES) is None


def test_an_old_range_past_the_shortened_file_is_class_b_when_the_bytes_are_unique() -> None:
    """A recorded range beyond the file still re-finds its unique range, as the fixer does."""

    anchor = _anchor(
        {"kind": "line_range", "start": 40, "end": 40},
        blob="0" * 40,
        content=_identity({"kind": "line_range", "start": 6, "end": 6}),
    )

    observation = _only(_memtree(anchor), _code(**{SOURCE: SOURCE_BYTES}))

    assert (observation.state, observation.repair) == (MOVED_RANGE, REPAIR_RANGE)
    assert refreshed_anchor(SOURCE, anchor, SOURCE_BYTES) is not None


def test_every_row_carries_the_recorded_content_identity_it_was_written_against() -> None:
    """Carried finding L191-PRE-02: a worklist row must reproduce the recorded identity, not a blob."""

    anchor = _anchor({"kind": "symbol", "name": "alpha"}, blob="0" * 40)
    result = census(_memtree(anchor), _code(**{SOURCE: SOURCE_BYTES}))
    [row] = result["targets"]

    assert row["recordedContent"] == anchor["content"]
    assert row["actualContent"] == _identity({"kind": "symbol", "name": "alpha"})
    assert row["recordedBlob"] == "0" * 40 and row["actualBlob"] == git_blob_id(SOURCE_BYTES)


def test_the_census_reconciles_its_targets_by_source_route() -> None:
    """Carried finding L191-PRE-02: rule 1's source-route totals, reconciling to the denominator."""

    document = {
        "schema": "ar-onboarding-file/v1",
        "path": SOURCE,
        "references": {
            "1": {
                "targets": [
                    {
                        "kind": "code",
                        "anchor": _anchor({"kind": "symbol", "name": "alpha"}, blob="0" * 40),
                    }
                ]
            },
            "2": {
                "targets": [
                    {
                        "kind": "code",
                        "anchor": _anchor({"kind": "symbol", "name": "beta"}, blob="0" * 40),
                    }
                ]
            },
        },
        "realizes": [],
    }
    memory = tree(
        {
            f"onboarding/{SOURCE}.json": encode(document),
            f"onboarding/{SOURCE}.md": b"# module.py\n\nSee [1] and [2].\n",
        }
    )
    result = census(memory, _code(**{SOURCE: SOURCE_BYTES}))

    assert result["routeCount"] == len(result["routes"]) == 1
    route = SOURCE.split("/", maxsplit=1)[0]
    assert result["routes"][route]["targets"] == 2
    assert result["routes"][route]["states"] == {"stale:blob": 2}
    assert sum(entry["targets"] for entry in result["routes"].values()) == result["denominator"]


def test_mechanical_repair_is_claimed_exactly_where_the_shipped_fixer_repairs() -> None:
    """Parity, both ways: no promise the fixer cannot keep, nothing repairable sent to judgment."""

    cases = [
        _anchor({"kind": "symbol", "name": "alpha"}, blob="0" * 40),
        _anchor(
            {"kind": "line_range", "start": 1, "end": 1},
            blob="0" * 40,
            content=_identity({"kind": "line_range", "start": 6, "end": 6}),
        ),
        _anchor(
            {"kind": "line_range", "start": 1, "end": 1},
            content=_identity({"kind": "line_range", "start": 6, "end": 6}),
        ),
        _anchor(
            {"kind": "line_range", "start": 1, "end": 1},
            content=_identity({"kind": "line_range", "start": 9, "end": 9}),
        ),
        _anchor(
            {"kind": "line_range", "start": 40, "end": 40},
            blob="0" * 40,
            content=_identity({"kind": "line_range", "start": 6, "end": 6}),
        ),
    ]
    data = SOURCE_BYTES + b"marker = 'twice'\n"
    for anchor in cases:
        observation = _only(_memtree(anchor), _code(**{SOURCE: data}))
        mechanical = observation.repair in {REPAIR_BLOB, REPAIR_RANGE}
        assert mechanical == (refreshed_anchor(SOURCE, anchor, data) is not None), observation


def test_a_line_range_that_appears_twice_is_ambiguous_and_needs_judgment() -> None:
    anchor = _anchor(
        {"kind": "line_range", "start": 1, "end": 1},
        content=_identity({"kind": "line_range", "start": 9, "end": 9}),
    )

    observation = _only(_memtree(anchor), _code(**{SOURCE: SOURCE_BYTES + b"marker = 'twice'\n"}))

    assert (observation.state, observation.repair) == (STALE_AMBIGUOUS, REPAIR_JUDGMENT)
    assert "3 identical same-length ranges" in observation.reason


def test_changed_bytes_with_no_identical_range_need_judgment() -> None:
    anchor = _anchor(
        {"kind": "line_range", "start": 1, "end": 1},
        content="sha256:" + "0" * 64,
    )

    observation = _only(_memtree(anchor), _code(**{SOURCE: SOURCE_BYTES}))

    assert (observation.state, observation.repair) == (STALE_CONTENT, REPAIR_JUDGMENT)
    assert "no identical same-length line range" in observation.reason


def test_a_whole_file_anchor_whose_content_changed_needs_judgment() -> None:
    anchor = _anchor({"kind": "file"}, content="sha256:" + "0" * 64)

    observation = _only(_memtree(anchor), _code(**{SOURCE: SOURCE_BYTES}))

    assert (observation.state, observation.repair) == (STALE_CONTENT, REPAIR_JUDGMENT)
    assert "whole-file content identity differs" in observation.reason


def test_an_unresolved_symbol_and_an_absent_path_are_named_distinctly() -> None:
    broken = {"blob": "0" * 40, "content": "sha256:" + "0" * 64}
    unresolved = {
        **_anchor({"kind": "symbol", "name": "alpha"}),
        **broken,
        "locator": {"kind": "symbol", "name": "gone"},
    }
    absent = {**_anchor({"kind": "file"}), "path": OTHER}

    assert _only(_memtree(unresolved), _code(**{SOURCE: SOURCE_BYTES})).state == STALE_UNRESOLVED
    observation = _only(_memtree(absent), _code(**{SOURCE: SOURCE_BYTES}))

    assert (observation.state, observation.repair) == (STALE_PATH_ABSENT, REPAIR_JUDGMENT)


def test_an_unreadable_sidecar_is_named_and_prevents_a_pass() -> None:
    files = {
        "onboarding/pkg/module.py.json": b"{not json",
        "onboarding/pkg/module.py.md": b"# module.py\n",
    }

    result = census(tree(files), _code(**{SOURCE: SOURCE_BYTES}))

    assert result["ok"] is False
    assert result["unreadableSidecars"] == ["onboarding/pkg/module.py.json"]


def test_the_census_is_reproducible() -> None:
    anchor = _anchor({"kind": "symbol", "name": "alpha"}, blob="0" * 40)
    code = _code(**{SOURCE: SOURCE_BYTES})

    assert census(_memtree(anchor), code) == census(_memtree(anchor), code)


def test_a_test_target_is_measured_like_a_code_target() -> None:
    anchor = _anchor({"kind": "symbol", "name": "beta"})

    observation = _only(_memtree(anchor, kind="test"), _code(**{SOURCE: SOURCE_BYTES}))

    assert (observation.kind, observation.state) == ("test", CURRENT)


def test_the_directory_census_measures_a_working_tree_pair(tmp_path: Any) -> None:
    """The CLI's engine: a memory directory against a code directory, nothing else needed."""

    anchor = _anchor({"kind": "symbol", "name": "alpha"}, blob="0" * 40)
    for path, data in _memtree(anchor).files.items():
        target = tmp_path / "memory" / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    code_root = tmp_path / "code" / SOURCE
    code_root.parent.mkdir(parents=True, exist_ok=True)
    code_root.write_bytes(SOURCE_BYTES)

    result = census_for_memory(tmp_path / "memory", tmp_path / "code")

    assert result["denominator"] == 1 and result["debtCount"] == 1
    assert result["documents"] == ["pkg/module.py.md"]
    assert result["pair"]["memory"] == (tmp_path / "memory").as_posix()


def test_a_sidecar_the_parser_refused_is_named_and_the_census_is_not_complete() -> None:
    """A readable sidecar rejected only for its location may not vanish from a complete census.

    ``onboarding/pkg/wrong.py.json`` declares ``pkg/module.py``. It parses, it holds a target, and the
    tree parser refuses it because a sidecar lives at its own declaration's path. Enumerating only the
    parser's sidecars dropped it: the answer was ``ok: true`` over a denominator of zero while readable
    evidence sat in the tree. Rule 1 asks for the input to be named as incomplete instead.
    """

    document = {
        "schema": "ar-onboarding-file/v1",
        "path": SOURCE,
        "references": {
            "1": {
                "targets": [
                    {
                        "kind": "code",
                        "anchor": _anchor({"kind": "symbol", "name": "alpha"}, blob="0" * 40),
                    }
                ]
            }
        },
        "realizes": [],
    }
    misplaced = tree(
        {
            "onboarding/pkg/wrong.py.json": encode(document),
            "onboarding/pkg/wrong.py.md": b"# module.py\n\nSee [1].\n",
        }
    )

    result = census(misplaced, _code(**{SOURCE: SOURCE_BYTES}))

    assert result["rejectedSidecars"] == ["onboarding/pkg/wrong.py.json"]
    assert result["invalidInputCount"] == 1
    assert result["ok"] is False, "an incomplete input may not be reported as a complete census"
    assert result["unreadableSidecars"] == [], (
        "the file is readable; it is the location that was refused"
    )


def test_an_accepted_sidecar_cannot_alias_a_rejected_sidecar_out_of_the_accounting() -> None:
    """A declared path may not stand in for a physical one when the parser refuses a file.

    The parser reports a location problem *at the physical path* of the file it refused. Accounting
    that compared those problems against the accepted sidecars' *declared* source paths let a second,
    legal sidecar hide the first one simply by declaring that path as its own source: the census went
    back to ``ok: true`` over a denominator of zero. Accounting now compares physical paths, so the
    refused file stays named no matter what any other sidecar declares.
    """

    rejected = {
        "schema": "ar-onboarding-file/v1",
        "path": SOURCE,
        "references": {
            "1": {
                "targets": [
                    {"kind": "code", "anchor": _anchor({"kind": "symbol", "name": "alpha"})}
                ]
            }
        },
        "realizes": [],
    }
    # Legal, empty, and its declared source is exactly the rejected file's tree path.
    alias = {
        "schema": "ar-onboarding-file/v1",
        "path": "onboarding/pkg/wrong.py.json",
        "references": {},
        "realizes": [],
    }
    tree_with_alias = tree(
        {
            "onboarding/pkg/wrong.py.json": encode(rejected),
            "onboarding/pkg/wrong.py.md": b"# module.py\n\nSee [1].\n",
            "onboarding/onboarding/pkg/wrong.py.json.json": encode(alias),
        }
    )

    result = census(tree_with_alias, _code(**{SOURCE: SOURCE_BYTES}))

    assert result["rejectedSidecars"] == ["onboarding/pkg/wrong.py.json"]
    assert result["invalidInputCount"] >= 1
    assert result["ok"] is False, "an aliasing declaration must not restore a complete answer"
