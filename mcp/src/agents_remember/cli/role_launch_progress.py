"""One disposable working notice, owned and reclaimed by the dispatch lock lifetime."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from fastapi.responses import JSONResponse

Phase = Literal["preparing", "starting"]


@dataclass(slots=True)
class _Notice:
    current: tuple[uuid.UUID, Phase] | None = None


_notice = _Notice()


def begin(request_id: uuid.UUID) -> None:
    _notice.current = (request_id, "preparing")


def starting() -> None:
    if _notice.current is not None:
        _notice.current = (_notice.current[0], "starting")


def finish() -> None:
    _notice.current = None


def read(request_id: uuid.UUID) -> JSONResponse:
    current = _notice.current
    return JSONResponse({"phase": current[1] if current and current[0] == request_id else None})
