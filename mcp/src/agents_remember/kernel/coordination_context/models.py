from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, TypedDict

from agents_remember.errors import AgentsRememberError
from agents_remember.kernel.memory_mode import MemoryMode, Topology


class MissingMemoryError(AgentsRememberError):
    """Raised when the supported durable memory location does not exist."""

    def __init__(
        self,
        code_repository_name: str,
        coordination_root: Path,
        external_memory: Path,
    ) -> None:
        self.code_repository_name = code_repository_name
        self.coordination_root = coordination_root
        self.external_memory = external_memory
        super().__init__(
            "Agents Remember memory is missing for "
            f"{code_repository_name!r}. Checked external memory at {external_memory.as_posix()} "
            f"using coordination root {coordination_root.as_posix()}. "
            "Memory setup is not a message's job to prescribe: the c-00-initialize-memory-repo "
            "skill owns creating or repairing this root, and the c-03-repo-bootstrap skill owns "
            "generating onboarding content under it."
        )


class StorageRule(TypedDict, total=False):
    path: str
    storage: str
    includes: list[str]
    excludes: list[str]
    include_file_types: list[str]
    exclude_file_types: list[str]


DEFAULT_STORAGE_MODE = "memory-repo"
"""Storage for eligible onboarding when a repository declares no override.

``internal`` memory used to default this to ``repo-sidecar``; with ``internal`` removed,
``memory-repo`` is the only derived default. ``repo-sidecar`` remains a *declarable* mode for
an individual path rule -- it says where one artifact is written, not where memory lives.
"""


@dataclass
class StorageSettings:
    mode: str = DEFAULT_STORAGE_MODE
    default: str = DEFAULT_STORAGE_MODE
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
    topology: Topology
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

    topology: Topology | None = None
    coordination_root: Path | None = None
    settings_path: Path | None = None
    onboarding_root: Path | None = None


class ContractReaderPort(Protocol):
    """The worktree contract-file surface the resolver may use."""

    def load_contract(self, path: Path) -> Any: ...
    def worktree_group_for(
        self, coordination_root: Path, code_repository_name: str, worktree_name: str
    ) -> Path: ...
    def resolve_active_task_root(
        self,
        coordination_root: Path,
        code_repository_name: str,
        task_name: str,
        *,
        parent_task: str | None = None,
    ) -> Path: ...
    def find_task_contract(
        self,
        coordination_root: Path,
        code_repository_name: str,
        task_name: str,
        *,
        parent_task: str | None = None,
        leaf_id: str | None = None,
    ) -> Path | None: ...
    def find_worktree_contract(
        self,
        coordination_root: Path,
        code_repository_name: str,
        worktree_name: str,
    ) -> Path | None: ...


@dataclass(frozen=True)
class EnclosureResolution:
    """One resolution's selector plus its bound contract reader."""

    selector: EnclosureSelector | None = None
    contract_reader: ContractReaderPort | None = None


@dataclass(frozen=True)
class CoordinationRequest:
    """Everything a coordination-context resolution needs beyond repo identity."""

    hints: CoordinationHints | None = None
    selector: EnclosureSelector | None = None
    contract_reader: ContractReaderPort | None = None


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

    topology: Topology
    coordination_root: Path
    memory_root: Path
    onboarding_root: Path
    settings_path: Path


@dataclass
class CoordinationContext:
    topology: Topology
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
    # From the worktree contract whenever one is in scope: `resolver.build_coordination_context`
    # (resolver.py line 284) reads `contract.memory_mode` straight into this field, falling back
    # to `_memory_mode(roots.topology)` only when there is no contract. So the vocabulary is the
    # contract's, and this alias is the one declaration rather than a second copy of the pair.
    memory_mode: MemoryMode
    contract_path: Path | None = None
    worktree_group: Path | None = None
    code_worktree: Path | None = None
    memory_worktree: Path | None = None
    ledger_path: Path | None = None
