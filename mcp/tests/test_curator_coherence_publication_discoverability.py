"""The curator-coherence request states its own publication inputs (``KS-R24@v1``).

``publish`` requires nine request members, and a caller could only discover which ones by reading the
validator: two of them looked optional, ``prepare`` never echoed them, and the refusal named none of
them. These cases pin every refusal to the request member it is about and the ``prepare`` text to the
one declaration that the model, the validator and the text all read, so the input set is discoverable
from the tool's own answers rather than from its source.

Split from ``test_final_full_memory_coherence_certification.py`` at the repository's file-size hard
limit, along the properties rather than the line count: that module protects the Gate-5
final-certification orchestration and owns the scaffold its sibling modules import, while these cases
protect publication discoverability. The two are different subjects that happened to share a file,
and each half now asserts exactly what the whole file asserted before the split.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

import pytest
from agents_remember.models.declared_caller import DeclaredCaller
from agents_remember.models.lifecycles import curator_coherence as coherence_models
from agents_remember.models.lifecycles.curator_coherence import (
    JUDGMENTS_MEMBER,
    PUBLICATION_MEMBERS,
    REVIEW_ASSESSMENTS_MEMBER,
    CuratorCoherenceRequest,
    PublicationMember,
    publication_input_statement,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.task_intent import TaskIntentIdentity
from agents_remember.worktrees.integration.closeout import curator_coherence_publication
from pydantic import ValidationError

_CODE_TREE = "c" * 40
_MEMORY_TREE = "7" * 40


# ---------------------------------------------------------------------------
# KS-R24@v1 -- the curator-coherence request states its own publication inputs
# ---------------------------------------------------------------------------
# `publish` required nine request members to be non-`None` while two of them looked optional, were
# never echoed by `prepare`, and were not named by the refusal, so a caller following the tool's
# own flow could only discover them by reading the validator. These cases pin every message to the
# request member it is about and the `prepare` text to the one declaration beside the request
# model, which the validator, the refusal and the text all read.

_CONTRACT_PATH = "/coord/tasks/repo/master/enclosures/leaf/series-contract.md"
_REFUSAL_PREFIX = "Value error, publish requires every identity, predecessor, and caller field; "

_PUBLICATION_INPUT_NAMES: tuple[str, ...] = (
    "semantic_requirement_revision",
    "delivery_attempt",
    "expected_predecessor_digest",
    "expected_code_candidate_tree",
    "expected_memory_candidate_tree",
    "expected_task_topology_fingerprint",
    "expected_task_intent",
    "expected_attestation_sha256",
    "caller",
)

_MEMBER_VALUES: dict[str, Any] = {
    "semantic_requirement_revision": "KS-R24@v1",
    "delivery_attempt": "260915-KS-L24-A1",
    "expected_predecessor_digest": "",
    "expected_code_candidate_tree": _CODE_TREE,
    "expected_memory_candidate_tree": _MEMORY_TREE,
    "expected_task_topology_fingerprint": "12" * 32,
    "expected_task_intent": TaskIntentIdentity(digest="c" * 64),
    "expected_attestation_sha256": "a" * 64,
    "caller": DeclaredCaller(
        role="curator",
        task_document_ref=TaskDocumentRef(repository="repo", path="master/leaf.json"),
    ),
}

_JUDGMENT: dict[str, Any] = {
    "sourceFile": "source.py",
    "onboardingFile": "source.py.md",
    "classification": "reconciled",
    "disposition": "reconciled",
    "rationale": "The publication text states the declared input set.",
    "evidenceRef": "code:source.py",
}


def _publication_request(**overrides: Any) -> dict[str, Any]:
    """One publish request carrying every publication member, before the overrides."""

    return {"action": "publish", "contract_path": _CONTRACT_PATH, **_MEMBER_VALUES, **overrides}


def _refusal_message(request: dict[str, Any]) -> str:
    with pytest.raises(ValidationError) as caught:
        CuratorCoherenceRequest.model_validate(request)
    return cast(str, caught.value.errors()[0]["msg"])


def _missing_detail(message: str) -> str:
    """The refusal's own text after the shipped opening sentence."""

    assert message.startswith(_REFUSAL_PREFIX)
    return message.removeprefix(_REFUSAL_PREFIX)


def _prepared_payload() -> dict[str, Any]:
    """The real ``prepare`` response over one stubbed observation boundary.

    ``_prepare`` owns the shipped text; only the observation it summarizes and the paths it echoes
    are stubbed, so the summary asserted here is the one a caller receives.
    """

    observation = SimpleNamespace(
        pair_identity=SimpleNamespace(model_dump=lambda **_: {"contractDigest": "a" * 64}),
        code_candidate_tree=_CODE_TREE,
        memory_candidate_tree=_MEMORY_TREE,
        task_topology_fingerprint="12" * 32,
        task_intent=SimpleNamespace(
            model_dump=lambda **_: {"schema": "task-intent/v1", "digest": "c" * 64}
        ),
        attestation_path=Path("/work/group/reports/curator-memory-quality.json"),
        attestation_sha256="a" * 64,
        attestation=SimpleNamespace(reportSha256="b" * 64),
        source_candidates=[],
    )
    contract: Any = SimpleNamespace(contract_path=Path(_CONTRACT_PATH))
    with mock.patch.multiple(
        curator_coherence_publication,
        observe_curator_coherence_source=mock.Mock(return_value=observation),
        current_curator_coherence_predecessor=mock.Mock(return_value=""),
        curator_coherence_paths=mock.Mock(
            return_value=SimpleNamespace(canonical=Path("/coord/leaf-curator-coherence.json"))
        ),
    ):
        return curator_coherence_publication.curator_coherence_action(
            contract, CuratorCoherenceRequest(action="prepare", contract_path=_CONTRACT_PATH)
        )


