"""The census section of the knowledge writer (MIK-R20 Scope): every census write, checked.

:class:`CensusWriter` writes one converted memory working tree. Each operation reads the tree,
builds the new file bytes in the canonical formatting, and runs the census checks (the same checks
the validator registers, MIK-R22 rule 9) over the result with the current tree as the base. A write
that any check refuses for that census writes nothing. So the writer can only append a claim's
assessments and a route's statuses: it has no operation that edits or removes one.

* :meth:`create` writes ``baseline.json`` and ``inventory.json`` (from :func:`.take_inventory`); a
  census is created once, and its baseline and inventory are then pinned.
* :meth:`add_claims` appends claims to a route's claims file; each names an inventoried artifact of
  that route.
* :meth:`append_assessment` appends one assessment to a claim (a correction is a new assessment).
* :meth:`set_disposition` sets a claim's migration disposition and the records it links to.
* :meth:`append_status` appends one entry to a route's status history.

The writer refuses a memory tree without the layout marker: census files are part of the converted
layout, and before the cutover (MIK-R37) no production memory tree is converted, so the writer never
touches production memory.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agents_remember.kernel.atomic_write import atomic_write_bytes
from agents_remember.memory_quality.knowledge_census.checks import check_censuses
from agents_remember.memory_quality.knowledge_census.files import (
    baseline_path,
    claims_path,
    inventory_path,
    read_censuses,
    route_status_path,
)
from agents_remember.memory_quality.knowledge_validator.trees import (
    KnowledgeTree,
    knowledge_tree_from_directory,
)
from agents_remember.models.knowledge_files.canonical import canonical_text
from agents_remember.models.knowledge_files.census import (
    Assessment,
    CensusBaseline,
    CensusClaim,
    CensusClaims,
    CensusInventory,
    CensusRoute,
    Disposition,
    StatusEntry,
)
from agents_remember.models.knowledge_files.documents import LAYOUT_MARKER_PATH, census_directory
from agents_remember.models.knowledge_files.shapes import FileModel


class CensusWriteError(ValueError):
    """A census write was refused; nothing was written."""


@contextmanager
def _refusing_invalid() -> Iterator[None]:
    """Report a document the models refuse as a refused write; nothing has been written yet."""

    try:
        yield
    except ValidationError as error:
        raise CensusWriteError(f"census write refused: {error}") from error


def _encode(model: FileModel) -> bytes:
    return canonical_text(model.to_document()).encode("utf-8")


class CensusWriter:
    """Writes census files into one converted memory working tree."""

    def __init__(self, memory_root: Path) -> None:
        self.memory_root = memory_root

    def _tree(self) -> KnowledgeTree:
        tree = knowledge_tree_from_directory(self.memory_root)
        if not tree.converted:
            raise CensusWriteError(
                f"{self.memory_root} has no {LAYOUT_MARKER_PATH}: census files are written only "
                "into a converted memory tree (MIK-R21 rule 1)"
            )
        return tree

    def _commit(
        self,
        census_id: str,
        tree: KnowledgeTree,
        changes: Mapping[str, bytes],
        *,
        base: Mapping[str, bytes] | None = None,
    ) -> None:
        """Check the tree with ``changes`` against ``base`` (the tree), then write them in order."""

        base = tree.files if base is None else base
        candidate = {**base, **changes}
        prefix = f"{census_directory(census_id)}/"
        refused = [
            finding
            for finding in check_censuses(candidate, bases=[base])
            if finding.path.startswith(prefix)
        ]
        if refused:
            raise CensusWriteError(
                "census write refused:\n"
                + "\n".join(
                    f"{item.path}: {item.field or '-'}: [{item.rule}] {item.message}"
                    for item in refused
                )
            )
        for path, data in changes.items():
            target = self.memory_root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_bytes(target, data)

    def create(self, baseline: CensusBaseline, inventory: CensusInventory) -> None:
        """Write a new census's pinned baseline and inventory.

        ``inventory.json`` is written first and ``baseline.json`` last: the baseline is the commit
        point. A census directory holding only an ``inventory.json`` is an interrupted creation
        (the validator refuses it for its missing baseline), and ``create`` writes it again.
        """

        if baseline.census != inventory.census:
            raise CensusWriteError("the baseline and the inventory name different censuses")
        tree = self._tree()
        census_id = baseline.census
        prefix = f"{census_directory(census_id)}/"
        present = {path for path in tree.files if path.startswith(prefix)}
        if present - {inventory_path(census_id)}:
            raise CensusWriteError(f"census {census_id} already exists; a census is created once")
        self._commit(
            census_id,
            tree,
            {
                inventory_path(census_id): _encode(inventory),
                baseline_path(census_id): _encode(baseline),
            },
            base={path: data for path, data in tree.files.items() if path not in present},
        )

    def add_claims(self, census_id: str, route: str, claims: Sequence[CensusClaim]) -> None:
        """Append ``claims`` to the claims file of ``route``."""

        tree = self._tree()
        path = claims_path(census_id, route)
        current = read_censuses(tree.files).censuses.get(census_id)
        existing = (
            () if current is None or path not in current.claims else current.claims[path].claims
        )
        with _refusing_invalid():
            document = CensusClaims(census=census_id, route=route, claims=(*existing, *claims))
        self._commit(census_id, tree, {path: _encode(document)})

    def _replace_claim(
        self, census_id: str, claim_id: str, change: Callable[[CensusClaim], dict[str, Any]]
    ) -> None:
        tree = self._tree()
        census = read_censuses(tree.files).censuses.get(census_id)
        for path, claims in sorted((census.claims if census else {}).items()):
            for index, claim in enumerate(claims.claims):
                if claim.id != claim_id:
                    continue
                document = {**claim.to_document(), **change(claim)}
                with _refusing_invalid():
                    updated = CensusClaim.model_validate(
                        {key: value for key, value in document.items() if value is not None}
                    )
                replaced = (*claims.claims[:index], updated, *claims.claims[index + 1 :])
                self._commit(
                    census_id, tree, {path: _encode(claims.model_copy(update={"claims": replaced}))}
                )
                return
        raise CensusWriteError(f"census {census_id} has no claim {claim_id}")

    def append_assessment(self, census_id: str, claim_id: str, assessment: Assessment) -> None:
        """Append ``assessment`` to the claim; earlier assessments are kept as recorded."""

        self._replace_claim(
            census_id,
            claim_id,
            lambda claim: {
                "assessments": [item.to_document() for item in (*claim.assessments, assessment)]
            },
        )

    def set_disposition(
        self,
        census_id: str,
        claim_id: str,
        disposition: Disposition,
        records: Sequence[str] = (),
    ) -> None:
        """Set the claim's migration disposition and the records it links to (none: no link)."""

        self._replace_claim(
            census_id,
            claim_id,
            lambda _claim: {"disposition": disposition, "records": list(records) or None},
        )

    def append_status(self, census_id: str, route: str, entry: StatusEntry) -> None:
        """Append ``entry`` to the status history of ``route`` in this census."""

        tree = self._tree()
        path = route_status_path(census_id, route)
        census = read_censuses(tree.files).censuses.get(census_id)
        existing = (
            () if census is None or path not in census.routes else census.routes[path].statuses
        )
        with _refusing_invalid():
            document = CensusRoute(census=census_id, route=route, statuses=(*existing, entry))
        self._commit(census_id, tree, {path: _encode(document)})
