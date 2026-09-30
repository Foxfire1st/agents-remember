"""MIK-R33: the change-kind facts of the reviewer's family tree, computed on the server.

The comparison is a live leaf over four real Git trees: a code repository and a converted memory
repository, each with a committed base and the leaf's uncommitted candidate in its worktree, resolved
and composed exactly as the reviewer does. Every memory side is read through the derived index of its
tree. The fixture authors one member per badge kind:

* ``INV-AAAAAA`` -- revision 1 -> 2, and ``land``'s edited body intersects its realization: primary
  ``intent`` with an ``implementation`` mark;
* ``INV-BBBBBB`` -- a realization of ``land`` (re-recorded at the candidate blob by the curator):
  ``implementation`` through the hunk;
* ``INV-CCCCCC`` -- its realization re-anchored from ``move`` to ``fixed`` in an unchanged file:
  ``implementation`` through definition 7;
* ``INV-DDDDDD`` -- a proof of ``test_land``, whose body changed: ``implementation`` marked ``test``;
* ``INV-EEEEEE`` -- a realization in an unchanged file: ``unchanged``;
* ``INV-FFFFFF`` -- shared: unchanged in ``FAM-F00001``, joins ``FAM-F00002`` (``membership``);
* ``INV-GGGGGG`` -- a realization in the changed file recorded at a blob neither side holds:
  ``unknown``;
* ``INV-KKKKKK`` -- a realization of ``other``, which the edit did not touch, carried mechanically to
  the candidate blob: ``unchanged`` (definition 7's exception);
* ``INV-HHHHHH`` -- added, and joins ``FAM-F00001``: ``intent`` with a ``membership`` mark;
* ``INV-PPPPPP`` -- in ``FAM-F00002`` beside the unchanged ``INV-EEEEEE``: the same revision with its
  applicability reworded, so ``intent`` marked ``text_differs``, never ``unchanged``;
* ``INV-MMMMMM`` and ``INV-NNNNNN`` (``FAM-F00003``) -- ``pkg/data.bin``, a binary file, is rewritten:
  ``M``'s ``file`` entry covers it (``implementation``, definition 8); ``N``'s ``line_range`` entry
  there cannot be intersected (``unknown``, with its reason);
* ``INV-SSSSSS`` (``FAM-F00003``) -- a realization recorded at a stale blob, re-anchored correctly by
  the curator in the unchanged ``pkg/b.py``: ``implementation`` (moved), although the worklist's own
  class for it is ``stale_at_base``;
* ``INV-XXXXXX`` (``FAM-F00003``) -- a realization of ``land`` the hunk meets, and a stale one in the
  changed test file: ``implementation`` with the unconditional ``unknown`` mark;
* ``INV-RRRRRR`` (``FAM-F00003``) -- retired on the after side at the same revision: ``intent``.

``FAM-F00002``'s guarantee text changes; ``FAM-F00001``'s does not; ``FAM-F00003``'s revision is
bumped (its members are reordered) while its guarantee text stays.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application.knowledge_review import compose_review
from agents_remember.application.knowledge_worklist.code import CodeTrees
from agents_remember.application.review_candidate_resolution import (
    ReviewCandidateResolution,
    resolve_review_candidate,
)
from agents_remember.application.review_change_kinds import with_change_kinds
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.memory.knowledge_index import text_uuid
from agents_remember.models.knowledge.read import FamilyIdentitySeed, InvariantIdentitySeed
from agents_remember.models.knowledge.review_change_kinds import ReviewMemberChange
from agents_remember.models.knowledge.review_family_context import (
    ReviewFamilyContext,
    ReviewFamilyContextEntry,
)
from agents_remember.models.knowledge_files import canonical_text
from agents_remember.worktrees.services import reset_worktree_services
from pydantic import ValidationError
from test_review_git_trees import LEAF, MASTER, REPO, TASK, World, git

A, B, C, D, E, F, G, H, K, M, N, P, R, S, X = (f"INV-{letter * 6}" for letter in "ABCDEFGHKMNPRSX")
FAM1, FAM2, FAM3 = "FAM-F00001", "FAM-F00002", "FAM-F00003"
ORIGIN = {"task": "260101-OLD", "leaf": "260101-OLD-L1"}
STALE_BLOB = "0" * 40
STALE_CONTENT = "sha256:" + "0" * 64
A_V1 = (
    "def land(value):\n    return value\n\n\ndef keep():\n    return 1\n\n\n"
    "def other():\n    return 3\n"
)
A_V2 = A_V1.replace("return value\n", "return value + 0\n")
B_V1 = "def move():\n    return 1\n\n\ndef fixed():\n    return 2\n"
TEST_V1 = "def test_land():\n    assert True\n"
TEST_V2 = "def test_land():\n    assert 1 == 1\n"
DATA_V1, DATA_V2 = bytes(range(256)), bytes(reversed(range(256)))
CODE_BASE: dict[str, str | bytes] = {
    "pkg/a.py": A_V1,
    "pkg/b.py": B_V1,
    "tests/test_a.py": TEST_V1,
    "pkg/data.bin": DATA_V1,
}


def _invariant(
    record_id: str, revision: int = 1, statement: str = "", **fields: str
) -> tuple[str, str]:
    body = {
        "schema": "ar-invariant/v1",
        "id": record_id,
        "revision": revision,
        "status": "accepted",
        "statement": statement or f"{record_id} holds (r{revision}).",
        "applicability": "Always.",
        "conditions": [],
        "exclusions": [],
        "supersedes": [],
        "admission": "legacy-unassessed",
        "origin": ORIGIN,
        **fields,
    }
    return f"knowledge/invariants/{record_id}-{record_id.lower()}.json", canonical_text(body)


def _family(
    record_id: str, members: list[str], guarantee: str, revision: int = 1
) -> tuple[str, str]:
    body = {
        "schema": "ar-family/v1",
        "id": record_id,
        "revision": revision,
        "status": "accepted",
        "title": record_id,
        "guarantee": guarantee,
        "members": members,
        "routes": ["pkg"],
        "admission": "legacy-unassessed",
        "origin": ORIGIN,
    }
    return f"knowledge/families/{record_id}-{record_id.lower()}.json", canonical_text(body)


class _Anchors:
    """Anchors resolved in one code tree, as a curator records them."""

    def __init__(self, repository: Path, commit_id: str) -> None:
        tree = git(repository, "rev-parse", f"{commit_id}^{{tree}}")
        self.trees = CodeTrees.open(repository, tree, tree)

    def blob(self, path: str) -> str:
        return self.trees.base()[path]

    def at(self, path: str, locator: dict[str, Any]) -> dict[str, Any]:
        blob = self.blob(path)
        resolved = self.trees.resolve(path, locator, blob, blob)
        assert resolved is not None, (path, locator)
        return {"locator": locator, "blob": blob, "content": resolved.content}


def _symbol(name: str) -> dict[str, str]:
    return {"kind": "symbol", "name": name}


def _entry(entry_id: str, invariant: str, anchor: dict[str, Any]) -> dict[str, Any]:
    if entry_id.startswith("PRF-"):
        return {"id": entry_id, "invariant": invariant, "anchor": anchor, "facet": "It lands."}
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


def _memory_base(base: _Anchors) -> dict[str, str]:
    stale = {"locator": {"kind": "file"}, "blob": STALE_BLOB, "content": STALE_CONTENT}
    return {
        "knowledge/layout.json": canonical_text(
            {"schema": "ar-memory-layout/v2", "conversion": "1"}
        ),
        "onboarding/overview.md": "# root\n",
        **dict(_invariant(one) for one in (A, B, C, D, E, F, G, K, M, N, P, R, S, X)),
        **dict([_family(FAM1, [A, B, C, D, E, F, G, K], "One.")]),
        **dict([_family(FAM2, [E, P], "Two.")]),
        **dict([_family(FAM3, [M, N, S, X, R], "Three.")]),
        **_sidecar(
            "pkg/data.bin",
            [
                _entry("RLZ-M00001", M, base.at("pkg/data.bin", {"kind": "file"})),
                _entry(
                    "RLZ-N00001",
                    N,
                    {
                        "locator": {"kind": "line_range", "start": 1, "end": 1},
                        "blob": base.blob("pkg/data.bin"),
                        "content": STALE_CONTENT,
                    },
                ),
            ],
        ),
        **_sidecar(
            "pkg/a.py",
            [
                _entry("RLZ-A00001", A, base.at("pkg/a.py", _symbol("land"))),
                _entry("RLZ-B00001", B, base.at("pkg/a.py", _symbol("land"))),
                _entry("RLZ-G00001", G, stale),
                _entry("RLZ-K00001", K, base.at("pkg/a.py", _symbol("other"))),
                _entry("RLZ-X00001", X, base.at("pkg/a.py", _symbol("land"))),
            ],
        ),
        **_sidecar(
            "pkg/b.py",
            [
                _entry("RLZ-C00001", C, base.at("pkg/b.py", _symbol("move"))),
                _entry("RLZ-E00001", E, base.at("pkg/b.py", _symbol("fixed"))),
                _entry("RLZ-F00001", F, base.at("pkg/b.py", _symbol("fixed"))),
                _entry("RLZ-S00001", S, {**stale, "locator": _symbol("fixed")}),
            ],
        ),
        **_sidecar(
            "tests/test_a.py",
            [_entry("RLZ-X00002", X, {**stale, "locator": _symbol("test_land")})],
            [_entry("PRF-D00001", D, base.at("tests/test_a.py", _symbol("test_land")))],
        ),
    }


def _memory_candidate(base: _Anchors, candidate: _Anchors) -> dict[str, str]:
    """The leaf's knowledge: A revised, H added to FAM1, F joins FAM2 (whose guarantee is revised),
    C re-anchored, B and K re-recorded at the candidate blob."""

    files = dict(_memory_base(base))
    stale = {"locator": {"kind": "file"}, "blob": STALE_BLOB, "content": STALE_CONTENT}
    files.update(
        [
            _invariant(A, 2),
            _invariant(H),
            _invariant(P, 1, applicability="Always, reworded without a new revision."),
            _invariant(R, 1, status="retired"),
            _family(FAM1, [A, B, C, D, E, F, G, K, H], "One."),
            _family(FAM2, [E, F, P], "Two, revised.", 2),
            _family(FAM3, [X, M, N, S, R], "Three.", 2),
        ]
    )
    files.update(
        _sidecar(
            "pkg/a.py",
            [
                _entry("RLZ-A00001", A, base.at("pkg/a.py", _symbol("land"))),
                _entry("RLZ-B00001", B, candidate.at("pkg/a.py", _symbol("land"))),
                _entry("RLZ-G00001", G, stale),
                _entry("RLZ-K00001", K, candidate.at("pkg/a.py", _symbol("other"))),
                _entry("RLZ-X00001", X, base.at("pkg/a.py", _symbol("land"))),
            ],
        )
    )
    files.update(
        _sidecar(
            "pkg/b.py",
            [
                _entry("RLZ-C00001", C, base.at("pkg/b.py", _symbol("fixed"))),
                _entry("RLZ-E00001", E, base.at("pkg/b.py", _symbol("fixed"))),
                _entry("RLZ-F00001", F, base.at("pkg/b.py", _symbol("fixed"))),
                _entry("RLZ-S00001", S, base.at("pkg/b.py", _symbol("fixed"))),
            ],
        )
    )
    return files


def _repository(root: Path) -> Path:
    root.mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "user.name", "change kinds fixture")
    return root


def _write(root: Path, files: dict[str, str | bytes]) -> None:
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")


def _commit(root: Path, files: dict[str, str | bytes], trailer: str | None = None) -> str:
    _write(root, files)
    git(root, "add", "-A")
    message = "change" if trailer is None else f"memory\n\nCode-Commit: {trailer}"
    git(root, "commit", "-q", "--allow-empty", "-m", message)
    return git(root, "rev-parse", "HEAD")


def build_world(tmp_path: Path) -> World:
    """The fixture's live leaf under ``tmp_path`` (also the dashboard fixtures' capture source)."""

    code = _repository(tmp_path / "repos" / "code")
    code_base = _commit(code, CODE_BASE)
    base = _Anchors(code, code_base)
    memory = _repository(tmp_path / "repos" / "memory")
    memory_base = _commit(memory, dict(_memory_base(base)), trailer=code_base)
    code_worktree, memory_worktree = tmp_path / "group" / "code", tmp_path / "group" / "memory"
    git(code, "worktree", "add", "-q", "-b", "leaf", str(code_worktree))
    git(memory, "worktree", "add", "-q", "-b", "leaf", str(memory_worktree))
    coordination = tmp_path / "coordination"
    task_root = coordination / "tasks" / REPO / MASTER
    task_root.mkdir(parents=True)
    (task_root / "task.md").write_text("# task\n", encoding="utf-8")
    (task_root / "task.json").write_text(json.dumps({"id": TASK}), encoding="utf-8")
    config = McpRuntimeConfig(
        workspace_root=tmp_path,
        coordination_root=coordination,
        config_path=tmp_path / "config.json",
        transcript_root=tmp_path / "transcripts",
    )
    made = World(
        tmp_path,
        config,
        code,
        memory,
        code_worktree,
        memory_worktree,
        task_root,
        code_base,
        memory_base,
    )
    made.contract()
    # The leaf's uncommitted change: code edited, knowledge curated against the edited code.
    _write(code_worktree, {"pkg/a.py": A_V2, "tests/test_a.py": TEST_V2, "pkg/data.bin": DATA_V2})
    scratch = _repository(tmp_path / "repos" / "scratch")
    candidate = _Anchors(scratch, _commit(scratch, {**CODE_BASE, "pkg/a.py": A_V2}))
    _write(memory_worktree, dict(_memory_candidate(base, candidate)))
    return made


@pytest.fixture
def world(tmp_path: Path) -> World:
    return build_world(tmp_path)


def _resolve(world: World) -> ReviewCandidateResolution:
    resolved = resolve_review_candidate(world.config, REPO, MASTER, LEAF)
    assert isinstance(resolved, ReviewCandidateResolution), resolved
    assert resolved.trees is not None
    return resolved


def _context(world: World, seed: Any, **request: Any) -> ReviewFamilyContext:
    review = compose_review(_resolve(world), world.review(selector=seed, **request))
    assert review.state == "review" and review.payload is not None, review.refusal
    context = review.payload.family_context
    assert context is not None
    return context


def _family_seed(family: str) -> FamilyIdentitySeed:
    return FamilyIdentitySeed(family_id=text_uuid("identity", family))


def _entry_of(context: ReviewFamilyContext, family: str) -> ReviewFamilyContextEntry:
    return next(e for e in context.entries if e.family_id == text_uuid("identity", family))


def _by_invariant(entry: ReviewFamilyContextEntry) -> dict[str, ReviewMemberChange]:
    assert entry.change_kinds is not None
    return {member.invariant or "": member for member in entry.change_kinds.members}


def _kinds(entry: ReviewFamilyContextEntry) -> dict[str, tuple[str, tuple[str, ...]]]:
    return {key: (one.primary, one.marks) for key, one in _by_invariant(entry).items()}


def _evidence_names_each_fact(facts: dict[str, ReviewMemberChange]) -> None:
    """Each positive fact names what established it; an unknown one names why it is unknown."""

    assert "INV-AAAAAA revision 1 → 2" in facts[A].evidence
    assert any("intersects realization RLZ-B00001" in line for line in facts[B].evidence)
    assert facts[C].evidence == ("realization RLZ-C00001 re-anchored",)
    assert facts[D].proof and any("PRF-D00001" in line for line in facts[D].evidence)
    assert facts[H].evidence == ("INV-HHHHHH is added", "INV-HHHHHH joined FAM-F00001")
    _unknowns_name_why_and_order_is_authored(facts)


def _unknowns_name_why_and_order_is_authored(facts: dict[str, ReviewMemberChange]) -> None:
    # The unknown member says why, in the lane's per-entry terms, and names no evidence.
    assert facts[G].evidence == ()
    assert any(
        "RLZ-G00001 supplies no range" in line
        and f"it is recorded at blob {STALE_BLOB[:10]}" in line
        and "recorded_blob_mismatch" in line
        for line in facts[G].unknown_reasons
    )
    # The stale entry is unresolved on both sides, and each side is named.
    assert any("on the before side" in line for line in facts[G].unknown_reasons)
    assert any("on the after side" in line for line in facts[G].unknown_reasons)
    assert {key for key, one in facts.items() if one.unknown_reasons} == {A, D, G}
    assert all(
        "supplies no range on the after side" in line
        for key in (A, D)
        for line in facts[key].unknown_reasons
    )
    # The authored order is the family record's ``members`` list (the after side's).
    order = [A, B, C, D, E, F, G, K, H]
    assert {key: one.authored_position for key, one in facts.items()} == {
        key: order.index(key) for key in order
    }
    # A mechanical carry to the candidate blob is not a re-anchor, and no hunk meets ``other``.
    assert facts[K].evidence == () and facts[K].implementation == "not_established"


# -- the badge derivation (the primary falsifier) ---------------------------------------------------


def test_every_member_occurrence_carries_its_kind_from_the_recorded_comparison(
    world: World,
) -> None:
    family = _entry_of(_context(world, _family_seed(FAM1)), FAM1)
    assert _kinds(family) == {
        # A and D: their after-side entries still sit at the base blob, so their ranges in the
        # changed file are unresolved there -- the unconditional unknown mark (ICR-R32 rule 1).
        A: ("intent", ("implementation", "unknown")),
        B: ("implementation", ()),
        C: ("implementation", ()),
        D: ("implementation", ("test", "unknown")),
        E: ("unchanged", ()),
        F: ("unchanged", ()),
        G: ("unknown", ()),
        H: ("intent", ("membership",)),
        K: ("unchanged", ()),
    }
    _evidence_names_each_fact(_by_invariant(family))
    # The family's own row: the guarantee did not change; nine live members on either side.
    assert family.change_kinds is not None
    assert family.change_kinds.guarantee == "unchanged"
    assert family.change_kinds.members_total == 9
    # Every returned member occurrence is described, and nothing else.
    returned = {m.member_id for side in (family.before, family.after) for m in side.members}
    assert {m.member_id for m in family.change_kinds.members} == returned


def test_a_shared_member_shows_membership_where_it_joined_and_unchanged_elsewhere(
    world: World,
) -> None:
    context = _context(world, InvariantIdentitySeed(invariant_id=text_uuid("identity", F)))
    first, second = _entry_of(context, FAM1), _entry_of(context, FAM2)
    assert _kinds(first)[F] == ("unchanged", ())
    assert _kinds(second)[F] == ("membership", ())
    # One revision whose text changed is intent, never unchanged -- beside a genuinely unchanged
    # member of the same family.
    assert _kinds(second)[P] == ("intent", ("text_differs",))
    assert _kinds(second)[E] == ("unchanged", ())
    reworded = _by_invariant(second)[P]
    assert reworded.text_differs
    # Only its applicability was reworded: every authored text field is compared.
    assert reworded.evidence == ("INV-PPPPPP keeps revision 1, but its text differs",)
    # The joined family's guarantee text changed: its own row shows intent.
    assert second.change_kinds is not None and second.change_kinds.guarantee == "intent"
    assert "differs" in second.change_kinds.guarantee_detail
    # One member occurrence per family: the same member is referenced, never cloned.
    member = text_uuid("member", f"{FAM2}/{F}")
    assert [m.member_id for m in second.change_kinds.members].count(member) == 1


def test_a_partial_page_describes_only_the_members_it_returned(world: World) -> None:
    family = _entry_of(_context(world, _family_seed(FAM1), page_size=2), FAM1)
    kinds = family.change_kinds
    assert kinds is not None
    returned = {m.member_id for side in (family.before, family.after) for m in side.members}
    assert 0 < len(returned) < 9
    assert {m.member_id for m in kinds.members} == returned
    # The total is still the whole deduplicated union, so the page can say "N of R returned (T)".
    assert kinds.members_total == 9
    assert any(
        side.page is not None and not side.page.complete for side in (family.before, family.after)
    )


# -- unreadable knowledge is unknown, never unchanged ----------------------------------------------


def test_an_unreadable_sidecar_leaves_implementation_unknown_never_unchanged(world: World) -> None:
    (world.memory_worktree / "onboarding" / "pkg" / "b.py.json").write_text("{", encoding="utf-8")
    family = _entry_of(_context(world, _family_seed(FAM1)), FAM1)
    kinds = _kinds(family)
    # What was established elsewhere stays; nothing reads unchanged over the unread sidecar.
    assert kinds[B] == ("implementation", ())
    assert kinds[E][0] == "unknown" and kinds[K][0] == "unknown"
    assert all(primary != "unchanged" for primary, _ in kinds.values())
    assert any("do not parse" in line for line in _by_invariant(family)[E].unknown_reasons)


def test_an_unreadable_family_record_leaves_guarantee_and_membership_unknown(
    world: World,
) -> None:
    resolved = _resolve(world)
    context = _context(world, InvariantIdentitySeed(invariant_id=text_uuid("identity", F)))
    fam2 = world.memory_worktree / "knowledge" / "families" / f"{FAM2}-{FAM2.lower()}.json"
    fam2.write_text("{", encoding="utf-8")
    broken = _resolve(world)
    assert broken.trees is not None and broken.trees.record != resolved.trees.record  # type: ignore[union-attr]
    # The composed context of the readable comparison, badged over the broken one.
    badged = with_change_kinds(_without_kinds(context), broken.trees)
    second = _entry_of(badged, FAM2)
    assert second.change_kinds is not None and second.change_kinds.guarantee == "unknown"
    assert second.change_kinds.members_total is None
    assert _by_invariant(second)[F].membership == "unknown"
    # The membership's reason is its own: never stated as a change-kind unknown.
    assert _by_invariant(second)[F].unknown_reasons == ()
    assert any("do not parse" in line for line in _by_invariant(second)[F].membership_reasons)
    assert _kinds(second)[F][0] == "unknown"


def test_trees_that_cannot_be_read_make_every_fact_unknown(world: World) -> None:
    resolved = _resolve(world)
    context = _context(world, _family_seed(FAM1))
    trees = resolved.trees
    assert trees is not None
    record = trees.record.model_copy(
        update={"code_base": trees.record.code_base.model_copy(update={"tree": "f" * 40})}
    )
    unreadable = type(trees)(**{**trees.__dict__, "record": record})
    badged = _entry_of(with_change_kinds(_without_kinds(context), unreadable), FAM1)
    assert badged.change_kinds is not None
    assert badged.change_kinds.guarantee == "unknown" and badged.change_kinds.detail
    assert {one.primary for one in badged.change_kinds.members} == {"unknown"}
    assert all(
        one.unknown_reasons == (badged.change_kinds.detail,) for one in badged.change_kinds.members
    )


def test_a_changed_non_text_file_is_implementation_through_a_file_entry_only(world: World) -> None:
    family = _entry_of(_context(world, _family_seed(FAM3)), FAM3)
    facts = _by_invariant(family)
    # Definition 8: a file entry covers a changed binary; a line range there meets no hunk.
    kinds = _kinds(family)
    assert kinds[M] == ("implementation", ("unknown",)) and kinds[N] == ("unknown", ())
    # M's after-side file entry sits at the base blob: covered, yet its range there is unresolved.
    assert any(
        "RLZ-M00001 supplies no range on the after side" in r for r in facts[M].unknown_reasons
    )
    assert any(
        "non-text change of pkg/data.bin is covered by realization RLZ-M00001" in line
        for line in facts[M].evidence
    )
    assert facts[N].evidence == ()
    # Rule 6b stated on its own: N's range resolves on the before side, yet a changed binary has no
    # hunk, so whether it is met is unknown -- beside the after side's unresolved range. (A range
    # re-recorded at the after blob would instead be a re-anchor, (a); a binary line range cannot be
    # carried, so rule 6b always sits beside another fact or an unresolved range.)
    reasons = facts[N].unknown_reasons
    assert any(
        "pkg/data.bin changed as a non-text file" in line
        and "RLZ-N00001 is not a file entry" in line
        for line in reasons
    )
    assert any("RLZ-N00001 supplies no range on the after side" in line for line in reasons)


# -- the transport's own rules ----------------------------------------------------------------------


def test_a_moved_entry_a_retired_record_and_an_unresolved_range_in_one_family(
    world: World,
) -> None:
    family = _entry_of(_context(world, _family_seed(FAM3)), FAM3)
    facts = _by_invariant(family)
    _moved_unresolved_and_retired(facts, _kinds(family))
    # The revision was bumped for a reorder; the guarantee text did not change.
    assert family.change_kinds is not None and family.change_kinds.guarantee == "unchanged"
    # Authored order is the after record's list; the retired member counts on the before side.
    assert {key: one.authored_position for key, one in facts.items()} == {
        X: 0,
        M: 1,
        N: 2,
        S: 3,
        R: 4,
    }
    assert family.change_kinds.members_total == 5


def _moved_unresolved_and_retired(
    facts: dict[str, ReviewMemberChange], kinds: dict[str, tuple[str, tuple[str, ...]]]
) -> None:
    # A stale entry the curator re-anchored in an unchanged file has moved: implementation, although
    # the worklist classes it stale_at_base (its own item would cover it; the reviewer has none).
    assert kinds[S] == ("implementation", ())
    assert facts[S].evidence == ("realization RLZ-S00001 re-anchored (stale at base)",)
    # Linked through one entry, unresolved through another: the unknown mark stays.
    assert kinds[X] == ("implementation", ("unknown",))
    assert any("intersects realization RLZ-X00001" in line for line in facts[X].evidence)
    # The reason of the entry that established nothing comes first (RLZ-X00001 linked the hunk).
    assert facts[X].unknown_reasons[0].startswith("RLZ-X00002 supplies no range")
    assert any("RLZ-X00001 supplies no range" in line for line in facts[X].unknown_reasons)
    # Retired on the after side at the same revision: removed, so intent.
    assert kinds[R] == ("intent", ())
    assert facts[R].evidence == ("INV-RRRRRR is removed (retired)",)


def test_an_unread_sidecar_of_a_changed_file_marks_unknown_beside_an_established_fact(
    world: World,
) -> None:
    sidecar = world.memory_worktree / "onboarding" / "tests" / "test_a.py.json"
    sidecar.write_text("{", encoding="utf-8")
    family = _entry_of(_context(world, _family_seed(FAM3)), FAM3)
    one = _by_invariant(family)[X]
    # The hunk in pkg/a.py still establishes implementation; the changed test file's after
    # knowledge cannot be read, so the unknown mark stays, naming that side.
    assert (one.primary, one.marks) == ("implementation", ("unknown",))
    assert any(
        line.startswith("the after knowledge of tests/test_a.py is unavailable")
        for line in one.unknown_reasons
    )


def test_the_total_is_unknown_only_when_the_familys_own_records_fail(world: World) -> None:
    broken = world.memory_worktree / "knowledge" / "invariants" / f"{N}-{N.lower()}.json"
    broken.write_text("{", encoding="utf-8")
    fam3 = _entry_of(_context(world, _family_seed(FAM3)), FAM3)
    fam1 = _entry_of(_context(world, _family_seed(FAM1)), FAM1)
    # FAM3 lists the record that fails to parse; FAM1 does not, and keeps its total.
    assert fam3.change_kinds is not None and fam3.change_kinds.members_total is None
    assert fam1.change_kinds is not None and fam1.change_kinds.members_total == 9


def test_the_facts_are_derived_and_describe_exactly_the_returned_members(world: World) -> None:
    family = _entry_of(_context(world, _family_seed(FAM1)), FAM1)
    kinds = family.change_kinds
    assert kinds is not None
    # A primary or mark that disagrees with the facts is refused.
    one = kinds.members[0].model_dump()
    with pytest.raises(ValidationError, match="primary kind"):
        ReviewMemberChange(**{**one, "primary": "unchanged", "intent": "established"})
    with pytest.raises(ValidationError, match="marks"):
        ReviewMemberChange(**{**one, "marks": ("unknown",)})
    # An unknown fact without its reason, or a reason beside known facts, is refused.
    unknown = next(m for m in kinds.members if m.primary == "unknown").model_dump()
    with pytest.raises(ValidationError, match="names why"):
        ReviewMemberChange(**{**unknown, "unknown_reasons": ()})
    known = next(m for m in kinds.members if m.primary == "unchanged").model_dump()
    with pytest.raises(ValidationError, match="names why"):
        ReviewMemberChange(**{**known, "unknown_reasons": ("a reason",)})
    # An unresolved range is recorded beside an established or unknown implementation only.
    with pytest.raises(ValidationError, match="unresolved range"):
        ReviewMemberChange(**{**known, "range_unresolved": True})
    with pytest.raises(ValidationError, match="unknown membership names why"):
        ReviewMemberChange(**{**known, "membership": "unknown", "primary": "unknown"})
    with pytest.raises(ValidationError, match="unknown membership names why"):
        ReviewMemberChange(**{**known, "membership_reasons": ("a reason",)})
    # A differing text marks an established intent only.
    with pytest.raises(ValidationError, match="differing text"):
        ReviewMemberChange(**{**unknown, "text_differs": True})
    # Facts for a member the page did not return, or none for one it did, are refused.
    dropped = kinds.model_copy(update={"members": kinds.members[1:]})
    with pytest.raises(ValidationError, match="exactly the member occurrences"):
        ReviewFamilyContextEntry(**{**dict(family), "change_kinds": dropped})


def test_a_dataset_review_carries_no_change_facts(world: World) -> None:
    for repository in (world.memory, world.memory_worktree):
        git(repository, "rm", "-q", "-r", "-f", "knowledge")
        git(repository, "commit", "-q", "-m", "unconverted")
    resolved = resolve_review_candidate(world.config, REPO, MASTER, LEAF)
    assert isinstance(resolved, ReviewCandidateResolution) and resolved.trees is None
    review = compose_review(resolved, world.review(selector=_family_seed(FAM1)))
    context = None if review.payload is None else review.payload.family_context
    assert context is None or all(entry.change_kinds is None for entry in context.entries)
    served = review.model_dump(mode="json", exclude_none=True)
    assert "change_kinds" not in json.dumps(served)


def _without_kinds(context: ReviewFamilyContext) -> ReviewFamilyContext:
    entries = tuple(
        ReviewFamilyContextEntry(**{**dict(entry), "change_kinds": None})
        for entry in context.entries
    )
    return ReviewFamilyContext(**{**dict(context), "entries": entries})


@pytest.fixture(autouse=True)
def _no_bound_services() -> Iterator[None]:
    reset_worktree_services()
    yield
    reset_worktree_services()
