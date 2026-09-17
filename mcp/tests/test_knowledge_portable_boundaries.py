"""The portable artifact's boundary population: canonical form, staging, admission, reading.

The round-trip module holds the artifact contract and the export/import population; this module holds
the boundary properties that population cannot reach from the outside. The two are one evidence set:
this module imports the first module's helpers rather than copying them, so there is still exactly one
definition of what an artifact, a refusal or a published row count is. It is a second module because
the repository's 1200-line hard limit is a real limit and one file cannot hold both populations.

Five properties live here, each because the ordinary path cannot fail it: the canonical key order of a
typed JSON value (the reader's acceptance rule and the encoder's canonicalisation are two halves of one
property); the staged sealed-aggregate read, proved against a destination that already holds the good
version of the dataset the artifact describes; the import's close/verify step, proved by a stage opened
in WAL mode; destination admission before any staging work; and the typed read of an artifact that is
not readable UTF-8 text.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import apsw
import pytest
from agents_remember.application.knowledge_export import (
    read_knowledge_artifact,
    validate_knowledge_artifact,
)
from agents_remember.memory.knowledge import export_import, logical
from agents_remember.memory.knowledge.connection import journal_mode, open_read_only_database
from agents_remember.memory.knowledge.export_portable import (
    EXPORT_FORMAT,
    _out_of_canonical_order,
    canonical_document,
    encode_export,
)
from agents_remember.memory.knowledge.schema_generations import (
    CURRENT_GENERATION,
    GENERATION_1,
    generation_for_key,
)
from agents_remember.models.knowledge.context import KNOWLEDGE_SCHEMA_NAME
from agents_remember.models.knowledge.result import KnowledgeRefusal
from generation_test_support import create_generation_1_store
from knowledge_fixture_test_support import (
    BranchingKnowledgeFixture,
    build_branching_knowledge_fixture,
)
from merge_case_test_support import copy_closed, file_digest
from snapshot_lifecycle_test_support import (
    SnapshotCase,
    build_case,
    create,
    journal_peer_names,
    live_store,
    publish,
    write_label_on_live_store,
)
from test_knowledge_portable_roundtrip import (
    _declared_digest,
    _refusal,
    artifact_of,
    envelope_of,
    exported,
    identity_of,
    import_into,
    reseal,
    row_counts_of,
    sqlite_entries,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def fixture(tmp_path: Path) -> BranchingKnowledgeFixture:
    """The shared branching dataset, built by the one builder both portable modules use."""

    return build_branching_knowledge_fixture(tmp_path / "source")


def journal_mode_of(path: Path) -> str:
    """Return one database's own journal mode, read through a connection that cannot change it."""

    connection = open_read_only_database(path)
    try:
        return journal_mode(connection)
    finally:
        connection.close()


# -- the freeze's closure, proved from this module --------------------------------------------


def test_a_frozen_snapshot_of_a_wal_resident_candidate_is_published_closed(tmp_path: Path) -> None:
    """The shared close/verify step is load-bearing for the *freeze* producer, not only the import.

    ``require_closed_database`` has two producers and one definition. The import half is proved by
    the WAL-stage case below; this is the freeze half, in a module that owns the claim rather than
    borrowing the L4 node whose *name* is about content-wholeness while its discriminating assertion
    is about file closure. The claim here is the closure, and the node is named for it.

    The candidate really holds content a main-file-only copy would omit -- the marker is asserted
    absent from the file before publication -- so the case cannot pass by the source never being in
    WAL mode. What is measured is the *published* file, which is what a consumer opens: it must report
    ``delete`` and have no journal or WAL peer beside it. Remove the freeze's call to the shared step
    and the published file keeps the WAL header the backup destination inherited, so this assertion is
    the one that reads the property; the L4 node is corroboration for the same mutation.
    """

    case: SnapshotCase = build_case(tmp_path)
    create(case)
    marker = "A committed batch that is still only in the write-ahead log."
    store = live_store(case)
    try:
        store.connection.execute("PRAGMA journal_mode=WAL")
        write_label_on_live_store(store, case, marker)
        identity = store.snapshot_identity()

        assert journal_mode_of(case.database_path) == "wal"
        assert marker.encode("utf-8") not in case.database_path.read_bytes(), (
            "the case needs committed content a main-file-only copy would omit"
        )

        published = publish(case, expected_candidate=identity)
    finally:
        store.connection.close()

    assert published.state == "published", published.refusal
    assert published.identity == identity
    assert journal_mode_of(case.destination_path()) == "delete"
    assert journal_peer_names(case.destination_path()) == []
    assert identity_of(case.destination_path()) == identity


