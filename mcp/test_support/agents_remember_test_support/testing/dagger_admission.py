"""Mint the testing-layer capability for a nonce-attested Dagger quality process."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Final

DAGGER_TEST_ATTESTATION_ENV = "AR_DAGGER_TEST_ATTESTATION"
DAGGER_TEST_ATTESTATION_PATH = Path("/tmp/ar-quality/dagger-test-attestation")


class DaggerAdmissionError(RuntimeError):
    """The current process cannot prove admission to the Dagger quality graph."""


class _DaggerAdmissionAuthority:
    """Module-owned mint authority that downstream callers cannot construct directly."""


_DAGGER_ADMISSION_AUTHORITY: Final[_DaggerAdmissionAuthority] = _DaggerAdmissionAuthority()


@dataclass(frozen=True, init=False)
class DaggerAdmission:
    """Opaque in-process route capability minted only from the nonce/file handshake.

    The handshake prevents ordinary unsupported host invocation; it is not hostile-host
    authentication because a repository owner controls this module, its interpreter,
    environment, and filesystem. Acceptance authority is established separately by the
    Dagger executor's candidate-bound immutable publication.
    """

    attestation_path: Path
    nonce_sha256: str
    _authority: _DaggerAdmissionAuthority

    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise TypeError("Dagger admission is minted only by require_dagger_admission")

    @classmethod
    def _mint(cls, *, attestation_path: Path, nonce: str) -> DaggerAdmission:
        instance = object.__new__(cls)
        object.__setattr__(instance, "attestation_path", attestation_path)
        object.__setattr__(instance, "nonce_sha256", sha256(nonce.encode()).hexdigest())
        object.__setattr__(instance, "_authority", _DAGGER_ADMISSION_AUTHORITY)
        return instance


def dagger_admission_refusal(
    environ: Mapping[str, str],
    attestation_path: Path | None = None,
) -> str | None:
    """Return why the modeled process cannot receive Dagger admission."""
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


def require_dagger_admission(
    *,
    environ: Mapping[str, str] | None = None,
    attestation_path: Path | None = None,
    subject: str = "Agents Remember tests",
) -> DaggerAdmission:
    """Validate the real handshake and return the sole certifying capability."""
    resolved_environment = os.environ if environ is None else environ
    resolved_attestation = attestation_path or DAGGER_TEST_ATTESTATION_PATH
    if error := dagger_admission_refusal(resolved_environment, resolved_attestation):
        raise DaggerAdmissionError(
            f"{subject} are Dagger-only; refusing host execution: {error}. "
            "Run the pinned `dagger call quality ...` graph."
        )
    return DaggerAdmission._mint(
        attestation_path=resolved_attestation,
        nonce=resolved_environment[DAGGER_TEST_ATTESTATION_ENV],
    )


def require_dagger_admission_capability(admission: DaggerAdmission) -> DaggerAdmission:
    """Refuse caller-shaped objects at certifying publication boundaries."""

    if (
        not isinstance(admission, DaggerAdmission)
        or getattr(admission, "_authority", None) is not _DAGGER_ADMISSION_AUTHORITY
    ):
        raise DaggerAdmissionError("a verified Dagger admission capability is required")
    return admission
