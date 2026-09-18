"""Typed error family for Agents Remember.

Every domain error subclasses :class:`AgentsRememberError`, which itself
subclasses :class:`ValueError` so existing ``except ValueError`` handlers and
the FastMCP error surface keep working unchanged. New code should raise (and
catch) the typed members of this family rather than bare ``ValueError`` /
``RuntimeError`` so the public surface has one coherent error contract.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal


class AgentsRememberError(ValueError):
    """Base class for all Agents Remember domain errors."""


class LockCapabilityError(AgentsRememberError):
    """The resource filesystem cannot provide the required exclusive file lock."""


class CertificationContractError(AgentsRememberError):
    """A rail registry, plan, or result manifest failed closed validation."""

    def __init__(self, detail: str, findings: Sequence[Mapping[str, object]]) -> None:
        self._findings = tuple(_freeze_contract_finding(finding) for finding in findings)
        super().__init__(detail)

    @property
    def findings(self) -> tuple[Mapping[str, object], ...]:
        return self._findings


class CertificationProfileError(CertificationContractError):
    """One repository profile authority failed before any certification command."""

    status = "certification-profile-invalid"


class CertificationExecutorPrerequisiteError(CertificationContractError):
    """An admitted repository executor cannot start its affected gate population."""

    status = "certification-executor-prerequisite-failed"


class CloseoutReadinessContractError(CertificationContractError):
    """One closeout readiness projection contradicted its exact authorities."""

    status = "closeout-readiness-contract-failed"


class DaggerRuntimeAuthorityError(CertificationContractError):
    """The host-level shared Dagger runner/layer-store authority refused admission.

    Raised before any Dagger command starts when the declared authority is missing,
    malformed, unsupported, provisioning-capable, worktree-local, ambiguous, not
    inspectable as a live engine with the exact layer store mounted, contradicted by
    ambient or per-worktree configuration, or blocked by an active authority-transition
    barrier with live owners. The findings carry the typed defect code plus both
    authority digests and the live-owner census where relevant.
    """

    status = "dagger-runtime-authority-invalid"


def _freeze_contract_finding(finding: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType({key: _freeze_contract_value(value) for key, value in finding.items()})


def _freeze_contract_value(value: object) -> object:
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("certification finding mapping keys must be strings")
        return MappingProxyType({key: _freeze_contract_value(item) for key, item in value.items()})
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_freeze_contract_value(item) for item in value)
    if isinstance(value, bytearray):
        return bytes(value)
    if value is None or isinstance(value, (str, bytes, int, float, bool)):
        return value
    raise TypeError("certification finding values must be immutable JSON-like data")


class TaskIntentError(AgentsRememberError):
    """Normative task intent is unavailable, invalid, stale, or unsupported."""

    def __init__(self, status: str, detail: str, *, next_action: str = "task_doc") -> None:
        self.status = status
        self.detail = detail
        self.next_action = next_action
        super().__init__(detail)


class SeatOccupancyError(AgentsRememberError):
    """A canonical document-and-role seat has multiple claimants in one generation."""


class StructuralDispatchError(AgentsRememberError):
    """Durable dispatch evidence is ambiguous or contradicts the current seat."""


class StructuralDispatchLockError(AgentsRememberError):
    """The canonical-seat serializer could not establish cross-process exclusion."""


class StructuralRoutingError(AgentsRememberError):
    """A structural route is absent or ambiguous; routing must fail instead of guessing."""


class AuthorityError(AgentsRememberError):
    """A path or repo argument violated the MCP authority settings.

    Raised when a caller names a repo that settings do not allow, or passes a
    path that escapes the coordinator root. Centralizing this means every
    application entry point reports the same boundary violation the same way.
    """


class ConfiguredContractAuthorityError(AgentsRememberError):
    """A readable task contract contradicts configured repository authority.

    The exception identifies the bounded authority cell that failed without
    carrying repository paths or lower backend diagnostics into public results.
    """

    def __init__(self, *, side: str, name: str) -> None:
        self.side = side
        self.name = name
        super().__init__("configured contract authority does not match")


ConfiguredContractRereadReason = Literal[
    "location-invalid",
    "contract-unreadable",
    "authority-invalid",
]


class ConfiguredContractRereadError(AgentsRememberError):
    """A mutation boundary could not re-prove current configured-contract truth.

    The value contains only the finite semantic classification and bounded
    public evidence assembled by the domain authority owner. The lower
    exception remains available only through ordinary exception chaining.
    """

    def __init__(
        self,
        *,
        reason: ConfiguredContractRereadReason,
        status: str,
        detail: str,
        expected: Mapping[str, object],
        observed: Mapping[str, object],
    ) -> None:
        self.reason = reason
        self.status = status
        self.detail = detail
        self.expected = dict(expected)
        self.observed = dict(observed)
        super().__init__(detail)


class RouteIndexCensusError(AgentsRememberError):
    """A validated repository could not provide one authoritative source census."""


class FutureCodeCandidateError(AgentsRememberError):
    """A future-code candidate could not be captured or is no longer current."""

    def __init__(self, status: str, detail: str) -> None:
        self.status = status
        super().__init__(detail)


class CuratorCoherenceError(AgentsRememberError):
    """The leaf's structured curator-coherence authority is absent, stale, or invalid."""

    def __init__(
        self,
        status: str,
        detail: str,
        *,
        expected: Mapping[str, object] | None = None,
        observed: Mapping[str, object] | None = None,
        next_action: str = "curator_coherence",
    ) -> None:
        self.status = status
        self.detail = detail
        self.expected = dict(expected or {})
        self.observed = dict(observed or {})
        self.next_action = next_action
        super().__init__(detail)

    def response_fields(self) -> dict[str, object]:
        """Project the bounded refusal facts without duplicating error-family logic."""

        result: dict[str, object] = {
            "status": self.status,
            "detail": self.detail,
            "nextAction": self.next_action,
        }
        if self.expected:
            result["expected"] = self.expected
        if self.observed:
            result["observed"] = self.observed
        return result