# -- the canonical form of a typed JSON value ------------------------------------------------


def test_the_canonical_form_of_the_whole_document_is_the_only_form_the_reader_accepts(
    fixture: BranchingKnowledgeFixture, tmp_path: Path
) -> None:
    """The canonical rendering is the format, at every depth and across the whole document.

    The digest is deliberately insensitive to how a JSON value was spelled, so the *document* is
    where that spelling has to be pinned down. Two halves are measured together because either alone
    is unsound: the **encoder** sorts a typed JSON value's nested keys however the stored text was
    spelled (a legal dataset, because the storage contract says the *value* is the knowledge), and
    the **reader** accepts one spelling and refuses every other.

    The refusal is one whole-document gate rather than a set of level-by-level checks, and this case
    drives every axis a difference can arrive on -- a JSON escape that spells the same character (byte
    length differs, parsed value does not), insignificant whitespace, a reordered envelope header, a
    reordered table mapping, a permuted nested key at depth 2, and a permuted object inside a list --
    because the axes are what a foreign producer gets wrong and what a hash comparison across
    artifacts depends on. The depth-2 object and the list-of-objects value are the two shapes the
    recursion has to carry, so the encoder half drives them too.

    Two further axes are driven after the gate's loop, and neither is a *spelling*: the JSON **type**
    of the envelope's one non-string scalar, ``userVersion``, spelled ``1.0`` and ``true`` -- two
    values Python's ``==`` calls equal to the supported generation ``1``, so a plain inequality
    accepts a document that is not the encoder's bytes and does not even re-encode to itself. They are
    refused by the header check by name (``unsupported_schema``) rather than by the gate, because the
    gate compares spelling and keeps the declared type; and the same defect is closed from the other
    end, since the published :func:`encode_export` renders the generation this build validates. The
    case then respells **every** one of the envelope's seven keys with a different JSON type and
    measures that each is refused by the header check while rendering equal to itself -- which is the
    measurement the digest-coverage statement rests on, and it is a measurement rather than an
    assertion of the documentation. It also asserts :func:`canonical_document`'s published contract on
    the artifacts it already holds, because that function is what the guarantee is checked against and
    nothing else would notice if it returned its input or nothing at all.

    The ordering claim this case also proves: because a document's spelling is checked before the
    declared generation and before any table is read, an artifact that is *both* non-canonical and
    unsupported is refused as non-canonical, under this gate's own code and record id. The field-level
    canonical check is unreachable behind the gate and is disclosed as defence in depth (annex §14).
    """

    artifact = exported(fixture).artifact or ""
    body = envelope_of(artifact)
    provenance = body["tables"]["invariant_revision"][0]["provenance"]
    spelled = _respelled_encoding(fixture, provenance, tmp_path / "respelled.sqlite")
    axes = _non_canonical_axes(artifact, provenance)
    respellings = {name: text for name, text in axes.items() if text != artifact}
    doubly = respellings["non_canonical_and_unsupported"]
    declared = _declared_digest(artifact)
    destination = tmp_path / "refused.sqlite"

    # The encoder half: the dataset really carries the permuted spelling, and the artifact is still
    # the one canonical rendering of the value it spells -- at the top level, at depth 2, and inside
    # the list.
    assert json.dumps(provenance)[:1] == "{"
    assert all(not isinstance(value, dict) for value in provenance.values()), (
        "the fixture's own values hold no object, so the depth-2 axis below is injected"
    )
    assert list(spelled) == sorted(provenance)
    assert list(spelled["origin_refs"][0]) == ["requirement", "version"]
    assert json.dumps(spelled) == json.dumps(_canonical_value(provenance))
    assert validate_knowledge_artifact(axes["encoder_direction_marker"]).state == "validated"

    # Seven respellings of one document, each independently refused (the eighth axis -- the header's
    # declared *type* -- is driven below, and is refused by the header check rather than here). Three
    # facts make them something a hash comparison would call *equal* and a byte comparison would call
    # different: each really is a distinct document, none of them is the artifact, and they all declare
    # the original digest.
    assert set(respellings) == {
        "escaped_solidus",
        "insignificant_whitespace",
        "envelope_header_order",
        "table_mapping_order",
        "depth_two_key_order",
        "list_of_objects_key_order",
        "non_canonical_and_unsupported",
    }
    assert len(set(respellings.values())) == len(respellings)
    assert all(_declared_digest(text) == declared for text in respellings.values())
    assert doubly != respellings["depth_two_key_order"], (
        "the doubly-defective input must really declare another schema"
    )
    assert len(respellings["escaped_solidus"].encode("utf-8")) != len(artifact.encode("utf-8"))
    for name, text in respellings.items():
        refused = validate_knowledge_artifact(text)
        # asserted by name rather than as one set, so a regression that dropped a single level of the
        # rendering names the level it dropped instead of failing on whichever entry comes first.
        # The import half is asserted per entry too: the refusal reaches the caller before any
        # database work, so nothing is created for any of the seven.
        assert refused.state == "refused", name
        assert refused.refusal is not None, name
        assert refused.refusal.code == "invalid_export", name
        assert refused.refusal.record_id == "<canonical document>", name
        assert "canonical rendering" in _refusal(import_into(text, destination)).detail, name
        assert not destination.exists(), name

    # The eighth axis, and the only one the *header* refuses rather than the gate; then the per-key
    # measurement the digest-coverage statement rests on. Both are driven by helpers below, so the
    # axes stay readable as the two properties they are.
    _assert_the_header_types_are_pinned(artifact, declared, destination, tmp_path)

    # The published seam's own contract, asserted where the artifacts it renders already exist. Every
    # accepted artifact is its own canonical document and a respelling of one is not, so these
    # assertions are what make ``canonical_document``'s published contract load-bearing: without them
    # the function could return its input unchanged, or nothing at all, with every node still green.
    # Only the spelling-only axes have to render back to the artifact; the depth-2 and list axes inject
    # an object where the stored value held a plain string, so their canonical document is a third
    # document -- which is why they are refused for more than one reason and are not asserted here.
    assert canonical_document(artifact) == artifact
    assert canonical_document(respellings["escaped_solidus"]) == artifact
    assert canonical_document(respellings["envelope_header_order"]) == artifact
    assert canonical_document(respellings["table_mapping_order"]) == artifact
    assert canonical_document(respellings["insignificant_whitespace"]) == artifact
    assert (
        canonical_document(respellings["insignificant_whitespace"])
        != respellings["insignificant_whitespace"]
    )
    assert canonical_document("not a document at all") is None

    # The other half of the same property: the encoder emits the canonical order where the *stored*
    # value was permuted, and the reader-side check -- unreachable behind the gate, kept as defence in
    # depth -- still finds a permuted key when it is handed one directly, through both containers.
    assert _out_of_canonical_order(provenance) is None
    nested_only = {**provenance, "origin_refs": _permuted_origin_refs(provenance["origin_refs"])}
    assert _out_of_canonical_order(nested_only) == "origin_refs[0] ['version', 'requirement']"
    assert _out_of_canonical_order([{"z": 1, "a": 2}]) == "[0] ['z', 'a']"
    assert _out_of_canonical_order({"outer": {"z": 1, "a": 2}}) == "outer ['z', 'a']"


