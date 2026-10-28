"""Resumable, entry-budgeted discovery of native receipts and held stores."""

from __future__ import annotations

import heapq
import os
from collections.abc import Callable, Generator, Iterator
from contextlib import suppress
from pathlib import Path
from typing import NamedTuple

from agents_remember.kernel.coordination_context.task_folders import canonical_task_folder_steps

# Where this line keeps its receipts, under a task's or the coordination root's notes/reports.
EXECUTIONS_DIRECTORY = "paseo-native-executions"

ENUMERATION_BUDGET = 256
MAX_HELD_FOLDER_WALKS = 64
MAX_HELD_FOLDER_STORES = 64
SKIPPED_RECEIPT_DIRS = frozenset({"history", "message-bindings"})
# D1-D4: arbitrary nested directories under a receipt root are not retained without limit.
# Beyond 16 open levels the branch is shed and the observation is explicitly incomplete.
RECEIPT_WALK_DEPTH = 16


def receipt_holder(root: Path, directory_name: str = EXECUTIONS_DIRECTORY) -> Path:
    """The holder checked by both receipt discovery and its input watchers."""
    return root / "notes" / "reports" / directory_name


def receipt_holder_chain(root: Path) -> tuple[Path, ...]:
    """Discovery parents, including absent candidates, from root through its holder."""
    chain = [root]
    for part in receipt_holder(root).relative_to(root).parts:
        chain.append(chain[-1] / part)
    return tuple(chain)


class DirectoryWalk:
    """Keep one scan open; examine at most the allowance, with no lookahead entry."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.cursor: str | None = None
        self.done = False
        self.examined = 0
        self._scan: Iterator[os.DirEntry[str]] | None = None

    def advance(self, budget: int, visit: Callable[[os.DirEntry[str]], None]) -> bool:
        self.examined = 0
        if self.done or budget <= 0:
            return self.done
        if self._scan is None:
            self._scan = os.scandir(self.directory)
        while self.examined < budget:
            try:
                entry = next(self._scan)
            except StopIteration:
                self.close()
                break
            self.examined += 1
            self.cursor = entry.name
            visit(entry)
        return self.done

    def close(self) -> None:
        self.done = True
        scan, self._scan = self._scan, None
        close = getattr(scan, "close", None)
        if callable(close):
            close()

    def __del__(self) -> None:
        self.close()


def file_stamp(path: Path) -> tuple[int, int, int] | None:
    with suppress(OSError):
        info = path.stat()
        return info.st_size, info.st_mtime_ns, info.st_ino
    return None


def receipt_steps(
    root: Path,
    depth: int = 0,
    *,
    on_directory: Callable[[Path, int], None] | None = None,
    on_error: Callable[[OSError], None] | None = None,
) -> Generator[str | None, None, bool]:
    """Every entry, including directories/non-JSON files, consumes a traversal step."""
    if depth >= RECEIPT_WALK_DEPTH:
        yield None
        return False
    if on_directory is not None:
        on_directory(root, depth)
    complete = True
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                try:
                    directory = entry.is_dir(follow_symlinks=False)
                except OSError as error:
                    if on_error is not None:
                        on_error(error)
                    yield None
                    continue
                if directory:
                    yield None
                    if entry.name not in SKIPPED_RECEIPT_DIRS:
                        complete = (
                            yield from receipt_steps(
                                Path(entry.path),
                                depth + 1,
                                on_directory=on_directory,
                                on_error=on_error,
                            )
                        ) and complete
                else:
                    yield entry.path if entry.name.endswith(".json") else None
    except FileNotFoundError:
        return complete
    return complete


def task_receipt_steps(
    tasks_root: Path,
    *,
    on_directory: Callable[[Path, int], None] | None = None,
    on_error: Callable[[OSError], None] | None = None,
) -> Generator[str | None]:
    """Only the live canonical task holders used by the receipt writer (D561)."""
    for folder in canonical_task_folder_steps(
        tasks_root,
        on_directory=on_directory,
        on_error=on_error,
    ):
        if folder is None:
            yield None
        else:
            holder = receipt_holder(folder)
            yield holder.as_posix() if holder.is_dir() else None


class _StepWalk:
    def __init__(self, steps: Generator[str | None, None, bool | None]) -> None:
        self._steps = steps
        self.done = False
        self.complete = True
        self.examined = 0

    def take(self, budget: int) -> list[str]:
        """Bound traversal work, including paths that are not candidate receipts."""

        return self._take(budget, budget)

    def _take(self, entry_budget: int, path_budget: int) -> list[str]:

        paths: list[str] = []
        self.examined = 0
        while self.examined < entry_budget and len(paths) < path_budget and not self.done:
            try:
                path = next(self._steps)
            except StopIteration as error:
                if error.value is False:
                    self.complete = False
                self.close()
                break
            except OSError:
                self.complete = False
                self.close()
                break
            self.examined += 1
            if path is not None:
                paths.append(path)
        return paths

    def close(self) -> None:
        self.done = True
        self._steps.close()


class ReceiptWalk(_StepWalk):
    def __init__(self, root: Path) -> None:
        self.root = root
        super().__init__(receipt_steps(root))

    def take(self, budget: int, *, entry_budget: int = ENUMERATION_BUDGET) -> list[str]:
        """At most ``budget`` receipts and ``entry_budget`` traversal steps.

        Delivery's read allowance counts receipts, so a skipped history directory consumes
        traversal work without stealing a recipient's read slot. Attention supplies its shared
        remaining entry allowance explicitly.
        """

        return self._take(entry_budget, budget)


class ReceiptRootWalk(_StepWalk):
    def __init__(self, root: Path) -> None:
        def steps() -> Generator[str | None]:
            top = receipt_holder(root)
            yield top.as_posix() if top.is_dir() else None
            yield from task_receipt_steps(root / "tasks")

        super().__init__(steps())


def receipt_roots(coordination_root: str | Path) -> list[Path]:
    """The delivery sweep's synchronous roots; attention carries its own resumable walk."""

    walk = ReceiptRootWalk(Path(coordination_root))
    found: list[Path] = []
    while not walk.done:
        found.extend(Path(path) for path in walk.take(ENUMERATION_BUDGET))
    return sorted(found)


