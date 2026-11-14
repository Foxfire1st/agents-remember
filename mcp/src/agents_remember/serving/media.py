"""The one media table, size bound and byte-range policy for the dashboard's content routes.

Every viewer that draws a picture or plays a video asks the bytes routes for bytes; those
routes serve only the fixed types listed here and refuse everything else, so a browser is
never handed an arbitrary content type. A text read still answers text: a caller that asks
for a media path's text gets a ``binary`` answer naming the media type, and this module is
the single place that decides which paths those are.

No cache lives here. A range request reads exactly the bytes it asks for (a working file is
seeked; a Git side is read from the object store), and the answer carries no store of its
own, so nothing of a picture or a video stays in memory after the response is built.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from fastapi.responses import JSONResponse, Response

from agents_remember.errors import AgentsRememberError

# The fixed list of types a viewer may draw: PNG, JPEG, GIF, WebP and SVG, and MP4 and WebM.
# A suffix outside this table is not a media file for the serving layer -- a text route reads
# it as text or binary, and a bytes request is refused with ``unsupported-type``.
MEDIA_TYPES: Final[dict[str, str]] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
}

# The one named size limit of the bytes routes. The repository's own explainer video is
# 9.5 MB, so the bound leaves room above it; above the bound the viewer states the size and
# that the file is too large to show (the route answers 413 with the measured size).
MEDIA_BYTE_LIMIT: Final = 16 * 1024 * 1024

# A file's bytes are read in bounded chunks for the probe that tells text from binary; the
# bytes routes never need more than this to answer a type.
_PROBE_BYTES: Final = 4096

# An SVG is a document, not only an image: opened by its own address it can carry script.
# The sandbox and the empty source list make that address inert, while a stylesheet an SVG
# already carries still paints. The same headers ride every media answer (a video ignores
# them), so one route family has one posture.
_MEDIA_HEADERS: Final[dict[str, str]] = {
    "accept-ranges": "bytes",
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
    "content-security-policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
}


def media_type_for(path: Path | str) -> str | None:
    """The fixed media type of ``path``'s suffix, or ``None`` when it is not a served type."""

    return MEDIA_TYPES.get(Path(path).suffix.lower())


def is_media_path(path: Path | str) -> bool:
    """Whether ``path``'s suffix is one of the types a viewer may draw."""

    return media_type_for(path) is not None


def looks_binary(head: bytes) -> bool:
    """Whether the first bytes of a file carry a NUL, the browser-text probe Git also uses."""

    return b"\0" in head[:_PROBE_BYTES]


class MediaValidationError(AgentsRememberError):
    """A media request, path or selector outside the bytes routes' small typed contract."""


class UnsupportedMedia(MediaValidationError):
    """The requested path's suffix is not one of the served picture or video types."""


class MediaTooLarge(MediaValidationError):
    """The file is larger than :data:`MEDIA_BYTE_LIMIT`; the viewer states its size instead."""

    def __init__(self, size: int) -> None:
        self.size = size
        super().__init__(f"the file is {size} bytes, above the {MEDIA_BYTE_LIMIT}-byte limit")


class RangeNotSatisfiable(MediaValidationError):
    """The requested byte range cannot be answered for a file of this size."""

    def __init__(self, detail: str, *, size: int | None = None) -> None:
        self.size = size
        super().__init__(detail)


@dataclass(frozen=True)
class MediaSource:
    """One file's bytes and type, read on demand within the media size bound.

    ``read`` takes an inclusive byte range and returns exactly those bytes. A working file
    is seeked; a Git side is read from its immutable object. The source holds no bytes, so
    an answer that used it leaves nothing behind.
    """

    media_type: str
    size: int
    read: Callable[[int, int], bytes]

    def read_all(self) -> bytes:
        return self.read(0, self.size - 1)

    def read_range(self, start: int, end: int) -> bytes:
        return self.read(start, end)


