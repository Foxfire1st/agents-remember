"""Reading one by-file text input of a ``task_doc`` call: bounded, UTF-8, named by its digest."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .task_doc_route_review import TaskDocError

# By-file text inputs (fields/section bodies) are KB-scale; this budget stops
# a pathological file from turning a write into unbounded work, refusing
# loudly with the bound instead of reading on.
TASK_DOC_INPUT_MAX_BYTES = 1024 * 1024


def read_text_input(path: str) -> tuple[str, str]:
    """Read one by-file text input once, or refuse the call in one sentence."""

    candidate = Path(path)
    if not candidate.is_absolute():
        raise TaskDocError(f"task_doc file input must name an absolute path, not {path!r}.")
    if not candidate.is_file():
        raise TaskDocError(f"task_doc file input is no regular file: {path}.")
    try:
        raw = candidate.read_bytes()
    except OSError as exc:
        raise TaskDocError(f"task_doc file input cannot be read: {path} ({exc}).") from exc
    if len(raw) > TASK_DOC_INPUT_MAX_BYTES:
        raise TaskDocError(
            f"task_doc file input too large: {path} holds {len(raw)} bytes, over the "
            f"{TASK_DOC_INPUT_MAX_BYTES} byte budget; pass the text inline instead."
        )
    digest = hashlib.sha256(raw).hexdigest()
    try:
        return raw.decode("utf-8"), digest
    except UnicodeDecodeError as exc:
        raise TaskDocError(f"task_doc file input is not UTF-8 text: {path}.") from exc
