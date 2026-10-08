"""MIK-R21: the text knowledge format's models, IDs and locations.

The Doc14 §4 worked examples are encoded as fixtures in ``fixtures/knowledge_files/``. Doc14's
examples are illustrative and predate the packet, so each fixture carries the same content in the
packet's normative shape: references are ``{targets: [...]}`` with typed targets, realization and
proof entries carry ``id`` and an ``anchor`` (``locator``, recorded ``blob``, ``content``), the
invariant's ``supersedes`` is a list, and the decision's ``governs``/``reconsider_on`` become
``links``. The recorded blobs are the real blobs at base ``b7ef73f8``; content hashes are
illustrative. §4.7 (history files) is owned by MIK-R07 and is not encoded here. Two cases below keep
Doc14's verbatim spellings and prove the models refuse them, so the adaptation is deliberate.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any, cast

import pytest
from agents_remember.models.knowledge_files import (
    RECORD_MODELS,
    RECORD_PREFIXES,
    RELATIONS_BY_KIND,
    SCHEMA_MODELS,
    canonical_text,
    derived_realization_id,
    derived_record_id,
    file_sidecar_path,
    mint_id,
    parse_document,
    parse_document_text,
    record_path,
    route_sidecar_path,
)
from agents_remember.models.knowledge_files.documents import split_record_filename
from agents_remember.models.knowledge_files.ids import (
    CROCKFORD_ALPHABET,
    ENTRY_PREFIXES,
    RECORD_ID_PATTERN,
    EntryKind,
    RecordKind,
    crockford_base32,
)
from agents_remember.models.knowledge_files.records import schema_name
from pydantic import ValidationError

FIXTURES = Path(__file__).parent / "fixtures" / "knowledge_files"
BLOB = "0" * 40
CONTENT = "sha256:" + "a" * 64
ORIGIN = {"task": "260928-MIK", "leaf": "260928-MIK-L21"}


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# ------------------------------------------------------------------------------------------------
# IDs
# ------------------------------------------------------------------------------------------------


def test_minted_ids_are_random_crockford_with_their_kind_prefix() -> None:
    assert set(CROCKFORD_ALPHABET).isdisjoint("ILOU") and len(CROCKFORD_ALPHABET) == 32
    kinds: list[RecordKind | EntryKind] = [*RECORD_PREFIXES, *ENTRY_PREFIXES]
    prefixes: dict[str, str] = {**RECORD_PREFIXES, **ENTRY_PREFIXES}
    for kind in kinds:
        prefix = prefixes[kind]
        minted = mint_id(kind)
        assert re.fullmatch(rf"{prefix}-[{CROCKFORD_ALPHABET}]{{6}}", minted), minted
    batch = {mint_id("invariant") for _ in range(2000)}
    assert len(batch) > 1990, "minting must be random, not sequential"
    assert re.fullmatch(RECORD_ID_PATTERN, "INV-0143") is None, "a sequential ID is refused"
    with pytest.raises(ValueError, match="unknown"):
        mint_id(cast(Any, "widget"))


def test_derived_ids_are_eight_characters_and_deterministic() -> None:
    # Golden values pin the derivation (sha256 -> first 40 bits -> Crockford), so a later build
    # reproduces exported IDs byte for byte (MIK-R24 rule 6).
    assert crockford_base32(bytes([0b00001_000, 0b10_00011_0, 0, 0, 0]), 8) == "12300000"
    legacy = "0b8124cd-1f0e-4c55-9a51-d3a1c0f1b2e4"
    derived = derived_record_id("family", legacy)
    assert derived == "FAM-SEQNTS6C"
    assert derived == derived_record_id("family", legacy)
    assert derived_record_id("invariant", legacy) == "INV-SEQNTS6C"
    locator = {"kind": "symbol", "name": "validate_integrate_memory_contract"}
    entry = derived_realization_id(legacy, "mcp/x.py", locator)
    assert re.fullmatch(rf"RLZ-[{CROCKFORD_ALPHABET}]{{8}}", entry)
    assert entry == derived_realization_id(legacy, "mcp/x.py", dict(reversed(locator.items())))
    # The encoding is pinned: compact key-sorted JSON of [legacy id, path, new-form locator].
    assert entry == "RLZ-0QYX992Q"
    material = (
        '["0b8124cd-1f0e-4c55-9a51-d3a1c0f1b2e4","mcp/x.py",'
        '{"kind":"symbol","name":"validate_integrate_memory_contract"}]'
    )
    digest = hashlib.sha256(material.encode("utf-8")).digest()
    assert entry == f"RLZ-{crockford_base32(digest, 8)}"
    assert entry != derived_realization_id(legacy, "mcp/y.py", locator)
    with pytest.raises(ValueError):
        derived_record_id("invariant", "")


# ------------------------------------------------------------------------------------------------
# Doc14 worked examples
# ------------------------------------------------------------------------------------------------

DOC14_FIXTURES = {
    "4.1-direct_landing.py.json": "FileSidecar",
    "4.1-worktrees-overview.json": "RouteSidecar",
    "4.2-family-attribution-and-landing-pairing.json": "FamilyRecord",
    "4.3-invariant-landing-pair.json": "InvariantRecord",
    "4.4-test_direct_landing.py.json": "FileSidecar",
    "4.5-decision-d12-local-family-routes.json": "DecisionRecord",
    "4.6-incident.json": "IncidentRecord",
    "r21-integrate.py.json": "FileSidecar",
    "layout.json": "LayoutMarker",
}


def test_doc14_worked_examples_validate_and_round_trip_byte_for_byte() -> None:
    assert sorted(path.name for path in FIXTURES.glob("*.json")) == sorted(DOC14_FIXTURES)
    for name, model_name in DOC14_FIXTURES.items():
        raw = (FIXTURES / name).read_bytes()
        model = parse_document_text(raw.decode("utf-8"))
        assert type(model).__name__ == model_name, name
        # parse -> serialize -> canonical text reproduces the file exactly: nothing is added,
        # dropped or rewritten by the models.
        assert canonical_text(model.to_document()).encode("utf-8") == raw, name


def test_doc14_verbatim_illustrative_shapes_are_refused() -> None:
    invariant = _fixture("4.3-invariant-landing-pair.json")
    invariant["supersedes"] = None  # Doc14 §4.3 writes ``"supersedes": null``
    with pytest.raises(ValidationError, match="explicit null"):
        parse_document(invariant)
    sidecar = _fixture("4.1-direct_landing.py.json")
    sidecar["references"]["2"] = {"code": {"symbol": "_verify_code_commit", "content": CONTENT}}
    with pytest.raises(ValidationError):
        parse_document(sidecar)


# ------------------------------------------------------------------------------------------------
# Every record kind
# ------------------------------------------------------------------------------------------------

_KIND_FIELDS: dict[RecordKind, dict[str, Any]] = {
    "invariant": {
        "revision": 1,
        "status": "accepted",
        "statement": "S.",
        "applicability": "A.",
        "conditions": ["C."],
        "exclusions": [],
        "supersedes": [],
        "admission": {"criteria": ["guarded_by_test"], "justification": "J."},
    },
    "family": {
        "revision": 2,
        "status": "retired",
        "title": "t",
        "guarantee": "G.",
        "members": ["INV-7K3F9Q"],
        "routes": ["mcp/src"],
        "admission": {"criteria": ["joint_guarantee"], "justification": "J."},
    },
    "decision": {
        "revision": 1,
        "status": "under_reconsideration",
        "context": "C.",
        "alternatives": [{"option": "o", "status": "deferred", "reason": "r"}],
        "consequences": [],
        "decider": "developer",
        "supersedes": ["DEC-0K3F9Q"],
        "links": [{"target": "route:mcp/src", "relation": "explains"}],
        "admission": "legacy-unassessed",
    },
    "incident": {
        "revision": 1,
        "status": "proposed",
        "occurrence": "O.",
        "observed_at": "2026-09-28",
        "observed_effect": "E.",
        "detection": "D.",
        "cause": "C.",
        "cause_uncertainty": "U.",
        "applicability": "unresolved",
        "links": [
            {
                "target": {
                    "path": "mcp/x.py",
                    "locator": {"kind": "line_range", "start": 1, "end": 2},
                    "blob": BLOB,
                    "content": CONTENT,
                },
                "relation": "occurred_at",
            }
        ],
    },
    "assumption": {
        "revision": 3,
        "status": "retired",
        "proposition": "P.",
        "basis": "B.",
        "links": [],
    },
    "limitation": {
        "revision": 1,
        "status": "accepted",
        "limited": "L.",
        "boundary": "B.",
        "unsupported": ["U."],
        "links": [],
    },
    "failure_mode": {
        "revision": 1,
        "status": "accepted",
        "failure": "F.",
        "condition": "C.",
        "observable_effect": "E.",
        "links": [{"target": "INV-7K3F9Q", "relation": "threatens"}],
    },
    "scenario": {
        "revision": 1,
        "status": "accepted",
        "situation": "S.",
        "preconditions": ["P."],
        "outcome": "O.",
        "links": [],
    },
    "diagnostic": {
        "revision": 1,
        "status": "accepted",
        "condition": "C.",
        "signal": "S.",
        "interpretation": "I.",
        "interpretation_limit": "L.",
        "links": [],
    },
    "term": {
        "revision": 1,
        "status": "accepted",
        "term": "leaf",
        "definition": "D.",
        "scope": "S.",
        "links": [],
    },
}


def _record(kind: RecordKind, **overrides: Any) -> dict[str, Any]:
    document = {
        "schema": schema_name(kind),
        "id": f"{RECORD_PREFIXES[kind]}-7K3F9Q",
        "origin": dict(ORIGIN),
        **copy.deepcopy(_KIND_FIELDS[kind]),
    }
    document.update(overrides)
    return document


@pytest.mark.parametrize("kind", sorted(RECORD_MODELS))
def test_every_record_kind_parses_round_trips_and_refuses_a_foreign_prefix(
    kind: RecordKind,
) -> None:
    document = _record(kind)
    model = parse_document(document)
    assert isinstance(model, RECORD_MODELS[kind])
    assert model.to_document() == document
    assert SCHEMA_MODELS[schema_name(kind)] is RECORD_MODELS[kind]
    other = "TRM" if kind != "term" else "INV"
    with pytest.raises(ValidationError):
        parse_document(_record(kind, id=f"{other}-7K3F9Q"))
    with pytest.raises(ValidationError):
        parse_document(_record(kind, undeclared="x"))
    # Every relation not in this kind's vocabulary is refused on this kind.
    every = {relation for relations in RELATIONS_BY_KIND.values() for relation in relations}
    if "links" not in document:
        return
    for relation in sorted(every - RELATIONS_BY_KIND[kind]):
        link: dict[str, Any] = {"target": "INV-7K3F9Q", "relation": relation}
        if relation == "reconsider_on":
            link["alternative"] = 0
        with pytest.raises(ValidationError, match="not allowed"):
            parse_document(_record(kind, links=[link]))


# ------------------------------------------------------------------------------------------------
# Non-conforming shapes
# ------------------------------------------------------------------------------------------------


def _refused(document: dict[str, Any], match: str | None = None) -> None:
    with pytest.raises(ValidationError, match=match):
        parse_document(document)


def test_records_refuse_second_owners_and_malformed_record_fields() -> None:
    # The packet's non-conforming example: an invariant listing its code locations.
    _refused(_record("invariant", realizations=[{"path": "mcp/x.py"}]), "Extra inputs")
    _refused(_record("invariant", families=["FAM-7K3F9Q"]), "Extra inputs")
    _refused(_record("invariant", revision=0))
    _refused(_record("invariant", status="active"))
    _refused(_record("invariant", supersedes=["INV-7K3F9Q"]), "supersede itself")
    _refused(_record("invariant", conditions=["  "]), "blank")
    _refused(
        _record("invariant", admission={"criteria": ["joint_guarantee"], "justification": "J"})
    )
    _refused(_record("invariant", admission={"criteria": [], "justification": "J"}))
    _refused(_record("invariant", admission="unassessed"))
    _refused(_record("family", members=["INV-7K3F9Q", "INV-7K3F9Q"]), "repeat")
    _refused(_record("family", routes=["../escape"]), "segments")
    _refused(_record("family", routes=["/abs"]), "repository-relative")
    _refused(_record("decision", superseded=True), "Extra inputs")
    _refused(_record("decision", status="retired"))
    _refused(_record("scenario", status="active"))
    _refused(_record("term", revision=0))
    _refused(_record("incident", admission="legacy-unassessed"), "Extra inputs")
    _refused(_record("decision", alternatives=[{"option": "o", "status": "maybe", "reason": "r"}]))
    _refused(_record("incident", applicability="resolved"), "recovery")
    _refused(_record("invariant", origin={"leaf": "L"}))
    _refused(_record("invariant", origin={"task": "T", "leaf": "L", "wave": "W"}), "not both")
    _refused(_record("invariant", origin={"task": "T", "handoff": {}}), "hand-off origin")
    _refused(_record("invariant", origin={"task": " T"}), "whitespace")
    resolved = _record(
        "incident", applicability="historical_only", recovery="R.", corrective_actions=[]
    )
    assert parse_document(resolved).to_document() == resolved


def test_links_carry_their_relation_rules() -> None:
    reconsider = {"target": "INV-7K3F9Q", "relation": "reconsider_on"}
    _refused(_record("decision", links=[reconsider]), "alternative")
    _refused(_record("decision", links=[{**reconsider, "alternative": -1}]))
    _refused(
        _record(
            "decision", links=[{"target": "INV-7K3F9Q", "relation": "explains", "alternative": 0}]
        ),
        "alternative",
    )
    _refused(_record("decision", links=[{"target": "INV-0143", "relation": "explains"}]))
    _refused(_record("decision", links=[{"target": "route:/abs", "relation": "explains"}]))
    anchor_without_path = {"locator": {"kind": "file"}, "blob": BLOB, "content": CONTENT}
    _refused(
        _record("decision", links=[{"target": anchor_without_path, "relation": "explains"}]),
        "name its path",
    )


def _sidecar(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema": "ar-onboarding-file/v1",
        "path": "mcp/x.py",
        "references": {},
        "realizes": [],
    }
    document.update(overrides)
    return document


def _anchor(**overrides: Any) -> dict[str, Any]:
    return {
        "locator": {"kind": "symbol", "name": "f"},
        "blob": BLOB,
        "content": CONTENT,
        **overrides,
    }


def _realization(**overrides: Any) -> dict[str, Any]:
    return {
        "id": "RLZ-7K3F9Q",
        "invariant": "INV-7K3F9Q",
        "anchor": _anchor(),
        "role": "enforcement",
        "rationale": "R.",
        **overrides,
    }


def test_sidecars_references_and_anchors_refuse_malformed_shapes() -> None:
    def ref(*targets: dict[str, Any]) -> dict[str, Any]:
        return {"references": {"1": {"targets": list(targets)}}}

    code = {"kind": "code", "anchor": _anchor(path="mcp/y.py")}
    ok = _sidecar(**ref(code, {"kind": "unresolved", "text": "old cite"}))
    assert parse_document(ok).to_document() == ok
    _refused(_sidecar(**ref()))
    _refused(_sidecar(references={"0": {"targets": [code]}}))
    _refused(_sidecar(references={"01": {"targets": [code]}}))
    _refused(_sidecar(**ref({"kind": "code", "anchor": _anchor(path="mcp/x.py")})), "omits 'path'")
    _refused(_sidecar(**ref({"kind": "invariant", "id": "FAM-7K3F9Q"})), "INV- ID")
    _refused(_sidecar(**ref({"kind": "record", "id": "XYZ-7K3F9Q"})))
    _refused(_sidecar(**ref({"kind": "code", "anchor": _anchor(content="sha256:abc")})))
    _refused(_sidecar(**ref({"kind": "code", "anchor": _anchor(blob="xyz")})))
    reversed_range = {"kind": "line_range", "start": 5, "end": 4}
    _refused(
        _sidecar(**ref({"kind": "code", "anchor": _anchor(locator=reversed_range)})), "precede"
    )
    requirement = {
        "task": {"repository": "agents-remember", "path": "260928_x"},
        "packet": "requirements/p.md",
        "id": "MIK-R21",
        "version": "1",
    }
    _refused(_sidecar(**ref({"kind": "requirement", "requirement": requirement})))
    _refused(_sidecar(realizes=[_realization(anchor=_anchor(path="mcp/x.py"))]), "omits 'path'")
    _refused(_sidecar(realizes=[_realization(role="incidental")]))
    _refused(_sidecar(realizes=[_realization(rationale=" ")]), "blank")
    _refused(_sidecar(realizes=[_realization(), _realization()]), "repeat")
    _refused(_sidecar(realizes=[_realization(id="PRF-7K3F9Q")]))
    _refused(_sidecar(path="C:/x.py"), "repository-relative")
    _refused(_sidecar(path=":(exclude)x.py"), "pathspec")
    route = {"schema": "ar-onboarding-route/v1", "path": ".", "references": {}}
    assert parse_document(route).to_document() == route
    _refused({**route, "coveredFiles": []}, "Extra inputs")
    _refused({**route, "references": ref({"kind": "code", "anchor": _anchor()})["references"]})
    _refused({**route, "path": "a/../b"})
    _refused({"schema": "ar-memory-layout/v2"})
    with pytest.raises(ValueError, match="unknown knowledge schema"):
        parse_document({"schema": "ar-census/v1"})


# ------------------------------------------------------------------------------------------------
# Locations
# ------------------------------------------------------------------------------------------------


def test_locations_follow_the_layout_and_the_slug_is_display_only() -> None:
    assert (
        record_path("invariant", "INV-7K3F9Q", "landing-code-and-memory-as-one-pair")
        == "knowledge/invariants/INV-7K3F9Q-landing-code-and-memory-as-one-pair.json"
    )
    assert record_path("failure_mode", "FLM-7K3F9Q", "x", extension="md") == (
        "knowledge/failure-modes/FLM-7K3F9Q-x.md"
    )
    assert split_record_filename("FAM-SEQNTS6C-ICR30-I-1.json") == (
        "FAM-SEQNTS6C",
        "ICR30-I-1",
        "json",
    )
    with pytest.raises(ValueError):
        split_record_filename("INV-0143-x.json")
    with pytest.raises(ValueError):
        record_path("invariant", "INV-7K3F9Q", "bad/slug")
    assert file_sidecar_path("mcp/x.py") == "onboarding/mcp/x.py.json"
    assert route_sidecar_path(".") == "onboarding/overview.json"
    assert route_sidecar_path("mcp/src") == "onboarding/mcp/src/overview.json"
