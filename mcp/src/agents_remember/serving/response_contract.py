"""The declared response contract for every HTTP route the serving app registers.

**Why this module exists at all.** The routes did not declare a ``response_model``. Nothing
said what ``GET /api/files/read`` answers with, and nothing could fail when that answer
drifted.

**Why the declaration alone is not the gate.** FastAPI applies ``response_model`` only to
values it serializes itself: ``fastapi.routing.get_request_handler`` returns a ``Response``
instance untouched and never reaches ``serialize_response``. The handlers return a
``Response`` subclass (``JSONResponse``/``Response``/``StreamingResponse``) directly, and
two -- ``GET /api/stream`` and ``GET /api/events`` -- are async generators feeding an
``EventSourceResponse``; on all of them the decorator contributes an OpenAPI schema and
**validates nothing**. "Declare a model everywhere plus a test that no declaration is
missing" would have been green on day one and would have enforced nothing.

So the declarations here are the *contract*, and the enforcement is
``mcp/tests/test_serving_response_conformance.py``: it drives every route through the real
app and validates the body that actually came back against the model the route declares.

Rewriting the handlers to return models instead of ``Response`` objects would hand the
enforcement to FastAPI, but the bare ``304`` branch on ``/api/state`` and the per-status
typed refusals choose a status *and* a body shape that a single return type cannot express.

**Multi-shape routes.** A route that answers with more than one shape declares the success
shape as ``response_model`` and every refusal shape under ``responses={...}``; the union
members are the exact bodies the handler can emit, not a generic error envelope. The route
tables below (``*_RESPONSES``) are shared because the refusal idiom is shared: the files /
notes / change-set family maps its domain errors through one status vocabulary.

Every model here is strict (``extra="forbid"``), immutable, and camel-aliased -- an
undeclared key is a failure, which is the entire point.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

__all__ = [
    "ACTION_RESPONSES",
    "CHANGESET_READ_RESPONSES",
    "SCOPED_READ_RESPONSES",
    "WireResponse",
]


class WireResponse(BaseModel):
    """Strict immutable camel-case response model, mirroring the conversation ``WireModel``.

    ``extra="forbid"`` is what makes a conformance check able to fail: a key the handler
    started emitting and nobody declared is a validation error, not a silent addition.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
    )


# --- refusal shapes ------------------------------------------------------------------------
#
# The serving status idiom: every refusal names itself in ``status`` and explains itself in
# ``detail``. The variants below differ only in which identifier the refusal echoes back, and
# each is a separate model because ``extra="forbid"`` means a shared "status + anything" model
# would accept every shape and pin none of them.


class StatusRefusal(WireResponse):
    """``{status, detail}`` -- the shared refusal body of every serving surface."""

    status: str
    detail: str


class UnknownRepoRefusal(WireResponse):
    """404 from the repo allow-list: the boundary echoes the repo it refused."""

    status: Literal["unknown-repo"]
    repo: str


class UnknownScopeRefusal(WireResponse):
    """404 from scope resolution: the enclosure/mainline scope that resolved to nothing."""

    status: Literal["unknown-scope"]
    scope: str


class MissingPathRefusal(WireResponse):
    """404 from a confined read: the path (or the missing artifact) that was not there."""

    status: Literal["not-found"]
    path: str


class HttpDetailRefusal(BaseModel):
    """FastAPI's own ``HTTPException`` body -- not ours, but it is what the wire carries."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    detail: str


# --- /api/state, /api/task-document, /api/stream, /api/events ------------------------------


class StreamReadyMarker(WireResponse):
    """The one frame ``/api/events`` mints itself: the backlog has drained.

    Every other frame on that stream is a verbatim observer JSONL record replayed from disk
    (``events.read_new_events`` parses it and re-emits the parsed object unchanged), so the
    river's schema belongs to the observer, not to this route. What the route itself promises
    is this marker, and that its own frames never invent a shape.
    """

    ready: Literal[True]
    # MIK-R114 rule 8: the newest event timestamp across every source, which may be
    # older than the replay window, so the panel can name the last feed on a silent
    # river. Absent when the store holds no event at all.
    last_event_ts: str | None = None


# --- /api/actions/{action} ------------------------------------------------------------------


class ActionIntent(WireResponse):
    """The attributed intent echoed back by every accepted action.

    ``actor``/``source``/``ts``/``action`` are always written; the rest are added only when
    the verb has them, which is why they are optional here rather than nullable.
    """

    actor: str
    source: Literal["dashboard"]
    ts: str
    action: str
    target: str | None = None
    item_id: str | None = None
    kind: str | None = None


class ActionAccepted(WireResponse):
    """202: the intent was recorded."""

    status: Literal["received"]
    detail: str
    intent: ActionIntent


class ActionTargetRefusal(WireResponse):
    """400/409 refusals that name the action they refused."""

    status: str
    detail: str
    action: str
    target: str | None = None
    item_id: str | None = None
    next_safe_action: str | None = None


class UnknownTargetRefusal(WireResponse):
    """404: the projection holds no such enclosure target."""

    status: Literal["unknown-target"]
    target: str


class ActionDetailRefusal(WireResponse):
    """400/409 refusals that name the target rather than the action."""

    status: str
    detail: str
    target: str | None = None


# --- files ----------------------------------------------------------------------------------


class MainlineScope(WireResponse):
    """A repository's own checkout as a browsable scope."""

    scope: Literal["mainline"]
    label: str
    exists: bool


