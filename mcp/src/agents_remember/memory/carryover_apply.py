"""Converted carryover's worktree-only write, validate and restore transaction.

The caller admits selection and prepares reanchored document/sidecar and missing
record pairs. This engine neither reads branch memory nor commits, stages,
refreshes the ledger, or persists task artifacts. A target-edited member refuses
the whole batch, so no candidate can be partially carried.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from agents_remember.application.knowledge_worklist.base_cache import converted_base_files
from agents_remember.application.knowledge_worklist.leaf import leaf_worklist, paired_memory_commit
from agents_remember.application.knowledge_writer.writer import _writer_split
from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.route_index import build_route_indexes
from agents_remember.memory.conversion.base import pinned_version
from agents_remember.memory_quality.knowledge_validator import (
    KnowledgeTree,
    code_tree_from_git,
    knowledge_tree_from_directory,
    knowledge_tree_from_git,
    validate_tree,
)
from agents_remember.memory_quality.knowledge_worklist_section import worklist_summary
from agents_remember.models.knowledge_files.documents import RECORD_DIRECTORIES
from agents_remember.worktrees.worktree_contract import load_contract


@dataclass(frozen=True)
class _Tree:
    """The complete write extent, including caches, hidden files and empty dirs."""

    files: dict[str, tuple[bytes, int]]
    directories: dict[str, int]

    @classmethod
    def read(cls, root: Path) -> _Tree:
        files: dict[str, tuple[bytes, int]] = {}
        directories: dict[str, int] = {}
        for top in ("knowledge", "onboarding"):
            base = root / top
            if not base.exists() and not base.is_symlink():
                continue
            if base.is_symlink() or not base.is_dir():
                raise ValueError(f"carryover write extent is not a regular directory: {base}")
            for directory, dirs, names in os.walk(base):
                full = Path(directory)
                directories[full.relative_to(root).as_posix()] = stat.S_IMODE(full.stat().st_mode)
                for name in (*dirs, *names):
                    path = full / name
                    if path.is_symlink():
                        raise ValueError(f"carryover refuses a symlink in its write extent: {path}")
                for name in names:
                    path = full / name
                    if not path.is_file():
                        raise ValueError(f"carryover refuses a non-regular file: {path}")
                    files[path.relative_to(root).as_posix()] = (
                        path.read_bytes(),
                        stat.S_IMODE(path.stat().st_mode),
                    )
        return cls(files, directories)

    def restore(self, root: Path) -> None:
        current = self.read(root)
        for relative in current.files.keys() - self.files.keys():
            (root / relative).unlink()
        for relative, mode in sorted(self.directories.items(), key=lambda one: len(one[0])):
            path = root / relative
            path.mkdir(parents=True, exist_ok=True)
            path.chmod(mode)
        for relative, (data, mode) in self.files.items():
            path = root / relative
            if not path.exists() or path.read_bytes() != data:
                path.write_bytes(data)
            path.chmod(mode)
        created = current.directories.keys() - self.directories.keys()
        for relative in sorted(
            created, key=lambda one: len(PurePosixPath(one).parts), reverse=True
        ):
            (root / relative).rmdir()


def _check_payloads(payloads: Mapping[str, bytes]) -> None:
    """Only document/sidecar and record pairs may enter the write extent."""

    for relative, data in payloads.items():
        path = PurePosixPath(relative)
        parts = path.parts
        if path.is_absolute() or path.as_posix() != relative or ".." in parts:
            raise ValueError(f"carryover payload is not a repository-relative path: {relative!r}")
        if not isinstance(data, bytes):
            raise ValueError(f"carryover payload is not bytes: {relative}")
        document = len(parts) > 1 and parts[0] == "onboarding"
        record = (
            len(parts) == 3 and parts[0] == "knowledge" and parts[1] in RECORD_DIRECTORIES.values()
        )
        if not (document or record) or path.suffix not in {".md", ".json"}:
            raise ValueError(f"carryover payload is not a document or record: {relative}")
        if relative.endswith(".index.json") or any(part.startswith(".") for part in parts[:-1]):
            raise ValueError(f"carryover payload is derived or hidden: {relative}")
        partner = path.with_suffix(".json" if path.suffix == ".md" else ".md").as_posix()
        if partner not in payloads and (document or path.suffix == ".md"):
            raise ValueError(f"carryover payload unit lacks its paired file: {relative}")


def _changed_paths(root: Path) -> set[str]:
    # diff is read-only: unlike status it does not opportunistically refresh the index.
    result = run_git(root, ["diff", "--name-only", "-z", "HEAD", "--"])
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "cannot read memory changes against HEAD")
    return set(filter(None, result.stdout.split("\0")))


def _conflicts(
    before: _Tree, head: KnowledgeTree, payloads: Mapping[str, bytes], changed: set[str]
) -> list[str]:
    conflicts = []
    for path, data in payloads.items():
        current = before.files.get(path)
        contents = None if current is None else current[0]
        if contents == data:
            continue  # interrupted writes are eligible even if staged or otherwise dirty
        if contents != head.get(path) or path in changed:
            conflicts.append(path)
        elif path.startswith("knowledge/") and contents is not None:
            conflicts.append(path)  # an existing record is never overwritten, even when clean
    return sorted(conflicts)


def _base_for(contract: Any, candidate: KnowledgeTree) -> tuple[str, KnowledgeTree]:
    repository = contract.memory_repo_path or contract.memory_worktree
    commit = paired_memory_commit(
        repository,
        contract.memory_source_branch,
        contract.code_repo_path,
        contract.code_base_commit,
    )
    base = knowledge_tree_from_git(repository, commit, label=f"leaf memory base {commit}")
    if not base.converted:
        version = pinned_version(candidate)
        if version is None:
            raise ValueError("converted carryover has no conversion-format version")
        base = KnowledgeTree(
            label=f"converted leaf memory base {commit}",
            files=converted_base_files(
                repository,
                commit,
                code=(contract.code_repo_path, contract.code_base_commit),
                version=version,
                cache_directory=None,
            ),
        )
    return commit, base


def _edited_candidates(
    selected: Sequence[Mapping[str, Any]], root: Path, conflicts: list[str]
) -> list[dict[str, Any]]:
    """Report document conflicts against the stable selection keys, plus record paths."""

    rows = []
    for candidate in selected:
        target = Path(str(candidate["target_onboarding"]))
        if not target.is_absolute():
            target = root / target
        relative = target.relative_to(root).as_posix()
        pair = {relative, PurePosixPath(relative).with_suffix(".json").as_posix()}
        paths = sorted(pair.intersection(conflicts))
        if paths:
            rows.append({**candidate, "decision": "target-edited", "target_edited_paths": paths})
    return rows


def _refused(
    result: dict[str, Any],
    root: Path,
    head: KnowledgeTree,
    payloads: Mapping[str, bytes],
) -> dict[str, Any]:
    current = _Tree.read(root)
    if result["route_index_refresh"].get("state") == "refreshed":
        result["route_index_refresh"]["rolled_back"] = True
    result.update(
        state="refused",
        written_paths=[],
        carried_paths_differ_from_head=sorted(
            path for path in payloads if (current.files.get(path, (None, 0))[0] != head.get(path))
        ),
    )
    return result


def apply_converted(
    request: Any,
    plan: dict[str, Any],
    selected: Sequence[Mapping[str, Any]],
    payloads: Mapping[str, bytes],
    storage: Any,
) -> dict[str, Any]:
    """Apply admitted payload units; any conflict refuses the entire batch.

    ``request`` supplies the existing CarryoverRequest fields. ``selected`` uses
    existing candidate dictionaries (including ``target_onboarding``); ``payloads``
    is the union of their prepared document/sidecar and missing record pairs.
    The leaf worklist is recomputed without persistence; the parent may publish
    it through its ordinary workflow. Refusals restore preexisting edits, modes,
    route caches and directory topology, not HEAD. Git's index is never written.
    """

    root = request.target_memory.resolve()
    _check_payloads(payloads)
    before = _Tree.read(root)
    head = knowledge_tree_from_git(root, "HEAD")
    conflicts = _conflicts(before, head, payloads, _changed_paths(root))
    result: dict[str, Any] = {
        **plan,
        "carried": list(selected),
        "written_paths": [],
        "violations": [],
        "route_index_refresh": {"state": "not-run"},
        "publication": "The recovery leaf curator answers its worklist with its own rows; "
        "the leaf closeout makes the one memory commit.",
    }
    if conflicts:
        result.update(
            reason="target-edited",
            target_edited_paths=conflicts,
            target_edited=_edited_candidates(selected, root, conflicts),
        )
        return _refused(result, root, head, payloads)
    try:
        contract = load_contract(request.target_contract_path)
        candidate = knowledge_tree_from_directory(root)
        memory_base, base = _base_for(contract, candidate)
        official = str(plan["official_code_head"])
        code = code_tree_from_git(request.code_repository_root, official)
        written = []
        for relative, data in sorted(payloads.items()):
            if before.files.get(relative, (None, 0))[0] == data:
                continue
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            written.append(relative)
        indexes = build_route_indexes(
            code_root=contract.code_worktree,
            onboarding_root=root / "onboarding",
            repository=request.code_repository_name,
            storage=storage,
            dry_run=False,
        )
        result["route_index_refresh"] = {"state": "refreshed", **indexes.to_dict()}
        validation = validate_tree(
            knowledge_tree_from_directory(root),
            bases=(base,),
            code=code,
        )
        refusals, reports = _writer_split(validation)
        result.update(
            memory_base_commit=memory_base,
            violations=[one.to_document() for one in (*refusals, *reports)],
        )
        if refusals:
            result["reason"] = "knowledge-validator-refused"
            before.restore(root)
            return _refused(result, root, head, payloads)
        document = leaf_worklist(contract, persist=False)
        after = _Tree.read(root)
        all_written = sorted(
            path
            for path, (data, _mode) in after.files.items()
            if before.files.get(path, (None, 0))[0] != data
        )
        result.update(
            state="carried-into-worktree" if all_written else "nothing-to-carryover",
            written_paths=all_written,
            payload_written_paths=written,
            worklist=worklist_summary(document, None) if document else None,
        )
        return result
    except BaseException as error:
        before.restore(root)
        if not isinstance(error, Exception):
            raise
        result.update(reason="carryover-failed", problems=[str(error)])
        return _refused(result, root, head, payloads)
