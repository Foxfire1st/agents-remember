"""Canonical source parsing for the role-capsule composition manifest.

``skills/l-01-agent-lifecycles/composition-manifest.json`` is authored metadata:
it routes a role and an operation to the source files that carry the instruction,
and it contains no instruction prose of its own ("exactly one source per
instruction"). This module parses those bytes into typed values without touching
the filesystem, so the manifest is inspectable, diffable and testable on its own.

The declared vocabulary is authoritative over the manifest, not the other way
round: the parser is handed the frozen role and operation sets and refuses a
manifest that disagrees. A manifest edit can therefore not quietly mint a new role
or a ninth operation.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from agents_remember.errors import CapsuleManifestError
from agents_remember.models.role_capsules.types import (
    CAPSULE_COMPOSITION_ORDER,
    CapsuleCompositionRoot,
    CapsuleOperation,
    CapsuleRole,
    CapsuleSeatKind,
    CapsuleToolId,
)
from agents_remember.models.role_capsules.vocabulary import (
    CAPSULE_LAUNCHER_MODE,
    CAPSULE_OPERATIONS,
    CAPSULE_ROLES,
)

COMPOSITION_MANIFEST_SCHEMA = "ar-role-capsule-composition/v1"
MANIFEST_INVALID = "manifest-invalid"
MANIFEST_VOCABULARY_MISMATCH = "manifest-vocabulary-mismatch"
MANIFEST_INCONSISTENT_APPLICABILITY = "manifest-inconsistent-applicability"


@dataclass(frozen=True, slots=True)
class CapsuleCoreEntry:
    """One shared core block: a name and the source file that carries it."""

    name: str
    source: str
    purpose: str


@dataclass(frozen=True, slots=True)
class CapsuleOperationEntry:
    """One operation block and the roles the manifest says may run it."""

    name: CapsuleOperation
    source: str
    purpose: str
    applies_to_roles: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CapsuleRoleEntry:
    """One role's routing metadata: its block, its shared core, its operations, its requests.

    ``operations`` is the authoritative applicability list — the set of
    operations this role may be compiled for. ``applies_to_roles`` on the
    operation side must agree with it, and the parser refuses a manifest where the
    two disagree rather than letting the compiler resolve the conflict at
    selection time.
    """

    role: CapsuleRole
    file: str
    altitude: str
    core: tuple[str, ...]
    operations: tuple[CapsuleOperation, ...]
    tools: tuple[CapsuleToolId, ...] = ()
    skills: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CapsuleLauncherEntry:
    """The ambient launcher: a routing condition with its own core composition."""

    is_role: bool
    routing_condition: str
    instruction_source: str
    core: tuple[str, ...]
    operations: tuple[CapsuleOperation, ...]


@dataclass(frozen=True, slots=True)
class CapsuleSkillEntry:
    """One declared skill a role may reference: origin, URI, and the root file to digest.

    A skill reference is a *pointer to separately delivered content*, so its identity
    is server/repository origin plus skill URI — a bare name would collide across
    servers. ``source`` is the skill's root instruction file; its content digest is
    what a reference's ``revision`` means, which is what makes the reference
    content-addressed rather than a name that can drift from what it names.
    """

    name: str
    origin: str
    uri: str
    source: str = "SKILL.md"


@dataclass(frozen=True, slots=True)
class CapsuleSpecializationEntry:
    """One declared repository specialization: the file and the identity it carries."""

    name: str
    source: str
    purpose: str


@dataclass(frozen=True, slots=True)
class CapsuleCompositionManifest:
    """The parsed canonical composition manifest.

    This is the *on-disk* metadata (``composition-manifest.json``). It is not the
    compiler's diagnostic output, which is
    :class:`~agents_remember.models.role_capsules.types.CapsuleManifest`; the two
    have similar names and completely different jobs.
    """

    schema: str
    role_order: tuple[CapsuleRole, ...]
    roles: Mapping[str, CapsuleRoleEntry]
    operations: Mapping[str, CapsuleOperationEntry]
    core: Mapping[str, CapsuleCoreEntry]
    launcher: CapsuleLauncherEntry
    specializations: Mapping[str, CapsuleSpecializationEntry]
    skills: Mapping[str, CapsuleSkillEntry]

    def skill_entry(self, skill: str) -> CapsuleSkillEntry:
        """The declared skill ``skill``, refusing a name the manifest never declared."""

        try:
            return self.skills[skill]
        except KeyError as error:
            raise CapsuleManifestError(
                status=MANIFEST_VOCABULARY_MISMATCH,
                detail=(
                    f"skill {skill!r} is not declared by this manifest "
                    f"(declared: {_quoted(tuple(sorted(self.skills)))})"
                ),
                next_action="declare the skill with its origin, uri and source, or stop naming it",
            ) from error

    def composing_roots(self, seat_kind: CapsuleSeatKind) -> tuple[CapsuleCompositionRoot, ...]:
        """Every composition root this seat kind can actually contribute a block from.

        A dispatched role seat composes shared core, its own role block, the operation
        it runs and any admitted repository specialization. The ambient launcher has
        **no role block** — it is a routing condition, not a tenth role — so telling a
        caller that ``role`` is available for it would contradict the boundary the
        compiler enforces everywhere else.
        """

        if seat_kind == CAPSULE_LAUNCHER_MODE:
            return tuple(root for root in CAPSULE_COMPOSITION_ORDER if root != "role")
        return CAPSULE_COMPOSITION_ORDER


def parse_composition_manifest(payload: bytes) -> CapsuleCompositionManifest:
    """Parse manifest bytes into the typed composition manifest.

    ``payload`` is bytes rather than a path because reading the file belongs to
    the application boundary; this function is pure and total over its input.
    """

    try:
        raw = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CapsuleManifestError(
            status=MANIFEST_INVALID,
            detail=f"composition manifest is not a readable UTF-8 JSON document: {error}",
            next_action="repair the canonical skills manifest; it is authored metadata",
        ) from error
    document = _require_mapping(raw, "composition manifest")
    schema = _require_str(document, "schema")
    if schema != COMPOSITION_MANIFEST_SCHEMA:
        raise CapsuleManifestError(
            status=MANIFEST_VOCABULARY_MISMATCH,
            detail=(
                f"composition manifest declares schema {schema!r}, but this compiler consumes "
                f"{COMPOSITION_MANIFEST_SCHEMA!r}"
            ),
            next_action="a consumer that needs another schema reports it to the manifest owner",
        )

    core = _parse_core(document)
    operations = _parse_operations(document)
    roles = _parse_roles(document, core, operations)
    role_order = _parse_role_order(document, roles)
    launcher = _parse_launcher(document, core, operations)
    specializations = _parse_specializations(document)
    skills = _parse_skills(document)
    _require_operation_applicability_agrees(roles, operations)
    _require_one_path_serves_one_identity(core, operations, roles, specializations, skills)
    _require_role_skills_are_declared(roles, skills)
    return CapsuleCompositionManifest(
        schema=schema,
        role_order=role_order,
        roles=roles,
        operations=operations,
        core=core,
        launcher=launcher,
        specializations=specializations,
        skills=skills,
    )


def _require_mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise CapsuleManifestError(status=MANIFEST_INVALID, detail=f"{label} must be a JSON object")
    return value


def _require_str(document: Mapping[str, object], key: str, label: str = "") -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CapsuleManifestError(
            status=MANIFEST_INVALID,
            detail=f"{label or 'composition manifest'} field {key!r} must be a non-blank string",
        )
    return value


def _require_str_tuple(document: Mapping[str, object], key: str, label: str) -> tuple[str, ...]:
    value = document.get(key)
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise CapsuleManifestError(
            status=MANIFEST_INVALID, detail=f"{label} field {key!r} must be a JSON array"
        )
    entries: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise CapsuleManifestError(
                status=MANIFEST_INVALID,
                detail=f"{label} field {key!r} must contain non-blank strings",
            )
        entries.append(item)
    return tuple(entries)


def _parse_core(document: Mapping[str, object]) -> Mapping[str, CapsuleCoreEntry]:
    # An empty core map needs no check of its own: every role must name at least one
    # core block, so the per-role existence check below refuses it first with a more
    # precise message. A dedicated empty-map guard here would be unreachable code.
    raw = _require_mapping(document.get("core"), "core")
    entries: dict[str, CapsuleCoreEntry] = {}
    for name, block in raw.items():
        body = _require_mapping(block, f"core.{name}")
        entries[name] = CapsuleCoreEntry(
            name=name,
            source=_require_str(body, "source", f"core.{name}"),
            purpose=_require_str(body, "purpose", f"core.{name}"),
        )
    return entries


def _parse_operations(document: Mapping[str, object]) -> Mapping[str, CapsuleOperationEntry]:
    raw = _require_mapping(document.get("operations"), "operations")
    declared = set(raw)
    expected = set(CAPSULE_OPERATIONS)
    if declared != expected:
        raise CapsuleManifestError(
            status=MANIFEST_VOCABULARY_MISMATCH,
            detail=(
                "composition manifest operation vocabulary does not match the frozen set: "
                f"manifest-only={sorted(declared - expected)} "
                f"compiler-only={sorted(expected - declared)}"
            ),
            next_action="the operation vocabulary is frozen; a new operation is a design change",
        )
    entries: dict[str, CapsuleOperationEntry] = {}
    for name, block in raw.items():
        body = _require_mapping(block, f"operations.{name}")
        applies = _require_str_tuple(body, "applies_to_roles", f"operations.{name}")
        unknown = sorted(role for role in applies if role not in CAPSULE_ROLES)
        if unknown:
            raise CapsuleManifestError(
                status=MANIFEST_VOCABULARY_MISMATCH,
                detail=f"operation {name!r} applies to unknown roles: {unknown}",
                next_action="repair the manifest; the role registry is frozen at nine roles",
            )
        operation = cast(CapsuleOperation, name)
        entries[name] = CapsuleOperationEntry(
            name=operation,
            source=_require_str(body, "source", f"operations.{name}"),
            purpose=_require_str(body, "purpose", f"operations.{name}"),
            applies_to_roles=applies,
        )
    return entries


def _parse_roles(
    document: Mapping[str, object],
    core: Mapping[str, CapsuleCoreEntry],
    operations: Mapping[str, CapsuleOperationEntry],
) -> Mapping[str, CapsuleRoleEntry]:
    raw = _require_mapping(document.get("roles"), "roles")
    declared = set(raw)
    expected = set(CAPSULE_ROLES)
    if declared != expected:
        raise CapsuleManifestError(
            status=MANIFEST_VOCABULARY_MISMATCH,
            detail=(
                "composition manifest role registry does not match the frozen registry: "
                f"manifest-only={sorted(declared - expected)} "
                f"compiler-only={sorted(expected - declared)}"
            ),
            next_action="the role registry is exactly nine roles; the launcher is not a tenth",
        )
    entries: dict[str, CapsuleRoleEntry] = {}
    for name, block in raw.items():
        entries[name] = _parse_one_role(
            name, _require_mapping(block, f"roles.{name}"), core, operations
        )
    return entries


def _parse_one_role(
    name: str,
    body: Mapping[str, object],
    core: Mapping[str, CapsuleCoreEntry],
    operations: Mapping[str, CapsuleOperationEntry],
) -> CapsuleRoleEntry:
    label = f"roles.{name}"
    core_names = _require_str_tuple(body, "core", label)
    if not core_names:
        raise CapsuleManifestError(
            status=MANIFEST_VOCABULARY_MISMATCH,
            detail=f"role {name!r} declares no shared core blocks",
            next_action="every role composes the shared core; an empty list is a manifest defect",
        )
    for core_name in core_names:
        if core_name not in core:
            raise CapsuleManifestError(
                status=MANIFEST_VOCABULARY_MISMATCH,
                detail=f"role {name!r} names unknown core block {core_name!r}",
            )
    operation_names = _require_str_tuple(body, "operations", label)
    for operation in operation_names:
        if operation not in operations:
            raise CapsuleManifestError(
                status=MANIFEST_VOCABULARY_MISMATCH,
                detail=f"role {name!r} names unknown operation {operation!r}",
            )
    role = cast(CapsuleRole, name)
    return CapsuleRoleEntry(
        role=role,
        file=_require_str(body, "file", label),
        altitude=_require_str(body, "altitude", label),
        core=core_names,
        operations=cast(tuple[CapsuleOperation, ...], operation_names),
        tools=_require_str_tuple(body, "tools", label),
        skills=_require_str_tuple(body, "skills", label),
    )


def _parse_role_order(
    document: Mapping[str, object], roles: Mapping[str, CapsuleRoleEntry]
) -> tuple[CapsuleRole, ...]:
    order = _require_str_tuple(document, "role_order", "composition manifest")
    if sorted(order) != sorted(roles):
        raise CapsuleManifestError(
            status=MANIFEST_VOCABULARY_MISMATCH,
            detail=(f"role_order and roles disagree: order={sorted(order)} roles={sorted(roles)}"),
            next_action="repair the manifest; the two must name the same nine roles",
        )
    return cast(tuple[CapsuleRole, ...], order)


def _parse_launcher(
    document: Mapping[str, object],
    core: Mapping[str, CapsuleCoreEntry],
    operations: Mapping[str, CapsuleOperationEntry],
) -> CapsuleLauncherEntry:
    body = _require_mapping(document.get("launcher"), "launcher")
    is_role = body.get("is_role")
    if is_role is not False:
        raise CapsuleManifestError(
            status=MANIFEST_VOCABULARY_MISMATCH,
            detail="launcher must declare is_role=false; the role registry is exactly nine roles",
            next_action="the launcher is a routing condition, not a tenth role",
        )
    core_names = _require_str_tuple(body, "core", "launcher")
    for core_name in core_names:
        if core_name not in core:
            raise CapsuleManifestError(
                status=MANIFEST_VOCABULARY_MISMATCH,
                detail=f"launcher names unknown core block {core_name!r}",
            )
    # The launcher's own block existence needs no separate check: it is named in the
    # core list the loop above already validates, so an absent block refuses there with
    # the block name in the message.
    operation_names = _require_str_tuple(body, "operations", "launcher")
    for operation in operation_names:
        if operation not in operations:
            raise CapsuleManifestError(
                status=MANIFEST_VOCABULARY_MISMATCH,
                detail=f"launcher names unknown operation {operation!r}",
            )
    return CapsuleLauncherEntry(
        is_role=False,
        routing_condition=_require_str(body, "routing_condition", "launcher"),
        instruction_source=_require_str(body, "instruction_source", "launcher"),
        core=core_names,
        operations=cast(tuple[CapsuleOperation, ...], operation_names),
    )


def _parse_specializations(
    document: Mapping[str, object],
) -> Mapping[str, CapsuleSpecializationEntry]:
    """Parse the declared repository specializations.

    A specialization is composed only when the admitted binding selects it, but it
    is *declared* here, beside every other source, so a path admitted as a
    specialization is a path the manifest knows rather than one it has never seen.
    """

    raw = document.get("specializations")
    if raw is None:
        return {}
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        raise CapsuleManifestError(
            status=MANIFEST_INVALID, detail="specializations must be a JSON array"
        )
    entries: dict[str, CapsuleSpecializationEntry] = {}
    for item in raw:
        body = _require_mapping(item, "specialization")
        name = _require_str(body, "name", "specialization")
        if name in entries:
            raise CapsuleManifestError(
                status=MANIFEST_VOCABULARY_MISMATCH,
                detail=f"specialization {name!r} is declared twice",
                next_action="declare each specialization exactly once",
            )
        entries[name] = CapsuleSpecializationEntry(
            name=name,
            source=_require_str(body, "source", f"specialization.{name}"),
            purpose=_require_str(body, "purpose", f"specialization.{name}"),
        )
    return entries


def _parse_skills(document: Mapping[str, object]) -> Mapping[str, CapsuleSkillEntry]:
    """Parse the declared skill references.

    A skill is declared once at the top level and *referenced by name* from a role, so
    the origin/URI pair and the file whose bytes define its revision have exactly one
    authority — the same single-source rule the rest of the corpus follows.
    """

    raw = document.get("skills")
    if raw is None:
        return {}
    body = _require_mapping(raw, "skills")
    entries: dict[str, CapsuleSkillEntry] = {}
    for name, block in body.items():
        declared = _require_mapping(block, f"skills.{name}")
        entries[name] = CapsuleSkillEntry(
            name=name,
            origin=_require_str(declared, "origin", f"skills.{name}"),
            uri=_require_str(declared, "uri", f"skills.{name}"),
            source=_require_str(declared, "source", f"skills.{name}"),
        )
    return entries


def _require_role_skills_are_declared(
    roles: Mapping[str, CapsuleRoleEntry], skills: Mapping[str, CapsuleSkillEntry]
) -> None:
    """Refuse a role that references a skill the manifest never declared.

    A reference with no declaration has no origin and no revision to carry, and the
    alternative — returning a nameless reference or silently dropping it — is exactly
    the silent omission this compiler refuses everywhere else.
    """

    undeclared = sorted(
        f"{role_name} -> {skill_name}"
        for role_name, role in roles.items()
        for skill_name in role.skills
        if skill_name not in skills
    )
    if undeclared:
        raise CapsuleManifestError(
            status=MANIFEST_VOCABULARY_MISMATCH,
            detail=(
                "roles reference skills the manifest does not declare, so no origin or revision "
                "exists to carry: " + ", ".join(undeclared)
            ),
            next_action=(
                "declare each skill under the manifest's top-level 'skills' map with its origin, "
                "uri and source, or remove the reference from the role"
            ),
        )


def _quoted(names: tuple[str, ...]) -> str:
    return ", ".join(repr(name) for name in names) if names else "<none>"


def _require_one_path_serves_one_identity(
    core: Mapping[str, CapsuleCoreEntry],
    operations: Mapping[str, CapsuleOperationEntry],
    roles: Mapping[str, CapsuleRoleEntry],
    specializations: Mapping[str, CapsuleSpecializationEntry],
    skills: Mapping[str, CapsuleSkillEntry],
) -> None:
    """Refuse a manifest that routes two different blocks at one source file.

    Two identities cannot be carried by one file without the compiler choosing which
    name to believe, and "the compiler chose the first one it saw" is not a rule
    anybody wrote down. It is a metadata defect, so it is refused where the metadata
    is read rather than surfacing later as a missing block or a duplicate.
    """

    claims: dict[str, list[str]] = {}
    for name, entry in core.items():
        claims.setdefault(entry.source, []).append(f"core:{name}")
    for name, entry in operations.items():
        claims.setdefault(entry.source, []).append(f"operation:{name}")
    for name, entry in roles.items():
        claims.setdefault(entry.file, []).append(f"role:{name}")
    for name, entry in specializations.items():
        claims.setdefault(entry.source, []).append(f"specialization:{name}")
    for name, entry in skills.items():
        claims.setdefault(entry.source, []).append(f"skill:{name}")
    contested = {path: names for path, names in claims.items() if len(names) > 1}
    if contested:
        listing = "; ".join(
            f"{path} claims {sorted(names)}" for path, names in sorted(contested.items())
        )
        raise CapsuleManifestError(
            status=MANIFEST_VOCABULARY_MISMATCH,
            detail=f"one source file is routed to more than one instruction identity: {listing}",
            next_action=(
                "give each instruction identity its own source; the corpus's single-source rule "
                "means one file carries one block"
            ),
        )


def _require_operation_applicability_agrees(
    roles: Mapping[str, CapsuleRoleEntry],
    operations: Mapping[str, CapsuleOperationEntry],
) -> None:
    """Refuse a manifest whose two applicability statements contradict each other.

    ``roles.<role>.operations`` and ``operations.<operation>.applies_to_roles``
    are two spellings of one predicate. A compiler forced to reconcile them would
    be picking a winner by position, so the disagreement is refused at parse time
    instead — the kind of defect that otherwise surfaces as an unexplainable
    missing block.
    """

    disagreements: list[str] = []
    for role_name, role in roles.items():
        for operation_name, operation in operations.items():
            in_role = operation_name in role.operations
            in_operation = role_name in operation.applies_to_roles
            if in_role != in_operation:
                disagreements.append(
                    f"{role_name}/{operation_name}: role-list={in_role} "
                    f"operation-list={in_operation}"
                )
    if disagreements:
        raise CapsuleManifestError(
            status=MANIFEST_INCONSISTENT_APPLICABILITY,
            detail=(
                "composition manifest applicability contradicts itself in "
                f"{len(disagreements)} place(s): " + "; ".join(sorted(disagreements))
            ),
            next_action="make roles.<role>.operations and operations.<op>.applies_to_roles agree",
        )


__all__ = [
    "COMPOSITION_MANIFEST_SCHEMA",
    "MANIFEST_INCONSISTENT_APPLICABILITY",
    "MANIFEST_INVALID",
    "MANIFEST_VOCABULARY_MISMATCH",
    "CapsuleCompositionManifest",
    "CapsuleCoreEntry",
    "CapsuleLauncherEntry",
    "CapsuleOperationEntry",
    "CapsuleRoleEntry",
    "CapsuleSkillEntry",
    "CapsuleSpecializationEntry",
    "parse_composition_manifest",
]
