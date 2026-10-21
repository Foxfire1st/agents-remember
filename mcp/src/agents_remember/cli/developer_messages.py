"""The developer's send from AR's tray to one live AR seat (MIK-R188 rules 8 to 11).

``POST /api/role-chats/{agentId}/message`` sends on the developer's behalf. It carries no agent
binding and starts, retires or binds nothing; the dashboard's write guard covers it like every
state-changing route (D332, INV-HDXPMA). One request is one target: a Send to several chats is
one request per chat, each composing its own message.

One message, in rule 8's order: a first line ``From developer · <task of the target>`` (or
``From developer`` for a target without a task), his text, then each pick with its note. A
reference is given in its written form, whose first line is the call that opens it
(:mod:`developer_message_forms`); a text pick goes as a titled ``text`` attachment with its
provenance, an image pick as an ``images`` entry. Paseo renders text attachments after the text
and the images (L89 W32), so the numbered list in the text keeps the order and names each
attachment and the file it is saved in. The last line names the message file and its SHA-256.

A chat text pick's provenance is checked before Send as its references are (rule 3, ebdae1e5):
its agent id has the agent-id form, one token of letters, digits, ``-`` and ``_``, and it names
its message's time, which is a time (ISO 8601, a date and time or a time of day). Otherwise the
pick is refused by name (``provenance-invalid``), so a provenance line never adds lines to the
message and never states a time that is none or unknown.
A reference whose parts disagree is refused as ``reference-contradictory``, one whose root or
record this server cannot name as ``reference-unresolvable`` (:mod:`developer_message_forms`).

The message is filed before it is sent (:mod:`developer_message_files`), then delivered or held
(:mod:`developer_delivery`). A filing that fails leaves no saved pick that no message names
(45e5b434). A refused or failed delivery keeps its file, marked undelivered. Each filed send is
one line of the recipient's sent-picks log (rule 10); a held send's line names its held entry, so
the log tells later how it ended.

Bounds (rule 11, W31): the dashboard's preferences bound a message before Send; this server
refuses by name above the same ceilings: 20 picks, 5 MiB per decoded image, 20 MiB per message.
The body itself is read only up to what a message within those bounds needs (its bytes as base64
and room for the JSON around them, ``MAX_BODY_BYTES``): a larger one is refused as
``message-too-large`` before it is read whole or parsed. A body that is no request of this shape
is refused as ``invalid-request`` with the parser's detail; an image of a type the tray does not
send as ``image-type-unsupported``. Every refusal is a delivery answer, never a bare 422.

The repository a target without a task files under is the one the page names; a page that names
none sends none, and the server takes its one configured repository, or refuses by name
(``unknown-repository``) when it has several.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)
from pydantic.alias_generators import to_camel

from agents_remember.cli.developer_delivery import (
    DEVELOPER_SENDER,
    DeveloperDelivery,
    deliver_or_hold,
)
from agents_remember.cli.developer_message_files import (
    MessageFiling,
    append_sent,
    message_filing,
    message_stem,
    read_developer_message,
    read_sent,
    target_folder,
)
from agents_remember.cli.developer_message_forms import (
    ContradictoryReference,
    FormRoots,
    LeafMemoryRoot,
    Segment,
    TrayReference,
    UnresolvableReference,
    written_form,
)
from agents_remember.cli.developer_seats import (
    SeatRecord,
    SeatWalk,
    register_seat_list_route,
    task_of,
    walk_seats,
)
from agents_remember.cli.role_launch_receipts import _read_receipt
from agents_remember.cli.role_message_delivery import _Refused
from agents_remember.cli.role_message_files import (
    MessageAttachment,
    MessageContent,
    MessageFileError,
    file_line,
    mark_undelivered,
    sha256_file,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig

MAX_PICKS = 20
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_MESSAGE_BYTES = 20 * 1024 * 1024
# The most body a message within its bounds needs: its bytes as base64, and 1 MiB for the JSON
# around them (references, titles, notes, escapes). Nothing beyond it is read.
MAX_BODY_BYTES = (MAX_MESSAGE_BYTES + 2) // 3 * 4 + 1024 * 1024
# A held entry keeps at most this many characters of the message; its file keeps all of it.
HELD_TEXT_LIMIT = 16_000
# A body of more picks than this is no tray's; it is refused as malformed, not counted.
_PARSE_PICKS_LIMIT = 1000
_IMAGE_SUFFIXES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}
_KIND_LABELS = {
    "invariant": "invariant",
    "family": "family",
    "review-block": "review block",
    "code-without-invariant": "code without an invariant",
    "task": "task",
    "file-line": "file lines",
    "change-set": "change set",
}
OneLine = Annotated[str, StringConstraints(max_length=2000)]
# The agent-id form a chat pick's provenance names (rule 3, ebdae1e5): one token, as the host's ids.
_AGENT_ID_FORM = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,199}")


class _Wire(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, alias_generator=to_camel)


class PanelProvenance(_Wire):
    source: Literal["panel"]
    panel: OneLine


class ChatProvenance(_Wire):
    source: Literal["chat"]
    agent_id: OneLine
    message_id: OneLine | None = None
    message_time: OneLine | None = None


class ImagePick(_Wire):
    # Any type parses; one the tray does not send is refused by name (image-type-unsupported).
    mime_type: OneLine
    data: str


class DeveloperPick(_Wire):
    id: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    kind: Literal["reference", "text", "image"]
    title: OneLine
    note: str = ""
    reference: TrayReference | None = None
    text: str | None = None
    provenance: (
        Annotated[PanelProvenance | ChatProvenance, Field(discriminator="source")] | None
    ) = None
    image: ImagePick | None = None

    @model_validator(mode="after")
    def _fields_of_its_kind(self) -> Self:
        given = {
            "reference": self.reference is not None,
            "text": self.text is not None or self.provenance is not None,
            "image": self.image is not None,
        }
        complete = {
            "reference": self.reference is not None,
            "text": self.text is not None and self.provenance is not None,
            "image": self.image is not None,
        }
        if not complete[self.kind] or any(given[kind] for kind in given if kind != self.kind):
            raise ValueError(f"a {self.kind} pick carries exactly the fields of its kind")
        return self


class DeveloperMessageRequest(_Wire):
    message_id: uuid.UUID
    # The page's repository; a page without one sends none (L89 P-T3).
    repository: Segment | None = None
    text: str
    picks: list[DeveloperPick] = Field(default_factory=list, max_length=_PARSE_PICKS_LIMIT)


@dataclass(frozen=True, slots=True)
class _Prepared:
    """What the picks become before anything is filed: forms and decoded images."""

    forms: dict[int, tuple[str, ...]]
    images: dict[int, bytes]
    # The repository a target without a task files under; None when the server cannot name one.
    repository: str | None


class _Refusal(Exception):
    """A send refused before anything is filed."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


