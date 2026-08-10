from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar, Literal

from pydantic import Field, model_validator

from agents_remember.models.conversations.identity import (
    ActiveConversationRef,
    NonEmptyText,
    WireModel,
)
from agents_remember.models.conversations.primitives import OperationFingerprint, PositiveRevision


class OpenConversationOperation(WireModel):
    _phases_by_outcome: ClassVar[Mapping[str, frozenset[str]]] = {
        "pending": frozenset({"requested", "launching", "catalog-wait"}),
        "opened": frozenset({"opened"}),
        "unsupported": frozenset({"failed"}),
        "stale-identity": frozenset({"failed"}),
        "launch-failed": frozenset({"retiring", "failed"}),
        "identity-mismatch": frozenset({"retiring", "failed"}),
        "timeout-unknown": frozenset({"launching", "catalog-wait", "unknown"}),
        "request-conflict": frozenset({"failed"}),
    }
    _failure_rollbacks: ClassVar[Mapping[tuple[str, str, bool], frozenset[str]]] = {
        ("launch-failed", "failed", False): frozenset({"not-needed"}),
        ("launch-failed", "retiring", True): frozenset({"retire-pending"}),
        ("launch-failed", "failed", True): frozenset({"retired", "retire-failed"}),
        ("identity-mismatch", "retiring", True): frozenset({"retire-pending"}),
        ("identity-mismatch", "failed", True): frozenset({"retired", "retire-failed"}),
    }

    request_id: NonEmptyText
    request_fingerprint: OperationFingerprint
    revision: PositiveRevision
    phase: Literal[
        "requested", "launching", "catalog-wait", "opened", "retiring", "failed", "unknown"
    ]
    outcome: Literal[
        "pending",
        "opened",
        "unsupported",
        "stale-identity",
        "launch-failed",
        "identity-mismatch",
        "timeout-unknown",
        "request-conflict",
    ]
    ar_session_id: str | None = None
    bridge_epoch: str | None = None
    identity: ActiveConversationRef | None = None
    catalog_generation: int | None = Field(default=None, ge=1)
    rollback: Literal["not-needed", "retired", "retire-pending", "retire-failed"]
    detail: str | None = None

    @model_validator(mode="after")
    def require_phase_outcome_product(self) -> OpenConversationOperation:
        if self.phase not in self._phases_by_outcome[self.outcome]:
            raise ValueError("open phase/outcome product is contradictory")
        return self

    @model_validator(mode="after")
    def require_complete_identity_tuple(self) -> OpenConversationOperation:
        identity_parts = (self.ar_session_id, self.bridge_epoch, self.identity)
        if any(part is not None for part in identity_parts) and not all(
            part is not None for part in identity_parts
        ):
            raise ValueError("open identity tuple must be complete")
        return self

    @model_validator(mode="after")
    def require_matching_identity_tuple(self) -> OpenConversationOperation:
        if self.identity is not None and (
            self.ar_session_id != self.identity.ar_session_id
            or self.bridge_epoch != self.identity.bridge_epoch
        ):
            raise ValueError("open identity tuple must agree exactly")
        return self

    @model_validator(mode="after")
    def require_identity_for_catalog_generation(self) -> OpenConversationOperation:
        if self.catalog_generation is not None and self.identity is None:
            raise ValueError("catalog generation requires an exact open identity")
        return self

    @model_validator(mode="after")
    def forbid_identity_for_no_launch_outcome(self) -> OpenConversationOperation:
        if self.outcome in {"unsupported", "stale-identity"} and self.identity is not None:
            raise ValueError(f"{self.outcome} cannot carry a spawned identity")
        return self

    @model_validator(mode="after")
    def require_opened_catalog_proof(self) -> OpenConversationOperation:
        if self.outcome == "opened" and (
            self.identity is None
            or self.catalog_generation is None
            or self.rollback != "not-needed"
        ):
            raise ValueError("opened requires exact identity, catalog proof, and no rollback")
        return self

    @model_validator(mode="after")
    def require_coherent_rollback(self) -> OpenConversationOperation:  # pragma: no cover
        if self.outcome not in {"launch-failed", "identity-mismatch"}:
            if self.rollback != "not-needed":
                raise ValueError("rollback requires an exact failed spawned identity")
            return self

        product = (self.outcome, self.phase, self.identity is not None)
        allowed_rollbacks = self._failure_rollbacks.get(product, frozenset())
        if self.rollback not in allowed_rollbacks:
            raise ValueError("rollback state must agree with failure identity and phase")
        if self.outcome == "identity-mismatch" and self.catalog_generation is None:
            raise ValueError("identity mismatch requires exact catalog generation")
        return self
