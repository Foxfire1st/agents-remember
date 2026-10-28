"""MIK-R211 rules 1 and 2: the comparison scopes the evidence, and the packet holds all of it.

Each case builds a real comparison -- two Git repositories and the trees a comparison names -- and
reads it through :func:`prepare_evidence`, so what the packet carries is what these files actually
hold rather than what a fixture says they hold.

1. Judgments are the rows of the history files *the comparison changed*, read whole. A 1.2 MB file
   of 714 rows is counted as 714 and carried as 714 (MIK-R211 v4 rules 1-2): no record bound and no
   row cut beside the map leaves any of them out.
2. A planned binding reads the leaf's own history file and no other task's; a leaf without one
   selects nothing and says why.
3. Proofs are the entries of the sidecars the comparison changed, every one of them read.
4. Nothing is short silently: a collection that carries fewer items than its count names the bound,
   and the packet's map must name what it left out; the packet declares no read limits at all.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from agents_remember.application.review_evidence_adapter import _memory_state, prepare_evidence
from agents_remember.application.review_evidence_map import (
    NAME_JUDGMENTS,
    NAME_PROOFS,
)
from agents_remember.application.review_evidence_packet import (
    PLANNED_OWNER,
    SideState,
    build_evidence_packet,
)
from agents_remember.application.review_tree_comparison import reopened_trees, write_files_tree
from agents_remember.models.knowledge.review_evidence import (
    EvidenceCollection,
    EvidenceGroupAddress,
    EvidenceGroupMap,
    EvidenceMap,
    EvidencePacket,
    SupplementalReceipt,
    packet_errors,
)
from agents_remember.models.knowledge.review_trees import (
    ReviewKnowledgeSide,
    ReviewTreeComparisonRecord,
    ReviewTreeSide,
)
from agents_remember.models.tools.public_roster import PUBLIC_TOOLS
from pydantic import ValidationError

AT = "2026-10-18T00:00:00Z"
TASK = "260928_maintained-invariant-knowledge"
LEAF = "260928-MIK-L211"
OTHER_LEAF = "260928-MIK-L199"
HISTORY = f"knowledge/history/{LEAF}.json"
OTHER_HISTORY = f"knowledge/history/{OTHER_LEAF}.json"
INVARIANT = "INV-AAAAAA"

# 714 rows of a padded note: the real L183 history file this rule was written for holds 714 rows in
# 1.15 MB, which is more than one bounded page and more than one scan of the blob.
ROW_COUNT = 714
PADDING = "the row a reviewer recorded about this part of the change " * 28


def _repository(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


def _history(rows: list[dict[str, object]], leaf: str = LEAF) -> bytes:
    return json.dumps(
        {"schema": "ar-history/v1", "leaf": leaf, "closed": False, "rows": rows}
    ).encode()


def _rows(count: int, *, subject: str = INVARIANT, prefix: str = "ROW") -> list[dict[str, object]]:
    return [
        {
            "id": f"{prefix}-{index:06d}",
            "subject": subject if index % 2 else f"hunk:sha256:{index:064d}",
            "disposition": "no_impact",
            "reason": PADDING,
            "items": [],
            "covers": [],
            "revision": 1,
        }
        for index in range(count)
    ]


def _sidecar(index: int) -> bytes:
    return json.dumps(
        {
            "schema": "ar-onboarding-file/v1",
            "path": f"mcp/src/agents_remember/part_{index}.py",
            "references": {},
            "realizes": [],
            "proves": [
                {
                    "id": f"PRF-{index:06d}",
                    "invariant": INVARIANT,
                    "anchor": {
                        "locator": {"kind": "symbol", "name": f"test_part_{index}"},
                        "blob": "c" * 40,
                        "content": "sha256:" + "2" * 64,
                    },
                    "facet": "a part of the change is proved",
                }
            ],
        }
    ).encode()


def _world(
    tmp_path: Path, *, memory: dict[str, bytes], base_memory: dict[str, bytes] | None = None
) -> dict[str, object]:
    """One comparison whose memory side moved from one tree to another."""

    code = _repository(tmp_path / "code")
    repo = _repository(tmp_path / "memory")
    base = write_files_tree(code, {"src/app.py": b"def run():\n    return 1\n"})
    candidate = write_files_tree(code, {"src/app.py": b"def run():\n    return 2\n"})
    memory_base = write_files_tree(
        repo,
        base_memory
        if base_memory is not None
        else {f"knowledge/invariants/{INVARIANT}-one.json": b"{}\n"},
    )
    memory_candidate = write_files_tree(repo, memory)
    record = ReviewTreeComparisonRecord(
        task_id=TASK,
        leaf_id=LEAF,
        number=3,
        code_base=ReviewTreeSide(repository=str(code), tree=base),
        code_candidate=ReviewTreeSide(repository=str(code), tree=candidate),
        memory_base=ReviewTreeSide(repository=str(repo), tree=memory_base),
        memory_candidate=ReviewTreeSide(repository=str(repo), tree=memory_candidate),
        recorded_at=AT,
    )
    return {
        "repo": repo,
        "record": record,
        "trees": reopened_trees(tmp_path / "coordination", record, None),
        "memory_candidate": memory_candidate,
    }


def _errors(packet: EvidencePacket) -> tuple[str, ...]:
    """The packet's cross-entry errors: a packet declares no read limits, so none is an approval gap."""

    return packet_errors(packet)


