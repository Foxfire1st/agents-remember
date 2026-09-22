"""The source half of one review: the exact change inventory and the pane that renders it (ICR-R02).

A review is a comparison between **two bound Git object identities** and, independently of that,
between two knowledge datasets. This module owns the first half only, and it owns it as one
measurement:

* **The inventory is measured from the pair, not from a knowledge selection.** Nothing here reads a
  database, a claim or an invariant, so a candidate that records no subject -- or whose datasets do
  not exist at all -- still has its complete source change inventory. That is the packet's entry
  requirement: the task context is the entry, and knowledge availability cannot remove a source
  change from the list.
* **Every path is an address, so the Git interface is delimiter-safe.** Git quotes and escapes a
  pathname that contains a tab or a newline when it prints lines, and a reader that split those lines
  would hold a *different* string from the one the file is reached by. The two observations below
  therefore read NUL-delimited records and never split on whitespace inside a name: ``--raw -z`` for
  the status and the modes, ``--numstat -z`` for whether the content is text at all.
* **Status, mode and renderability are three separate facts.** An addition, a deletion, a modified
  path, a type change, a mode-only change, a symlink and a submodule pointer each keep their own
  entry, and one whose content cannot be rendered says so instead of disappearing. A binary path is
  listed exactly like a text one; the surface is told it is binary rather than shown nothing.
* **A failed measurement is a state, never an empty list.** An absent root, a tree this repository
  does not hold and an output that is not the declared format each produce ``available=False`` with
  the reason, and the review publishes that as ``unavailable``. A comparison whose *content
  classification* failed is published as ``partial`` with its entries intact, because the path set
  was measured even though one field of it was not.

The pane projection lives here too, because it is the same responsibility seen from the surface: the
source pane is the inventory plus the attribution facts the shipped comparison already owns. Nothing
in this module selects a record, ranks a path or states what a change means.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from agents_remember.application.review_relationship_movement import source_locations
from agents_remember.kernel.git_command import run_git
from agents_remember.memory.knowledge.diff_display import (
    TreeChange,
    TreeDifferenceProbe,
    TreePaths,
    TreeSide,
    no_tree_difference_probe,
)
from agents_remember.models.knowledge.diff import (
    KnowledgeDiffItem,
    KnowledgeDiffResult,
    SourceAttribution,
)
from agents_remember.models.knowledge.review import (
    ReviewChangedFile,
    ReviewFileContent,
    ReviewFileStatus,
    ReviewRemainingCount,
    ReviewRemainingCountName,
    ReviewSourceInventory,
    ReviewSourcePane,
    ReviewUnrepresentablePath,
    ReviewUnresolvedReference,
)
from agents_remember.models.knowledge.review_relationships import ReviewRelationshipMovement

__all__ = [
    "SOURCE_INVENTORY_REFERENCE",
    "attribution_limitations",
    "byte_form",
    "inventory_command",
    "inventory_limitations",
    "is_text_path",
    "review_inventory",
    "source_pane",
    "source_tree_side",
    "tree_difference_observation",
]

# The reference a review publishes for its inventory. It names the operation and the measurement
# rather than a cached artifact, because a caller acts on it by running the command beside it.
SOURCE_INVENTORY_REFERENCE = "review:source-change-inventory-of-the-bound-code-tree-pair"

# The two Git questions this module asks, each once per measurement. ``--no-renames`` is deliberate
# and is the shipped comparison's own policy: a rename is a deletion of one path and an addition of
# another, and reporting a rename would attribute the candidate's *new* path to a baseline path no
# recorded anchor names. ``-z`` is what makes the two interfaces delimiter-safe: records are
# NUL-terminated and a pathname is carried whole, however many tabs or newlines it contains.
_RAW_ARGS = ("diff", "--raw", "-z", "--no-renames")
_NUMSTAT_ARGS = ("diff", "--numstat", "-z", "--no-renames")

# The Git status letters this vocabulary names. A letter outside the table is still listed, as
# ``unknown`` with its own letter stated, rather than dropped or mapped onto a neighbouring status.
_STATUS_NAMES: Mapping[str, ReviewFileStatus] = {
    "A": "added",
    "D": "deleted",
    "M": "modified",
    "T": "type_changed",
}

# The two Git modes whose content is not a file's text: a gitlink is a submodule pointer and a
# symlink's blob is the link target. Both are listed like any other path, and their content
# classification is stated from the mode rather than guessed from bytes that are not there.
_SUBMODULE_MODE = "160000"
_SYMLINK_MODE = "120000"

# Why an entry's content type is unknown, per reason. Both are measurements of the same comparison,
# so neither is a claim about the file itself.
_UNCLASSIFIED_DETAIL = (
    "the content classification for this comparison was not measured, so this path is listed with "
    "its change status and no content type rather than assumed to be text"
)
_UNSTATED_DETAIL = (
    "this observation reported the path as changed without a status or a content classification, so "
    "it is listed as changed and its status and content are stated as unmeasured rather than assumed"
)
_UNLISTED_CONTENT_DETAIL = (
    "this comparison reported no content measurement for this path, so it is listed with its change "
    "status and no content type rather than assumed to be text"
)
_UNREPRESENTABLE_DETAIL = (
    "this changed path's name is not valid text under this code, so the bytes Git reported are "
    "carried here instead of a name; the status and mode beside them are the ones Git reported, and "
    "the path is listed rather than dropped"
)

# The detail an unavailable inventory carries when the two ids could not be compared at all.
_UNCOMPARABLE_DETAIL = (
    "the two requested code trees could not be compared ({before} in {before_root} against {after} "
    "in {after_root}), so no change set was observed and none is reported; no working tree or HEAD "
    "was substituted"
)
_UNPARSED_DETAIL = (
    "the Git interface answered with output that is not the declared NUL-delimited record format, so "
    "no change set was read from it and none is reported"
)

# The observation a side that named no exact tree earns. It is the shared probe's own sentence, kept
# as one value so an unavailable inventory and an unavailable expansion read identically.
_NO_ROOT_DETAIL = (
    "a side named an exact code tree without the repository root it lives in, so the two trees could "
    "not be compared and no change set was observed"
)


@dataclass(frozen=True)
class _RawRecord:
    """One ``git diff --raw -z`` record: the two modes, the change letter and the raw path."""

    old_mode: str
    new_mode: str
    status: str
    path: str

    @property
    def is_submodule(self) -> bool:
        return _SUBMODULE_MODE in (self.old_mode, self.new_mode)

    @property
    def is_symlink(self) -> bool:
        return _SYMLINK_MODE in (self.old_mode, self.new_mode)

    @property
    def is_mode_change(self) -> bool:
        """Whether the path's bytes were reachable and only its mode moved."""

        return self.status == "M" and self.old_mode != self.new_mode


