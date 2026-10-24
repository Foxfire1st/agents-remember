"""Deterministic explicit harness fixture and shared host port records (MIK-R105).

One implementation behind :class:`agents_remember.canvas.presenter.PresenterRunner`, doing two
things at once:

* **confined authoring.** One attempt gets one prepared folder. The harness writes only presentation
  output below ``<working>/assets`` and session bookkeeping below ``<working>/host``; every other
  target is refused by name (``native-scope-escape``). It reads exactly the three admitted inputs --
  evidence packet, design text, capability text -- each verified against the attempt's recorded
  digest, and refuses any other read (``foreign-scope-read``). An instruction saying "do not write
  elsewhere" is not a boundary; this writer has no other target it can name.
* **identity-bound host operations.** ``start``, ``message``, ``status``, ``transcript``,
  ``resume``, ``cancel`` and ``archive`` each name one host session and answer with a receipt the
  presenter records. Session state lives in the attempt's folder, so a restarted product reconciles
  the same session instead of inventing a second one. Nothing is implicit.

Production wires no presenter harness: the capability's native launch was retired with its
dedicated tool server (MIK-R186 @ v2 Scope), so a presenter constructed with no runner refuses as
:class:`~agents_remember.canvas.presenter.UnconfiguredPresenterRunner`. This local harness is only
selected explicitly by callers such as deterministic tests and supplies no native-host or
model-authoring qualification.

The draft is named for what it is: ``static-html-readiness`` over the authored bytes at a stated
viewport, with the check's own errors; no browser is launched and no result claims one observed it.
Documentation is a separate read-only surface and never review evidence. The harness is local and
deterministic on purpose: the product runs and is tested without an external host agent. It is not
a model -- it composes from the admitted packet -- so a richer page still needs a real host agent.
"""

from __future__ import annotations

import html
import json
import os
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar, Literal

from agents_remember.canvas.paths import (
    PathRefused,
    design_text_source,
    ensure_directory,
    guard_path,
    path_segment,
    presenter_root,
    presenter_work_base,
    presenter_working_root,
    task_root,
)
from agents_remember.canvas.presenter_files import write_output
from agents_remember.canvas.presenter_store import (
    MAX_MESSAGE_CHARS,
    MAX_RECORD_BYTES,
    PresenterAttempt,
    PresenterRefused,
    RunnerReceipt,
    digest_bytes,
    read_bounded,
)
from agents_remember.canvas.readiness import check_page
from agents_remember.kernel.canonical_json import canonical_json_bytes
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.knowledge.review_evidence import EvidencePacket

__all__ = [
    "DEFAULT_VIEWPORT",
    "SESSION_SCHEMA",
    "DocumentationTools",
    "DraftRender",
    "HarnessRefused",
    "HostIdentity",
    "HostReceipt",
    "HostStatus",
    "HostTurn",
    "HostWorkspace",
    "InputGrant",
    "LocalPresenterHarness",
    "compose_page",
    "render_draft",
]

DEFAULT_VIEWPORT = (1280, 720)
SESSION_SCHEMA = "ar-review-presenter-host-session/v1"
MAX_TRANSCRIPT_TURNS = 200
MAX_INPUT_BYTES = MAX_RECORD_BYTES
MAX_DRAFT_DEPTH = 8
MAX_DRAFT_ENTRIES = 1024
MAX_DRAFT_BYTES = 4 * 1024 * 1024

DEFAULT_ACCENT = "#2f6f4f"
_ACCENT = re.compile(r"--accent\s*:\s*(#[0-9a-fA-F]{3,8})\b")
_WORD = re.compile(r"[a-z0-9]+")
_LANES = {"developer": "developer", "presenter": "presenter", "system-owner": "system-owner"}
_TALKABLE_STATES = frozenset({"running", "published"})


class HarnessRefused(PresenterRefused):
    """The confined harness refused one act and named why. Nothing outside it was touched."""


@dataclass(frozen=True)
class HostIdentity:
    repository_id: str
    task: str
    leaf_id: str
    frame_id: str


@dataclass(frozen=True)
class InputGrant:
    role: Literal["evidence", "design", "instructions"]
    path: Path
    sha256: str