def _map() -> EvidenceMap:
    return EvidenceMap(coverage="the selected change, nothing wider")


def _prepared(world: dict[str, object], **over: object):
    arguments: dict[str, object] = {
        "scope": "the change to src/app.py",
        "question": "why did run() return 2?",
        "evidence_map": _map(),
    }
    arguments.update(over)
    return prepare_evidence(world["trees"], **arguments)  # type: ignore[arg-type]


def _collection(prepared, name: str) -> EvidenceCollection:
    collection = prepared.packet.collection(name)
    assert collection is not None
    return collection


# --- rule 1: the comparison scopes the evidence -----------------------------------------------


def test_judgments_count_the_changed_files_rows_whole_and_carry_every_one(tmp_path: Path) -> None:
    world = _world(tmp_path, memory={HISTORY: _history(_rows(ROW_COUNT))})
    reason = _rows(ROW_COUNT)[0]["reason"]
    assert isinstance(reason, str)
    assert len(reason) > 1_000  # one page of the file is not the file

    prepared = _prepared(world)
    judgments = _collection(prepared, NAME_JUDGMENTS)

    assert judgments.count == ROW_COUNT  # the whole file's rows are counted, not one page's
    assert "714" in judgments.population
    # MIK-R211 v4 rules 1-2: the packet holds the complete map, so every row is carried beside it,
    # with no limit text and no omission standing in for the rows.
    assert len(judgments.items) == ROW_COUNT
    assert {item["row_id"] for item in judgments.items} == {row["id"] for row in _rows(ROW_COUNT)}
    assert judgments.limit is None
    assert not [
        entry
        for entry in prepared.packet.evidence_map.omissions
        if entry.item == NAME_JUDGMENTS or entry.kind == "bounded-collection"
    ]
    assert not _errors(prepared.packet)


def test_judgments_carry_only_the_history_file_the_comparison_changed(tmp_path: Path) -> None:
    world = _world(
        tmp_path,
        memory={
            HISTORY: _history(_rows(3)),
            OTHER_HISTORY: _history(_rows(4, prefix="OTH"), OTHER_LEAF),
        },
        base_memory={
            f"knowledge/invariants/{INVARIANT}-one.json": b"{}\n",
            OTHER_HISTORY: _history(_rows(4, prefix="OTH"), OTHER_LEAF),
        },
    )

    judgments = _collection(_prepared(world), NAME_JUDGMENTS)

    assert {item["path"] for item in judgments.items} == {HISTORY}
    assert judgments.count == 3
    assert not any(str(item["row_id"]).startswith("OTH-") for item in judgments.items)