def source_tree_side(tree_id: str | None, root: Path | None) -> TreeSide:
    """One bound endpoint as the observation's own side: an object id and the root it resolves in.

    The two travel together or not at all, which is the read context's own rule: a tree id without
    the repository that holds it is not resolvable, and a root without an id would be a licence to
    read a working tree.
    """

    return TreeSide(tree_id=tree_id, root=None if root is None else str(root))


def inventory_limitations(inventory: ReviewSourceInventory) -> tuple[str, ...]:
    """The declared limits of one inventory: unavailable, partial, or neither.

    Both states are limits of the *measurement* rather than of the change set, and both are declared
    at the top level of the response: a limit a reader has to open a pane to discover is a limit the
    response did not state.
    """

    if inventory.state == "unavailable":
        return ("limitation:source_inventory_unavailable",)
    if inventory.partial:
        return ("limitation:source_inventory_partial",)
    return ()


def tree_difference_observation(before: TreeSide, after: TreeSide) -> TreePaths:
    """Return the changed paths of two exact code trees, each with its status and renderability.

    This is the production ``TreeDifferenceProbe``: the shipped comparison calls it for its expansion
    and the review calls it for its inventory, so both read one observation rather than two that
    could disagree. Two sides are compared only when each named an exact tree *and* is resolvable in
    the root it named; anything else is reported as an observation this run could not make, never as
    an empty change set. The trees are addressed by object id and never by a branch, a working tree or
    ``HEAD``, so a comparison of published snapshots cannot silently become a comparison of whatever
    is checked out now.
    """

    if before.tree_id is None or after.tree_id is None:
        return no_tree_difference_probe(before, after)
    if before.root is None or after.root is None:
        return TreePaths(available=False, detail=_NO_ROOT_DETAIL)
    root = Path(after.root)
    raw = run_git(root, [*_RAW_ARGS, before.tree_id, after.tree_id])
    if raw.returncode != 0:
        return TreePaths(
            available=False,
            detail=_UNCOMPARABLE_DETAIL.format(
                before=before.tree_id,
                before_root=before.root,
                after=after.tree_id,
                after_root=after.root,
            ),
        )
    records = _raw_records(raw.stdout)
    if records is None:
        return TreePaths(available=False, detail=_UNPARSED_DETAIL)
    classified = _content_classification(root, before.tree_id, after.tree_id)
    changes = _changes(records, classified)
    carried = tuple(change for change in changes if is_text_path(change.path))
    uncarried = tuple(change for change in changes if not is_text_path(change.path))
    return TreePaths(
        available=True,
        paths=tuple(change.path for change in carried),
        entries=carried,
        unrepresentable=uncarried,
        partial=classified is None or bool(uncarried),
        detail=_observation_detail(classified, uncarried),
    )