@dataclass(frozen=True)
class HostTurn:
    at: str
    role: str
    lane: str
    text: str


@dataclass(frozen=True)
class HostReceipt:
    """What one host operation answered. The presenter records this; it does not invent it."""

    session_id: str
    message_id: str
    state: str
    detail: str = ""
    turns: tuple[HostTurn, ...] = ()
    execution_id: str | None = None


@dataclass(frozen=True)
class HostStatus:
    session_id: str
    attempt_id: str
    state: str
    turns: int | None
    execution_id: str
    model: str | None
    updated_at: str
    detail: str
    archived: bool = False


@dataclass(frozen=True)
class DraftRender:
    """The named draft result: surface, size, and the check's own failures. No browser claimed."""

    state: Literal["draft-ready", "draft-not-ready"]
    surface: str
    width: int
    height: int
    entrypoint: str
    errors: tuple[str, ...] = ()
    browser: str | None = None
    detail: str = ""

    @property
    def ready(self) -> bool:
        return self.state == "draft-ready"

    @property
    def size(self) -> str:
        return f"{self.width}x{self.height}"


@dataclass(frozen=True)
class _ClaimLine:
    claim_id: str
    subject: str
    statement: str
    classification: str
    references: tuple[str, ...]
    states: tuple[str, ...]
    limit: str | None = None


class HostWorkspace:
    """One attempt's prepared folder: the only place this harness may write.

    ``assets`` holds presentation output, ``host`` session bookkeeping; anything else is a
    native-scope escape.
    """

    def __init__(
        self, working: Path, *, inputs: tuple[InputGrant, ...], protected: tuple[Path, ...]
    ) -> None:
        self.working = working
        self.inputs = inputs
        self._protected = tuple(path.resolve() for path in protected)
        self.assets_root = working / "assets"
        self.host_root = working / "host"

    def prepare(self) -> None:
        for target, label in ((self.assets_root, "assets folder"), (self.host_root, "host folder")):
            try:
                ensure_directory(self.working, target, label=label)
            except PathRefused as refusal:
                raise HarnessRefused("native-scope-escape", str(refusal)) from refusal

    def read_input(self, path: Path | str) -> bytes:
        resolved = Path(path).resolve()
        for grant in self.inputs:
            if grant.path.resolve() == resolved:
                return _read_bounded_file(grant.path, MAX_INPUT_BYTES, expected=grant.sha256)
        raise HarnessRefused(
            "foreign-scope-read",
            f"{path} is not one of this attempt's admitted inputs; it reads exactly "
            f"{', '.join(grant.role for grant in self.inputs)}",
        )

    def write_output(self, relative: str, payload: bytes) -> Path:
        return self._write(self.assets_root, relative, payload, label="presentation output")

    def write_bookkeeping(self, name: str, payload: bytes) -> Path:
        if "/" in name or "\\" in name or name in {".", ".."}:
            raise HarnessRefused("native-scope-escape", f"{name!r} is not one host file name")
        return self._write(self.host_root, name, payload, label="host bookkeeping")

    def _write(self, root: Path, relative: str, payload: bytes, *, label: str) -> Path:
        target = _admit_relative(root, relative)
        self._refuse_protected(target)
        try:
            ensure_directory(root, target.parent, label=label)
            guard_path(root, target, label=label)
        except PathRefused as refusal:
            raise HarnessRefused("native-scope-escape", str(refusal)) from refusal
        write_output(root, relative, payload)
        return target

    def _refuse_protected(self, target: Path) -> None:
        resolved = target.resolve()
        for protected in self._protected:
            if resolved == protected or protected in resolved.parents:
                raise HarnessRefused(
                    "native-scope-escape",
                    f"{target} is inside the product's own area at {protected}; the harness "
                    "writes only presentation output and its own session bookkeeping",
                )