def test_a_planned_binding_reads_only_the_leafs_own_history_file(tmp_path: Path) -> None:
    world = _world(
        tmp_path,
        memory={
            HISTORY: _history(_rows(2)),
            OTHER_HISTORY: _history(_rows(4, prefix="OTH"), OTHER_LEAF),
        },
    )
    record: ReviewTreeComparisonRecord = world["record"]  # type: ignore[assignment]
    planned = record.model_copy(update={"memory_base": record.memory_candidate})
    trees = reopened_trees(tmp_path / "planned", planned, None)

    prepared = prepare_evidence(
        trees,
        scope="the planned change",
        question="what will this change?",
        evidence_map=_map(),
        comparison_owner=PLANNED_OWNER,
    )
    judgments = _collection(prepared, NAME_JUDGMENTS)

    assert {item["path"] for item in judgments.items} == {HISTORY}
    assert judgments.count == 2
    assert not any(str(item["row_id"]).startswith("OTH-") for item in judgments.items)
    assert _collection(prepared, NAME_PROOFS).state == "measured-empty"


def test_a_planned_leaf_without_its_own_history_file_selects_nothing(tmp_path: Path) -> None:
    world = _world(tmp_path, memory={OTHER_HISTORY: _history(_rows(4, prefix="OTH"), OTHER_LEAF)})
    record: ReviewTreeComparisonRecord = world["record"]  # type: ignore[assignment]
    planned = record.model_copy(update={"memory_base": record.memory_candidate})
    trees = reopened_trees(tmp_path / "planned", planned, None)

    prepared = prepare_evidence(
        trees,
        scope="the planned change",
        question="what will this change?",
        evidence_map=_map(),
        comparison_owner=PLANNED_OWNER,
    )
    judgments = _collection(prepared, NAME_JUDGMENTS)

    assert judgments.state == "not-selected"
    assert judgments.items == ()
    assert judgments.reason is not None and HISTORY in judgments.reason
    assert any(
        entry.item == NAME_JUDGMENTS and entry.kind == "not-selected-collection"
        for entry in prepared.packet.evidence_map.omissions
    )
    assert not _errors(prepared.packet)


# --- rule 2: a bound cuts, and the cut is stated ----------------------------------------------


def test_every_changed_sidecar_is_read_however_many(tmp_path: Path) -> None:
    """Ruling 6085456d, addendum (c): a path ceiling cut these at 64; now every one is read.

    The old ceiling left the tail of the sidecars unread and named them in one omission. With the
    ceiling gone, all 70 files are read, their entries all arrive, and no omission speaks of a cut.
    """

    sidecars = {
        f"onboarding/mcp/src/agents_remember/part_{index}.py.json": _sidecar(index)
        for index in range(70)
    }
    world = _world(tmp_path, memory={HISTORY: _history(_rows(2)), **sidecars})

    prepared = _prepared(world)
    proofs = _collection(prepared, NAME_PROOFS)

    assert proofs.state == "present"
    assert proofs.count == 70  # every one of the 70 sidecars was read
    assert "70" in proofs.population
    # The map may still state that it addresses these 70 records rather than listing them; that is
    # the map's own shape, not a cut. What the ceiling used to add -- an omission saying the tail was
    # never read -- is what must not appear.
    assert not [
        entry
        for entry in prepared.packet.evidence_map.omissions
        if entry.item == NAME_PROOFS and "per collection" in entry.detail
    ]
    assert not _errors(prepared.packet)


