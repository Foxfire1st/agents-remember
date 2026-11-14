"""Media sources read from a Git object store, beside the working-tree source in ``media``.

The bytes routes answer a picture or a video from one of two places: a folder a viewer may
read (``media.working_media``) or a Git tree the change-set and knowledge routes already
resolved. The client never names a commit or a revision: the route's selector picks the
tree, and this module reads the exact object that selector names.

A revision is an object id and nothing else. That is what keeps request text out of a Git
command's option position, and it is why a ref-shaped value is refused here rather than
passed on for Git to interpret.
"""

from __future__ import annotations

import re
from pathlib import Path

from agents_remember.kernel.git_command import run_git, run_git_bytes
from agents_remember.serving.media import (
    MEDIA_BYTE_LIMIT,
    MediaSource,
    MediaTooLarge,
    MediaValidationError,
    UnsupportedMedia,
    media_type_for,
)

_OBJECT_ID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


def is_object_id(value: str) -> bool:
    """Whether ``value`` is exactly one immutable object id (a commit or a blob)."""

    return bool(_OBJECT_ID.fullmatch(value))


def require_readable_path(path: str) -> str:
    """Refuse a request path shaped like a Git option or otherwise unusable as one path.

    Confinement (``confine_rel``) already keeps a path inside its root; this is the extra
    shape rule the bytes routes owe their Git calls, so no request text reaches an option
    position even when the value happens to resolve inside the root.
    """

    if not path or path.startswith(("-", "/")) or "\x00" in path or "\\" in path:
        raise MediaValidationError(f"path {path!r} is not a plain repository-relative path")
    return path


def _object_size(repository: Path, spec: str) -> int | None:
    result = run_git(repository, ["cat-file", "-s", spec])
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return int(value) if value.isdigit() else None


def _object_bytes(repository: Path, spec: str) -> bytes | None:
    result = run_git_bytes(repository, ["cat-file", "blob", spec])
    return result.stdout if result.returncode == 0 else None


def git_media(
    repository: Path, revision: str, path: str, *, limit: int = MEDIA_BYTE_LIMIT
) -> MediaSource:
    """The media source of ``path`` at ``revision`` in ``repository``.

    ``revision`` must be an object id (a ref is refused: a client never names a revision);
    ``path`` must already be confined to ``repository`` and pass :func:`require_readable_path`.
    A path the tree does not hold is ``FileNotFoundError``; a non-media suffix is
    :class:`~agents_remember.serving.media.UnsupportedMedia` from :func:`media_type_for`.
    """

    if not is_object_id(revision):
        raise MediaValidationError(f"revision {revision!r} is not an object id")
    require_readable_path(path)
    media_type = media_type_for(path)
    if media_type is None:
        raise UnsupportedMedia(path)
    spec = f"{revision}:{path}"
    size = _object_size(repository, spec)
    if size is None:
        raise FileNotFoundError(path)
    if size > limit:
        raise MediaTooLarge(size)

    def read(start: int, end: int) -> bytes:
        data = _object_bytes(repository, spec)
        if data is None:
            raise FileNotFoundError(path)
        return data[start : end + 1]

    return MediaSource(media_type=media_type, size=size, read=read)


def blob_media(
    repository: Path, blob: str, path: str, *, limit: int = MEDIA_BYTE_LIMIT
) -> MediaSource:
    """The media source of one immutable blob, named by the code reader's own answer."""

    if not is_object_id(blob):
        raise MediaValidationError(f"blob {blob!r} is not an object id")
    media_type = media_type_for(path)
    if media_type is None:
        raise UnsupportedMedia(path)
    size = _object_size(repository, blob)
    if size is None:
        raise FileNotFoundError(path)
    if size > limit:
        raise MediaTooLarge(size)

    def read(start: int, end: int) -> bytes:
        data = _object_bytes(repository, blob)
        if data is None:
            raise FileNotFoundError(path)
        return data[start : end + 1]

    return MediaSource(media_type=media_type, size=size, read=read)