@dataclass(frozen=True)
class MemoryCandidatePairFailure:
    """Bounded public context for one exact-pair refusal."""

    field: str
    contract_path: str
    expected: Mapping[str, object] | None = None
    observed: Mapping[str, object] | None = None
    next_action: str = "developer-decision"
    next_args: Mapping[str, object] | None = None


class MemoryCandidatePairError(AgentsRememberError):
    """An exact contract-owned code/memory pair is absent, stale, or contradictory."""

    def __init__(
        self,
        status: str,
        detail: str,
        *,
        failure: MemoryCandidatePairFailure,
    ) -> None:
        self.status = status
        self.field = failure.field
        self.detail = detail
        self.contract_path = failure.contract_path
        self.expected = dict(failure.expected or {})
        self.observed = dict(failure.observed or {})
        self.next_action = failure.next_action
        self.next_args = dict(failure.next_args or {})
        super().__init__(detail)

    def response_fields(self) -> dict[str, object]:
        """Project the one canonical public shape for an exact-pair refusal."""

        result: dict[str, object] = {
            "pairStatus": self.status,
            "pairField": self.field,
            "contractPath": self.contract_path,
            "detail": self.detail,
            "nextAction": self.next_action,
        }
        if self.expected:
            result["expected"] = self.expected
        if self.observed:
            result["observed"] = self.observed
        if self.next_args:
            result["nextArgs"] = self.next_args
        return result


class CuratorCoherencePairError(CuratorCoherenceError):
    """A curator-coherence refusal caused by the shared exact-pair validator."""

    def __init__(self, error: MemoryCandidatePairError) -> None:
        self.pair_error = error
        super().__init__(
            error.status,
            error.detail,
            expected=error.expected,
            observed=error.observed,
            next_action=error.next_action,
        )

    def response_fields(self) -> dict[str, object]:
        result = {
            **super().response_fields(),
            "pairField": self.pair_error.field,
        }
        if self.pair_error.next_args:
            result["nextArgs"] = self.pair_error.next_args
        return result


