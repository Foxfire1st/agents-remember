"""MIK-R03@v2: stale invariants flagged at read time, over real Git code and a converted memory tree.

Every case builds a real **code** repository (a module whose four functions each realize their own
invariant, a test module whose tests prove one of them, a text file with a line-range anchor and a
Markdown file) and a converted **memory** tree whose entries are anchored at the code's first
commit. A case then commits code changes the way raw Git would -- outside any managed flow -- and
asks for the state of the invariants at a code tree, through the state function and through the two
read surfaces that attach it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

import pytest
from agents_remember.application import knowledge_currentness as currentness
from agents_remember.application.knowledge_currentness import (
    OBSERVATIONS,
    CodeTree,
    invariant_currentness,
    observation_key,
)
from agents_remember.application.knowledge_currentness import observe as observe_module
from agents_remember.application.knowledge_currentness import surface as surface_module
from agents_remember.application.knowledge_worklist.code import CodeTrees
from agents_remember.application.published_intent import published_intent_block
from agents_remember.kernel.coordination_context.models import CoordinationContext
from agents_remember.mcp.tools.knowledge import ReadToolRequest, knowledge_read_payload
from agents_remember.memory.knowledge.read_anchor_memo import BoundedMemo
from agents_remember.memory.knowledge_index import (
    IndexMismatchError,
    KnowledgeIndex,
    build_index,
    directory_snapshot,
    text_uuid,
)
from agents_remember.models.knowledge_files import canonical_text

ORIGIN = {"task": "260101-OLD", "leaf": "260101-OLD-L1"}
REVIEW = "pkg/review.py"
TESTS = "tests/test_review.py"
LINES = "pkg/lines.txt"
NOTES = "docs/notes.md"

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
TESTS_V1 = """from pkg.review import _not_listed


def test_refuses_unlisted():
    assert _not_listed("b")