def is_text_path(path: str) -> bool:
    """Whether one measured pathname can be carried as text: no surrogate-escaped byte remains.

    ``run_git`` decodes Git's output with ``surrogateescape``, deliberately: a pathname is bytes and
    the runner's job is to preserve them, not to lose a change because its name is not UTF-8. The
    consequence is that an undecodable byte arrives as a lone surrogate (``U+DC80``-``U+DCFF``), which
    this surface's own text fields refuse. This predicate is where that fact is observed, so the
    caller can state it instead of raising inside a model constructor.
    """

    return not any(0xDC80 <= ord(character) <= 0xDCFF for character in path)


def byte_form(path: str) -> str:
    """Return one measured pathname's exact bytes, rendered ASCII-safely for a reader.

    ``b'src/caf\xe9-latin1.py'`` is the path, byte for byte, in a spelling that is itself valid text
    and can be pasted into a shell or a Python literal. It is the whole point of the field it feeds:
    the bytes are preserved, and the name is never re-encoded, because a re-encoded name would address
    a file this repository does not hold.
    """

    return repr(path.encode("utf-8", "surrogateescape"))


def _observation_detail(
    classified: Mapping[str, bool] | None, unrepresentable: Sequence[TreeChange]
) -> str:
    """State every limit one observation has: an uncarried name, an unclassified content, or neither."""

    parts: list[str] = []
    if unrepresentable:
        parts.append(_unrepresentable_detail(unrepresentable))
    if classified is None:
        parts.append(_UNCLASSIFIED_DETAIL)
    return " ".join(parts)


def _unrepresentable_detail(changes: Sequence[TreeChange]) -> str:
    """State that some changed paths could not be carried as names, naming their bytes."""

    named = ", ".join(byte_form(change.path) for change in changes[:3])
    more = "" if len(changes) <= 3 else f" and {len(changes) - 3} more"
    return (
        f"{_count(len(changes), 'changed path')} could not be carried as a name in this vocabulary "
        f"({named}{more}): a Git pathname is bytes, and a name that is not valid UTF-8 has no text "
        "form under this code. The bytes are preserved exactly as Git reported them and each path is "
        "reported by its byte form -- never re-encoded and never dropped -- so a partial change set "
        "cannot be read as a whole one"
    )


def _count(count: int, noun: str) -> str:
    """Return one counted noun phrase, so a stated limit reads as a measurement."""

    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _raw_records(output: str) -> tuple[_RawRecord, ...] | None:
    """Return one record per changed path, or ``None`` when the output is not the declared format.

    The ``-z`` form is a NUL-terminated alternation of one metadata field and one path, so it is read
    as exactly that: an odd token count, a field that does not begin with ``:``, a metadata row that
    does not carry Git's five fields or an empty path each mean the output is not this interface's and
    the whole observation is refused. Paths are carried verbatim -- split on NUL and never trimmed --
    because the name *is* the address the same file is expanded with.
    """

    tokens = output.split("\0")
    if tokens and tokens[-1] == "":
        tokens.pop()
    if not tokens:
        # Two trees that agree print nothing at all, and that is a *measured* empty change set: the
        # one answer this function must not confuse with a malformed one.
        return ()
    if len(tokens) % 2:
        return None
    records: list[_RawRecord] = []
    for index in range(0, len(tokens), 2):
        record = _raw_record(tokens[index], tokens[index + 1])
        if record is None:
            return None
        records.append(record)
    return tuple(records)