class FinalCertificationError(AgentsRememberError):
    """The final full memory-coherence certification refused or was blocked."""

    def __init__(
        self,
        status: str,
        detail: str,
        *,
        expected: Mapping[str, object] | None = None,
        observed: Mapping[str, object] | None = None,
        next_action: str = "memory_quality_check",
    ) -> None:
        self.status = status
        self.detail = detail
        self.expected = dict(expected or {})
        self.observed = dict(observed or {})
        self.next_action = next_action
        super().__init__(detail)

    def response_fields(self) -> dict[str, object]:
        """Project the bounded typed Gate-5 refusal without duplicating other families."""
        result: dict[str, object] = {
            "certificationStatus": self.status,
            "detail": self.detail,
            "nextAction": self.next_action,
        }
        if self.expected:
            result["expected"] = self.expected
        if self.observed:
            result["observed"] = self.observed
        return result


class CitationCacheError(AgentsRememberError):
    """A citation cache authority, capacity, or lifecycle lease was invalid."""


class ConversationCompositionError(AgentsRememberError):
    """The app-scoped conversation runtime composition contract was violated.

    Raised when the one immutable runtime authority is retrieved before
    installation, installed a second time, replaced by a foreign object, or
    constructed without a required authority. These are composition bugs that
    must fail at startup or request entry, never silently at first use.
    """


class TokenizerVocabularyError(AgentsRememberError):
    """The tiktoken vocabulary a token counter needs is not the one vendored here.

    Raised instead of letting tiktoken download the vocabulary it cannot find. The
    counter is built while the MCP tool surface is still importing, so a download there
    is a network round trip on the server's startup path -- the thing that made a cold
    container, an offline machine and a hermetic CI job unable to start the server.
    A build that failed to ship the file must say so, not work only where egress exists.
    """


class GrammarUnavailableError(AgentsRememberError):
    """A language the citation check declares it parses has no grammar to parse it with.

    Raised instead of falling back to occurrence matching or to a second parser. The
    check's whole value is telling a DEFINITION from a MENTION, and a run that quietly
    lost that for one language would report the same "ok" over a scope nobody stated.
    The grammars are ordinary wheels pinned in ``mcp/pyproject.toml``; nothing about the
    parse path reaches the network, so this means an incomplete install.
    """


class HarnessControlError(AgentsRememberError):
    """The hosted harness control contract or exact-session identity was violated."""


class HarnessControlClientError(HarnessControlError):
    """The serving process lost an exact-session IPC request before or after its first byte.

    ``may_have_sent`` is retry-safety evidence: callers may retry a pre-write failure, while a
    post-write failure must remain ``unknown`` until the same request id is reconciled.
    """

    def __init__(self, detail: str, *, may_have_sent: bool) -> None:
        super().__init__(detail)
        self.may_have_sent = may_have_sent


class HarnessAdapterDisconnectedError(HarnessControlError):
    """A protocol adapter disconnected before or after a prompt might have been sent."""

    def __init__(
        self,
        detail: str,
        *,
        may_have_sent: bool,
        vendor_correlation_id: str | None = None,
    ) -> None:
        super().__init__(detail)
        self.may_have_sent = may_have_sent
        self.vendor_correlation_id = vendor_correlation_id


class HarnessAdapterBusyError(HarnessControlError):
    """An adapter's final write-boundary guard proved that zero operation bytes were sent.

    The common submission authority may move ``dispatching`` back to ``queued`` only for this
    typed error.  A generic exception cannot safely make that claim because vendor bytes might
    already have crossed the transport.
    """

    def __init__(self, detail: str, *, may_have_sent: bool = False) -> None:
        if may_have_sent:
            raise ValueError("HarnessAdapterBusyError must certify may_have_sent=False")
        super().__init__(detail)
        self.may_have_sent = False


class HarnessRequestConflictError(HarnessControlError):
    """A retained request id was reused with a different immutable source or payload."""


