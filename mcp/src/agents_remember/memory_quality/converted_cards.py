"""A converted card's census identity: its kind and its source, read from the converted format.

A legacy card names its kind and its source in a ``| Field | Value |`` metadata table (``doc_type``,
``path``, ``sourceRoute``). The conversion drops those rows (MIK-R24 rule 1): a converted card's kind
and source are its place in the tree, and its sidecar names the same source when the card has
references (MIK-R21 rule 1):

* ``onboarding/<source>.md`` is the file card of ``<source>``; its sidecar
  ``onboarding/<source>.json`` (``ar-onboarding-file/v1``) carries ``path: <source>``;
* ``onboarding/<route>/overview.md`` is the overview of ``<route>`` (``.`` for the repository root
  route); its sidecar ``overview.json`` (``ar-onboarding-route/v1``) carries ``path: <route>``;
* ``onboarding/entities.md`` is the repository's entity catalog.

:func:`converted_card_metadata` answers in the legacy table's vocabulary, so the census (and the
curator candidates it feeds) reads both formats through one shape. The sidecar's ``path`` is the
source identity when the card has a sidecar of its kind; a card without references has no sidecar,
and its place in the tree is then its identity.
"""

from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Final

from agents_remember.models.knowledge_files.sidecars import (
    FILE_SIDECAR_SCHEMA,
    ROOT_ROUTE_PATH,
    ROUTE_SIDECAR_SCHEMA,
)

__all__ = [
    "card_sidecar_path",
    "converted_card_metadata",
    "declared_sidecar_path",
    "is_overview",
]

OVERVIEW: Final = "overview.md"
ENTITY_CATALOG: Final = "entities.md"
_ROOT_OVERVIEW_TYPE: Final = "repo-overview"
_ROUTE_OVERVIEW_TYPE: Final = "route-local-overview"
_FILE_CARD_TYPE: Final = "file-level-onboarding"
_CATALOG_TYPE: Final = "repo-entity-catalog"


def card_sidecar_path(card: str) -> str:
    """The sidecar beside a converted card: ``<card without .md>.json``."""

    return f"{card.removesuffix('.md')}.json"


def declared_sidecar_path(text: str | None, *, route: bool) -> str | None:
    """The ``path`` a sidecar of the card's kind declares, or ``None`` (absent, unreadable, other)."""

    if text is None:
        return None
    try:
        document = json.loads(text)
    except ValueError:
        return None
    if not isinstance(document, dict):
        return None
    schema = ROUTE_SIDECAR_SCHEMA if route else FILE_SIDECAR_SCHEMA
    declared = document.get("path")
    if document.get("schema") != schema or not isinstance(declared, str) or not declared:
        return None
    return declared


def is_overview(card: str) -> bool:
    return PurePosixPath(card).name == OVERVIEW


def converted_card_metadata(card: str, onboarding: str, sidecar_text: str | None) -> dict[str, str]:
    """``card``'s kind and source in the legacy table's keys (``doc_type``, ``path``,
    ``sourceRoute``); ``sidecar_text`` is the card's sidecar, or ``None`` when it has none."""

    relative = card.removeprefix(f"{onboarding}/")
    if is_overview(card):
        parent = PurePosixPath(relative).parent.as_posix()
        structural = ROOT_ROUTE_PATH if parent in {"", "."} else parent
        route = declared_sidecar_path(sidecar_text, route=True) or structural
        kind = _ROOT_OVERVIEW_TYPE if route == ROOT_ROUTE_PATH else _ROUTE_OVERVIEW_TYPE
        return {"doc_type": kind, "sourceRoute": route}
    if relative == ENTITY_CATALOG:
        return {"doc_type": _CATALOG_TYPE}
    source = declared_sidecar_path(sidecar_text, route=False) or relative.removesuffix(".md")
    return {"doc_type": _FILE_CARD_TYPE, "path": source}