def _raw_record(metadata: str, path: str) -> _RawRecord | None:
    """Return one metadata row and its path, or ``None`` when either is not in the declared shape."""

    if not metadata.startswith(":") or not path:
        return None
    fields = metadata[1:].split(" ")
    if len(fields) != 5 or not _is_mode(fields[0]) or not _is_mode(fields[1]) or not fields[4]:
        return None
    return _RawRecord(old_mode=fields[0], new_mode=fields[1], status=fields[4][0], path=path)


def _is_mode(field: str) -> bool:
    """Whether one mode field is Git's six-digit octal permission word."""

    return len(field) == 6 and all(character in "01234567" for character in field)


def _content_classification(
    root: Path, before_tree: str, after_tree: str
) -> Mapping[str, bool] | None:
    """Return one ``path -> is binary`` measurement for the same pair, or ``None`` if it failed.

    ``--numstat`` reports a text path's two line counts and ``-`` for both when Git considers the
    content binary, which is the measured fact "this path's content cannot be rendered as text". Its
    ``-z`` records are read the same way as the raw ones, so a name containing a tab is still one
    name: the two count fields are split off the left and the remainder is the path.
    """

    result = run_git(root, [*_NUMSTAT_ARGS, before_tree, after_tree])
    if result.returncode != 0:
        return None
    measured: dict[str, bool] = {}
    for record in result.stdout.split("\0"):
        if not record:
            continue
        fields = record.split("\t", 2)
        if len(fields) != 3 or not fields[2]:
            return None
        measured[fields[2]] = "-" in (fields[0], fields[1])
    return measured


def _changes(
    records: Sequence[_RawRecord], classified: Mapping[str, bool] | None
) -> tuple[TreeChange, ...]:
    """Return every changed path as one status-bearing entry, in a stable path order."""

    changes: list[TreeChange] = []
    for record in records:
        content = _content(record, classified)
        changes.append(
            TreeChange(
                path=record.path,
                status=_STATUS_NAMES.get(record.status, "unknown"),
                content=content,
                mode_change=record.is_mode_change,
                detail="" if content != "unknown" else _content_reason(classified),
            )
        )
    return tuple(sorted(changes, key=lambda change: change.path))


def _content(record: _RawRecord, classified: Mapping[str, bool] | None) -> ReviewFileContent:
    """Return what can be rendered for one record: a mode-borne kind, a measured kind, or unknown.

    The mode is consulted first because it is the more specific fact: a gitlink's content is a commit
    pointer and a symlink's is a link target, and neither is text or binary in the sense ``--numstat``
    measures. Everything else is answered by the content measurement, and a path that measurement did
    not cover is ``unknown`` rather than ``text``.
    """

    if record.is_submodule:
        return "submodule"
    if record.is_symlink:
        return "symlink"
    if classified is None or record.path not in classified:
        return "unknown"
    return "binary" if classified[record.path] else "text"


def _content_reason(classified: Mapping[str, bool] | None) -> str:
    """Return why one entry's content type is unknown: not measured, or not reported for this path."""

    return _UNCLASSIFIED_DETAIL if classified is None else _UNLISTED_CONTENT_DETAIL


def inventory_command(before: TreeSide, after: TreeSide) -> str:
    """Return the exact command that reproduces this inventory, naming both bound objects.

    The command names the two *requested* object ids and the root they were compared in, and never a
    branch, a working tree or ``HEAD``: a caller can reproduce the whole change set from this value
    without reading this module, and reproducing it cannot silently become a comparison of whatever
    is checked out now. A side that named no tree is stated as such rather than substituted.
    """

    root = after.root or before.root
    prefix = "" if root is None else f"git -C {root} "
    return (
        f"{prefix}diff --raw -z --no-renames "
        f"{before.tree_id or '<no baseline tree requested>'} "
        f"{after.tree_id or '<no candidate tree requested>'}"
    )