def test_a_partial_memory_index_names_every_file_it_could_not_read(tmp_path: Path) -> None:
    """MIK-R211 v4 rule 1 and Q6: every unread file is named, and no prose bound refuses the list."""

    problems = tuple(
        (f"knowledge/invariants/INV-{index:06d}-x.json", "unsupported") for index in range(5_000)
    )
    side = ReviewKnowledgeSide(
        side="after",
        state="available",
        tree="a" * 40,
        index_state="partial",
        problems=problems,
    )

    state = _memory_state(side)

    detail = state.detail or ""
    assert f"{len(problems)} file(s) failed" in detail
    assert all(path in detail for path, _reason in problems)
    assert "more" not in detail
    assert len(detail) > 20_000  # longer than the prose bound the packet used to refuse

    world = _world(tmp_path, memory={HISTORY: _history(_rows(2))})
    packet = build_evidence_packet(
        world["record"],  # type: ignore[arg-type]
        repository_id="agents-remember",
        scope="the change to src/app.py",
        question="why did run() return 2?",
        code_base=SideState("available"),
        code_candidate=SideState("available"),
        memory_before=SideState("available", index_state="complete"),
        memory_after=state,
        evidence_map=_map(),
        recorded_at=AT,
    )
    assert packet.comparison.memory_candidate.detail == detail


def test_a_collection_that_carries_fewer_items_than_its_count_names_the_bound() -> None:
    with pytest.raises(ValidationError, match="names the bound that cut"):
        EvidenceCollection(
            name="judgments",
            producer="agents-remember:260928_maintained-invariant-knowledge/260928-MIK-L211/3",
            population="714 judgment row(s)",
            state="present",
            count=714,
            items=({"row_id": "ROW-000001", "path": HISTORY},),
        )

    stated = EvidenceCollection(
        name="judgments",
        producer="agents-remember:260928_maintained-invariant-knowledge/260928-MIK-L211/3",
        population="714 judgment row(s)",
        state="present",
        count=714,
        items=({"row_id": "ROW-000001", "path": HISTORY},),
        limit="200 record(s) per collection",
    )
    assert stated.limit == "200 record(s) per collection"


def test_a_cut_the_evidence_map_never_names_is_a_packet_error(tmp_path: Path) -> None:
    world = _world(tmp_path, memory={HISTORY: _history(_rows(2))})
    record: ReviewTreeComparisonRecord = world["record"]  # type: ignore[assignment]
    cut = EvidenceCollection(
        name="judgments",
        producer=f"agents-remember:{TASK}/{LEAF}/3",
        population="714 judgment row(s)",
        state="present",
        count=714,
        items=({"row_id": "ROW-000001", "path": HISTORY},),
        limit="200 record(s) per collection",
    )
    packet: EvidencePacket = build_evidence_packet(
        record,
        repository_id="agents-remember",
        scope="the change to src/app.py",
        question="why did run() return 2?",
        code_base=SideState("available"),
        code_candidate=SideState("available"),
        memory_before=SideState("available", index_state="complete"),
        memory_after=SideState("available", index_state="complete"),
        evidence_map=_map(),
        collections={"judgments": cut},
        recorded_at=AT,
    )

    problems = packet_errors(packet)
    assert any("does not name what it left out" in problem for problem in problems)
    # The packet declares no read limits, so none is reported as an unmeasured approval gap; a
    # packet stored before the limits went still reads, its old field kept for that alone.
    assert packet.limits is None
    assert not [problem for problem in problems if "unmeasured" in problem]
    stored = packet.model_dump(mode="json", by_alias=True) | {
        "limits": {
            "bytes_per_read": 262144,
            "paths_per_packet": 64,
            "records_per_collection": 200,
            "relationship_depth": 2,
            "pages_per_read": 4,
        }
    }
    assert EvidencePacket.model_validate(stored).limits is not None


# --- rule 2: one budget, groups collapse, nothing refused --------------------------------------


