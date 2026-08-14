from __future__ import annotations

import unittest

from agents_remember.errors import HarnessControlError
from agents_remember.serving.pi_rpc_adapter import PiAdapterLimits, PiRpcAdapter
from agents_remember.serving.pi_rpc_submissions import PiSubmissionEvidence, PiSubmissionLedger
from test_pi_rpc_adapter import (
    _direct_submit,
    _FakePiTransport,
    _launch,
    _operation,
    _prompt,
    _TransportSequence,
)


class PiSubmissionLedgerTests(unittest.IsolatedAsyncioTestCase):
    """What the adapter's request-id ledger retains, refuses and forgets.

    The ledger is the whole of Pi's reconciliation story: an ambiguous send leaves a row
    behind, and that row is the only evidence the write may have landed. Its eviction rule
    is therefore a correctness rule, not a memory-management one.
    """

    async def test_a_repeated_request_id_is_refused_before_anything_reaches_pi(self) -> None:
        # The request id is the vendor correlation id, so reusing one would make two
        # different prompts indistinguishable in reconciliation. The refusal has to land
        # before the write, or the ambiguity it prevents has already happened.
        transport = _FakePiTransport()
        adapter = PiRpcAdapter(transport_factory=_TransportSequence(transport))
        await adapter.start(_launch())
        try:
            operation = _operation("request-1")
            await adapter.preflight_operation(operation)
            await adapter.submit(_prompt("request-1", operation=operation))
            prompts_before = [c for c in transport.commands if c["type"] == "prompt"]

            # A retry of the operation that is still prepared: the write already went out,
            # so the ledger -- not the operation gate -- is what has to refuse it.
            with self.assertRaisesRegex(HarnessControlError, "duplicate Pi RPC request id"):
                await adapter.submit(_prompt("request-1", operation=operation))

            self.assertEqual(
                [c for c in transport.commands if c["type"] == "prompt"], prompts_before
            )
        finally:
            await adapter.stop("forced")

    async def test_a_refused_prompt_settles_its_row_so_the_ledger_can_move_on(self) -> None:
        """A rejection is evidence, and evidence makes the row droppable.

        Only ``accepted``/``rejected`` rows may be evicted. A refusal that left its row
        ``pending`` would be indistinguishable from an ambiguous send, and a ledger of
        those refuses the next submission outright -- so a run of refused prompts would
        wedge the seat.
        """
        transport = _FakePiTransport()
        transport.prompt_refusals.append("Pi refused the prompt")
        adapter = PiRpcAdapter(
            transport_factory=_TransportSequence(transport),
            limits=PiAdapterLimits(submission=1),
        )
        await adapter.start(_launch())
        try:
            refused = await _direct_submit(adapter, "refused-1")

            self.assertEqual(refused.acceptance, "rejected")
            assert refused.detail is not None
            self.assertIn("Pi refused the prompt", refused.detail)

            # The proof that the row settled: the one-slot ledger admits the next send.
            accepted = await _direct_submit(adapter, "after-refusal")
            self.assertEqual(accepted.acceptance, "immediate")
        finally:
            await adapter.stop("forced")

    def test_a_ledger_of_ambiguous_sends_refuses_the_next_row(self) -> None:
        """An unknown row is never evicted, even when that costs the next submission.

        Forgetting one would destroy the only record that a write may have reached Pi, and
        with it the operator's ability to ask. Refusing is the honest failure: the seat
        stops accepting prompts until somebody resolves the ambiguity, rather than
        quietly losing the evidence that there was one.
        """
        ledger = PiSubmissionLedger(2)
        for request_id, state in (("settled", "accepted"), ("ambiguous", "unknown")):
            ledger.remember(request_id, self._evidence(request_id, state))

        # One settled row is droppable, so the third submission fits.
        ledger.remember("third", self._evidence("third", "pending"))
        self.assertFalse(ledger.knows("settled"))
        self.assertTrue(ledger.knows("ambiguous"))

        # Now nothing is settled, and the ledger says so instead of forgetting one.
        with self.assertRaisesRegex(HarnessControlError, "ledger is full of ambiguous sends"):
            ledger.remember("fourth", self._evidence("fourth", "pending"))
        self.assertTrue(ledger.knows("ambiguous"))
        self.assertTrue(ledger.knows("third"))

    def test_a_settled_row_is_evicted_oldest_first_and_its_evidence_is_gone(self) -> None:
        # Eviction order is what decides which reconciliation input survives, so it is
        # asserted rather than left to the mapping's iteration order by accident.
        ledger = PiSubmissionLedger(2)
        ledger.remember("oldest", self._evidence("oldest", "accepted"))
        ledger.remember("newer", self._evidence("newer", "rejected"))

        ledger.remember("newest", self._evidence("newest", "pending"))

        self.assertIsNone(ledger.get("oldest"))
        self.assertTrue(ledger.knows("newer"))
        held = ledger.get("newest")
        assert held is not None
        self.assertEqual(held.state, "pending")

    def _evidence(self, request_id: str, state: str) -> PiSubmissionEvidence:
        return PiSubmissionEvidence(
            request=_prompt(request_id),
            cursor_before="entry-0",
            state=state,  # type: ignore[arg-type]
        )