class WorktreeScope(WireResponse):
    """One active leaf enclosure as a browsable scope."""

    scope: str
    label: str
    branch: str | None = None
    leaf_id: str | None = None
    task_name: str | None = None


class RepoCatalogRow(WireResponse):
    """One allow-listed repository with everything that can be browsed inside it."""

    repo: str
    mainline: MainlineScope
    worktrees: list[WorktreeScope]


class RepoCatalog(WireResponse):
    """``GET /api/files/repos``."""

    repos: list[RepoCatalogRow]


class PathNode(WireResponse):
    """One onboarding-tree child: name, tree-relative path, and which of the two it is."""

    name: str
    path: str
    kind: Literal["file", "dir"]


class CodeDirNode(WireResponse):
    """A code directory child -- no language and no sidecar pairing to report."""

    name: str
    path: str
    kind: Literal["dir"]


class CodeFileNode(WireResponse):
    """A code file child, annotated with its language and whether a sidecar pairs it."""

    name: str
    path: str
    kind: Literal["file"]
    language: str
    has_sidecar: bool


CodeNode = Annotated[CodeFileNode | CodeDirNode, Field(discriminator="kind")]
"""A code listing entry. The discriminator is what makes the annotation load-bearing: only a
``kind: "file"`` row is allowed to carry ``language``/``hasSidecar``, and only it must."""


class DirectoryListing(WireResponse):
    """``GET /api/files/list``: one lazily-expanded directory level, both trees."""

    scope: str
    dir: str
    code: list[CodeNode]
    onboarding: list[PathNode]


class OnboardingMissing(WireResponse):
    """No sidecar pairs this path. Normal -- the File Viewer renders a placeholder."""

    status: Literal["missing"]


class OnboardingFound(WireResponse):
    """The paired sidecar, with the drift-bearing verification stamps it carries."""

    status: Literal["found"]
    path: str
    last_verified_commit_hash: str | None = None
    last_verified_commit_date: str | None = None


OnboardingMeta = Annotated[OnboardingFound | OnboardingMissing, Field(discriminator="status")]


class FileContents(WireResponse):
    """``GET /api/files/read``: one size-capped, binary-tolerant file plus its pairing."""

    scope: str
    path: str
    language: str
    size: int
    truncated: bool
    content: str
    onboarding: OnboardingMeta


class OnboardingForwardMissing(WireResponse):
    """``direction=forward`` for a code path with no sidecar."""

    scope: str
    code_path: str
    body: str | None = None
    status: Literal["missing"]


class OnboardingForwardFound(WireResponse):
    """``direction=forward``: the sidecar's stamps and, when present, its rendered body."""

    scope: str
    code_path: str
    body: str | None = None
    status: Literal["found"]
    path: str
    last_verified_commit_hash: str | None = None
    last_verified_commit_date: str | None = None


class OnboardingPartnerNone(WireResponse):
    """``direction=reverse`` in a scope with no onboarding root at all."""

    scope: str
    onboarding_path: str
    kind: Literal["none"]


class OnboardingPartnerOverview(WireResponse):
    """``direction=reverse`` for a partnerless doc: it carries its own markdown body."""

    scope: str
    onboarding_path: str
    kind: Literal["overview"]
    route: str
    body: str | None = None


