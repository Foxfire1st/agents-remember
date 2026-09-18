"""The declared traversal policy: one immutable identity and an append-only version set.

A composition edge may carry a declared traversal policy, and the policy is a *record* rather than
two text columns on the edge for one reason: the version an edge was authored under has to be
answerable. ``Doc13:227`` makes completion reportable only relative to the traversal policy that was
executed, so a policy that cannot say which version ran is not a reportable traversal -- and
identity-without-version is not a representable state here, because an edge cites the pair.

Three rules shape every function below:

* **A policy version is immutable.** A declared version is sealed against rewrite by the schema's
  own trigger; a corrected policy is a new version with its own identity, and the version spelling
  is unique per identity so one identity cannot declare one spelling twice.
* **Nothing is resolved to a default.** An unknown identity and an unknown version of a known
  identity are refused with the same shipped code and different facts, because the remedy differs:
  declare the policy, or declare the version. `"latest"`, `"default"` and "the only version stored"
  are not resolutions this module performs.
* **A malformed policy is refused as malformed.** A policy that names no finite depth bound, or that
  widens a scope this build does not register, is refused by the value's own construction and by the
  table's own ``CHECK`` -- never stored and later interpreted.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from agents_remember.kernel.canonical_json import sha256_digest
from agents_remember.memory.knowledge.connection import fetch_one
from agents_remember.memory.knowledge.records import decode_authorship, encode_authorship
from agents_remember.memory.knowledge.refusals import (
    KnowledgeRefused,
    KnowledgeStorageError,
    RefusalFacts,
    composition_policy_refusal,
    refusal,
)
from agents_remember.models.knowledge.composition import (
    REGISTERED_REVIEW_SCOPE,
    FamilyCompositionPolicyVersion,
    FollowDirection,
    WidenedScope,
)
from agents_remember.models.knowledge.result import KnowledgeOperation

if TYPE_CHECKING:
    from agents_remember.memory.knowledge.store import OpenedKnowledgeStore

# The operation name every refusal raised by an authored act here carries when it is reached through
# the in-transaction step. A candidate batch restates it under ``change_candidate`` and names the
# command and its position, which is what its caller submitted.
_AUTHORING_OPERATION: KnowledgeOperation = "change_candidate"

# The policy identity this leaf declares. It is a *name*, not a registry entry: a caller authors a
# policy under this identity and only a declared, stored version of it makes an edge traversable.
# Nothing here resolves another spelling to a default, and a request naming an unregistered policy
# identity is refused as an unknown policy rather than served.
REGISTERED_COMPOSITION_POLICY_ID = "registered-review-scope/composition"

_POLICY_VERSION_COLUMNS = (
    "repository_id, policy_id, policy_version_id, declared_version, direction, depth_bound, "
    "widened_scope, provenance"
)

_POLICY_INSERT = (
    "INSERT INTO family_composition_policy (repository_id, policy_id, provenance) VALUES (?, ?, ?)"
)

_POLICY_VERSION_INSERT = (
    "INSERT INTO family_composition_policy_version "
    "(repository_id, policy_id, policy_version_id, declared_version, direction, depth_bound, "
    "widened_scope, provenance) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)

# The declared direction vocabulary as a membership set, so "is this a declared direction" is one
# test rather than a chain of comparisons a fourth member could silently escape.
FOLLOW_DIRECTIONS_SET = frozenset({"forward", "reverse", "both"})


# ---------------------------------------------------------------------------
def policy_identity(version: FamilyCompositionPolicyVersion) -> tuple[str, str, str]:
    """Return ``(policy_id, policy_version_id, declared_version)`` for one stored version."""

    return (version.policy_id, version.policy_version_id, version.declared_version)


def insert_policy_version(
    store: OpenedKnowledgeStore, version: FamilyCompositionPolicyVersion
) -> None:
    """Insert one declared policy version, and its identity row when it is the first.

    The identity row and the version row are separate because the identity is what a caller names
    and the version is what an edge cites. Re-declaring a version is refused by the table's own
    keys: one identity declares one spelling of a version exactly once, and a version row is sealed
    against rewrite, so a corrected policy is a new version rather than an edited one.
    """

    if version.repository_id != store.repository_id:
        raise KnowledgeRefused(
            refusal(
                "unauthorized_scope",
                _AUTHORING_OPERATION,
                "the declared policy version names a repository namespace this store is not bound to",
                facts=RefusalFacts(
                    table="family_composition_policy_version",
                    record_id=version.policy_version_id,
                    expected=store.repository_id,
                    observed=version.repository_id,
                ),
                next_action="Declare the policy in the namespace the store is bound to.",
            )
        )
    if (
        fetch_one(
            store.connection,
            "SELECT 1 FROM family_composition_policy WHERE repository_id = ? AND policy_id = ?",
            (store.repository_id, version.policy_id),
        )
        is None
    ):
        store.write(
            _POLICY_INSERT,
            (store.repository_id, version.policy_id, encode_authorship(version.provenance)),
        )
    existing = get_policy_version(store, version.policy_id, version.policy_version_id)
    if existing is not None:
        if policy_identity(existing) == policy_identity(version):
            return
        raise KnowledgeRefused(
            refusal(
                "duplicate_identity",
                _AUTHORING_OPERATION,
                "the policy version identity is already declared with different content, and a "
                "declared policy version is immutable",
                facts=RefusalFacts(
                    table="family_composition_policy_version",
                    record_id=version.policy_version_id,
                    expected=existing.declared_version,
                    observed=version.declared_version,
                ),
                next_action=(
                    "Declare a new policy version with its own identity; a declared version is "
                    "never rewritten in place."
                ),
            )
        )
    store.write(
        _POLICY_VERSION_INSERT,
        (
            store.repository_id,
            version.policy_id,
            version.policy_version_id,
            version.declared_version,
            version.direction,
            version.depth_bound,
            version.widened_scope,
            encode_authorship(version.provenance),
        ),
    )


def get_policy_version(
    store: OpenedKnowledgeStore, policy_id: str, policy_version_id: str
) -> FamilyCompositionPolicyVersion | None:
    """Return one declared policy version, or ``None`` when it is not declared in this namespace."""

    row = fetch_one(
        store.connection,
        f"SELECT {_POLICY_VERSION_COLUMNS} FROM family_composition_policy_version "
        "WHERE repository_id = ? AND policy_id = ? AND policy_version_id = ?",
        (store.repository_id, policy_id, policy_version_id),
    )
    if row is None:
        return None
    return FamilyCompositionPolicyVersion(
        repository_id=str(row[0]),
        policy_id=str(row[1]),
        policy_version_id=str(row[2]),
        declared_version=str(row[3]),
        direction=_direction_of(str(row[4])),
        depth_bound=int(str(row[5])),
        widened_scope=_scope_of(str(row[6])),
        provenance=decode_authorship(str(row[7])),
    )


def list_policy_versions(
    store: OpenedKnowledgeStore, policy_id: str
) -> tuple[FamilyCompositionPolicyVersion, ...]:
    """Return every declared version of one policy, in declared order."""

    rows = store.connection.execute(
        f"SELECT {_POLICY_VERSION_COLUMNS} FROM family_composition_policy_version "
        "WHERE repository_id = ? AND policy_id = ? ORDER BY declared_version, policy_version_id",
        (store.repository_id, policy_id),
    )
    return tuple(policy_version_of(row) for row in rows)


def require_declared_policy(
    store: OpenedKnowledgeStore, policy_id: str, policy_version_id: str
) -> FamilyCompositionPolicyVersion:
    """Return the declared version an edge cites, or refuse by name.

    An unknown policy identity and an unknown version of a known identity are refused with the same
    code and different facts, because the remedy differs: declare the policy, or declare the
    version. Neither is ever resolved to a default (`"latest"`, `"default"` or the only version
    stored), because a policy version that is not the one the caller named is not the policy the
    caller asked for (requirement 3.5).
    """

    version = get_policy_version(store, policy_id, policy_version_id)
    if version is not None:
        return version
    known = fetch_one(
        store.connection,
        "SELECT 1 FROM family_composition_policy WHERE repository_id = ? AND policy_id = ?",
        (store.repository_id, policy_id),
    )
    if known is None:
        raise KnowledgeRefused(
            composition_policy_refusal(
                _AUTHORING_OPERATION,
                f"the policy identity {policy_id!r} is not declared in this repository namespace",
                record_id=policy_version_id,
                expected="a declared policy identity",
                observed=policy_id,
            )
        )
    raise KnowledgeRefused(
        composition_policy_refusal(
            _AUTHORING_OPERATION,
            f"policy {policy_id!r} declares no version {policy_version_id!r}",
            record_id=policy_version_id,
            expected=" | ".join(
                version.policy_version_id for version in list_policy_versions(store, policy_id)
            )
            or "a declared policy version",
            observed=policy_version_id,
        )
    )


def policy_identity_row_digest(repository_id: str, policy_id: str) -> str:
    """Digest one declared policy identity row, so an expectation can name it."""

    return sha256_digest(
        {
            "table": "family_composition_policy",
            "repository_id": repository_id,
            "policy_id": policy_id,
        }
    )


def policy_version_row_digest(repository_id: str, version: FamilyCompositionPolicyVersion) -> str:
    """Digest one declared policy version row, so an expectation can name it."""

    return sha256_digest(
        {
            "table": "family_composition_policy_version",
            "repository_id": repository_id,
            "policy_id": version.policy_id,
            "policy_version_id": version.policy_version_id,
            "declared_version": version.declared_version,
            "direction": version.direction,
            "depth_bound": version.depth_bound,
            "widened_scope": version.widened_scope,
            "provenance": version.provenance.model_dump(mode="json"),
        }
    )


def policy_version_of(row: Sequence[object]) -> FamilyCompositionPolicyVersion:
    """Decode one stored policy-version row, in the declared column order."""

    return FamilyCompositionPolicyVersion(
        repository_id=str(row[0]),
        policy_id=str(row[1]),
        policy_version_id=str(row[2]),
        declared_version=str(row[3]),
        direction=_direction_of(str(row[4])),
        depth_bound=int(str(row[5])),
        widened_scope=_scope_of(str(row[6])),
        provenance=decode_authorship(str(row[7])),
    )


def _direction_of(value: str) -> FollowDirection:
    if value in FOLLOW_DIRECTIONS_SET:
        return value  # type: ignore[return-value]
    raise KnowledgeStorageError(f"stored policy direction {value!r} is not a declared direction")


def _scope_of(value: str) -> WidenedScope:
    """Return the one widened scope this build registers, refusing any other stored spelling.

    A stored ``widened_scope`` that is not a registered scope is a defect rather than a refusal a
    caller could provoke: the plan of record says a policy may widen a *named* scope, and the only
    name it may carry is the one this build registers. Nothing here approximates the unknown value.
    """

    if value == REGISTERED_REVIEW_SCOPE:
        return REGISTERED_REVIEW_SCOPE
    raise KnowledgeStorageError(f"stored widened scope {value!r} is not a registered scope")


__all__ = [
    "FOLLOW_DIRECTIONS_SET",
    "REGISTERED_COMPOSITION_POLICY_ID",
    "get_policy_version",
    "insert_policy_version",
    "list_policy_versions",
    "policy_identity",
    "policy_identity_row_digest",
    "policy_version_of",
    "policy_version_row_digest",
    "require_declared_policy",
]
