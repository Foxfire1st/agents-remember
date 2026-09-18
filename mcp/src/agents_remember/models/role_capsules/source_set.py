"""Validation of an admitted source set against the manifest's declared plan.

The compiler never derives the file list from the manifest. Deriving it would make
"a required block is missing" unfalsifiable — the compiler would only ever see the
subset the manifest asked for, so the check could not fail. Instead the plan is
locked first (:func:`~agents_remember.models.role_capsules.sources.CapsuleDeclaredInstruction`),
the admitted paths are supplied explicitly, and this module proves the two agree in
both directions:

* every path the manifest routes *to* is a path that was admitted, as the identity
  the manifest says it carries; and
* every path that was admitted is one the manifest declares.

It also refuses a source whose declared composition root disagrees with the root it
was routed from, and a repository specialization that was selected without being
admitted on the binding. Every refusal is a typed value, because a source set that
is merely "mostly right" is exactly the state that produces a capsule missing
mandatory material.
"""

from __future__ import annotations

from collections.abc import Mapping

from agents_remember.errors import CapsuleCompilationError
from agents_remember.models.role_capsules.manifest import CapsuleCompositionManifest
from agents_remember.models.role_capsules.sources import (
    CapsuleDeclaredInstruction,
    CapsuleSource,
    instruction_identity,
    root_of_identity,
    skills_declared_identity,
    specializations_declared_identity,
)
from agents_remember.models.role_capsules.statuses import (
    STATUS_DUPLICATE_IDENTITY,
    STATUS_MISSING_REQUIRED_INSTRUCTION,
    STATUS_SOURCE_NOT_DECLARED,
    STATUS_SOURCE_ROOT_MISMATCH,
    STATUS_SPECIALIZATION_NOT_ADMITTED,
)
from agents_remember.models.role_capsules.types import (
    CapsuleBinding,
    CapsuleBlockIdentity,
    CapsuleSourceAdmission,
    CapsuleSourceSelection,
)


def sources_by_path(sources: tuple[CapsuleSource, ...]) -> Mapping[str, CapsuleSource]:
    """Index admitted sources by path, refusing a path admitted twice."""

    by_path: dict[str, CapsuleSource] = {}
    for source in sources:
        if source.path in by_path:
            raise CapsuleCompilationError(
                status=STATUS_DUPLICATE_IDENTITY,
                detail=f"source path {source.path!r} was admitted twice",
                next_action="admit each source path exactly once",
            )
        by_path[source.path] = source
    return by_path


def identity_index(
    parsed: CapsuleCompositionManifest, selection: CapsuleSourceSelection
) -> Mapping[str, CapsuleBlockIdentity]:
    """Map every legitimately admitted path to the identity the manifest gives it.

    Built from the manifest, never from the file's own name: that is what lets the
    compiler say "you admitted a file that is not this identity" instead of
    silently composing whatever arrived on disk.
    """

    identities: dict[str, CapsuleBlockIdentity] = {}
    for name, core in parsed.core.items():
        identities[core.source] = instruction_identity("core", name)
    for role_name, role in parsed.roles.items():
        identities[role.file] = instruction_identity("role", role_name)
    for operation_name, operation in parsed.operations.items():
        identities[operation.source] = instruction_identity("operation", operation_name)
    for name, entry in parsed.specializations.items():
        identities[entry.source] = instruction_identity("specialization", name)
    for name, entry in parsed.skills.items():
        identities[entry.source] = skills_declared_identity(entry.origin, name)
    for path in selection.specialization:
        identities[path] = specializations_declared_identity(path)
    return identities


def admit_source_set(
    parsed: CapsuleCompositionManifest,
    request: CapsuleSourceAdmission,
    by_path: Mapping[str, CapsuleSource],
    identities: Mapping[str, CapsuleBlockIdentity],
    declared: tuple[CapsuleDeclaredInstruction, ...],
) -> None:
    """Prove the admitted source set satisfies the locked plan, or refuse."""

    binding = request.binding
    selection = request.selection
    _require_manifest_admitted(selection, by_path, declared)
    _require_identities_agree(selection, by_path, identities)
    _require_declared_present(by_path, identities, declared)
    _require_declared_skills_present(parsed, by_path, identities)
    _require_specializations_admitted(binding, selection)