def _assert_the_header_types_are_pinned(
    artifact: str, declared: str, destination: Path, tmp_path: Path
) -> None:
    """Assert the two header-type properties the canonical-form case's name rests on.

    **The declared generation.** ``userVersion`` is the envelope's one non-string scalar and the one
    field a Python comparison can lose: ``1.0`` and ``true`` both equal ``1``, so an inequality alone
    calls a respelled generation equal and the artifact is accepted -- same digest, different bytes,
    and not even its own re-encoding. Both spellings are driven because Python has two ways to lose
    that comparison and a fix for one would leave the other open. The refusal is a *different* one
    than the axes above on purpose: the gate compares spelling and keeps the declared type, so this
    declaration is refused by name, as an unsupported generation, rather than as a rendering
    difference. The same defect is closed from the encoder's end too -- the published
    :func:`encode_export` renders the generation this build validates, so a consumer holding the
    respelled envelope cannot re-emit the artifact its own reader refuses.

    **Every other header key.** The digest does not cover the header's *types*; this half is the
    measurement that makes that statement safe rather than asserted. Each of the envelope's seven keys
    is respelled with a different JSON type and each respelling renders *equal to itself* -- asserted,
    so the whole-document gate passes it -- which is what proves the refusal comes from the header
    check, the only place a declaration's type is decided. The digest and the repository namespace
    carry data, so an ``isinstance`` guard refuses them; the generation is compared type-strictly; and
    the remaining three are compared for equality against the one string this build implements, which
    no other JSON value equals.
    """

    # Re-scoped with `KS-R10` §Shipped Assertions. The two loose spellings are driven against the
    # artifact's **own declared generation** -- the created generation, which is generation 2 after
    # this leaf. Both must be refused as an unsupported generation, with the observed spelling
    # rendered and the expected version being the *selected* generation's own.
    #
    # The boolean spelling is the one place generation 2 changes the property rather than its
    # operands: `True == 1` in Python, so ``true`` is a spelling the comparison can lose only where
    # the version is 1. That hazard is asserted where it lives, on generation 1 (below the loop),
    # rather than being silently dropped for the generation whose version is 2.
    loose = {
        "float_spelling": (
            float(CURRENT_GENERATION.user_version),
            f"{CURRENT_GENERATION.user_version}.0",
        ),
        "json_true": (True, "true"),
    }
    for name, (value, spelling) in loose.items():
        assert repr(value).lower() == spelling, name
        text = artifact.replace(
            f'"userVersion":{CURRENT_GENERATION.user_version},',
            f'"userVersion":{spelling},',
            1,
        )
        assert text != artifact, name
        assert _declared_digest(text) == declared, name
        refused = validate_knowledge_artifact(text)
        assert refused.state == "refused", name
        assert refused.refusal is not None, name
        assert refused.refusal.code == "unsupported_schema", name
        assert refused.refusal.observed == repr(value), name
        assert refused.refusal.expected == str(CURRENT_GENERATION.user_version), name
        assert "user version" in _refusal(import_into(text, destination)).detail, name
        assert not destination.exists(), name
        assert encode_export(envelope_of(text)) == artifact, name
        assert canonical_document(text) == artifact, name

    # The loose comparison Python can lose, stated where it is reachable: generation 1's version is
    # 1, and both spellings compare equal to it while hashing alike, which is the whole reason the
    # registry lookup is type-strict rather than equality-based.
    loose_boolean: object = True
    loose_float: object = 1.0
    assert loose_boolean == GENERATION_1.user_version
    assert loose_float == GENERATION_1.user_version
    assert generation_for_key(GENERATION_1.schema_name, True) is None
    assert generation_for_key(GENERATION_1.schema_name, 1.0) is None

    # The same refusal on a **genuine generation-1 artifact**, because the loop above drives the
    # artifact's own declared generation -- the created generation, which is generation 2 after this
    # leaf -- while the packet's re-scope row for this case requires generation 1's own rendering
    # (`expected == "1"`) to stay asserted. The registry-level assertions just above keep the
    # comparison hazard; this keeps the *refusal*, on an artifact whose ten tables and version come
    # from generation 1's own record. The string spelling is driven here too: `"1"` is refused for
    # the same reason as the two loose spellings, and generation 1 is the version it is spelled
    # against.
    version_one = tmp_path / "generation-one-artifact.db"
    with create_generation_1_store(version_one, str(uuid4())):
        pass
    version_one_artifact = exported(version_one).artifact or ""
    assert envelope_of(version_one_artifact)["schema"] == GENERATION_1.schema_name
    for spelling in ("1.0", "true", '"1"'):
        text = version_one_artifact.replace(
            f'"userVersion":{GENERATION_1.user_version},',
            f'"userVersion":{spelling},',
            1,
        )
        assert text != version_one_artifact, spelling
        version_one_refusal = validate_knowledge_artifact(text)
        assert version_one_refusal.state == "refused", spelling
        assert version_one_refusal.refusal is not None, spelling
        assert version_one_refusal.refusal.code == "unsupported_schema", spelling
        assert version_one_refusal.refusal.expected == str(GENERATION_1.user_version), spelling

    for name, (key, value, code, record_id) in _header_type_respellings().items():
        respelled = envelope_of(artifact)
        respelled[key] = value
        text = json.dumps(respelled, separators=(",", ":"), ensure_ascii=False)
        assert text != artifact, name
        assert canonical_document(text) == text, (
            f"{name}: this respelling must pass the whole-document gate, or the case is not measuring "
            "the header check at all"
        )
        refused = validate_knowledge_artifact(text)
        assert refused.state == "refused", name
        assert refused.refusal is not None, name
        assert refused.refusal.code == code, name
        assert refused.refusal.record_id == record_id, name


