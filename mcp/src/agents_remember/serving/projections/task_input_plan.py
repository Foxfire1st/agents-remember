"""One task-reader input plan for native registration and closeout invalidation."""

from __future__ import annotations

import os
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from agents_remember.controlplane.closeout_queue_store import queue_store_paths
from agents_remember.controlplane.held_store_discovery import (
    RECEIPT_WALK_DEPTH,
    SKIPPED_RECEIPT_DIRS,
    receipt_holder_chain,
    receipt_steps,
    task_receipt_steps,
)
from agents_remember.kernel.primitives.observer_paths import LANDING_FINAL_BASENAME
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.serving.projections.series_input_plan import (
    MasterSeriesInput,
    series_input_plan,
)
from agents_remember.serving.projections.snapshots_impl._common import (
    task_directories,
    walked_directories,
)
from agents_remember.tasks.task_paths import (
    ARCHIVE_DIR,
    ENCLOSURES_DIR,
    SERIES_CONTRACT_FILENAME,
    TASK_DOCUMENT_FILENAME,
    iter_enclosure_folders,
    task_folders_below,
)

DirectoryRole = Literal[
    "task-root",
    "repository",
    "task-folder",
    "contract-discovery",
    "contract-folder",
    "enclosures",
    "enclosure",
    "queue-artifacts",
    "receipt-discovery",
    "receipt-holder",
    "expected-parent",
    "series-ref",
    "series-discovery",
]


@dataclass(frozen=True)
class TaskInputDirectory:
    path: Path
    roles: frozenset[DirectoryRole]
    expected_children: frozenset[str] = frozenset()
    input_names: frozenset[str] = frozenset()
    receipt_depth: int | None = None


@dataclass(frozen=True)
class TaskInputPlan:
    directories: tuple[TaskInputDirectory, ...]
    errors: tuple[str, ...] = ()
    series_inputs: tuple[MasterSeriesInput, ...] = ()
    _by_path: Mapping[Path, TaskInputDirectory] = field(init=False, compare=False, repr=False)
    _series_by_path: Mapping[Path, frozenset[tuple[str, str]]] = field(
        init=False, compare=False, repr=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "_by_path", MappingProxyType({d.path: d for d in self.directories})
        )

        owners: dict[Path, set[tuple[str, str]]] = {}
        for item in self.series_inputs:
            for path in item.ref_paths:
                owners.setdefault(path, set()).add((item.repo_id, item.master))
        object.__setattr__(
            self,
            "_series_by_path",
            MappingProxyType({path: frozenset(value) for path, value in owners.items()}),
        )

    @property
    def series_masters(self) -> tuple[tuple[str, str], ...]:
        return tuple((item.repo_id, item.master) for item in self.series_inputs)

    def series_for_paths(self, paths: Iterable[Path | str]) -> frozenset[tuple[str, str]]:
        return frozenset(
            owner for path in paths for owner in self._series_by_path.get(Path(path), ())
        )

    def series_event(self, path: Path) -> bool:
        """Only exact ref files and their planned discovery directories bypass .git filtering."""
        if path in self._series_by_path:
            return True
        node = self._by_path.get(path)
        if node is not None and node.roles & {"series-ref", "series-discovery"}:
            return True
        parent = self._by_path.get(path.parent)
        return bool(
            parent is not None
            and "series-discovery" in parent.roles
            and path.name in parent.expected_children
        )

    @property
    def complete(self) -> bool:
        return not self.errors

    @property
    def shallow_dirs(self) -> tuple[Path, ...]:
        return tuple(d.path for d in self.directories)

    def _ordinary_file(self, path: Path, node: TaskInputDirectory) -> bool:
        roles, name = node.roles, path.name
        return (
            ("task-folder" in roles and name.endswith(".json"))
            or ("contract-folder" in roles and name == SERIES_CONTRACT_FILENAME)
            or ("enclosure" in roles and name in {SERIES_CONTRACT_FILENAME, LANDING_FINAL_BASENAME})
            or ("queue-artifacts" in roles and name in node.input_names)
            or ("series-ref" in roles and name in node.input_names)
        )

    def accepts_file(self, path: Path) -> bool:
        node = self._by_path.get(path.parent)
        return node is not None and (
            self._ordinary_file(path, node)
            or ("receipt-holder" in node.roles and path.name.endswith(".json"))
        )

    def topology_candidate(self, path: Path) -> bool:
        if path in self._by_path:
            return True
        node = self._by_path.get(path.parent)
        if node is None:
            return False
        return (
            path.name in node.expected_children
            or (
                "contract-discovery" in node.roles
                and path.name
                in {
                    TASK_DOCUMENT_FILENAME,
                    SERIES_CONTRACT_FILENAME,
                }
            )
            or (
                bool(node.roles & {"task-root", "repository", "contract-discovery"})
                and path.name != ARCHIVE_DIR
            )
            or "enclosures" in node.roles
            or (
                node.receipt_depth is not None
                and node.receipt_depth < RECEIPT_WALK_DEPTH
                and path.name not in SKIPPED_RECEIPT_DIRS
            )
        )

    def topology_change(self, path: Path, *, is_directory: bool) -> bool:
        if path in self._by_path:
            return True  # a removed/renamed known directory no longer passes is_dir()
        node = self._by_path.get(path.parent)
        if node is None:
            return False
        recognition = "contract-discovery" in node.roles and path.name in {
            TASK_DOCUMENT_FILENAME,
            SERIES_CONTRACT_FILENAME,
        }
        return recognition or (is_directory and self.topology_candidate(path))

    def input_files(self) -> Iterator[Path]:
        if not self.complete:
            raise OSError("task input plan is incomplete: " + "; ".join(self.errors))
        for node in self.directories:
            if not node.roles & {
                "task-folder",
                "contract-folder",
                "enclosure",
                "queue-artifacts",
                "receipt-holder",
            }:
                continue
            with os.scandir(node.path) as entries:
                for entry in entries:
                    path = Path(entry.path)
                    if not self.accepts_file(path):
                        continue
                    # The receipt reader recurses into directories, even *.json directories.
                    # Canonical task/contract candidates intentionally retain unreadable types.
                    if not self._ordinary_file(path, node) and entry.is_dir(follow_symlinks=False):
                        continue
                    yield path


