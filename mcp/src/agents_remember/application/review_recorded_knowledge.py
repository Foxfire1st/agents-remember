"""Read the knowledge blobs at a closed leaf's exact recorded memory endpoints.

This is a request-owned materialization, not a published comparison generation. Git remains
the authority; the private files exist only while the resolution holds them. No current memory
worktree, branch tip, or other task's retained generation participates in this read.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from weakref import finalize

from agents_remember.application.knowledge_before_half import read_dataset_identity
from agents_remember.application.published_intent import PUBLISHED_DATASET_NAME
from agents_remember.application.review_comparison_generation import KnowledgeSide
from agents_remember.application.review_comparison_reopen import ComparisonKnowledgeChannel
from agents_remember.kernel.git_command import read_git_blob_bytes, run_git
from agents_remember.kernel.git_preparation import GitPreparationError
from agents_remember.serving.changeset_endpoints import (
    RecordedEndpointAbsent,
    recorded_committed_range,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract


@dataclass(frozen=True)
class RecordedKnowledge:
    """Two measured endpoints and their private read files, reclaimed with this resolution."""

    workspace: TemporaryDirectory[str]
    before: ComparisonKnowledgeChannel
    after: ComparisonKnowledgeChannel

    def __post_init__(self) -> None:
        finalize(self, self.workspace.cleanup)

    def channel(self, side: KnowledgeSide) -> ComparisonKnowledgeChannel:
        return self.before if side == "before" else self.after

    def database(self, side: KnowledgeSide) -> Path:
        return Path(self.workspace.name) / side / PUBLISHED_DATASET_NAME

    def namespace(self, default: str) -> str:
        for channel in (self.after, self.before):
            if channel.identity is not None:
                return channel.identity.repository_id
        return default


def read_recorded_knowledge(contract: WorktreeContract) -> RecordedKnowledge:
    """Resolve each recorded commit through the ordinary committed-range and dataset readers.

    At most two files are held per live resolution; TemporaryDirectory owns their cleanup when
    the resolution leaves the composition. The names never enter comparison or cursor identity.
    """

    workspace = TemporaryDirectory(prefix="ar-review-history-")
    try:
        recorded = recorded_committed_range(contract, memory=True)
    except RecordedEndpointAbsent as absent:
        state = "not-recorded" if absent.kind in {"not-recorded", "no-repository"} else "missing"
        return RecordedKnowledge(
            workspace,
            ComparisonKnowledgeChannel("before", state, None, None, str(absent)),
            ComparisonKnowledgeChannel("after", state, None, None, str(absent)),
        )
    root = Path(workspace.name)
    before = read_memory_knowledge(recorded.repository, recorded.base_commit, "before", root)
    after = read_memory_knowledge(recorded.repository, recorded.head_commit, "after", root)
    if (
        before.identity is not None
        and after.identity is not None
        and before.identity.repository_id != after.identity.repository_id
    ):
        after = replace(
            after,
            state="corrupt",
            detail="the recorded memory endpoints contain different repository namespaces",
        )
    return RecordedKnowledge(workspace, before, after)


def read_memory_knowledge(
    repository: Path, commit: str, side: KnowledgeSide, workspace: Path
) -> ComparisonKnowledgeChannel:
    endpoint = f"{commit}:{PUBLISHED_DATASET_NAME}"
    lookup = run_git(repository, ["ls-tree", commit, "--", PUBLISHED_DATASET_NAME])
    if lookup.returncode != 0:
        return ComparisonKnowledgeChannel(
            side,
            "missing",
            None,
            None,
            f"the recorded memory endpoint {endpoint} could not be read",
        )
    if not lookup.stdout.strip():
        return ComparisonKnowledgeChannel(
            side, "not-recorded", None, None, f"the recorded memory commit has no {endpoint}"
        )
    mode, kind, blob, *_ = lookup.stdout.split()
    if kind != "blob" or mode not in {"100644", "100755"}:
        return ComparisonKnowledgeChannel(
            side, "corrupt", None, None, f"the recorded endpoint {endpoint} is not a regular file"
        )
    database = workspace / side / PUBLISHED_DATASET_NAME
    try:
        database.parent.mkdir()
        database.write_bytes(read_git_blob_bytes(repository, blob))
        identity = read_dataset_identity(database)
    except (GitPreparationError, OSError) as error:
        return ComparisonKnowledgeChannel(
            side, "missing", None, None, f"the recorded endpoint {endpoint} is unreadable: {error}"
        )
    if isinstance(identity, str):
        return ComparisonKnowledgeChannel(
            side,
            "corrupt",
            None,
            database,
            f"the recorded endpoint {endpoint} is not readable: {identity}",
        )
    return ComparisonKnowledgeChannel(
        side,
        "available",
        identity,
        database,
        f"reconstructed from exact recorded memory endpoint {endpoint} (blob {blob}); "
        "no frozen review generation was recorded",
    )