def _header_type_respellings() -> dict[str, tuple[str, Any, str, str | None]]:
    """Return one JSON-type respelling per envelope key, with the refusal each has to produce.

    Every key is covered, because the statement they back is about every header value rather than
    about the generation alone. Written out rather than derived, so the expected refusal is
    independent of the code that produces it.
    """

    return {
        "format_as_array": ("format", [EXPORT_FORMAT], "invalid_export", f"['{EXPORT_FORMAT}']"),
        # The three declared facts are respelled against the *selected* generation's own values
        # (KS-R10 §Shipped Assertions, the `user_version_as_string` row): a string is still not an
        # `int`, and the expected value rendered in the refusal is the selected generation's own
        # version string rather than generation 1's.
        "schema_as_array": (
            "schema",
            [CURRENT_GENERATION.schema_name],
            "unsupported_schema",
            None,
        ),
        "user_version_as_string": (
            "userVersion",
            str(CURRENT_GENERATION.user_version),
            "unsupported_schema",
            None,
        ),
        "fingerprint_as_array": (
            "schemaFingerprint",
            [CURRENT_GENERATION.fingerprint],
            "unsupported_schema",
            None,
        ),
        "repository_id_as_integer": ("repositoryId", 12345, "invalid_export", "12345"),
        "logical_digest_as_integer": ("logicalDigest", 12345, "invalid_export", "12345"),
        "tables_as_string": ("tables", "none", "invalid_export", None),
    }