class DocumentationTools:
    """Guidance for the author, never review evidence: no claim, reference or asset identity."""

    evidence = False
    surface = "documentation"

    _TOPICS: ClassVar[dict[str, str]] = {
        "capability": "the shipped review-presenter text, read from the package",
        "authoring": (
            "A draft comes from the admitted evidence packet only. It writes presentation output "
            "below its own assets folder and may not touch the memory layer, repository, task "
            "record or another attempt's folder."
        ),
        "publication": (
            "A draft becomes a ready revision only through the frame store's confined writer, "
            "after it loads once without a model and every readiness check passes."
        ),
        "isolation": (
            "The published page is served from the frame store under the canvas host's isolation "
            "policy; this documentation is not served with it or read as its input."
        ),
    }

    def topics(self) -> tuple[str, ...]:
        return tuple(self._TOPICS)

    def read(self, topic: str) -> str:
        if topic not in self._TOPICS:
            raise HarnessRefused(
                "unknown-documentation-topic", f"{topic!r} is not one of {', '.join(self._TOPICS)}"
            )
        return _shipped_instructions() if topic == "capability" else self._TOPICS[topic]

    def as_review_evidence(self) -> bytes:
        raise HarnessRefused(
            "not-review-evidence",
            "documentation guides the author; it carries no claim, no evidence reference and no "
            "asset, so no review evidence is read from it",
        )


class SessionShelf:
    """The session files of one page: load, save and the identity-bound host operations."""

    def __init__(
        self,
        *,
        working_for: Callable[[str], Path],
        protected: Callable[[], tuple[Path, ...]],
        now: Callable[[], str],
    ) -> None:
        self._working_for = working_for
        self._protected = protected
        self._now = now

    def message(self, session_id: str, *, role: str, text: str) -> HostReceipt:
        """Record one turn; a developer turn is answered from the authored claims."""

        if role not in _LANES:
            raise HarnessRefused("unknown-role", f"{role!r} is not a conversation role")
        spoken = text.strip()
        if not spoken:
            raise HarnessRefused("empty-message", "a conversation turn carries text")
        if len(spoken) > MAX_MESSAGE_CHARS:
            raise HarnessRefused(
                "message-too-long", f"the turn is longer than {MAX_MESSAGE_CHARS} characters"
            )
        workspace, payload = self.load(session_id)
        if payload["state"] not in _TALKABLE_STATES:
            raise HarnessRefused(
                "session-settled",
                f"session {session_id} is {payload['state']}; it accepts no further turn",
            )
        now = self._now()
        turns = [_turn(now, role, _LANES[role], spoken)]
        if role == "developer":
            turns.append(_turn(now, "presenter", "developer", _answer(payload, spoken)))
        payload["turns"] = ((payload.get("turns") or []) + turns)[-MAX_TRANSCRIPT_TURNS:]
        payload["updated_at"] = now
        payload["detail"] = f"answered a {role} turn; {len(turns) - 1} presenter turn(s) recorded"
        self.save(workspace, payload)
        stamp = digest_bytes(f"{session_id}:{now}:{len(payload['turns'])}".encode())[:12]
        return _receipt(payload, f"msg-{stamp}", payload["state"], payload["detail"], turns)

    def status(self, session_id: str) -> HostStatus:
        _workspace, payload = self.load(session_id)
        return HostStatus(
            session_id=session_id,
            attempt_id=payload["attempt_id"],
            state=payload["state"],
            turns=len(payload.get("turns") or ()),
            execution_id=payload["execution_id"],
            model=payload.get("model"),
            updated_at=payload["updated_at"],
            detail=payload.get("detail") or "",
            archived=bool(payload.get("archived_at")),
        )

    def transcript(self, session_id: str) -> tuple[HostTurn, ...]:
        _workspace, payload = self.load(session_id)
        return tuple(HostTurn(**turn) for turn in payload.get("turns") or ())

    def resume(self, session_id: str) -> HostReceipt:
        """Re-attach to the recorded session; the identity does not change and no turn starts."""

        workspace, payload = self.load(session_id)
        if payload["state"] in {"cancelled", "failed"}:
            raise HarnessRefused(
                "not-resumable", f"session {session_id} is {payload['state']}, not an open session"
            )
        now = self._now()
        payload["resumed_at"] = now
        payload["resume_count"] = int(payload.get("resume_count") or 0) + 1
        payload["updated_at"] = now
        payload["detail"] = "resumed the recorded session; no new turn was started"
        self.save(workspace, payload)
        return _receipt(payload, f"resume-{payload['resume_count']}", payload["state"])

    def cancel(self, session_id: str, *, reason: str) -> HostReceipt:
        """Cancel the named session; a repeat reports it was already cancelled."""

        workspace, payload = self.load(session_id)
        if payload["state"] == "cancelled":
            return _receipt(payload, "cancel-0", "cancelled", "already cancelled; nothing changed")
        now = self._now()
        payload.update(
            {
                "state": "cancelled",
                "cancel_reason": reason,
                "cancelled_at": now,
                "updated_at": now,
                "detail": f"host cancellation recorded: {reason}",
            }
        )
        self.save(workspace, payload)
        return _receipt(payload, "cancel-1", "cancelled")

    def archive(self, session_id: str) -> HostReceipt:
        """Store the transcript beside the session; it is bookkeeping, not a page asset."""

        workspace, payload = self.load(session_id)
        if payload.get("archived_at"):
            target = workspace.host_root / "archive.json"
            return _receipt(payload, "archive-0", "archived", str(target))
        now = self._now()
        archive = {
            "schema": SESSION_SCHEMA,
            "session_id": session_id,
            "attempt_id": payload["attempt_id"],
            "execution_id": payload["execution_id"],
            "archived_at": now,
            "turns": payload.get("turns") or [],
        }
        path = workspace.write_bookkeeping("archive.json", canonical_json_bytes(archive))
        payload["archived_at"] = now
        payload["updated_at"] = now
        self.save(workspace, payload)
        return _receipt(payload, "archive-1", "archived", str(path))

    def load(self, session_id: str) -> tuple[HostWorkspace, dict[str, Any]]:
        attempt_id = attempt_id_from(session_id)
        try:
            working = self._working_for(attempt_id)
            payload = json.loads(
                _read_bounded_file(working / "host" / "session.json", MAX_RECORD_BYTES)
            )
        except (PathRefused, OSError, ValueError, PresenterRefused) as error:
            raise HarnessRefused(
                "unknown-session",
                f"no host session {session_id!r} is recorded for attempt {attempt_id!r}: {error}",
            ) from error
        if not isinstance(payload, dict):
            raise HarnessRefused("unknown-session", "the session file is not one session record")
        if payload.get("session_id") != session_id or payload.get("attempt_id") != attempt_id:
            raise HarnessRefused(
                "session-identity-mismatch",
                f"the session file names {payload.get('session_id')!r}/"
                f"{payload.get('attempt_id')!r}, not {session_id!r}/{attempt_id!r}",
            )
        workspace = HostWorkspace(
            working, inputs=_grants_from(payload), protected=self._protected()
        )
        return workspace, payload

    def save(self, workspace: HostWorkspace, payload: dict[str, Any]) -> None:
        workspace.write_bookkeeping("session.json", canonical_json_bytes(payload))


