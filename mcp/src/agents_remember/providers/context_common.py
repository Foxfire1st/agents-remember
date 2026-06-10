"""Shared context provider helpers."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from agents_remember.errors import AgentsRememberError

# Re-exported for ``from agents_remember.providers.context import stable_provider_id``
# (and the ``import *`` facade in ``context/__init__.py``); canonical source is identity.
from agents_remember.providers.identity import stable_provider_id  # noqa: F401


class ContextProviderError(AgentsRememberError):
    """Raised when a provider layout or patch check fails."""


def to_container_path(path: Path | str) -> str:
    """Map a host path to the POSIX path seen inside a Linux provider container.

    Bind mounts and in-container arguments require POSIX paths. On Windows a
    resolved path renders as ``C:/ew/x`` via :meth:`Path.as_posix`, whose drive
    colon both breaks Docker's ``host:container`` mount parsing ("too many
    colons") and is not a valid Linux path. Stripping the leading ``<drive>:``
    yields ``/ew/x``. On POSIX hosts there is no drive letter, so the value is
    returned unchanged and Linux/macOS behavior is preserved exactly.
    """

    posix = path.as_posix() if isinstance(path, Path) else str(path).replace("\\", "/")
    if len(posix) >= 2 and posix[0].isalpha() and posix[1] == ":":
        remainder = posix[2:]
        return remainder if remainder.startswith("/") else f"/{remainder}"
    return posix


def expand_template(value: str, variables: dict[str, str]) -> str:
    """Expand ``<name>`` tokens using the provided values."""

    expanded = value
    for key, replacement in variables.items():
        expanded = expanded.replace(f"<{key}>", replacement)
    return expanded


def provider_requirements_file(coordination_root: Path, provider: str) -> Path:
    """Return the copied runtime requirements file for a provider."""

    return coordination_root.resolve() / "providers" / "requirements" / f"{provider}.txt"


def ensure_provider_requirements_file(coordination_root: Path, provider: str, pin: str) -> Path:
    """Create the copied provider requirements file when an older runtime lacks it."""

    requirements_file = provider_requirements_file(coordination_root, provider)
    requirements_file.parent.mkdir(parents=True, exist_ok=True)
    if not requirements_file.exists():
        requirements_file.write_text(f"{pin}\n", encoding="utf-8")
    return requirements_file


def read_provider_pin(requirements_file: Path, package_name: str) -> str:
    """Read a single pinned requirement such as ``grepai==0.35.0``."""

    if not requirements_file.exists():
        raise ContextProviderError(
            f"provider requirements file does not exist: {requirements_file}"
        )

    package_name = package_name.lower()
    pins = [
        _provider_pin_from_line(line, requirements_file, package_name)
        for line in _provider_requirement_lines(requirements_file)
    ]
    return _single_provider_pin(pins, requirements_file, package_name)


def _provider_requirement_lines(requirements_file: Path) -> list[str]:
    return [
        line
        for raw_line in requirements_file.read_text(encoding="utf-8").splitlines()
        if (line := raw_line.strip()) and not line.startswith("#")
    ]


def _provider_pin_from_line(requirement: str, requirements_file: Path, package_name: str) -> str:
    if not requirement.lower().startswith(f"{package_name}=="):
        raise ContextProviderError(
            f"unsupported requirement in {requirements_file}: {requirement}; expected {package_name}==<version>"
        )
    return requirement.split("==", 1)[1].strip()


def _single_provider_pin(pins: list[str], requirements_file: Path, package_name: str) -> str:
    if len(pins) != 1 or not pins[0]:
        raise ContextProviderError(
            f"expected exactly one {package_name} pin in {requirements_file}"
        )
    return pins[0]


def write_provider_state(layout: Any, data: dict[str, Any]) -> None:
    """Write provider state as pretty JSON."""

    layout.state_file.parent.mkdir(parents=True, exist_ok=True)
    layout.state_file.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def remove_runtime_path(path: Path, dry_run: bool) -> None:
    if dry_run:
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()
