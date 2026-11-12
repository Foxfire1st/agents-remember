"""MIK-R110 rule 3: the coherence tools answer with a status, and publish reads a file.

``prepare`` writes the candidate set to the enclosure's
``reports/curator-coherence-candidates.json`` (with a ``.md`` reading rendering beside it)
and answers with the count of candidates by kind, the set's digest and the locations;
``status``/``validate`` answer the state, the counts and what blocks; ``publish`` answers
the outcome with the count of published judgments, takes its two content lists inline or
from one JSON file, and answers that file's SHA-256.

Each case below protects one distinct user operation of that rule: the written set, the
bounded prepare answer, the file/inline record equivalence, the stale-set refusal, and the
four file-input refusals (too many, both ways, unreadable, invalid).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application.curator_coherence import _domain_refusal
from agents_remember.errors import CuratorCoherenceError
from agents_remember.models.declared_caller import DeclaredCaller
from agents_remember.models.lifecycles.curator_coherence import (
    MAX_CURATOR_REVIEW_ASSESSMENTS,
    MAX_CURATOR_SOURCE_CANDIDATES,
    CuratorCoherenceJudgment,
    CuratorCoherenceRequest,
)
from agents_remember.models.lifecycles.review_assessment import ReviewAssessmentRevision
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.tools.tool_registry import TOOL_RESPONSE_MODELS
from agents_remember.worktrees.integration.closeout.curator_coherence_publication import (
    CANDIDATES_FILE_SCHEMA,
    curator_coherence_action,
)
from agents_remember.worktrees.integration.closeout.curator_judgments_file import (
    JUDGMENTS_FILE_MAX_BYTES,
    _validate_file_entries,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract
from agents_remember_test_support.testing.review_git_trees import CODE_FILE
from curator_coherence_test_support import (
    prepared_publish_inputs,
    read_prepared_candidate_set,
    write_curator_evidence,
)
from pydantic import ValidationError
from review_curator_fixture_support import (
    EVIDENCE_RELATIVE,
    build_recorded_fixture,
)

pytestmark = [pytest.mark.evidence_unit, pytest.mark.usefixtures("worktree_services")]

REPO = "repo-a"
_MANY = 25


def _candidates(count: int) -> list[dict[str, str]]:
    """``count`` fixture candidates across three classifications."""

    kinds = ("source-change", "new-card", "removed-anchor")
    return [
        {
            "sourceFile": f"pkg/candidate-{index:04d}.py",
            "onboardingFile": f"onboarding/pkg/candidate-{index:04d}.py.md",
            "classification": kinds[index % len(kinds)],
        }
        for index in range(count)
    ]


def _judgments(
    candidates: list[dict[str, Any]], evidence_ref: str
) -> list[CuratorCoherenceJudgment]:
    return [
        CuratorCoherenceJudgment(
            sourceFile=str(candidate["sourceFile"]),
            onboardingFile=str(candidate["onboardingFile"]),
            classification=str(candidate["classification"]),
            disposition="reconciled",
            rationale="The fixture candidate has one explicit reconciliation judgment.",
            evidenceRef=evidence_ref,
        )
        for candidate in candidates
    ]


def _publish_request(
    contract: WorktreeContract,
    sprint: TaskDocumentRef,
    prepared: dict[str, object],
    candidates: list[dict[str, Any]],
    *,
    inputs: dict[str, Any] | None = None,
    **overrides: Any,
) -> CuratorCoherenceRequest:
    evidence = contract.task_root / EVIDENCE_RELATIVE
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text("# fixture evidence\n\nthe record this judgment cites\n", encoding="utf-8")
    kwargs: dict[str, Any] = {
        "action": "publish",
        "contract_path": contract.contract_path.as_posix(),
        "semantic_requirement_revision": "MIK-R110@v1",
        "delivery_attempt": "L110-A001",
        "judgments": _judgments(candidates, f"task:{EVIDENCE_RELATIVE}"),
        **(inputs if inputs is not None else prepared_publish_inputs(prepared)),
        "caller": DeclaredCaller(role="architect", task_document_ref=sprint),
    }
    kwargs.update(overrides)
    if kwargs.pop("file_only", False):
        # The file is the sole source: both lists stay omitted (an explicitly
        # passed empty list is still a second source and is refused).
        kwargs.pop("judgments", None)
        kwargs.pop("review_assessments", None)
    return CuratorCoherenceRequest(**kwargs)


def _many_fixture(path: Path):
    recorded = build_recorded_fixture(path)
    write_curator_evidence(
        recorded.contract,
        caller_ref=recorded.sprint,
        source_candidates=_candidates(_MANY),
    )
    return recorded


def test_prepare_writes_the_set_and_answers_counts_digest_and_locations(tmp_path: Path) -> None:
    recorded = build_recorded_fixture(tmp_path)
    write_curator_evidence(
        recorded.contract,
        caller_ref=recorded.sprint,
        source_candidates=_candidates(3),
    )
    prepared = curator_coherence_action(
        recorded.contract,
        CuratorCoherenceRequest(
            action="prepare", contract_path=recorded.contract.contract_path.as_posix()
        ),
    )

    assert prepared["state"] == "prepared"
    assert prepared["candidateCount"] == 3
    assert prepared["candidatesByKind"] == {"source-change": 1, "new-card": 1, "removed-anchor": 1}
    json_path = Path(str(prepared["candidatesPath"]))
    md_path = Path(str(prepared["candidatesRenderingPath"]))
    assert json_path.is_file()
    assert md_path.is_file()
    envelope = json.loads(json_path.read_text(encoding="utf-8"))
    assert envelope["schema"] == CANDIDATES_FILE_SCHEMA
    assert envelope["candidateCount"] == 3
    assert len(envelope["candidates"]) == 3
    assert set(envelope["expected"]) == {
        "predecessorAuthorityDigest",
        "codeCandidateTree",
        "memoryCandidateTree",
        "taskTopologyFingerprint",
        "taskIntent",
        "attestationSha256",
    }
    lists = (
        json.dumps(envelope["candidates"], sort_keys=True, separators=(",", ":")).encode() + b"\n"
    )
    assert envelope["candidateSetDigest"] == hashlib.sha256(lists).hexdigest()
    assert prepared["candidateSetDigest"] == envelope["candidateSetDigest"]
    rendering = md_path.read_text(encoding="utf-8")
    assert str(prepared["candidateSetDigest"]) in rendering
    for candidate in envelope["candidates"]:
        assert candidate["sourceFile"] in rendering


def test_prepare_answers_without_the_candidate_list_or_identity_blocks(
    tmp_path: Path,
) -> None:
    """More candidates than the list bound: the answer stays a status (evidence item 3)."""

    recorded = _many_fixture(tmp_path)
    prepared = curator_coherence_action(
        recorded.contract,
        CuratorCoherenceRequest(
            action="prepare", contract_path=recorded.contract.contract_path.as_posix()
        ),
    )

    assert prepared["candidateCount"] == _MANY
    assert sum(dict(prepared["candidatesByKind"]).values()) == _MANY  # type: ignore[arg-type]
    for absent in (
        "candidates",
        "pairIdentity",
        "codeCandidateTree",
        "memoryCandidateTree",
        "taskTopologyFingerprint",
        "taskIntent",
        "attestationSha256",
        "predecessorAuthorityDigest",
    ):
        assert absent not in prepared
    envelope = read_prepared_candidate_set(prepared)
    assert len(envelope["candidates"]) == _MANY


def test_publish_from_a_file_leaves_the_same_record_as_the_inline_call(
    tmp_path: Path,
) -> None:
    recorded = build_recorded_fixture(tmp_path)
    write_curator_evidence(
        recorded.contract,
        caller_ref=recorded.sprint,
        source_candidates=[
            {
                "sourceFile": CODE_FILE,
                "onboardingFile": f"onboarding/{CODE_FILE}.md",
                "classification": "source-change",
            }
        ],
    )
    prepared = curator_coherence_action(
        recorded.contract,
        CuratorCoherenceRequest(
            action="prepare", contract_path=recorded.contract.contract_path.as_posix()
        ),
    )
    envelope = read_prepared_candidate_set(prepared)
    inline_request = _publish_request(
        recorded.contract, recorded.sprint, prepared, list(envelope["candidates"])
    )
    inline = curator_coherence_action(recorded.contract, inline_request)
    assert inline["state"] == "published"

    inputs_path = tmp_path / "judgments.json"
    inputs = {
        "judgments": [judgment.model_dump(mode="json") for judgment in inline_request.judgments],
        "review_assessments": [],
    }
    inputs_path.write_text(json.dumps(inputs, sort_keys=True), encoding="utf-8")
    # File-only: both lists omitted, so the file is the sole source.
    by_file = curator_coherence_action(
        recorded.contract,
        CuratorCoherenceRequest(
            action="publish",
            contract_path=recorded.contract.contract_path.as_posix(),
            semantic_requirement_revision="MIK-R110@v1",
            delivery_attempt="L110-A001",
            judgments_file=inputs_path.as_posix(),
            caller=DeclaredCaller(role="architect", task_document_ref=recorded.sprint),
            **prepared_publish_inputs(prepared),
        ),
    )

    assert by_file["state"] == "already-current"
    assert by_file["recordDigest"] == inline["recordDigest"]
    assert by_file["publishedJudgmentCount"] == inline["publishedJudgmentCount"] == 1
    assert by_file["judgmentsFileSha256"] == hashlib.sha256(inputs_path.read_bytes()).hexdigest()
    assert "candidates" not in by_file
    assert "reviewAssessments" not in by_file


def test_the_wire_answer_carries_the_file_digest_and_validates(tmp_path: Path) -> None:
    """The shaped answers validate against the tool's own response model.

    ``finalize_tool_response`` model-validates every answer on the wire; these are the
    same calls with the transport layer's own model, proving the status answers pass it.
    """

    recorded = build_recorded_fixture(tmp_path)
    write_curator_evidence(
        recorded.contract,
        caller_ref=recorded.sprint,
        source_candidates=[
            {
                "sourceFile": CODE_FILE,
                "onboardingFile": f"onboarding/{CODE_FILE}.md",
                "classification": "source-change",
            }
        ],
    )
    prepared = curator_coherence_action(
        recorded.contract,
        CuratorCoherenceRequest(
            action="prepare", contract_path=recorded.contract.contract_path.as_posix()
        ),
    )
    wire_model = TOOL_RESPONSE_MODELS["curator_coherence"]
    wire_model.model_validate({"operation": "curator_coherence", **dict(prepared)})
    assert prepared["ok"] is True
    assert prepared["state"] == "prepared"
    assert "candidates" not in prepared

    envelope = read_prepared_candidate_set(prepared)
    inputs_path = tmp_path / "judgments.json"
    inline_request = _publish_request(
        recorded.contract, recorded.sprint, prepared, list(envelope["candidates"])
    )
    inputs_path.write_text(
        json.dumps(
            {
                "judgments": [
                    judgment.model_dump(mode="json") for judgment in inline_request.judgments
                ],
                "review_assessments": [],
            }
        ),
        encoding="utf-8",
    )
    published = curator_coherence_action(
        recorded.contract,
        _publish_request(
            recorded.contract,
            recorded.sprint,
            prepared,
            list(envelope["candidates"]),
            file_only=True,
            judgments_file=inputs_path.as_posix(),
        ),
    )

    wire_model.model_validate({"operation": "curator_coherence", **dict(published)})
    assert published["ok"] is True
    assert published["state"] == "published"
    assert published["publishedJudgmentCount"] == 1
    assert published["judgmentsFileSha256"] == hashlib.sha256(inputs_path.read_bytes()).hexdigest()


def test_publish_after_the_set_changed_is_refused(tmp_path: Path) -> None:
    recorded = _many_fixture(tmp_path)
    prepared = curator_coherence_action(
        recorded.contract,
        CuratorCoherenceRequest(
            action="prepare", contract_path=recorded.contract.contract_path.as_posix()
        ),
    )
    envelope = read_prepared_candidate_set(prepared)
    stale_inputs = prepared_publish_inputs(prepared)

    write_curator_evidence(
        recorded.contract,
        caller_ref=recorded.sprint,
        source_candidates=_candidates(2),
    )
    with pytest.raises(CuratorCoherenceError) as refused:
        curator_coherence_action(
            recorded.contract,
            _publish_request(
                recorded.contract,
                recorded.sprint,
                prepared,
                list(envelope["candidates"]),
                inputs=stale_inputs,
            ),
        )

    assert refused.value.status == "curator-coherence-prepared-source-stale"
    assert refused.value.next_action == "prepare"
    assert (
        refused.value.expected["attestationSha256"] != refused.value.observed["attestationSha256"]
    )


def test_a_file_with_one_judgment_too_many_is_refused_as_the_inline_list(
    tmp_path: Path,
) -> None:
    """2049 file judgments are refused; 2049 inline judgments never build a request."""

    recorded = build_recorded_fixture(tmp_path)
    prepared = curator_coherence_action(
        recorded.contract,
        CuratorCoherenceRequest(
            action="prepare", contract_path=recorded.contract.contract_path.as_posix()
        ),
    )
    inputs = {
        "judgments": [
            {
                "sourceFile": f"pkg/overflow-{index:05d}.py",
                "onboardingFile": f"onboarding/pkg/overflow-{index:05d}.py.md",
                "classification": "source-change",
                "disposition": "reconciled",
                "rationale": "overflow probe",
                "evidenceRef": f"task:{EVIDENCE_RELATIVE}",
            }
            for index in range(2049)
        ],
        "review_assessments": [],
    }
    inputs_path = tmp_path / "overflow.json"
    inputs_path.write_text(json.dumps(inputs), encoding="utf-8")

    with pytest.raises(CuratorCoherenceError) as refused:
        curator_coherence_action(
            recorded.contract,
            _publish_request(
                recorded.contract,
                recorded.sprint,
                prepared,
                [],
                file_only=True,
                judgments_file=inputs_path.as_posix(),
            ),
        )
    assert refused.value.status == "curator-coherence-judgments-file-invalid"
    assert refused.value.next_action == "publish"
    wire_refusal = _domain_refusal(
        _publish_request(
            recorded.contract,
            recorded.sprint,
            prepared,
            [],
            file_only=True,
            judgments_file=inputs_path.as_posix(),
        ),
        refused.value,
    )
    assert wire_refusal["ok"] is False
    assert wire_refusal["state"] == "refused"
    TOOL_RESPONSE_MODELS["curator_coherence"].model_validate(dict(wire_refusal))

    with pytest.raises(ValidationError) as invalid:
        CuratorCoherenceRequest.model_validate(
            {
                "action": "publish",
                "contract_path": recorded.contract.contract_path.as_posix(),
                "semantic_requirement_revision": "MIK-R110@v1",
                "delivery_attempt": "L110-A002",
                "judgments": inputs["judgments"],
                **prepared_publish_inputs(prepared),
                "caller": {
                    "role": "curator",
                    "task_document_ref": {"repository": REPO, "path": "master/leaf.json"},
                },
            }
        )
    assert "at most 2048 items" in str(invalid.value)


def test_supplying_a_list_both_inline_and_by_file_is_refused(tmp_path: Path) -> None:
    recorded = build_recorded_fixture(tmp_path)
    write_curator_evidence(
        recorded.contract,
        caller_ref=recorded.sprint,
        source_candidates=[
            {
                "sourceFile": CODE_FILE,
                "onboardingFile": f"onboarding/{CODE_FILE}.md",
                "classification": "source-change",
            }
        ],
    )
    prepared = curator_coherence_action(
        recorded.contract,
        CuratorCoherenceRequest(
            action="prepare", contract_path=recorded.contract.contract_path.as_posix()
        ),
    )
    envelope = read_prepared_candidate_set(prepared)
    inputs_path = tmp_path / "judgments.json"
    inputs_path.write_text(
        json.dumps({"judgments": envelope["candidates"], "review_assessments": []}),
        encoding="utf-8",
    )

    with pytest.raises(CuratorCoherenceError) as refused:
        curator_coherence_action(
            recorded.contract,
            _publish_request(
                recorded.contract,
                recorded.sprint,
                prepared,
                list(envelope["candidates"]),
                judgments_file=inputs_path.as_posix(),
            ),
        )

    assert refused.value.status == "curator-coherence-input-both-inline-and-file"
    assert refused.value.next_action == "publish"


def _explicit_empty_fixture(tmp_path: Path):
    """One prepared leaf with a valid judgments file beside the request."""

    recorded = build_recorded_fixture(tmp_path)
    write_curator_evidence(
        recorded.contract,
        caller_ref=recorded.sprint,
        source_candidates=[
            {
                "sourceFile": CODE_FILE,
                "onboardingFile": f"onboarding/{CODE_FILE}.md",
                "classification": "source-change",
            }
        ],
    )
    prepared = curator_coherence_action(
        recorded.contract,
        CuratorCoherenceRequest(
            action="prepare", contract_path=recorded.contract.contract_path.as_posix()
        ),
    )
    envelope = read_prepared_candidate_set(prepared)
    inputs_path = tmp_path / "judgments.json"
    inputs_path.write_text(
        json.dumps({"judgments": envelope["candidates"], "review_assessments": []}),
        encoding="utf-8",
    )
    return recorded, prepared, envelope, inputs_path


@pytest.mark.parametrize(
    "lists",
    [{"judgments": []}, {"review_assessments": []}, {"judgments": [], "review_assessments": []}],
    ids=["judgments-empty", "assessments-empty", "both-empty"],
)
def test_an_explicitly_empty_list_with_a_file_is_refused(tmp_path: Path, lists: dict) -> None:
    """An explicitly passed empty list is still a second source for that list."""

    recorded, prepared, _envelope, inputs_path = _explicit_empty_fixture(tmp_path)
    with pytest.raises(CuratorCoherenceError) as refused:
        curator_coherence_action(
            recorded.contract,
            CuratorCoherenceRequest(
                action="publish",
                contract_path=recorded.contract.contract_path.as_posix(),
                semantic_requirement_revision="MIK-R110@v1",
                delivery_attempt="L110-A001",
                judgments_file=inputs_path.as_posix(),
                caller=DeclaredCaller(role="architect", task_document_ref=recorded.sprint),
                **prepared_publish_inputs(prepared),
                **lists,
            ),
        )
    assert refused.value.status == "curator-coherence-input-both-inline-and-file"
    assert refused.value.next_action == "publish"


def test_the_largest_legal_payload_validates() -> None:
    """2048 judgments and 256 assessments pass entry validation under budget."""

    judgments: list[object] = [
        {
            "sourceFile": f"pkg/a-{index:04d}.py",
            "onboardingFile": f"onboarding/pkg/a-{index:04d}.py.md",
            "classification": "source-change",
            "disposition": "reconciled",
            "rationale": "fixture",
            "evidenceRef": "task:evidence.md",
        }
        for index in range(MAX_CURATOR_SOURCE_CANDIDATES)
    ]
    assessments: list[object] = [
        {
            "assessmentId": f"ASM-{index:04d}",
            "subject": {
                "kind": "comparison",
                "recordId": f"CMP-{index:04d}",
                "comparisonRef": "task:evidence.md",
            },
            "disposition": "no_concern_found",
            "finding": "",
            "rationale": "fixture",
            "comparisonRef": "task:evidence.md",
            "scopeManifestRef": "task:evidence.md",
        }
        for index in range(MAX_CURATOR_REVIEW_ASSESSMENTS)
    ]
    validated_judgments = _validate_file_entries(
        judgments, CuratorCoherenceJudgment, "judgments", MAX_CURATOR_SOURCE_CANDIDATES
    )
    validated_assessments = _validate_file_entries(
        assessments,
        ReviewAssessmentRevision,
        "review_assessments",
        MAX_CURATOR_REVIEW_ASSESSMENTS,
    )
    assert len(validated_judgments) == MAX_CURATOR_SOURCE_CANDIDATES
    assert len(validated_assessments) == MAX_CURATOR_REVIEW_ASSESSMENTS
    assert (
        len(json.dumps({"judgments": judgments, "review_assessments": assessments}).encode())
        < JUDGMENTS_FILE_MAX_BYTES
    )


def test_two_sizes_of_the_same_content_read_bounded(tmp_path: Path) -> None:
    """A 1MiB and a 2MiB file with the same lists parse identically, bounded."""

    recorded, prepared, _envelope, _inputs = _explicit_empty_fixture(tmp_path)
    small = tmp_path / "small.json"
    large = tmp_path / "large.json"
    body = json.dumps({"judgments": [], "review_assessments": []})
    small.write_text(body + " " * (1048576 - len(body)), encoding="utf-8")
    large.write_text(body + " " * (2097152 - len(body)), encoding="utf-8")
    assert small.stat().st_size == 1048576
    assert large.stat().st_size == 2097152
    assert large.stat().st_size < JUDGMENTS_FILE_MAX_BYTES
    statuses = []
    for path in (small, large):
        with pytest.raises(CuratorCoherenceError) as refused:
            curator_coherence_action(
                recorded.contract,
                _publish_request(
                    recorded.contract,
                    recorded.sprint,
                    prepared,
                    [],
                    file_only=True,
                    judgments_file=path.as_posix(),
                ),
            )
        statuses.append(refused.value.status)
    # Both padded empty lists read, parse, and travel together past validation
    # to the same next step (no candidates match the empty file).
    assert statuses[0] == statuses[1]


def test_an_over_budget_file_is_refused_before_parsing(tmp_path: Path) -> None:
    """A file over the largest legal payload is refused without being read whole."""

    recorded, prepared, _envelope, _inputs = _explicit_empty_fixture(tmp_path)
    huge = tmp_path / "huge.json"
    with huge.open("wb") as handle:
        handle.seek(JUDGMENTS_FILE_MAX_BYTES + 16)
        handle.write(b"x")
    assert huge.stat().st_size > JUDGMENTS_FILE_MAX_BYTES
    with pytest.raises(CuratorCoherenceError) as refused:
        curator_coherence_action(
            recorded.contract,
            _publish_request(
                recorded.contract,
                recorded.sprint,
                prepared,
                [],
                file_only=True,
                judgments_file=huge.as_posix(),
            ),
        )
    assert refused.value.status == "curator-coherence-judgments-file-over-budget"
    assert refused.value.next_action == "publish"


def test_a_non_regular_file_is_refused_without_reading(tmp_path: Path) -> None:
    """A directory as judgments_file is refused before any read."""

    recorded, prepared, _envelope, _inputs = _explicit_empty_fixture(tmp_path)
    with pytest.raises(CuratorCoherenceError) as refused:
        curator_coherence_action(
            recorded.contract,
            _publish_request(
                recorded.contract,
                recorded.sprint,
                prepared,
                [],
                file_only=True,
                judgments_file=tmp_path.as_posix(),
            ),
        )
    assert refused.value.status == "curator-coherence-judgments-file-not-regular"
    assert refused.value.next_action == "publish"


@pytest.mark.parametrize(
    "named", ["missing-absolute", "relative-path"], ids=["missing-absolute", "relative-path"]
)
def test_an_unreadable_judgments_file_is_refused(tmp_path: Path, named: str) -> None:
    recorded = build_recorded_fixture(tmp_path)
    prepared = curator_coherence_action(
        recorded.contract,
        CuratorCoherenceRequest(
            action="prepare", contract_path=recorded.contract.contract_path.as_posix()
        ),
    )
    envelope = read_prepared_candidate_set(prepared)
    missing = tmp_path / "no-such-file.json"
    assert not missing.exists()

    with pytest.raises(CuratorCoherenceError) as refused:
        curator_coherence_action(
            recorded.contract,
            _publish_request(
                recorded.contract,
                recorded.sprint,
                prepared,
                list(envelope["candidates"]),
                file_only=True,
                judgments_file=(
                    missing.as_posix() if named == "missing-absolute" else "relative/file.json"
                ),
            ),
        )

    assert refused.value.status == "curator-coherence-judgments-file-unreadable"
    assert refused.value.next_action == "publish"


@pytest.mark.parametrize(
    "payload",
    [
        b"{not json",
        b"[1, 2]",
        json.dumps({"judgments": [], "review_assessments": [], "extra": 1}).encode(),
        json.dumps({"judgments": {}, "review_assessments": []}).encode(),
        json.dumps(
            {
                "judgments": [
                    {
                        "sourceFile": "pkg/a.py",
                        "onboardingFile": "onboarding/pkg/a.py.md",
                        "classification": "source-change",
                        "disposition": "reconciled",
                        "evidenceRef": "task:x.md",
                    }
                ],
                "review_assessments": [],
            }
        ).encode(),
    ],
    ids=["not-json", "not-an-object", "unknown-keys", "not-lists", "bad-entry"],
)
def test_an_invalid_judgments_file_is_refused(tmp_path: Path, payload: bytes) -> None:
    recorded = build_recorded_fixture(tmp_path)
    prepared = curator_coherence_action(
        recorded.contract,
        CuratorCoherenceRequest(
            action="prepare", contract_path=recorded.contract.contract_path.as_posix()
        ),
    )
    envelope = read_prepared_candidate_set(prepared)
    inputs_path = tmp_path / "judgments.json"
    inputs_path.write_bytes(payload)

    with pytest.raises(CuratorCoherenceError) as refused:
        curator_coherence_action(
            recorded.contract,
            _publish_request(
                recorded.contract,
                recorded.sprint,
                prepared,
                list(envelope["candidates"]),
                file_only=True,
                judgments_file=inputs_path.as_posix(),
            ),
        )

    assert refused.value.status == "curator-coherence-judgments-file-invalid"
    assert refused.value.next_action == "publish"


@pytest.mark.parametrize("action", ["status", "prepare", "validate"])
def test_a_non_publish_action_refuses_the_judgments_file(action: str) -> None:
    with pytest.raises(ValidationError) as refused:
        CuratorCoherenceRequest.model_validate(
            {
                "action": action,
                "contract_path": "/coord/tasks/repo/master/enclosures/leaf/series-contract.md",
                "judgments_file": "/leaf/judgments.json",
            }
        )

    assert "judgments_file" in str(refused.value)


def test_status_names_stale_and_validate_answers_counts(tmp_path: Path) -> None:
    recorded = build_recorded_fixture(tmp_path)
    write_curator_evidence(
        recorded.contract,
        caller_ref=recorded.sprint,
        source_candidates=_candidates(2),
    )
    _rewrite_attestation_candidates(
        recorded.contract,
        [
            {
                "sourceFile": "pkg/stale.py",
                "onboardingFile": "onboarding/pkg/stale.py.md",
                "classification": "source-change",
            }
        ],
    )

    status = curator_coherence_action(
        recorded.contract,
        CuratorCoherenceRequest(
            action="status", contract_path=recorded.contract.contract_path.as_posix()
        ),
    )
    assert status["state"] == "stale"
    assert status["candidateCount"] == 1
    assert status["currentnessStatus"] == "curator-coherence-stale"
    assert status["nextAction"] == "publish"
    assert "candidates" not in status
    assert "pairIdentity" not in status

    prepared = curator_coherence_action(
        recorded.contract,
        CuratorCoherenceRequest(
            action="prepare", contract_path=recorded.contract.contract_path.as_posix()
        ),
    )
    envelope = read_prepared_candidate_set(prepared)
    published = curator_coherence_action(
        recorded.contract,
        _publish_request(
            recorded.contract, recorded.sprint, prepared, list(envelope["candidates"])
        ),
    )
    assert published["state"] == "published"

    validated = curator_coherence_action(
        recorded.contract,
        CuratorCoherenceRequest(
            action="validate", contract_path=recorded.contract.contract_path.as_posix()
        ),
    )
    assert validated["state"] == "valid"
    assert validated["candidateCount"] == 1
    assert validated["publishedJudgmentCount"] == 1
    assert validated["assessmentCount"] == 0
    assert Path(str(validated["recordPath"])).is_file()
    assert Path(str(validated["reportPath"])).is_file()
    assert "candidates" not in validated
    assert "reviewAssessments" not in validated
    assert "validationResult" not in validated
    assert "pairIdentity" not in validated


def _rewrite_attestation_candidates(
    contract: WorktreeContract, candidates: list[dict[str, str]]
) -> None:
    """Swap the attestation's candidate list keeping its dependency-bound inputs.

    Trees and report stay identical, so the observation stays valid while the live
    authority no longer binds it: the stale branch, not an unreadiness refusal.
    """

    attestation_path = contract.worktree_group / "reports" / "curator-memory-quality.json"
    attestation = json.loads(attestation_path.read_text(encoding="utf-8"))
    attestation["sourceChangeCandidates"] = candidates
    attestation["sourceChangeCandidateCount"] = len(candidates)
    attestation_path.write_text(
        json.dumps(attestation, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def test_prepare_refused_when_the_set_cannot_be_written(tmp_path: Path) -> None:
    recorded = build_recorded_fixture(tmp_path)
    blocking = recorded.contract.worktree_group / "reports" / "curator-coherence-candidates.json"
    blocking.unlink()
    blocking.mkdir()

    with pytest.raises(CuratorCoherenceError) as refused:
        curator_coherence_action(
            recorded.contract,
            CuratorCoherenceRequest(
                action="prepare", contract_path=recorded.contract.contract_path.as_posix()
            ),
        )

    assert refused.value.status == "curator-coherence-candidates-unwritable"
    assert refused.value.next_action == "prepare"
