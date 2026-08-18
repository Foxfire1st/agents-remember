"""The authority's re-verification of whatever an adapter hands back.

An adapter is a vendor boundary, not a trusted collaborator: it can answer with the wrong
type, acknowledge somebody else's request, or claim an acceptance the contract forbids after
dispatch. None of those may be projected as a settled outcome, and none of them may be
projected as a rejection either --
the adapter method has already returned, so the authority cannot certify that nothing crossed
the wire. Every case below therefore ends in the same place: an ``unknown`` blocker that pins
the head, keeps the successor undispatched, and waits for an operator to resolve it.
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from collections import deque
from pathlib import Path
from typing import cast

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.models.conversations.control_wire import (
    ControlOperationRef,
    SubmissionReceipt,
)
from agents_remember.serving.harness_capabilities import SetResult
from agents_remember.serving.harness_control_models import (
    PromptRequest,
)
from agents_remember.serving.harness_submission_authority import HarnessSubmissionAuthority
from test_harness_submission_authority import NOW, _authority, _AuthorityAdapter, _prompt


class VerbatimAdapter(_AuthorityAdapter):
    """Answers with exactly what the test queued, with none of the usual repairs.

    The shared double stamps the request id back onto every receipt it returns, which is the
    right shape for testing the timeline but is precisely what hides the contract under test
    here. This one answers verbatim so the authority's own verification is the only thing
    standing between a malformed adapter answer and a settled submission.
    """

    def __init__(self) -> None:
        super().__init__()
        self.verbatim: deque[object] = deque()

    async def submit(self, request: PromptRequest) -> SubmissionReceipt:
        self.submissions.append(request)
        self.submit_started.set()
        if not self.verbatim:
            return SubmissionReceipt(
                request_id=request.request_id,
                acceptance="immediate",
                submitted_at=request.submitted_at,
                accepted_at=NOW,
            )
        return cast(SubmissionReceipt, self.verbatim.popleft())

    async def set_model(
        self, model_key: str, *, operation: ControlOperationRef | None = None
    ) -> SetResult:
        self.set_operations.append(("set-model", model_key, operation))
        self.set_started.set()
        if not self.verbatim:
            return SetResult(ok=True, acceptance="immediate", requested_value=model_key)
        return cast(SetResult, self.verbatim.popleft())


async def state_of(authority: HarnessSubmissionAuthority, request_id: str) -> str:
    batch = await authority.ledger.status("epoch-1", (request_id,), cockpit_only=True)
    submission = batch.submissions[0].submission
    assert submission is not None
    return submission.state


async def dispatched_ids(adapter: _AuthorityAdapter, *, expected: int) -> list[str]:
    """The request ids the adapter was actually asked to send, once the dispatcher settles."""

    for _ in range(10):
        if len(adapter.submissions) >= expected:
            break
        await asyncio.sleep(0)
    return [item.request_id for item in adapter.submissions]


class AdapterAnswerContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_an_answer_that_is_not_a_receipt_pins_the_head_for_an_operator(self) -> None:
        """A wrong-typed adapter answer is ambiguity, not rejection, and it stops the line.

        The adapter method already returned, so the bytes may or may not have crossed. The
        authority may not retire the submission and may not start the next one; it records
        what made the answer unreadable and waits.
        """

        adapter = VerbatimAdapter()
        adapter.verbatim.append(SetResult(ok=True, acceptance="immediate", requested_value="huh"))
        authority = _authority(adapter)
        try:
            receipt = await authority.submit(_prompt("wrong-type"))
            self.assertEqual(receipt.acceptance, "unknown")
            assert receipt.detail is not None
            self.assertIn("adapter prompt result must be a submission receipt", receipt.detail)
            self.assertIn("incoherent adapter HarnessControlError", receipt.detail)
            # Not a transport failure: the connection is fine, the answer was not.
            self.assertEqual(adapter.current.control, "ready")

            queued = await authority.submit(_prompt("behind-the-blocker"))
            self.assertEqual(queued.acceptance, "queued")
            self.assertEqual(await dispatched_ids(adapter, expected=2), ["wrong-type"])
            self.assertEqual(await state_of(authority, "wrong-type"), "unknown")

            await authority.resolve_operation(
                "wrong-type",
                "prompt",
                resolution="not-applied",
                detail="operator confirmed the turn never started",
            )
            self.assertEqual(await state_of(authority, "wrong-type"), "rejected")
            self.assertEqual(
                await dispatched_ids(adapter, expected=2),
                ["wrong-type", "behind-the-blocker"],
            )
        finally:
            await authority.stop(forced=True)

    async def test_a_receipt_for_another_request_never_settles_this_one(self) -> None:
        """Acknowledging some other request id is not acknowledging this submission.

        The correlation is the whole point of the receipt. An adapter that answers with a
        different id has proved nothing about the operation the authority dispatched, and the
        caller still gets an answer addressed to the request it actually made.
        """

        adapter = VerbatimAdapter()
        adapter.verbatim.append(
            SubmissionReceipt(
                request_id="somebody-elses-request",
                acceptance="immediate",
                submitted_at=NOW,
                accepted_at=NOW,
            )
        )
        authority = _authority(adapter)
        try:
            receipt = await authority.submit(_prompt("mine"))
            self.assertEqual(receipt.request_id, "mine")
            self.assertEqual(receipt.acceptance, "unknown")
            assert receipt.detail is not None
            self.assertIn("adapter receipt request id does not match operation", receipt.detail)
            self.assertIsNone(receipt.accepted_at)
            self.assertEqual(await state_of(authority, "mine"), "unknown")
        finally:
            await authority.stop(forced=True)

    async def test_a_queued_acceptance_after_dispatch_is_forbidden(self) -> None:
        """The authority is the only prompt queue; an adapter may not open a second one.

        ``queued`` from an adapter that has already been handed the operation says the vendor
        holds a submission the authority cannot see, order or withdraw. It is refused as an
        acceptance and converted into the blocker, keeping the vendor's own correlation handle
        so an operator can go and look.
        """

        adapter = VerbatimAdapter()
        adapter.verbatim.append(
            SubmissionReceipt(
                request_id="second-queue",
                acceptance="queued",
                submitted_at=NOW,
                vendor_correlation_id="vendor-queue-1",
                detail="the vendor put it in its own queue",
            )
        )
        authority = _authority(adapter)
        try:
            receipt = await authority.submit(_prompt("second-queue"))
            self.assertEqual(receipt.acceptance, "unknown")
            self.assertEqual(
                receipt.detail, "adapter returned forbidden queued acceptance after dispatch"
            )
            self.assertEqual(receipt.vendor_correlation_id, "vendor-queue-1")
            self.assertEqual(adapter.current.control, "ready")

            queued = await authority.submit(_prompt("after-second-queue"))
            self.assertEqual(queued.acceptance, "queued")
            self.assertEqual(await dispatched_ids(adapter, expected=2), ["second-queue"])

            await authority.resolve_operation(
                "second-queue",
                "prompt",
                resolution="applied",
                detail="operator found the turn in the vendor's queue and let it run",
            )
            self.assertEqual(await state_of(authority, "second-queue"), "delivered")
            self.assertEqual(
                await dispatched_ids(adapter, expected=2),
                ["second-queue", "after-second-queue"],
            )
        finally:
            await authority.stop(forced=True)

    async def test_a_setter_answer_that_is_not_a_set_result_is_never_reported_as_applied(
        self,
    ) -> None:
        """A setter has no later echo to correct it, so an unreadable answer cannot pass.

        Reporting ``ok`` here would tell the cockpit the model changed on the strength of an
        object the authority could not even read. The caller is told the outcome is unknown,
        and the setter keeps the ordinary lane until it is resolved.
        """

        adapter = VerbatimAdapter()
        adapter.verbatim.append(SubmissionReceipt("set-model", "immediate", NOW))
        authority = _authority(adapter)
        try:
            result = await authority.set_model("model-b")
            self.assertFalse(result.ok)
            self.assertEqual(result.acceptance, "unknown")
            self.assertEqual(result.requested_value, "model-b")
            assert result.detail is not None
            self.assertIn("adapter setter result must be SetResult", result.detail)

            active = authority.active_operation
            self.assertIsNotNone(active)
            assert active is not None
            self.assertEqual(active.kind, "set-model")

            queued = await authority.submit(_prompt("behind-the-setter"))
            self.assertEqual(queued.acceptance, "queued")
            self.assertEqual(await dispatched_ids(adapter, expected=1), [])

            await authority.resolve_operation(
                active.operation_id,
                "set-model",
                resolution="not-applied",
                detail="operator confirmed the model never changed",
            )
            self.assertEqual(await dispatched_ids(adapter, expected=1), ["behind-the-setter"])
        finally:
            await authority.stop(forced=True)


if __name__ == "__main__":
    unittest.main()