# The schema name the datasets these cases build declare. They are created as the newest generation
# the build supports (requirement 2.7), so this is that generation's name and not generation 1's.
DECLARED_SCHEMA_NAME = CURRENT_GENERATION.schema_name


def _non_canonical_axes(artifact: str, provenance: dict) -> dict[str, str]:
    """Return every respelling of one document that the canonical-form rule has to refuse.

    One entry per axis: a JSON escape that spells the same character, insignificant whitespace, the
    envelope header's key order, the table mapping's key order, the depth-2 object's key order, the
    list-of-objects value's key order, and both defects at once. Each is a *respelling* of the same
    document -- the parsed values and the declared digest are unchanged -- which is why only the
    canonical-form rule can tell it apart from the artifact.

    ``encoder_direction_marker`` is not an axiom to refuse: it is the canonical artifact itself,
    carried under an honest name so the caller can assert the same positive property in the same loop.
    """

    depth_two, listed = _nested_spelling_mutants(artifact, provenance)
    return {
        "escaped_solidus": artifact.replace('"src/', '"src\\/', 1),
        "insignificant_whitespace": artifact.replace('],"', '],\n"', 1),
        "envelope_header_order": _reordered_document(artifact, envelope=True, tables=False),
        "table_mapping_order": _reordered_document(artifact, envelope=False, tables=True),
        "depth_two_key_order": depth_two,
        "list_of_objects_key_order": listed,
        # Re-scoped with `KS-R10`: the artifact declares its **own** generation's schema name, which
        # is the created generation's -- so the literal is read from the artifact rather than spelled
        # here, and a generation-1 literal would leave this axis identical to ``depth_two_key_order``
        # and collapse two independent refusals into one.
        "non_canonical_and_unsupported": depth_two.replace(
            f'"schema":"{DECLARED_SCHEMA_NAME}"', '"schema":"ar-knowledge-sqlite/v9"', 1
        ),
        "encoder_direction_marker": artifact,
    }


def _reordered_document(artifact: str, *, envelope: bool, tables: bool) -> str:
    """Return one artifact with the envelope's and/or the table mapping's key order reversed."""

    body = json.loads(artifact)
    document = {key: body[key] for key in reversed(list(body))} if envelope else dict(body)
    if tables:
        document["tables"] = {
            key: document["tables"][key] for key in reversed(list(document["tables"]))
        }
    return json.dumps(document, separators=(",", ":"), ensure_ascii=False)