def test_the_map_declares_no_budget_and_keeps_every_item(
    tmp_path: Path,
) -> None:
    """Ruling 6085456d: the map states the size it reached, and nothing that could bind it.

    The Manager measured the residue: ``budget_bytes`` equalled ``measured_bytes``, so a budget derived
    from the output can never bind, and ``limits.packet_bytes`` still declared 102,400 beside a
    536,465-byte packet with ``measurement: null``. Both declarations are gone.
    """

    world = _map_world(tmp_path)
    group_map = _prepared(world).packet.map
    assert group_map is not None

    assert "budget_bytes" not in group_map.model_dump()
    assert _prepared(world).packet.limits is None
    assert group_map.collapsed == ()
    assert group_map.fallback is False
    assert group_map.total_items == sum(
        group.count for group in group_map.groups if group.kind != "counts"
    )
    assert all(group.items for group in group_map.groups if group.kind != "counts")


def test_a_map_inside_its_budget_keeps_its_detail(tmp_path: Path) -> None:
    """Under its budget the map carries its rows, and no group is collapsed."""

    world = _world(tmp_path, memory={HISTORY: _history(_rows(40))})

    prepared = _prepared(world)
    judgments = _collection(prepared, NAME_JUDGMENTS)

    assert len(judgments.items) == 40
    assert not [
        entry for entry in prepared.packet.evidence_map.omissions if "byte budget" in entry.detail
    ]
    assert not any("byte budget" in line for line in prepared.packet.limitations)


# --- rules 1-2: the packet is the map, and the map is held to one budget ------------------------


def _invariant(id_: str, *, title: str = "one guarantee") -> bytes:
    return (
        json.dumps(
            {
                "schema": "ar-invariant/v1",
                "id": id_,
                "origin": {"task": TASK, "leaf": LEAF},
                "revision": 1,
                "status": "proposed",
                "statement": f"{id_} {title}",
                "applicability": "Every admitted candidate write.",
                "conditions": [],
                "exclusions": [],
                "supersedes": [],
                "admission": {
                    "criteria": ["spans_locations"],
                    "justification": "The fixture states one admitted guarantee.",
                },
            }
        )
        + "\n"
    ).encode()


def _map_sidecar(
    path: str,
    *,
    realizes: list[dict[str, object]] | None = None,
    proves: list[dict[str, object]] | None = None,
) -> bytes:
    body: dict[str, object] = {
        "schema": "ar-onboarding-file/v1",
        "path": path,
        "realizes": realizes or [],
        "proves": proves or [],
        "references": {},
    }
    if realizes:
        body["references"] = {
            "1": {
                "note": f"{path} enforces it",
                "targets": [
                    {
                        "kind": "code",
                        "anchor": {
                            "blob": "b" * 40,
                            "content": "sha256:" + "c" * 64,
                            "locator": {"kind": "line_range", "start": 3, "end": 7},
                        },
                    }
                ],
            }
        }
    return (json.dumps(body) + "\n").encode()


def _entry(
    id_: str, invariant: str, *, blob: str = "b" * 40, content: str = "sha256:" + "c" * 64
) -> dict[str, object]:
    """One realization entry: the anchor names its own path, range, blob and content identity."""

    return {
        "id": id_,
        "invariant": invariant,
        "role": "enforcement",
        "rationale": f"{id_} enforces {invariant}",
        # An entry's anchor sits in its sidecar's own file, so it names no path: the sidecar's own
        # ``path`` is the file the anchor is in, and the index reads the entry's path from it.
        "anchor": {
            "blob": blob,
            "content": content,
            "locator": {"kind": "line_range", "start": 1, "end": 2},
        },
    }


def _proof(id_: str, invariant: str, *, blob: str, content: str) -> dict[str, object]:
    """One proof entry: what the test establishes, anchored at the proving file's own range."""

    return {
        "id": id_,
        "invariant": invariant,
        "facet": "the change is proved by its own test",
        "anchor": {
            "blob": blob,
            "content": content,
            "locator": {"kind": "line_range", "start": 1, "end": 2},
        },
    }