class LocalPresenterHarness(SessionShelf):
    """The shipped confined harness: deterministic authoring plus the host session operations."""

    def __init__(
        self, config: McpRuntimeConfig, *, identity: HostIdentity, clock: Any = None
    ) -> None:
        self._config = config
        self._identity = identity
        self._clock = clock
        self.documentation = DocumentationTools()
        super().__init__(working_for=self._working, protected=self._protected, now=self._now)

    def _now(self) -> str:
        if self._clock is None:
            return datetime.now(UTC).isoformat().replace("+00:00", "Z")
        return str(self._clock())

    def _working(self, attempt_id: str) -> Path:
        identity = self._identity
        return presenter_working_root(
            self._config,
            repository_id=identity.repository_id,
            task=identity.task,
            leaf_id=identity.leaf_id,
            frame_id=identity.frame_id,
            attempt_id=attempt_id,
        )

    def _evidence_path(self, attempt_id: str) -> Path:
        identity = self._identity
        task = task_root(self._config, identity.repository_id, identity.task)
        leaf = path_segment(identity.leaf_id, name="leaf")
        frame = path_segment(identity.frame_id, name="frame")
        return presenter_root(task) / leaf / frame / f"{attempt_id}.evidence.json"

    def _protected(self) -> tuple[Path, ...]:
        identity = self._identity
        task = task_root(self._config, identity.repository_id, identity.task)
        scope = self._config.repositories.get(identity.repository_id)
        roots = [presenter_root(task), task]
        if scope is not None:
            roots.append(Path(scope.path))
            if scope.memory_root is not None:
                roots.append(Path(scope.memory_root))
        return tuple(roots)

    # -- authoring -----------------------------------------------------------------------------

    def start(self, attempt: PresenterAttempt, instructions: str, working: Path) -> RunnerReceipt:
        """Prepare the attempt folder, author its page, and record one host session."""

        workspace, packet, design = self._prepare(attempt, working)
        claims = _claim_lines(packet)
        assets = compose_page(attempt=attempt, claims=claims, design=design)
        for relative, payload in assets.items():
            workspace.write_output(relative, payload)
        render = render_draft(assets, packet=packet)
        if not render.ready:
            raise HarnessRefused(
                "draft-not-ready",
                "the authored draft failed its static readiness check: " + "; ".join(render.errors),
            )
        seed = _SessionSeed(
            attempt=attempt,
            now=self._now(),
            grants=workspace.inputs,
            claims=claims,
            render=render,
            assignment_sha256=digest_bytes(instructions.encode()),
        )
        payload = _new_session(seed)
        self.save(workspace, payload)
        return RunnerReceipt(
            execution_id=payload["execution_id"],
            session_id=payload["session_id"],
            detail=(
                f"draft {render.state} at {render.size} via {render.surface}: "
                f"{len(claims)} claim(s)"
            ),
        )

    def _prepare(
        self, attempt: PresenterAttempt, working: Path
    ) -> tuple[HostWorkspace, EvidencePacket, str]:
        self._admit_working(attempt, working)
        grants, packet, design = self._inputs(attempt)
        workspace = HostWorkspace(working, inputs=grants, protected=self._protected())
        try:
            workspace.prepare()
            workspace.write_bookkeeping(
                "inputs.json",
                canonical_json_bytes(
                    [
                        {"role": grant.role, "path": str(grant.path), "sha256": grant.sha256}
                        for grant in grants
                    ]
                ),
            )
        except OSError as error:
            raise HarnessRefused(
                "unprepared-working-folder", f"the attempt folder could not be prepared: {error}"
            ) from error
        return workspace, packet, design

    def _admit_working(self, attempt: PresenterAttempt, working: Path) -> None:
        expected = self._working(attempt.attempt_id)
        if Path(working).resolve() != expected.resolve():
            raise HarnessRefused(
                "foreign-working-folder",
                f"{working} is not attempt {attempt.attempt_id}'s prepared folder {expected}",
            )
        try:
            guard_path(presenter_work_base(self._config), Path(working), label="attempt folder")
        except PathRefused as refusal:
            raise HarnessRefused("unsafe-working-folder", str(refusal)) from refusal
        if not Path(working).is_dir():
            raise HarnessRefused(
                "unprepared-working-folder",
                f"the attempt folder {working} was not prepared by the product",
            )

    def _inputs(
        self, attempt: PresenterAttempt
    ) -> tuple[tuple[InputGrant, ...], EvidencePacket, str]:
        identity = self._identity
        source, design = design_text_source(self._config, identity.repository_id)
        if design is None:
            raise HarnessRefused(
                "design-input-unreadable", f"the {source} design text could not be read"
            )
        grants = (
            InputGrant(
                role="evidence",
                path=self._evidence_path(attempt.attempt_id),
                sha256=attempt.evidence_digest,
            ),
            InputGrant(
                role="design",
                path=_design_path(self._config, identity.repository_id, source),
                sha256=attempt.design_sha256,
            ),
            InputGrant(
                role="instructions", path=_instructions_path(), sha256=attempt.instructions_sha256
            ),
        )
        evidence_bytes = _read_bounded_file(
            grants[0].path, MAX_RECORD_BYTES, expected=attempt.evidence_digest
        )
        try:
            packet = EvidencePacket.model_validate_json(evidence_bytes)
        except ValueError as error:
            raise HarnessRefused(
                "evidence-input-unreadable", f"the admitted packet is not readable: {error}"
            ) from error
        for grant in grants[1:]:
            _read_bounded_file(grant.path, MAX_INPUT_BYTES, expected=grant.sha256)
        return grants, packet, design

    # -- draft and explicit probes --------------------------------------------------------------

    def render(self, session_id: str) -> DraftRender:
        """Re-run the draft's static check over the authored bytes on disk."""

        workspace, payload = self.load(session_id)
        evidence = next(grant for grant in payload["grants"] if grant["role"] == "evidence")
        packet = EvidencePacket.model_validate_json(
            _read_bounded_file(
                Path(evidence["path"]), MAX_RECORD_BYTES, expected=evidence["sha256"]
            )
        )
        return render_draft(_read_assets(workspace.assets_root), packet=packet)

    def write_output(self, session_id: str, relative: str, payload: bytes) -> Path:
        """The authoring write path, addressable by session so an escape can be probed by name."""

        workspace, _payload = self.load(session_id)
        return workspace.write_output(relative, payload)

    def read_input(self, session_id: str, path: Path | str) -> bytes:
        workspace, _payload = self.load(session_id)
        return workspace.read_input(path)


