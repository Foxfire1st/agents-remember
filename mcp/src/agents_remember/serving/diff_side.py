"""What one side of a file diff holds: text, a media file, or another binary.

The diff route used to decode both sides as text, so a changed picture or video arrived as
replacement characters and an SVG as its source. A side is now classified before it is
decoded: a served media type is reported as media with its type and size, any other
non-text file is reported as binary, and only a text side is decoded.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agents_remember.kernel.git_command import run_git, run_git_bytes
from agents_remember.serving.media import MediaValidationError, looks_binary, media_type_for
from agents_remember.serving.media_sources import is_object_id, require_readable_path


@dataclass(frozen=True)
class SideContent:
    """One side of a diff: decoded text, or a binary/media statement about the file."""

    content: str | None = None
    binary: bool = False
    media_type: str | None = None
    size: int | None = None

    def to_wire(self) -> dict[str, object]:
        """The side as the diff route publishes it: text in ``content``, never raw bytes."""

        if self.binary:
            return {"binary": True, "mediaType": self.media_type, "size": self.size}
        return {"content": self.content}


def _classify(path: str, data: bytes) -> SideContent:
    media_type = media_type_for(path)
    if media_type is not None:
        return SideContent(binary=True, media_type=media_type, size=len(data))
    if looks_binary(data):
        return SideContent(binary=True, size=len(data))
    try:
        return SideContent(content=data.decode("utf-8"))
    except UnicodeDecodeError:
        return SideContent(binary=True, size=len(data))


def working_side(path: Path, rel: str) -> SideContent | None:
    """The working-tree side of ``rel`` under ``path``; ``None`` when the file does not exist."""

    if not path.is_file():
        return None
    size = path.stat().st_size
    media_type = media_type_for(rel)
    if media_type is not None:
        # The bytes route draws it; the diff never loads the file to say so.
        return SideContent(binary=True, media_type=media_type, size=size)
    return _classify(rel, path.read_bytes())


def committed_side(repository: Path, revision: str, path: str) -> SideContent | None:
    """The side of ``path`` at ``revision``; ``None`` when the tree holds no such file.

    A revision that is not an object id is refused: the change-set routes resolve the tree
    themselves, and no request text may reach Git as an option.
    """

    if not is_object_id(revision):
        raise MediaValidationError(f"revision {revision!r} is not an object id")
    require_readable_path(path)
    spec = f"{revision}:{path}"
    size = run_git(repository, ["cat-file", "-s", spec])
    if size.returncode != 0 or not size.stdout.strip().isdigit():
        return None
    measured = int(size.stdout.strip())
    media_type = media_type_for(path)
    if media_type is not None:
        # The type and the measured size are the whole diff side; do not load a possibly
        # 16 MiB video into a request that shows only its metadata.
        return SideContent(binary=True, media_type=media_type, size=measured)
    data = run_git_bytes(repository, ["cat-file", "blob", spec])
    if data.returncode != 0:
        return None
    return _classify(path, data.stdout)


def side_or_none(side: SideContent | None) -> dict[str, object] | None:
    """The wire shape of an optional side (``None`` for an added or deleted file's absent half)."""

    return None if side is None else side.to_wire()
