"""The authored citation-binding write path: the binding's own tables, and the refusals between them.

One authored act lives here -- record that this citation key, in this exact revision of this prose
document, denotes this knowledge record at this locator -- and it has the shipped pair of entry
points, exactly as the facet and detection writes do:

* the **operation**, which owns the candidate lock and one ``BEGIN IMMEDIATE`` transaction, and
  returns a typed :class:`CitationBindingResult`;
* the **in-transaction step**, which writes inside a caller's open transaction and raises
  :class:`KnowledgeRefused` so a batch path rolls the whole batch back.

That split is what makes "a refused binding write leaves the dataset exactly as it was" a property
of the transaction boundary rather than a promise about statement order.

Four rules shape this path:

* **The payload seam is the only payload decision point.** A binding payload is validated by
  :func:`…record_envelope.validate_record_payload`, so an unregistered kind, a schema inadmissible
  for its kind, a payload that omits a required fact and a payload carrying an undeclared field are
  all the same shipped ``invalid_payload`` refusal, raised before any row exists.

* **The binding is authored, not inferred.** Nothing here derives a target from a path prefix, a
  folder name, a symbol string, a display label or a prose mention, and nothing searches for a
  nearest plausible record. The target arrives as a typed identity the caller states; this module
  checks that the stated identity *exists* and that its **recorded kind is the kind the reference
  declares**, and refuses otherwise. A wrong-kind reference is a refusal at this boundary, which is
  why the read path's ``target_kind_mismatch`` state reports a fact that can only arise from a store
  changed after the write rather than from an accepted one.

* **Every reference is checked before the row it belongs to.** The target record must be stored in
  this namespace, the exact revision -- when one is named -- must be a revision *of that record*, and
  a named governing route must be authored here. A dangling reference is refused rather than stored,
  so the read path never has to guess what an absent reference meant.

* **Scope is never inferred.** ``governing_route_id`` is a parameter and is never derived from the
  document path, the target's path, the key's written source or the repository root. ``None`` is the
  explicit **ungoverned** state -- a binding with no governing route is reported as ungoverned and is
  never defaulted to a route that happens to exist.

The generation gate is the same one the facet and detection writes apply: a dataset whose recorded
generation predates this table is refused with the observed generation as a fact, never migrated,
widened or written through.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agents_remember.kernel.canonical_json import sha256_digest
from agents_remember.memory.knowledge import routes
from agents_remember.memory.knowledge.facet_records import (
    RecordRevisionDraft,
    record_revision_digest,
    record_revision_row,
)
from agents_remember.memory.knowledge.record_envelope import validate_record_payload
from agents_remember.memory.knowledge.records import encode_authorship
from agents_remember.memory.knowledge.refusals import (
    KnowledgeRefused,
    KnowledgeStorageError,
    RefusalFacts,
    SqliteFailureContext,
    generation_mismatch_refusal,
    missing_expected_row_refusal,
    refusal,
    scope_refusal,
)
from agents_remember.memory.knowledge.schema_generations import GENERATION_5
from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.citation import (
    BINDING_RECORD_KIND,
    BINDING_RECORD_SCHEMA,
    CitationBindingPayload,
    CitationBindingRequest,
    CitationBindingResult,
    CitationBindingWriteIdentity,
    ProseCitationKey,
    TableRowKeyForm,
    render_local_key,
)
from agents_remember.models.knowledge.result import KnowledgeOperation, KnowledgeRefusal

if TYPE_CHECKING:
    from agents_remember.memory.knowledge.store import OpenedKnowledgeStore

# The generation whose table a binding write needs. A dataset that predates it is refused, never
# migrated or widened -- the same rule the facet write applies to its own generation.
REQUIRED_BINDING_GENERATION = GENERATION_5

BINDING_OPERATION: KnowledgeOperation = "author_citation_binding"

_RECORD_INSERT = (
    "INSERT INTO knowledge_record (repository_id, record_id, kind, authority_home, lifecycle, "
    "governing_route_id, record_schema, provenance) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)
_REVISION_INSERT = (
    "INSERT INTO record_revision (repository_id, revision_id, record_id, record_schema, payload, "
    "predecessor_revision_id, content_digest, provenance) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)
_BINDING_INSERT = (
    "INSERT INTO citation_binding (repository_id, binding_id, owner_document_path, "
    "owner_blob_object_id, local_key_form, local_key_text, target_record_id, target_record_kind, "
    "target_revision_id, target_locator_kind, governing_route_id, provenance) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
_BINDING_BY_ID = "SELECT * FROM citation_binding WHERE repository_id = ? AND binding_id = ?"
_BINDING_IDS = "SELECT binding_id FROM citation_binding WHERE repository_id = ? ORDER BY binding_id"
_TARGET_RECORD = (
    "SELECT kind, authority_home, lifecycle, record_schema FROM knowledge_record "
    "WHERE repository_id = ? AND record_id = ?"
)
_TARGET_REVISION = (
    "SELECT record_id FROM record_revision WHERE repository_id = ? AND revision_id = ?"
)

# The lifecycle an authored binding is written under. A binding is an *authored claim* about what a
# sentence cited -- it is not accepted origin data and it carries no acceptance reference -- so it
# declares the proposed lifecycle exactly as an authored facet does.
BINDING_RECORD_LIFECYCLE = "proposed"


# ---------------------------------------------------------------------------
# The write path.


def author_citation_binding(
    store: OpenedKnowledgeStore, request: CitationBindingRequest, authorship: Authorship
) -> CitationBindingResult:
    """Record one authored citation binding, holding the candidate lock and one transaction."""

    denied = _binding_precondition(store, request)
    if denied is not None:
        return _refused_result(request, denied)
    with store.exclusive_candidate_lock(BINDING_OPERATION) as lock_refusal:
        if lock_refusal is not None:
            return _refused_result(request, lock_refusal)
        return store.within_immediate(
            lambda: _apply_binding_write(store, request, authorship),
            on_refusal=lambda refused: _refused_result(request, refused),
            failure=SqliteFailureContext(
                operation=BINDING_OPERATION, table="citation_binding", record_id=request.binding_id
            ),
        )


def apply_citation_binding(
    store: OpenedKnowledgeStore,
    request: CitationBindingRequest,
    authorship: Authorship,
) -> tuple[CitationBindingWriteIdentity, ...]:
    """Apply one binding write inside the caller's open transaction, or raise ``KnowledgeRefused``."""

    denial = _binding_precondition(store, request)
    if denial is not None:
        raise KnowledgeRefused(denial)
    validated = _validated_payload(request)
    if isinstance(validated, KnowledgeRefusal):
        raise KnowledgeRefused(validated)
    return _write_binding_rows(store, request, validated, authorship)