class OnboardingPartnerSidecar(WireResponse):
    """``direction=reverse`` for a file sidecar: the code path it pairs, and whether it is there."""

    scope: str
    onboarding_path: str
    kind: Literal["sidecar"]
    code_path: str
    exists: bool


OnboardingResolution = (
    OnboardingForwardFound
    | OnboardingForwardMissing
    | OnboardingPartnerSidecar
    | OnboardingPartnerOverview
    | OnboardingPartnerNone
)
"""``GET /api/files/onboarding`` answers in one of five shapes -- the ``direction`` query
picks the pair, and within each pair the ``status``/``kind`` discriminates. Declaring the
union is the honest contract; declaring only the forward shape would have been a lie about
half the route's traffic."""


# --- notes ----------------------------------------------------------------------------------


class FolderEntry(WireResponse):
    """One entry of one notes folder: a file (with size and language) or a folder."""

    name: str
    path: str
    kind: str
    size: int | None
    language: str = ""


class NotesFolder(WireResponse):
    """``GET /api/notes/folder``: one folder's entries in a stable order, in parts.

    ``total`` counts every entry of the folder; ``entries`` holds this part;
    ``remaining`` says how many the next ``offset`` still owes. ``exists`` is false
    (with ``reason``) when the master has no notes folder or the folder is gone —
    both are empty, never an error.
    """

    repo: str
    master: str
    path: str
    entries: list[FolderEntry]
    total: int
    offset: int
    limit: int
    remaining: int
    exists: bool
    reason: str | None


class NoteResolution(WireResponse):
    """One reference string resolved to a notes path, or null (plain text, never a guess)."""

    reference: str
    path: str | None


class NotesResolutions(WireResponse):
    """``POST /api/notes/resolve``: each reference of one request, resolved or null."""

    repo: str
    master: str
    resolutions: list[NoteResolution]


class NoteContents(WireResponse):
    """``GET /api/notes/read``: one note, size-capped and binary-tolerant."""

    repo: str
    master: str
    path: str
    language: str
    size: int
    truncated: bool
    content: str


class TaskReportRow(WireResponse):
    """One role start recorded for a task document, newest first.

    ``path``/``canonical_path`` are null until the product records the report; a row
    without them is a start with no readable report, never a guessed file.
    """

    role: str
    request_id: str
    created_at: str
    status: str | None
    path: str | None
    canonical_path: str | None
    available: bool


class TaskReports(WireResponse):
    """``GET /api/role-launch/reports``: a task's recorded role starts, at most twenty.

    ``total`` counts every recorded start; ``explorer`` names the notes-relative
    folder holding the task's report files, and is set only when more starts exist
    than the rows show.
    """

    repo: str
    master: str
    doc: str
    reports: list[TaskReportRow]
    total: int
    explorer: str | None


# --- task-local requirements ----------------------------------------------------------------


class RequirementRow(WireResponse):
    """One canonical Markdown packet under a task master's ``requirements/`` root."""

    name: str
    path: str
    address: str
    size: int
    sha256: str


class RequirementsListing(WireResponse):
    """``GET /api/requirements/list``: the registered root selected by task context."""

    repo: str
    master: str
    document: str
    registered: bool
    requirements: list[RequirementRow]


class RequirementContents(WireResponse):
    """``GET /api/requirements/read``: exact UTF-8 bytes decoded from one packet."""

    repo: str
    master: str
    document: str
    name: str
    path: str
    address: str
    size: int
    sha256: str
    content: str


# --- change-set -----------------------------------------------------------------------------


class ChangedFile(WireResponse):
    """One changed file with its per-file counts (``None`` for a binary numstat)."""

    path: str
    insertions: int | None = None
    deletions: int | None = None
    status: str


class ChangedCodeFile(ChangedFile):
    """A changed *code* file, which additionally reports whether a sidecar pairs it."""

    has_sidecar: bool


class ChangeCounters(WireResponse):
    """File count plus summed insertions/deletions for one side of a change-set."""

    files: int
    insertions: int
    deletions: int


class ChangeSetCounters(WireResponse):
    """Both sides' counters, always both present."""

    code: ChangeCounters
    memory: ChangeCounters


class TaskChangeSet(WireResponse):
    """``GET /api/changeset/task`` for an enclosure scope: base -> worktree."""

    scope: str
    code: list[ChangedCodeFile]
    memory: list[ChangedFile]
    counters: ChangeSetCounters


class ComparisonTrees(WireResponse):
    code_base: str
    code_tip: str
    memory_base: str
    memory_tip: str


