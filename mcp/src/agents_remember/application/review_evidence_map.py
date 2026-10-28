"""Group measured evidence into a map that keeps its detail, and build the packet beside it."""

from __future__ import annotations

import json
import re
import shlex
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

import apsw

from agents_remember.application.review_evidence_packet import (
    COMPARISON_OWNER,
    PLANNED_OWNER,
    SideState,
    build_evidence_packet,
)
from agents_remember.application.review_evidence_read import (
    EvidenceReadReceipt,
    side_operand,
)
from agents_remember.application.review_tree_comparison import ReviewTrees
from agents_remember.kernel.git_command import run_git
from agents_remember.memory.knowledge_index import Entry, IndexMismatchError, KnowledgeIndex
from agents_remember.models.knowledge.review_evidence import (
    EvidenceClaim,
    EvidenceCollection,
    EvidenceGroup,
    EvidenceGroupAddress,
    EvidenceGroupItem,
    EvidenceGroupMap,
    EvidenceMap,
    EvidencePacket,
    SupplementalReceipt,
)
from agents_remember.models.knowledge.review_trees import (
    ReviewTreeComparisonRecord,
)

NAME_SOURCES = "source-inventory-and-linked-locations"

NAME_FAMILY = "family-guarantees-and-membership"

NAME_JUDGMENTS = "judgments"

NAME_PROOFS = "proofs"


# An invariant's record file is named by its own id, and a judgment row's subject names the
# invariant it was recorded against ("membership:INV-YY035T"), so a row is one of the changed
# invariants' rows when any id its subject carries is one the comparison itself changed.
# The folder depth a map group is named at: one group per openable folder, not per leaf directory.
_FOLDER_DEPTH = 2

#: The supplemental receipt whose source is MIK-R189's agreed plan (the map's first priority).
AGREED_MANIFEST_RECEIPT = "agreed-manifest"

_INVARIANT_FILE = re.compile(r"^(?P<id>INV-[A-Z0-9]+)-")

_INVARIANT_TOKEN = re.compile(r"INV-[A-Z0-9]+")

#: Where a group is found inside the explainer's own workspace (MIK-R211 v5 rule 1, rule 12's layout):
#: a file it opens, or a native Git command it runs in one of its checkouts. Any other address names a
#: registered general tool -- the leaf's task documents are not in the workspace -- and none names a
#: removed one.
WORKSPACE_FILE = "file"

NATIVE_GIT = "git"

#: The task's reference notes for a leaf, which the build copies into the workspace's ``reference/``.
_TASK_REFERENCE = "notes/reference/"


def _changed_invariant_ids(record: ReviewTreeComparisonRecord) -> frozenset[str]:
    """The invariant record ids whose files the comparison changed, by their own file names."""

    names = _changed_memory_paths(record, "knowledge/invariants")
    found: set[str] = set()
    for name in names or ():
        match = _INVARIANT_FILE.match(name.rsplit("/", 1)[-1])
        if match:
            found.add(match.group("id"))
    return frozenset(found)


def _names_changed_invariant(row: dict[str, object], changed: frozenset[str]) -> bool:
    """Whether one judgment row's subject names an invariant the comparison itself changed."""

    subject = str(row.get("subject") or "")
    return any(name in changed for name in _INVARIANT_TOKEN.findall(subject))


