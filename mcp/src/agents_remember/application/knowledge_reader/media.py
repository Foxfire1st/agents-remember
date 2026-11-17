"""The code reader's media read: the bytes of a picture or video at the selected code tree.

The code view's text read answers a binary blob with a notice; a viewer that can draw a
picture needs the blob itself. The selection is resolved exactly as the text view resolves
it (the same memory tree and its code tree), so the picture a reader opens is the one the
tree holds -- never a file from today's checkout by the same name.
"""

from __future__ import annotations

from typing import Any

from agents_remember.application.knowledge_reader.files import FileRead, _code_blob, normal_path
from agents_remember.application.knowledge_reader.selection import (
    ReaderSelection,
    ReaderUnavailable,
    open_selection,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.serving.knowledge_reader import KnowledgeReaderQuery
from agents_remember.serving.media import MediaSource, media_type_for
from agents_remember.serving.media_sources import blob_media

__all__ = ["read_knowledge_media"]


def _selection_media(selection: ReaderSelection, path: str) -> MediaSource | dict[str, Any]:
    normalised = normal_path(path)
    if media_type_for(normalised) is None:
        return {
            "state": "unsupported-type",
            "detail": f"{normalised} is not a served picture or video type",
            "path": normalised,
        }
    located = _code_blob(selection, normalised)
    if isinstance(located, FileRead):
        return {"state": located.state, "detail": located.detail, "path": normalised}
    blob, repository = located
    return blob_media(repository, blob, normalised)


def read_knowledge_media(
    config: McpRuntimeConfig, query: KnowledgeReaderQuery
) -> MediaSource | dict[str, Any]:
    """The media source of one code path at the queried memory tree, or the reader's refusal."""

    if not query.path:
        return {"state": "invalid-request", "detail": "the code view names a path"}
    opened = open_selection(config, query.repository_id, query.commit)
    if isinstance(opened, ReaderUnavailable):
        return opened.to_document(query.repository_id, query.commit or "published")
    with opened as selection:
        return _selection_media(selection, query.path)
