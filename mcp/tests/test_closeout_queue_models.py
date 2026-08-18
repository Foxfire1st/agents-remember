from __future__ import annotations

import hashlib
import unittest

from agents_remember.controlplane.closeout_queue_records import CloseoutQueuePendingTransaction
from agents_remember.models.closeout_queue import (
    ActiveAtomicBlocker,
    AppliedQueueRequest,
    CandidateAdmissionFacts,
    CloseoutCandidateRecord,
    CloseoutQueueRequest,
    CloseoutQueueState,
    EvidenceFact,
    SchedulingGrade,
    SchedulingGradeInput,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.worktrees import (
    closeout_queue_candidate_evidence,
    closeout_queue_evidence,
)
from pydantic import ValidationError
from test_closeout_queue import LEAF_A, MASTER_A, SPRINT, _grade

NOW = "2026-08-15T00:00:00+00:00"
HEX40 = "a" * 40
HEX64 = "b" * 64


def _route_review() -> dict[str, object]:
    return {
        "required": False,
        "status": "not-required",
        "recordSha256": HEX64,
        "evidence": [],
    }


def _candidate_payload(*, memory_mode: str = "disabled") -> dict[str, object]:
    payload: dict[str, object] = {
        "taskDocumentRef": LEAF_A.model_dump(),
        "owningMaster": MASTER_A.model_dump(),
        "contractPath": "/tmp/contract.md",
        "candidateTree": HEX40,
        "graphRevision": HEX64,
        "codeBaseCommit": HEX40,
        "routeReview": _route_review(),
        "memoryMode": memory_mode,
        "memoryReadiness": "not-applicable",
        "declaredBy": "manager",
        "declaredAt": NOW,
    }
    if memory_mode == "external":
        payload.update(
            {
                "memoryCandidateTree": "c" * 40,
                "memoryBaseCommit": "d" * 40,
                "ledgerMemoryCommit": "e" * 40,
                "memoryReadiness": "ready",
                "memoryEvidence": [
                    {"path": "quality.json", "sha256": "1" * 64},
                    {"path": "curator.json", "sha256": "2" * 64},
                ],
            }
        )
    return payload


def _candidate(*, memory_mode: str = "disabled") -> CloseoutCandidateRecord:
    return CloseoutCandidateRecord.model_validate(_candidate_payload(memory_mode=memory_mode))


def _state(**updates: object) -> CloseoutQueueState:
    payload: dict[str, object] = {
        "sprintTaskDocumentRef": SPRINT.model_dump(),
        "revision": 0,
        "graphRevision": HEX64,
        "updatedAt": NOW,
    }
    payload.update(updates)
    return CloseoutQueueState.model_validate(payload)


class CloseoutQueueModuleOwnershipTests(unittest.TestCase):
    def test_split_evidence_modules_have_direct_test_ownership(self) -> None:
        self.assertTrue(callable(closeout_queue_candidate_evidence.route_review_fact))
        self.assertTrue(callable(closeout_queue_evidence.curator_evidence))


class CloseoutQueueModelTests(unittest.TestCase):
    def test_request_payload_matrix_requires_and_forbids_action_specific_fields(self) -> None:
        base = {
            "sprint_task_document_ref": SPRINT.model_dump(),
            "request_id": "request",
            "expected_revision": 0,
        }
        with self.assertRaisesRegex(ValidationError, "missing=.*contract_path"):
            CloseoutQueueRequest.model_validate({**base, "action": "declare"})
        with self.assertRaisesRegex(ValidationError, "forbidden=.*grade"):
            CloseoutQueueRequest.model_validate(
                {
                    **base,
                    "action": "withdraw",
                    "candidate_task_document_ref": LEAF_A.model_dump(),
                    "grade": _grade("normal", LEAF_A),
                }
            )

    def test_models_refuse_ambiguous_or_unproven_input(self) -> None:
        with self.assertRaisesRegex(ValidationError, "resourceReason"):
            CandidateAdmissionFacts(resourceReady=False)
        with self.assertRaisesRegex(ValidationError, "admissionReason"):
            CandidateAdmissionFacts(admissionReady=False)
        with self.assertRaisesRegex(ValidationError, "omitted or non-blank"):
            SchedulingGradeInput.model_validate({**_grade("normal", LEAF_A), "urgency": ""})

    def test_grade_and_evidence_metadata_is_canonical_and_bounded(self) -> None:
        self.assertEqual(
            SchedulingGradeInput(
                priority="normal", judgmentId="  J-1  ", urgency=None, risk="  low  "
            ).model_dump(),
            {"priority": "normal", "judgmentId": "J-1", "urgency": None, "risk": "low"},
        )
        with self.assertRaisesRegex(ValidationError, "judgment id"):
            SchedulingGradeInput(priority="normal", judgmentId=" ")
        for field in ("urgency", "risk"):
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(ValidationError, "omitted or non-blank"),
            ):
                SchedulingGradeInput.model_validate(
                    {"priority": "normal", "judgmentId": "J", field: " "}
                )

        grade = {
            "priority": "normal",
            "judgmentId": "J",
            "subject": LEAF_A.key,
            "rationale": "because",
            "evidenceRefs": [" a.md ", "b.md"],
            "decidedBy": "orchestrator",
            "confidence": "high",
        }
        self.assertEqual(SchedulingGrade.model_validate(grade).evidenceRefs, ["a.md", "b.md"])
        for field in ("judgmentId", "subject", "rationale", "decidedBy", "confidence"):
            with self.subTest(field=field), self.assertRaisesRegex(ValidationError, "provenance"):
                SchedulingGrade.model_validate({**grade, field: " "})
        for evidence, message in (
            ([""], "must not be blank"),
            (["x" * 8193], "bounded path size"),
            (["same", "same"], "must be unique"),
        ):
            with self.subTest(evidence=evidence), self.assertRaisesRegex(ValidationError, message):
                SchedulingGrade.model_validate({**grade, "evidenceRefs": evidence})

        with self.assertRaisesRegex(ValidationError, "must not be blank"):
            EvidenceFact(path=" ", sha256=HEX64)
        with self.assertRaisesRegex(ValidationError, "must not be blank"):
            AppliedQueueRequest(requestId=" ", fingerprint=HEX64, revision=1)

    def test_admission_and_blocker_metadata_is_explained_and_trimmed(self) -> None:
        facts = CandidateAdmissionFacts(
            resourceReady=False,
            resourceReason="  unavailable ",
            admissionReady=False,
            admissionReason="  held ",
        )
        self.assertEqual((facts.resourceReason, facts.admissionReason), ("unavailable", "held"))
        blocker = ActiveAtomicBlocker(
            master=MASTER_A,
            graphRevision=HEX64,
            acquiredBy="  orchestrator ",
            acquiredAt=f" {NOW} ",
            rationale="  isolate framework ",
        )
        self.assertEqual(
            (blocker.acquiredBy, blocker.acquiredAt, blocker.rationale),
            ("orchestrator", NOW, "isolate framework"),
        )
        for field in ("acquiredBy", "acquiredAt", "rationale"):
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(ValidationError, "must not be blank"),
            ):
                ActiveAtomicBlocker.model_validate(
                    {
                        **blocker.model_dump(mode="json"),
                        field: " ",
                    }
                )

    def test_candidate_state_and_memory_matrix_is_fail_closed(self) -> None:
        base = _candidate_payload()
        for field in ("contractPath", "declaredBy", "declaredAt"):
            with self.subTest(field=field), self.assertRaisesRegex(ValidationError, "metadata"):
                CloseoutCandidateRecord.model_validate({**base, field: " "})

        invalid = (
            ({**base, "state": "closeout-in-flight"}, "owner fingerprint"),
            ({**base, "inFlightOwnerFingerprint": HEX64}, "owner fingerprint"),
            ({**base, "state": "certified"}, "closeout code commit"),
            ({**base, "closeoutCodeCommit": HEX40}, "closeout code commit"),
            (
                {**base, "closeoutMemoryContentCommit": "c" * 40},
                "uncertified candidates",
            ),
            ({**base, "closeoutLedgerCommit": "d" * 40}, "uncertified candidates"),
        )
        for payload, message in invalid:
            with self.subTest(message=message), self.assertRaisesRegex(ValidationError, message):
                CloseoutCandidateRecord.model_validate(payload)

        external = _candidate_payload(memory_mode="external")
        for field, replacement in (
            ("memoryReadiness", "not-applicable"),
            ("memoryEvidence", []),
            ("memoryCandidateTree", None),
            ("memoryBaseCommit", None),
            ("ledgerMemoryCommit", None),
        ):
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(ValidationError, "exact ready memory evidence"),
            ):
                CloseoutCandidateRecord.model_validate({**external, field: replacement})

        certified_external = {
            **external,
            "state": "certified",
            "closeoutCodeCommit": "f" * 40,
            "closeoutMemoryContentCommit": "1" * 40,
            "closeoutLedgerCommit": "2" * 40,
        }
        self.assertEqual(
            CloseoutCandidateRecord.model_validate(certified_external).state, "certified"
        )
        for field in ("closeoutMemoryContentCommit", "closeoutLedgerCommit"):
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(ValidationError, "require memory commits"),
            ):
                CloseoutCandidateRecord.model_validate({**certified_external, field: None})

        nonexternal = _candidate_payload()
        for field, value in (
            ("memoryReadiness", "ready"),
            ("memoryEvidence", [{"path": "x", "sha256": HEX64}]),
            ("memoryCandidateTree", "c" * 40),
            ("memoryBaseCommit", "d" * 40),
            ("ledgerMemoryCommit", "e" * 40),
        ):
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(ValidationError, "typed not-applicable"),
            ):
                CloseoutCandidateRecord.model_validate({**nonexternal, field: value})
        certified_nonexternal = {
            **nonexternal,
            "state": "certified",
            "closeoutCodeCommit": "f" * 40,
        }
        for field, value in (
            ("closeoutMemoryContentCommit", "1" * 40),
            ("closeoutLedgerCommit", "2" * 40),
        ):
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(ValidationError, "typed not-applicable"),
            ):
                CloseoutCandidateRecord.model_validate({**certified_nonexternal, field: value})

    def test_queue_state_rejects_noncanonical_keys_lane_and_closed_state(self) -> None:
        candidate = _candidate()
        key = candidate.taskDocumentRef.key
        receipt = AppliedQueueRequest(requestId="r", fingerprint=HEX64, revision=1)
        blocker = ActiveAtomicBlocker(
            master=MASTER_A,
            graphRevision=HEX64,
            acquiredBy="orchestrator",
            acquiredAt=NOW,
            rationale="isolate",
        )
        with self.assertRaisesRegex(ValidationError, "canonical"):
            _state(candidates={"wrong": candidate})
        with self.assertRaisesRegex(ValidationError, "request ids"):
            _state(appliedRequests=[receipt, receipt])
        active_a = candidate.model_copy(update={"state": "selected"})
        active_b = candidate.model_copy(
            update={
                "taskDocumentRef": TaskDocumentRef(
                    repository="repo-a", path="master-b/leaf-b.json"
                ),
                "owningMaster": TaskDocumentRef(repository="repo-a", path="master-b/task.json"),
                "state": "selected",
            }
        )
        with self.assertRaisesRegex(ValidationError, "at most one"):
            _state(candidates={key: active_a, active_b.taskDocumentRef.key: active_b})
        with self.assertRaisesRegex(ValidationError, "excludes"):
            _state(
                candidates={active_b.taskDocumentRef.key: active_b},
                activeBlocker=blocker,
            )
        with self.assertRaisesRegex(ValidationError, "quiescent"):
            _state(candidates={key: candidate}, closed=True)
        with self.assertRaisesRegex(ValidationError, "quiescent"):
            _state(activeBlocker=blocker, closed=True)

    def test_pending_transactions_bind_revision_status_and_receipt(self) -> None:
        receipt = AppliedQueueRequest(requestId="r", fingerprint=HEX64, revision=1)
        mutation_state = _state(revision=1, appliedRequests=[receipt])
        base = {
            "recordedAt": NOW,
            "transactionKind": "queue-mutation",
            "requestId": "r",
            "requestFingerprint": HEX64,
            "action": "declare",
            "actor": "manager",
            "previousRevision": 0,
            "state": mutation_state,
        }
        self.assertEqual(CloseoutQueuePendingTransaction.model_validate(base).state.revision, 1)
        for field in ("requestId", "recordedAt", "actor"):
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(ValidationError, "must not be blank"),
            ):
                CloseoutQueuePendingTransaction.model_validate({**base, field: " "})
        with self.assertRaisesRegex(ValidationError, "exactly one"):
            CloseoutQueuePendingTransaction.model_validate({**base, "previousRevision": 1})
        with self.assertRaisesRegex(ValidationError, "cannot carry sprint"):
            CloseoutQueuePendingTransaction.model_validate({**base, "sprintCompleted": True})

        for receipts, fingerprint, message in (
            ([], HEX64, "exact retry receipt"),
            ([receipt], "c" * 64, "exact retry receipt"),
            (
                [receipt.model_copy(update={"revision": 2})],
                HEX64,
                "exact retry receipt",
            ),
        ):
            state = _state(revision=1, appliedRequests=receipts)
            with self.subTest(receipts=receipts), self.assertRaisesRegex(ValidationError, message):
                CloseoutQueuePendingTransaction.model_validate(
                    {**base, "requestFingerprint": fingerprint, "state": state}
                )

        closed = _state(revision=1, closed=True)
        fingerprint = hashlib.sha256(closed.model_dump_json(exclude_none=True).encode()).hexdigest()
        status = {
            **base,
            "transactionKind": "sprint-status",
            "action": "reclaim-sprint",
            "requestFingerprint": fingerprint,
            "sprintCompleted": True,
            "state": closed,
        }
        self.assertTrue(CloseoutQueuePendingTransaction.model_validate(status).state.closed)
        invalid_status = (
            ({"action": "declare"}, "quiescent exact"),
            ({"sprintCompleted": None}, "quiescent exact"),
            ({"requestFingerprint": "f" * 64}, "quiescent exact"),
            ({"sprintCompleted": False}, "quiescent exact"),
            (
                {
                    "state": CloseoutQueueState.model_construct(
                        **{**closed.__dict__, "candidates": {LEAF_A.key: _candidate()}}
                    )
                },
                "closed sprint queue must be quiescent",
            ),
            (
                {
                    "state": CloseoutQueueState.model_construct(
                        **{
                            **closed.__dict__,
                            "activeBlocker": ActiveAtomicBlocker(
                                master=MASTER_A,
                                graphRevision=HEX64,
                                acquiredBy="orchestrator",
                                acquiredAt=NOW,
                                rationale="isolate",
                            ),
                        }
                    )
                },
                "closed sprint queue must be quiescent",
            ),
            (
                {
                    "state": CloseoutQueueState.model_construct(
                        **{**closed.__dict__, "appliedRequests": [receipt]}
                    )
                },
                "quiescent exact",
            ),
        )
        for update, message in invalid_status:
            candidate_update = update
            changed_state = candidate_update.get("state")
            if isinstance(changed_state, CloseoutQueueState):
                candidate_update = {
                    **candidate_update,
                    "requestFingerprint": hashlib.sha256(
                        changed_state.model_dump_json(exclude_none=True).encode()
                    ).hexdigest(),
                }
            with (
                self.subTest(update=candidate_update),
                self.assertRaisesRegex(ValidationError, message),
            ):
                CloseoutQueuePendingTransaction.model_validate({**status, **candidate_update})