def _respelled_encoding(
    fixture: BranchingKnowledgeFixture, provenance: dict, respelled_path: Path
) -> dict:
    """Return the value one export carries for a dataset whose stored spelling was permuted.

    The dataset really carries the permuted spelling -- the storage contract says the *value* is the
    knowledge, so this is a legal dataset -- and the encoder must still emit the one canonical artifact
    for that value. The copy is closed first, so the case edits a self-contained database.
    """

    copy_closed(fixture.database_path, respelled_path)
    connection = apsw.Connection(str(respelled_path))
    try:
        connection.execute(
            "UPDATE invariant SET label_provenance = ?", (json.dumps(_permuted_value(provenance)),)
        )
    finally:
        connection.close()
    respelled = exported(respelled_path).artifact or ""
    return envelope_of(respelled)["tables"]["invariant"][0]["label_provenance"]


def _permuted_value(provenance: dict) -> dict:
    """Return one typed value with its keys permuted at the top level and two levels down.

    Both levels are permuted in one value so a reader that canonicalises (or checks) only the
    outermost mapping is distinguishable from one that recurses: the second permutation is an object
    *inside a list* inside the outer object, which is the only shape that reaches both recursions.
    The value's key *set* and every value are unchanged, so this is one value spelled two ways.
    """

    return {
        key: _permuted_origin_refs(provenance[key]) if key == "origin_refs" else provenance[key]
        for key in sorted(provenance, reverse=True)
    }


def _permuted_origin_refs(refs: list) -> list:
    """Return one provenance list whose first entry is an object in reverse key order.

    The fixture stores requirement references as plain strings. The object form is the depth-2 and
    list axis the format has to carry: it is a mapping inside a list inside a mapping, and it is
    derived from the stored string rather than invented, so the case measures the *spelling* rule and
    not some other value.
    """

    first = refs[0]
    if isinstance(first, dict):
        return [{inner: first[inner] for inner in sorted(first, reverse=True)}, *refs[1:]]
    # The fixture stores requirement references as plain strings, so the depth-2 and list axis is
    # injected as an object that carries the same two facts the string names. The outer list is left
    # alone: what is out of order is the *object* inside it.
    requirement, version = first.split(":", 1)
    return [{"version": version, "requirement": requirement}, *refs[1:]]


def _canonical_value(provenance: dict) -> dict:
    """Return the one canonical spelling of the permuted value: the same facts, sorted keys.

    This is the value the encoder must emit for ``_permuted_value(provenance)``. Written out rather
    than computed through the encoder, because the point of the comparison is to be independent of
    the code under test.
    """

    requirement, version = provenance["origin_refs"][0].split(":", 1)
    refs = [{"requirement": requirement, "version": version}, *provenance["origin_refs"][1:]]
    return {key: refs if key == "origin_refs" else provenance[key] for key in sorted(provenance)}


def _nested_spelling_mutants(artifact: str, provenance: dict) -> tuple[str, str]:
    """Return two artifacts whose only difference is a nested key order inside a typed value.

    Both are re-rendered from the *parsed* document with the exact parameters this format renders
    with, so the whole document -- every declared order, and the digest it declares -- is byte-for-
    byte what the artifact's own text would be. That is what makes them spellings of one document
    rather than two documents: the digest cannot tell them apart, and only the canonical-form rule
    can.
    """

    def render(value: Any) -> str:
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)

    nested = json.loads(artifact)
    row = nested["tables"]["invariant_revision"][0]
    row["provenance"] = _permuted_value(provenance)
    listed = json.loads(artifact)
    listed["tables"]["invariant_revision"][0]["conditions"] = [
        {
            "statement": listed["tables"]["invariant_revision"][0]["conditions"][0],
            "role": "precondition",
        },
        *listed["tables"]["invariant_revision"][0]["conditions"][1:],
    ]
    return render(nested), render(listed)


# -- staged verification ---------------------------------------------------------------------


