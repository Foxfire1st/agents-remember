"""One pathname inventory per side supplies the Knowledge tree and its coverage.

The only content read is the selected memory's scope settings. Names, presence and
counts share these inventories; no source/card content, hashes or persistent cache.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agents_remember.application.knowledge_reader.files import read_memory_file
from agents_remember.application.knowledge_reader.selection import (
    READ_FAILURES,
    ReaderReadError,
    ReaderSelection,
)
from agents_remember.kernel.coordination_context.json_settings import parse_json_storage_settings
from agents_remember.kernel.coordination_context.setting_values import require_mapping
from agents_remember.kernel.coordination_context.storage import resolve_storage_for_source
from agents_remember.kernel.git_command import (
    GIT_METADATA_TIMEOUT_SECONDS,
    GitRunnerOptions,
    run_git,
)
from agents_remember.kernel.sidecar_pairing import confine_rel
from agents_remember.memory_quality.knowledge_validator.trees import is_excluded_from_knowledge
from agents_remember.models.knowledge_files.sidecars import ROOT_ROUTE_PATH

_OPTIONS = GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS)


@dataclass
class Pathnames:
    files: set[str] = field(default_factory=set)
    directories: set[str] = field(default_factory=set)
    sources: set[str] = field(default_factory=set)


def read_tree_listing(
    selection: ReaderSelection, directory: str, entries: dict[str, int]
) -> dict[str, Any]:
    """One code pass and one memory enumeration feed every immediate child."""

    code, code_problem = _read_code(selection, directory)
    memory, memory_problem = _read_memory(selection, directory)
    children = _children(directory, code, memory, entries)
    if memory_problem:
        _unavailable(children, memory_problem)
    else:
        assert memory is not None
        _presence(directory, children, memory.files, selection.index.state.complete)
        try:
            counts = _coverage(selection, directory, memory.files, code.sources)
            if code_problem:
                raise ReaderReadError(code_problem)
        except (*READ_FAILURES, ValueError, TypeError) as error:
            _unavailable(children, f"coverage unavailable: {type(error).__name__}: {error}")
        else:
            _apply_counts(children, counts)
    state = (
        {"state": "listed"}
        if not code_problem
        else {"state": "unavailable", "detail": code_problem}
    )
    return {"directory": directory, "code": state, "children": children}


def _read_code(selection: ReaderSelection, directory: str) -> tuple[Pathnames, str | None]:
    try:
        if selection.code_tree is None:
            raise ReaderReadError(selection.code_note)
        return _git_paths(selection.code_tree.repository, selection.code_tree.tree, directory), None
    except READ_FAILURES as error:
        return Pathnames(), f"code paths unavailable: {type(error).__name__}: {error}"


def _read_memory(selection: ReaderSelection, directory: str) -> tuple[Pathnames | None, str | None]:
    try:
        return _memory_paths(selection, directory), None
    except READ_FAILURES as error:
        return None, f"memory paths unavailable: {type(error).__name__}: {error}"


def _children(
    directory: str, code: Pathnames, memory: Pathnames | None, entries: dict[str, int]
) -> list[dict[str, Any]]:
    prefix = "" if directory == ROOT_ROUTE_PATH else f"{directory}/"
    children: dict[str, dict[str, Any]] = {}
    for path in code.files | code.directories:
        if not path.startswith(prefix):
            continue
        rest = path.removeprefix(prefix)
        name = rest.split("/", 1)[0]
        kind = "dir" if "/" in rest or path in code.directories else "file"
        children[name] = {"name": name, "kind": kind, "inCode": True}
    if memory is not None:
        base = f"onboarding/{prefix}"
        _add_memory_children(children, base, memory)
    for name, count in entries.items():
        children.setdefault(name, {"name": name, "kind": "dir", "inCode": False})["entries"] = count
    rows = sorted(children.values(), key=lambda row: (row["kind"] != "dir", row["name"].lower()))
    for row in rows:
        row["path"] = prefix + row["name"]
        row.setdefault("entries", 0)
        if memory is not None:
            row.setdefault("onboarding", False)
    return rows


def _code_name(name: str) -> str | None:
    if name in {"overview.md", "overview.json"} or name.endswith(".index.json"):
        return None
    for suffix in (".md", ".json"):
        if name.endswith(suffix):
            return name.removesuffix(suffix)
    return None


def _presence(directory: str, rows: list[dict[str, Any]], files: set[str], complete: bool) -> None:
    prose = {path for path in files if path.endswith(".md")}
    base = "onboarding/" if directory == ROOT_ROUTE_PATH else f"onboarding/{directory}/"
    known = set()
    for path in prose:
        rest = path.removeprefix(base)
        if rest not in (path, "overview.md"):
            known.add(rest.split("/", 1)[0] if "/" in rest else rest.removesuffix(".md"))
    for row in rows:
        own = (
            f"onboarding/{row['path']}/overview.md"
            if row["kind"] == "dir"
            else f"onboarding/{row['path']}.md"
        )
        present = own in prose or row["name"] in known or row["entries"] > 0
        if present or complete:
            row["hasKnowledge"] = present
        if row["kind"] == "dir":
            row["hasOverview"] = own in prose


def _coverage(
    selection: ReaderSelection, directory: str, memory_files: set[str], sources: set[str]
) -> dict[str, tuple[int, int]]:
    read = read_memory_file(selection, "system/settings.json")
    if read.state != "present" or read.text is None:
        raise ReaderReadError(f"selected system/settings.json {read.state}: {read.detail or ''}")
    root = require_mapping(json.loads(read.text), "selected system/settings.json")
    onboarding = require_mapping(root.get("onboarding", root), "onboarding scope")
    settings = parse_json_storage_settings(root, onboarding)
    prefix = "" if directory == ROOT_ROUTE_PATH else f"{directory}/"
    counts: dict[str, tuple[int, int]] = {}
    for path in sources:
        if resolve_storage_for_source(path, settings, selection.repository_id) == "disabled":
            continue
        rest = path.removeprefix(prefix)
        if "/" not in rest:
            continue
        child = rest.split("/", 1)[0]
        files, cards = counts.get(child, (0, 0))
        card = f"onboarding/{path}.md"
        counted = card in memory_files and not card.endswith("/overview.md")
        counts[child] = files + 1, cards + int(counted)
    return counts


def _apply_counts(rows: list[dict[str, Any]], counts: dict[str, tuple[int, int]]) -> None:
    for row in rows:
        if row["kind"] == "dir":
            files, cards = counts.get(row["name"], (0, 0))
            row["coverage"] = {"state": "counted", "files": files, "cards": cards}


def _unavailable(rows: list[dict[str, Any]], detail: str) -> None:
    for row in rows:
        if row["kind"] == "dir":
            row["coverage"] = {"state": "unavailable", "detail": detail}


def _git_paths(repository: Path, revision: str, directory: str) -> Pathnames:
    pathspec = [] if directory == ROOT_ROUTE_PATH else ["--", f"{directory}/"]
    result = run_git(repository, ["ls-tree", "-r", "-t", "-z", revision, *pathspec], _OPTIONS)
    if result.returncode:
        raise ReaderReadError(result.stderr.strip() or "git ls-tree failed")
    paths = Pathnames()
    for row in result.stdout.split("\0"):
        if not row:
            continue
        metadata, _, path = row.partition("\t")
        mode, kind, _object = metadata.split(" ", 2)
        if kind == "tree":
            paths.directories.add(path)
        elif kind == "blob":
            paths.files.add(path)
            if mode in {"100644", "100755"}:
                paths.sources.add(path)
    return paths


def _memory_paths(selection: ReaderSelection, directory: str) -> Pathnames:
    base = "onboarding" if directory == ROOT_ROUTE_PATH else f"onboarding/{directory}"
    if selection.memory_root is None:
        paths = _git_paths(selection.memory_repository, str(selection.revision), base)
    else:
        paths = Pathnames()
        root = selection.memory_root / confine_rel(selection.memory_root, base)
        try:
            root.stat()
        except FileNotFoundError:
            return paths
        for here, directories, files in os.walk(root, onerror=_raise_walk_error):
            directories[:] = [name for name in directories if not name.startswith(".")]
            paths.files.update(
                (Path(here) / name).relative_to(selection.memory_root).as_posix() for name in files
            )
            paths.directories.update(
                (Path(here) / name).relative_to(selection.memory_root).as_posix()
                for name in directories
            )
    paths.files = {path for path in paths.files if not is_excluded_from_knowledge(path)}
    paths.directories = {
        path for path in paths.directories if not is_excluded_from_knowledge(f"{path}/_")
    }
    return paths


def _raise_walk_error(error: OSError) -> None:
    raise error


def _add_memory_children(children: dict[str, dict[str, Any]], base: str, memory: Pathnames) -> None:
    for path in memory.files | memory.directories:
        if not path.startswith(base):
            continue
        rest = path.removeprefix(base)
        name = rest.split("/", 1)[0]
        kind = "dir" if "/" in rest or path in memory.directories else "file"
        if kind == "file":
            name = _code_name(name)
        if name:
            children.setdefault(name, {"name": name, "kind": kind, "inCode": False})[
                "onboarding"
            ] = True
