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
    CODE_STATE_HEAD,
    READ_AR_FILES_MAX_PATHS,
    CardSelection,
    SectionAnchors,
    aggregate_state,
    card_records_for_path,
    head_tree_of,
)
from agents_remember.application.read_files_format import TEXT_FORMAT, memory_format
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
    _context: CoordinationContext | None = None,
) -> dict[str, Any]:
    """Name the knowledge governing a batch of repository-relative paths.

    ``working_folder`` is the caller's working folder when the server knows
    it (a leaf's worktree group under task scope): locations inside it are
    answered relative, so the caller opens them as written. Otherwise
    locations are absolute. ``_context`` is a test seam: callers normally let
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
    head_tree = head_tree_of(code_root)
    code_state = CODE_STATE_HEAD if head_tree is not None else "unmeasured"
    scope = _BatchScope(
        memory_state=memory_state,
        dataset=dataset,
        head_tree=head_tree,
        working_folder=working_folder,
    )

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
        "repoId": repo.repo_id,
        "state": "named",
        "codeState": code_state,
        "memoryState": memory_state,
        "files": file_results,
        "overviews": _overview_locations(context, code_root, files, working_folder),
    }
    if head_tree is not None:
        payload["codeTree"] = head_tree
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
    head_tree: str | None
    working_folder: Path | None


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

    card_rel = _governing_card(context, rel)
    if card_rel is None or scope.memory_state != "converted":
        result["card"] = None
        return result

    memory_root = context.onboarding_root.parent
    card_absolute = memory_root / "onboarding" / f"{card_rel}.md"
    if not card_absolute.is_file():
        # The route index still covers the path but the card file is gone or
        # not a file (stale index): the row names the absence instead of an
        # unopenable location, and the valid siblings are still answered.
        result["card"] = None
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
        result["statesProblem"] = (
            f"the governing card {card_rel} is indexed but its file could not be read; "
            "it names no records"
        )
        return result
    result["card"] = _locate(card_absolute, scope.working_folder)

    dataset = scope.dataset
    head_tree = scope.head_tree
    if dataset.index_path is not None and head_tree is not None:
        records = card_records_for_path(
            CardSelection(
                index_path=dataset.index_path,
                memory_tree_id=dataset.memory_tree_id or "",
                index_state=dataset.index_state or "",
                code_tree=CodeTree(code_root, head_tree),
                index_problems=dataset.index_problems,
            ),
            rel,
            card_text,
            card_rel,
            anchors=SectionAnchors(
                references=_sidecar_references(card_absolute),
                own_path=rel,
            ),
        )
        result["recordCounts"] = records.counts
        result["cardState"] = aggregate_state(records.counts)
        result["actionNeeded"] = [
            {
                "id": one.id,
                "kind": one.kind,
                "state": one.state,
                "heading": one.heading,
                "titleFallback": one.title_fallback,
            }
            for one in records.action_needed
        ]
        if records.problem is not None:
            result["statesProblem"] = records.problem
        if not records.index_complete:
            result["indexComplete"] = False
    return result


def _governing_card(context: CoordinationContext, rel: str) -> str | None:
    """The card governing ``rel`` as its memory-relative stem, or ``None``.

    Which card governs a file is decided as today (packet Preservation): the
    route index says ``present`` exactly when the sidecar covers the path.
    """

    storage_mode = resolve_storage_for_source(rel, context.storage, context.code_repository_name)
    if storage_mode not in _SIDECAR_STORAGE_MODES:
        return None
    if _route_sidecar_status(context.onboarding_root, rel) != "present":
        return None
    return rel


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