class _HeldFolderWalk:
    def __init__(self, stamp: tuple[int, int, int], limit: int) -> None:
        self.stamp, self.limit = stamp, limit
        self.met = 0
        self._walk: DirectoryWalk | None = None
        self._kept: list[tuple[int, str, str]] = []

    @property
    def done(self) -> bool:
        return self._walk is not None and self._walk.done

    @property
    def examined(self) -> int:
        return 0 if self._walk is None else self._walk.examined

    def advance(self, directory: Path, budget: int) -> int:
        if self._walk is None:
            self._walk = DirectoryWalk(directory)
        self._walk.advance(budget, self._visit)
        return self._walk.examined

    def _visit(self, entry: os.DirEntry[str]) -> None:
        if not entry.name.endswith(".json"):
            return
        self.met += 1
        info = entry.stat()
        item = (info.st_mtime_ns, entry.name, entry.path)
        if len(self._kept) < self.limit:
            heapq.heappush(self._kept, item)
        elif item > self._kept[0]:
            heapq.heapreplace(self._kept, item)

    def paths(self) -> tuple[Path, ...]:
        return tuple(Path(path) for _, _, path in sorted(self._kept, reverse=True))

    def close(self) -> None:
        if self._walk is not None:
            self._walk.close()


class FolderListing(NamedTuple):
    paths: tuple[Path, ...]
    examined: int
    done: bool
    whole: bool


class HeldFolderDiscovery:
    """At most 64 open/cached folders; evicting one closes its scan."""

    def __init__(self) -> None:
        self._walks: dict[str, _HeldFolderWalk] = {}

    def reset(self) -> None:
        while self._walks:
            self._drop(next(iter(self._walks)))

    def _drop(self, key: str) -> None:
        walk = self._walks.pop(key, None)
        if walk is not None:
            walk.close()

    def read(
        self,
        directory: Path,
        limit: int = MAX_HELD_FOLDER_STORES,
        *,
        budget: int = ENUMERATION_BUDGET,
    ) -> FolderListing:
        key = directory.as_posix()
        stamp = file_stamp(directory)
        if stamp is None:
            self._drop(key)
            try:
                directory.stat()
            except FileNotFoundError:
                return FolderListing((), 0, True, True)
            except OSError:
                pass
            return FolderListing((), 0, True, False)
        state = self._walks.get(key)
        if state is not None and (state.stamp != stamp or state.limit != limit):
            self._drop(key)
            state = None
        if state is None:
            state = self._walks[key] = _HeldFolderWalk(stamp, limit)
            while len(self._walks) > MAX_HELD_FOLDER_WALKS:
                self._drop(next(iter(self._walks)))
        try:
            scanned = 0 if state.done else state.advance(directory, budget)
        except OSError:
            scanned = state.examined
            self._drop(key)
            return FolderListing((), scanned, True, False)
        return FolderListing(state.paths(), scanned, state.done, state.done and state.met <= limit)