def _group_map(
    trees: ReviewTrees,
    *,
    rows: tuple[dict[str, object], ...],
    supplemental: tuple[SupplementalReceipt, ...] = (),
    owner: str = COMPARISON_OWNER,
) -> EvidenceGroupMap | None:
    """The packet's map: the comparison's changed items, grouped by invariant and folder (rule 1).

    The groups and their counts come from what the comparison already owns -- the memory candidate's
    index for each invariant's realizations and proofs, the changed sidecar set for which of those
    the comparison itself changed, and the history rows the judgment read parsed -- so the map costs
    no read the packet did not already make. Every group carries every address it counts: the map
    has no budget and collapses nothing to fit (MIK-R211 v4 rule 2).

    ``None`` is the answer when the memory index cannot be opened at all: a map of a comparison whose
    memory side is unreadable is not a smaller map, it is a wrong one, and the packet's own sides
    already state that.
    """

    record = trees.record
    changed_invariants = _changed_invariant_ids(record)
    stems: dict[str, str] = {}
    # Only a path the candidate still carries names a group: a deleted invariant file would name the
    # group by something a reader cannot open.
    for name in _changed_memory_paths(record, "knowledge/invariants", statuses="ACMR") or ():
        stem = name.rsplit("/", 1)[-1].removesuffix(".json")
        stems[stem] = name
        matched = _INVARIANT_FILE.match(stem)
        if matched:
            # A group is named by the invariant's own file, looked up by the id the rows and the
            # index carry: the file's slug is the readable half of the same name.
            stems[matched.group("id")] = name
    sidecars = set(_changed_memory_paths(record, "onboarding") or ())
    code_paths = _changed_code_paths(record)
    database = trees.after.database
    if database is None:
        return None
    try:
        with KnowledgeIndex(database) as index:
            groups = _invariant_groups(index, changed_invariants, stems, sidecars, rows)
            unchanged = len(index.record_ids("invariant").value) - len(changed_invariants)
    except (apsw.Error, IndexMismatchError, OSError):
        return None
    # The agreed plan is the map's first priority, and the leaf's own documents are its own sources
    # (rule 1). Both arrive as supplemental receipts, so the map states them without opening a file.
    plan, documents = _receipt_groups(supplemental, record.leaf_id)
    groups = [*([plan] if plan is not None else []), *groups]
    if owner == PLANNED_OWNER:
        # A planned binding changed nothing, so its map has no invariant groups: what it has is the
        # leaf's own history file (when it exists) and the documents above.
        planned_rows = _planned_history_group(record, rows)
        if planned_rows is not None:
            groups.append(planned_rows)
    covered_proofs = {
        item.address for group in groups for item in group.items if item.kind == "proof"
    }
    # Priority 5 is every other changed memory file, from the comparison's own memory diff: any path
    # an earlier group already names (a location's or proof's sidecar, a row's history file, an
    # invariant's own file) stays there, and every other one -- whatever its folder or suffix, and
    # deleted ones too -- is a folder group's.
    memory_trees = (_side_tree(record, "memory-base"), record.memory_candidate.tree)
    code_trees = (_side_tree(record, "code-base"), record.code_candidate.tree)
    other_memory = set(_memory_diff(record) or ()) - _named_paths(groups)
    groups = [*groups, *_folder_groups(other_memory, "memory", memory_trees)]
    groups = [*groups, *_folder_groups(code_paths - covered_proofs, "code", code_trees)]
    groups = [*groups, *documents]
    if unchanged > 0:
        groups.append(
            EvidenceGroup(
                name="unchanged-invariants",
                kind="counts",
                count=unchanged,
                address=_git_address(
                    "memory",
                    "ls-tree",
                    "--name-only",
                    record.memory_candidate.tree,
                    "knowledge/invariants/",
                ),
            )
        )
    return _fit_map(groups)


def _git_address(side: str, *arguments: str) -> EvidenceGroupAddress:
    """A native Git command the explainer runs in its own ``trees/<side>`` checkout (rule 1)."""

    return EvidenceGroupAddress(
        tool=NATIVE_GIT,
        arguments={"directory": f"trees/{side}", "command": shlex.join(("git", *arguments))},
    )


def _side_tree(record: ReviewTreeComparisonRecord, side: str) -> str | None:
    """The tree one base side is read at (the converted tree for a converted memory base)."""

    operand = side_operand(record, side)
    return None if isinstance(operand, EvidenceReadReceipt) else operand.tree


def _plan_address(items: Sequence[EvidenceGroupItem], leaf_id: str) -> EvidenceGroupAddress:
    """The plan as a ``reference/`` file when the build copied it in, else through read_ar_files."""

    own = f"{_TASK_REFERENCE}{leaf_id}/"
    if len(items) == 1 and items[0].address.startswith(own):
        path = f"reference/{items[0].address.removeprefix(own)}"
        return EvidenceGroupAddress(tool=WORKSPACE_FILE, arguments={"path": path})
    return EvidenceGroupAddress(
        tool="read_ar_files", arguments={"paths": [item.address for item in items]}
    )


def _named_paths(groups: Sequence[EvidenceGroup]) -> set[str]:
    """The paths the groups already name: their items' files and their addresses' own paths."""

    named = {item.address.split("#", 1)[0] for group in groups for item in group.items}
    for group in groups:
        paths = group.address.arguments.get("paths") if group.address is not None else None
        named.update([paths] if isinstance(paths, str) else paths or ())
    return named


