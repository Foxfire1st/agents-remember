"""Response models for the ``read_ar_files`` tool (MIK-R109 rule 1).

AR-owned, strict response contract: one batch of knowledge locations for
repo-relative paths inside an AR-managed repo. A read names where a thing
is and does not carry it: the answer holds no source text, no card text
and no overview text.

Token fields are stamped by ``finalize_payload_tokens`` at the ``_tool_payload``
choke point -- this module never sets them.
"""

from __future__ import annotations

from typing import Literal

from agents_remember.models.base import StrictResponseModel, ToolResponse


class NamedRecord(StrictResponseModel):
    """One record whose state asks the caller to act (packet rule 1).

    Named singly: the text ID, the kind, the computed state, and the card
    heading that covers it, so the caller opens the card at the heading.
    ``titleFallback`` reports the limitation: the card carries sections,
    but none scored, so the heading is the title rather than a resolved
    section.
    """

    id: str
    kind: str
    state: str
    heading: str
    titleFallback: bool = False


class NamedFile(StrictResponseModel):
    """What governs one requested path: existence, card location, record states.

    ``card`` is the governing card's location in the caller's memory
    worktree — relative to the caller's working folder when the file lies
    in it, absolute otherwise — or ``None`` when the path has none.
    ``recordCounts`` counts the card's records by state on the code the
    caller sees; ``actionNeeded`` names only the records whose state asks
    the caller to act (longer lists are cut to the count plus first entries
    by the answer guard, MIK-R108 rule 4). A row the server could not
    confine carries ``exists: None`` with a row-level ``refusalCode`` /
    ``refusalDetail`` instead of a card: confinement fails closed per row,
    never for the whole batch.
    """

    path: str
    exists: bool | None = None
    card: str | None = None
    cardStatus: Literal["found", "missing"] | None = None
    cardState: str | None = None
    recordCounts: dict[str, int] | None = None
    # MIK-R118 rule 5 and MIK-R124 rule 7: one short line, a number, and no more. The count of open
    # weaknesses on the file, and the count of documents that stand on it. The field is absent when
    # the count is zero, and neither ever carries a record's text.
    openWeaknesses: int | None = None
    dependentDocuments: int | None = None
    actionNeeded: list[NamedRecord] | None = None
    statesProblem: str | None = None
    indexComplete: bool | None = None
    routeIndexStale: bool | None = None
    refusalCode: str | None = None
    refusalDetail: str | None = None


class ReadArFilesResponse(ToolResponse):
    """``read_ar_files``: the knowledge governing a batch of paths, as locations.

    ``overviews`` lists the governing overviews once for the call, as
    locations, nearest first. ``codeState`` says what the record states were
    measured on (``head``: the checkout's HEAD tree; MIK-R73 rule 7 keeps the
    field and moves the measurement to the working tree) and ``codeTree`` is
    the tree observed. ``memoryState`` says whether cards could be named at
    all (``converted``, ``missing`` or ``unconverted``).

    A refusal is a state, not a partial naming: ``state`` is ``named`` or
    ``refused``, and the refusal fields name the offending input.
    """

    operation: Literal["read_ar_files"] = "read_ar_files"
    repoId: str
    state: Literal["named", "refused"]
    codeState: str | None = None
    codeTree: str | None = None
    memoryState: str | None = None
    worktreeScope: Literal["leaf", "official-line"] | None = None
    sourceRoot: str | None = None
    memoryRoot: str | None = None
    files: list[NamedFile] | None = None
    overviews: list[str] | None = None
    routeIndexStale: bool | None = None
    routeIndexRefresh: str | None = None
    statesProblem: str | None = None
    refusalCode: str | None = None
    refusalDetail: str | None = None
