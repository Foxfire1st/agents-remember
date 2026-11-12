"""MIK-R74 rule 6: independent oracle acceptance controls.

Split whole from ``test_knowledge_read_boundary.py`` under the Owner line-bound
disposition: pure unit cases over hand-built world states plus real-git witnesses,
every assertion preserved; only the address changes. No product read runs here, so
each control fails the oracle regardless of any read's success or refusal.
"""

from __future__ import annotations

import json
from pathlib import Path

from read_boundary_oracle_test_support import (
    _bare_repo,
    _classify,
    _GitState,
    _snapshot,
    _WorldState,
)
from test_source_lineage import _git as git

# --- independent oracle controls ----------------------------------------------------------
# Pure unit cases over hand-built world states: no product read runs, so each fails the oracle
# regardless of any read's success or refusal. They pin the three false-clean inputs shut.


def test_a_real_unauthorized_object_write_fails_the_oracle(tmp_path: Path) -> None:
    """A genuine object no review captured fails, with no product read running.

    ``git hash-object -w`` writes a real valid loose object into a real repository;
    no review compared it and no record attests it. This is the actual unauthorized
    object-write witness: the oracle fails it without any read succeeding or refusing.
    """

    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    before = _snapshot({"repo": repo})
    blob = repo / "foreign.txt"
    blob.write_text("an object no review touched\n", encoding="utf-8")
    sha = git(repo, "hash-object", "-w", str(blob)).strip()
    blob.unlink()
    findings = _classify(
        before,
        _snapshot({"repo": repo}),
        coordination_roots=("coord",),
        reviewed_prefixes=("repo",),
    )
    assert (
        f"object changed with no attributed comparison: repo/.git/objects/{sha[:2]}/{sha[2:]}"
        in findings
    ), findings


