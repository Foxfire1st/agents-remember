"""The immutable local receipt that binds one candidate database to its admission.

A candidate database carries knowledge; the receipt carries *which* candidate it is -- namespace,
lane, exact code and memory inputs, schema generation and candidate reference. The two live
beside each other and neither is recoverable from the other, which is why the receipt is written
and read as one object rather than inferred from a path.

The receipt is content-addressed and validated on read. A receipt that was edited in place, or
one written by a different admission, is detected here and answered with a typed refusal -- the
working database is never re-initialized, repaired or partially trusted to make a later step
succeed.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from agents_remember.kernel.atomic_write import atomic_write_bytes
from agents_remember.kernel.canonical_json import canonical_json_bytes, decoded_json
from agents_remember.memory.knowledge.refusals import (
    KnowledgeStorageError,
    candidate_binding_changed_refusal,
)
from agents_remember.models.knowledge.context import KnowledgeSchemaIdentity
from agents_remember.models.knowledge.repository import RepositoryIdentity
from agents_remember.models.knowledge.result import KnowledgeRefusal
from agents_remember.models.knowledge.snapshot import (
    AdmittedCandidateDestination,
    CandidateReceipt,
    build_candidate_receipt,
)

# The admission-derived fields compared when an existing candidate is reopened. Each is a fact
# the admission established, so a difference between the receipt and the admission means this is
# not the candidate that was admitted -- not that the receipt should be rewritten.
_BINDING_FIELDS = (
    "repository_id",
    "lane",
    "code",
    "memory",
    "snapshot_ref",
    "candidate_ref",
    "task_ref",
)


def write_candidate_receipt(path: Path, receipt: CandidateReceipt) -> None:
    """Write one receipt as canonical bytes, atomically and durably.

    The bytes are the canonical JSON encoding rather than a pretty-printed dump, so the same
    receipt always has the same file content and a digest over the file is a digest over the
    binding itself.
    """

    payload = canonical_json_bytes(receipt.model_dump(mode="json"))
    atomic_write_bytes(path, payload)


def read_candidate_receipt(path: Path) -> CandidateReceipt:
    """Read and validate the receipt beside a working database.

    Every failure here is a storage error rather than a returned refusal: a receipt that cannot
    be read or does not seal itself is not a candidate this package will operate on, and the
    caller turns it into the ``selected_input_unavailable`` refusal that names the exact path.
    """

    try:
        raw = path.read_bytes()
    except OSError as error:
        raise KnowledgeStorageError(
            f"the candidate receipt {path} could not be read: {error}"
        ) from error
    try:
        decoded = decoded_json(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise KnowledgeStorageError(
            f"the candidate receipt {path} is not unambiguous UTF-8 JSON: {error}"
        ) from error
    try:
        return CandidateReceipt.model_validate(decoded)
    except ValidationError as error:
        raise KnowledgeStorageError(
            f"the candidate receipt {path} is not a valid sealed receipt: {error}"
        ) from error


def build_receipt_for_candidate(
    destination: AdmittedCandidateDestination, schema: KnowledgeSchemaIdentity
) -> CandidateReceipt:
    """Build this candidate's receipt from its admitted resolution and its stored schema."""

    return build_candidate_receipt(
        resolution=destination.resolution,
        repository_id=destination.repository.repository_id,
        schema_version=schema.schema_name,
        schema_fingerprint=schema.fingerprint,
    )


def receipt_binding_refusal(
    receipt: CandidateReceipt,
    destination: AdmittedCandidateDestination,
    *,
    bound_repository: RepositoryIdentity,
    schema: KnowledgeSchemaIdentity,
) -> KnowledgeRefusal | None:
    """Return the refusal for a receipt that is not this admission's, or ``None``.

    Three comparisons happen here and they answer different questions:

    * against the *database*: is the stored namespace the destination's? A database bound
      elsewhere is not a stale receipt -- it is a different knowledge namespace, and answering
      ``candidate_binding_changed`` is what keeps a rebind from happening by accident;
    * against the *admission*: were these the lane, the exact code and memory inputs and the
      candidate reference this candidate was admitted with?
    * against the *schema generation*: was this candidate created under the generation this code
      declares? The check is the recorded fingerprint, because a database can pass the current
      table manifest while having been written by a different generation's rules.

    The expected binding is derived by the one constructor that builds receipts, so the
    comparison cannot drift from the value a new candidate would be written with.
    """

    if bound_repository.repository_id != destination.repository.repository_id:
        return candidate_binding_changed_refusal(
            "open_candidate",
            "the working database is bound to a different repository namespace than the admission",
            expected=destination.repository.repository_id,
            observed=bound_repository.repository_id,
        )
    if (
        receipt.schema_version != schema.schema_name
        or receipt.schema_fingerprint != schema.fingerprint
    ):
        return candidate_binding_changed_refusal(
            "open_candidate",
            "the candidate receipt records a schema generation this code does not declare",
            expected=f"{schema.schema_name}/{schema.fingerprint}",
            observed=f"{receipt.schema_version}/{receipt.schema_fingerprint}",
        )
    expected_receipt = build_candidate_receipt(
        resolution=destination.resolution,
        repository_id=destination.repository.repository_id,
        schema_version=receipt.schema_version,
        schema_fingerprint=receipt.schema_fingerprint,
    )
    observed = {field: getattr(receipt, field) for field in _BINDING_FIELDS}
    expected = {field: getattr(expected_receipt, field) for field in _BINDING_FIELDS}
    differing = [field for field in _BINDING_FIELDS if observed[field] != expected[field]]
    if not differing:
        return None
    return candidate_binding_changed_refusal(
        "open_candidate",
        "the stored candidate receipt binds this working database to an admission that is not "
        f"the one presented; differing field(s): {', '.join(differing)}",
        expected=_render(expected, differing),
        observed=_render(observed, differing),
    )


def _render(binding: dict[str, object], fields: list[str]) -> str:
    """Render the compared values of the differing fields for a refusal's facts."""

    return "; ".join(f"{field}={binding[field]!r}" for field in fields)