def compose_page(
    *, attempt: PresenterAttempt, claims: tuple[_ClaimLine, ...], design: str
) -> dict[str, bytes]:
    """Compose the presentation-only page: one document, one stylesheet, no scripts."""

    title = f"the change to {attempt.focus}" if attempt.focus else "the explained change"
    accent = _ACCENT.search(design)
    return {
        "index.html": _PAGE.format(
            title=html.escape(title),
            comparison=html.escape(attempt.comparison.comparison_reference),
            focus=html.escape(attempt.focus or "the selected change"),
            claims="\n".join(_claim_markup(claim) for claim in claims),
        ).encode(),
        "app.css": _STYLESHEET.format(
            accent=accent.group(1) if accent else DEFAULT_ACCENT
        ).encode(),
    }


def render_draft(
    assets: dict[str, bytes], *, packet: EvidencePacket, entrypoint: str = "index.html"
) -> DraftRender:
    """The named draft result: the static readiness check at a stated viewport."""

    report = check_page(assets, entrypoint=entrypoint, packet=packet)
    return DraftRender(
        state="draft-ready" if report.ready else "draft-not-ready",
        surface="static-html-readiness",
        width=DEFAULT_VIEWPORT[0],
        height=DEFAULT_VIEWPORT[1],
        entrypoint=entrypoint,
        errors=tuple(f"{check.name}: {check.detail}" for check in report.failures),
        browser=None,
        detail=(
            "no browser was launched; this result is the static readiness check over the "
            "authored bytes, not a rendering a browser observed"
        ),
    )