class _Builder:
    def __init__(self) -> None:
        self.nodes: dict[Path, TaskInputDirectory] = {}
        self.errors: set[str] = set()

    def error(self, error: OSError) -> None:
        self.errors.add(str(error))

    def add(
        self,
        path: Path,
        role: DirectoryRole,
        *,
        expected: frozenset[str] = frozenset(),
        receipt_depth: int | None = None,
        inputs: frozenset[str] = frozenset(),
    ) -> None:
        try:
            if not path.is_dir():
                return
            if path not in self.nodes:
                # Fail a discovery whose directory cannot be opened; do not call it empty.
                with os.scandir(path):
                    pass
        except OSError as error:
            self.error(error)
            return
        old = self.nodes.get(path)
        roles: frozenset[DirectoryRole] = frozenset({role})
        if old is not None:
            roles |= old.roles
        self.nodes[path] = TaskInputDirectory(
            path,
            roles,
            expected | (old.expected_children if old else frozenset()),
            inputs | (old.input_names if old else frozenset()),
            receipt_depth if receipt_depth is not None else old.receipt_depth if old else None,
        )

    def chain(self, paths: tuple[Path, ...]) -> None:
        for index, path in enumerate(paths):
            expected = frozenset({paths[index + 1].name}) if index + 1 < len(paths) else frozenset()
            self.add(path, "expected-parent", expected=expected)

    def canonical(self, tasks_root: Path) -> None:
        for repository in walked_directories(tasks_root, on_error=self.error):
            if ARCHIVE_DIR not in repository.parts:
                self.add(repository, "repository")
        for folder in task_directories(tasks_root, on_error=self.error):
            if ARCHIVE_DIR in folder.parts or ENCLOSURES_DIR in folder.parts:
                continue
            self.add(folder, "task-folder")
            ref = TaskDocumentRef(
                repository=folder.parent.name,
                path=f"{folder.name}/{TASK_DOCUMENT_FILENAME}",
            )
            state, _build = queue_store_paths(tasks_root.parent, ref)
            self.add(folder, "expected-parent", expected=frozenset({state.parent.name}))
            self.add(state.parent, "queue-artifacts", inputs=frozenset({state.name}))

    def contracts(self, tasks_root: Path) -> None:
        def contract_directory(path: Path) -> None:
            if ARCHIVE_DIR not in path.parts:
                self.add(path, "contract-discovery")

        for folder in task_folders_below(
            tasks_root, on_directory=contract_directory, on_error=self.error
        ):
            if ARCHIVE_DIR in folder.parts:
                continue
            self.add(folder, "contract-folder", expected=frozenset({ENCLOSURES_DIR}))
            enclosures = folder / ENCLOSURES_DIR
            self.add(folder, "expected-parent", expected=frozenset({ENCLOSURES_DIR}))
            self.add(enclosures, "enclosures")
            for leaf in iter_enclosure_folders(folder, on_error=self.error):
                self.add(leaf, "enclosure")

    def receipts(self, tasks_root: Path) -> None:
        def receipt_directory(path: Path, depth: int) -> None:
            if depth == 2:
                self.add(path, "receipt-discovery")
                self.chain(receipt_holder_chain(path))
            else:
                self.add(path, "task-root" if depth == 0 else "repository")

        holders = [
            Path(path)
            for path in task_receipt_steps(
                tasks_root,
                on_directory=receipt_directory,
                on_error=self.error,
            )
            if path
        ]
        for holder in holders:
            walk = receipt_steps(
                holder,
                on_directory=lambda p, d: self.add(p, "receipt-holder", receipt_depth=d),
                on_error=self.error,
            )
            while True:
                try:
                    next(walk)
                except StopIteration as stop:
                    if stop.value is False:
                        self.errors.add(f"{holder}: receipt discovery exceeded its depth")
                    break

    def series(self) -> tuple[MasterSeriesInput, ...]:
        folders = tuple(node.path for node in self.nodes.values() if "task-folder" in node.roles)
        inputs = series_input_plan(folders)
        for item in inputs:
            for path in item.ref_paths:
                parent = path.parent
                missing: list[Path] = []
                while not parent.is_dir() and parent != parent.parent:
                    missing.append(parent)
                    parent = parent.parent
                chain = (parent, *reversed(missing))
                for index, directory in enumerate(chain):
                    expected = (
                        frozenset({chain[index + 1].name})
                        if index + 1 < len(chain)
                        else frozenset()
                    )
                    self.add(directory, "series-discovery", expected=expected)
                self.add(path.parent, "series-ref", inputs=frozenset({path.name}))
        return inputs