def _receipt_groups(
    supplemental: tuple[SupplementalReceipt, ...], leaf_id: str
) -> tuple[EvidenceGroup | None, list[EvidenceGroup]]:
    """The leaf's own sources as map groups: the agreed plan first, then its other documents.

    A receipt already binds the bytes that were examined, so a group built from it costs no read: it
    names the document, states what the receipt establishes and gives the call that opens it. Only a
    present receipt is addressed; one that is unavailable is named in the collection that holds it.
    """

    plan_items: list[EvidenceGroupItem] = []
    document_items: list[EvidenceGroupItem] = []
    for receipt in supplemental:
        if receipt.read_state != "present":
            continue
        item = EvidenceGroupItem(
            kind="document",
            label=receipt.receipt_id,
            address=receipt.canonical_source,
            detail=(
                f"{receipt.establishes}"
                + (f" ({receipt.limitation})" if receipt.limitation else "")
            ),
        )
        if receipt.receipt_id == AGREED_MANIFEST_RECEIPT:
            plan_items.append(item)
        else:
            document_items.append(item)
    plan = (
        EvidenceGroup(
            name="the agreed plan",
            kind="plan",
            count=len(plan_items),
            items=tuple(plan_items),
            address=_plan_address(plan_items, leaf_id),
        )
        if plan_items
        else None
    )
    documents = (
        [
            EvidenceGroup(
                name="the leaf's documents",
                kind="folder",
                count=len(document_items),
                items=tuple(document_items),
                address=EvidenceGroupAddress(
                    tool="read_ar_files",
                    arguments={"paths": [item.address for item in document_items]},
                ),
            )
        ]
        if document_items
        else []
    )
    return plan, documents


def _planned_history_group(
    record: ReviewTreeComparisonRecord,
    rows: tuple[dict[str, object], ...],
) -> EvidenceGroup | None:
    """The leaf's own history rows, as one group, on a planned binding (rule 1's planned clause).

    A planned binding changed nothing, so no invariant is "changed" and no folder group would hold
    these rows; the leaf's own history file is the one source it has, and the map states it.
    """

    if not rows:
        return None
    items = tuple(
        EvidenceGroupItem(
            kind="history",
            label=str(row.get("id") or "row"),
            address=f"{record.leaf_id}#{row.get('id')}",
            detail=str(row.get("subject") or row.get("disposition") or "history row"),
        )
        for row in rows
    )
    return EvidenceGroup(
        name=f"memory:knowledge/history/{record.leaf_id}",
        kind="folder",
        count=len(rows),
        items=items,
        address=EvidenceGroupAddress(
            tool=WORKSPACE_FILE,
            arguments={"path": f"trees/memory/knowledge/history/{record.leaf_id}.json"},
        ),
    )


def _invariant_groups(
    index: KnowledgeIndex,
    changed: frozenset[str],
    stems: Mapping[str, str],
    sidecars: set[str],
    rows: tuple[dict[str, object], ...],
) -> list[EvidenceGroup]:
    """One group per changed invariant: its changed locations, proofs and history rows (rule 1)."""

    groups: list[EvidenceGroup] = []
    for invariant_id in sorted(changed):
        knowledge = index.invariant(invariant_id).value
        locations = sorted(
            (entry for entry in knowledge.realizations if entry.sidecar in sidecars),
            key=lambda entry: (entry.path, entry.id),
        )
        proofs = sorted(
            (entry for entry in knowledge.proofs if entry.sidecar in sidecars),
            key=lambda entry: (entry.path, entry.id),
        )
        naming = [
            row
            for row in rows
            if invariant_id in _INVARIANT_TOKEN.findall(str(row.get("record_id") or ""))
        ]
        name = stems.get(invariant_id, invariant_id)
        items = [
            *(
                EvidenceGroupItem(
                    kind="location",
                    label=entry.path,
                    address=entry.sidecar,
                    detail=_anchor_range(entry),
                )
                for entry in locations
            ),
            *(
                EvidenceGroupItem(
                    kind="proof",
                    label=entry.path,
                    address=entry.sidecar,
                    detail=f"proof {entry.id}",
                )
                for entry in proofs
            ),
        ]
        items.extend(
            EvidenceGroupItem(
                kind="history",
                label=str(row.get("path") or ""),
                address=f"{row.get('path')}#{row.get('row_id')}",
                detail=str(row.get("record_id") or ""),
            )
            for row in naming
        )
        count = len(locations) + len(proofs) + len(naming)
        if not count:
            continue
        groups.append(
            EvidenceGroup(
                name=name,
                kind="invariant",
                count=count,
                counts={"locations": len(locations), "proofs": len(proofs), "history": len(naming)},
                items=tuple(items),
                address=EvidenceGroupAddress(
                    tool="read_ar_files",
                    arguments={
                        "paths": stems.get(
                            invariant_id, f"knowledge/invariants/{invariant_id}.json"
                        )
                    },
                ),
            )
        )
    return groups