def working_media(path: Path, *, limit: int = MEDIA_BYTE_LIMIT) -> MediaSource:
    """The media source of one working-tree file; raises the typed refusals by name."""

    media_type = media_type_for(path)
    if media_type is None:
        # The suffix is refused before the file is touched: a path outside the fixed table is
        # not a media request, and the answer says so whether or not a file exists there.
        raise UnsupportedMedia(path.suffix or path.name)
    if not path.is_file():
        raise FileNotFoundError(path.name)
    size = path.stat().st_size
    if size > limit:
        raise MediaTooLarge(size)

    def read(start: int, end: int) -> bytes:
        with path.open("rb") as handle:
            handle.seek(start)
            return handle.read(end - start + 1)

    return MediaSource(media_type=media_type, size=size, read=read)


def media_response(source: MediaSource, range_header: str | None) -> Response:
    """The full or partial answer for ``source``; ``range_header`` is the request's HTTP Range.

    A malformed or unsatisfiable range is :class:`RangeNotSatisfiable`, which the route maps
    to a 416 carrying the file's total size. A satisfying range is a 206 with its
    ``Content-Range``; no range is a 200 of the whole file.
    """

    span = parse_range(range_header, source.size)
    if span is None:
        if source.size == 0:
            headers = dict(_MEDIA_HEADERS)
            headers["content-length"] = "0"
            return Response(
                content=b"", status_code=200, media_type=source.media_type, headers=headers
            )
        start, end = 0, source.size - 1
        status = 200
    else:
        start, end = span
        status = 206
    headers = dict(_MEDIA_HEADERS)
    headers["content-length"] = str(end - start + 1)
    if status == 206:
        headers["content-range"] = f"bytes {start}-{end}/{source.size}"
    return Response(
        content=source.read(start, end),
        status_code=status,
        media_type=source.media_type,
        headers=headers,
    )


def media_error_response(error: BaseException) -> JSONResponse | None:
    """The typed refusal of a media read, or ``None`` when ``error`` is not one of them."""

    if isinstance(error, MediaTooLarge):
        return JSONResponse(
            {"status": "too-large", "size": error.size, "limit": MEDIA_BYTE_LIMIT},
            status_code=413,
        )
    if isinstance(error, UnsupportedMedia):
        return JSONResponse({"status": "unsupported-type", "detail": str(error)}, status_code=415)
    if isinstance(error, RangeNotSatisfiable):
        headers = {"content-range": f"bytes */{error.size}"} if error.size is not None else None
        return JSONResponse(
            {"status": "range-not-satisfiable", "detail": str(error)},
            status_code=416,
            headers=headers,
        )
    return None


def media_answer(produce: Callable[[], Response]) -> Response:
    """Run a media read, turning its typed refusal into the route's answer.

    Every bytes route needs the same three answers: 413 with the measured size, 415 for a
    suffix outside the fixed table, and 416 with the file's total size. One helper keeps
    that mapping in one place.
    """

    try:
        return produce()
    except (MediaTooLarge, UnsupportedMedia, RangeNotSatisfiable) as error:
        answer = media_error_response(error)
        assert answer is not None  # the three typed refusals above always map
        return answer


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """The inclusive ``(start, end)`` of one single byte range, or ``None`` for the whole file.

    Only the single-range form is answered: a multi-range request would need a multipart
    body, and the dashboard's players ask for one range at a time. A syntactically invalid
    or out-of-bounds range is :class:`RangeNotSatisfiable`; an empty header is no range.
    """

    if header is None or not header.strip():
        return None
    unit, separator, value = header.strip().partition("=")
    if separator == "" or unit.lower() != "bytes" or "," in value or size <= 0:
        raise RangeNotSatisfiable(header, size=size)
    first, dash, last = value.strip().partition("-")
    if dash == "" or (first and not first.isdigit()) or (last and not last.isdigit()):
        raise RangeNotSatisfiable(header, size=size)
    if first == "":
        # A suffix range: the last N bytes.
        if last == "":
            raise RangeNotSatisfiable(header, size=size)
        suffix = int(last)
        if suffix <= 0:
            raise RangeNotSatisfiable(header, size=size)
        start = max(0, size - suffix)
        return start, size - 1
    start = int(first)
    if start >= size:
        raise RangeNotSatisfiable(header, size=size)
    if last == "":
        return start, size - 1
    end = int(last)
    if end < start:
        raise RangeNotSatisfiable(header, size=size)
    return start, min(end, size - 1)