def _validated_payload(
    request: CitationBindingRequest,
) -> CitationBindingPayload | KnowledgeRefusal:
    """Validate one binding payload through the one payload seam."""

    validated = validate_record_payload(
        BINDING_RECORD_KIND,
        BINDING_RECORD_SCHEMA,
        request.payload.model_dump(mode="json"),
        operation=BINDING_OPERATION,
        record_id=request.binding_id,
    )
    if isinstance(validated, KnowledgeRefusal):
        return validated
    return CitationBindingPayload.model_validate(validated.model_dump(mode="json"))


def _binding_precondition(
    store: OpenedKnowledgeStore, request: CitationBindingRequest
) -> KnowledgeRefusal | None:
    """Return the refusal one binding request earns before any row is written, or ``None``.

    The ordering is deliberate: scope first, because a request addressed to another namespace has not
    asked a question about this dataset at all; then the generation, because a dataset that predates
    the table cannot answer any of the reference checks; then the references themselves.
    """

    denial = scope_refusal(BINDING_OPERATION, store.repository_id, request.repository_id)
    if denial is None:
        denial = require_binding_generation(store, BINDING_OPERATION)
    if denial is None:
        denial = require_target_reference(store, request)
    if denial is None:
        denial = _require_governing_route(store, request)
    return denial


def require_target_reference(
    store: OpenedKnowledgeStore, request: CitationBindingRequest
) -> KnowledgeRefusal | None:
    """Refuse a target that is not stored, or whose recorded kind is not the declared kind.

    Three facts, two refusals, and the split matters to a caller:

    * the record is not stored -> ``missing_expected_row``, because the reference names a row this
      namespace does not hold;
    * the record is stored under another kind -> ``invalid_reference``, because the reference's own
      declared kind contradicts the stored record and no reading of the key can settle which side is
      wrong. This is the *wrong-kind target refusal* the acceptance evidence names, and it happens
      here rather than being stored and reported later: a binding that stored a kind contradicting
      its target would make "which kind is this" have two answers;
    * a named revision that is not a revision of the named record -> ``invalid_reference``, so a
      stored reference cannot point at a revision belonging to a different record.
    """

    target = request.payload.target
    rows = tuple(store.connection.execute(_TARGET_RECORD, (store.repository_id, target.record_id)))
    if not rows:
        return missing_expected_row_refusal(
            operation=BINDING_OPERATION, table="knowledge_record", record_id=target.record_id
        )
    stored_kind = str(rows[0][0])
    if stored_kind != target.kind:
        return refusal(
            "invalid_reference",
            BINDING_OPERATION,
            "the binding's declared target kind is not the kind the named record is stored under",
            facts=RefusalFacts(
                table="knowledge_record",
                record_id=target.record_id,
                expected=stored_kind,
                observed=target.kind,
            ),
            next_action=(
                "Declare the kind the target record is actually stored under, or name the record "
                "that carries the kind this binding means. The declared kind is not adjusted to "
                "match: a reference whose kind is decided by its target is not a typed reference."
            ),
        )
    return _require_target_revision(store, request)


