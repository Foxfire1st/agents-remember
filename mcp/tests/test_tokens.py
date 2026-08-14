"""Direct tests for the response token-accounting engine.

These cover ``models/tokens.py`` in isolation: the two counters, the dict-level
``finalize_payload_tokens`` finalizer wired into the MCP dispatch path, and the
``response_payload`` model serializer. The central guarantee is that the reported
``tokens`` value is self-consistent with a fresh recount of the *final* payload,
which is what proves the ``_finalize_token_count`` fixpoint actually converged
after the token metadata fields were folded back into the counted JSON.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.mcp import SERVER_VERSION
from agents_remember.models.core import PingResponse
from agents_remember.models.tokens import (
    DEFAULT_TOKEN_COUNTER,
    ApproximateTokenCounter,
    TiktokenTokenCounter,
    count_response_tokens,
    dump_with_token_count,
    finalize_payload_tokens,
    response_payload,
)


def _canonical_json(payload: dict) -> str:
    """Mirror the module's canonical JSON form for assertions."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


class ApproximateTokenCounterTests(unittest.TestCase):
    def test_name_and_inexact_flag(self) -> None:
        counter = ApproximateTokenCounter()
        self.assertEqual(counter.name, "approx:json_chars_div_4")
        self.assertFalse(counter.exact)

    def test_empty_mapping_stays_positive(self) -> None:
        # An empty mapping serializes to the non-empty string "{}", so it skips the
        # empty-text guard and floors at 1 rather than 0. The contract that matters
        # is that it never raises and never returns a negative count.
        self.assertEqual(ApproximateTokenCounter().count({}), 1)

    def test_count_tracks_compact_json_length(self) -> None:
        counter = ApproximateTokenCounter()
        payload = {"ok": True, "operation": "ping", "value": "x" * 40}
        expected = max(1, (len(_canonical_json(payload)) + 3) // 4)
        self.assertEqual(counter.count(payload), expected)

    def test_count_is_deterministic(self) -> None:
        counter = ApproximateTokenCounter()
        payload = {"ok": True, "operation": "ping"}
        self.assertEqual(counter.count(payload), counter.count(payload))


class TiktokenTokenCounterTests(unittest.TestCase):
    def test_default_counter_is_exact_o200k(self) -> None:
        self.assertIsInstance(DEFAULT_TOKEN_COUNTER, TiktokenTokenCounter)
        self.assertEqual(DEFAULT_TOKEN_COUNTER.name, "tiktoken:o200k_base")
        self.assertTrue(DEFAULT_TOKEN_COUNTER.exact)

    def test_count_is_positive_for_non_empty_payload(self) -> None:
        self.assertGreater(DEFAULT_TOKEN_COUNTER.count({"ok": True, "operation": "ping"}), 0)

    def test_count_response_tokens_uses_supplied_counter(self) -> None:
        payload = {"ok": True, "operation": "ping"}
        self.assertEqual(
            count_response_tokens(payload, token_counter=DEFAULT_TOKEN_COUNTER),
            DEFAULT_TOKEN_COUNTER.count(payload),
        )


class FinalizePayloadTokensTests(unittest.TestCase):
    def test_stamps_all_three_metadata_fields(self) -> None:
        payload = finalize_payload_tokens({"ok": True, "operation": "ping"})
        self.assertGreater(payload["tokens"], 0)
        self.assertEqual(payload["tokenizer"], "tiktoken:o200k_base")
        self.assertIs(payload["tokenCountExact"], True)

    def test_reported_tokens_match_a_fresh_recount(self) -> None:
        # Fixpoint guarantee: the stored count already accounts for the digits of
        # the count itself plus the tokenizer/exact fields, so recounting the
        # finalized payload must return the same number.
        payload = finalize_payload_tokens({"ok": True, "operation": "ping"})
        self.assertEqual(payload["tokens"], DEFAULT_TOKEN_COUNTER.count(payload))

    def test_fixpoint_converges_with_approximate_counter(self) -> None:
        counter = ApproximateTokenCounter()
        payload = finalize_payload_tokens({"ok": True, "operation": "ping"}, token_counter=counter)
        self.assertEqual(payload["tokenizer"], counter.name)
        self.assertFalse(payload["tokenCountExact"])
        self.assertEqual(payload["tokens"], counter.count(payload))

    def test_mutates_and_returns_same_object(self) -> None:
        original = {"ok": True, "operation": "ping"}
        returned = finalize_payload_tokens(original)
        self.assertIs(returned, original)


class ResponsePayloadTests(unittest.TestCase):
    def test_serializes_model_and_adds_token_metadata(self) -> None:
        response = PingResponse(
            ok=True, server="agents-remember", version=SERVER_VERSION, transport="stdio"
        )
        payload = response_payload(response)
        self.assertEqual(payload["server"], "agents-remember")
        self.assertGreater(payload["tokens"], 0)
        self.assertEqual(payload["tokenizer"], "tiktoken:o200k_base")
        self.assertEqual(payload["tokens"], DEFAULT_TOKEN_COUNTER.count(payload))

    def test_dump_with_token_count_is_response_payload_alias(self) -> None:
        response = PingResponse(
            ok=True, server="agents-remember", version=SERVER_VERSION, transport="stdio"
        )
        self.assertEqual(dump_with_token_count(response), response_payload(response))


if __name__ == "__main__":
    unittest.main()