class ComparisonChangeSet(TaskChangeSet):
    """A recorded review comparison, never a working or landed range."""

    mode: Literal["comparison"]
    comparison: int
    trees: ComparisonTrees


class LeafChangeSet(TaskChangeSet):
    """The same shape for a leaf view, which additionally names the mode it was resolved in.

    ``state`` says whether that mode's own endpoints are recorded. ``recorded`` is the ordinary
    answer: the range's two commits both exist and the lists are that range.

    ``unrecorded`` is a ``committed`` view of a leaf whose landed commit nothing has recorded yet --
    the state every live leaf is in before its closeout, and one the change-set bar probes as soon
    as a leaf document is opened. It is answered rather than refused because the resource exists and
    only its second endpoint does not, and because a ``404`` for it is a browser console error on a
    page whose accepted criterion is zero (register B6). ``stateDetail`` names the missing endpoint
    and the action that produces it, so the answer is explicit: the counters beside it are a measured
    zero **of nothing**, and no reader may take them for "the leaf landed nothing".
    """

    mode: Literal["committed", "working"]
    state: Literal["recorded", "unrecorded"] = "recorded"
    state_detail: str = ""


class LeafSummary(WireResponse):
    """One leaf's counter breakdown alongside the master's net diff."""

    leaf_id: str
    state: Literal["committed", "working"] = "committed"
    counters: ChangeSetCounters


class MasterNetGeneration(WireResponse):
    """The exact endpoints one master net comparison was computed over, and their identity.

    The four commits are the generation: a completed master's recorded result re-resolves from
    these rather than from the live branch tip. Empty memory commits mean the master shows no
    memory half. ``digest`` is the deterministic content id of the four endpoints.
    """

    code_base: str
    code_tip: str
    memory_base: str = ""
    memory_tip: str = ""
    digest: str


class MasterCounters(WireResponse):
    """The series counter projection; complete counts without file presentation data."""

    master: str
    leaves: list[LeafSummary]
    counters: ChangeSetCounters
    generation: MasterNetGeneration | None = None
    currentness: Literal["current", "superseded", "unmeasured"] = "unmeasured"
    scope: Literal["integrated"] = "integrated"


class MasterChangeSet(WireResponse):
    """``GET /api/changeset/master``: the NET series diff, plus the per-leaf breakdown."""

    master: str
    leaves: list[LeafSummary]
    code: list[ChangedCodeFile]
    memory: list[ChangedFile]
    counters: ChangeSetCounters
    generation: MasterNetGeneration | None = None
    currentness: Literal["current", "superseded", "unmeasured"] = "unmeasured"
    scope: Literal["integrated"] = "integrated"


class DiffSide(WireResponse):
    """One side of a file diff. Absent entirely (``null``) for a pure add or delete."""

    content: str


class FileDiff(WireResponse):
    """``GET /api/changeset/file-diff``: BEFORE + AFTER content, never unified-diff text."""

    scope: str
    kind: Literal["code", "memory"]
    path: str
    language: str
    before: DiffSide | None = None
    after: DiffSide | None = None


# --- shared ``responses=`` tables ------------------------------------------------------------
#
# Declared once because the refusal idiom is shared. A route adds to its table only the
# statuses it can actually produce; nothing here is boilerplate carried by every route.

SCOPED_READ_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": StatusRefusal, "description": "Confinement breach or malformed selector"},
    404: {
        "model": UnknownRepoRefusal | UnknownScopeRefusal | MissingPathRefusal,
        "description": "Unknown repo, unknown scope, or a path that is not there",
    },
}
"""The files / notes / change-set family: ``run_scoped`` and its two siblings map every domain
error onto exactly these two statuses."""


CHANGESET_READ_RESPONSES: dict[int | str, dict[str, Any]] = {
    **SCOPED_READ_RESPONSES,
    304: {"description": "Change-set content unchanged; ETag only, no body"},
}


ACTION_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ActionTargetRefusal | ActionDetailRefusal, "description": "Ill-posed action"},
    404: {"model": UnknownTargetRefusal, "description": "No such target in the projection"},
    409: {
        "model": ActionTargetRefusal | ActionDetailRefusal,
        "description": "Unavailable or disabled",
    },
    503: {"model": HttpDetailRefusal, "description": "Projection not ready"},
}
"""``/api/actions/{action}``: the evaluator's refusals plus the not-ready projection."""