class HarnessInteractionNotPendingError(HarnessControlError):
    """An interaction response named an interaction that is not the pending one.

    The submission authority raises this (never a generic control error) when a respond
    request arrives with no matching pending interaction, with no active ordinary operation,
    or twice for the same interaction, so the serving route can answer with the honest
    ``not-pending`` conflict status instead of an unavailable claim.
    """


class HarnessBridgeEpochMismatchError(HarnessControlError):
    """A caller addressed lifecycle state from a replaced hosted runner generation."""

    def __init__(self, expected: str, actual: str) -> None:
        super().__init__(
            f"submission authority epoch changed (expected {expected!r}, actual {actual!r})"
        )
        self.expected = expected
        self.actual = actual


class CodexAppServerError(HarnessControlError):
    """The negotiated Codex app-server protocol or its configured contract was violated."""


class CodexAppServerRpcError(CodexAppServerError):
    """A correlated Codex JSON-RPC request returned an error response."""

    def __init__(self, method: str, code: int, message: str) -> None:
        super().__init__(f"Codex app-server {method} failed ({code}): {message}")
        self.method = method
        self.code = code


class NativeHistoryUnavailable(CodexAppServerError):
    """One native-history read is unavailable without invalidating the shared adapter."""

    def __init__(self, detail: str, *, code: str = "unavailable") -> None:
        super().__init__(detail)
        self.code = code


class NativeHistoryLimitExceeded(NativeHistoryUnavailable):
    """One native-history unit crossed its explicit bounded-materialization contract."""

    def __init__(
        self,
        detail: str,
        *,
        actual_bytes: int,
        limit_bytes: int,
    ) -> None:
        super().__init__(detail, code="materialization-limit")
        self.actual_bytes = actual_bytes
        self.limit_bytes = limit_bytes


class CapsuleCompilationError(AgentsRememberError):
    """A role capsule could not be compiled, or an admitted input refused to build.

    A compilation failure never produces a partially valid capsule: the caller
    receives this typed refusal instead of content. ``status`` is a stable,
    branchable code (``unknown-role``, ``missing-required-instruction``,
    ``equal-authority-contradiction``, ...); ``detail`` names the exact defect for
    an operator who does not know the internals; ``next_action`` names the owner
    that has to change something. ``conflicts`` carries the structured
    contradiction rows when the refusal is a stopped conflict.
    """

    def __init__(
        self,
        status: str,
        detail: str,
        *,
        next_action: str = "",
        conflicts: Sequence[Mapping[str, object]] = (),
    ) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.next_action = next_action
        self.conflicts = tuple(MappingProxyType(dict(row)) for row in conflicts)

    def render(self) -> str:
        """The one-line operator-facing projection of this refusal."""

        if self.next_action:
            return f"{self.status}: {self.detail} (remedy: {self.next_action})"
        return f"{self.status}: {self.detail}"

    def response_fields(self) -> dict[str, object]:
        """The bounded refusal facts, without lower-layer diagnostics."""

        fields: dict[str, object] = {"status": self.status, "detail": self.detail}
        if self.next_action:
            fields["nextAction"] = self.next_action
        if self.conflicts:
            fields["conflicts"] = [dict(row) for row in self.conflicts]
        return fields


class CapsuleManifestError(CapsuleCompilationError):
    """The canonical composition manifest (or a declared source path) was invalid."""


class CapsuleSourceError(CapsuleCompilationError):
    """A canonical instruction source was absent, unreadable, or outside its root."""