# ---------------------------------------------------------------------------------------------
# The send
# ---------------------------------------------------------------------------------------------


def send_developer_message(
    config: McpRuntimeConfig,
    agent_id: str,
    request: DeveloperMessageRequest,
    *,
    leaf_memory_root: LeafMemoryRoot | None = None,
) -> dict[str, Any]:
    """File one message for one target, then deliver or hold it; the answer names its state."""

    message_id = str(request.message_id)
    try:
        prepared = _prepare(config, request, FormRoots(config, leaf_memory_root))
        walk = walk_seats(config)
        record, receipt = _recipient(walk, agent_id)
        task = task_of(config, record)
        repository = task["repository"] if task else prepared.repository
        if repository is None:
            raise _Refusal(
                "unknown-repository",
                f"The page names no repository and this server has {len(config.repositories)}; "
                "a target without a task needs one to file under.",
            )
        if read_developer_message(config, message_id, recipient_agent_id=agent_id):
            raise _Refusal(
                "message-id-reused",
                f"Message {message_id} was already filed for agent {agent_id}; one Send names "
                "one message id.",
            )
    except _Refusal as refusal:
        return _answer("refused", message_id, None, reason=refusal.reason, detail=refusal.detail)
    folder = target_folder(config, repository, task["id"] if task else None, agent_id)
    when = datetime.now(UTC)
    target = _Target(record, receipt, task, walk)
    try:
        path, digest, delivery = _file(config, request, prepared, target, (folder, when))
    except MessageFileError as error:
        return _answer("refused", message_id, None, reason="message-file-failed", detail=str(error))
    try:
        outcome = deliver_or_hold(delivery)
    except _Refused as refused:
        kept = mark_undelivered(path, refusal=refused.refusal, refusal_detail=refused.detail)
        filed = (kept.as_posix(), sha256_file(kept) if kept.is_file() else digest)
        _log(config, request, agent_id, (*filed, "refused", {}), when)
        return _answer("refused", message_id, filed, reason=refused.refusal, detail=refused.detail)
    state = str(outcome.pop("state"))
    # A held send's line names its held entry and the record its end is written in (rule 10).
    held = (
        {
            "heldMessageId": outcome.get("heldMessageId"),
            "receipt": record.path.as_posix(),
            "requestId": receipt.get("requestId"),
        }
        if state == "held"
        else {}
    )
    _log(config, request, agent_id, (path.as_posix(), digest, state, held), when)
    return {**_answer(state, message_id, (path.as_posix(), digest)), **outcome}