def _claim_markup(claim: _ClaimLine) -> str:
    citations = "\n".join(
        f'<li data-evidence="{html.escape(ref)}">{html.escape(ref)} ({html.escape(state)})</li>'
        for ref, state in zip(claim.references, claim.states, strict=False)
    )
    limit = f'<p class="limit">{html.escape(claim.limit)}</p>' if claim.limit else ""
    return (
        f'<article class="claim" data-claim="{html.escape(claim.claim_id)}">'
        f"<h3>{html.escape(claim.subject)}</h3><p>{html.escape(claim.statement)}</p>"
        f'<p class="classification">{html.escape(claim.classification)}</p>'
        f"{limit}<ul>{citations}</ul></article>"
    )


def _claim_lines(packet: EvidencePacket) -> tuple[_ClaimLine, ...]:
    return tuple(
        _ClaimLine(
            claim_id=claim.claim_id,
            subject=claim.subject,
            statement=claim.statement,
            classification=claim.classification,
            references=tuple(basis.reference for basis in claim.bases),
            states=tuple(sorted({basis.state for basis in claim.bases})),
            limit=claim.limit,
        )
        for claim in packet.claims.claims
    )


@dataclass(frozen=True)
class _SessionSeed:
    """The fact set a start records, kept together so the builder stays one argument."""

    attempt: PresenterAttempt
    now: str
    grants: tuple[InputGrant, ...]
    claims: tuple[_ClaimLine, ...]
    render: DraftRender
    assignment_sha256: str