class TaskProjectionSourceError(AgentsRememberError):
    """An admitted task/worktree binding could not be projected into task context.

    A projection is either complete or refused: there is no partial projection and
    no fallback to another branch, another task revision or a broader scope.
    ``status`` is a stable, branchable code; the authoritative vocabulary is the
    registry in
    :mod:`agents_remember.application.task_projection.statuses`
    (``PROJECTION_STATUSES``), which every raise site imports its code from, so a
    second spelling or an unregistered code is a test failure rather than drift.
    ``detail`` names the exact defect for an operator who does not know the
    internals; ``next_action`` names the owner that has to change something. The
    projection never repairs what it could not resolve, so the current task
    document is untouched by any of these refusals.
    """

    def __init__(
        self,
        status: str,
        detail: str,
        *,
        next_action: str = "",
        owner_status: str = "",
    ) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.next_action = next_action
        # The status the existing AR owner raised, when this refusal wraps one, so a
        # caller can still branch on the owner's own vocabulary instead of matching prose.
        self.owner_status = owner_status

    def render(self) -> str:
        """The one-line operator-facing projection of this refusal."""

        if self.next_action:
            return f"{self.status}: {self.detail} (remedy: {self.next_action})"
        return f"{self.status}: {self.detail}"

    def response_fields(self) -> dict[str, object]:
        """The bounded refusal facts a transport may publish."""

        fields: dict[str, object] = {"status": self.status, "detail": self.detail}
        if self.next_action:
            fields["nextAction"] = self.next_action
        if self.owner_status:
            fields["ownerStatus"] = self.owner_status
        return fields


class AtomicReplaceError(AgentsRememberError, OSError):
    """A publish failed on **one named leg**, and the destination's state is reported with it.

    ``kernel.atomic_write.atomic_replace`` does two things that can fail independently: the
    rename that makes the new bytes reachable, and the directory flush that makes the *name*
    durable. Both used to surface as one indistinguishable ``OSError``, so a caller could not
    tell "the destination never changed" from "the destination already changed but the rename
    is not durable" -- the second is a recoverable publication whose retry must not be
    confused with the first, and a caller that retried the first as if it were the second, or
    reported either as a plain replace failure, was describing a state it had not measured.

    ``leg`` names the failure (``replace`` or ``directory-fsync``) and ``destinationState``
    states what the destination holds at the moment of the raise: ``previous-bytes`` (the
    rename did not happen), ``new-bytes`` (the rename completed and its name is not yet
    durable) or ``source-absent`` (the rename completed and the source path was consumed).
    It subclasses ``OSError`` as well as the domain family so an existing ``except OSError``
    around a publish keeps observing the failure it always did.
    """

    def __init__(
        self,
        leg: str,
        destination: str,
        *,
        destination_state: str,
        detail: str,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(detail)
        self.leg = leg
        self.destination = destination
        self.destination_state = destination_state
        self.detail = detail
        self.cause = cause

    def response_fields(self) -> dict[str, object]:
        """The bounded facts a caller branches on, with the destination's state named."""

        return {
            "leg": self.leg,
            "destination": self.destination,
            "destinationState": self.destination_state,
        }


class MemoryModeUnsupportedError(AgentsRememberError):
    """A caller asked for a memory mode this product removed.

    The refusal names the removed mode, the supported set and the route out, so an operator
    who never saw the old vocabulary can still act. It deliberately carries the *artifact*
    that records the removed mode when one exists -- a contract path, a settings path or a
    memory root -- because existing state is reported, never silently migrated: the caller
    must be able to point at the exact file or directory that still says ``internal``.

    ``status`` is the stable, branchable code every surface publishes for this refusal; it is
    one value rather than a per-surface spelling so a caller can match on it directly.
    """

    status = "memory-mode-unsupported"

    def __init__(
        self,
        detail: str,
        *,
        requested: str,
        supported: Sequence[str],
        artifact: str | None = None,
        remedies: Sequence[str] = (),
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.requested = requested
        self.supported = tuple(supported)
        self.artifact = artifact
        self.remedies = tuple(remedies)

    def render(self) -> str:
        """The one-line operator-facing projection of this refusal."""

        return f"{self.status}: {self.detail}"

    def response_fields(self) -> dict[str, object]:
        """The bounded refusal facts a transport may publish."""

        fields: dict[str, object] = {
            "status": self.status,
            "detail": self.detail,
            "requested": self.requested,
            "supported": list(self.supported),
            "remedies": list(self.remedies),
        }
        if self.artifact is not None:
            fields["artifact"] = self.artifact
        return fields
