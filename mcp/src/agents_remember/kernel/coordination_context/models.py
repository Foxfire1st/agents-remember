from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, TypedDict

from agents_remember.errors import AgentsRememberError


class MissingMemoryError(AgentsRememberError):
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
            "Ask the developer whether to initialize memory with c-00-initialize-memory-repo, then run c-03-repo-bootstrap if they want onboarding content generated."
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


@dataclass(frozen=True)
class EnclosureSelector:
    """How a caller names the enclosure to resolve.

    Either directly, by ``contract_path``, or indirectly: a task (with ``parent_task`` to
    disambiguate a repeated task name) plus the leaf id or worktree name that picks one
    enclosure inside it. Resolution tries these in a fixed order, so a caller that supplies
    a subset is still supplying one selector -- the whole set travels from the tool boundary
    down to :func:`resolve_contract` unchanged.
    """

    contract_path: Path | None = None
    task_name: str | None = None
    parent_task: str | None = None
    leaf_id: str | None = None
    worktree_name: str | None = None


@dataclass(frozen=True)
class CoordinationHints:
    """What a caller already knows about where the coordination tree is.

    Every field is a hint, not a fact: a requested topology overrides detection, an explicit
    coordination root or settings file short-circuits the search, and an onboarding root
    selects the from-onboarding resolution path entirely. Detection fills in whatever is
    absent.
    """

    topology: Literal["internal", "external"] | None = None
    coordination_root: Path | None = None
    settings_path: Path | None = None
    onboarding_root: Path | None = None


@dataclass(frozen=True)
class CodeRepository:
    """A resolved code repository: its name, its root on disk, and the workspace holding it."""

    name: str
    root: Path
    workspace: Path


@dataclass(frozen=True)
class CoordinationRoots:
    """The coordination tree after resolution: which topology won, and the four roots that
    topology implies. Detection produces them together and no reader wants a subset."""

    topology: Literal["internal", "external"]
    coordination_root: Path
    memory_root: Path
    onboarding_root: Path
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