def _answer(
    state: str, message_id: str | None, filed: tuple[str, str] | None, **said: str
) -> dict[str, Any]:
    """The route's answer: the state, the message, its file and SHA-256, and why."""

    return {
        "state": state,
        "messageId": message_id,
        "messageFile": filed[0] if filed else None,
        "messageFileSha256": filed[1] if filed else None,
        **said,
    }


def _log(
    config: McpRuntimeConfig,
    request: DeveloperMessageRequest,
    agent_id: str,
    filed: tuple[str, str, str, dict[str, Any]],
    when: datetime,
) -> None:
    """One line of the recipient's sent-picks log: the file, its digest, the state and, for a
    held send, its held entry."""

    path, digest, state, held = filed
    append_sent(
        config,
        agent_id,
        {
            "time": when.isoformat(),
            "targetAgentId": agent_id,
            "pickIds": [pick.id for pick in request.picks],
            "messageId": str(request.message_id),
            "messageFile": path,
            "sha256": digest,
            "state": state,
            **held,
        },
    )


def _prepare(
    config: McpRuntimeConfig, request: DeveloperMessageRequest, roots: FormRoots
) -> _Prepared:
    """Check the bounds and render every reference; nothing is written yet."""

    if not request.text.strip() and not request.picks:
        raise _Refusal("empty-message", "The message has no text and no pick.")
    if request.repository is not None and request.repository not in config.repositories:
        raise _Refusal(
            "unknown-repository", f"Repository {request.repository!r} is not one of this server's."
        )
    if len(request.picks) > MAX_PICKS:
        raise _Refusal(
            "too-many-picks",
            f"The message has {len(request.picks)} picks; the bound is {MAX_PICKS}.",
        )
    images: dict[int, bytes] = {}
    for index, pick in enumerate(request.picks):
        if pick.image is not None:
            images[index] = _decoded(index, pick.image)
    size = len(request.text.encode("utf-8")) + sum(len(data) for data in images.values())
    size += sum(
        len((pick.title + pick.note + (pick.text or "")).encode("utf-8")) for pick in request.picks
    )
    if size > MAX_MESSAGE_BYTES:
        raise _Refusal(
            "message-too-large", f"The message is {size} bytes; the bound is {MAX_MESSAGE_BYTES}."
        )
    return _Prepared(_forms(request, roots), images, _repository(config, request))