@pytest.mark.parametrize("omitted", _PUBLICATION_INPUT_NAMES)
def test_publish_refusal_names_the_one_missing_publication_member(omitted: str) -> None:
    """Each member omitted alone is named; no other member is reported as missing.

    The assertion reads the members the refusal reports rather than whole-message substrings: the
    shipped opening sentence contains "caller" as prose and the required callout contains
    "caller-supplied", so a substring search cannot tell those from a reported member.
    """

    detail = _missing_detail(_refusal_message(_publication_request(**{omitted: None})))

    if omitted in {"semantic_requirement_revision", "delivery_attempt"}:
        assert detail == (
            f"missing {omitted}; {omitted} is a caller-supplied delivery identity prepare does"
            " not derive -- supply it in the request."
        )
    else:
        assert detail == f"missing {omitted}."


def test_publish_refusal_without_any_member_names_all_nine_in_declaration_order() -> None:
    detail = _missing_detail(
        _refusal_message(_publication_request(**dict.fromkeys(_PUBLICATION_INPUT_NAMES)))
    )

    assert detail == (
        "missing "
        + ", ".join(_PUBLICATION_INPUT_NAMES)
        + "; semantic_requirement_revision and delivery_attempt are caller-supplied delivery"
        " identities prepare does not derive -- supply them in the request."
    )


def test_publish_with_every_publication_member_validates() -> None:
    """The positive control: the nine-member request the existing publication case sends.

    The declaration is also asserted to be the request model's own field order, which 1.2 makes
    the ordering authority, so a field added to the model without the declaration fails here.

    ``judgments`` and ``review_assessments`` are the two *content* members: a leaf with nothing to
    reconcile or nothing to review supplies neither, which is why neither is one of the nine. They
    are named from the module's own constants rather than spelled here, so the exclusion set and the
    shape validator that refuses them on ``status``/``prepare``/``validate`` cannot drift apart --
    and a field added to the model that is neither declared nor named in that set still fails here.
    """

    request = CuratorCoherenceRequest.model_validate(_publication_request())
    declared = tuple(member.name for member in PUBLICATION_MEMBERS)
    content_members = {JUDGMENTS_MEMBER, REVIEW_ASSESSMENTS_MEMBER}
    excluded = {"action", "contract_path", "freeze_snapshot"} | content_members
    assert content_members.isdisjoint(declared)
    model_order = tuple(
        name for name in CuratorCoherenceRequest.model_fields if name not in excluded
    )

    assert request.action == "publish" and request.caller is not None
    assert declared == _PUBLICATION_INPUT_NAMES == model_order


def test_prepare_states_the_complete_publication_input_set() -> None:
    """2.1 and 2.3: the prepare response names the judgments and every member, and invents none."""

    payload = _prepared_payload()
    summary = cast(str, payload["summary"])
    named = summary.split("publish requires: ", 1)[1]

    assert payload["state"] == "prepared"
    assert publication_input_statement() in summary
    assert "one agent-owned judgment per source candidate" in summary
    assert named.partition(";")[0] == ", ".join(_PUBLICATION_INPUT_NAMES)
    assert "caller-supplied delivery identities" in named
    assert payload.get("semanticRequirementRevision") is None
    assert payload.get("deliveryAttempt") is None


def test_a_member_added_to_the_declaration_reaches_both_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2.2 and 1.1: an added member needs no second edit anywhere.

    The declaration is extended and a scratch request model declares the matching field, so the
    refusal names the added member and the prepare text states it, with neither text touched.
    """

    added = PublicationMember("expected_review_verdict_sha256")
    monkeypatch.setattr(coherence_models, "PUBLICATION_MEMBERS", (*PUBLICATION_MEMBERS, added))

    class _ScratchCoherenceRequest(CuratorCoherenceRequest):
        expected_review_verdict_sha256: str | None = None

    assert added.name in publication_input_statement()

    with pytest.raises(ValidationError) as caught:
        _ScratchCoherenceRequest.model_validate(_publication_request(**{added.name: None}))

    assert _missing_detail(caught.value.errors()[0]["msg"]) == f"missing {added.name}."


@pytest.mark.parametrize("action", ["status", "prepare", "validate"])
def test_non_publish_actions_name_the_publication_member_they_received(action: str) -> None:
    message = _refusal_message(
        {
            "action": action,
            "contract_path": _CONTRACT_PATH,
            "expected_code_candidate_tree": _CODE_TREE,
        }
    )

    assert message == (
        "Value error, status/prepare/validate forbid publication-only fields; supplied "
        "expected_code_candidate_tree"
    )


def test_non_publish_refusal_names_every_supplied_field_in_model_order() -> None:
    message = _refusal_message(
        {
            "action": "validate",
            "contract_path": _CONTRACT_PATH,
            "delivery_attempt": "260915-KS-L24-A1",
            "expected_attestation_sha256": "a" * 64,
            "judgments": [_JUDGMENT],
        }
    )

    assert message == (
        "Value error, status/prepare/validate forbid publication-only fields; supplied "
        "delivery_attempt, judgments, expected_attestation_sha256"
    )


def test_freeze_snapshot_keeps_its_own_named_refusal() -> None:
    """3.2: the freeze branch already named its member and keeps that message."""

    message = _refusal_message(
        {"action": "status", "contract_path": _CONTRACT_PATH, "freeze_snapshot": True}
    )

    assert message == "Value error, only publish may freeze an immutable attempt snapshot"