"""
LINES_V1 = "".join(f"line {number}\n" for number in range(1, 11))
NOTES_V1 = "# Notes\n\nNothing here.\n"

# entry id -> (invariant, path, locator, list)
ENTRIES: dict[str, tuple[str, str, dict[str, Any], str]] = {
    "RLZ-A00001": ("INV-AAAAAA", REVIEW, {"kind": "symbol", "name": "_not_listed"}, "realizes"),
    "RLZ-B00001": ("INV-BBBBBB", REVIEW, {"kind": "symbol", "name": "other"}, "realizes"),
    "RLZ-C00001": ("INV-CCCCCC", REVIEW, {"kind": "symbol", "name": "third"}, "realizes"),
    "RLZ-D00001": ("INV-DDDDDD", REVIEW, {"kind": "symbol", "name": "fourth"}, "realizes"),
    "RLZ-E00001": ("INV-EEEEEE", LINES, {"kind": "line_range", "start": 3, "end": 5}, "realizes"),
    "RLZ-G00001": ("INV-GGGGGG", LINES, {"kind": "file"}, "realizes"),
    "RLZ-H00001": ("INV-HHHHHH", NOTES, {"kind": "symbol", "name": "Notes"}, "realizes"),
    "RLZ-H00002": ("INV-HHHHHH", REVIEW, {"kind": "symbol", "name": "other"}, "realizes"),
    "PRF-P00001": (
        "INV-AAAAAA",
        TESTS,
        {"kind": "symbol", "name": "test_refuses_unlisted"},
        "proves",
    ),
    "PRF-P00002": (
        "INV-PPPPPP",
        TESTS,
        {"kind": "symbol", "name": "test_refuses_unlisted"},
        "proves",
    ),
}
# INV-NNNNNN has no entry at all; INV-PPPPPP has a proof and no realization.
INVARIANTS = sorted({one[0] for one in ENTRIES.values()} | {"INV-NNNNNN"})
FAMILIES = {"FAM-F00001": ["INV-AAAAAA", "INV-BBBBBB", "INV-CCCCCC", "INV-DDDDDD"]}
NOTES_CONTENT = "sha256:" + "0" * 64


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
    git(root, "config", "user.name", "currentness fixture")


def _commit(root: Path, files: dict[str, str | None]) -> str:
    for relative, content in files.items():
        target = root / relative
        if content is None:
            target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "--allow-empty", "-m", "change")
    return git(root, "rev-parse", "HEAD^{tree}")


def _record(schema: str, record_id: str, **fields: Any) -> str:
    return canonical_text(
        {
            "schema": schema,
            "id": record_id,
            "revision": 1,
            "status": "accepted",
            **fields,
            "admission": "legacy-unassessed",
            "origin": ORIGIN,
        }
    )


def _anchor(code: Path, tree: str, path: str, locator: dict[str, Any]) -> dict[str, Any]:
    trees = CodeTrees.open(code, tree, tree)
    blob = trees.base()[path]
    if path == NOTES:
        return {"locator": locator, "blob": blob, "content": NOTES_CONTENT}
    resolved = trees.resolve(path, locator, blob, blob)
    assert resolved is not None, (path, locator)
    return {"locator": locator, "blob": blob, "content": resolved.content}


def _memory_files(code: Path, tree: str) -> dict[str, str | None]:
    files: dict[str, str | None] = {
        "knowledge/layout.json": canonical_text(
            {"schema": "ar-memory-layout/v2", "conversion": "1"}
        ),
    }
    for one in INVARIANTS:
        files[f"knowledge/invariants/{one}-rule.json"] = _record(
            "ar-invariant/v1",
            one,
            statement=f"{one} holds.",
            applicability="Always.",
            conditions=[],
            exclusions=[],
            supersedes=[],
        )
    for one, members in FAMILIES.items():
        files[f"knowledge/families/{one}-group.json"] = _record(
            "ar-family/v1",
            one,
            title=one,
            guarantee="They hold together.",
            members=members,
            routes=["pkg"],
        )
    sidecars: dict[str, dict[str, Any]] = {}
    for entry_id, (invariant_id, path, locator, key) in sorted(ENTRIES.items()):
        sidecar = sidecars.setdefault(
            path,
            {"schema": "ar-onboarding-file/v1", "path": path, "references": {}, "realizes": []},
        )
        entry: dict[str, Any] = {
            "id": entry_id,
            "invariant": invariant_id,
            "anchor": _anchor(code, tree, path, locator),
        }
        if key == "proves":
            entry["facet"] = "It refuses an unlisted path."
            sidecar.setdefault("proves", []).append(entry)
        else:
            entry |= {"role": "primary-authority", "rationale": "It is the rule."}
            sidecar["realizes"].append(entry)
    files.update({f"onboarding/{path}.json": canonical_text(doc) for path, doc in sidecars.items()})
    return files


class World:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.code = root / "code"
        self.memory = root / "memory"
        _init(self.code)
        self.base = _commit(
            self.code, {REVIEW: REVIEW_V1, TESTS: TESTS_V1, LINES: LINES_V1, NOTES: NOTES_V1}
        )
        _init(self.memory)
        _commit(self.memory, _memory_files(self.code, self.base))

    def change(self, files: dict[str, str | None]) -> CodeTree:
        """Commit a code change outside any managed flow and name its tree."""

        return CodeTree(self.code, _commit(self.code, files))

    def tree(self, tree: str | None = None) -> CodeTree:
        return CodeTree(self.code, tree or self.base)

    def index(self) -> KnowledgeIndex:
        snapshot = directory_snapshot(self.memory)
        destination = self.root / f"index-{snapshot.key}.sqlite"
        if not destination.exists():
            build_index(snapshot, destination)
        return KnowledgeIndex(destination, expected_key=snapshot.key)

    def states(self, code: CodeTree | None, *, cache: Any = None) -> dict[str, Any]:
        with self.index() as index:
            result = invariant_currentness(
                code,
                index,
                INVARIANTS,
                FAMILIES,
                cache=cache if cache is not None else BoundedMemo(1024),
            )
        return {
            "result": result,
            "states": {one.id: one.state for one in result.invariants},
            "entries": {
                entry.entry_id: entry for one in result.invariants for entry in one.entries
            },
        }


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def _body_edit() -> str:
    return REVIEW_V1.replace("return path not in LISTED", "return path not in LISTED or not path")


# --------------------------------------------------------------------------------------------------
# Entry states, invariant states and precedence
# --------------------------------------------------------------------------------------------------


def test_a_raw_git_body_edit_flags_only_its_invariant_and_names_the_entry(world: World) -> None:
    at_base = world.states(world.tree())
    assert at_base["states"] == {
        "INV-AAAAAA": "current",
        "INV-BBBBBB": "current",
        "INV-CCCCCC": "current",
        "INV-DDDDDD": "current",
        "INV-EEEEEE": "current",
        "INV-GGGGGG": "current",
        "INV-HHHHHH": "current",  # its Markdown symbol's blob is unchanged: current (ruling N1)
        "INV-NNNNNN": "unrealized",
        "INV-PPPPPP": "unrealized",
    }

    edited = world.states(world.change({REVIEW: _body_edit()}))
    states = edited["states"]
    # Conforming: the edited function's invariant is stale; the file's other three stay current.
    assert states["INV-AAAAAA"] == "stale"
    assert [states[one] for one in ("INV-BBBBBB", "INV-CCCCCC", "INV-DDDDDD")] == ["current"] * 3
    stale = edited["result"].of("INV-AAAAAA")
    assert [entry.entry_id for entry in stale.differing] == ["RLZ-A00001"]
    named = stale.to_document()["entries"][0]
    assert named["state"] == "stale"
    assert named["reason"] == "the range content differs from the recorded content"
    assert named["observedContent"] != named["recordedContent"]
    assert named["observedBlob"] != named["recordedBlob"]
    # The other three were observed in a changed blob and still matched: current, never "stale".
    assert edited["entries"]["RLZ-B00001"].observed_content == (
        edited["entries"]["RLZ-B00001"].recorded_content
    )
    counts = edited["result"].counts()
    assert counts == {"stale": 1, "unverifiable": 0, "unrealized": 2, "current": 6}
    family = edited["result"].families[0].to_document()
    assert family == {"id": "FAM-F00001", "members": 4, "staleMembers": 1, "stale": ["INV-AAAAAA"]}


def test_stale_covers_an_absent_path_an_ambiguous_symbol_and_an_unmappable_range(
    world: World,
) -> None:
    moved = LINES_V1.replace("line 1\n", "line 0\nline 1\n")  # lines 3-5 move to 4-6 unchanged
    assert world.states(world.change({LINES: moved}))["states"]["INV-EEEEEE"] == "current"
    at_base = world.states(world.tree())["result"]
    assert [one.id for one in at_base.invariants if one.differing] == []

    tree = world.change(
        {
            REVIEW: REVIEW_V1 + "\n\ndef third(value):\n    return value\n",
            LINES: "".join(f"line {n}\n" for n in (1, 2, 6, 7, 8, 9, 10)),
        }
    )
    observed = world.states(tree)
    assert observed["states"]["INV-CCCCCC"] == "stale"
    assert observed["entries"]["RLZ-C00001"].reason == (
        "the symbol does not resolve uniquely at the code tree"
    )
    assert observed["states"]["INV-EEEEEE"] == "stale"
    assert observed["entries"]["RLZ-E00001"].reason == (
        "the line range has no mapping to the code tree"
    )
    assert observed["states"]["INV-GGGGGG"] == "stale"  # the whole file changed

    gone = world.states(world.change({REVIEW: None}))
    assert gone["states"]["INV-BBBBBB"] == "stale"
    assert gone["entries"]["RLZ-B00001"].reason == f"the path {REVIEW!r} is absent at the code tree"
    assert gone["entries"]["RLZ-B00001"].observed_blob is None


def test_unverifiable_names_why_for_no_tree_an_unreadable_tree_and_an_unsupported_locator(
    world: World,
) -> None:
    # Boundary: a read with no code tree shows every realized invariant unverifiable.
    no_tree = world.states(None)
    assert set(no_tree["states"].values()) == {"unverifiable", "unrealized"}
    assert no_tree["states"]["INV-AAAAAA"] == "unverifiable"
    assert no_tree["result"].problem == "no code tree was requested"
    assert no_tree["result"].to_document()["codeTree"] is None
    assert {entry.reason for entry in no_tree["entries"].values()} == {"no code tree was requested"}

    missing = world.states(world.tree("f" * 40))
    assert missing["states"]["INV-AAAAAA"] == "unverifiable"
    assert "cannot be read" in (missing["result"].problem or "")

    # An unsupported locator (a symbol in Markdown) follows the packet table's order (ruling N1):
    # (b) its blob unchanged -> current, whatever the kind ...
    unchanged = world.states(world.tree())["entries"]["RLZ-H00001"]
    assert (unchanged.state, unchanged.reason) == ("current", None)
    # (c) its blob changed and the kind cannot be re-resolved -> unverifiable, naming why ...
    changed = world.states(world.change({NOTES: NOTES_V1 + "More.\n"}))["entries"]["RLZ-H00001"]
    assert changed.state == "unverifiable"
    assert "no shipped grammar reads it" in (changed.reason or "")
    # (a) its path absent at the tree -> stale.
    gone = world.states(world.change({NOTES: None}))["entries"]["RLZ-H00001"]
    assert (gone.state, gone.reason) == ("stale", f"the path {NOTES!r} is absent at the code tree")


def test_a_failed_or_timed_out_git_call_is_unverifiable_with_its_reason(world: World) -> None:
    tree = world.change({REVIEW: _body_edit()})
    timeout = subprocess.TimeoutExpired(["git", "diff"], 20)
    with mock.patch.object(CodeTrees, "resolve", side_effect=timeout):
        observed = world.states(tree)
    entry = observed["entries"]["RLZ-A00001"]
    assert entry.state == "unverifiable"
    assert entry.reason == f"a Git read failed (TimeoutExpired: {timeout})"
    assert observed["states"]["INV-AAAAAA"] == "unverifiable"
    # Nothing failed is remembered: the next read observes again and finds the edit.
    assert world.states(tree)["states"]["INV-AAAAAA"] == "stale"


def test_a_line_range_recorded_against_a_blob_the_store_lacks_is_unverifiable(
    world: World,
) -> None:
    tree = world.change({LINES: LINES_V1 + "line 11\n"})
    with world.index() as index:
        entry = index.invariant("INV-EEEEEE").value.realizations[0]
    lost = type(entry)(
        **{
            **entry.__dict__,
            "document": {
                **entry.document,
                "anchor": {**entry.document["anchor"], "blob": "e" * 40},
            },
        }
    )
    observed = currentness.observe_entry(
        lost, currentness.open_code_tree(tree), cache=BoundedMemo(8)
    )
    assert observed.state == "unverifiable"
    assert f"the blob {'e' * 40} the line range was recorded against is unavailable" == (
        observed.reason
    )


def test_precedence_stale_before_unverifiable_before_unrealized(world: World) -> None:
    # INV-HHHHHH: its Markdown entry changed (unverifiable) and its Python entry is current.
    notes = world.change({NOTES: NOTES_V1 + "More.\n"})
    assert world.states(notes)["states"]["INV-HHHHHH"] == "unverifiable"
    # Its current entry turning stale wins over the unverifiable one.
    tree = world.change({REVIEW: REVIEW_V1.replace("return value + 1", "return value + 2")})
    both = world.states(tree)
    assert both["entries"]["RLZ-H00001"].state == "unverifiable"
    assert both["states"]["INV-HHHHHH"] == "stale"
    # A proof alone never realizes: INV-PPPPPP is unrealized while its proof is current ...
    assert world.states(world.tree())["states"]["INV-PPPPPP"] == "unrealized"
    # ... and an invariant with no entry at all is unrealized too.
    assert world.states(tree)["states"]["INV-NNNNNN"] == "unrealized"
    assert currentness.invariant_state(()) == "unrealized"


def test_a_proof_whose_test_changed_or_disappeared_is_flagged_stale(world: World) -> None:
    """Carried from L28: a proof is flagged stale at read time exactly like a realization."""

    changed = world.states(
        world.change({TESTS: TESTS_V1.replace('_not_listed("b")', '_not_listed("c")')})
    )
    # INV-AAAAAA's realization is current; its proof's test body changed: stale.
    assert changed["entries"]["RLZ-A00001"].state == "current"
    assert changed["entries"]["PRF-P00001"].state == "stale"
    assert changed["entries"]["PRF-P00001"].kind == "proof"
    assert changed["states"]["INV-AAAAAA"] == "stale"
    # A proof-only invariant whose proof went stale is stale, not unrealized (rule 1 first).
    assert changed["states"]["INV-PPPPPP"] == "stale"

    removed = world.states(world.change({TESTS: "from pkg.review import _not_listed\n"}))
    assert removed["entries"]["PRF-P00001"].reason == (
        "the symbol does not resolve uniquely at the code tree"
    )
    assert removed["states"]["INV-AAAAAA"] == "stale"


def test_one_function_computes_each_side_of_a_comparison(world: World) -> None:
    base = world.tree()
    candidate = world.change({REVIEW: _body_edit()})
    with world.index() as index:
        sides = {
            label: invariant_currentness(tree, index, ["INV-AAAAAA", "INV-BBBBBB"])
            for label, tree in (("base", base), ("candidate", candidate))
        }
    assert {one.id: one.state for one in sides["base"].invariants} == {
        "INV-AAAAAA": "current",
        "INV-BBBBBB": "current",
    }
    assert {one.id: one.state for one in sides["candidate"].invariants} == {
        "INV-AAAAAA": "stale",
        "INV-BBBBBB": "current",
    }
    assert sides["candidate"].to_document()["codeTree"] == {
        "repositoryRoot": str(world.code),
        "treeId": candidate.tree,
    }


# --------------------------------------------------------------------------------------------------
# The observation cache
# --------------------------------------------------------------------------------------------------


def test_observations_are_keyed_by_blob_locator_and_extractor_version_and_reused(
    world: World,
) -> None:
    symbol = {"kind": "symbol", "name": "other"}
    key = observation_key("b" * 40, REVIEW, symbol, "a" * 40)
    assert key == (
        "b" * 40,
        '{"kind":"symbol","name":"other"}',
        REVIEW,
        "",
        currentness.EXTRACTOR_VERSION,
    )
    assert "tree-sitter=" in currentness.EXTRACTOR_VERSION
    lines = {"kind": "line_range", "start": 3, "end": 5}
    assert observation_key("b" * 40, LINES, lines, "a" * 40)[3] == "a" * 40
    assert observation_key("b" * 40, LINES, lines, "a" * 40) != observation_key(
        "b" * 40, LINES, lines, "c" * 40
    )
    assert OBSERVATIONS is observe_module.OBSERVATIONS

    tree = world.change({REVIEW: _body_edit()})
    cache: BoundedMemo[Any, Any] = BoundedMemo(1024)
    first = world.states(tree, cache=cache)
    assert len(cache) == 4  # the four symbols observed in the changed blob; others unchanged
    with mock.patch.object(CodeTrees, "resolve", side_effect=AssertionError("not reused")):
        second = world.states(tree, cache=cache)
    assert second["states"] == first["states"]
    # A different extractor version is a different key: nothing is reused across versions.
    with (
        mock.patch.object(observe_module, "EXTRACTOR_VERSION", "anchor-observation/v2"),
        mock.patch.object(CodeTrees, "resolve", side_effect=AssertionError("resolved")),
        pytest.raises(AssertionError, match="resolved"),
    ):
        world.states(tree, cache=cache)


# --------------------------------------------------------------------------------------------------
# The read surfaces
# --------------------------------------------------------------------------------------------------


def _read(world: World, view: str, **fields: Any) -> dict[str, Any]:
    return knowledge_read_payload(
        ReadToolRequest(
            memory_root=str(world.memory),
            view=view,
            **fields,
        ),
        workspace_root=str(world.code),
        coordination_root=str(world.root / "coordination"),
    )


def test_knowledge_read_flags_a_stale_invariant_and_keeps_it_visible(world: World) -> None:
    tree = world.change({REVIEW: _body_edit()})
    family = _read(
        world,
        "family",
        family_revision_id=text_uuid("revision", "FAM-F00001@1"),
        code_tree_id=tree.tree,
        repository_root=str(world.code),
    )
    assert family["state"] == "view", family
    block = family["currentness"]
    assert block["codeTree"] == {"repositoryRoot": str(world.code), "treeId": tree.tree}
    assert block["families"] == [
        {"id": "FAM-F00001", "members": 4, "staleMembers": 1, "stale": ["INV-AAAAAA"]}
    ]
    assert {one["id"]: one["state"] for one in block["invariants"]} == {
        "INV-AAAAAA": "stale",
        "INV-BBBBBB": "current",
        "INV-CCCCCC": "current",
        "INV-DDDDDD": "current",
    }
    assert block["counts"] == {"stale": 1, "unverifiable": 0, "unrealized": 0, "current": 3}
    # Nothing is withheld: the stale invariant's realization is still a row of the view.
    names = [(row.get("locator") or {}).get("qualified_name") for row in family["payload"]["rows"]]
    assert "_not_listed" in names

    invariant = _read(
        world,
        "invariant",
        invariant_revision_id=text_uuid("revision", "INV-AAAAAA@1"),
        code_tree_id=tree.tree,
    )  # a tree named without a repository is read from the mount's workspace store
    (stale,) = invariant["currentness"]["invariants"]
    assert stale["state"] == "stale"
    assert [entry["id"] for entry in stale["entries"]] == ["RLZ-A00001"]
    assert "INV-AAAAAA holds." in str(invariant["payload"])


def test_knowledge_read_without_a_named_tree_is_unverifiable_and_never_reads_head(
    world: World,
) -> None:
    world.change({REVIEW: _body_edit()})  # HEAD now holds the edit
    for fields in ({}, {"repository_root": str(world.code)}):
        response = _read(
            world,
            "invariant",
            invariant_revision_id=text_uuid("revision", "INV-AAAAAA@1"),
            **fields,
        )
        block = response["currentness"]
        assert block["codeTree"] is None
        assert block["unverifiableReason"] == "no code tree was requested"
        assert [one["state"] for one in block["invariants"]] == ["unverifiable"]
        # One read-wide reason explains every entry, so each is named compactly (review N4).
        assert block["invariants"][0]["entries"] == [
            {"id": "RLZ-A00001", "kind": "realization", "path": REVIEW, "state": "unverifiable"},
            {"id": "PRF-P00001", "kind": "proof", "path": TESTS, "state": "unverifiable"},
        ]


def test_the_published_intent_block_flags_returned_invariants_at_its_resolved_tree(
    world: World,
) -> None:
    world.change({REVIEW: _body_edit()})
    context = cast(
        CoordinationContext,
        SimpleNamespace(
            memory_root=world.memory,
            coordination_root=world.root / "coordination",
            code_repository_root=world.code,
            code_repository_name="agents-remember",
        ),
    )
    block = published_intent_block(context, [REVIEW])
    assert block["state"] == "recorded", block
    head = git(world.code, "rev-parse", "HEAD^{tree}")
    assert block["sourceResolution"]["codeTreeId"] == head
    flagged = block["currentness"]
    assert flagged["codeTree"]["treeId"] == head
    assert flagged["treeScope"] == (
        f"states observed at the committed code tree {head} this read resolved; uncommitted "
        "working-tree edits are not reflected"
    )
    # An uncommitted edit of another function is not reflected: the committed tree is observed.
    (world.code / REVIEW).write_text(
        _body_edit().replace("return value * 2", "return value * 3"), encoding="utf-8"
    )
    dirty = published_intent_block(context, [REVIEW])["currentness"]
    assert dirty["codeTree"]["treeId"] == head
    assert {one["id"]: one["state"] for one in dirty["invariants"]} == {
        one["id"]: one["state"] for one in flagged["invariants"]
    }
    states = {one["id"]: one["state"] for one in flagged["invariants"]}
    assert states["INV-AAAAAA"] == "stale"
    assert states["INV-BBBBBB"] == "current"
    assert flagged["counts"]["stale"] >= 1
    # The page is unchanged: the stale invariant's statement is still returned.
    assert "INV-AAAAAA holds." in str(block["seeds"])

    unresolved = cast(
        CoordinationContext, SimpleNamespace(**{**context.__dict__, "code_repository_root": None})
    )
    none = published_intent_block(unresolved, [REVIEW])["currentness"]
    assert none["codeTree"] is None
    assert "treeScope" not in none
    assert none["unverifiableReason"] == "no code tree was requested"
    assert {one["state"] for one in none["invariants"]} <= {"unverifiable", "unrealized"}
    assert "stale" not in {one["state"] for one in none["invariants"]}


def test_a_failing_currentness_step_degrades_to_a_reason_and_never_refuses_the_read(
    world: World,
) -> None:
    tree = world.change({REVIEW: _body_edit()})
    failure = IndexMismatchError("the index was rebuilt for another key")
    with mock.patch.object(surface_module, "KnowledgeIndex", side_effect=failure):
        response = _read(
            world,
            "invariant",
            invariant_revision_id=text_uuid("revision", "INV-AAAAAA@1"),
            code_tree_id=tree.tree,
        )
    assert response["state"] == "view", response
    assert "INV-AAAAAA holds." in str(response["payload"])
    assert response["currentness"] == {
        "codeTree": tree.to_document(),
        "counts": {"stale": 0, "unverifiable": 0, "unrealized": 0, "current": 0},
        "invariants": [],
        "families": [],
        "unverifiableReason": (
            "currentness could not be computed (IndexMismatchError: the index was rebuilt for "
            "another key)"
        ),
    }