def _forms(request: DeveloperMessageRequest, roots: FormRoots) -> dict[int, tuple[str, ...]]:
    """Every reference's written form, after each chat pick's provenance is checked."""

    forms: dict[int, tuple[str, ...]] = {}
    for index, pick in enumerate(request.picks):
        if isinstance(pick.provenance, ChatProvenance):
            _check_provenance(index, pick, pick.provenance)
        if pick.reference is None:
            continue
        try:
            forms[index] = written_form(pick.reference, roots)
        except ContradictoryReference as error:
            raise _Refusal(
                "reference-contradictory", f"Pick {index + 1} ({pick.title}): {error}"
            ) from error
        except UnresolvableReference as error:
            raise _Refusal(
                "reference-unresolvable", f"Pick {index + 1} ({pick.title}): {error}"
            ) from error
    return forms


def _check_provenance(index: int, pick: DeveloperPick, source: ChatProvenance) -> None:
    """Refuse by name a chat pick whose agent id or time is none (rule 3, ebdae1e5)."""

    if _AGENT_ID_FORM.fullmatch(source.agent_id) is None:
        raise _Refusal(
            "provenance-invalid",
            f"Pick {index + 1} ({pick.title}): its provenance agent id {source.agent_id!r} does "
            "not have the agent-id form.",
        )
    if source.message_time is None or not _is_time(source.message_time):
        named = (
            "names no message time"
            if source.message_time is None
            else f"message time {source.message_time!r} is not a time"
        )
        raise _Refusal(
            "provenance-invalid", f"Pick {index + 1} ({pick.title}): its provenance {named}."
        )


def _is_time(value: str) -> bool:
    """Whether ``value`` is an ISO 8601 date and time, or a time of day."""

    for parse in (datetime.fromisoformat, time.fromisoformat):
        try:
            parse(value)
        except ValueError:
            continue
        return True
    return False


def _repository(config: McpRuntimeConfig, request: DeveloperMessageRequest) -> str | None:
    """The page's repository, else the server's one configured repository, else none."""

    if request.repository is not None:
        return request.repository
    configured = list(config.repositories)
    return configured[0] if len(configured) == 1 else None


def _decoded(index: int, image: ImagePick) -> bytes:
    if image.mime_type not in _IMAGE_SUFFIXES:
        raise _Refusal(
            "image-type-unsupported",
            f"Pick {index + 1} is an image of type {image.mime_type!r}; the tray sends png, jpeg, "
            "gif and webp.",
        )
    try:
        decoded = base64.b64decode(image.data, validate=True)
    except (binascii.Error, ValueError) as error:
        raise _Refusal("image-unreadable", f"Pick {index + 1} is no base64 image.") from error
    if len(decoded) > MAX_IMAGE_BYTES:
        raise _Refusal(
            "image-too-large",
            f"Pick {index + 1} is an image of {len(decoded)} bytes; the bound is {MAX_IMAGE_BYTES}.",
        )
    return decoded


def _recipient(walk: SeatWalk, agent_id: str) -> tuple[SeatRecord, dict[str, Any]]:
    """The current launch record of the target, read whole; AR seats only."""

    try:
        minted = str(uuid.UUID(agent_id)) == agent_id
    except ValueError:
        minted = False
    record = walk.record_of(agent_id) if minted else None
    if record is None:
        unread = f" {walk.unread} launch record(s) could not be read." if walk.unread else ""
        raise _Refusal(
            "recipient-not-found", f"No current AR launch record names agent {agent_id}.{unread}"
        )
    try:
        receipt = _read_receipt(record.path)
    except HTTPException as error:
        raise _Refusal("recipient-not-found", str(error.detail)) from error
    if receipt is None or receipt.get("agentId") != agent_id:
        raise _Refusal("recipient-not-found", f"The launch record of agent {agent_id} changed.")
    return record, receipt


# ---------------------------------------------------------------------------------------------
# Composition and filing
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Target:
    record: SeatRecord
    receipt: dict[str, Any]
    task: dict[str, str] | None
    walk: SeatWalk


