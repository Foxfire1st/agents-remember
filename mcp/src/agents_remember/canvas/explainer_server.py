"""The explainer's own tool server: the kept authoring tools, the reads, and its two writes.

The bridge admits exactly one tool server with ``tools: ''``, so this module is the seat's whole
authority (MIK-R186 rule 3). It composes:

* **kept** from the presenter (MIK-R105 rule 5), with the same names and behaviour: ``inputs``,
  ``read_input``, ``source``, ``assets``, ``write_asset``, ``carry_kit``, ``documentation`` and
  ``render``;
* **the read surfaces** this seat adds, from :mod:`agents_remember.canvas.explainer_access`:
  ``list``, ``search`` and ``read``; the six read-only Git subcommands; and ``knowledge_read``'s
  views at the bound memory tree;
* **the writes** this seat adds: MIK-R187 rule 2's ``define_category``, ``remove_category`` and
  ``grade``, written through the one grade store under the leaf's lock alone; and MIK-R189 rule
  7's ``report_to_architect``, one report per filed tray message.

Only the leaf's explainer may call the grade tools and the report. The caller identity is read
from this server's own environment at construction (``PASEO_AGENT_ID``), never from a tool
argument, and it must be the id of the leaf's current generation; ``AR_EXPLAINER_WORKSPACE``, when
set, must name this workspace (MIK-R186 rule 5). A grade carries the binding it was made at and is
refused ``binding-moved`` when the environment has left it, checked again under the lock. The
master-to-architect record is the Manager's and the architect's: this module reads it and never
writes it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from agents_remember.application.master_architect_record import (
    architect_receipt,
)
from agents_remember.application.review_frame_store import (
    READY_POINTER,
    FrameRefused,
    begin_draft,
    frame_scope,
    frames_area,
    publish_revision,
)
from agents_remember.canvas.explainer_access import (
    binding_face,
    require_binding,
    require_claim,
    require_seat,
)
from agents_remember.canvas.explainer_grades import (
    MAX_GRADE_CHARACTERS,
    GradeStore,
    ScalePoint,
    grades_path,
)
from agents_remember.canvas.explainer_inputs import (  # re-exported: __all__ names ExplainerInputs
    ExplainerInputs,
    coordination_of,
    now_iso,
)
from agents_remember.canvas.explainer_paths import (
    DRAFTS_DIRECTORY,
    EXPLAINER_FRAME,
    INSTRUCTIONS_DIRECTORY,
    LOCK_RESOURCE,
    leaf_lock_resource,
)
from agents_remember.canvas.explainer_state import (
    BUSY,
    ExplainerBinding,
    ExplainerRefused,
    read_binding,
)
from agents_remember.canvas.explainer_target import ExplainerTargetReads
from agents_remember.canvas.paths import path_segment
from agents_remember.canvas.presenter_assets import collect_assets
from agents_remember.canvas.presenter_files import ensure_child
from agents_remember.canvas.presenter_store import (
    MAX_RECORD_BYTES,
    PresenterRefused,
    digest_bytes,
    read_bounded,
)
from agents_remember.canvas.readiness import check_page
from agents_remember.kernel.primitives.control_vocabulary import REPORT_LOG_NAME

if TYPE_CHECKING:  # annotation-only: the authoring class loads when a tool is actually called
    from agents_remember.canvas.presenter_tools import AuthoringTools
from agents_remember.cli import developer_message_files as developer_messages
from agents_remember.cli.paseo_bridge import (
    BRIDGE_TIMEOUT,
    SEND_OUTCOME_UNKNOWN,
    PaseoBridgeFailure,
    bridge_call,
)
from agents_remember.cli.provider_limit_hold import PROVIDER_LIMIT_REASON, provider_limit_hold
from agents_remember.cli.role_held_messages import (
    HeldMessage,
    HeldStoreFull,
    hold_message,
    waiting_total,
)
from agents_remember.cli.role_message_files import (
    UNDELIVERED_SUFFIX,
    MessageContent,
    file_line,
    leaf_messages_folder,
    mark_undelivered,
    render_message_file,
    sha256_file,
    store_message_file,
)
from agents_remember.kernel.file_lock import LockAcquisitionTimeout, exclusive_file_lock
from agents_remember.kernel.primitives.paseo_runtime_settings import PaseoRuntimeSettings
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.knowledge.review_evidence import EvidencePacket
from agents_remember.models.knowledge.review_frame import (
    FrameAsset,
    FrameAuthoring,
    FrameBinding,
    FrameLimits,
    FramePart,
    artifacts_digest,
)
from agents_remember.serving.projections.host_activity import agent_in_turn

__all__ = [
    "ExplainerInputs",
    "ExplainerJudgment",
    "ExplainerReports",
    "explainer_authoring",
    "page_revision",
    "production_report_sender",
    "render_and_publish",
    "report_state",
    "settle_reports",
]

#: The private spellings this module's own bodies use: the same objects, one home.
_coordination_of = coordination_of
_utc_now = now_iso

MAX_PERSPECTIVE_CHARACTERS = 4_000
MAX_LOG_BYTES = 1024 * 1024
REPORT_STATES = ("reported", "held", "sent, outcome unknown", "not reported")
PERSPECTIVE_HEADING = "## The explainer's perspective (presenter inference)"

#: L89's per-block fingerprint, handed in by the product (MIK-R187 rule 3).
FingerprintReader = Callable[[tuple[str, ...]], Mapping[str, str]]
#: The host's one send, answering one of :data:`REPORT_STATES`.
ReportSender = Callable[[str, str], str]


def production_report_sender(config: McpRuntimeConfig) -> ReportSender:
    """The production sender, composed here on the first report that needs it.

    The sender's implementation is the ``cli`` half's (it is the half that may call the bridge),
    so the registration layer cannot name it: ``mcp`` may not import ``cli`` under ``layers.toml``.
    This delivery owner resolves it on the call instead, where the canvas package already composes
    the ``cli`` readers the report path needs; the import stays deferred so a light registration
    still loads no bridge, host or serving module.
    """

    from agents_remember.cli.paseo_explainer_host import (  # noqa: PLC0415
        explainer_report_sender,
    )

    return explainer_report_sender(config)


@dataclass(frozen=True)
class ExplainerJudgment:
    """``define_category``, ``remove_category`` and ``grade``, written under the leaf's lock.

    The store is the one :class:`GradeStore` file per leaf beside ``review-frames``, so it survives
    Retire. Every call carries the binding it was made at and is refused ``binding-moved`` when the
    environment has left it, and the check is made again under the lock because an Update replaces
    the binding last. A grade needs the fingerprint MIK-R187 rule 3 defines for the block's kind,
    which is L89's machinery, handed in at construction; without one a reference is refused rather
    than graded with an invented fingerprint, which could never be marked stale.
    """

    workspace: Path
    task_root: Path | None
    seat: str | None
    fingerprints: FingerprintReader | None
    now: Callable[[], str]
    _initial_binding: ExplainerBinding | None = None

    def __post_init__(self) -> None:
        try:
            current = read_binding(self.workspace)
        except Exception:
            current = None
        object.__setattr__(self, "_initial_binding", current)

    def define(
        self,
        name: str,
        scale: Sequence[Mapping[str, str]],
        claim: str,
        meaning: str = "",
    ) -> dict[str, Any]:
        """Define one category: 2 to 5 named grades, highest first, each with its meaning."""

        seat = require_seat(self.workspace, self.seat)
        points = _scale(scale)
        if not name or len(name) > MAX_GRADE_CHARACTERS:
            raise ExplainerRefused(
                "category-name", f"a category name is 1 to {MAX_GRADE_CHARACTERS} characters"
            )
        with self._held(claim) as binding:
            store = self._store()
            before = store.load()
            after = store.define(name, points, meaning=meaning)
        cat = after.category(name)
        return {
            "state": "defined" if after != before else "unchanged",
            "category": name,
            "meaning": cat.meaning if cat is not None else meaning.strip(),
            "grades": [point.grade for point in points],
            "categories": len(after.categories),
            "seat": seat,
            "binding": binding_face(binding),
        }

    def remove(self, name: str, claim: str) -> dict[str, Any]:
        """Remove one category and, with it, its grades."""

        require_seat(self.workspace, self.seat)
        with self._held(claim) as binding:
            store = self._store()
            before = store.load()
            after = store.remove(name)
        return {
            "state": "removed" if after != before else "unchanged",
            "category": name,
            "gradesRemoved": len(before.grades) - len(after.grades),
            "categories": len(after.categories),
            "binding": binding_face(binding),
        }

    def grade(
        self, references: Sequence[str], category: str, grade: str, reason: str, claim: str
    ) -> dict[str, Any]:
        """Grade blocks; answer counts and the references it refused, with their reasons."""

        require_seat(self.workspace, self.seat)
        named = tuple(references)
        if not named:
            raise ExplainerRefused("grade-empty", "a grade names at least one block")
        with self._held(claim) as binding:
            store = self._store()
            after, refused = store.grade(
                references=named,
                category=category,
                grade=grade,
                reason=reason,
                fingerprints=self._fingerprints(named, binding),
                binding=binding.binding,
                at=self.now(),
            )
        if refused:
            return {
                "state": "not-graded",
                "refusal": "fingerprint-unavailable",
                "graded": 0,
                "refused": [
                    {
                        "reference": reference,
                        "reason": "the bound comparison has no fingerprint for this block",
                    }
                    for reference in refused
                ],
                "detail": "nothing was written: a grade without a fingerprint could not go stale",
                "binding": binding_face(binding),
            }
        return {
            "state": "graded",
            "graded": len(named),
            "refused": [],
            "category": category,
            "grade": grade,
            "reason": reason,
            "grades": len(after.grades),
            "binding": binding_face(binding),
        }

    @contextmanager
    def _leaf_lock(self) -> Iterator[None]:
        """The leaf's one lock: the same file a build, an Update and a Retire hold (MIK-R187 rule 6).

        The grade store lies with the task's records, so a seat that writes it while a build or an Update
        rewrites the same file would lose one side's categories. The seat therefore takes the leaf's lock --
        `task_root/<leaf>.lock`, the resource the service names -- and only then the environment's own lock
        for the binding it must re-read. It waits, because a build is short and a lost category is not.
        """

        if self.task_root is None:
            yield
            return
        leaf = require_binding(self.workspace).binding.leaf_id
        path = leaf_lock_resource(self.task_root / path_segment(leaf, name="leaf"))
        try:
            with exclusive_file_lock(path, LOCK_RESOURCE):
                yield
        except LockAcquisitionTimeout as error:
            raise ExplainerRefused(
                BUSY, f"{error}: the leaf is building or updating, so this grade write waits"
            ) from error

    @contextmanager
    def _held(self, claim: str) -> Iterator[ExplainerBinding]:
        """Take the leaf lock alone, then re-read the binding and the claim under it."""

        with self._leaf_lock():
            binding = require_binding(self.workspace)
            require_claim(claim, binding, prior=self._initial_binding)
            if (
                self._initial_binding is None
                or self._initial_binding.binding.comparison_number
                != binding.binding.comparison_number
            ):
                object.__setattr__(self, "_initial_binding", binding)
            yield binding

    def _store(self) -> GradeStore:
        if self.task_root is None:
            raise ExplainerRefused(
                "grades-unavailable",
                "the grade store lies with the task's records, and this server was built without "
                "the task folder",
            )
        leaf = require_binding(self.workspace).binding.leaf_id
        return GradeStore(path=grades_path(self.task_root, leaf), leaf_id=leaf)

    def _fingerprints(
        self, references: tuple[str, ...], binding: ExplainerBinding | None = None
    ) -> dict[str, str]:
        if self.fingerprints is not None:
            return dict(self.fingerprints(references))
        current = binding if binding is not None else require_binding(self.workspace)
        reads = ExplainerTargetReads(binding=current.binding, task_root=self.task_root)
        return reads.resolve_fingerprints(references)


def _scale(points: Sequence[Mapping[str, str]]) -> tuple[ScalePoint, ...]:
    """One category's scale, refusing a point that names no grade, no meaning, or exceeds bounds."""

    built: list[ScalePoint] = []
    for point in points:
        grade = str(point.get("grade", "")).strip()
        meaning = str(point.get("meaning", "")).strip()
        if not grade or not meaning:
            raise ExplainerRefused(
                "category-scale", "each point of a scale names its grade and its meaning"
            )
        if len(grade) > MAX_GRADE_CHARACTERS or len(meaning) > MAX_GRADE_CHARACTERS:
            raise ExplainerRefused(
                "category-scale",
                f"each grade and meaning of a scale is 1 to {MAX_GRADE_CHARACTERS} characters",
            )
        built.append(ScalePoint(grade=grade, meaning=meaning))
    return tuple(built)


