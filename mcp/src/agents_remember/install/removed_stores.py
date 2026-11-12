"""One inventory of retired stores, shared by install reporting and explicit removal."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

from agents_remember.kernel.primitives.gate_vocab import STORED_GATE_WAY_OUT
from agents_remember.tasks.task_paths import NOTES_DIR, SERIES_CONTRACT_FILENAME
from agents_remember.worktrees.worktree_contract import ContractError, load_contract

# Stores the parts MIK-R113 removed wrote under the coordination root, relative to it
# (rule 5). The writers are gone; the files and folders stay until the user says otherwise.
REMOVED_PART_STORE_PATHS = (
    "logs/dashboard/terminal-sessions.json",
    "logs/observer/workspace/operator-inbox.jsonl",
    "logs/observer/workspace/expectation-rows.jsonl",
    "logs/observer/workspace/orchestration-nudges.jsonl",
    "logs/observer/workspace/supervisor-signals.jsonl",
    "logs/observer/workspace/supervisor-heartbeat.json",
    "logs/observer/workspace/agent-notifier-death-watch.json",
    "logs/observer/workspace/terminal-observer-health.json",
    "runtime/harness-control",
    "runtime/structural-seat-locks",
    "runtime/eve-carriers",
    "runtime/eve-agent",
)
# The gate logs. The landing chain still reads them, and no part of the product decides a
# gate any more: removing them is the way out of a refusal by a stored gate.
STORED_GATE_LOG_PATTERNS = (
    "logs/observer/workspace/gates.jsonl",
    "logs/observer/lifecycles/*/gates.jsonl",
)
REMOVED_LIFECYCLE_STORE_PATTERNS = tuple(
    f"logs/observer/lifecycles/*/{name}"
    for name in ("events.jsonl", "heartbeat.json", "served.jsonl")
)
# What an install's report says beside the count of such stores: where the list is and that
# only the user's command removes them.
REMOVED_STORES_NOTICE = (
    "The install deleted none of them: `agents-remember removed-stores --dry-run` lists "
    "them, and without `--dry-run` the command removes them on your word"
)


def _own_mode(path: Path, unreadable: dict[str, str]) -> int | None:
    """The mode of ``path`` itself, never of what a link points to; none when it is absent.

    A path that is there and cannot be examined goes into ``unreadable`` with the reason:
    a store may lie there, so it is not taken for absent.
    """
    try:
        return path.lstat().st_mode
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as error:
        unreadable[path.as_posix()] = str(error)
        return None


def _entries(folder: Path, unreadable: dict[str, str]) -> list[Path]:
    """A folder's entries in order; none for an absent folder, a reason for an unreadable one."""
    try:
        return sorted(folder.iterdir())
    except (FileNotFoundError, NotADirectoryError):
        return []
    except OSError as error:
        unreadable[folder.as_posix()] = str(error)
        return []


def _is_plain_file(path: Path, unreadable: dict[str, str]) -> bool:
    mode = _own_mode(path, unreadable)
    return mode is not None and stat.S_ISREG(mode)


def _folder_files(folder: Path, unreadable: dict[str, str]) -> list[Path]:
    """A store folder's whole content: its files, and its links as the links themselves."""
    files: list[Path] = []
    for path in _entries(folder, unreadable):
        mode = _own_mode(path, unreadable)
        if mode is None:
            continue
        if stat.S_ISDIR(mode):
            files.extend(_folder_files(path, unreadable))
        elif stat.S_ISLNK(mode) or stat.S_ISREG(mode):
            files.append(path)
    return files


def _store_files(store: Path, unreadable: dict[str, str]) -> list[Path]:
    """One store's files: a folder's whole content, or the file with what stands beside it.

    Beside a store file stand its lock (``<name>.lock``, ``.<name>.lock``, ``<stem>.lock``) and
    any copy an earlier repair left (``<name>.<anything>``). They are the store's files also
    when the store file itself is gone. Nothing follows a symbolic link: a link inside a store
    folder is one of the store's files as the link itself, and a store that is a link is none.
    A folder that cannot be listed and a path that cannot be examined go into ``unreadable``.
    """
    mode = _own_mode(store, unreadable)
    if mode is not None and stat.S_ISLNK(mode):
        return []
    if mode is not None and stat.S_ISDIR(mode):
        return sorted(_folder_files(store, unreadable))
    names = {f".{store.name}.lock", f"{store.name.split('.')[0]}.lock"}
    beside = [
        path
        for path in _entries(store.parent, unreadable)
        if path.name in names or path.name.startswith(f"{store.name}.")
    ]
    return [path for path in (store, *beside) if _is_plain_file(path, unreadable)]


def _pattern_paths(
    coordination_root: Path, patterns: tuple[str, ...], unreadable: dict[str, str]
) -> list[Path]:
    """Expand owned file patterns without descending into a lifecycle's content."""
    found: list[Path] = []
    for pattern in patterns:
        folder, star, name = pattern.partition("*/")
        candidates = (
            [entry / name for entry in _entries(coordination_root / folder, unreadable)]
            if star
            else [coordination_root / pattern]
        )
        found.extend(candidates)
    return found