def _anchor_range(entry: Entry) -> str:
    """The path and range an entry's own anchor names, or its path when the anchor names neither.

    An entry's anchor sits in its sidecar's own source file (the sidecar model refuses one that names
    a path), so the address is the entry's own locator: a line range reads ``path:start-end`` and a
    symbol reads ``path#name``, which is what a reader opens.
    """

    anchor = entry.document.get("anchor")
    locator = anchor.get("locator") if isinstance(anchor, dict) else None
    if isinstance(locator, dict):
        start, end = locator.get("start"), locator.get("end")
        if isinstance(start, int) and isinstance(end, int):
            return f"{entry.path}:{start}-{end}"
        symbol = locator.get("name")
        if isinstance(symbol, str) and symbol:
            return f"{entry.path}#{symbol}"
    return entry.path


def _folder_groups(
    paths: Iterable[str],
    surface: str,
    trees: tuple[str | None, str],
    depth: int = _FOLDER_DEPTH,
) -> list[EvidenceGroup]:
    """The changed files no invariant group already counts, one group per folder (rule 1 priority 5).

    A group is the folder a reader opens, ``_FOLDER_DEPTH`` segments deep, and it carries every
    changed path in it: the addresses inside the group name the exact files. The group's own address
    is the native diff of that folder between the comparison's two trees, run in the explainer's
    ``trees/<surface>`` checkout, which also names a file the change deleted.
    """

    by_folder: dict[str, list[str]] = {}
    for path in sorted(paths):
        parts = path.split("/")
        folder = "/".join(parts[:depth]) if len(parts) > depth else (path.rsplit("/", 1)[0] or ".")
        by_folder.setdefault(folder if folder != path else ".", []).append(path)
    groups: list[EvidenceGroup] = []
    for folder, members in sorted(by_folder.items()):
        groups.append(
            EvidenceGroup(
                name=f"{surface}:{folder}",
                kind="folder",
                count=len(members),
                counts={("documents" if surface == "code" else "locations"): len(members)},
                items=tuple(
                    EvidenceGroupItem(
                        kind="document" if surface == "code" else "location",
                        label=path,
                        address=path,
                    )
                    for path in members
                ),
                address=_git_address(
                    surface,
                    "diff",
                    "--name-status",
                    *(tree for tree in trees if tree is not None),
                    "--",
                    *([folder] if folder != "." else members),
                ),
            )
        )
    return groups


def _fit_map(groups: Sequence[EvidenceGroup]) -> EvidenceGroupMap:
    """The map as it is built: every group keeps its addresses, whatever their size.

    Ruling 6085456d, addendum (c): "Nothing is collapsed to fit a budget." No byte count decides what
    the map carries any more, and the map declares no budget at all: one set from the output it
    measures restates ``measured_bytes`` and can never bind. ``collapsed`` and ``fallback`` are empty
    by construction.
    """

    working = tuple(groups)
    measured = _map_bytes(working)
    return EvidenceGroupMap(
        groups=working,
        measured_bytes=measured,
        total_items=sum(group.count for group in working if group.kind != "counts"),
    )


def _map_bytes(groups: Sequence[EvidenceGroup]) -> int:
    """The map's own size in bytes, as its groups' JSON: the size it states, not a bound."""

    payload = [json.loads(group.model_dump_json()) for group in groups]
    return len(json.dumps(payload, separators=(",", ":")).encode("utf-8"))