@dataclass(frozen=True)
class _CopiedSource:
    """The filed developer message and the exact byte slice copied from it."""

    path: Path
    sha256: str
    copied: str
    start_byte: int
    end_byte: int
    copied_sha256: str


@dataclass(frozen=True)
class _Draft:
    """What one report is written from, before any file exists."""

    message_id: str
    seat: str
    copied: str
    perspective: str
    revision: int | None
    task_id: str
    leaf: str
    source: _CopiedSource | None = None


@dataclass(frozen=True)
class _Report:
    """One report's durable facts, as they are written and then delivered."""

    draft: _Draft
    report_id: str
    path: Path
    digest: str

    @property
    def body(self) -> str:
        return _perspective(self.draft.perspective, self.draft.revision, source=self.draft.source)


@dataclass(frozen=True)
class ExplainerReports:
    """``report_to_architect``: copy his words, add the perspective, address, deliver, log.

    The arguments are only the id of his filed tray message and the perspective (MIK-R189 rule 7).
    His selections and words are copied byte for byte from the filed message with their SHA-256;
    the explainer has no argument for them. The master-to-architect record is the Manager's and the
    architect's, so this module only reads it -- and when it names no seat, the report goes back to
    his tray as a message-file pick (``not reported``) rather than to a seat that would not get it.
    """

    workspace: Path
    task_root: Path | None
    coordination_root: Path
    seat: str | None
    sender: ReportSender | None
    now: Callable[[], str]

    def settle(self) -> list[dict[str, Any]]:
        """Reconcile held reports in ``reports.jsonl`` against recipient launch receipts."""

        if self.task_root is None:
            return []
        require_binding(self.workspace)
        return settle_reports(
            self.coordination_root,
            self.task_root,
            seat=self.seat or "",
            sender=self.sender,
            now=self.now,
        )

    def report(self, message_id: str, perspective: str) -> dict[str, Any]:
        """Write one report for one filed tray message, deliver it once, and log the outcome."""

        seat = require_seat(self.workspace, self.seat)
        text = perspective.strip()
        if not text or len(text) > MAX_PERSPECTIVE_CHARACTERS:
            raise ExplainerRefused(
                "perspective-bounds",
                f"a perspective is 1 to {MAX_PERSPECTIVE_CHARACTERS} characters",
            )
        binding = require_binding(self.workspace)
        task_root = self._task_root()
        self.settle()
        source = self._filed(message_id, seat)
        log = self._log_path(binding)
        if _already_logged(log, message_id):
            raise ExplainerRefused(
                "already-reported",
                f"the tray message {message_id} was already reported; the tool takes one report "
                "per tray message",
            )
        revision = page_revision(task_root, binding.binding.leaf_id)
        record = self._write(
            _Draft(
                message_id=message_id,
                seat=seat,
                copied=source.copied,
                perspective=text,
                revision=revision,
                task_id=binding.binding.task_id,
                leaf=binding.binding.leaf_id,
                source=source,
            )
        )
        state, reason, architect, path, tray_pick = self._deliver(binding, record, log.parent)
        log_row: dict[str, Any] = {
            "messageId": message_id,
            "reportId": record.report_id,
            "sha256": source.copied_sha256,
            "sourceFile": source.path.as_posix(),
            "sourceSha256": source.sha256,
            "copiedByteRange": [source.start_byte, source.end_byte],
            "state": state,
            "architect": architect,
            "file": str(path),
            "at": self.now(),
        }
        if reason is not None:
            log_row["refusal"] = reason
        if tray_pick is not None:
            log_row["trayPick"] = tray_pick
        _append_log(log, log_row)
        answer: dict[str, Any] = {
            "state": state,
            "reportId": record.report_id,
            "messageId": message_id,
            "file": str(path),
            "sha256": record.digest,
            "copiedSha256": source.copied_sha256,
            "sourceFile": source.path.as_posix(),
            "sourceSha256": source.sha256,
            "copiedByteRange": [source.start_byte, source.end_byte],
            "perspectiveCharacters": len(text),
            "architect": architect,
            "pageRevision": revision,
            "binding": binding_face(binding),
        }
        if reason is not None:
            answer["refusal"] = reason
        if tray_pick is not None:
            answer["trayPick"] = tray_pick
        return answer

    def _task_root(self) -> Path:
        if self.task_root is None:
            raise ExplainerRefused(
                "records-unavailable",
                "a report is filed with the task's records, and this server was built without "
                "the task folder",
            )
        return self.task_root

    def _filed(self, message_id: str, seat: str) -> _CopiedSource:
        filed = developer_messages.read_developer_message(
            _coordination(self.coordination_root), message_id, recipient_agent_id=seat
        )
        if not filed:
            raise ExplainerRefused(
                "not-a-developer-message",
                f"{message_id!r} is not a filed developer message to this explainer; the tool "
                "copies his words from that file and takes no other",
            )
        one = filed[0]
        raw = one.path.read_bytes()
        copied, start_byte, end_byte, copied_sha256 = _extract_copied_bytes(raw)
        return _CopiedSource(
            path=one.path,
            sha256=one.sha256,
            copied=copied,
            start_byte=start_byte,
            end_byte=end_byte,
            copied_sha256=copied_sha256,
        )

    def _log_path(self, binding: ExplainerBinding) -> Path:
        task_root = self._task_root()
        folder = developer_messages.target_folder(
            _coordination(self.coordination_root),
            task_root.parent.name,
            binding.binding.task_id,
            self.seat or "",
        )
        return folder / REPORT_LOG_NAME

    def _write(self, draft: _Draft) -> _Report:
        """Write the report file whole: the copied part, the perspective and the page revision."""

        report_id = _report_id(draft.message_id, draft.seat)
        content = render_message_file(
            MessageContent(
                when=_moment(self.now()),
                sender_line=f"From explainer · {draft.task_id} · agent {draft.seat}",
                sender_task=draft.task_id,
                recipient_role="architect",
                recipient_id="",
                recipient_task="",
                message_id=report_id,
                text=draft.copied,
                body=_perspective(draft.perspective, draft.revision, source=draft.source),
            )
        )
        folder = leaf_messages_folder(self._task_root(), draft.leaf)
        path = store_message_file(folder, _report_stem(self.now(), report_id), content)
        return _Report(draft=draft, report_id=report_id, path=path, digest=sha256_file(path))

    def _deliver(
        self, binding: ExplainerBinding, report: _Report, tray_folder: Path
    ) -> tuple[str, str | None, str | None, Path, dict[str, Any] | None]:
        """Deliver to the seat the record names, or keep the file back for his tray."""

        architect = self._architect()
        if architect is None:
            return _held_to_tray(
                report, tray_folder, "architect-unaddressed", NO_ARCHITECT, self.now()
            )
        text = _delivered_text(binding, report)
        return _deliver_report_to_architect(
            _DeliveryContext(
                coordination_root=self.coordination_root,
                architect=architect,
                report=report,
                text=text,
                folder=None,
                tray_folder=tray_folder,
                now=self.now(),
            ),
            sender=self.sender,
        )

    def _architect(self) -> str | None:
        """The architect the master-to-architect record names, read and never written here."""

        return _recorded_architect(self.coordination_root, self._task_root())


