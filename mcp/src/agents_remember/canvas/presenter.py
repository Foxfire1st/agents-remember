"""The review-presenter capability: one exact comparison, one authored page (MIK-R105).

The capability is a *capability*, not a fourth acceptance seat: it receives one admitted comparison
and an admitted R102 evidence packet, investigates only through those bases, writes only its own page
into its own working folder, publishes only through MIK-R103's confined writer, and keeps a durable,
attributable record of the attempt, its activity and its conversation. It writes no knowledge, no
task record, no review result and no gate decision; those stay with their owners.

Four separations are structural here, not advisory:

* **generation is explicit.** :meth:`Presenter.start` demands an explicit activation and refuses
  without one. Reading a saved page, a status or a transcript calls none of this module's start
  paths; there is no implicit generation to hide a model turn in a renderer.
* **publication is not a finished turn.** :meth:`Presenter.publish` hands bytes to the frame store
  and reports the store's result; the attempt's ``publication`` and the store's ready pointer are
  read separately, so a cancelled turn never claims to have published.
* **the harness must be confined.** The authoring execution is behind :class:`PresenterRunner`; a
  caller that wires none gets :class:`UnconfiguredPresenterRunner`, which refuses by name. The
  native launch this capability shipped -- a dedicated presenter tool server, whose standalone
  entry is gone -- was retired with the explainer's move to the normal task server, so a bare
  presenter starts nothing rather than a seat with no tools (MIK-R186 @ v2 Scope). A runner that
  cannot confine its writes must refuse. An instruction that says "do not write outside the
  folder" is not confinement.
* **the design text steers the look only.** The selected text travels with the assignment verbatim,
  beside its source and sha256, behind a non-authority fence. A repository text that asks to change
  permissions, evidence, the claim set, the isolation or where the page may load from is delivered
  as text but not followed, and the attempt's conversation says so (rule 18).

Attempt listing is chronological and bounded (20 shown, 32 retained); request and session lookups
read every retained record, not only the displayed window. Every transition is a locked
read-modify-write of a fresh record, so a delayed host receipt cannot revive a cancelled attempt.
Retention actually evicts the oldest settled attempts and reclaims their working folders.
The durable store and the launch, design and instruction inputs are separate modules
(:mod:`agents_remember.canvas.presenter_store` and
:mod:`agents_remember.canvas.presenter_inputs`); this module keeps the capability's own
orchestration, its saved-first view and the working-folder admission it publishes from.
"""

from __future__ import annotations

import json
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol, cast

from agents_remember.application.review_frame_store import (
    FramePublication,
    FrameRefused,
    FrameScope,
    begin_draft,
    frame_scope,
    publish_revision,
    reopen_frame,
    revision_manifest,
)
from agents_remember.canvas.paths import (
    PathRefused,
    guard_path,
    make_directory,
    path_segment,
    presenter_root,
    presenter_work_base,
    presenter_working_root,
    task_root,
)
from agents_remember.canvas.presenter_assets import collect_assets
from agents_remember.canvas.presenter_files import remove_directory, write_file
from agents_remember.canvas.presenter_host import HostReceipt
from agents_remember.canvas.presenter_inputs import (
    DESIGN_NON_AUTHORITY_FENCE,
    INSTRUCTIONS_FILE,
    MAX_DESIGN_WORDS,
    MAX_INSTRUCTION_WORDS,
    DesignText,
    PresenterLaunch,
    _compose_assignment,
    _design_disclosures,
    admit_retained,
    capability_instructions,
    design_text,
    presenter_launch,
    seed_from_revision,
)
from agents_remember.canvas.presenter_store import (
    ATTEMPT_SCHEMA,
    CAPABILITY,
    LIVE_STATES,
    MAX_ACTIVITY,
    MAX_CONVERSATION,
    MAX_MESSAGE_CHARS,
    MAX_RECORD_BYTES,
    MAX_REQUEST_LEDGER,
    MAX_RETAINED_ATTEMPTS,
    MAX_SCAN_ENTRIES,
    REQUEST_LEDGER_NAME,
    ActivityEntry,
    AttemptStore,
    ConversationEntry,
    PresenterAttempt,
    PresenterRefused,
    RunnerReceipt,
    StartOutcomeUnknown,
    digest_bytes,
    failed_changes,
    receipt_changes,
)
from agents_remember.canvas.prospective import BrowserProspectiveLoad, ProspectiveLoad
from agents_remember.canvas.readiness import check_page
from agents_remember.cli.role_instructions import compose_capability_capsule, instruction_metadata
from agents_remember.kernel.canonical_json import canonical_json_bytes
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.knowledge.review_evidence import EvidencePacket
from agents_remember.models.knowledge.review_frame import (
    FrameAuthoring,
    FrameBinding,
    FrameLimits,
    FramePart,
)

