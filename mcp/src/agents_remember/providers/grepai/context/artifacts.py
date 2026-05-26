"""GrepAI indexed-root artifact cleanup helpers."""

from __future__ import annotations

from pathlib import Path

from agents_remember.providers.context.common import (
    ContextProviderError,
    remove_runtime_path,
)
from agents_remember.providers.grepai.context.constants import GREPAI_ROOT_ARTIFACT_NAMES
from agents_remember.providers.grepai.context.layout import GrepaiMemoryRoot


def grepai_root_provider_artifacts(root: Path) -> list[Path]:
    """Return GrepAI runtime artifacts that should not exist in indexed roots."""

    resolved = root.resolve()
    return [resolved / name for name in GREPAI_ROOT_ARTIFACT_NAMES if (resolved / name).exists()]


def assert_no_grepai_root_provider_artifacts(roots: tuple[GrepaiMemoryRoot, ...]) -> None:
    artifacts: list[Path] = []
    for root in roots:
        artifacts.extend(grepai_root_provider_artifacts(root.source_path or root.path))
    if artifacts:
        rendered = ", ".join(path.as_posix() for path in artifacts)
        raise ContextProviderError(f"grepai provider artifacts found in indexed roots: {rendered}")


def remove_grepai_root_provider_artifacts(
    roots: tuple[GrepaiMemoryRoot, ...],
    *,
    dry_run: bool = False,
) -> list[dict[str, str]]:
    """Remove disposable GrepAI artifacts from indexed roots after direct-child validation."""

    removals: list[dict[str, str]] = []
    for root in roots:
        removals.extend(_remove_grepai_root_artifacts(root, dry_run=dry_run))
    return removals


def _remove_grepai_root_artifacts(
    root: GrepaiMemoryRoot,
    *,
    dry_run: bool,
) -> list[dict[str, str]]:
    root_path = (root.source_path or root.path).resolve()
    removals: list[dict[str, str]] = []
    for artifact in grepai_root_provider_artifacts(root_path):
        _assert_grepai_root_artifact(root_path, artifact)
        removals.append({"projectId": root.project_id, "path": artifact.as_posix()})
        remove_runtime_path(artifact, dry_run=dry_run)
    return removals


def _assert_grepai_root_artifact(root_path: Path, artifact: Path) -> None:
    if artifact.name not in GREPAI_ROOT_ARTIFACT_NAMES or artifact.resolve().parent != root_path:
        raise ContextProviderError(
            f"refusing to remove unexpected GrepAI artifact path: {artifact.as_posix()}"
        )