def _terminal_lifecycles(coordination_root: Path, unreadable: dict[str, str]) -> set[str]:
    """Prove terminal subjects from all canonical contracts, including archived ones.

    Evidence copies under notes are not contracts. Any failed traversal or native
    contract read makes this census incomplete and proves no lock reclaimable.
    """
    terminal: dict[str, bool] = {}
    failures: dict[str, str] = {}

    def failed(error: OSError) -> None:
        failures[str(error.filename)] = str(error)

    tasks_root = coordination_root / "tasks"
    for folder, directories, files in os.walk(tasks_root, onerror=failed):
        directories[:] = [name for name in directories if name != NOTES_DIR]
        for name in directories:
            path = Path(folder, name)
            if path.is_symlink():
                failures[str(path)] = "linked task directory prevents a complete contract census"
        if SERIES_CONTRACT_FILENAME not in files:
            continue
        path = Path(folder, SERIES_CONTRACT_FILENAME)
        try:
            contract = load_contract(path)
        except (OSError, ContractError) as error:
            failures[str(path)] = str(error)
            continue
        if contract.lifecycle_id:
            terminal[contract.lifecycle_id] = terminal.get(contract.lifecycle_id, True) and (
                contract.cleanup in {"completed", "abandoned"} and not contract.unknown_cells
            )
    unreadable.update(failures)
    return set() if failures else {subject for subject, done in terminal.items() if done}


def _terminal_lock_stores(
    coordination_root: Path, unreadable: dict[str, str]
) -> list[dict[str, Any]]:
    lifecycles = coordination_root / "logs/observer/lifecycles"
    candidates = []
    for folder in _entries(lifecycles, unreadable):
        mode = _own_mode(folder, unreadable)
        if mode is None or not stat.S_ISDIR(mode):
            continue
        lock = folder / "gates.jsonl.lock"
        if _entries(folder, unreadable) == [lock] and _is_plain_file(lock, unreadable):
            candidates.append((folder, lock))
    if not candidates:
        return []
    terminal = _terminal_lifecycles(coordination_root, unreadable)
    return [
        _store_entry(folder, [lock], gate_log=False)
        for folder, lock in candidates
        if folder.name in terminal
    ]


def _files_bytes(files: list[Path]) -> int:
    total = 0
    for path in files:
        try:
            total += path.lstat().st_size
        except OSError:
            continue
    return total


def removed_part_stores(
    coordination_root: Path,
    unreadable: dict[str, str] | None = None,
    *,
    include_terminal_locks: bool = False,
) -> list[dict[str, Any]]:
    """Stores of removed parts that the install preserves, with sizes (MIK-R113 rule 5).

    The install never deletes these; only the explicit ``removed-stores``
    command does. Reporting them here is the once-per-install notice. ``files`` are the
    files that command removes for the store; a gate log is removed without its lock,
    which the landing chain still takes.

    ``unreadable``, when given, receives every place that is there and could not be read,
    as path and reason. A store may lie in such a place, so the command names it instead
    of reporting that nothing remains.
    """
    unreadable = {} if unreadable is None else unreadable
    found: list[dict[str, Any]] = []
    stores = [coordination_root / relative for relative in REMOVED_PART_STORE_PATHS]
    stores.extend(_pattern_paths(coordination_root, REMOVED_LIFECYCLE_STORE_PATTERNS, unreadable))
    for store in stores:
        files = _store_files(store, unreadable)
        if files:
            found.append(_store_entry(store, files, gate_log=False))
    for store in _pattern_paths(coordination_root, STORED_GATE_LOG_PATTERNS, unreadable):
        if _is_plain_file(store, unreadable):
            found.append(_store_entry(store, [store], gate_log=True))
    if include_terminal_locks:
        found.extend(_terminal_lock_stores(coordination_root, unreadable))
    return found


def _store_entry(store: Path, files: list[Path], *, gate_log: bool) -> dict[str, Any]:
    return {
        "path": store.as_posix(),
        "bytes": _files_bytes(files),
        "files": [path.as_posix() for path in files],
        "gateLog": gate_log,
    }


def removed_part_stores_report(stores: list[dict[str, Any]]) -> dict[str, Any]:
    """The part of an install's report on the stores of removed parts (MIK-R113 rule 5).

    ``stores`` names every store with its path, size and files; it is what the filed report
    keeps and the tool's answer leaves out. The count, the size and the sentence that names
    the command ride in both, and the way out of a stored gate when a gate log is among them.
    """
    report: dict[str, Any] = {
        "count": len(stores),
        "bytes": sum(item["bytes"] for item in stores),
        "notice": REMOVED_STORES_NOTICE,
        "stores": stores,
    }
    if any(item["gateLog"] for item in stores):
        report["storedGates"] = STORED_GATE_WAY_OUT
    return report