__all__ = [
    "ATTEMPT_SCHEMA",
    "CAPABILITY",
    "DESIGN_NON_AUTHORITY_FENCE",
    "INSTRUCTIONS_FILE",
    "MAX_ACTIVITY",
    "MAX_ATTEMPTS",
    "MAX_CONVERSATION",
    "MAX_DESIGN_WORDS",
    "MAX_INSTRUCTION_WORDS",
    "MAX_MESSAGE_CHARS",
    "MAX_RECORD_BYTES",
    "MAX_REQUEST_LEDGER",
    "MAX_RETAINED_ATTEMPTS",
    "MAX_SCAN_ENTRIES",
    "REQUEST_LEDGER_NAME",
    "ActivityEntry",
    "ConversationEntry",
    "DesignText",
    "Presenter",
    "PresenterAttempt",
    "PresenterHost",
    "PresenterLaunch",
    "PresenterRefused",
    "PresenterRunner",
    "RunnerReceipt",
    "UnconfiguredPresenterRunner",
    "capability_instructions",
    "design_text",
    "presenter_launch",
]
#: How many attempts the panel reads. The retained set is larger, so a request or a live attempt
#: outside the displayed window can still be reconciled.
MAX_ATTEMPTS = 20

_ACTIVITY_KINDS = ("inspection", "question", "stage", "conclusion", "limitation")
#: Every identity-bound operation a runner must answer before it is used as a host. A runner with
#: only ``start`` still works: the conversation then stays the attempt's own local record.
_HOST_OPERATIONS = ("message", "status", "transcript", "resume", "cancel", "archive")

#: The refusal a load's state earns. Only ``loaded`` allows the ready pointer to move.
_LOAD_REFUSALS = {
    "failed": "prospective-load-failed",
    "unavailable": "prospective-load-unavailable",
}


class RunnerRefused(PresenterRefused):
    """The configured authoring harness cannot honour the admitted grants."""


class PresenterRunner(Protocol):
    """The authoring execution port. A runner that cannot confine its writes must refuse.

    ``instructions`` is the whole assignment: the capability text, the non-authority fence and the
    exact recorded design text with its source and sha256. A runner never re-reads the design from
    the repository; the attempt's ``design_sha256`` names the bytes it was given.
    """

    def start(
        self, attempt: PresenterAttempt, instructions: str, working: Path
    ) -> RunnerReceipt: ...


class PresenterHost(Protocol):
    """The identity-bound operations a confined runner may also answer.

    Each call names one exact host session and answers with a receipt the presenter records. A
    runner that cannot answer all of them is a local execution, and the record says so.
    """

    def message(self, session_id: str, *, role: str, text: str) -> HostReceipt: ...

    def status(self, session_id: str) -> Any: ...

    def transcript(self, session_id: str) -> tuple[Any, ...]: ...

    def resume(self, session_id: str) -> HostReceipt: ...

    def cancel(self, session_id: str, *, reason: str) -> HostReceipt: ...

    def archive(self, session_id: str) -> HostReceipt: ...


def _host_port(runner: object) -> PresenterHost | None:
    """The runner as a host, only when every identity-bound operation is callable."""

    if all(callable(getattr(runner, name, None)) for name in _HOST_OPERATIONS):
        return cast(PresenterHost, runner)
    return None


class UnconfiguredPresenterRunner:
    """A runner that refuses: kept for a caller with no confined harness to wire.

    It keeps :class:`PresenterRunner`'s exact parameter names so it is the port's own refusal, not
    a lookalike a type checker would reject; the three values are what the refusal declines.
    """

    def start(self, attempt: PresenterAttempt, instructions: str, working: Path) -> RunnerReceipt:
        del attempt, instructions, working
        raise RunnerRefused(
            "no-confined-harness",
            "this build wires no confined authoring harness for review-presenter; the page was "
            "not generated rather than authored with an unproven write boundary",
        )


@dataclass(frozen=True)
class _StartRequest:
    """The admitted inputs one start or replacement hands to :meth:`Presenter._admit_start`."""

    caller: str
    request_id: str
    binding: FrameBinding
    evidence: EvidencePacket
    focus: str
    regenerate_from: int | None = None
    predecessor_attempt: str | None = None


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _mirrored_turns(
    current: PresenterAttempt, receipt: HostReceipt | None, *, fallback: ConversationEntry
) -> tuple[ConversationEntry, ...]:
    """The turns one say records: the host's own answer, or the local entry when there is none."""

    comparison = current.comparison.comparison_reference
    mirrored = tuple(
        ConversationEntry(
            at=turn.at,
            role=cast(Literal["presenter", "developer"], turn.role),
            comparison=comparison,
            revision=fallback.revision if turn.role == fallback.role else None,
            text=turn.text,
        )
        for turn in (receipt.turns if receipt is not None else ())
        if turn.role in {"presenter", "developer"}
    )
    return mirrored or (fallback,)


