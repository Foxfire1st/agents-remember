"""Durable selection of private closeout work."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from agents_remember.models.certification.base import FrozenContractModel
from agents_remember.models.certification.references import CertificateObjectReference
from agents_remember.models.lifecycles.preparation import PreparationLeg

_Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
_GitObject = Annotated[str, Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")]
_LEG_ORDER: tuple[PreparationLeg, ...] = ("code", "memory-content")


class SelectedPreparation(FrozenContractModel):
    """One exact intent and its selected output."""

    leg: PreparationLeg
    intent: CertificateObjectReference
    output: CertificateObjectReference | None = None

    @model_validator(mode="after")
    def _require_exact_references(self) -> Self:
        if self.intent.kind != "preparation-intent":
            raise ValueError("preparation selection requires an exact intent reference")
        if self.output is not None and self.output.kind != "prepared-output":
            raise ValueError("preparation output requires an exact prepared-output reference")
        return self


class OperationPreparationState(FrozenContractModel):
    """Unfinished private work, independent of published mutation authority."""

    schemaVersion: Literal["closeout-preparation-selection/v1"] = (
        "closeout-preparation-selection/v1"
    )
    operationKey: _Digest
    generation: int = Field(strict=True, ge=1)
    legs: tuple[SelectedPreparation, ...] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def _require_ordered_legs(self) -> Self:
        if tuple(item.leg for item in self.legs) != _LEG_ORDER[: len(self.legs)]:
            raise ValueError("private preparation must retain the ordered code/content prefix")
        if any(item.output is None for item in self.legs[:-1]):
            raise ValueError("a later private leg requires the prior selected output")
        return self


class PreparedCodeRetention(FrozenContractModel):
    """One proved handoff of a private code output to a corrective generation.

    The old preparation intent/output remain immutable certificate objects.  This
    record binds the successor's re-bound objects to that exact predecessor and
    records the Git identity that the owner re-observed before replacement.  It
    is deliberately specific to the code leg; memory outputs still
    have to be prepared by their normal successor owner.
    """

    schemaVersion: Literal["closeout-prepared-code-retention/v1"] = (
        "closeout-prepared-code-retention/v1"
    )
    predecessorOperationKey: _Digest
    predecessorGeneration: int = Field(strict=True, ge=1)
    predecessorFingerprint: _Digest
    sourceIntent: CertificateObjectReference
    sourceOutput: CertificateObjectReference
    successorIntent: CertificateObjectReference
    successorOutput: CertificateObjectReference
    commit: _GitObject
    tree: _GitObject
    committerDate: str = Field(min_length=1, max_length=64)
    rawCommitSha256: _Digest

    @model_validator(mode="after")
    def _require_exact_references(self) -> Self:
        for reference, kind in (
            (self.sourceIntent, "preparation-intent"),
            (self.sourceOutput, "prepared-output"),
            (self.successorIntent, "preparation-intent"),
            (self.successorOutput, "prepared-output"),
        ):
            if reference.kind != kind:
                raise ValueError(f"prepared code retention requires an exact {kind} reference")
        return self


def validate_preparation_owner(
    selected: OperationPreparationState | None,
    operation_kind: str,
    operation_key: str,
    generation: int,
    *,
    has_certification: bool,
) -> None:
    if selected is not None and (
        operation_kind != "closeout"
        or (selected.operationKey, selected.generation) != (operation_key, generation)
    ):
        raise ValueError("private preparation must belong to this exact closeout generation")
    if selected is not None and not has_certification:
        raise ValueError("private preparation requires the original selected certification")


def validate_prepared_code_retention(
    retained: PreparedCodeRetention | None,
    selected: OperationPreparationState | None,
    owner: tuple[str, str, int, str],
) -> None:
    """Validate the journal-side shape of a successor's proved code handoff."""

    if retained is None:
        return
    operation_kind, operation_key, generation, predecessor_fingerprint = owner
    if operation_kind != "closeout":
        raise ValueError("prepared code retention belongs only to closeout successors")
    if retained.predecessorGeneration != generation - 1:
        raise ValueError("prepared code retention must name the immediately prior generation")
    if not predecessor_fingerprint or retained.predecessorFingerprint != predecessor_fingerprint:
        raise ValueError("prepared code retention must bind the archived predecessor")
    if selected is None or (selected.operationKey, selected.generation) != (
        operation_key,
        generation,
    ):
        raise ValueError("prepared code retention requires the successor's selected preparation")
    if not selected.legs or selected.legs[0].leg != "code":
        raise ValueError("prepared code retention requires a code-first successor prefix")
    leg = selected.legs[0]
    if leg.intent != retained.successorIntent or leg.output != retained.successorOutput:
        raise ValueError("prepared code retention does not bind the selected successor output")