def _changed_code_paths(record: ReviewTreeComparisonRecord) -> set[str]:
    """The code paths the comparison changed between its two code trees."""

    before = side_operand(record, "code-base")
    if isinstance(before, EvidenceReadReceipt):
        return set()
    try:
        result = run_git(
            Path(record.code_candidate.repository),
            ["diff", "--name-only", "-z", before.tree, record.code_candidate.tree],
        )
    except OSError:
        return set()
    if result.returncode != 0:
        return set()
    return {name for name in result.stdout.split("\0") if name}


def _fit_packet(  # noqa: PLR0913 - one packet states its own inputs
    record: ReviewTreeComparisonRecord,
    *,
    collections: tuple[EvidenceCollection, ...],
    groups: EvidenceGroupMap | None,
    sides: Mapping[str, SideState],
    scope: str,
    question: str,
    evidence_map: EvidenceMap,
    claims: tuple[EvidenceClaim, ...],
    supplemental: tuple[SupplementalReceipt, ...],
    comparison_owner: str,
    comparison_reference: str | None,
) -> EvidencePacket:
    """One packet, built once: no budget and no count decides what it carries.

    Ruling 6085456d, addendum (c) and MIK-R211 v4 rules 1-2: the map keeps every group's addresses
    and every collection keeps every record, so this adds no omission of its own.
    """

    return build_evidence_packet(
        record,
        repository_id=record.task_id,
        scope=scope,
        question=question,
        code_base=sides["base"],
        code_candidate=sides["candidate"],
        memory_before=sides["before"],
        memory_after=sides["after"],
        evidence_map=evidence_map,
        collections={collection.name: collection for collection in collections},
        claims=claims,
        limitations=_limitations(record, collections)
        + tuple(side.detail for side in (sides["before"], sides["after"]) if side.detail),
        recorded_at=record.recorded_at,
        supplemental=supplemental,
        comparison_owner=comparison_owner,
        comparison_reference_value=comparison_reference,
        group_map=groups,
    )


def _changed_memory_paths(
    record: ReviewTreeComparisonRecord, prefix: str, *, statuses: str | None = None
) -> list[str] | None:
    """The paths one comparison changed under *prefix*, in one stated path order.

    The diff is taken between the same two memory trees the packet reads, so a file that the
    comparison did not change is never named and a file it did change is never missed. ``None``
    says the diff itself did not answer, which is a different fact from "it changed nothing".
    """

    names = _memory_diff(record, prefix, statuses=statuses)
    return None if names is None else [name for name in names if name.endswith(".json")]


def _memory_diff(
    record: ReviewTreeComparisonRecord, *pathspec: str, statuses: str | None = None
) -> list[str] | None:
    """Every path the comparison's own memory diff names (under *pathspec*), sorted; ``None`` when
    the diff did not answer."""

    before = side_operand(record, "memory-base")
    if isinstance(before, EvidenceReadReceipt):
        return None
    if Path(before.repository) != Path(record.memory_candidate.repository):
        return None
    selection = ["--diff-filter", statuses] if statuses else []
    try:
        result = run_git(
            Path(record.memory_candidate.repository),
            [
                "diff",
                "--name-only",
                "-z",
                *selection,
                before.tree,
                record.memory_candidate.tree,
                "--",
                *pathspec,
            ],
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return sorted(name for name in result.stdout.split("\0") if name)


def _json(text: str) -> object:
    try:
        return json.loads(text)
    except ValueError:
        return None


def _limitations(
    record: ReviewTreeComparisonRecord, collections: tuple[EvidenceCollection, ...]
) -> tuple[str, ...]:
    """What this preparation could not establish, stated beside the packet (rule 10)."""

    found: list[str] = [
        "the packet describes this comparison only: an answer about another comparison, "
        "repository or task is not evidence about this one"
    ]
    for collection in collections:
        if collection.state == "not-selected":
            found.append(f"{collection.name}: not selected for this scope")
        elif collection.state != "present":
            found.append(
                f"{collection.name}: {collection.state}"
                + (f" ({collection.reason})" if collection.reason else "")
            )
    if record.converted_base is not None:
        found.append(
            "the memory base is read as the comparison's converted tree "
            f"{record.converted_base.tree}; the stored tree {record.memory_base.tree} is named "
            "beside it and is not what these reads examined"
        )
    return tuple(found)