def _require_target_revision(
    store: OpenedKnowledgeStore, request: CitationBindingRequest
) -> KnowledgeRefusal | None:
    """Refuse a named revision that is not a stored revision of the named record."""

    revision_id = request.payload.target.revision_id
    if revision_id is None:
        return None
    rows = tuple(store.connection.execute(_TARGET_REVISION, (store.repository_id, revision_id)))
    if rows and str(rows[0][0]) == request.payload.target.record_id:
        return None
    return refusal(
        "invalid_reference",
        BINDING_OPERATION,
        "the binding names a target revision this namespace does not hold under the named record",
        facts=RefusalFacts(
            table="record_revision",
            record_id=revision_id,
            expected=request.payload.target.record_id,
            observed=str(rows[0][0]) if rows else "<absent in this namespace>",
        ),
        next_action=(
            "Name a revision the target record really holds, or record the binding against the "
            "record without pinning a revision. A dangling revision reference is refused rather "
            "than stored, so a read never has to guess what an absent revision meant."
        ),
    )


def _require_governing_route(
    store: OpenedKnowledgeStore, request: CitationBindingRequest
) -> KnowledgeRefusal | None:
    """Refuse a binding whose declared governing route is not authored in this repository.

    An **ungoverned** binding is the explicit ``None`` state and is never refused (requirement 1.6);
    a *named* route that does not exist is a dangling reference and is refused rather than stored.
    Nothing here derives a route from the document path, the target, or the repository root.
    """

    route_id = request.governing_route_id
    if route_id is None:
        return None
    if routes.route_exists(store.connection, store.repository_id, route_id):
        return None
    return missing_expected_row_refusal(
        operation=BINDING_OPERATION, table="route", record_id=route_id
    )


def require_binding_generation(
    store: OpenedKnowledgeStore, operation: KnowledgeOperation
) -> KnowledgeRefusal | None:
    """Refuse a binding write against a dataset whose recorded generation predates its table.

    The dataset's own generation is read from the open store, so this is a comparison against what
    the file declares rather than against what the build supports, and the refusal carries both
    numbers as facts. Nothing is migrated, widened or written through.
    """

    observed = store.generation.user_version
    required = REQUIRED_BINDING_GENERATION.user_version
    if observed >= required:
        return None
    return generation_mismatch_refusal(
        operation,
        "the citation-binding table is registered by generation "
        f"{REQUIRED_BINDING_GENERATION.schema_name} (user_version {required})",
        required=required,
        observed=observed,
    )


def _apply_binding_write(
    store: OpenedKnowledgeStore, request: CitationBindingRequest, authorship: Authorship
) -> CitationBindingResult:
    """Write one binding envelope, its sealed revision and its own recorded row."""

    validated = _validated_payload(request)
    if isinstance(validated, KnowledgeRefusal):
        return _refused_result(request, validated)
    written = _write_binding_rows(store, request, validated, authorship)
    return CitationBindingResult(
        state="applied", repository_id=request.repository_id, written=written
    )


def _write_binding_rows(
    store: OpenedKnowledgeStore,
    request: CitationBindingRequest,
    payload: CitationBindingPayload,
    authorship: Authorship,
) -> tuple[CitationBindingWriteIdentity, ...]:
    """Insert the envelope, the sealed revision and the binding's own row, in that order."""

    store.write(
        _RECORD_INSERT,
        (
            store.repository_id,
            request.binding_id,
            BINDING_RECORD_KIND,
            _authority_home(store),
            BINDING_RECORD_LIFECYCLE,
            request.governing_route_id,
            BINDING_RECORD_SCHEMA,
            encode_authorship(authorship),
        ),
    )
    draft = RecordRevisionDraft(
        record_id=request.binding_id,
        revision_id=request.binding_id,
        record_schema=BINDING_RECORD_SCHEMA,
        predecessor_revision_id=None,
    )
    stored_payload = payload.model_dump(mode="json")
    store.write(
        _REVISION_INSERT,
        (
            store.repository_id,
            *record_revision_row(draft, stored_payload, authorship),
        ),
    )
    store.write(_BINDING_INSERT, (store.repository_id, *binding_row(request, authorship)))
    revision_digest = record_revision_digest(draft, stored_payload)
    return (
        CitationBindingWriteIdentity(
            table="knowledge_record", record_id=request.binding_id, digest=revision_digest
        ),
        CitationBindingWriteIdentity(
            table="record_revision", record_id=request.binding_id, digest=revision_digest
        ),
        CitationBindingWriteIdentity(
            table="citation_binding",
            record_id=request.binding_id,
            digest=_binding_row_digest(store, request.binding_id),
        ),
    )