def review_inventory(
    before: TreeSide,
    after: TreeSide,
    *,
    probe: TreeDifferenceProbe | None = None,
    observed: TreePaths | None = None,
) -> ReviewSourceInventory:
    """Return the review's own inventory value for one bound pair: measured, partial or unavailable.

    ``probe`` is the same injectable observation seam the comparison takes, so a case that
    substitutes an observation measures one review rather than two. An observation that reported
    paths *without* the statuses beside them is still listed -- one entry per path, each stating that
    its status and content were not measured -- because the alternative is a measured-empty list,
    which is the one thing a partial observation must never become.

    ``observed`` is the measurement to render, for a caller that has already made exactly this one:
    the attribution partition counts that same observation as its denominator, and handing it in is
    what keeps the inventory and the partition two renderings of one measurement rather than two
    measurements that have to agree.
    """

    if observed is not None:
        observation = observed
    elif probe is None:
        observation = tree_difference_observation(before, after)
    else:
        observation = probe(before, after)
    entries = _entries(observation)
    return ReviewSourceInventory(
        state="measured" if observation.available else "unavailable",
        entries=entries,
        listed_total=len(entries),
        partial=observation.partial or (bool(observation.paths) and not observation.entries),
        detail=observation.detail or _measured_detail(len(entries)),
        command=inventory_command(before, after),
        before_code_tree_id=before.tree_id,
        after_code_tree_id=after.tree_id,
        unrepresentable_paths=tuple(
            _unrepresentable_path(change) for change in observation.unrepresentable
        ),
    )


def _unrepresentable_path(change: TreeChange) -> ReviewUnrepresentablePath:
    """Return one changed path this surface cannot name, carried by its exact byte form."""

    return ReviewUnrepresentablePath(
        path_bytes=byte_form(change.path),
        status=_file_status(change.status),
        mode_change=change.mode_change,
        detail=_UNREPRESENTABLE_DETAIL,
    )


def _entries(observed: TreePaths) -> tuple[ReviewChangedFile, ...]:
    """Return one surface entry per observed path, at whatever resolution the observation reached.

    A full observation carries a :class:`TreeChange` per path and is rendered as it stands. An
    observation that named paths and no entries -- another probe's answer, or one this module did not
    produce -- is rendered at the resolution it has: every path listed, with the missing status and
    content classified ``unknown`` and stating that they were not measured.
    """

    if observed.entries:
        return tuple(_changed_file(change) for change in observed.entries)
    return tuple(
        ReviewChangedFile(
            path=path,
            status="unknown",
            content="unknown",
            detail=_UNSTATED_DETAIL,
        )
        for path in observed.paths
    )


def _changed_file(change: TreeChange) -> ReviewChangedFile:
    """Return one measured change as the surface's own entry, keeping its stated reason."""

    return ReviewChangedFile(
        path=change.path,
        status=_file_status(change.status),
        content=_file_content(change.content),
        mode_change=change.mode_change,
        detail=change.detail or None,
    )


def _file_status(status: str) -> ReviewFileStatus:
    """Return one measured status, stating ``unknown`` for a letter this vocabulary does not name."""

    return status if status in ("added", "deleted", "modified", "type_changed") else "unknown"


def _file_content(content: str) -> ReviewFileContent:
    """Return one measured content kind, stating ``unknown`` for a kind this vocabulary lacks."""

    named: tuple[ReviewFileContent, ...] = ("text", "binary", "symlink", "submodule")
    return content if content in named else "unknown"


def _measured_detail(count: int) -> str:
    """Return the sentence a measured inventory publishes about itself.

    A measured empty set is stated as a measurement -- "the two trees agree" -- rather than as an
    absence of information, and the two are different sentences on purpose: a caller that was handed
    no paths must be able to tell a comparison that found none from one that was never made.
    """

    if count == 0:
        return (
            "the two requested code trees hold identical content at the compared paths: zero paths "
            "differ, which is a measured empty change set and not an unmeasured one"
        )
    return (
        f"the two requested code trees differ at {count} path(s); every one is listed with the change "
        "status Git reported for it, whether or not its content can be rendered"
    )


# --- the source pane -------------------------------------------------------------------------
#
# The pane's recorded-association half -- one location per selected claim, and the two-sided
# relationship movement each location belongs to -- is ``ICR-R08@v1``'s. The traversal and its display
# live in :mod:`agents_remember.application.review_relationship_movement` and
# :mod:`agents_remember.application.review_relationship_display`, which own the before/after union and
# the rendering of one relationship of it; this module measures the source change set and renders the
# pane's attribution facts. There is one implementation, the address view below is that module's own
# ``source_locations``, and the pane composes its result rather than re-deriving a location.


