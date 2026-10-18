"""Application entry point for the ``read_ar_files`` tool (MIK-R109 rule 1).

A read names where a thing is and does not carry it. The tool takes
``repo_id`` and ``files`` (repository-relative paths, as strings, up to
:data:`READ_AR_FILES_MAX_PATHS`) and answers, per path, whether the file
exists in the caller's code checkout, the card that governs it as a location
in the caller's memory worktree (or that it has none), the card's record
counts by state on the code the caller sees, and — only for records whose
state asks the caller to act — the text ID, kind, state and card heading of
each. The governing overviews are listed once per call, as locations,
nearest first.

It returns no source text, no card text and no overview text. The removed
parameters (``refresh``, per-file ``source``/``onboarding``) and object-form
file entries are refused with ``argument-removed``: the caller reads the
file with its own tools. The server keeps no per-session state about what
it sent: the same call on the same state gives the same answer. There is no
continuation.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import apsw

from agents_remember.application.knowledge_currentness.observe import CodeTree
from agents_remember.application.published_intent import select_knowledge_dataset
from agents_remember.application.read_file_cards import (
    READ_AR_FILES_MAX_PATHS,
    CardSelection,
    SectionAnchors,
    aggregate_state,
    card_records_for_path,
)
from agents_remember.application.read_files_format import TEXT_FORMAT, memory_format
from agents_remember.application.worktree_code_state import observed_code_tree
from agents_remember.errors import AuthorityError
from agents_remember.kernel import filesystem
from agents_remember.kernel.authority import require_repo
from agents_remember.kernel.coordination_context.models import (
    CoordinationContext,
    CoordinationRequest,
)
from agents_remember.kernel.coordination_context_resolver import (
    CoordinationHints,
    EnclosureSelector,
    resolve_coordination_context,
    resolve_storage_for_source,
)
from agents_remember.kernel.git_preparation import GitPreparationError
from agents_remember.kernel.route_index import ROUTE_OVERVIEW_NAME
from agents_remember.kernel.sidecar_pairing import (
    confine_rel as _confined_rel,
)
from agents_remember.kernel.sidecar_pairing import (
    route_sidecar_status as _route_sidecar_status,
)
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.memory.knowledge_index import IndexMismatchError, MemoryTreeError
from agents_remember.models.knowledge_files.documents import LAYOUT_MARKER_PATH
from agents_remember.worktrees.modules.contract_reader import WorktreeContractReader

# Storage modes whose card lives in a sidecar markdown file beside the route
# index. This repo's own memory is ``external``, which also writes sidecars,
# so it is treated as sidecar here even though ``is_sidecar_storage`` (used
# for missing-onboarding gating) returns True only for ``repo-sidecar`` /
# ``memory-repo``.
_SIDECAR_STORAGE_MODES = {"repo-sidecar", "memory-repo", "external"}

ARGUMENT_REMOVED_SENTENCE = "read the file with your own tools; this tool names the cards"


@dataclass(frozen=True)
class _Refusal:
    """Why one ``read_ar_files`` call is refused before anything is read."""

    code: str
    detail: str


def read_ar_files_tool(
    config: Any,
    *,
    repo_id: str,
    files: list[Any],
    working_folder: Path | None = None,
    worktree_scope: str = "official-line",
    _context: CoordinationContext | None = None,
) -> dict[str, Any]:
    """Name the knowledge governing a batch of repository-relative paths.

    ``working_folder`` is the caller's working folder when the server knows
    it (a leaf's worktree group under task scope): locations inside it are
    answered relative, so the caller opens them as written. Otherwise
    locations are absolute. ``worktree_scope`` says whose worktrees were
    read: ``leaf`` when the call named a leaf's task context, ``official-line``
    when it read the configured checkout (MIK-R83 rule 6); the answer names
    both roots either way. ``_context`` is a test seam: callers normally let
    the resolver run, but tests inject a pre-built
    :class:`CoordinationContext` to isolate one storage mode.
    """

    repo = require_repo(config, repo_id)
    refusal = _request_refusal(files)
    if refusal is not None:
        return {
            "ok": True,
            "operation": "read_ar_files",
            "repoId": repo.repo_id,
            "state": "refused",
            "refusalCode": refusal.code,
            "refusalDetail": refusal.detail,
        }

    context = _context or resolve_coordination_context(
        code_repository_name=repo.repo_id,
        workspace_root=config.workspace_root,
        code_repository_root=repo.path,
        request=CoordinationRequest(
            hints=CoordinationHints(coordination_root=config.coordination_root),
            selector=EnclosureSelector(contract_path=repo.contract_path),
            contract_reader=WorktreeContractReader(),
        ),
    )

    memory_state, dataset = _memory_selection(context, config)
    code_root = Path(repo.path)
    # The states are measured at the code the caller sees: the commit when the checkout is clean,
    # else its working tree, captured and held for exactly this read (MIK-R73 rule 7).
    with observed_code_tree(code_root) as observation:
        if observation is None:
            code_state, code_tree, object_directory = "unmeasured", None, None
        else:
            code_state = observation.code_state
            code_tree = observation.tree_id
            object_directory = observation.object_directory
        return _named_files(
            _NamedRead(
                context=context,
                repo_id=repo.repo_id,
                code_root=code_root,
                files=files,
                scope=_BatchScope(
                    memory_state=memory_state,
                    dataset=dataset,
                    code_tree=code_tree,
                    object_directory=object_directory,
                    working_folder=working_folder,
                ),
                worktree_scope=worktree_scope,
                code_state=code_state,
                code_tree=code_tree,
            )
        )


@dataclass(frozen=True)
class _NamedRead:
    """One batch read: whose memory, which paths, and the code tree it observed."""

    context: CoordinationContext
    repo_id: str
    code_root: Path
    files: list[Any]
    scope: _BatchScope
    worktree_scope: str
    code_state: str
    code_tree: str | None


def _named_files(read: _NamedRead) -> dict[str, Any]:
    """Name each path against the memory and the code tree the caller's read observed."""

    context = read.context
    repo_id = read.repo_id
    code_root = read.code_root
    files = read.files
    scope = read.scope
    worktree_scope = read.worktree_scope
    code_state = read.code_state
    code_tree = read.code_tree

    file_results: list[dict[str, Any]] = []
    for rel in files:
        try:
            result = _name_one(context, code_root, rel, scope)
        except AuthorityError as error:
            # Confinement stays fail-closed, but at the row boundary (packet
            # Failure): one outside path earns its row and never reads outside,
            # while the valid siblings are still answered.
            result = {
                "path": rel,
                "exists": None,
                "refusalCode": "outside-repository",
                "refusalDetail": str(error),
            }
        file_results.append(result)

    payload: dict[str, Any] = {
        "ok": True,
        "operation": "read_ar_files",
        "repoId": repo_id,
        "state": "named",
        "codeState": code_state,
        "memoryState": scope.memory_state,
        "worktreeScope": worktree_scope,
        "sourceRoot": code_root.as_posix(),
        "memoryRoot": context.onboarding_root.parent.as_posix(),
        "files": file_results,
        "overviews": _overview_locations(context, code_root, files, scope.working_folder),
    }
    if any(row.get("routeIndexStale") for row in file_results):
        payload["routeIndexStale"] = True
        payload["routeIndexRefresh"] = "route_index_refresh"
    if code_tree is not None:
        payload["codeTree"] = code_tree
    else:
        payload["statesProblem"] = (
            "the code checkout's HEAD tree could not be read, so no record state "
            "was measured; the cards are still named"
        )

    return payload


# --- request validation ----------------------------------------------------


def _request_refusal(files: list[Any]) -> _Refusal | None:
    """The refusal a malformed batch earns before anything is read, or ``None``."""

    if not isinstance(files, list) or not files:
        return _Refusal(
            "empty_paths",
            "name one to twenty repository-relative paths as strings",
        )
    if len(files) > READ_AR_FILES_MAX_PATHS:
        return _Refusal(
            "too_many_paths",
            f"name at most {READ_AR_FILES_MAX_PATHS} paths per call; got {len(files)}",
        )
    for entry in files:
        if not isinstance(entry, str) or not entry:
            return _Refusal("argument-removed", ARGUMENT_REMOVED_SENTENCE)
    return None


# --- memory selection ------------------------------------------------------


@dataclass(frozen=True)
class _Dataset:
    """The converted memory tree's index selection, or why there is none."""

    index_path: Path | None = None
    memory_tree_id: str | None = None
    index_state: str | None = None
    index_problems: tuple[tuple[str, str], ...] = ()


def _memory_selection(context: CoordinationContext, config: Any) -> tuple[str, _Dataset]:
    """The memory state and, when converted, its index selection.

    A missing or unconverted memory worktree is not a refusal: the answer
    says so with the existing code and names no card (packet Failure).
    """

    memory_root = context.onboarding_root.parent
    if not memory_root.exists():
        return "missing", _Dataset()
    if not (memory_root / LAYOUT_MARKER_PATH).is_file():
        return "unconverted", _Dataset()
    try:
        selected = select_knowledge_dataset(memory_root, coordination_root=config.coordination_root)
    except (
        MemoryTreeError,
        IndexMismatchError,
        GitPreparationError,
        KnowledgeStorageError,
        apsw.Error,
        OSError,
    ):
        return "unconverted", _Dataset()
    tree = selected.memory_tree
    if tree is None:
        return "unconverted", _Dataset()
    if memory_format(memory_root) != TEXT_FORMAT:
        return "unconverted", _Dataset()
    return "converted", _Dataset(
        index_path=selected.database_path,
        memory_tree_id=tree.tree_key,
        index_state=tree.index_state,
        index_problems=tuple(tree.problems),
    )


# --- per-path naming -------------------------------------------------------


@dataclass(frozen=True)
class _BatchScope:
    """What one batch measures every path against: memory, tree and folder."""

    memory_state: str
    dataset: _Dataset
    code_tree: str | None
    working_folder: Path | None
    object_directory: Path | None
    """The held capture's store when the read observes a working tree, else ``None``."""


def _name_one(
    context: CoordinationContext,
    code_root: Path,
    path: str,
    scope: _BatchScope,
) -> dict[str, Any]:
    """Name what governs one path: existence, card location, record states."""

    rel = _confined_rel(code_root, path)
    exists = filesystem.is_file(code_root / rel)
    result: dict[str, Any] = {"path": path, "exists": exists}
    if not exists:
        return result

    card_rel, probed = _governing_card(context, rel)
    if card_rel is None or scope.memory_state != "converted":
        result["card"] = None
        if scope.memory_state == "converted":
            # `missing` is said only when there is no card at all (MIK-R83
            # rule 5): a path outside the onboarding scope has none, and so
            # has a path whose card does not exist at its own place.
            result["cardStatus"] = "missing"
        return result
    if probed:
        # The card exists but the route index does not list it: the answer
        # names the stale index and the refresh that repairs it.
        result["routeIndexStale"] = True

    memory_root = context.onboarding_root.parent
    card_absolute = memory_root / "onboarding" / f"{card_rel}.md"
    if not card_absolute.is_file():
        # The route index still covers the path but the card file is gone or
        # not a file (stale index): the row names the absence instead of an
        # unopenable location, and the valid siblings are still answered.
        result["card"] = None
        result["cardStatus"] = "missing"
        result["statesProblem"] = (
            f"the governing card {card_rel} is indexed but has no readable file; "
            "it names no records"
        )
        return result
    try:
        card_text = filesystem.read_text(card_absolute)
    except (UnicodeDecodeError, OSError):
        card_text = None
    if card_text is None:
        # Readability before emission: a card that cannot be read names no
        # location and no states. The actual surface (unreadable, not the
        # converted one) is reported, and the valid siblings are still
        # answered; a failed read never stands in for their aligned data.
        result["card"] = None
        result["cardStatus"] = "missing"
        result["statesProblem"] = (
            f"the governing card {card_rel} is indexed but its file could not be read; "
            "it names no records"
        )
        return result
    result["card"] = _locate(card_absolute, scope.working_folder)
    result["cardStatus"] = "found"
    result.update(
        _record_fields(
            _NamedCard(
                code_root=code_root,
                rel=rel,
                card_rel=card_rel,
                card_absolute=card_absolute,
                card_text=card_text,
            ),
            scope,
        )
    )
    return result


@dataclass(frozen=True)
class _NamedCard:
    """One card, its path and the code root whose records it is measured against."""

    code_root: Path
    rel: str
    card_rel: str
    card_absolute: Path
    card_text: str


def _record_fields(card: _NamedCard, scope: _BatchScope) -> dict[str, Any]:
    """The record counts and action-asking records one card names on the code the caller sees."""

    dataset = scope.dataset
    code_tree = scope.code_tree
    if dataset.index_path is None or code_tree is None:
        return {}
    records = card_records_for_path(
        CardSelection(
            index_path=dataset.index_path,
            memory_tree_id=dataset.memory_tree_id or "",
            index_state=dataset.index_state or "",
            code_tree=CodeTree(card.code_root, code_tree, scope.object_directory),
            index_problems=dataset.index_problems,
        ),
        card.rel,
        card.card_text,
        card.card_rel,
        anchors=SectionAnchors(
            references=_sidecar_references(card.card_absolute),
            own_path=card.rel,
        ),
    )
    fields: dict[str, Any] = {
        "recordCounts": records.counts,
        "cardState": aggregate_state(records.counts),
        "actionNeeded": [
            {
                "id": one.id,
                "kind": one.kind,
                "state": one.state,
                "heading": one.heading,
                "titleFallback": one.title_fallback,
            }
            for one in records.action_needed
        ],
    }
    if records.problem is not None:
        fields["statesProblem"] = records.problem
    if not records.index_complete:
        fields["indexComplete"] = False
    return fields


def _governing_card(context: CoordinationContext, rel: str) -> tuple[str | None, bool]:
    """The card governing ``rel`` as its memory-relative stem, and whether it was probed.

    Which card governs a file is decided as today (packet Preservation): the
    route index says ``present`` exactly when the sidecar covers the path.
    When it does not, the card's own path is checked once (MIK-R83 rule 5): a
    card that exists is served, and the second value says the route index is
    stale so the answer can name ``route_index_refresh``. A path outside the
    onboarding scope is answered as today, with no probe.
    """

    storage_mode = resolve_storage_for_source(rel, context.storage, context.code_repository_name)
    if storage_mode not in _SIDECAR_STORAGE_MODES:
        return None, False
    if _route_sidecar_status(context.onboarding_root, rel) == "present":
        return rel, False
    try:
        card_exists = (context.onboarding_root / f"{rel}.md").is_file()
    except OSError:
        card_exists = False
    return (rel, True) if card_exists else (None, False)


def _sidecar_references(card_absolute: Path) -> Mapping[str, Any] | None:
    """The card sidecar's ``references{}`` mapping, or ``None``.

    Converted ``[n]`` markers resolve through the sidecar beside the card
    (``<card without .md>.json``); a missing or unreadable sidecar simply
    leaves legacy ``cit`` matching and title fallback.
    """

    try:
        document = json.loads(filesystem.read_text(card_absolute.with_suffix(".json")))
    except (UnicodeDecodeError, OSError, ValueError):
        return None
    if not isinstance(document, dict):
        return None
    references = document.get("references")
    return references if isinstance(references, Mapping) else None


def _locate(absolute: Path, working_folder: Path | None) -> str:
    """The location as the caller opens it (MIK-R108 rule 1).

    Relative to the caller's working folder when the file lies in it,
    absolute otherwise.
    """

    if working_folder is not None:
        try:
            return str(absolute.relative_to(working_folder))
        except ValueError:
            pass
    return str(absolute)


# --- overview locations, once per call -------------------------------------


def _overview_locations(
    context: CoordinationContext,
    code_root: Path,
    paths: list[str],
    working_folder: Path | None,
) -> list[str]:
    """The overviews governing the named files, as locations, nearest first.

    Every path contributes its chain -- immediate parent, ancestors, root --
    and each overview is listed once for the call (packet rule 1). Deeper
    chains sort before their ancestors, so a shared ancestor never moves
    before its descendants; ties sort by location for determinism, and the
    repository root (depth zero) sorts last.
    """

    depths: dict[str, int] = {}
    for path in paths:
        try:
            rel = _confined_rel(code_root, path)
        except AuthorityError:
            continue
        chain = rel.split("/")[:-1]
        for depth in range(len(chain), -1, -1):
            ancestor = "/".join(chain[:depth])
            overview = context.onboarding_root / (
                ROUTE_OVERVIEW_NAME if not ancestor else f"{ancestor}/{ROUTE_OVERVIEW_NAME}"
            )
            if overview.is_file():
                depths.setdefault(_locate(overview, working_folder), depth)
    return sorted(depths, key=lambda location: (-depths[location], location))


__all__ = ["ARGUMENT_REMOVED_SENTENCE", "read_ar_files_tool"]
