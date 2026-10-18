"""Derive distinct certification authorities from the actual contract and owner outputs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Never

from pydantic import BaseModel

from agents_remember.errors import CertificationContractError


def _finding_value(value: object) -> object:
    """Project typed evidence at every depth; the error owner still validates values."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {key: _finding_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_finding_value(item) for item in value)
    return value


def refuse(code: str, expected: object, observed: object) -> Never:
    raise CertificationContractError(
        "closeout certification admission refused",
        (
            {
                "code": code,
                "path": "candidate",
                "expected": _finding_value(expected),
                "observed": _finding_value(observed),
            },
        ),
    )
