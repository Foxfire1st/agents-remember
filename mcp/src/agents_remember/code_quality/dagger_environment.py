"""Validate the nonce-attested Dagger environment before any test-capable quality path."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path

DAGGER_TEST_ATTESTATION_ENV = "AR_DAGGER_TEST_ATTESTATION"
DAGGER_TEST_ATTESTATION_PATH = Path("/tmp/ar-quality/dagger-test-attestation")


class DaggerEnvironmentError(RuntimeError):
    """The current process is not inside the authorized Dagger quality graph."""


def dagger_test_environment_error(
    environ: Mapping[str, str],
    attestation_path: Path | None = None,
) -> str | None:
    """Return why this process is not the nonce-attested Dagger test container."""
    token = environ.get(DAGGER_TEST_ATTESTATION_ENV, "")
    if re.fullmatch(r"[0-9a-f]{32}", token) is None:
        return f"{DAGGER_TEST_ATTESTATION_ENV} is absent or invalid"
    path = attestation_path or DAGGER_TEST_ATTESTATION_PATH
    try:
        recorded = path.read_text(encoding="utf-8")
    except OSError as error:
        return f"Dagger attestation file is unavailable: {error}"
    if recorded != token:
        return "Dagger environment and attestation-file nonces do not match"
    return None


def require_dagger_test_environment(
    *,
    environ: Mapping[str, str] | None = None,
    attestation_path: Path | None = None,
    subject: str = "Agents Remember tests",
) -> None:
    """Refuse a test-capable path outside the pinned Dagger quality graph."""
    resolved_environment = os.environ if environ is None else environ
    if error := dagger_test_environment_error(resolved_environment, attestation_path):
        raise DaggerEnvironmentError(
            f"{subject} are Dagger-only; refusing host execution: {error}. "
            "Run the pinned `dagger call quality ...` graph."
        )