NO_ARCHITECT = "no master-to-architect record names a seat for this leaf's master"
NO_SENDER = "this server was built without a host sender, so the report stays with his tray"
TRAY_PICKS_NAME = "tray-picks.jsonl"
_UNDELIVERED_TRAILER = re.compile(rb"\ndelivery: refused as [^\n]*\nrefusalDetail: [^\n]*\n\Z")
_ATTACHMENTS_TRAILER = re.compile(
    rb"\n\n## attachments\n(?:- [^\n]+ sha256:[0-9a-f]{64}(?: \(copied as [^\n]+\))?\n)*\Z"
)


def _recorded_architect(coordination_root: Path, task_root: Path) -> str | None:
    record = (
        coordination_root
        / "notes"
        / "reports"
        / "master-architect"
        / task_root.parent.name
        / f"{task_root.name}.json"
    )
    if not record.is_file():
        return None
    try:
        payload = json.loads(
            read_bounded(record, MAX_RECORD_BYTES, reason="record-too-large", detail="record")
        )
    except (PresenterRefused, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return _architect_id(payload)


def _held_to_tray(
    report: _Report, tray_folder: Path, reason: str, detail: str, now: str
) -> tuple[str, str, None, Path, dict[str, Any]]:
    """Keep a report's file back for his tray as a message-file pick, marked undelivered."""

    path = (
        report.path
        if report.path.name.endswith(UNDELIVERED_SUFFIX)
        else mark_undelivered(report.path, refusal=reason, refusal_detail=detail)
    )
    digest = sha256_file(path) if path.is_file() else report.digest
    pick = {
        "kind": "message-file",
        "reportId": report.report_id,
        "messageId": report.draft.message_id,
        "file": path.as_posix(),
        "sha256": digest,
        "reason": reason,
        "detail": detail,
        "at": now,
    }
    _append_log(tray_folder / TRAY_PICKS_NAME, pick)
    return "not reported", reason, None, path, pick


def _architect_workspace(receipt: Mapping[str, Any] | None) -> Path | None:
    if not isinstance(receipt, Mapping):
        return None
    workspace = receipt.get("workspace")
    folder = workspace.get("path") if isinstance(workspace, Mapping) else None
    return Path(folder) if isinstance(folder, str) and folder else None


@dataclass(frozen=True)
class _DeliveryContext:
    """Context for one report delivery attempt to one architect."""

    coordination_root: Path
    architect: str
    report: _Report
    text: str
    folder: Path | None
    tray_folder: Path
    now: str


def _hold_for_architect(
    ctx: _DeliveryContext,
    reason: str,
    *,
    limited_until: str | None = None,
) -> None:
    assert ctx.folder is not None
    sender_line = f"From explainer · {ctx.report.draft.task_id} · agent {ctx.report.draft.seat}"
    hold_message(
        ctx.folder,
        ctx.architect,
        HeldMessage(
            sender_line=sender_line,
            sender_agent_id=ctx.report.draft.seat,
            text=ctx.text,
            reason=reason,
            attempted_message_id=ctx.report.report_id,
            limited_until=limited_until,
        ),
    )


def _pre_send_hold_reason(
    coordination_root: Path, architect: str, receipt: Mapping[str, Any] | None, folder: Path | None
) -> tuple[str | None, str | None]:
    config = _coordination(coordination_root)
    limited = provider_limit_hold(config, architect)
    if limited is not None:
        until = str(limited["limitedUntil"]) if limited.get("limitedUntil") else None
        return PROVIDER_LIMIT_REASON, until
    if receipt is not None and str(receipt.get("status") or "") in {"held", "starting", "unknown"}:
        return "held-seat-unopened", None
    if folder is not None and waiting_total(folder, architect) > 0:
        return "held-behind-waiting", None
    if agent_in_turn(config, architect):
        return "recipient-in-turn", None
    return None, None


def _deliver_report_to_architect(
    ctx: _DeliveryContext,
    *,
    sender: ReportSender | None = None,
) -> tuple[str, str | None, str | None, Path, dict[str, Any] | None]:
    found = architect_receipt(ctx.coordination_root, ctx.architect)
    receipt = None if found is None else found[1]
    if receipt is not None and (
        bool(receipt.get("archived"))
        or str(receipt.get("status") or "") in {"archived", "stopped", "rejected"}
    ):
        return _held_to_tray(
            ctx.report,
            ctx.tray_folder,
            "recipient-archived",
            "the recorded architect is archived",
            ctx.now,
        )
    folder = _architect_workspace(receipt)
    resolved_ctx = _DeliveryContext(
        coordination_root=ctx.coordination_root,
        architect=ctx.architect,
        report=ctx.report,
        text=ctx.text,
        folder=folder,
        tray_folder=ctx.tray_folder,
        now=ctx.now,
    )
    hold_reason, limited_until = _pre_send_hold_reason(
        ctx.coordination_root, ctx.architect, receipt, folder
    )
    if hold_reason is not None and folder is not None:
        return _hold_or_fallback(resolved_ctx, hold_reason, limited_until=limited_until)
    if sender is not None:
        return _deliver_via_injected_sender(sender, resolved_ctx)
    return _deliver_via_bridge(resolved_ctx)


def _hold_or_fallback(
    ctx: _DeliveryContext, reason: str, *, limited_until: str | None = None
) -> tuple[str, str | None, str | None, Path, dict[str, Any] | None]:
    if ctx.folder is None:
        return _held_to_tray(ctx.report, ctx.tray_folder, "recipient-busy", reason, ctx.now)
    try:
        _hold_for_architect(ctx, reason, limited_until=limited_until)
    except HeldStoreFull as full:
        return _held_to_tray(ctx.report, ctx.tray_folder, "recipient-busy", str(full), ctx.now)
    except ValueError as error:
        return _held_to_tray(ctx.report, ctx.tray_folder, "bridge-error", str(error), ctx.now)
    return "held", None, ctx.architect, ctx.report.path, None


def _sender_exception_outcome(
    ctx: _DeliveryContext, error: Exception
) -> tuple[str, str | None, str | None, Path, dict[str, Any] | None]:
    if isinstance(error, PaseoBridgeFailure) and error.code in {
        SEND_OUTCOME_UNKNOWN,
        BRIDGE_TIMEOUT,
    }:
        return "sent, outcome unknown", None, ctx.architect, ctx.report.path, None
    if isinstance(error, HeldStoreFull):
        return _held_to_tray(ctx.report, ctx.tray_folder, "recipient-busy", str(error), ctx.now)
    text = str(error).lower()
    if "outcome unknown" in text or "timeout" in text:
        return "sent, outcome unknown", None, ctx.architect, ctx.report.path, None
    return _held_to_tray(ctx.report, ctx.tray_folder, "bridge-error", str(error), ctx.now)


def _deliver_via_injected_sender(
    sender: ReportSender, ctx: _DeliveryContext
) -> tuple[str, str | None, str | None, Path, dict[str, Any] | None]:
    try:
        state = sender(ctx.architect, ctx.text)
    except Exception as error:
        return _sender_exception_outcome(ctx, error)
    if state == "held" and ctx.folder is not None:
        return _hold_or_fallback(ctx, "recipient-busy")
    if state == "not reported" or state not in REPORT_STATES:
        detail = (
            "the host answered not reported"
            if state == "not reported"
            else f"the host answered {state!r}"
        )
        return _held_to_tray(ctx.report, ctx.tray_folder, "bridge-error", detail, ctx.now)
    return state, None, ctx.architect, ctx.report.path, None


def _deliver_via_bridge(
    ctx: _DeliveryContext,
) -> tuple[str, str | None, str | None, Path, dict[str, Any] | None]:
    try:
        reply = bridge_call(
            _coordination(ctx.coordination_root),
            "agent-send",
            {"agentId": ctx.architect, "text": ctx.text, "messageId": ctx.report.report_id},
        )
    except Exception as error:
        return _sender_exception_outcome(ctx, error)
    delivery = reply.get("delivery") if isinstance(reply.get("delivery"), dict) else reply
    if isinstance(delivery, dict) and delivery.get("delivered") is True:
        return "reported", None, ctx.architect, ctx.report.path, None
    refused = delivery.get("refused") if isinstance(delivery, dict) else None
    if refused in {"busy", "in-turn", "permission-pending", "host-unreachable"}:
        return _hold_or_fallback(ctx, str(refused))
    reason = "recipient-archived" if refused in {"archived", "not-found"} else "bridge-error"
    return _held_to_tray(
        ctx.report, ctx.tray_folder, reason, f"the host answered {reply!r}", ctx.now
    )


def _architect_id(payload: object) -> str | None:
    """The architect's id in one record, under the names its writers use."""

    if not isinstance(payload, dict):
        return None
    for name in ("architectAgentId", "agentId", "architect"):
        value = payload.get(name)
        if isinstance(value, str) and 0 < len(value) <= 128:
            return value
    return None


def _coordination(root: Path) -> McpRuntimeConfig:
    """The one field the developer-message reader uses: the coordination root it searches."""

    @dataclass(frozen=True)
    class _Root:
        coordination_root: Path
        paseo_runtime: PaseoRuntimeSettings | None = None

    return cast(McpRuntimeConfig, _Root(root))


def _extract_copied_bytes(raw: bytes) -> tuple[str, int, int, str]:
    """Extract the developer's exact message byte slice without truncating internal ``## `` headings."""

    marker = b"\n\n## message\n"
    idx = raw.find(marker)
    if idx >= 0:
        start_byte = idx + len(marker)
    elif raw.startswith(b"## message\n"):
        start_byte = len(b"## message\n")
    else:
        return raw.decode("utf-8"), 0, len(raw), hashlib.sha256(raw).hexdigest()
    end_byte = len(raw)
    undelivered = _UNDELIVERED_TRAILER.search(raw, start_byte)
    if undelivered is not None:
        end_byte = undelivered.start()
    attachments = _ATTACHMENTS_TRAILER.search(raw, start_byte, end_byte)
    if attachments is not None:
        end_byte = attachments.start()
    elif end_byte > start_byte and raw[end_byte - 1 : end_byte] == b"\n":
        # Strip only the single framing newline that render_message_file appends at EOF.
        end_byte -= 1
    copied_bytes = raw[start_byte:end_byte]
    return (
        copied_bytes.decode("utf-8"),
        start_byte,
        end_byte,
        hashlib.sha256(copied_bytes).hexdigest(),
    )


def _copied_part(text: str) -> str:
    """His selections and words, byte for byte from the filed message (rule 7 step 1)."""

    copied, _start, _end, _digest = _extract_copied_bytes(text.encode("utf-8"))
    return copied


def _perspective(text: str, revision: int | None, *, source: _CopiedSource | None = None) -> str:
    """The explainer's own voice under its heading, with the page revision and source seal."""

    lines = [
        PERSPECTIVE_HEADING,
        text,
        "",
        f"page revision: {revision if revision else 'none'}",
    ]
    if source is not None:
        lines.extend(
            [
                f"copiedSource: {source.path.as_posix()} (SHA-256 {source.sha256})",
                f"copiedBytes: {source.start_byte}..{source.end_byte} (SHA-256 {source.copied_sha256})",
            ]
        )
    return "\n".join(lines)


def _delivered_text(binding: ExplainerBinding, report: _Report) -> str:
    """The delivered text: the first line names the sender, the last names the file (rule 7)."""

    head = f"From explainer · {binding.binding.task_id} · agent {report.draft.seat}"
    return "\n".join(
        [head, "", report.draft.copied, "", report.body, "", file_line(report.path, report.digest)]
    )


def _report_id(message_id: str, seat: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{message_id}#{seat}"))


def _report_stem(now: str, report_id: str) -> str:
    stamp = re.sub(r"[^0-9A-Za-z]", "", now)[:15] or "report"
    return f"{stamp}_explainer_to_architect_{report_id[:8]}"


def _moment(now: str) -> datetime:
    """The one timestamp a message file's head renders, refusing an unreadable clock."""

    try:
        moment = datetime.fromisoformat(now)
    except ValueError as error:
        raise ExplainerRefused(
            "clock-unreadable", f"{now!r} is not a timestamp: {error}"
        ) from error
    return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).astimezone(UTC)


def _log_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        body = read_bounded(path, MAX_LOG_BYTES, reason="log-too-large", detail="report log")
    except PresenterRefused:
        return []
    rows: list[dict[str, Any]] = []
    for line in body.decode("utf-8", "replace").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def report_state(path: Path, message_id: str) -> dict[str, Any] | None:
    """The current state of one report: the last line for ``message_id`` in ``reports.jsonl``."""

    latest: dict[str, Any] | None = None
    for row in _log_rows(path):
        if row.get("messageId") == message_id:
            latest = row
    return latest


def _already_logged(path: Path, message_id: str) -> bool:
    """Whether one tray message already has a report, by any state of its log lines."""

    if not path.is_file():
        return False
    try:
        read_bounded(path, MAX_LOG_BYTES, reason="log-too-large", detail="report log")
    except PresenterRefused:
        return True
    return report_state(path, message_id) is not None


def _held_outcome_for(coordination_root: Path, architect: str, report_id: str) -> str | None:
    found = architect_receipt(coordination_root, architect)
    if found is None:
        return None
    _path, receipt = found
    lines = receipt.get("heldMessageLines")
    for entry in lines if isinstance(lines, list) else ():
        if isinstance(entry, dict) and entry.get("messageId") == report_id:
            return str(entry.get("how") or "")
    if bool(receipt.get("archived")) or str(receipt.get("status") or "") in {
        "archived",
        "stopped",
        "rejected",
    }:
        return "not-delivered-archived-or-gone"
    return None


def settle_reports(
    coordination_root: Path,
    task_root: Path,
    *,
    seat: str = "",
    sender: ReportSender | None = None,
    now: Callable[[], str] = now_iso,
) -> list[dict[str, Any]]:
    """Settle held reports in ``reports.jsonl``, re-addressing once on archive or falling back to tray."""

    folder = developer_messages.target_folder(
        _coordination(coordination_root),
        task_root.parent.name,
        task_root.name,
        seat,
    )
    log = folder / REPORT_LOG_NAME
    rows = _log_rows(log)
    if not rows:
        return []
    by_message: dict[str, dict[str, Any]] = {}
    for row in rows:
        msg_id = row.get("messageId")
        if isinstance(msg_id, str):
            by_message[msg_id] = row
    appended: list[dict[str, Any]] = []
    for row in by_message.values():
        if row.get("state") != "held":
            continue
        architect = str(row.get("architect") or "")
        report_id = str(row.get("reportId") or "")
        if not architect or not report_id:
            continue
        how = _held_outcome_for(coordination_root, architect, report_id)
        if how is None:
            continue
        transition = _settle_one_held_row(
            (coordination_root, task_root, folder),
            row=row,
            how=how,
            sender=sender,
            now=now(),
        )
        if transition is not None:
            _append_log(log, transition)
            appended.append(transition)
    return appended


def _settle_one_held_row(
    roots: tuple[Path, Path, Path],
    *,
    row: Mapping[str, Any],
    how: str,
    sender: ReportSender | None,
    now: str,
) -> dict[str, Any] | None:
    coordination_root, task_root, tray_folder = roots
    if how == "delivered":
        return {**dict(row), "state": "reported", "at": now}
    report_path = Path(str(row.get("file") or ""))
    report_id = str(row.get("reportId") or "")
    msg_id = str(row.get("messageId") or "")
    task_id = task_root.name
    dummy_draft = _Draft(
        message_id=msg_id,
        seat="",
        copied="",
        perspective="",
        revision=None,
        task_id=task_id,
        leaf="",
    )
    report_obj = _Report(
        draft=dummy_draft,
        report_id=report_id,
        path=report_path,
        digest=sha256_file(report_path) if report_path.is_file() else str(row.get("sha256") or ""),
    )
    if not row.get("readdressed"):
        new_architect = _recorded_architect(coordination_root, task_root)
        if new_architect is not None and new_architect != row.get("architect"):
            body_text = report_path.read_text(encoding="utf-8") if report_path.is_file() else ""
            state, reason, arch, path, pick = _deliver_report_to_architect(
                _DeliveryContext(
                    coordination_root=coordination_root,
                    architect=new_architect,
                    report=report_obj,
                    text=body_text,
                    folder=None,
                    tray_folder=tray_folder,
                    now=now,
                ),
                sender=sender,
            )
            updated: dict[str, Any] = {
                **dict(row),
                "state": state,
                "architect": arch,
                "file": str(path),
                "readdressed": True,
                "at": now,
            }
            if reason is not None:
                updated["refusal"] = reason
            if pick is not None:
                updated["trayPick"] = pick
            return updated
    state, reason, arch, path, pick = _held_to_tray(
        report_obj,
        tray_folder,
        "recipient-archived",
        "the recipient architect was archived before the held report could be delivered",
        now,
    )
    return {
        **dict(row),
        "state": state,
        "architect": arch,
        "file": str(path),
        "refusal": reason,
        "trayPick": pick,
        "at": now,
    }


def _append_log(path: Path, row: Mapping[str, Any]) -> None:
    """Add one line about one report's state; whole lines, under the log's own lock."""

    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(dict(row), sort_keys=True) + "\n"
    with (
        exclusive_file_lock(path, "explainer report log"),
        path.open("a", encoding="utf-8") as stream,
    ):
        stream.write(line)
        stream.flush()
        os.fsync(stream.fileno())


def page_revision(task_root: Path | None, leaf: str) -> int | None:
    """The highest ready page revision the leaf holds, or ``None`` when it has no page."""

    if task_root is None:
        return None
    area = frames_area(task_root, leaf)
    if not area.is_dir():
        return None
    highest: int | None = None
    for frame in sorted(area.iterdir()):
        pointer = frame / READY_POINTER
        if pointer.is_symlink() or not pointer.is_file():
            continue
        try:
            payload = json.loads(
                read_bounded(pointer, MAX_RECORD_BYTES, reason="record-too-large", detail="pointer")
            )
        except (PresenterRefused, OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        value = payload.get("revision") if isinstance(payload, dict) else None
        if isinstance(value, int) and (highest is None or value > highest):
            highest = value
    return highest


def explainer_authoring(workspace: Path) -> AuthoringTools:
    """The kept eight tools over ``drafts/`` and ``instructions/``, re-read on every call.

    The environment keeps the grants and the packet in ``instructions/`` and the page draft in
    ``drafts/``, so the presenter's class is pointed there; the input digests are taken from the
    files as they are now, which is what lets an Update's new packet be read without a restart.
    """

    # MIK-R204 @ v1 rule 1 (approved subset, group 1 -- the eight authoring tools): the class is resolved
    # here, on the call, so declaring these tools no longer loads `canvas.presenter_tools`.
    authoring = import_module("agents_remember.canvas.presenter_tools").AuthoringTools
    instructions = workspace / INSTRUCTIONS_DIRECTORY
    drafts = workspace / DRAFTS_DIRECTORY
    try:
        manifest = json.loads(
            read_bounded(
                instructions / "grants.json",
                MAX_RECORD_BYTES,
                reason="grants-too-large",
                detail="the environment's grants",
            )
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ExplainerRefused(
            "grants-unreadable", f"the environment's grants cannot be read: {error}"
        ) from error
    if not isinstance(manifest, dict):
        raise ExplainerRefused("grants-unreadable", "the environment's grants are not an object")
    ensure_child(drafts, "assets")
    return authoring(drafts, host=instructions, grants=_granted(manifest, instructions, drafts))


def _written(workspace: Path, relative: str, body: bytes) -> dict[str, Any]:
    """Write one page asset through the confined authoring writer; answer its digest and size."""

    explainer_authoring(workspace).write(relative, body)
    return {"path": relative, "sha256": digest_bytes(body), "bytes": len(body)}


def _granted(manifest: Mapping[str, Any], instructions: Path, drafts: Path) -> dict[str, Any]:
    """The grants the authoring tools check: this working folder and these input digests."""

    inputs: dict[str, str] = {}
    for key in ("evidence", "assignment"):
        name = manifest.get(key)
        if not isinstance(name, str) or not name:
            continue
        body = read_bounded(
            instructions / name, MAX_RECORD_BYTES, reason="input-too-large", detail=name
        )
        inputs[name] = digest_bytes(body)
    return {**dict(manifest), "working": str(drafts), "inputs": inputs}


def _binding_for_publish(workspace: Path, draft_binding: FrameBinding) -> FrameBinding:
    """Re-read ``binding.json`` under ``.frames.lock`` alone for publication admission."""

    env_binding = require_binding(workspace)
    frame = env_binding.binding
    if env_binding.planned is not None:
        return frame.model_copy(
            update={
                "code_base_commit": env_binding.planned.code_tip,
                "code_candidate_commit": env_binding.planned.code_tip,
                "memory_base_commit": env_binding.planned.memory_tip,
                "memory_candidate_commit": env_binding.planned.memory_tip,
                "code_base_tree": env_binding.planned.code_tree,
                "code_candidate_tree": env_binding.planned.code_tree,
                "memory_base_tree": env_binding.planned.memory_tree,
                "memory_candidate_tree": env_binding.planned.memory_tree,
            }
        )
    if (
        frame.comparison_owner != "planned"
        and draft_binding.comparison_owner != "planned"
        and frame.comparison_reference == draft_binding.comparison_reference
        and frame.comparison_number == draft_binding.comparison_number
    ):
        return frame.model_copy(update={"comparison_owner": draft_binding.comparison_owner})
    return frame


def _draft_entries(assets: Mapping[str, bytes]) -> tuple[FrameAsset, ...]:
    return tuple(
        FrameAsset(
            path=path,
            media_type="text/html" if path.endswith(".html") else "application/octet-stream",
            byte_size=len(payload),
            digest=hashlib.sha256(payload).hexdigest(),
        )
        for path, payload in sorted(assets.items())
    )


def render_and_publish(
    workspace: Path,
    *,
    task_root: Path | None = None,
    seat: str | None = None,
    now: Callable[[], str] | None = None,
    binding_callback: Callable[[], FrameBinding | None] | None = None,
) -> tuple[dict[str, Any], bytes | None]:
    """Render the draft in ``drafts/`` and, when ready, publish a revision under ``.frames.lock``."""

    authoring = explainer_authoring(workspace)
    report, picture = authoring.render()
    packet = EvidencePacket.model_validate_json(authoring.read("evidence.json"))
    drafts = workspace / DRAFTS_DIRECTORY
    limits = FrameLimits()
    assets = collect_assets(drafts, limits)
    readiness = check_page(assets, entrypoint="index.html", packet=packet)
    report["readiness"] = readiness.diagnostics()
    if task_root is None or not readiness.ready or report.get("state") != "loaded":
        return report, picture
    part_map = (
        ()
        if readiness.part_map is None or readiness.part_map.implicit
        else tuple(
            FramePart(part=entry.part_id, title=entry.title, claims=entry.claims)
            for entry in readiness.part_map.parts
        )
    )
    moment = (now or now_iso)()
    digest = artifacts_digest(_draft_entries(assets))
    request_id = f"explainer-{digest[:16]}-{packet.comparison.comparison_number}"
    scope = frame_scope(task_root, packet.comparison.leaf_id, EXPLAINER_FRAME)
    draft_binding = FrameBinding.from_packet(packet.comparison)
    source_cb = binding_callback or (lambda: _binding_for_publish(workspace, draft_binding))
    try:
        begin_draft(
            scope,
            request_id=request_id,
            authoring=FrameAuthoring(
                request_id=request_id,
                session_id=seat or None,
                started_at=moment,
                finished_at=moment,
            ),
            focus=packet.scope,
            assets=assets,
            working_directory=drafts.resolve(),
            limits=limits,
            now=moment,
        )
        publication = publish_revision(
            scope,
            request_id=request_id,
            entrypoint="index.html",
            evidence=packet,
            supplemental=packet.supplemental,
            created_at=moment,
            limits=limits,
            part_map=part_map,
            binding_source=source_cb,
        )
    except (FrameRefused, ExplainerRefused) as error:
        reason = getattr(error, "reason", None) or "publication-refused"
        detail = getattr(error, "detail", str(error))
        report["publication"] = {"state": "refused", "refusal": reason, "detail": detail}
        if reason == "binding-moved":
            report["state"] = "refused"
            report["refusal"] = reason
            report["detail"] = detail
        return report, picture
    manifest = publication.manifest
    report["publication"] = {
        "state": "published" if publication.published else "unchanged",
        "revision": None if manifest is None else manifest.revision,
        "binding": None if manifest is None else manifest.binding.comparison_reference,
        "partMap": [part.model_dump(mode="json") for part in part_map],
    }
    return report, picture