def source_pane(
    comparison: KnowledgeDiffResult | None,
    inventory: ReviewSourceInventory,
    attribution: SourceAttribution,
    relationships: Sequence[ReviewRelationshipMovement] = (),
) -> ReviewSourcePane:
    """Pane 2: the whole-task inventory, the selected locations and the measured attribution.

    The inventory is the pane's own first fact and it is present whether or not a knowledge comparison
    was made: a selection filters *attribution*, never the source changes in the declared comparison.

    ``attribution`` is the measured partition of that same inventory -- one accounting, at the
    changed-path granularity, of which of those changes either bound snapshot registers a valid
    mapping for -- and every attribution field and count below is read from it rather than recomputed
    from the displayed items. That is the correction this pane needed: the three buckets are disjoint
    and exhaustive over the measured changes, so an unchanged mapped file can no longer appear in a
    list of changed ones, and a path whose attribution a side's silence left undetermined is carried
    beside the two conclusions instead of being folded into either.

    ``relationships`` is the recorded before/after relationship union the composition traversed
    (ICR-R08@v1), carried whole beside the location rows it also renders as addresses. The pane
    neither builds it nor adds to it: a relationship this pane displayed without the traversal would
    be the one-sided projection the union exists to correct.
    """

    items = () if comparison is None or comparison.page is None else comparison.page.items
    expansion = None if comparison is None else comparison.expansion
    outside = tuple(item for item in items if item.coverage == "present_outside_selection")
    return ReviewSourcePane(
        inventory=inventory,
        locations=source_locations(relationships),
        relationships=tuple(relationships),
        remaining=_remaining(comparison, attribution, outside),
        expansion_reference=SOURCE_INVENTORY_REFERENCE
        if expansion is None
        else expansion.reference,
        expansion_command=inventory.command if expansion is None else expansion.command,
        unattributed_changed_paths=attribution.confirmed_unregistered_paths,
        attributed_changed_paths=attribution.attributed_paths,
        unknown_attribution_changed_paths=attribution.unknown_attribution_paths,
        attribution=attribution,
        unresolved=tuple(
            ReviewUnresolvedReference(
                field="attribution",
                recorded_reference=item.item_id,
                detail=(
                    "this record is held by one snapshot and was not reached by the other side's "
                    "declared selection; it is displayed as present outside the selection and "
                    "never as a deletion"
                ),
            )
            for item in outside
        ),
    )


# The reason each count states when it has no value, kept as one value per count so the same absence
# is never spelled two ways in two compositions.
_LOCATIONS_ABSENT = (
    "no knowledge subject was selected for this review, so no selection was made and no locations "
    "were reached or left"
)
_OUTSIDE_SELECTION_ABSENT = (
    "no invariant or family subject was selected for this review, so no change is inside or outside "
    "one; the inventory above is the complete source change set of the bound pair"
)
_OUTSIDE_RECORDS_ABSENT = "no knowledge subject was selected, so neither snapshot was selected over"
_REFERENCES_ABSENT = "no knowledge subject was selected, so no reference was resolved"