def _new_session(seed: _SessionSeed) -> dict[str, Any]:
    attempt, render = seed.attempt, seed.render
    return {
        "schema": SESSION_SCHEMA,
        "session_id": f"sess-{attempt.attempt_id}",
        "attempt_id": attempt.attempt_id,
        "request_id": attempt.request_id,
        "execution_id": f"exec-{attempt.attempt_id}",
        "model": attempt.model,
        "state": "running",
        "created_at": seed.now,
        "updated_at": seed.now,
        "resume_count": 0,
        "archived_at": None,
        "grants": [{"role": g.role, "path": str(g.path), "sha256": g.sha256} for g in seed.grants],
        "assignment_sha256": seed.assignment_sha256,
        "claims": [asdict(c) for c in seed.claims],
        "render": asdict(render) | {"size": render.size},
        "turns": [
            {
                "at": seed.now,
                "role": "system-owner",
                "lane": "system-owner",
                "text": (
                    f"the draft is authored; {render.state} at {render.size} ({render.surface})"
                ),
            }
        ],
        "detail": f"draft {render.state} at {render.size}",
    }


def _receipt(
    payload: dict[str, Any],
    message_id: str,
    state: str,
    detail: str | None = None,
    turns: list[dict[str, str]] | None = None,
) -> HostReceipt:
    return HostReceipt(
        session_id=payload["session_id"],
        message_id=message_id,
        state=state,
        detail=payload.get("detail", "") if detail is None else detail,
        turns=tuple(HostTurn(**turn) for turn in turns or ()),
        execution_id=payload["execution_id"],
    )


def _turn(at: str, role: str, lane: str, text: str) -> dict[str, str]:
    return {"at": at, "role": role, "lane": lane, "text": text}


def _answer(payload: dict[str, Any], question: str) -> str:
    """Answer a developer turn from the authored claims, not from a second reading."""

    claims = payload.get("claims") or []
    words = {word for word in _WORD.findall(question.lower()) if len(word) > 3}
    match = next(
        (
            claim
            for claim in claims
            if words & set(_WORD.findall(f"{claim['subject']} {claim['statement']}".lower()))
        ),
        None,
    )
    if match is None:
        refs = sorted({ref for claim in claims for ref in claim["references"]})
        return f"the draft carries {len(claims)} claim(s); it cites {', '.join(refs) or 'none'}."
    limit = f" The admitted limitation is: {match['limit']}." if match.get("limit") else ""
    return (
        f"{match['subject']}: {match['statement']} It rests on "
        f"{', '.join(match['references']) or 'no reference'} "
        f"({', '.join(match['states']) or 'unstated'}).{limit}"
    )


def _grants_from(payload: dict[str, Any]) -> tuple[InputGrant, ...]:
    return tuple(
        InputGrant(role=grant["role"], path=Path(grant["path"]), sha256=grant["sha256"])
        for grant in payload.get("grants") or ()
    )


def attempt_id_from(session_id: str) -> str:
    if not isinstance(session_id, str) or not session_id.startswith("sess-att-"):
        raise HarnessRefused(
            "unknown-session",
            f"{session_id!r} is not one host session of this page; a session identity is granted "
            "by start, never parsed from user text",
        )
    return session_id[len("sess-") :]


def _admit_relative(root: Path, relative: str) -> Path:
    if not relative or "\\" in relative:
        raise HarnessRefused(
            "native-scope-escape", f"{relative!r} is not one relative presentation path"
        )
    candidate = PurePosixPath(relative)
    if candidate.is_absolute() or any(part in {"..", "."} for part in candidate.parts):
        raise HarnessRefused(
            "native-scope-escape",
            f"{relative!r} leaves the attempt's own write area; the harness has no other target",
        )
    return root.joinpath(*candidate.parts)