class _Composer:
    """The numbered picks of one message, with what goes natively beside its text."""

    def __init__(self, filing: MessageFiling, prepared: _Prepared, walk: SeatWalk) -> None:
        self.filing = filing
        self.prepared = prepared
        self.walk = walk
        self.lines: list[str] = []
        self.attachments: list[MessageAttachment] = []
        self.images: list[dict[str, str]] = []
        self.texts: list[dict[str, Any]] = []

    def add(self, index: int, pick: DeveloperPick) -> None:
        number = index + 1
        head = f"[{number}] {_pick_label(pick)} · {_one_line(pick.title)}"
        if pick.reference is not None:
            body = list(self.prepared.forms[index])
        elif pick.image is not None:
            body = [self._image(number, index, pick.image)]
        else:
            provenance = _provenance(pick, self.walk)
            head = f"{head} · {provenance}"
            body = [self._text(number, pick, provenance)]
        self.lines.append(head)
        self.lines.extend(f"    {line}" for line in body)
        note = pick.note.strip().splitlines()
        if note:
            self.lines.append(f"    note: {note[0]}")
            self.lines.extend(f"          {line}" for line in note[1:])

    def _image(self, number: int, index: int, image: ImagePick) -> str:
        data = self.prepared.images[index]
        path, digest = self.filing.save_blob(data, _IMAGE_SUFFIXES[image.mime_type])
        self.images.append({"data": image.data, "mimeType": image.mime_type})
        self.attachments.append(MessageAttachment(f"pick {number} image", digest, path.as_posix()))
        return f"attached as image {len(self.images)}; file {path.as_posix()} (SHA-256 {digest})"

    def _text(self, number: int, pick: DeveloperPick, provenance: str) -> str:
        text = pick.text or ""
        title = f"Pick {number} · {_one_line(pick.title)} · {provenance}"
        path, digest = self.filing.save_blob(text.encode("utf-8"), ".txt")
        self.texts.append({"type": "text", "mimeType": "text/plain", "title": title, "text": text})
        self.attachments.append(MessageAttachment(f"pick {number} text", digest, path.as_posix()))
        return f'attached as text "{title}"; file {path.as_posix()} (SHA-256 {digest})'


def _file(
    config: McpRuntimeConfig,
    request: DeveloperMessageRequest,
    prepared: _Prepared,
    target: _Target,
    place: tuple[Path, datetime],
) -> tuple[Path, str, DeveloperDelivery]:
    """Save the picks, file the message whole, and name what the bridge is handed.

    One filing: a failure before the message is placed leaves none of the picks it saved
    (45e5b434, :func:`message_filing`).
    """

    folder, when = place
    record = target.record
    message_id = str(request.message_id)
    task = target.task
    with message_filing(folder) as filing:
        composer = _Composer(filing, prepared, target.walk)
        for index, pick in enumerate(request.picks):
            composer.add(index, pick)
        head = [request.text.rstrip("\n"), ""] if request.text.strip() else []
        message = "\n".join([*head, *composer.lines]).rstrip("\n")
        path, digest = filing.file_message(
            message_stem(when, record.role, record.agent_id, message_id),
            MessageContent(
                when=when,
                sender_line=DEVELOPER_SENDER,
                sender_task="none",
                recipient_role=record.role,
                recipient_id=record.agent_id,
                recipient_task=task["id"] if task else "Projects",
                message_id=message_id,
                text=message,
                attachments=tuple(composer.attachments),
            ),
        )
    first_line = f"From developer · {task['id']}" if task else "From developer"
    named = file_line(path, digest)
    payload: dict[str, Any] = {
        "agentId": record.agent_id,
        "text": "\n".join(part for part in (first_line, message, named) if part),
        "messageId": message_id,
        **({"images": composer.images} if composer.images else {}),
        **({"attachments": composer.texts} if composer.texts else {}),
    }
    delivery = DeveloperDelivery(
        config,
        record.agent_id,
        record.path,
        target.receipt,
        first_line,
        _held_text(message, named),
        payload,
    )
    return path, digest, delivery


