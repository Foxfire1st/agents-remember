from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, TypedDict


class MissingMemoryError(ValueError):
    """Raised when neither supported durable memory location exists."""

    def __init__(
        self,
        code_repository_name: str,
        internal_root: Path,
        coordination_root: Path,
        external_memory: Path,
    ) -> None:
        self.code_repository_name = code_repository_name
        self.internal_root = internal_root
        self.coordination_root = coordination_root
        self.external_memory = external_memory
        super().__init__(
            "Agents Remember memory is missing for "
            f"{code_repository_name!r}. Checked internal memory at {internal_root.as_posix()} "
            f"and external memory at {external_memory.as_posix()} using coordination root {coordination_root.as_posix()}. "
            "Ask the developer whether to initialize memory with C-00-initialize-memory-repo, then run C-03 if they want onboarding content generated."
        )


class StorageRule(TypedDict, total=False):
    path: str
    storage: str
    includes: list[str]
    excludes: list[str]
    include_file_types: list[str]
    exclude_file_types: list[str]


@dataclass
class StorageSettings:
    mode: str = "repo-sidecar"
    default: str = "repo-sidecar"
    path_rules: list[StorageRule] = field(default_factory=list)


@dataclass
class CrossRepoAllowEntry:
    repo: str
    expected_branch: str
    include_code: bool = True
    include_memory: bool = False
    state: str = ""
    reason: str = ""
    code: dict[str, str] = field(default_factory=dict)
    memory: dict[str, str] = field(default_factory=dict)


@dataclass
class CrossRepoSettings:
    allow: list[CrossRepoAllowEntry] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class CoordinationSelection:
    topology: Literal["internal", "external"]
    coordination_root: Path
    memory_root: Path
    settings_path: Path


@dataclass
class CoordinationContext:
    topology: Literal["internal", "external"]
    code_repository_name: str
    code_repository_root: Path
    coordination_root: Path
    memory_root: Path
    onboarding_root: Path
    settings_path: Path
    path_settings_path: Path | None
    task_root: Path
    temp_root: Path
    docs_root: Path
    system_root: Path
    sources_path: Path
    tools_path: Path
    storage: StorageSettings
    path_rules: list[StorageRule]
    cross_repo: CrossRepoSettings
    memory_mode: Literal["internal", "external", "disabled"]
    contract_path: Path | None = None
    worktree_group: Path | None = None
    code_worktree: Path | None = None
    memory_worktree: Path | None = None
    ledger_path: Path | None = None