class Presenter:
    """One page's presenter state: its attempts, their records, and the confined publication."""

    def __init__(  # noqa: PLR0913 - one page's own identity and its two ports
        self,
        config: McpRuntimeConfig,
        *,
        repository_id: str,
        task: str,
        leaf_id: str,
        frame_id: str,
        runner: PresenterRunner | None = None,
        limits: FrameLimits | None = None,
        prospective: ProspectiveLoad | None = None,
        clock: Any = None,
    ) -> None:
        self._config = config
        self._repository_id = repository_id
        self._task = task
        self._leaf_id = leaf_id
        self._frame_id = frame_id
        self._limits = limits or FrameLimits()
        self._clock = clock or _now
        # MIK-R104 rule 14: a ready pointer moves only after the exact prospective revision was
        # loaded once without a model. The shipped default is a real browser load; a caller that
        # cannot run one passes a port that says ``unavailable`` rather than a port that says
        # ``loaded``.
        self._prospective: ProspectiveLoad = prospective or BrowserProspectiveLoad()
        self._runner: PresenterRunner = runner or UnconfiguredPresenterRunner()
        self._attempts: AttemptStore | None = None

    def _host(self) -> PresenterHost | None:
        return _host_port(self._runner)

    def _host_for(self, attempt: PresenterAttempt) -> tuple[PresenterHost, str] | None:
        """The host and the exact session its calls must name, or ``None`` for a local record."""

        host = self._host()
        session_id = attempt.session_id
        if host is None or not session_id:
            return None
        return host, session_id

    # -- store and paths -----------------------------------------------------------------------

    @property
    def scope(self) -> FrameScope:
        root = task_root(self._config, self._repository_id, self._task)
        return frame_scope(root, self._leaf_id, self._frame_id)

    def _directory(self) -> Path:
        directory = (
            presenter_root(self.scope.task_root)
            / path_segment(self._leaf_id, name="leaf")
            / path_segment(self._frame_id, name="frame")
        )
        try:
            return self.scope.guard(directory, label="presenter directory")
        except FrameRefused as refusal:
            raise PresenterRefused("unsafe-presenter-path", str(refusal)) from refusal

    @property
    def _store(self) -> AttemptStore:
        """The page's durable attempts, attached on first use so construction still does no IO."""

        if self._attempts is None:
            self._attempts = AttemptStore(
                self._config, scope=self.scope, directory=self._directory(), clock=self._clock
            )
        return self._attempts

    def _working(self, attempt: PresenterAttempt) -> Path:
        try:
            return guard_path(
                presenter_work_base(self._config),
                Path(attempt.working_directory),
                label="attempt working folder",
            )
        except PathRefused as refusal:
            raise PresenterRefused("unsafe-working-path", str(refusal)) from refusal

    # -- records -------------------------------------------------------------------------------

    def _attached(self) -> AttemptStore | None:
        """The store, or ``None`` when this page's own directory is not admissible.

        A read is not a write: an unsafe presenter directory makes the page look empty to a
        reader, exactly as A2's facade behaved, rather than raising on the first read. Writes
        still refuse by name, because a refused write is not an empty page.
        """

        try:
            return self._store
        except PresenterRefused:
            return None

    def attempts(self) -> tuple[PresenterAttempt, ...]:
        """The displayed attempts, newest first, bounded by :data:`MAX_ATTEMPTS`."""

        store = self._attached()
        if store is None:
            return ()
        return tuple(store.records()[:MAX_ATTEMPTS])

    def read(self, attempt_id: str) -> PresenterAttempt | None:
        store = self._attached()
        return None if store is None else store.read(attempt_id)

    def current(self) -> PresenterAttempt | None:
        """The newest live attempt, else the newest retained one: the default panel view."""

        store = self._attached()
        return None if store is None else store.current()

    def live(self) -> PresenterAttempt | None:
        """The latest-created attempt still open, independent of the displayed window."""

        store = self._attached()
        return None if store is None else store.live()

    def by_request(self, request_id: str) -> PresenterAttempt | None:
        """Reconcile one request identity across every retained record, not the shown window."""

        store = self._attached()
        return None if store is None else store.by_request(request_id)

    def by_session(self, session_id: str) -> PresenterAttempt | None:
        """Find one attempt by its recorded host session, across every retained record."""

        store = self._attached()
        return None if store is None else store.by_session(session_id)

    def _record_path(self, attempt_id: str) -> Path:
        return self._store.record_path(attempt_id)

    def _evidence_path(self, attempt_id: str) -> Path:
        return self._store.evidence_path(attempt_id)

    def _read_evidence(self, attempt_id: str, expected: str) -> EvidencePacket | None:
        return self._store.read_evidence(attempt_id, expected)

    # -- authoring -----------------------------------------------------------------------------

    def start(  # noqa: PLR0913 - one start states the assignment's own inputs
        self,
        *,
        caller: str,
        request_id: str,
        activation: str,
        evidence: EvidencePacket,
        focus: str,
        regenerate_from: int | None = None,
        predecessor_attempt: str | None = None,
    ) -> PresenterAttempt:
        """Start one explicit authoring attempt, or refuse by name.

        The request identity is the reconciliation key: a repeat returns the attempt it already
        created, a second live attempt is refused, and no start happens without explicit developer
        activation. Regeneration copies the retained revision's own assets into the new working
        folder and records its ancestry; missing retained bytes are a refusal, never a fresh start.
        The runner receives the exact selected design text with its source and sha256, behind the
        non-authority fence.
        """

        binding = FrameBinding.from_packet(evidence.comparison)
        self._admit_binding(binding)
        if evidence.comparison.repository_id != self._repository_id:
            raise PresenterRefused("foreign-comparison", "the evidence names another repository")
        if activation != "explicit":
            raise PresenterRefused(
                "not-activated",
                "authoring starts only from the explicit Generate control; a mount, a reload, a "
                "saved page or a status read starts no model",
            )
        attempt, assignment, launch_model = self._admit_start(
            _StartRequest(
                caller=caller,
                request_id=request_id,
                binding=binding,
                evidence=evidence,
                focus=focus,
                regenerate_from=regenerate_from,
                predecessor_attempt=predecessor_attempt,
            )
        )
        if assignment is None or launch_model is None:
            if attempt.state == "started" and (attempt.detail or "").startswith(
                "start-outcome-unknown"
            ):
                reconcile = getattr(self._runner, "reconcile", None)
                if callable(reconcile):
                    try:
                        receipt = cast(RunnerReceipt, reconcile(attempt, self._working(attempt)))
                    except StartOutcomeUnknown:
                        return attempt
                    return self._store.mutate(
                        attempt.attempt_id,
                        lambda current: receipt_changes(current, receipt, attempt.model or ""),
                    )
            return attempt
        return self._run(attempt, assignment=assignment, launch_model=launch_model)

    def _admit_binding(self, binding: FrameBinding) -> None:
        if (binding.task_id, binding.leaf_id) != (self._task, self._leaf_id):
            raise PresenterRefused(
                "foreign-comparison",
                f"the evidence names {binding.task_id}/{binding.leaf_id}; this page explains "
                f"{self._task}/{self._leaf_id}",
            )

    def _admit_start(
        self, request: _StartRequest
    ) -> tuple[PresenterAttempt, str | None, str | None]:
        """Admit and durably record one new attempt, then release the lock before any host call."""

        with self._store.locked():
            existing = self._store.by_request(request.request_id)
            if existing is not None:
                return existing, None, None
            reclaimed = self._store.ledger().get(request.request_id)
            if reclaimed is not None:
                raise PresenterRefused(
                    "attempt-record-reclaimed",
                    f"request {request.request_id} created attempt {reclaimed}, whose record has "
                    "been reclaimed under the retention ceiling; this request is reconciled to it "
                    "and starts no second session",
                )
            live = self._store.live()
            if live is not None:
                raise PresenterRefused(
                    "in-flight",
                    f"attempt {live.attempt_id} is still {live.state} for this comparison; "
                    "reconcile it before a second start",
                )
            launch = presenter_launch(self._config, repository_id=self._repository_id)
            if launch.state != "ok":
                raise PresenterRefused("launch-choice-unavailable", launch.detail)
            design = design_text(self._config, self._repository_id)
            instructions, composition = compose_capability_capsule(
                CAPABILITY, launch.provenance.get("notes", {})
            )
            instructions_digest = digest_bytes(instructions.encode())
            attempt = self._open_attempt(
                caller=request.caller,
                request_id=request.request_id,
                binding=request.binding,
                evidence=request.evidence,
                focus=request.focus,
                launch_model=launch.model or "",
                design=design,
                instructions_digest=instructions_digest,
                regenerate_from=request.regenerate_from,
                predecessor_attempt=request.predecessor_attempt,
                disclosures=_design_disclosures(design),
            )
            attempt = attempt.model_copy(
                update={
                    "instruction_composition": instruction_metadata(composition),
                    "launch_provenance": launch.provenance,
                }
            )
            self._store.write_locked(attempt)
        return attempt, _compose_assignment(instructions, design), launch.model or ""

    def _open_attempt(  # noqa: PLR0913 - one attempt record's own decided fields
        self,
        *,
        caller: str,
        request_id: str,
        binding: FrameBinding,
        evidence: EvidencePacket,
        focus: str,
        launch_model: str,
        design: DesignText,
        instructions_digest: str,
        regenerate_from: int | None,
        predecessor_attempt: str | None,
        disclosures: tuple[str, ...],
    ) -> PresenterAttempt:
        attempt_id = f"att-{digest_bytes(f'{request_id}:{self._clock()}'.encode())[:16]}"
        working = presenter_working_root(
            self._config,
            repository_id=self._repository_id,
            task=self._task,
            leaf_id=self._leaf_id,
            frame_id=self._frame_id,
            attempt_id=attempt_id,
        )
        make_directory(presenter_work_base(self._config), working, label="attempt working folder")
        try:
            if regenerate_from is not None:
                seed_from_revision(self.scope, working, regenerate_from)
            evidence_bytes = canonical_json_bytes(
                json.loads(evidence.model_dump_json(by_alias=True))
            )
            write_file(self._store.evidence_path(attempt_id), evidence_bytes)
        except (PresenterRefused, OSError):
            with suppress(OSError):
                remove_directory(working)
            raise
        now = self._clock()
        conversation = tuple(
            ConversationEntry(
                at=now,
                role="presenter",
                comparison=binding.comparison_reference,
                text=text,
            )
            for text in disclosures
        )
        return PresenterAttempt(
            attempt_id=attempt_id,
            request_id=request_id,
            caller=caller,
            repository_id=self._repository_id,
            task=self._task,
            leaf_id=self._leaf_id,
            frame_id=self._frame_id,
            comparison=binding,
            evidence_digest=digest_bytes(evidence_bytes),
            focus=focus,
            state="started",
            created_at=now,
            updated_at=now,
            working_directory=str(working),
            parent_revision=regenerate_from,
            predecessor_attempt=predecessor_attempt,
            model=launch_model,
            design_source=design.source,
            design_sha256=design.sha256,
            design_words=design.words,
            instructions_sha256=instructions_digest,
            conversation=conversation,
        )

    def _run(
        self, attempt: PresenterAttempt, *, assignment: str, launch_model: str
    ) -> PresenterAttempt:
        """Hand the attempt to the confined runner and keep whatever it answered."""

        working = self._working(attempt)
        try:
            receipt = self._runner.start(attempt, assignment, working)
        except StartOutcomeUnknown as refusal:
            detail = f"start-outcome-unknown: {refusal}; retry the same request to reconcile"
            return self._store.mutate(attempt.attempt_id, lambda _current: {"detail": detail})
        except PresenterRefused as refusal:
            captured = refusal
            return self._store.mutate(
                attempt.attempt_id, lambda current: failed_changes(current, captured)
            )
        return self._store.mutate(
            attempt.attempt_id,
            lambda current: receipt_changes(current, receipt, launch_model),
        )

    def note_activity(self, attempt_id: str, *, kind: str, text: str) -> PresenterAttempt:
        """Append one attributable activity line, bounded and typed."""

        if kind not in _ACTIVITY_KINDS:
            raise PresenterRefused("unknown-activity", f"{kind!r} is not an activity kind")

        def change(current: PresenterAttempt) -> dict[str, Any]:
            entry = ActivityEntry(at=self._clock(), kind=kind, text=text)
            return {"activity": (*current.activity, entry)[-MAX_ACTIVITY:]}

        return self._store.mutate(attempt_id, change)

    def say(
        self,
        attempt_id: str,
        *,
        role: Literal["presenter", "developer", "system-owner"],
        text: str,
        revision: int | None = None,
    ) -> PresenterAttempt:
        """Send one conversation turn; a configured host answers it and its receipt is recorded."""

        if role not in {"presenter", "developer", "system-owner"}:
            raise PresenterRefused("unknown-role", f"{role!r} is not a conversation role")
        attempt = self._store.require(attempt_id)
        fallback = ConversationEntry(
            at=self._clock(),
            role=role,
            comparison=attempt.comparison.comparison_reference,
            revision=revision,
            text=text,
        )
        sent = text
        if attempt.published_revision is not None:
            manifest = revision_manifest(self.scope.frame_dir, attempt.published_revision)
            if manifest is None or manifest.authoring.request_id != attempt.request_id:
                raise PresenterRefused(
                    "publication-unavailable", "the exact publication receipt is unavailable"
                )
            entry = next(
                asset for asset in manifest.assets if asset.path == manifest.entrypoint.path
            )
            sent = (
                f"[Product publication receipt, not a new claim: comparison "
                f"{manifest.binding.comparison_reference}; published revision {manifest.revision}; "
                f"request {manifest.authoring.request_id}; session {manifest.authoring.session_id}; "
                f"entrypoint {entry.path}; sha256 {entry.digest}. The confined writer published "
                f"these bytes from this attempt. Authoring label and published revision are "
                f"different identities.]\n\n{text}"
            )
        hosted = self._host_for(attempt)
        receipt = hosted[0].message(hosted[1], role=role, text=sent) if hosted else None

        def change(current: PresenterAttempt) -> dict[str, Any]:
            entries = _mirrored_turns(current, receipt, fallback=fallback)
            changes: dict[str, Any] = {
                "conversation": (*current.conversation, *entries)[-MAX_CONVERSATION:]
            }
            if receipt is not None:
                changes["detail"] = f"host message {receipt.message_id} on {receipt.session_id}"
            return changes

        return self._store.mutate(attempt_id, change)

    def status(self, attempt_id: str) -> dict[str, Any]:
        """The host's own view of this attempt's session, or a truthful local one."""

        attempt = self._store.require(attempt_id)
        hosted = self._host_for(attempt)
        if hosted is None:
            return {
                "source": "local",
                "state": attempt.state,
                "session_id": attempt.session_id,
                "execution_id": attempt.execution_id,
                "turns": len(attempt.conversation),
                "detail": (
                    "no host operations are configured for this attempt; the conversation is the "
                    "durable local record"
                ),
            }
        view = hosted[0].status(hosted[1])
        return {
            "source": "host",
            "state": view.state,
            "session_id": view.session_id,
            "execution_id": view.execution_id,
            "turns": view.turns,
            "updated_at": view.updated_at,
            "detail": view.detail,
        }

    def transcript(self, attempt_id: str) -> tuple[Any, ...]:
        """The host transcript when one exists, else the attempt's own recorded conversation."""

        attempt = self._store.require(attempt_id)
        hosted = self._host_for(attempt)
        if hosted is None:
            return attempt.conversation
        try:
            return hosted[0].transcript(hosted[1])
        except PresenterRefused as refusal:
            if refusal.reason != "presenter-transcript-unavailable":
                raise
            return attempt.conversation

    def _retain_transcript(self, attempt: PresenterAttempt, *, locked: bool = False) -> None:
        """Collect the last eight actual turns before an explicit settlement, never resume for it.

        This is a bounded snapshot, not a promise that the host keeps complete history. A closed
        host stays closed and its unavailable state is exposed by the observational opening.
        """
        if self._host_for(attempt) is None:
            return
        observed = self._observed_attempt(attempt)
        if observed.host_status.get("state") == "unavailable":
            return
        changes = {
            "conversation": observed.conversation[-8:],
            "host_status": {
                **observed.host_status,
                "conversationRetention": "last eight collected native turns; not complete host history",
            },
        }
        if locked:
            current = self._store.require(attempt.attempt_id)
            self._store.write_locked(current.model_copy(update=changes))
        else:
            self._store.mutate(attempt.attempt_id, lambda _current: changes)

    def draft(self, attempt_id: str) -> Any:
        """The draft result the confined harness names, when it exposes one."""

        attempt = self._store.require(attempt_id)
        render = getattr(self._runner, "render", None)
        session_id = attempt.session_id
        if not callable(render) or session_id is None:
            raise PresenterRefused(
                "no-draft-surface", "this runner exposes no draft result for the attempt"
            )
        return render(session_id)

    def documentation(self) -> Any:
        """The authoring documentation surface, which is never review evidence."""

        surface = getattr(self._runner, "documentation", None)
        if surface is None:
            raise PresenterRefused(
                "no-documentation-surface", "this runner exposes no documentation surface"
            )
        return surface

    def request_explanation(self, attempt_id: str, question: str) -> PresenterAttempt:
        """Record a frame's request for an explanation. It starts no model by itself."""

        return self.note_activity(
            attempt_id, kind="question", text=f"the page asked: {question[:MAX_MESSAGE_CHARS]}"
        )

    # -- session operations --------------------------------------------------------------------

    def resume(self, attempt_id: str) -> PresenterAttempt:
        """Resume the same recorded session, only when the host can still name it."""

        attempt = self._store.require(attempt_id)
        hosted = self._host_for(attempt)
        receipt = (
            hosted[0].resume(hosted[1])
            if hosted is not None and attempt.state != "cancelled"
            else None
        )

        def change(current: PresenterAttempt) -> dict[str, Any]:
            if current.state == "cancelled":
                raise PresenterRefused(
                    "not-resumable",
                    f"attempt {attempt_id} was explicitly cancelled/replaced",
                )
            if not current.session_id:
                raise PresenterRefused(
                    "no-recorded-session",
                    f"attempt {attempt_id} records no host session; a resume would invent one",
                )
            detail = "resume requested on the recorded session"
            if receipt is not None:
                detail = f"{receipt.detail} ({receipt.session_id})"
            return {"detail": detail}

        return self._store.mutate(attempt_id, change)

    def cancel(self, attempt_id: str, *, reason: str) -> PresenterAttempt:
        """Cancel an open attempt. It keeps every retained revision untouched."""

        attempt = self._store.require(attempt_id)
        hosted = self._host_for(attempt)
        note = "no host session was configured to confirm it"
        if hosted is not None and attempt.state in LIVE_STATES:
            self._retain_transcript(attempt)
            try:
                note = hosted[0].cancel(hosted[1], reason=reason).detail
            except PresenterRefused as refusal:
                note = f"the host did not confirm: {refusal.reason}: {refusal.detail}"

        def change(current: PresenterAttempt) -> dict[str, Any]:
            if current.state not in LIVE_STATES:
                raise PresenterRefused(
                    "not-cancellable", f"attempt {attempt_id} is {current.state}"
                )
            published = (
                f"revision {current.published_revision} remains ready"
                if current.published_revision
                else "no revision was published by this attempt"
            )
            return {"state": "cancelled", "detail": f"{reason}; {published}; {note}"}

        return self._store.mutate(attempt_id, change)

    def replace(self, attempt_id: str, *, request_id: str, reason: str) -> PresenterAttempt:
        """Record an explicit replacement after admitting every successor input.

        Nothing about the predecessor is mutated until the successor's evidence, launch choices,
        design text and retained assets are all admitted. A refused replacement therefore leaves
        the predecessor's record bytes, state and published revision exactly as they were. A
        failed or cancelled regeneration passes its own retained parent, not an empty start.
        """

        with self._store.locked():
            existing = self._store.by_request(request_id)
            if existing is not None:
                if existing.predecessor_attempt != attempt_id:
                    raise PresenterRefused(
                        "replacement-request-conflict", "this request names another predecessor"
                    )
                return existing
            predecessor = self._store.require(attempt_id)
            evidence = self._read_evidence(predecessor.attempt_id, predecessor.evidence_digest)
            if evidence is None:
                raise PresenterRefused(
                    "retained-evidence-unavailable",
                    f"attempt {attempt_id}'s admitted evidence is unreadable; a replacement would "
                    "invent the comparison it starts from",
                )
            parent = predecessor.published_revision or predecessor.parent_revision
            self._admit_replacement(predecessor, parent)
            binding = FrameBinding.from_packet(evidence.comparison)
            self._admit_binding(binding)
            detail = f"replaced: {reason}"
            settled = self._store.mutate(
                predecessor.attempt_id,
                lambda current: {
                    "state": "cancelled" if current.state in LIVE_STATES else current.state,
                    "detail": detail,
                },
            )
            archived = self._archive_host(predecessor)
            if archived:
                settled = self._store.mutate(
                    predecessor.attempt_id,
                    lambda current: {"detail": f"{current.detail}{archived}"},
                )
            try:
                attempt, assignment, launch_model = self._admit_start(
                    _StartRequest(
                        caller=settled.caller,
                        request_id=request_id,
                        binding=binding,
                        evidence=evidence,
                        focus=settled.focus,
                        regenerate_from=parent,
                        predecessor_attempt=predecessor.attempt_id,
                    )
                )
            except (PresenterRefused, OSError):
                self._store.write_locked(predecessor)
                raise
        if assignment is None or launch_model is None:
            return attempt
        return self._run(attempt, assignment=assignment, launch_model=launch_model)

    def _admit_replacement(self, predecessor: PresenterAttempt, parent: int | None) -> None:
        """Admit the successor's choices and retained source before touching the predecessor."""

        other = next(
            (
                item
                for item in self._store.records()
                if item.state in LIVE_STATES and item.attempt_id != predecessor.attempt_id
            ),
            None,
        )
        if other is not None:
            raise PresenterRefused(
                "in-flight",
                f"attempt {other.attempt_id} is still {other.state}; reconcile it before a "
                "replacement",
            )
        launch = presenter_launch(self._config, repository_id=self._repository_id)
        if launch.state != "ok":
            raise PresenterRefused("launch-choice-unavailable", launch.detail)
        design_text(self._config, self._repository_id)
        capability_instructions()
        if parent is not None:
            admit_retained(self.scope, parent)

    def _archive_host(self, attempt: PresenterAttempt) -> str:
        """Settle the predecessor's host session and record what the host answered."""

        hosted = self._host_for(attempt)
        if hosted is None:
            return ""
        self._retain_transcript(attempt, locked=True)
        try:
            receipt = hosted[0].archive(hosted[1])
        except PresenterRefused as refusal:
            return f"; host archive refused: {refusal.reason}"
        return f"; host archive: {receipt.detail}"

    # -- publication ---------------------------------------------------------------------------

    def publish(
        self,
        attempt_id: str,
        *,
        entrypoint: str,
        created_at: str | None = None,
    ) -> FramePublication:
        """Publish the attempt's own folder as a new ready revision, through MIK-R103 only."""

        attempt = self._store.require(attempt_id)
        if attempt.state not in LIVE_STATES:
            raise PresenterRefused(
                "not-publishable", f"attempt {attempt_id} is {attempt.state}; it publishes nothing"
            )
        evidence = self._read_evidence(attempt_id, attempt.evidence_digest)
        if evidence is None:
            raise PresenterRefused(
                "retained-evidence-unavailable",
                "the attempt's admitted evidence is unreadable; a revision is never published "
                "without the packet its claims rest on",
            )
        working = self._working(attempt)
        assets = collect_assets(working, self._limits)
        if entrypoint not in assets:
            raise PresenterRefused(
                "entrypoint-missing", f"the working folder holds no {entrypoint!r}"
            )
        # MIK-R104 rule 14: no ready pointer moves before the exact prospective revision has been
        # loaded once, without a model. A failed check says what failed and leaves the last ready
        # revision in place; it does not consume the attempt, so the presenter can fix and retry.
        readiness = check_page(assets, entrypoint=entrypoint, packet=evidence)
        if not readiness.ready:
            raise PresenterRefused(
                "readiness-failed",
                "the prospective revision is not ready: "
                + "; ".join(f"{item.name}: {item.detail}" for item in readiness.failures)
                + (" | " + " | ".join(readiness.notes) if readiness.notes else ""),
            )
        self._require_prospective_load(
            assets, entrypoint=entrypoint, evidence=evidence, notes=readiness.notes
        )
        scope = self.scope
        try:
            begin_draft(
                scope,
                working_directory=working,
                request_id=attempt.request_id,
                authoring=FrameAuthoring(
                    request_id=attempt.request_id,
                    execution_id=attempt.execution_id,
                    session_id=attempt.session_id,
                    model=attempt.model,
                    started_at=attempt.created_at,
                    finished_at=self._clock(),
                ),
                focus=attempt.focus,
                assets=assets,
                limits=self._limits,
                now=self._clock(),
            )
            part_map = (
                ()
                if readiness.part_map is None or readiness.part_map.implicit
                else tuple(
                    FramePart(part=entry.part_id, title=entry.title, claims=entry.claims)
                    for entry in readiness.part_map.parts
                )
            )
            publication = publish_revision(
                scope,
                request_id=attempt.request_id,
                entrypoint=entrypoint,
                evidence=evidence,
                supplemental=evidence.supplemental,
                created_at=created_at or self._clock(),
                limits=self._limits,
                asset_ancestry=()
                if attempt.parent_revision is None
                else (attempt.parent_revision,),
                part_map=part_map,
            )
        except FrameRefused as refusal:
            self._store.record_publication_refusal(attempt.attempt_id, refusal)
            raise PresenterRefused(
                refusal.reason or "publication-refused", str(refusal)
            ) from refusal
        if publication.manifest is not None and publication.published:
            self._store.record_publication(attempt, publication)
            self._retain_transcript(self._store.require(attempt.attempt_id))
        return publication

    def _require_prospective_load(
        self,
        assets: dict[str, bytes],
        *,
        entrypoint: str,
        evidence: EvidencePacket,
        notes: tuple[str, ...] = (),
    ) -> None:
        """Load the exact prospective revision once without a model before any pointer moves.

        The static check above read the bytes; this one proves a page resolves and runs them under
        the served policy. ``failed`` carries what the load observed, and ``unavailable`` carries
        why nothing was observed -- neither is a pass, both refuse publication, and both leave the
        last ready revision where it was.
        """

        result = self._prospective.load(assets=assets, entrypoint=entrypoint, packet=evidence)
        if result.state == "loaded":
            return
        detail = result.diagnostics()
        if notes:
            detail = f"{detail}; {' | '.join(notes)}"
        raise PresenterRefused(_LOAD_REFUSALS.get(result.state, "prospective-load-failed"), detail)

    def opening(self) -> dict[str, Any]:
        """The saved-first view a panel reads. It starts nothing and resumes nothing."""

        opening = reopen_frame(self.scope)
        records = self._store.records()
        attempts = tuple(self._observed_attempt(item) for item in records[:MAX_ATTEMPTS])
        live = next((item for item in records if item.state in LIVE_STATES), None)
        if live is not None:
            live = next((item for item in attempts if item.attempt_id == live.attempt_id), live)
        return {
            "state": opening.state,
            "revision": opening.revision,
            "detail": opening.detail,
            "entrypoint": None if opening.manifest is None else opening.manifest.entrypoint.path,
            "comparison": (
                None if opening.manifest is None else opening.manifest.binding.comparison_reference
            ),
            "attempts": [attempt.model_dump() for attempt in attempts],
            "live": None if live is None else live.model_dump(),
        }

    def _observed_attempt(self, attempt: PresenterAttempt) -> PresenterAttempt:
        """Read the recorded native session without resuming it or changing a saved record."""
        hosted = self._host_for(attempt)
        if hosted is None:
            return attempt
        try:
            status = self.status(attempt.attempt_id)
            turns = hosted[0].transcript(hosted[1])
        except PresenterRefused as refusal:
            return attempt.model_copy(
                update={"host_status": {"state": "unavailable", "detail": str(refusal)}}
            )
        conversation = tuple(
            ConversationEntry(
                at=turn.at,
                role=turn.role,
                comparison=attempt.comparison.comparison_reference,
                revision=attempt.published_revision,
                text=turn.text,
            )
            for turn in turns
        )
        return attempt.model_copy(
            update={"host_status": status, "conversation": conversation or attempt.conversation}
        )
