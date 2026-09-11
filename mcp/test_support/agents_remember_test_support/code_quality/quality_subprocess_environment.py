"""Environment boundary between one quality wrapper and its child rails."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from pathlib import Path

# These values control the outer wrapper invocation. Once retry planning and
# report-path resolution are complete, inheriting them into pytest lets tests of
# the quality wrapper overwrite the outer run's cache or progress evidence.
OUTER_INVOCATION_ONLY = frozenset(
    {
        "AR_QUALITY_NO_RETRY",
        "AR_QUALITY_PROGRESS_REPORT",
        "AR_QUALITY_RETRY_CACHE",
        "AR_QUALITY_RETRY_CONTEXT_VARIANT",
        "AR_QUALITY_RETRY_EVIDENCE_KEY",
    }
)


def child_environment(environment: Mapping[str, str]) -> dict[str, str]:
    """Return candidate-test semantics without outer execution controls."""
    return {key: value for key, value in environment.items() if key not in OUTER_INVOCATION_ONLY}


def build(
    environment: Mapping[str, str],
    *,
    project_root: Path,
    coverage_paths: Sequence[Path],
    coverage_data: Path | None,
) -> dict[str, str]:
    """Build one child-rail environment pinned to this checkout."""
    result = child_environment(environment)
    roots = [str(root) for root in source_import_roots(project_root, coverage_paths)]
    if existing := result.get("PYTHONPATH"):
        roots.append(existing)
    result["PYTHONPATH"] = os.pathsep.join(roots)
    if coverage_data is not None:
        coverage_data.parent.mkdir(parents=True, exist_ok=True)
        result["COVERAGE_FILE"] = str(coverage_data)
    return result


def source_import_roots(project_root: Path, coverage_paths: Sequence[Path]) -> list[Path]:
    """Resolve source files and package directories to their import roots."""
    roots: list[Path] = []
    resolved_root = project_root.resolve()
    for source in coverage_paths:
        resolved = source if source.is_absolute() else project_root / source
        if resolved.is_file() and resolved.suffix == ".py":
            package_root = resolved.resolve().parent
            while (package_root / "__init__.py").is_file() and package_root not in {
                package_root.parent,
                resolved_root,
            }:
                package_root = package_root.parent
            root = resolved_root if (package_root / "__init__.py").is_file() else package_root
        else:
            root = resolved.resolve().parent
        if root not in roots:
            roots.append(root)
    return roots
