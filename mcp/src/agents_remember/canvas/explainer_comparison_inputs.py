"""Inputs and the native currentness witness for one explainer comparison."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from agents_remember.canvas.explainer_state import PlannedIdentity
from agents_remember.canvas.explainer_workspace import EnvironmentRef
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.knowledge.review_frame import FrameBinding


class ComparisonWitness(Protocol):
    """The producer re-observes the exact inputs that selected this comparison."""

    def current(self) -> bool: ...


@dataclass(frozen=True)
class LeafScope:
    """Which leaf: its repository, its task folder and its leaf id, never a path."""

    repository_id: str
    task: str
    leaf_id: str

    def environment(self, config: McpRuntimeConfig) -> EnvironmentRef:
        return EnvironmentRef(
            config=config,
            repository_id=self.repository_id,
            task=self.task,
            leaf_id=self.leaf_id,
        )


@dataclass(frozen=True)
class LeafFacts:
    """What the task document and the master's records say about the leaf (rules 1, 4)."""

    #: The leaf document's stage, such as ``planning`` or ``inProgress``.
    status: str
    #: Whether the master is retired or archived, so a build is refused as ``task-archived``.
    archived: bool = False
    #: Where the archive files its receipt, named from the task root alone (never read here).
    archive_receipt: Path | None = None
    #: ``intentReview.agreedRevision.sha256`` when the leaf has one (MIK-R189 rule 9).
    agreed_revision: str | None = None

    @property
    def planned(self) -> bool:
        """Whether the leaf has no work yet, so its binding is the planned comparison."""

        return self.status == "planning"


@dataclass(frozen=True)
class BoundComparison:
    """The comparison an environment binds, with the identity the answers carry (rule 4)."""

    binding: FrameBinding
    planned: PlannedIdentity | None = None
    record_digest: str | None = None
    #: The record the binding was read from, when the owner answered one: the Update answer counts
    #: the blocks between it and the binding it replaces (rule 6). A planned binding has none.
    record: Any = None
    witness: ComparisonWitness | None = None

    def identity(self) -> str:
        """The label every answer names: ``#7``, or ``planned``, never ``#0`` for planned."""

        if self.binding.comparison_owner == "planned":
            return "planned"
        return f"#{self.binding.comparison_number}"


@dataclass(frozen=True)
class PreparedPacket:
    """One admitted packet for the bound comparison, and the digest of the bytes written."""

    packet: Any
    digest: str
