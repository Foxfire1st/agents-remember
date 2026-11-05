"""Explicit auth comparisons; selected contents stay in memory and are never reported."""

from __future__ import annotations

import json
from pathlib import Path
from types import TracebackType
from typing import Literal, NoReturn


class AuthObservationError(RuntimeError):
    """The requested before/after observation could not be completed."""


def _read(
    path: Path, phase: Literal["before", "after"], *, missing_is_empty: bool = False
) -> bytes:
    cause = ""
    try:
        return path.read_bytes()
    except FileNotFoundError:
        if missing_is_empty:
            return b"{}"
        cause = "FileNotFoundError"
    except OSError as error:
        cause = type(error).__name__
    # Raise outside the handler: neither the original error payload nor its
    # exception context (which may quote opaque data) belongs in evidence.
    raise AuthObservationError(f"auth observation {phase} read failed: {cause}") from None


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate auth key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> NoReturn:
    raise ValueError("non-JSON auth constant")


def _subject(raw: bytes, provider: str, phase: Literal["before", "after"]) -> bytes:
    cause = ""
    try:
        document = json.loads(raw, object_pairs_hook=_unique_pairs, parse_constant=_reject_constant)
        if not isinstance(document, dict):
            raise ValueError("auth store is not an object")
        selected = {}
        for section in ("providers", "credential_pool"):
            entries = document.get(section, {})
            if not isinstance(entries, dict):
                raise ValueError("auth section is not an object")
            if provider in entries:
                selected[section] = entries[provider]
        if phase == "before" and not selected:
            raise AuthObservationError("auth observation before subject missing")
        # Each representation's presence and every nested value are compared.
        # Sorting object keys ignores serialization without dropping content.
        return json.dumps(
            selected, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
        ).encode("ascii")
    except (UnicodeError, ValueError, RecursionError) as error:
        cause = type(error).__name__
    raise AuthObservationError(f"auth observation {phase} subject invalid: {cause}") from None


class AuthObservation:
    """Capture once and compare once, then discard the private baseline.

    Use as a context manager so an interrupted run also discards the baseline.
    Only ``compare()``'s ``equal``/``differs`` string belongs in a report.
    ``provider=None`` retains exact whole-file behavior. The GPT-only selection
    compares canonical providers/pool entries, excluding other providers and
    top-level metadata. Its admitted private credential cannot refresh; real
    selected-entry rotation must therefore remain a finding.
    """

    __slots__ = ("__before", "__path", "__provider")

    def __init__(self, path: Path, *, provider: Literal["openai-codex"] | None = None) -> None:
        self.__path = path
        self.__provider = provider
        self.__before: bytes | None = self.__snapshot("before")

    def __snapshot(self, phase: Literal["before", "after"]) -> bytes:
        raw = _read(
            self.__path, phase, missing_is_empty=self.__provider is not None and phase == "after"
        )
        return raw if self.__provider is None else _subject(raw, self.__provider, phase)

    def __repr__(self) -> str:
        return "<AuthObservation>"

    def __getstate__(self) -> NoReturn:
        raise TypeError("AuthObservation cannot be serialized")

    def __enter__(self) -> AuthObservation:
        if self.__before is None:
            raise AuthObservationError("auth observation baseline already discarded")
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.__before = None

    def compare(self) -> Literal["equal", "differs"]:
        if self.__before is None:
            raise AuthObservationError("auth observation baseline already discarded")
        try:
            return "equal" if self.__before == self.__snapshot("after") else "differs"
        finally:
            self.__before = None