def task_input_plan(tasks_root: Path, *, include_series_refs: bool = False) -> TaskInputPlan:
    """One exact task-input plan. Call off-loop; incomplete discovery is explicit.

    Receipt holders follow canonical writer places (D561); content traversal
    preserves the reader's depth and skipped-directory rules.
    """
    builder = _Builder()
    parent = tasks_root
    missing: list[Path] = []
    while not parent.is_dir() and parent != parent.parent:
        missing.append(parent)
        parent = parent.parent
    builder.chain((parent, *reversed(missing)))
    builder.add(tasks_root, "task-root")
    try:
        root_info = tasks_root.stat()
    except FileNotFoundError:
        root_info = None  # The watched parent discovers the task tree when it appears.
    except OSError as error:
        builder.error(error)
        root_info = None
    if root_info is not None:
        try:
            builder.canonical(tasks_root)
            builder.contracts(tasks_root)
            builder.receipts(tasks_root)
        except OSError as error:
            builder.error(error)
    series: tuple[MasterSeriesInput, ...] = ()
    if include_series_refs:
        try:
            series = builder.series()
        except (OSError, RuntimeError) as error:
            builder.errors.add(str(error))
    return TaskInputPlan(
        tuple(builder.nodes[path] for path in sorted(builder.nodes)),
        tuple(sorted(builder.errors)),
        series,
    )
