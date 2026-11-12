"""Capture the bytes a census reads, so its receipt identifies its actual inputs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents_remember.application.census_artifacts import (
    CensusInputs,
    directory_digest,
    directory_files,
    pair_identities,
    tree_identity,
)
from agents_remember.memory_quality import reference_anchors
from agents_remember.memory_quality.knowledge_validator.trees import (
    CodePathSet,
    KnowledgeTree,
    KnowledgeTreeReadError,
    code_tree_from_git,
    is_knowledge_path,
    knowledge_tree_from_git,
)


def _capture(root: Path) -> tuple[dict[str, Any], dict[str, bytes] | None]:
    """Git inputs are immutable objects; plain directories are captured byte maps."""

    identity = tree_identity(root)
    if identity["kind"] == "git":
        if not identity["worktreeTree"]:
            raise KnowledgeTreeReadError(f"cannot capture the census input at {root}")
        return identity, None
    if identity["kind"] == "unreadable":
        raise KnowledgeTreeReadError(f"cannot read the census input at {root}")
    files = directory_files(root)
    if identity["kind"] != "absent":
        identity = {"kind": "directory", "contentSha256": directory_digest(files)}
    return identity, files


def measured_census(memory_root: Path, code_root: Path) -> tuple[dict[str, Any], CensusInputs]:
    """Enumerate captured inputs, never the mutable paths whose identities were sampled.

    A Git working-tree tree is read back through its object ID. Directory identities are hashed
    from the same captured bytes the parser and resolver consume. Live endpoint equality remains
    diagnostic only: it cannot establish what a census read, or detect a change later reverted.
    """

    memory_id, memory_files = _capture(memory_root)
    code_id, code_files = _capture(code_root)
    memory = (
        knowledge_tree_from_git(memory_root, memory_id["worktreeTree"])
        if memory_files is None
        else KnowledgeTree(
            label=memory_root.as_posix(),
            files={path: data for path, data in memory_files.items() if is_knowledge_path(path)},
        )
    )
    code = (
        code_tree_from_git(code_root, code_id["worktreeTree"])
        if code_files is None
        else CodePathSet(
            label=code_root.as_posix(), paths=frozenset(code_files), contents=code_files
        )
    )
    identity = {"code": code_id, "memory": memory_id}
    full = reference_anchors.census(memory, code)
    full["inputIdentities"] = identity
    return full, CensusInputs(
        identity=identity, stable=pair_identities(memory_root, code_root) == identity
    )