def _map_world(tmp_path: Path) -> dict[str, object]:
    """A comparison whose changed invariant has one location, one proof and one history row.

    The sidecars' anchors carry the code tree's own blob and content hash: the index records an
    entry only where its anchor still matches the bytes, so a fixture with invented hashes would test
    a map of nothing.
    """

    code = _repository(tmp_path / "code")
    base = write_files_tree(code, {"src/app.py": b"def run():\n    return 1\n"})
    body = b"def run():\n    return 2\n"
    candidate = write_files_tree(code, {"src/app.py": body})
    blob = subprocess.run(
        ["git", "-C", str(code), "rev-parse", f"{candidate}:src/app.py"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    content = "sha256:" + hashlib.sha256(body).hexdigest()

    repo = _repository(tmp_path / "memory")
    memory_base = write_files_tree(
        repo,
        {
            f"knowledge/invariants/{INVARIANT}-one-guarantee.json": _invariant(
                INVARIANT, title="one guarantee, first revision"
            ),
            "knowledge/invariants/INV-CCCCCC-an-unchanged-guarantee.json": _invariant("INV-CCCCCC"),
            "system/retired-notes.md": b"a note the change deleted\n",
        },
    )
    memory_candidate = write_files_tree(
        repo,
        {
            f"knowledge/invariants/{INVARIANT}-one-guarantee.json": _invariant(
                INVARIANT, title="one guarantee, second revision"
            ),
            "knowledge/invariants/INV-CCCCCC-an-unchanged-guarantee.json": _invariant("INV-CCCCCC"),
            "onboarding/src/app.py.json": _map_sidecar(
                "src/app.py",
                realizes=[_entry("RLZ-AAAAAA", INVARIANT, blob=blob, content=content)],
            ),
            "onboarding/tests/test_app.py.json": _map_sidecar(
                "tests/test_app.py",
                proves=[_proof("PRF-BBBBBB", INVARIANT, blob=blob, content=content)],
            ),
            "onboarding/README.md.json": _map_sidecar("README.md"),
            # Changed memory that is neither an invariant nor a sidecar, and not JSON: priority 5.
            "system/settings.md": b"# settings the change added\n",
            "knowledge/families/FAM-AAAAAA-one-family.json": b"{}\n",
            HISTORY: _history(
                [
                    {
                        "id": "ROW-MAP001",
                        "subject": f"membership:{INVARIANT}",
                        "disposition": "no_family",
                        "items": [],
                        "reason": "the guarantee still holds after the change",
                        "revision": 1,
                    }
                ]
            ),
        },
    )
    record = ReviewTreeComparisonRecord(
        task_id=TASK,
        leaf_id=LEAF,
        number=4,
        code_base=ReviewTreeSide(repository=str(code), tree=base),
        code_candidate=ReviewTreeSide(repository=str(code), tree=candidate),
        memory_base=ReviewTreeSide(repository=str(repo), tree=memory_base),
        memory_candidate=ReviewTreeSide(repository=str(repo), tree=memory_candidate),
        recorded_at=AT,
    )
    return {
        "repo": repo,
        "record": record,
        "trees": reopened_trees(tmp_path / "coordination", record, None),
        "memory_candidate": memory_candidate,
    }


def test_the_map_counts_each_changed_invariant_by_locations_proofs_and_history(
    tmp_path: Path,
) -> None:
    """Rule 1: families, changed invariants, and each one counted by what the comparison changed."""

    world = _map_world(tmp_path)

    prepared = _prepared(world)
    group_map = prepared.packet.map

    assert group_map is not None
    names = [group.name for group in group_map.groups]
    assert f"knowledge/invariants/{INVARIANT}-one-guarantee.json" in names
    group = next(group for group in group_map.groups if INVARIANT in group.name)
    assert group.kind == "invariant"
    assert group.counts["locations"] == 1
    assert group.counts["proofs"] == 1
    assert group.counts["history"] == 1
    assert group.count == 3
    # A location's address is its path and range; a proof's is its id; a row's is its file and id.
    labels = {item.kind: item for item in group.items}
    assert set(labels) == {"location", "proof", "history"}
    assert labels["location"].detail == "src/app.py:1-2"
    assert labels["proof"].detail == "proof PRF-BBBBBB"
    assert labels["history"].address == f"{HISTORY}#ROW-MAP001"
    assert group.address is not None and group.address.tool == "read_ar_files"
    _assert_every_changed_path_is_named_where_the_explainer_finds_it(world, group_map)


def _assert_every_changed_path_is_named_where_the_explainer_finds_it(
    world: dict[str, object], group_map: EvidenceGroupMap
) -> None:
    """Rule 1: every path the comparison changed appears, and every group says where it is.

    The comparison's own diffs are the source: a changed memory file that is neither an invariant
    nor a sidecar (or is not JSON, or was deleted) is still a priority-5 folder group. A group's
    address is a registered tool, a workspace file or a native Git command in a checkout; a folder
    group's command is the diff of exactly its folder between the comparison's own trees.
    """

    record = world["record"]
    assert isinstance(record, ReviewTreeComparisonRecord)
    named: set[str] = set()
    for group in group_map.groups:
        # A history row's address is its file and id: the file is what it names.
        named.update(item.address.split("#", 1)[0] for item in group.items)
        address = group.address
        assert address is not None, group.name
        assert address.tool in {"git", "file"} or address.tool in PUBLIC_TOOLS, group.name
        if address.tool == "git":
            assert address.arguments["directory"] in {"trees/code", "trees/memory"}, group.name
        if address.tool == "file":
            assert address.arguments["path"].startswith(("trees/", "reference/")), group.name
        paths = address.arguments.get("paths")
        named.update([paths] if isinstance(paths, str) else paths or ())
    for side in ("code", "memory"):
        base, candidate = getattr(record, f"{side}_base"), getattr(record, f"{side}_candidate")
        changed = subprocess.run(
            ["git", "-C", candidate.repository, "diff", "--name-only", base.tree, candidate.tree],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
        assert changed and set(changed) <= named, (side, sorted(set(changed) - named))
        folders = {
            group.name: group for group in group_map.groups if group.name.startswith(f"{side}:")
        }
        for name, group in folders.items():
            assert group.address == EvidenceGroupAddress(
                tool="git",
                arguments={
                    "directory": f"trees/{side}",
                    "command": f"git diff --name-status {base.tree} {candidate.tree} -- "
                    + name.removeprefix(f"{side}:"),
                },
            ), name
    assert "memory:system" in {group.name for group in group_map.groups}


def test_the_map_counts_what_it_did_not_change_and_the_packet_carries_no_second_copy(
    tmp_path: Path,
) -> None:
    """Rules 1-2: the unchanged population is a count, and every collection carries its rows."""

    world = _map_world(tmp_path)

    prepared = _prepared(world)
    group_map = prepared.packet.map
    assert group_map is not None

    counts = [group for group in group_map.groups if group.kind == "counts"]
    assert len(counts) == 1
    assert counts[0].count == 1  # INV-CCCCCC, which the comparison did not change
    # The count is opened in the memory checkout with native Git, never with a removed tool.
    record = world["record"]
    assert isinstance(record, ReviewTreeComparisonRecord)
    assert counts[0].address == EvidenceGroupAddress(
        tool="git",
        arguments={
            "directory": "trees/memory",
            "command": f"git ls-tree --name-only {record.memory_candidate.tree} "
            "knowledge/invariants/",
        },
    )
    assert group_map.total_items == sum(
        group.count for group in group_map.groups if group.kind != "counts"
    )
    # Every collection carries every record it counts (MIK-R211 v4 rule 1): no width hides rows
    # behind a call, and no collection names a limit.
    for collection in prepared.packet.collections:
        assert len(collection.items) == (collection.count or 0), collection.name
        assert collection.limit is None, collection.name
    wide = _collection(prepared, NAME_JUDGMENTS)
    assert wide.count == 1 and len(wide.items) == 1


def _receipt(
    id_: str, source: str, *, establishes: str = "it states the agreement"
) -> SupplementalReceipt:
    return SupplementalReceipt(
        receipt_id=id_,
        canonical_source=source,
        producer="leaf 260928-MIK-L211",
        authority="the task document this leaf is registered by",
        digest="a" * 64,
        byte_size=1_024,
        read_state="present",
        capture_identity="the bytes read when the packet was made",
        establishes=establishes,
    )


def test_the_map_states_the_agreed_plan_first_and_the_leafs_documents(tmp_path: Path) -> None:
    """Rule 1's first priority is the agreed plan; the leaf's own documents follow it."""

    world = _map_world(tmp_path)
    record = world["record"]
    assert isinstance(record, ReviewTreeComparisonRecord)
    planned = record.model_copy(update={"memory_base": record.memory_candidate})
    trees = reopened_trees(tmp_path / "planned", planned, None)
    supplemental = (
        _receipt("agreed-manifest", f"notes/reference/{LEAF}/AGREED.md"),
        _receipt("leaf-document", "201_the-explainer-builds-on-real-data-previewed-live.json"),
    )

    prepared = prepare_evidence(
        trees,
        scope="the planned change",
        question="what will this change?",
        evidence_map=_map(),
        supplemental=supplemental,
        comparison_owner=PLANNED_OWNER,
    )
    group_map = prepared.packet.map
    assert group_map is not None

    first = group_map.groups[0]
    assert first.kind == "plan"
    assert first.name == "the agreed plan"
    assert [item.address for item in first.items] == [f"notes/reference/{LEAF}/AGREED.md"]
    # The leaf's reference notes are copied into the workspace's reference/, so the plan is a file
    # there; the leaf's other documents stay in the task and are opened with read_ar_files.
    assert first.address == EvidenceGroupAddress(
        tool="file", arguments={"path": "reference/AGREED.md"}
    )
    documents = [group for group in group_map.groups if group.name == "the leaf's documents"]
    assert len(documents) == 1
    assert [item.address for item in documents[0].items] == [
        "201_the-explainer-builds-on-real-data-previewed-live.json"
    ]
    assert documents[0].address is not None and documents[0].address.tool == "read_ar_files"
    assert group_map.total_items >= 2


def test_a_planned_bindings_map_states_the_leafs_own_history_rows(tmp_path: Path) -> None:
    """A planned binding changed nothing, so its map is the leaf's own history and documents."""

    world = _map_world(tmp_path)
    record = world["record"]
    assert isinstance(record, ReviewTreeComparisonRecord)
    planned = record.model_copy(update={"memory_base": record.memory_candidate})
    trees = reopened_trees(tmp_path / "planned", planned, None)

    prepared = prepare_evidence(
        trees,
        scope="the planned change",
        question="what will this change?",
        evidence_map=_map(),
        comparison_owner=PLANNED_OWNER,
    )
    group_map = prepared.packet.map
    assert group_map is not None

    history = [
        group for group in group_map.groups if group.kind == "folder" and "history" in group.name
    ]
    assert len(history) == 1, [group.name for group in group_map.groups]
    assert history[0].count == 1
    assert history[0].items[0].kind == "history"
    assert history[0].address == EvidenceGroupAddress(
        tool="file", arguments={"path": f"trees/memory/{HISTORY}"}
    )
    # Nothing else is claimed as changed: a planned binding changed no invariant.
    assert not [group for group in group_map.groups if group.kind == "invariant"]