def test_an_artifact_whose_sealed_payload_contradicts_its_digest_is_refused(
    fixture: BranchingKnowledgeFixture, tmp_path: Path
) -> None:
    """A ``payload_digest`` is a stored identity, so the import re-derives it before publishing.

    Both revision tables carry one and it crosses the artifact as ordinary text, so nothing about
    parsing the document establishes it -- and the artifact is an untrusted input by this module's
    own rule. The destination already holds exactly the dataset the artifact came from, so the two
    outcomes are distinguishable rather than both looking like a refusal: without the re-derivation
    the broken dataset *replaces* a good one, and with it the import refuses and the destination is
    byte-identical, row-for-row identical per collection and logically identical afterwards.

    **Every retained revision is tampered, one artifact per row**, because the read the requirement
    names is "every retained revision" and a check that visited only the first row of each table
    would refuse all of these but the ones below the first -- which is exactly the gap a stale seal on
    a *second* row would install through. The `revision_id` each refusal names is asserted against the
    row that was actually broken, so the loop cannot pass by refusing for a different row's reason.
    """

    destination = tmp_path / "occupied.sqlite"
    assert import_into(artifact_of(fixture), destination).state == "installed"
    admitted = identity_of(destination)
    before_bytes = file_digest(destination)
    before_rows = row_counts_of(destination)
    codes: dict[tuple[str, int], str] = {}
    identifiers: dict[tuple[str, int], str] = {}

    for table in ("invariant_revision", "family_revision"):
        artifact = artifact_of(fixture)
        rows = envelope_of(artifact)["tables"][table]
        assert len(rows) > 1, f"{table} needs more than one row for this case to have breadth"
        for position in range(len(rows)):
            envelope = envelope_of(artifact)
            row = envelope["tables"][table][position]
            identifiers[(table, position)] = str(row["revision_id"])
            row["payload_digest"] = "0" * 64
            broken = reseal(envelope)
            refused = import_into(broken, destination, expected_destination=admitted)
            assert refused.state == "refused", (table, position, refused.state)
            codes[(table, position)] = _refusal(refused).code
            assert _refusal(refused).record_id == identifiers[(table, position)], (table, position)
            assert "payload digest" in _refusal(refused).detail

    assert set(codes.values()) == {"relationship_constraint"}
    assert len(codes) == len(identifiers)
    assert file_digest(destination) == before_bytes
    assert identity_of(destination) == admitted
    assert row_counts_of(destination) == before_rows
    assert sqlite_entries(tmp_path) == ["occupied.sqlite"]


