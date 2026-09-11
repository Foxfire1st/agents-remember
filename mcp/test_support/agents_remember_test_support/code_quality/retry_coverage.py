"""Coverage artifact ownership for dependency-aware quality retries."""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

from agents_remember.kernel.atomic_write import atomic_replace
from coverage import Coverage, CoverageData
from coverage.exceptions import CoverageException

CACHED_CONTEXT = "agents-remember:unchanged-test-proof"


def validate_context_proof(path: Path) -> None:
    """Require branch data with test contexts before a proof can be reused."""
    data = _read_data(path)
    if not data.has_arcs():
        raise RuntimeError("branch coverage data is missing")
    if not any(context for context in data.measured_contexts()):
        raise RuntimeError("pytest test contexts are missing")


def retain_unchanged_contexts(
    source: Path,
    destination: Path,
    changed: Sequence[Path],
) -> bool:
    """Write only coverage owned by test contexts outside ``changed``.

    The empty collection/import context is deliberately dropped. The retained
    database is a conservative subset and is kept separate from the delta run so
    pytest-cov and xdist cannot replace it while they publish fresh worker data.
    """
    prior = _read_data(source)
    if not prior.has_arcs():
        raise RuntimeError("cached Coverage.py data does not contain branch arcs")
    prefixes = "|".join(re.escape(path.as_posix()) for path in changed)
    prior.set_query_contexts([rf"^(?!(?:{prefixes})::).+$"])
    arcs = {
        filename: measured
        for filename in sorted(prior.measured_files())
        if (measured := prior.arcs(filename))
    }
    destination.unlink(missing_ok=True)
    if not arcs:
        return False
    retained = CoverageData(basename=str(destination))
    retained.set_context(CACHED_CONTEXT)
    retained.add_arcs(arcs)
    retained.write()
    return True


def merge_delta_artifacts(
    *,
    retained_path: Path | None,
    delta_path: Path,
    destination_path: Path,
    coverage_json: Path,
    project_root: Path,
) -> None:
    """Merge retained and fresh data, then regenerate the scored JSON report.

    Both published artifacts are derived from the same temporary merged database.
    Any read, merge, analysis, or publication failure removes the public pair so
    downstream rails cannot score a half-updated result.
    """
    merged_temp = _private_temp(destination_path)
    json_temp = _private_temp(coverage_json)
    try:
        config_path = project_root / "pyproject.toml"
        if not config_path.is_file():
            raise RuntimeError(f"Coverage.py configuration is missing: {config_path}")
        retained = _read_data(retained_path) if retained_path is not None else None
        delta = _read_data(delta_path)
        if not delta.has_arcs():
            raise RuntimeError("delta Coverage.py data does not contain branch arcs")
        merged = CoverageData(basename=str(merged_temp))
        merged.update(delta)
        if retained is not None and retained.has_arcs():
            merged.update(retained)
        merged.write()
        report = Coverage(
            data_file=str(merged_temp),
            config_file=str(config_path),
        )
        report.load()
        report.json_report(outfile=str(json_temp))
        atomic_replace(json_temp, coverage_json)
        atomic_replace(merged_temp, destination_path)
    except (CoverageException, OSError, RuntimeError, ValueError) as error:
        coverage_json.unlink(missing_ok=True)
        destination_path.unlink(missing_ok=True)
        raise RuntimeError(f"could not merge delta Coverage.py proof: {error}") from error
    finally:
        merged_temp.unlink(missing_ok=True)
        json_temp.unlink(missing_ok=True)


def _read_data(path: Path) -> CoverageData:
    if not path.is_file():
        raise RuntimeError(f"Coverage.py data is missing: {path}")
    data = CoverageData(basename=str(path))
    try:
        data.read()
    except (CoverageException, OSError) as error:
        raise RuntimeError(f"Coverage.py data is unreadable: {path}: {error}") from error
    return data


def _private_temp(destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    return destination.with_name(f".{destination.name}.{os.getpid()}.{uuid4().hex}.retry.tmp")