def test_borrowed_alternate_objects_answer_to_the_same_attribution(tmp_path: Path) -> None:
    """An external alternates store is inventoried; its objects need trees too.

    Repository B borrows A's object store through a real alternates pointer outside
    every snapshot root. A borrowed object resolving inside a declared compared tree
    is clean; the pointer file appearing unattributed, and a borrowed object no tree
    holds, are findings. No product read runs.
    """

    lender = tmp_path / "lender"
    lender.mkdir()
    git(lender, "init", "-q")
    (lender / "kept.txt").write_text("kept\n", encoding="utf-8")
    git(lender, "add", "kept.txt")
    git(lender, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    borrower = tmp_path / "borrower"
    borrower.mkdir()
    git(borrower, "init", "-q")
    info = borrower / ".git" / "objects" / "info"
    info.mkdir(parents=True, exist_ok=True)
    (info / "alternates").write_text(str(lender / ".git" / "objects") + "\n", encoding="utf-8")
    before = _snapshot({"b": borrower})
    assert any("@alternate" in key for key in before.files), sorted(before.files)[:5]
    (lender / "kept2.txt").write_text("kept too\n", encoding="utf-8")
    git(lender, "add", "kept2.txt")
    git(lender, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "second")
    second_tree = git(lender, "rev-parse", "HEAD^{tree}").strip()
    second_commit = git(lender, "rev-parse", "HEAD").strip()
    foreign = lender / "foreign.txt"
    foreign.write_text("no tree holds this\n", encoding="utf-8")
    foreign_sha = git(lender, "hash-object", "-w", str(foreign)).strip()
    foreign.unlink()
    after = _snapshot({"b": borrower})
    attributed = _classify(
        before,
        after,
        coordination_roots=("coord",),
        reviewed_prefixes=("b",),
        reviewed_trees={"b": (second_tree, second_commit)},
    )
    assert attributed == [
        f"object changed with no attributed comparison: b@alternate1/{foreign_sha[:2]}/{foreign_sha[2:]}",
        f"a read created a directory: b@alternate1/{foreign_sha[:2]}",
    ], attributed


def test_transitive_alternate_chains_are_followed_and_cycles_terminate(tmp_path: Path) -> None:
    """Borrowed-of-borrowed stores are inventoried; alternate cycles stay finite.

    Middle borrows lender, borrower borrows middle: a new object in the lender is
    inventoried two hops out and attributes through a declared tree, while a foreign
    object fails. Pointing middle back at borrower then completes a cycle; the
    snapshot still terminates because every store is visited once. No product read runs.
    """

    lender = tmp_path / "lender"
    lender.mkdir()
    git(lender, "init", "-q")
    (lender / "kept.txt").write_text("kept\n", encoding="utf-8")
    git(lender, "add", "kept.txt")
    git(lender, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    middle = tmp_path / "middle"
    middle.mkdir()
    git(middle, "init", "-q")
    middle_info = middle / ".git" / "objects" / "info"
    middle_info.mkdir(parents=True, exist_ok=True)
    (middle_info / "alternates").write_text(
        str(lender / ".git" / "objects") + "\n", encoding="utf-8"
    )
    borrower = tmp_path / "borrower"
    borrower.mkdir()
    git(borrower, "init", "-q")
    borrower_info = borrower / ".git" / "objects" / "info"
    borrower_info.mkdir(parents=True, exist_ok=True)
    (borrower_info / "alternates").write_text(
        str(middle / ".git" / "objects") + "\n", encoding="utf-8"
    )
    before = _snapshot({"b": borrower})
    transitives = sorted(key for key in before.files if "@alternate" in key)
    assert len(transitives) > 0, sorted(before.files)[:5]
    assert any(key.startswith("b@alternate2/") for key in transitives), transitives[:6]
    (lender / "kept2.txt").write_text("kept too\n", encoding="utf-8")
    git(lender, "add", "kept2.txt")
    git(lender, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "second")
    second_tree = git(lender, "rev-parse", "HEAD^{tree}").strip()
    second_commit = git(lender, "rev-parse", "HEAD").strip()
    foreign = lender / "foreign.txt"
    foreign.write_text("no tree holds this\n", encoding="utf-8")
    foreign_sha = git(lender, "hash-object", "-w", str(foreign)).strip()
    foreign.unlink()
    after = _snapshot({"b": borrower})
    attributed = _classify(
        before,
        after,
        coordination_roots=("coord",),
        reviewed_prefixes=("b",),
        reviewed_trees={"b": (second_tree, second_commit)},
    )
    assert attributed == [
        f"object changed with no attributed comparison: b@alternate2/{foreign_sha[:2]}/{foreign_sha[2:]}",
        f"a read created a directory: b@alternate2/{foreign_sha[:2]}",
    ], attributed
    (middle_info / "alternates").write_text(
        str(lender / ".git" / "objects") + "\n" + str(borrower / ".git" / "objects") + "\n",
        encoding="utf-8",
    )
    cycled = _snapshot({"b": borrower})
    assert any("@alternate" in key for key in cycled.files)


def test_inside_root_chain_to_outside_lender_is_inventoried(tmp_path: Path) -> None:
    """A chain inside the root ending outside it is still fully inventoried.

    Borrower and middle store both sit under the snapshot root; only the lender
    sits outside. The borrower's pointer names the middle store and the middle
    store's own pointer names the lender. Real Git resolves a lender blob through
    both hops, so the oracle must inventory the lender too: a foreign lender
    object fails the oracle even though every inside-root byte is unchanged. No
    product read runs.
    """

    root = tmp_path / "snapshotted"
    root.mkdir()
    lender = tmp_path / "outside-lender"
    lender.mkdir()
    git(lender, "init", "-q")
    borrower = root / "borrower"
    borrower.mkdir()
    git(borrower, "init", "-q")
    (borrower / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    git(borrower, "add", "tracked.txt")
    git(borrower, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    middle_objects = root / "middle" / "objects"
    middle_objects.mkdir(parents=True)
    (middle_objects / "info").mkdir(exist_ok=True)
    (middle_objects / "info" / "alternates").write_text(
        str(lender / ".git" / "objects") + "\n", encoding="utf-8"
    )
    borrower_info = borrower / ".git" / "objects" / "info"
    borrower_info.mkdir(parents=True, exist_ok=True)
    (borrower_info / "alternates").write_text(str(middle_objects) + "\n", encoding="utf-8")
    before = _snapshot({"b": root})
    foreign = lender / "foreign.txt"
    foreign.write_text("an external object no comparison captured\n", encoding="utf-8")
    foreign_sha = git(lender, "hash-object", "-w", str(foreign)).strip()
    foreign.unlink()
    resolved = git(borrower, "cat-file", "-p", foreign_sha).strip()
    assert resolved == "an external object no comparison captured", resolved
    after = _snapshot({"b": root})
    foreign_key = f"b@alternate1/{foreign_sha[:2]}/{foreign_sha[2:]}"
    assert foreign_key in after.files, sorted(after.files)[:8]
    findings = _classify(
        before,
        after,
        coordination_roots=("coord",),
        reviewed_prefixes=("b",),
    )
    assert f"object changed with no attributed comparison: {foreign_key}" in findings, findings


def test_an_empty_directory_fails_the_oracle_even_where_caches_are_allowed() -> None:
    """An empty directory is a finding -- even under runtime/ -- unless it holds a file."""

    before = _WorldState(git=_bare_repo(), files={}, dirs=set())
    empty = _WorldState(git=_bare_repo(), files={}, dirs={"coord/runtime/emptydir"})
    findings = _classify(before, empty, coordination_roots=("coord",), reviewed_prefixes=())
    assert findings == ["a read created a directory: coord/runtime/emptydir"], findings

    holding = _WorldState(
        git=_bare_repo(),
        files={"coord/runtime/d/cache.json": b"{}"},
        dirs={"coord/runtime", "coord/runtime/d"},
    )
    assert _classify(before, holding, coordination_roots=("coord",), reviewed_prefixes=()) == []


def test_an_unauthorized_object_fails_the_oracle_without_an_attributed_comparison() -> None:
    """An object under a reviewed prefix with no attributed comparison is a finding."""

    before = _WorldState(git=_bare_repo(), files={}, dirs=set())
    planted = _WorldState(
        git=_bare_repo(),
        files={"repo/.git/objects/ab/cdef0123456789": b"\x00"},
        dirs={"repo/.git/objects", "repo/.git/objects/ab"},
    )
    findings = _classify(
        before, planted, coordination_roots=("coord",), reviewed_prefixes=("repo",)
    )
    assert findings == [
        "object changed with no attributed comparison: repo/.git/objects/ab/cdef0123456789",
        "a read created a directory: repo/.git/objects",
        "a read created a directory: repo/.git/objects/ab",
    ], findings
    # Declared trees the repository does not hold attribute nothing either.
    attributed = _classify(
        before,
        planted,
        coordination_roots=("coord",),
        reviewed_prefixes=("repo",),
        reviewed_trees={"repo": ("0" * 40,)},
    )
    assert attributed == findings, attributed

    # An empty record attests nothing: the object beside it still fails. Mere
    # placement beside any changed record path is not custody.
    beside_an_empty_record = _WorldState(
        git=_bare_repo(),
        files={
            "repo/.git/objects/ab/cdef0123456789": b"\x00",
            "task/notes/reports/review-comparisons/comparison.json": b"{}",
        },
        dirs={"repo/.git/objects", "repo/.git/objects/ab", "task/notes/reports/review-comparisons"},
    )
    assert (
        _classify(
            before,
            beside_an_empty_record,
            coordination_roots=("coord",),
            reviewed_prefixes=("repo",),
        )
        == findings
    )


def test_a_nested_git_admin_directory_fails_the_oracle() -> None:
    """An empty Git administrative directory is inventoried and fails."""

    before = _WorldState(git=_bare_repo(), files={}, dirs=set())
    after = _WorldState(git=_bare_repo(), files={}, dirs={"repo/.git/refs/heads/stale"})
    findings = _classify(before, after, coordination_roots=("coord",), reviewed_prefixes=("repo",))
    assert findings == ["a read created a directory: repo/.git/refs/heads/stale"], findings


def test_refs_are_keyed_by_name_so_a_shared_object_never_collapses_two_refs() -> None:
    """Two refs on one object are two rows: a new second ref is a finding."""

    before = _WorldState(
        git={
            "repo": _GitState(
                refs="refs/heads/a " + "0" * 40,
                head="0" * 40,
            )
        },
        files={},
        dirs=set(),
    )
    after = _WorldState(
        git={
            "repo": _GitState(
                refs="refs/heads/a " + "0" * 40 + "\nrefs/heads/b " + "0" * 40,
                head="0" * 40,
            )
        },
        files={},
        dirs=set(),
    )
    findings = _classify(before, after, coordination_roots=("coord",), reviewed_prefixes=("repo",))
    assert findings == ["repo: ref appeared: refs/heads/b"], findings


def test_a_moved_pin_is_a_finding_but_an_attested_pin_is_allowed() -> None:
    """Pins need both halves: a record name pointing at an attributed object."""

    old = _WorldState(
        git={"repo": _GitState(refs="refs/ar/review/x aaa", head="0" * 40)},
        files={},
        dirs=set(),
    )
    moved = _WorldState(
        git={"repo": _GitState(refs="refs/ar/review/x bbb", head="0" * 40)},
        files={},
        dirs=set(),
    )
    findings = _classify(old, moved, coordination_roots=("coord",), reviewed_prefixes=("repo",))
    assert findings == ["repo: ref moved to another object: refs/ar/review/x"], findings

    lonely = _WorldState(
        git={"repo": _GitState(refs="", head="0" * 40)},
        files={},
        dirs=set(),
    )
    added = _WorldState(
        git={"repo": _GitState(refs="refs/ar/review/y ccc", head="0" * 40)},
        files={},
        dirs=set(),
    )
    findings = _classify(lonely, added, coordination_roots=("coord",), reviewed_prefixes=("repo",))
    assert findings == ["repo: ref appeared: refs/ar/review/y"], findings

    attested_tree = "a" * 40
    attested_record = json.dumps(
        {
            "schema": "ar-review-tree-comparison/v1",
            "code_base": {"tree": attested_tree, "ref": "refs/ar/review/y"},
            "code_candidate": {"tree": "b" * 40},
            "memory_base": {"tree": "c" * 40},
            "memory_candidate": {"tree": "d" * 40},
        }
    ).encode("utf-8")
    both_halves = _WorldState(
        git={"repo": _GitState(refs=f"refs/ar/review/y {attested_tree}", head="0" * 40)},
        files={"task/notes/reports/review-comparisons/c.json": attested_record},
        dirs={"task/notes/reports/review-comparisons"},
    )
    assert (
        _classify(lonely, both_halves, coordination_roots=("coord",), reviewed_prefixes=("repo",))
        == []
    )

    # A record-named pin to a foreign object is a finding: the name half alone never
    # clears. The pure probe names y while pointing at e*40 against trees of a*40.
    foreign = _WorldState(
        git={"repo": _GitState(refs="refs/ar/review/y " + "e" * 40, head="0" * 40)},
        files={"task/notes/reports/review-comparisons/c.json": attested_record},
        dirs={"task/notes/reports/review-comparisons"},
    )
    findings = _classify(
        lonely, foreign, coordination_roots=("coord",), reviewed_prefixes=("repo",)
    )
    assert findings == ["repo: ref appeared: refs/ar/review/y"], findings
