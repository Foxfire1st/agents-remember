"""Resolve task-tree leaf refs to canonical leaf document identities."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from difflib import get_close_matches
from pathlib import Path

from agents_remember.tasks import TASK_DOCUMENT_SCHEMA, TaskDocument, read_task_doc
from agents_remember.worktrees.task_resolver import (
    ARCHIVE_DIR,
    ENCLOSURES_DIR,
    TaskResolutionError,
    is_archived_path,
    leaf_enclosure_path,
    resolve_active_task_root,
    slugify,
)

LEAF_REF_EXPECTED_FORM = "<repo>/<master-folder>/<doc-id>"


@dataclass(frozen=True)
class ResolvedLeafRef:
    repo_name: str
    task_root: Path
    master_folder: str
    doc_id: str
    qualified_id: str
    aliases: tuple[str, ...] = ()


class LeafRefResolutionError(TaskResolutionError):
    """Raised when a leaf ref cannot resolve to one canonical task-doc id."""

    def __init__(
        self,
        ref: str,
        *,
        repo_name: str | None,
        reason: str,
        candidates: Iterable[str] = (),
    ) -> None:
        self.ref = ref
        self.repo_name = repo_name
        self.reason = reason
        self.candidates = tuple(dict.fromkeys(candidates))
        self.status = "leaf-ref-ambiguous" if reason == "ambiguous" else "leaf-ref-not-found"
        scope = f" for repo {repo_name!r}" if repo_name else ""
        nearest = ", ".join(self.candidates) if self.candidates else "none"
        super().__init__(
            f"leaf ref {ref!r} is {reason}{scope}; expected "
            f"{LEAF_REF_EXPECTED_FORM}; candidates: {nearest}"
        )


@dataclass(frozen=True)
class _LeafCandidate:
    repo_name: str
    task_root: Path
    doc_id: str
    aliases: tuple[str, ...]

    @property
    def qualified_id(self) -> str:
        return f"{self.repo_name}/{self.task_root.name}/{self.doc_id}"


@dataclass(frozen=True)
class _ParsedLeafRef:
    repo_name: str | None
    master_folder: str | None
    value: str


def resolve_leaf_ref(
    coordination_root: Path,
    repo_name: str | None,
    ref: str,
    *,
    task_name: str | None = None,
    parent_task: str | None = None,
) -> ResolvedLeafRef:
    """Resolve ``ref`` to the canonical qualified leaf doc id.

    Accepted refs are the canonical qualified id, a unique doc id, and unique
    legacy stem/slug aliases. No-match and ambiguous refs raise
    :class:`LeafRefResolutionError` with the expected wire form and candidates.
    """

    parsed = _parse_leaf_ref(ref)
    resolved_repo = _resolve_repo_name(coordination_root, repo_name, parsed)
    candidates = _leaf_candidates(
        coordination_root,
        resolved_repo,
        task_name=task_name,
        parent_task=parent_task,
        master_folder=parsed.master_folder,
    )
    wanted = _normalize_ref(parsed.value)
    matches = [
        candidate
        for candidate in candidates
        if wanted in {_normalize_ref(alias) for alias in candidate.aliases}
    ]
    unique = {candidate.qualified_id: candidate for candidate in matches}
    if len(unique) == 1:
        candidate = next(iter(unique.values()))
        return ResolvedLeafRef(
            repo_name=candidate.repo_name,
            task_root=candidate.task_root,
            master_folder=candidate.task_root.name,
            doc_id=candidate.doc_id,
            qualified_id=candidate.qualified_id,
            aliases=candidate.aliases,
        )
    if len(unique) > 1:
        raise LeafRefResolutionError(
            ref,
            repo_name=resolved_repo,
            reason="ambiguous",
            candidates=sorted(unique),
        )
    raise LeafRefResolutionError(
        ref,
        repo_name=resolved_repo,
        reason="unmatchable",
        candidates=_candidate_suggestions(parsed.value, candidates),
    )


def leaf_ref_enclosure_aliases(
    coordination_root: Path,
    repo_name: str,
    leaf_ref: str,
    *,
    task_name: str,
    parent_task: str | None = None,
) -> tuple[str, ...]:
    resolved = resolve_leaf_ref(
        coordination_root,
        repo_name,
        leaf_ref,
        task_name=task_name,
        parent_task=parent_task,
    )
    return tuple(dict.fromkeys((resolved.doc_id, *resolved.aliases, leaf_ref)))


def resolve_leaf_enclosure_contract_for_ref(
    coordination_root: Path,
    repo_name: str,
    task_name: str,
    *,
    leaf_id: str,
    parent_task: str | None = None,
) -> Path | None:
    task_root = resolve_active_task_root(
        coordination_root,
        repo_name,
        task_name,
        parent_task=parent_task,
    )
    aliases = _enclosure_aliases_or_raw(
        coordination_root,
        repo_name,
        leaf_id,
        task_name=task_name,
        parent_task=parent_task,
    )
    for alias in aliases:
        path = leaf_enclosure_path(task_root, alias)
        if path.exists():
            return path
    return None


def _enclosure_aliases_or_raw(
    coordination_root: Path,
    repo_name: str,
    leaf_id: str,
    *,
    task_name: str,
    parent_task: str | None,
) -> tuple[str, ...]:
    try:
        return leaf_ref_enclosure_aliases(
            coordination_root,
            repo_name,
            leaf_id,
            task_name=task_name,
            parent_task=parent_task,
        )
    except LeafRefResolutionError:
        # Legacy contract lookup still falls back to the raw enclosure path when a task tree is absent
        # or does not prove a unique alias. Malformed task docs still fail loud while indexing aliases.
        return (leaf_id,)


def _normalize_ref(value: str) -> str:
    return slugify(value)


def _parse_leaf_ref(ref: str) -> _ParsedLeafRef:
    value = ref.strip()
    parts = [part.strip() for part in value.split("/")]
    if len(parts) == 3 and all(parts):
        return _ParsedLeafRef(parts[0], parts[1], parts[2])
    return _ParsedLeafRef(None, None, value)


def _single_repo_name(coordination_root: Path) -> str | None:
    tasks_root = coordination_root / "tasks"
    if not tasks_root.is_dir():
        return None
    names = [
        path.name
        for path in sorted(tasks_root.iterdir())
        if path.is_dir() and path.name != ARCHIVE_DIR and not is_archived_path(path)
    ]
    return names[0] if len(names) == 1 else None


def _active_task_roots(
    coordination_root: Path,
    repo_name: str,
    *,
    task_name: str | None,
    parent_task: str | None,
    master_folder: str | None,
) -> list[Path]:
    repo_task_root = coordination_root / "tasks" / repo_name
    if master_folder:
        root = repo_task_root / master_folder
        if not root.exists() or is_archived_path(root):
            return []
        if task_name:
            task_root = resolve_active_task_root(
                coordination_root,
                repo_name,
                task_name,
                parent_task=parent_task,
            )
            if task_root != root:
                return []
        return [root]
    if task_name:
        root = resolve_active_task_root(
            coordination_root,
            repo_name,
            task_name,
            parent_task=parent_task,
        )
        return [root] if root.exists() and not is_archived_path(root) else []
    if not repo_task_root.is_dir():
        return []
    roots: list[Path] = []
    for path in sorted(repo_task_root.rglob("task.json")):
        if is_archived_path(path) or ENCLOSURES_DIR in path.parts:
            continue
        roots.append(path.parent)
    return roots


def _read_optional_task_document(path: Path) -> TaskDocument | None:
    if not path.exists():
        return None
    if not _has_task_doc_schema_marker(path):
        return None
    try:
        return read_task_doc(path)
    except FileNotFoundError:
        return None


def _has_task_doc_schema_marker(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(data, dict) and data.get("schema") == TASK_DOCUMENT_SCHEMA


def _candidate_aliases(*values: str) -> tuple[str, ...]:
    aliases: list[str] = []
    for value in values:
        stripped = value.strip()
        if stripped:
            aliases.append(stripped)
            aliases.append(slugify(stripped))
    return tuple(dict.fromkeys(aliases))


def _leaf_candidates_for_root(repo_name: str, task_root: Path) -> list[_LeafCandidate]:
    by_qualified: dict[str, set[str]] = {}

    def add_candidate(doc_id: str, aliases: Iterable[str]) -> None:
        clean_id = doc_id.strip()
        if not clean_id:
            return
        qualified = f"{repo_name}/{task_root.name}/{clean_id}"
        bucket = by_qualified.setdefault(qualified, set())
        bucket.update(_candidate_aliases(clean_id, *aliases, qualified))

    root_doc = _read_optional_task_document(task_root / "task.json")
    if root_doc is not None:
        if root_doc.kind == "master":
            for ref in root_doc.subTasks:
                file_stem = Path(ref.file).stem if ref.file else ""
                add_candidate(ref.number, (file_stem, ref.file))
        else:
            enclosure_aliases = [ref.leafId for ref in root_doc.enclosures]
            add_candidate(root_doc.id, (root_doc.slug, task_root.name, *enclosure_aliases))

    for json_path in sorted(task_root.glob("*.json")):
        if json_path.name == "task.json":
            continue
        if not _has_task_doc_schema_marker(json_path):
            continue
        doc = read_task_doc(json_path)
        if doc.kind == "master":
            continue
        enclosure_aliases = [ref.leafId for ref in doc.enclosures]
        add_candidate(doc.id, (doc.slug, json_path.stem, *enclosure_aliases))

    candidates: list[_LeafCandidate] = []
    for qualified, aliases in by_qualified.items():
        _, _, doc_id = qualified.split("/", 2)
        candidates.append(
            _LeafCandidate(
                repo_name=repo_name,
                task_root=task_root,
                doc_id=doc_id,
                aliases=tuple(sorted(aliases)),
            )
        )
    return sorted(candidates, key=lambda candidate: candidate.qualified_id.lower())


def _leaf_candidates(
    coordination_root: Path,
    repo_name: str,
    *,
    task_name: str | None = None,
    parent_task: str | None = None,
    master_folder: str | None = None,
) -> list[_LeafCandidate]:
    candidates: list[_LeafCandidate] = []
    for task_root in _active_task_roots(
        coordination_root,
        repo_name,
        task_name=task_name,
        parent_task=parent_task,
        master_folder=master_folder,
    ):
        candidates.extend(_leaf_candidates_for_root(repo_name, task_root))
    return candidates


def _candidate_suggestions(ref: str, candidates: list[_LeafCandidate]) -> tuple[str, ...]:
    if not candidates:
        return ()
    by_alias: dict[str, list[_LeafCandidate]] = {}
    for candidate in candidates:
        for alias in candidate.aliases:
            by_alias.setdefault(_normalize_ref(alias), []).append(candidate)
    nearest = get_close_matches(_normalize_ref(ref), sorted(by_alias), n=5, cutoff=0.25)
    suggested: list[str] = []
    for alias in nearest:
        suggested.extend(candidate.qualified_id for candidate in by_alias[alias])
    if not suggested:
        suggested = [candidate.qualified_id for candidate in candidates[:5]]
    return tuple(dict.fromkeys(suggested))


def _resolve_repo_name(
    coordination_root: Path,
    repo_name: str | None,
    parsed: _ParsedLeafRef,
) -> str:
    if parsed.repo_name and repo_name and parsed.repo_name != repo_name:
        candidates = _candidate_suggestions(
            parsed.value,
            _leaf_candidates(coordination_root, repo_name),
        )
        raise LeafRefResolutionError(
            "/".join(part for part in (parsed.repo_name, parsed.master_folder, parsed.value) if part),
            repo_name=repo_name,
            reason="unmatchable",
            candidates=candidates,
        )
    resolved = parsed.repo_name or repo_name or _single_repo_name(coordination_root)
    if resolved:
        return resolved
    raise LeafRefResolutionError(
        parsed.value,
        repo_name=None,
        reason="ambiguous",
        candidates=(),
    )
