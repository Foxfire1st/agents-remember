"""Response models for the ``read_ar_files`` tool (slice 07).

AR-owned, strict response contract: one batch of paired source+onboarding reads
of repo-relative paths inside an AR-managed repo. Per-file ``status`` is the
onboarding-lookup outcome (``found | missing | disabled | unsupported |
not_requested``); ``source`` is independent of status (present whenever the
source file exists and decodes as UTF-8 text). The packet also auto-attaches the
repo overview and the governing route-overview chain, deduplicated per lifecycle.

Token fields are stamped by ``finalize_payload_tokens`` at the ``_tool_payload``
choke point -- this module never sets them.
"""

from __future__ import annotations

from typing import Any, Literal

# ``FileReadStatus`` is imported from the controller that decides it, never retyped here:
# ``_resolve_onboarding`` returns it and ``_read_one`` puts it into an untyped payload dict, so
# a copy on this side would only be measured against the producer when a real read carried the
# new member -- as a ValidationError, on the tool path, with no handler for one.
from agents_remember.controllers.read_files import FileReadStatus
from agents_remember.models.base import StrictResponseModel, ToolResponse


class FileRead(StrictResponseModel):
    """One requested file's paired source + onboarding result.

    ``source`` is the full file or the exact requested line range; it is omitted
    when the file is absent or is binary/non-decodable. ``onboarding`` is the
    file-level onboarding body (``meaningful_body``) when ``status == found``;
    omitted otherwise.
    """

    path: str
    status: FileReadStatus
    source: str | None = None
    onboarding: str | None = None


class ReadArFilesResponse(ToolResponse):
    """``read_ar_files``: paired source+onboarding reads + auto-attached overviews.

    ``repository_overview`` and ``route_overviews`` are the session-deduplicated
    front-door: each is served once per lifecycle, or again when its content
    changed; both are omitted when already served unchanged (or when
    ``onboarding`` was suppressed for every file).
    """

    operation: Literal["read_ar_files"] = "read_ar_files"
    repoId: str
    files: list[FileRead]
    repository_overview: dict[str, Any] | None = None
    route_overviews: dict[str, Any] | None = None