def _remaining(
    comparison: KnowledgeDiffResult | None,
    attribution: SourceAttribution,
    outside: Sequence[KnowledgeDiffItem],
) -> tuple[ReviewRemainingCount, ...]:
    """Return the pane's persistent counts, each stating its own measurement or its own reason.

    Two of the five are the attribution partition's own numbers and they are counted at the
    changed-path granularity: the paths confirmed to carry no valid registered attribution, and the
    measured changed paths that no link to the selected subject reaches. ``changed_paths_outside_
    selection`` is that second number and not the length of two other lists summed -- the population
    it names is the measured change population, so a mapped *unchanged* file cannot increment it.

    A count the attribution could not measure states why rather than reporting zero: an unmeasured
    population has no count, and the reason carries the partition's own sentence about which side or
    scope was not inspected.
    """

    counts = None if comparison is None or comparison.page is None else comparison.page.counts
    return (
        _stated_or_absent(
            "locations_remaining",
            None if counts is None else counts.items_remaining,
            _LOCATIONS_ABSENT,
        ),
        _stated_or_absent(
            "changed_paths_outside_selection",
            _outside_selection_total(attribution),
            _outside_selection_reason(attribution),
        ),
        _stated_or_absent(
            "unattributed_changed_paths",
            attribution.confirmed_unregistered_total,
            _unregistered_reason(attribution),
        ),
        # The two attribution counts the reader has to see together (ICR-R04): the confirmed negative
        # conclusion and the measured population that conclusion could not be drawn for. A bare zero
        # for the first, with no second row beside it, is the "measured zero implies completeness"
        # reading; the pair states the scope of both in one place, in the units the pane already
        # prints and the surface already renders.
        _stated_or_absent(
            "unknown_attribution_changed_paths",
            attribution.unknown_attribution_total,
            _undetermined_reason(attribution),
        ),
        _stated_or_absent(
            "records_present_outside_selection",
            None if counts is None else len(outside),
            _OUTSIDE_RECORDS_ABSENT,
        ),
        _stated_or_absent(
            "references_unresolved",
            None if counts is None else counts.suppressed_total,
            _REFERENCES_ABSENT,
        ),
    )


def _stated_or_absent(
    name: ReviewRemainingCountName, value: int | None, reason: str
) -> ReviewRemainingCount:
    """One count as its measured value, or as the reason it has none -- never both and never zero.

    ``ReviewRemainingCount`` refuses a value and a reason together, which is the point: a zero that
    means "none" and a zero that means "not measured here" must not be the same value.
    """

    if value is None:
        return ReviewRemainingCount(name=name, value=None, reason=reason)
    return ReviewRemainingCount(name=name, value=value)


def _outside_selection_total(attribution: SourceAttribution) -> int | None:
    """The measured changed paths no known link to the selected subject reaches, or ``None``."""

    if attribution.state != "measured" or attribution.subject_scope_complete is None:
        return None
    return len(attribution.paths) - len(attribution.selected_subject_paths)


def _outside_selection_reason(attribution: SourceAttribution) -> str:
    """Why the outside-selection count has no value, naming which of the two states applies."""

    if attribution.state != "measured":
        return (
            "the source change population was not measured, so no count of changes outside the "
            f"selected subject exists ({attribution.detail})"
        )
    return _OUTSIDE_SELECTION_ABSENT


def _unregistered_reason(attribution: SourceAttribution) -> str:
    """Why the confirmed-unregistered count has no value, naming which of the two states applies."""

    if attribution.state != "measured":
        return (
            "the source change population was not measured, so no path is reported as confirmed to "
            f"carry no registered attribution ({attribution.detail})"
        )
    return (
        "the measured population was completely inspected and every changed path carries a valid "
        "registered attribution"
    )


def _undetermined_reason(attribution: SourceAttribution) -> str:
    """Why the undetermined-attribution count has no value, naming which state applies."""

    if attribution.state != "measured":
        return (
            "the source change population was not measured, so no path is reported as of "
            f"undetermined attribution ({attribution.detail})"
        )
    return (
        "every measured changed path's attribution is determined: each one either carries a valid "
        "registered attribution or was confirmed to carry none, in snapshots that were completely "
        "inspected or legitimately known empty"
    )


def attribution_limitations(attribution: SourceAttribution) -> tuple[str, ...]:
    """The limits one partition establishes, in the spelling the comparison route already uses.

    A route that made no comparison has no ``KnowledgeDiffResult`` to carry its own declaration, so it
    states the same two facts from the partition itself and in the same spelling: the limitation token
    the result vocabulary declares, and the counted omission behind it. Both are emitted exactly when
    the partition establishes them, so a response cannot claim a limit it does not have, and a reader
    that sees only the top-level strings is told which bucket the counts below are short of.
    """

    if attribution.state != "measured":
        # No counted omission, in the same spelling the comparison route uses for this state: there is
        # no population to count, so the missing measurement *is* the limit and a count would be the
        # fabricated zero this requirement forbids.
        return ("limitation:unknown_attribution_changed_paths",)
    undetermined = len(attribution.unknown_attribution_paths)
    if not undetermined:
        return ()
    return (
        "limitation:unknown_attribution_changed_paths",
        f"omitted:attribution_not_determined:{undetermined}",
    )