def _require_manifest_admitted(
    selection: CapsuleSourceSelection,
    by_path: Mapping[str, CapsuleSource],
    declared: tuple[CapsuleDeclaredInstruction, ...],
) -> None:
    if not by_path:
        raise CapsuleCompilationError(
            status=STATUS_MISSING_REQUIRED_INSTRUCTION,
            detail=(
                f"no instruction source bytes were admitted for {len(declared)} declared "
                f"instruction(s): {_quoted(tuple(item.identity for item in declared))}"
            ),
            next_action="admit the canonical instruction sources the manifest declares",
        )
    if selection.manifest not in by_path:
        raise CapsuleCompilationError(
            status=STATUS_SOURCE_NOT_DECLARED,
            detail=(
                f"the admitted source set does not include the composition manifest "
                f"{selection.manifest!r}, so its selections cannot be validated against it"
            ),
            next_action=(
                "admit the canonical composition manifest alongside the instruction sources it "
                "routes"
            ),
        )


def _require_identities_agree(
    selection: CapsuleSourceSelection,
    by_path: Mapping[str, CapsuleSource],
    identities: Mapping[str, CapsuleBlockIdentity],
) -> None:
    undeclared = sorted(
        path for path in by_path if path not in identities and path != selection.manifest
    )
    if undeclared:
        raise CapsuleCompilationError(
            status=STATUS_SOURCE_NOT_DECLARED,
            detail=(
                "the admitted source set contains paths the composition manifest does not declare: "
                f"{_quoted(tuple(undeclared))}"
            ),
            next_action=(
                "admit only the sources the manifest declares, or declare them in the manifest; a "
                "source the manifest does not know has no defined place in the composition order"
            ),
        )
    mismatched = sorted(
        f"{path} (read as {by_path[path].composition_root!r}, routed as "
        f"{root_of_identity(identity)!r})"
        for path, identity in identities.items()
        if path in by_path and by_path[path].composition_root != root_of_identity(identity)
    )
    if mismatched:
        raise CapsuleCompilationError(
            status=STATUS_SOURCE_ROOT_MISMATCH,
            detail=(
                "admitted sources disagree with the manifest about the composition root they were "
                "read as: " + "; ".join(mismatched)
            ),
            next_action="read each source as the composition root the manifest routes it from",
        )


def _require_declared_present(
    by_path: Mapping[str, CapsuleSource],
    identities: Mapping[str, CapsuleBlockIdentity],
    declared: tuple[CapsuleDeclaredInstruction, ...],
) -> None:
    admitted = {identities[path] for path in by_path if path in identities}
    missing = sorted(item.identity for item in declared if item.identity not in admitted)
    if missing:
        raise CapsuleCompilationError(
            status=STATUS_MISSING_REQUIRED_INSTRUCTION,
            detail=(
                "required instruction blocks were not admitted, so no capsule can be compiled: "
                f"{_quoted(tuple(missing))}"
            ),
            next_action=(
                "admit every block this role, operation and admission require; a required obligation "
                "is never dropped to fit a size target"
            ),
        )


def _require_declared_skills_present(
    parsed: CapsuleCompositionManifest,
    by_path: Mapping[str, CapsuleSource],
    identities: Mapping[str, CapsuleBlockIdentity],
) -> None:
    """A declared skill must have its root bytes admitted, or its revision is a fiction.

    Checked for every declared skill rather than only the selected seat's, because a
    skill file is a *declared source* like any other: admitting an incomplete source
    set is the condition this whole module exists to refuse. The compiler additionally
    refuses a *referenced* skill that is missing, so both the metadata plane and the
    compiled plane fail closed.
    """

    admitted = {identities[path] for path in by_path if path in identities}
    missing = sorted(
        skills_declared_identity(entry.origin, name)
        for name, entry in parsed.skills.items()
        if skills_declared_identity(entry.origin, name) not in admitted
    )
    if missing:
        raise CapsuleCompilationError(
            status=STATUS_MISSING_REQUIRED_INSTRUCTION,
            detail=(
                "declared skill root files were not admitted, so no content-addressed revision "
                f"exists for them: {_quoted(tuple(missing))}"
            ),
            next_action=(
                "admit each declared skill's root file; a skill reference without a revision is not "
                "a reference"
            ),
        )


def _require_specializations_admitted(
    binding: CapsuleBinding, selection: CapsuleSourceSelection
) -> None:
    unadmitted = sorted(
        path
        for path in selection.specialization
        if specializations_declared_identity(path) not in binding.specializations
    )
    if unadmitted:
        raise CapsuleCompilationError(
            status=STATUS_SPECIALIZATION_NOT_ADMITTED,
            detail=(
                "repository specialization sources were selected without being admitted by the "
                f"binding: {_quoted(tuple(unadmitted))}"
            ),
            next_action=(
                "admit each specialization identity on the binding; repository specialization is an "
                "explicit admission, never an environmental default"
            ),
        )


def _quoted(names: tuple[str, ...]) -> str:
    return ", ".join(repr(name) for name in names) if names else "<none>"


__all__ = ["admit_source_set", "identity_index", "sources_by_path"]
