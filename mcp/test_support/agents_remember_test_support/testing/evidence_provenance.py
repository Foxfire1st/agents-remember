"""Candidate and machine provenance shared by non-accepting Dagger evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from agents_remember.kernel.atomic_write import atomic_write_text

from agents_remember_test_support.testing.candidate_snapshot import candidate_snapshot
from agents_remember_test_support.testing.dagger_admission import require_dagger_admission

PROVENANCE_SCHEMA = "ar-nonaccepting-evidence-provenance/v1"


def capture_provenance(project_root: Path) -> dict[str, object]:
    """Capture exact candidate identity and stable runtime-machine facts."""

    snapshot = candidate_snapshot(project_root.resolve())
    environment = {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "pytest": pytest.__version__,
        "logicalCpuCount": os.cpu_count(),
        "executable": sys.executable,
    }
    encoded = json.dumps(environment, sort_keys=True, separators=(",", ":")).encode()
    return {
        "schemaVersion": PROVENANCE_SCHEMA,
        "capturedAt": datetime.now(UTC).isoformat(),
        "candidate": snapshot.payload(),
        "environment": environment,
        "environmentId": hashlib.sha256(encoded).hexdigest(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    require_dagger_admission(subject="non-accepting evidence provenance")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        args.output,
        json.dumps(capture_provenance(args.project_root), indent=2, sort_keys=True) + "\n",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