def _held_text(message: str, named: str) -> str:
    """What a held entry keeps under the first line: the message, cut at a bound, and its file.

    A held store keeps at most twenty messages for one recipient (MIK-R100 rule 4a); this bound
    keeps their bytes bounded too. Nothing is lost by the cut: the file holds the whole message.
    """

    if len(message) > HELD_TEXT_LIMIT:
        message = (
            f"{message[:HELD_TEXT_LIMIT]}\n[cut at {HELD_TEXT_LIMIT} characters: the whole "
            "message is in its file]"
        )
    return "\n".join(part for part in (message, named) if part)


def _pick_label(pick: DeveloperPick) -> str:
    if pick.reference is not None:
        return _KIND_LABELS[pick.reference.kind]
    return pick.kind


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _provenance(pick: DeveloperPick, walk: SeatWalk) -> str:
    source = pick.provenance
    if isinstance(source, PanelProvenance):
        return f"from the dashboard panel {_one_line(source.panel)}"
    assert isinstance(source, ChatProvenance)
    seat = walk.record_of(source.agent_id)
    who = f"{seat.role} (agent {source.agent_id})" if seat else f"agent {source.agent_id}"
    # The time is checked before anything is filed (rule 3, ebdae1e5): it is always one.
    return f"from the chat of {who} · {_one_line(source.message_time or '')}"


# ---------------------------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------------------------


async def _bounded_body(request: Request) -> bytes | None:
    """The body when it holds at most ``MAX_BODY_BYTES``; None, read no further, when larger.

    As ``serving/canvas.py``'s ``_bounded_raw``: a declared length decides before anything is
    read, and a stream is read only up to one byte past the bound.
    """

    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        return None
    payload = bytearray()
    async for chunk in request.stream():
        payload.extend(chunk[: MAX_BODY_BYTES + 1 - len(payload)])
        if len(payload) > MAX_BODY_BYTES:
            return None
    return bytes(payload)


def _invalid(error: ValidationError) -> dict[str, Any]:
    """A body of another shape, refused by name with what the parser said (at most ten points)."""

    points = error.errors(include_url=False, include_input=False, include_context=False)
    detail = "; ".join(
        f"{'.'.join(str(part) for part in point['loc']) or 'body'}: {point['msg']}"
        for point in points[:10]
    )
    return _answer("refused", None, None, reason="invalid-request", detail=detail[:2000])


def register_developer_chat_routes(
    app: FastAPI, config: McpRuntimeConfig, *, leaf_memory_root: LeafMemoryRoot | None = None
) -> None:
    """The tray's server routes: the seat list, the developer's send and the sent-picks log.

    ``leaf_memory_root`` resolves a review comparison's leaf memory worktree for the written
    forms; the dashboard composes its own review resolver here.
    """

    register_seat_list_route(app, config)

    async def send(agent_id: str, request: Request) -> JSONResponse:
        raw = await _bounded_body(request)
        if raw is None:
            return JSONResponse(
                _answer(
                    "refused",
                    None,
                    None,
                    reason="message-too-large",
                    detail=f"The request body is over {MAX_BODY_BYTES} bytes, more than a message "
                    "within its bounds needs; it was not read further.",
                )
            )
        try:
            body = DeveloperMessageRequest.model_validate_json(raw)
        except ValidationError as error:
            return JSONResponse(_invalid(error))
        answer = await asyncio.to_thread(
            send_developer_message, config, agent_id, body, leaf_memory_root=leaf_memory_root
        )
        return JSONResponse(answer)

    def sent(agent_id: uuid.UUID, limit: int = Query(default=50, ge=1, le=500)) -> JSONResponse:
        return JSONResponse(read_sent(config, str(agent_id), limit))

    app.add_api_route("/api/role-chats/{agent_id}/message", send, methods=["POST"])
    app.add_api_route("/api/role-chats/{agent_id}/sent", sent, methods=["GET"])