def test_a_stage_opened_in_wal_mode_is_published_as_a_closed_database(
    fixture: BranchingKnowledgeFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The import's close/verify step is exercised: a stage that opens in WAL mode is normalised.

    The step exists because a *finished file* is not necessarily a self-contained database: a copy or
    a stage that inherits WAL mode leaves a header whose completeness depends on a peer file that the
    atomic replace does not carry, which is the L4/D-5 class. A stage this module builds is opened in
    SQLite's default rollback mode, so the producer is injected here -- the stage connection is opened
    in WAL mode -- and the published destination, not the stage, is what the assertions read: a
    published dataset must be a closed delete-mode database with no peer beside it.
    """

    artifact = artifact_of(fixture)
    opened: list[str] = []
    open_database = export_import.open_database

    def open_in_wal(stage: Path):
        connection = open_database(stage)
        opened.append(str(next(iter(connection.execute("PRAGMA journal_mode=WAL")))[0]).lower())
        return connection

    monkeypatch.setattr(export_import, "open_database", open_in_wal)
    destination = tmp_path / "restored.sqlite"

    installed = import_into(artifact, destination)

    assert opened == ["wal"]
    assert installed.state == "installed", installed.refusal
    assert installed.publication == "published"
    assert journal_mode_of(destination) == "delete"
    assert sqlite_entries(tmp_path) == ["restored.sqlite"]
    assert identity_of(destination).logical_digest == _declared_digest(artifact)


# -- destination admission -------------------------------------------------------------------


def test_destination_admission_refuses_before_any_staging_work(
    fixture: BranchingKnowledgeFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three destination states are refused by the admission check, before a stage exists.

    *When* the refusal happens is the property, and the code alone cannot show it: the publication
    refuses the same inputs one step later, and for an admitted-but-absent destination it refuses
    under the very same ``destination_stale`` code. The staging call is therefore recorded as well as
    the code, because "the caller learns the path is wrong without any database work" is what the
    check is for: an unreadable destination is ``selected_input_unavailable`` (the publication's code
    for it is ``destination_stale``, so a collapsed check would be visibly different), an occupied
    destination with no admitted identity is ``destination_occupied``, and an admitted destination
    that is gone is ``destination_stale``.
    """

    artifact = artifact_of(fixture)
    occupied = tmp_path / "occupied.sqlite"
    other = build_branching_knowledge_fixture(tmp_path / "other")
    assert import_into(artifact_of(other), occupied).state == "installed"
    occupied_bytes = file_digest(occupied)
    unreadable_note = "this file is not a knowledge dataset"
    unreadable = tmp_path / "unreadable.sqlite"
    unreadable.write_text(unreadable_note)
    absent = tmp_path / "absent.sqlite"
    staged: list[str] = []
    private_stage_directory = export_import._private_stage_directory

    def record_staging() -> Path:
        staged.append("staged")
        return private_stage_directory()

    monkeypatch.setattr(export_import, "_private_stage_directory", record_staging)
    outcomes = {
        "occupied": import_into(artifact, occupied),
        "unreadable": import_into(artifact, unreadable),
        "absent_admitted": import_into(
            artifact,
            absent,
            expected_destination=logical.SnapshotIdentity(
                repository_id=fixture.repository_id,
                schema_version=KNOWLEDGE_SCHEMA_NAME,
                logical_digest="0" * 64,
            ),
        ),
    }

    assert [outcome.state for outcome in outcomes.values()] == ["refused"] * 3
    assert {
        name: outcome.refusal.code for name, outcome in outcomes.items() if outcome.refusal
    } == {
        "occupied": "destination_occupied",
        "unreadable": "selected_input_unavailable",
        "absent_admitted": "destination_stale",
    }
    assert staged == []
    assert file_digest(occupied) == occupied_bytes
    assert unreadable.read_text() == unreadable_note
    assert not absent.exists()


# -- reading an artifact file ------------------------------------------------------------------


def test_an_artifact_that_cannot_be_read_as_text_is_refused_with_a_typed_code(
    fixture: BranchingKnowledgeFixture, tmp_path: Path
) -> None:
    """Reading an artifact is a value-or-refusal boundary: nothing raises for a caller to catch.

    Three inputs, two codes, because they are different facts rather than one failure: a selected path
    that is not a readable file is ``selected_input_unavailable``, and a byte sequence that is not
    UTF-8 is ``invalid_export`` -- a portable artifact is UTF-8 by construction, so reading it
    through a replacement-character guess would turn a malformed artifact into a document that parses.
    An ``OSError`` or a ``UnicodeDecodeError`` crossing this boundary would leave the caller with a
    failure that has no code to branch on.

    An empty file and a file that is text but not JSON are read *successfully* and refused by the
    validator that owns the document (``the artifact is empty`` / ``not valid JSON``): the read
    helper's contract is text, and it is not the layer that decides whether the text is an artifact.
    The two halves are asserted together so the boundary cannot drift into refusing readable text or
    into handing a broken byte sequence to the parser.
    """

    artifact = artifact_of(fixture)
    present = tmp_path / "artifact.json"
    present.write_text(artifact, encoding="utf-8")
    absent = tmp_path / "absent.json"
    directory = tmp_path / "a-directory"
    directory.mkdir()
    not_text = tmp_path / "not-text.json"
    payload = b'{"format":"ar-knowledge-export/v1","repositoryId":"\xff\xfe not utf-8"}'
    not_text.write_bytes(payload)
    empty = tmp_path / "empty.json"
    empty.write_text("")
    not_json = tmp_path / "not-json.json"
    not_json.write_text("this is text but not an artifact")

    read = read_knowledge_artifact(present)
    missing = read_knowledge_artifact(absent)
    not_a_file = read_knowledge_artifact(directory)
    undecodable = read_knowledge_artifact(not_text)

    assert read == artifact
    assert isinstance(missing, KnowledgeRefusal)
    assert missing.code == "selected_input_unavailable"
    assert isinstance(not_a_file, KnowledgeRefusal)
    assert not_a_file.code == "selected_input_unavailable"
    assert isinstance(undecodable, KnowledgeRefusal)
    assert undecodable.code == "invalid_export"
    assert "UTF-8" in undecodable.detail
    assert not_text.read_bytes() == payload
    assert read_knowledge_artifact(empty) == ""
    assert read_knowledge_artifact(not_json) == "this is text but not an artifact"
    assert validate_knowledge_artifact("").state == "refused"
    assert validate_knowledge_artifact("this is text but not an artifact").state == "refused"
