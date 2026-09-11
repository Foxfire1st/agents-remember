"""Shared fail-closed errors and exact-file reads for lifecycle enclosure locations."""

from __future__ import annotations

import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Literal


class LifecycleOperationLocationError(RuntimeError):
    """The sole locator-to-root authority could not be proven."""

    def __init__(
        self,
        status: str,
        detail: str,
        *,
        expected: Mapping[str, object],
        observed: Mapping[str, object],
    ) -> None:
        self.status = status
        self.detail = detail
        self.expected = dict(expected)
        self.observed = dict(observed)
        super().__init__(detail)


def location_error(
    status: str,
    detail: str,
    *,
    contract_path: Path,
    observed: dict[str, object],
) -> LifecycleOperationLocationError:
    return LifecycleOperationLocationError(
        status,
        detail,
        expected={
            "contractPath": contract_path.as_posix(),
            "route": "locator -> root manifest -> root journal",
        },
        observed=observed,
    )


def read_location_bytes(path: Path, owner: str, contract_path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise location_error(
            "operation-location-invalid",
            f"the canonical {owner} bytes are unreadable",
            contract_path=contract_path,
            observed={
                "path": path.as_posix(),
                "owner": owner,
                "errorType": type(exc).__name__,
            },
        ) from exc


def location_path_presence(
    path: Path,
    contract_path: Path,
    owner: str,
) -> Literal["missing", "file"]:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return "missing"
    except OSError as exc:
        raise location_error(
            "operation-location-invalid",
            f"the canonical {owner} path cannot be inspected",
            contract_path=contract_path,
            observed={
                "path": path.as_posix(),
                "owner": owner,
                "errorType": type(exc).__name__,
            },
        ) from exc
    if not stat.S_ISREG(mode):
        raise location_error(
            "operation-location-invalid",
            f"the canonical {owner} path is present but is not a regular file",
            contract_path=contract_path,
            observed={
                "path": path.as_posix(),
                "owner": owner,
                "fileType": "non-regular",
            },
        )
    return "file"


__all__ = [
    "LifecycleOperationLocationError",
    "location_error",
    "location_path_presence",
    "read_location_bytes",
]
