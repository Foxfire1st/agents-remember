"""Runtime type-hint resolution contracts for dataclasses and record-kind vocabularies."""

from __future__ import annotations

from dataclasses import fields
from typing import Final, get_args, get_type_hints

from agents_remember.application.knowledge_writer import authoring_rows
from agents_remember.models.knowledge_files.ids import RecordKind
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    EnclosurePublicationArtifacts,
)
from agents_remember.worktrees.outside_scratch import OutsideScratchRoot
from agents_remember_test_support.code_quality.quality_plan import CheckConfig
from agents_remember_test_support.code_quality.targeted import TargetedScopeResult


def test_enclosure_publication_artifacts_outside_scratch_type_hint_resolves() -> None:
    """The outside_scratch field keeps its public name and resolves in class scope."""

    field_names = {field.name for field in fields(EnclosurePublicationArtifacts)}
    assert "outside_scratch" in field_names

    module_hints = get_type_hints(EnclosurePublicationArtifacts)
    class_scope_hints = get_type_hints(
        EnclosurePublicationArtifacts,
        localns=dict(vars(EnclosurePublicationArtifacts)),
    )
    assert module_hints["outside_scratch"] == OutsideScratchRoot | None
    assert class_scope_hints["outside_scratch"] == OutsideScratchRoot | None


def test_check_config_targeted_scope_type_hint_resolves() -> None:
    """The targeted and targeted_scope fields keep public names and resolve in class scope."""

    field_names = {field.name for field in fields(CheckConfig)}
    assert {"targeted", "targeted_scope"}.issubset(field_names)

    module_hints = get_type_hints(CheckConfig)
    class_scope_hints = get_type_hints(CheckConfig, localns=dict(vars(CheckConfig)))
    assert module_hints["targeted"] is bool
    assert module_hints["targeted_scope"] == TargetedScopeResult | None
    assert class_scope_hints["targeted"] is bool
    assert class_scope_hints["targeted_scope"] == TargetedScopeResult | None


def test_record_row_kinds_typed_as_canonical_record_kind_tuple() -> None:
    """RECORD_ROW_KINDS is typed with canonical RecordKind and only names valid members."""

    canonical_kinds = set(get_args(RecordKind))
    assert authoring_rows.RECORD_ROW_KINDS
    assert set(authoring_rows.RECORD_ROW_KINDS).issubset(canonical_kinds)

    hints = get_type_hints(authoring_rows)
    assert hints["RECORD_ROW_KINDS"] == Final[tuple[RecordKind, ...]]