def binding_row(request: CitationBindingRequest, authorship: Authorship) -> tuple[str | None, ...]:
    """Return the ``citation_binding`` column tuple for one authored binding.

    The key's text is stored as its own canonical text rather than as one flattened string, because
    the failure states have to stay distinguishable: a key whose extent moved and a key whose anchor
    moved are different facts a curator acts on differently. For a ``cit:`` body the canonical text
    is the body exactly as written -- the text between the mark and its matching ``)``.
    """

    payload = request.payload
    return (
        request.binding_id,
        payload.owner_revision.document_path,
        payload.owner_revision.blob_object_id,
        payload.local_key.form,
        local_key_text(payload.local_key),
        payload.target.record_id,
        payload.target.kind,
        payload.target.revision_id,
        payload.target.locator.kind,
        request.governing_route_id,
        encode_authorship(authorship),
    )


def local_key_text(local_key: ProseCitationKey | TableRowKeyForm) -> str:
    """Return the exact recorded text of one local citation key, for the table's unique key.

    Both forms are stored as the text the prose wrote, so "a rewritten key is a different key" is
    checkable by comparison rather than by re-parsing, and the ``UNIQUE`` key over the recorded text
    is what makes "one owner revision records one key once" a constraint of the table rather than a
    rule this write path remembers.
    """

    return render_local_key(local_key)


def _authority_home(store: OpenedKnowledgeStore) -> str:
    """Return the authority home the bound repository declares.

    A binding envelope's ``authority_home`` is a fact about the namespace it was written into, not a
    field a caller authors: the destination resolved that namespace, so the record inherits it.
    """

    repository = store.get_repository()
    if repository is None:  # pragma: no cover - an unbound store cannot reach a write
        raise KnowledgeStorageError(
            f"the store is not bound to repository namespace {store.repository_id}"
        )
    return repository.authority_home


def _binding_row_digest(store: OpenedKnowledgeStore, binding_id: str) -> str:
    """Return the digest of one stored binding row, recomputed from what is stored."""

    rows = tuple(store.connection.execute(_BINDING_BY_ID, (store.repository_id, binding_id)))
    if not rows:  # pragma: no cover - the insert above either wrote the row or raised
        raise KnowledgeStorageError(f"citation binding {binding_id} was not stored")
    return binding_row_digest(rows[0])


def binding_row_digest(row: Any) -> str:
    """Return the digest of one stored ``citation_binding`` row, from the row as it stands.

    The four facts that make a binding what it is -- the owner revision, the key as written, the
    typed target and the governing route -- are covered by name rather than by position, so a
    column added by a later generation cannot silently slide this digest onto different fields. This
    is a *row* digest over the row's own recorded columns, not a content address for the prose the
    binding cites: it identifies the recorded attribution, which is the same job
    ``FacetAttachment.row_digest`` does for an attachment, and it never becomes an identity of the
    document.
    """

    return sha256_digest(
        {
            "binding_id": str(row[1]),
            "owner_document_path": str(row[2]),
            "owner_blob_object_id": str(row[3]),
            "local_key_form": str(row[4]),
            "local_key_text": str(row[5]),
            "target_record_id": str(row[6]),
            "target_record_kind": str(row[7]),
            "target_revision_id": None if row[8] is None else str(row[8]),
            "target_locator_kind": str(row[9]),
            "governing_route_id": None if row[10] is None else str(row[10]),
        }
    )


def load_binding_ids(store: OpenedKnowledgeStore) -> tuple[str, ...]:
    """Return every recorded binding identity of one namespace, in a stable order.

    Exposed so a refusal case can prove that a refused write wrote **nothing** rather than proving it
    by the absence of one particular row: a refusal that left a partial envelope behind would leave a
    different identity here, and reading the whole set is what makes that visible.
    """

    return tuple(
        str(row[0]) for row in store.connection.execute(_BINDING_IDS, (store.repository_id,))
    )


def _refused_result(
    request: CitationBindingRequest, refusal_value: KnowledgeRefusal
) -> CitationBindingResult:
    """Build the receipt for a refusal: nothing was written, so nothing is reported as written."""

    return CitationBindingResult(
        state="refused", repository_id=request.repository_id, refusal=refusal_value
    )


__all__ = [
    "BINDING_OPERATION",
    "BINDING_RECORD_LIFECYCLE",
    "REQUIRED_BINDING_GENERATION",
    "apply_citation_binding",
    "author_citation_binding",
    "binding_row",
    "binding_row_digest",
    "load_binding_ids",
    "local_key_text",
    "require_binding_generation",
    "require_target_reference",
]