def _design_path(config: McpRuntimeConfig, repository_id: str, source: str) -> Path:
    if source == "repository":
        scope = config.repositories[repository_id]
        if scope.memory_root is None:
            raise HarnessRefused("design-input-unreadable", "the repository has no memory root")
        return Path(scope.memory_root) / "system" / "artifact-design.md"
    return _package_path("package_data", "canvas", "artifact-design.md")


def _instructions_path() -> Path:
    return _package_path("canvas", "review-presenter.md")


def _package_path(*parts: str) -> Path:
    return Path(str(resources.files("agents_remember").joinpath(*parts)))


def _shipped_instructions() -> str:
    try:
        return _instructions_path().read_text(encoding="utf-8")
    except OSError as error:
        raise HarnessRefused("documentation-unavailable", f"not shipped: {error}") from error


def _read_bounded_file(path: Path, limit: int, *, expected: str | None = None) -> bytes:
    try:
        payload = read_bounded(
            path, limit, reason="unreadable-input", detail=f"{path} could not be read"
        )
    except PresenterRefused as refusal:
        raise HarnessRefused(refusal.reason, refusal.detail) from refusal
    if expected is not None and digest_bytes(payload) != expected:
        raise HarnessRefused(
            "input-digest-mismatch",
            f"{path} is not the input the attempt admitted (recorded {expected}); the harness "
            "does not author from bytes the attempt never saw",
        )
    return payload


def _read_assets(root: Path) -> dict[str, bytes]:
    assets: dict[str, bytes] = {}
    total = 0
    for path in _walk(root):
        payload = _read_bounded_file(path, MAX_DRAFT_BYTES - total)
        total += len(payload)
        assets[path.relative_to(root).as_posix()] = payload
    return assets


def _walk(root: Path) -> list[Path]:
    found: list[Path] = []
    seen = 0
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        if len(Path(directory).relative_to(root).parts) > MAX_DRAFT_DEPTH:
            raise HarnessRefused("draft-too-deep", "the draft is nested beyond its depth bound")
        for name in dirnames + filenames:
            seen += 1
            path = Path(directory) / name
            if path.is_symlink():
                raise HarnessRefused("symlinked-draft-asset", f"the draft holds a symlink: {path}")
            if path.is_file():
                found.append(path)
    if seen > MAX_DRAFT_ENTRIES:
        raise HarnessRefused("too-many-draft-entries", "the draft holds too many entries")
    return sorted(found)


_PAGE = (
    '<!doctype html><html lang="en"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width, initial-scale=1">'
    '<title>{title}</title><link rel="stylesheet" href="app.css"></head>'
    '<body><main data-comparison="{comparison}"><h1>{title}</h1>'
    '<p class="focus">{focus}</p><section aria-label="the admitted claims">'
    "{claims}</section><footer>review presenter draft</footer></main></body></html>"
)

_STYLESHEET = (
    "/* authored from the admitted evidence; the design text steers appearance only */\n"
    ":root {{ --accent: {accent}; --ink: #16181d; --paper: #f7f5f0; }}\n"
    "* {{ box-sizing: border-box; }}\n"
    "body {{ margin: 0; background: var(--paper); color: var(--ink); font: 16px/1.55 monospace; }}\n"
    "main {{ max-width: 68ch; margin: 0 auto; padding: 3rem 1.5rem; }}\n"
    "h1 {{ font-size: 1.8rem; border-bottom: 2px solid var(--accent); padding-bottom: .6rem; }}\n"
    ".focus {{ color: #4a4f59; }}\n"
    ".claim {{ border-left: 3px solid var(--accent); padding: .4rem 0 .4rem 1rem; margin: 1.6rem 0; }}\n"
    ".claim h3 {{ margin: 0 0 .4rem; font-size: 1.05rem; }}\n"
    ".classification {{ color: var(--accent); font-size: .8rem; text-transform: uppercase; }}\n"
    ".limit {{ color: #7a4a1f; font-style: italic; }}\n"
    "ul {{ padding-left: 1.1rem; font-size: .85rem; }}\n"
    "footer {{ margin-top: 3rem; color: #6b7280; font-size: .8rem; }}\n"
)
